"""Âm thanh: im lặng, đổi tốc độ (giữ cao độ), mã hoá qua ffmpeg, phụ đề SRT/VTT."""

from __future__ import annotations

import os
import shutil
import subprocess

import numpy as np

__all__ = ["apply_speed", "silence", "concat", "trim_silence", "encode", "encode_file", "transcode_file", "to_srt", "to_vtt", "CONTENT_TYPES", "FFMPEG"]

CONTENT_TYPES = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "ogg": "audio/ogg",
    "srt": "application/x-subrip",
    "vtt": "text/vtt",
}

FFMPEG = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "/usr/bin/ffmpeg"

SPEED_MIN, SPEED_MAX = 0.5, 2.0


class AudioError(RuntimeError):
    pass


def _as_f32(wav) -> np.ndarray:
    a = np.asarray(wav, dtype=np.float32).reshape(-1)
    if a.size and not np.all(np.isfinite(a)):
        a = np.nan_to_num(a, nan=0.0, posinf=1.0, neginf=-1.0)
    return a


def _ffmpeg(args: list[str], data: bytes) -> bytes:
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", *args]
    try:
        p = subprocess.run(cmd, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    except FileNotFoundError as e:  # pragma: no cover
        raise AudioError(f"Không tìm thấy ffmpeg ({FFMPEG})") from e
    if p.returncode != 0:
        raise AudioError(f"ffmpeg lỗi ({p.returncode}): {p.stderr.decode('utf-8', 'replace')[-500:]}")
    return p.stdout


def _raw_in(sr: int) -> list[str]:
    return ["-f", "f32le", "-ar", str(int(sr)), "-ac", "1", "-i", "pipe:0"]


def silence(sr, seconds) -> np.ndarray:
    n = max(0, int(round(float(seconds) * int(sr))))
    return np.zeros(n, dtype=np.float32)


def concat(parts: list[np.ndarray]) -> np.ndarray:
    parts = [_as_f32(p) for p in parts if p is not None and len(p)]
    if not parts:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(parts)


def trim_silence(wav, sr, threshold: float = 0.006, pad: float = 0.04) -> np.ndarray:
    """Cắt khoảng lặng đầu/cuối (biên độ < threshold), chừa lại ``pad`` giây mỗi bên."""
    a = _as_f32(wav)
    if a.size == 0:
        return a
    idx = np.flatnonzero(np.abs(a) > threshold)
    if idx.size == 0:
        return a[:0]
    p = int(pad * int(sr))
    return a[max(0, idx[0] - p): min(a.size, idx[-1] + 1 + p)]


def _atempo_chain(speed: float) -> str:
    # atempo hỗ trợ 0.5–100 mỗi khâu; tách thành nhiều khâu cho chắc chắn ngoài [0.5, 2].
    filters = []
    s = float(speed)
    while s > 2.0:
        filters.append("atempo=2.0")
        s /= 2.0
    while s < 0.5:
        filters.append("atempo=0.5")
        s /= 0.5
    filters.append(f"atempo={s:.6f}")
    return ",".join(filters)


def apply_speed(wav, sr, speed: float) -> np.ndarray:
    """Đổi tốc độ, giữ cao độ (ffmpeg atempo). speed==1 trả nguyên mảng."""
    a = _as_f32(wav)
    speed = float(speed)
    if abs(speed - 1.0) < 1e-6 or a.size == 0:
        return a
    if not (0.25 <= speed <= 4.0):
        raise ValueError(f"speed ngoài phạm vi: {speed}")
    out = _ffmpeg(
        _raw_in(sr) + ["-filter:a", _atempo_chain(speed), "-f", "f32le", "-ar", str(int(sr)), "-ac", "1", "pipe:1"],
        a.tobytes(),
    )
    return np.frombuffer(out, dtype=np.float32).copy()


def encode(wav, sr, fmt: str) -> bytes:
    """Mã hoá float32 mono → "mp3" (128k, 44.1 kHz), "wav" (PCM16), "ogg" (Opus 48 kHz)."""
    a = np.clip(_as_f32(wav), -1.0, 1.0)
    fmt = (fmt or "").lower()
    if fmt == "wav":
        args = ["-c:a", "pcm_s16le", "-f", "wav"]
    elif fmt == "mp3":
        args = ["-c:a", "libmp3lame", "-b:a", "128k", "-ar", "44100", "-f", "mp3"]
    elif fmt == "ogg":
        args = ["-c:a", "libopus", "-b:a", "64k", "-ar", "48000", "-f", "ogg"]
    else:
        raise ValueError(f"Định dạng không hỗ trợ: {fmt}")
    if fmt == "wav":
        # Ghi trực tiếp header WAV để ffmpeg không cần stdout tua ngược (seek) để sửa kích thước.
        return _wav_pcm16(a, int(sr))
    try:
        return _ffmpeg(_raw_in(sr) + ["-ac", "1", *args, "pipe:1"], a.tobytes())
    except AudioError:
        if fmt != "ogg":
            raise
        # ffmpeg không có libopus → Vorbis
        args = ["-c:a", "libvorbis", "-q:a", "4", "-ar", "44100", "-f", "ogg"]
        return _ffmpeg(_raw_in(sr) + ["-ac", "1", *args, "pipe:1"], a.tobytes())


_CHUNK = 1 << 18  # mẫu mỗi lần đọc (1 MB float32)


def _ffmpeg_file(args: list[str]) -> None:
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args]
    try:
        p = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                           check=False)
    except FileNotFoundError as e:  # pragma: no cover
        raise AudioError(f"Không tìm thấy ffmpeg ({FFMPEG})") from e
    if p.returncode != 0:
        raise AudioError(f"ffmpeg lỗi ({p.returncode}): {p.stderr.decode('utf-8', 'replace')[-500:]}")


