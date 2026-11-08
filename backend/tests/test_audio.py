import io
import re
import subprocess
import wave

import numpy as np
import pytest

from dattts import audio

SR = 48000


def tone(seconds=1.0, freq=220.0, sr=SR):
    t = np.arange(int(seconds * sr), dtype=np.float32) / sr
    return (0.3 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def probe_duration(data: bytes) -> float:
    p = subprocess.run(
        [audio.FFMPEG, "-hide_banner", "-i", "pipe:0", "-f", "null", "-"],
        input=data, stderr=subprocess.PIPE, stdout=subprocess.PIPE,
    )
    times = re.findall(rb"time=(\d+):(\d+):([\d.]+)", p.stderr)
    assert times, p.stderr[-400:]
    h, m, s = times[-1]
    return int(h) * 3600 + int(m) * 60 + float(s)


def test_silence():
    s = audio.silence(SR, 0.3)
    assert s.dtype == np.float32 and len(s) == 14400 and not s.any()
    assert len(audio.silence(SR, 0)) == 0


def test_concat():
    out = audio.concat([tone(0.1), np.zeros(0, np.float32), audio.silence(SR, 0.1)])
    assert len(out) == 9600 and out.dtype == np.float32
    assert len(audio.concat([])) == 0


def test_apply_speed_identity_returns_same():
    w = tone(0.5)
    assert np.array_equal(audio.apply_speed(w, SR, 1.0), w)


@pytest.mark.parametrize("speed", [0.7, 1.25, 1.5])
def test_apply_speed_changes_duration_keeps_pitch(speed):
    w = tone(1.0, 300.0)
    out = audio.apply_speed(w, SR, speed)
    assert out.dtype == np.float32
    assert abs(len(out) / SR - 1.0 / speed) < 0.05
    # cao độ giữ nguyên: đỉnh phổ vẫn ~300 Hz
    mid = out[len(out) // 4: 3 * len(out) // 4]
    spec = np.abs(np.fft.rfft(mid))
    peak = np.fft.rfftfreq(len(mid), 1 / SR)[spec.argmax()]
    assert abs(peak - 300.0) < 10


def test_apply_speed_empty():
    assert len(audio.apply_speed(np.zeros(0, np.float32), SR, 1.3)) == 0


def test_encode_wav_pcm16():
    data = audio.encode(tone(0.5), SR, "wav")
    with wave.open(io.BytesIO(data)) as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2 and w.getframerate() == SR
        assert w.getnframes() == 24000


@pytest.mark.parametrize("fmt, magic", [("mp3", None), ("ogg", b"OggS")])
def test_encode_compressed(fmt, magic):
    data = audio.encode(tone(1.0), SR, fmt)
    assert len(data) > 1000
    if magic:
        assert data[:4] == magic
    else:
        assert data[:3] == b"ID3" or data[0] == 0xFF
    assert abs(probe_duration(data) - 1.0) < 0.1


def test_encode_clips_out_of_range():
    data = audio.encode(np.array([2.0, -2.0, np.nan], np.float32), SR, "wav")
    with wave.open(io.BytesIO(data)) as w:
        frames = np.frombuffer(w.readframes(3), "<i2")
    assert list(frames) == [32767, -32767, 0]


def test_encode_bad_format():
    with pytest.raises(ValueError):
        audio.encode(tone(0.1), SR, "flac")


SEGS = [
    {"index": 0, "text": "Xin chào, mình là DatTTS.", "start": 0.0, "end": 1.92},
    {"index": 1, "text": "Giọng đọc\ntiếng Việt --> tự nhiên.", "start": 2.22, "end": 3661.5},
]


def test_to_srt():
    srt = audio.to_srt(SEGS)
    assert srt == (
        "1\n00:00:00,000 --> 00:00:01,920\nXin chào, mình là DatTTS.\n\n"
        "2\n00:00:02,220 --> 01:01:01,500\nGiọng đọc tiếng Việt → tự nhiên.\n"
    )


def test_to_vtt():
    vtt = audio.to_vtt(SEGS)
    assert vtt.startswith("WEBVTT\n\n1\n00:00:00.000 --> 00:00:01.920\nXin chào")
    assert "01:01:01.500" in vtt


def test_content_types():
    assert audio.CONTENT_TYPES == {"mp3": "audio/mpeg", "wav": "audio/wav", "ogg": "audio/ogg",
                                   "srt": "application/x-subrip", "vtt": "text/vtt"}


def test_trim_silence_keeps_padding():
    w = np.concatenate([audio.silence(SR, 0.3), tone(0.5), audio.silence(SR, 0.4)])
    out = audio.trim_silence(w, SR, pad=0.04)
    assert len(out) / SR == pytest.approx(0.5 + 0.08, abs=0.005)
    assert len(audio.trim_silence(audio.silence(SR, 1.0), SR)) == 0
    assert len(audio.trim_silence(np.zeros(0, np.float32), SR)) == 0