def _codec_args(fmt: str) -> list[str]:
    if fmt == "mp3":
        return ["-c:a", "libmp3lame", "-b:a", "128k", "-ar", "44100", "-f", "mp3"]
    if fmt == "ogg":
        return ["-c:a", "libopus", "-b:a", "64k", "-ar", "48000", "-f", "ogg"]
    if fmt == "wav":
        return ["-c:a", "pcm_s16le", "-f", "wav"]
    raise ValueError(f"Định dạng không hỗ trợ: {fmt}")


_VORBIS = ["-c:a", "libvorbis", "-q:a", "4", "-ar", "44100", "-f", "ogg"]


def encode_file(src_f32: str, sr: int, fmt: str, out_path: str) -> None:
    """Mã hoá file float32 mono (đã clip) → ``out_path``, đọc/ghi theo luồng (không nạp cả bản vào RAM)."""
    fmt = (fmt or "").lower()
    if fmt == "wav":
        import wave

        with open(src_f32, "rb") as f, wave.open(out_path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(int(sr))
            while True:
                buf = f.read(_CHUNK * 4)
                if not buf:
                    break
                a = np.frombuffer(buf, dtype="<f4")
                a = np.nan_to_num(a, nan=0.0, posinf=1.0, neginf=-1.0)
                w.writeframes((np.clip(a, -1.0, 1.0) * 32767.0).round().astype("<i2").tobytes())
        return
    args = _codec_args(fmt)
    inp = ["-f", "f32le", "-ar", str(int(sr)), "-ac", "1", "-i", src_f32]
    try:
        _ffmpeg_file(inp + ["-ac", "1", *args, out_path])
    except AudioError:
        if fmt != "ogg":
            raise
        _ffmpeg_file(inp + ["-ac", "1", *_VORBIS, out_path])  # ffmpeg không có libopus → Vorbis


def transcode_file(src_path: str, fmt: str, out_path: str) -> None:
    """Đổi định dạng một file âm thanh đã có (mp3/wav/ogg) sang ``fmt`` — không dựng lại giọng."""
    fmt = (fmt or "").lower()
    args = _codec_args(fmt)
    try:
        _ffmpeg_file(["-i", src_path, "-vn", "-ac", "1", *args, out_path])
    except AudioError:
        if fmt != "ogg":
            raise
        _ffmpeg_file(["-i", src_path, "-vn", "-ac", "1", *_VORBIS, out_path])


def _wav_pcm16(a: np.ndarray, sr: int) -> bytes:
    import io
    import wave

    pcm = (np.clip(a, -1.0, 1.0) * 32767.0).round().astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)
    return buf.getvalue()


def _ts(seconds: float, sep: str) -> str:
    ms_total = int(round(max(0.0, float(seconds)) * 1000))
    h, rem = divmod(ms_total, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _cue_text(text: str) -> str:
    # Dòng trống trong cue sẽ làm vỡ SRT/VTT; "-->" cũng bị cấm trong VTT.
    t = " ".join(str(text).split())
    return t.replace("-->", "→")


def to_srt(segments: list[dict]) -> str:
    blocks = []
    for i, seg in enumerate(segments, start=1):
        blocks.append(f"{i}\n{_ts(seg['start'], ',')} --> {_ts(seg['end'], ',')}\n{_cue_text(seg['text'])}\n")
    return "\n".join(blocks)


def to_vtt(segments: list[dict]) -> str:
    blocks = ["WEBVTT\n"]
    for i, seg in enumerate(segments, start=1):
        blocks.append(f"{i}\n{_ts(seg['start'], '.')} --> {_ts(seg['end'], '.')}\n{_cue_text(seg['text'])}\n")
    return "\n".join(blocks)
