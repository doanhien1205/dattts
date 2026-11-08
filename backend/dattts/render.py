"""Điều phối: kịch bản → câu → engine → ghép → encode (+ phụ đề SRT/VTT)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from . import audio
from .engine import DEFAULT_VOICE_ID, voice_id_from_name
from .messages import LocalizedError
from .textproc import _TAG as _VOICE_TAG
from .textproc import (
    apply_builtin_rules,
    apply_dictionary,
    clean_text,
    count_chars,
    parse_script,
    split_sentences,
)

__all__ = [
    "RenderRequest",
    "RenderError",
    "request_key",
    "plan",
    "render",
    "render_to_file",
    "billable_chars",
    "resolve_voice",
    "validate",
    "FORMATS",
    "SPEED_MIN",
    "SPEED_MAX",
    "GAPS",
]

# Đổi khi luật văn bản/ghép âm thay đổi → khoá cache mới.
PIPELINE_VERSION = "1"

FORMATS = ("mp3", "wav", "ogg")
SPEED_MIN, SPEED_MAX = 0.7, 1.5
DICT_MAX_ENTRIES, DICT_MAX_LEN = 200, 100
MAX_SENTENCE_CHARS = 220

GAPS = {"clause": 0.12, "sentence": 0.3, "paragraph": 0.6, "voice_change": 0.6}
EMPTY_GAP = 0.05  # câu không tạo ra âm thanh → chỉ chèn khoảng lặng nhỏ


class RenderError(LocalizedError):
    """Lỗi dựng giọng: ``code`` + khoá thông điệp song ngữ (``messages.py``) + HTTP status.

    ``RenderError(code, status=…, key=…, params=…)``; ``.message`` là bản tiếng Anh.
    """


@dataclass
class RenderRequest:
    text: str
    voice: str = DEFAULT_VOICE_ID
    speed: float = 1.0
    format: str = "mp3"
    dictionary: list[dict] = field(default_factory=list)


def _norm_dictionary(entries) -> list[dict]:
    out = []
    for e in entries or []:
        if isinstance(e, dict):
            out.append({"from": str(e.get("from", "") or "").strip(), "to": str(e.get("to", "") or "").strip()})
    return out


def request_key(req: RenderRequest, engine_name: str) -> str:
    payload = {
        "v": PIPELINE_VERSION,
        "engine": engine_name,
        "text": clean_text(req.text),
        "voice": voice_id_from_name(req.voice or DEFAULT_VOICE_ID),
        "speed": round(float(req.speed), 3),
        "format": (req.format or "mp3").lower(),
        "dictionary": [d for d in _norm_dictionary(req.dictionary) if d["from"]],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def validate(req: RenderRequest) -> None:
    """Kiểm tra request; lỗi → RenderError (thông điệp song ngữ, xem messages.py)."""
    if not isinstance(req.text, str) or not clean_text(req.text):
        raise RenderError("empty_text", status=400)
    try:
        speed = float(req.speed)
    except (TypeError, ValueError):
        speed = float("nan")
    if not (SPEED_MIN <= speed <= SPEED_MAX):
        raise RenderError("bad_speed", status=400)
    if (req.format or "").lower() not in FORMATS:
        raise RenderError("bad_format", status=400)
    d = req.dictionary or []
    if not isinstance(d, list) or len(d) > DICT_MAX_ENTRIES:
        raise RenderError("bad_dictionary", status=400, key="bad_dictionary.max_entries",
                          params={"max": DICT_MAX_ENTRIES})
    for e in d:
        if not isinstance(e, dict) or not isinstance(e.get("from", ""), str) or not isinstance(e.get("to", ""), str):
            raise RenderError("bad_dictionary", status=400)
        if len(e.get("from", "")) > DICT_MAX_LEN or len(e.get("to", "")) > DICT_MAX_LEN:
            raise RenderError("bad_dictionary", status=400, key="bad_dictionary.max_len",
                              params={"max": DICT_MAX_LEN})


def _unknown_voice(name: str, voices: list[dict]) -> RenderError:
    sample = ", ".join(v["name"] for v in voices[:6])
    return RenderError("unknown_voice", status=400, params={"name": name, "examples": sample})


def resolve_voice(name: str | None, voices: list[dict], default: str = DEFAULT_VOICE_ID) -> str:
    """Tên hiển thị hoặc id (không phân biệt hoa thường, dấu) → id giọng. Không có → RenderError."""
    ids = {v["id"] for v in voices}
    if name is None or not str(name).strip():
        if default in ids:
            return default
        if voices:
            return voices[0]["id"]
        raise RenderError("unknown_voice", status=500, key="unknown_voice.none_available")
    vid = voice_id_from_name(name)
    if vid in ids:
        return vid
    raise _unknown_voice(str(name).strip(), voices)


def _droppable_tag(name: str) -> bool:
    """Ngoặc vuông không phải tên giọng: chú thích "[1]", "[a]", "[cần dẫn nguồn]", chỉ dẫn "[cười]".

    Chỉ giữ lỗi ``unknown_voice`` cho thẻ trông như tên riêng (mọi từ viết hoa, không có số),
    ví dụ gõ sai tên giọng "[Ai Đó]".
    """
    n = name.strip()
    if not n:
        return True
    if any(ch.isdigit() for ch in n) or len(n) == 1:
        return True
    words = n.split()
    return not all(w[:1].isupper() for w in words)


_SPEAKABLE = re.compile(r"\w")


def plan(req: RenderRequest, engine) -> list[dict]:
    validate(req)
    voices = engine.voices()
    resolver = getattr(engine, "voice_name", None)

    def resolve(name):
        if name is None or not str(name).strip():
            return resolve_voice(None, voices)
        try:
            return resolve_voice(name, voices)
        except RenderError:
            if resolver is not None:  # alias của VieNeu, ví dụ "Minh Quân" → "Hải Đăng"
                try:
                    real = resolver(name)
                    return voice_id_from_name(real)
                except Exception:
                    pass
            raise

    def strip_tag(m: re.Match) -> str:
        try:
            resolve(m.group(1).strip())
            return m.group(0)  # thẻ giọng thật
        except RenderError:
            return " " if _droppable_tag(m.group(1)) else m.group(0)

    default_voice = resolve(req.voice)
    dictionary = _norm_dictionary(req.dictionary)
    text = _VOICE_TAG.sub(strip_tag, clean_text(req.text))
    items: list[dict] = []
    for seg in parse_script(text):
        vid = default_voice if seg.voice is None else resolve(seg.voice)
        sents = split_sentences(seg.text, max_chars=MAX_SENTENCE_CHARS)
        for k, (sent, kind) in enumerate(sents):
            dict_text = apply_dictionary(sent, dictionary)
            spoken = apply_builtin_rules(dict_text)
            # Từ điển/luật có thể làm câu dài ra (một từ → 100 ký tự) → cắt lại để mỗi lần gọi engine
            # vẫn ≤ MAX_SENTENCE_CHARS.
            if len(spoken) > MAX_SENTENCE_CHARS:
                parts = [p for p, _ in split_sentences(spoken, max_chars=MAX_SENTENCE_CHARS)]
            else:
                parts = [spoken]
            if items and k == 0 and items[-1]["voice"] != vid:
                items[-1]["gap_after"] = max(items[-1]["gap_after"], GAPS["voice_change"])
            items.append({
                "index": len(items),
                "voice": vid,
                "text": sent,
                "spoken": spoken,
                "parts": parts,
                "expansion": len(dict_text) - len(sent),
                "gap_after": GAPS[kind],
            })
    if not items:
        raise RenderError("empty_text", status=400)
    if not any(_SPEAKABLE.search(it["spoken"]) for it in items):
        raise RenderError("empty_audio", status=422)
    items[-1]["gap_after"] = 0.0
    return items


def billable_chars(req: RenderRequest, items: list[dict] | None = None, engine=None) -> int:
    """Số ký tự tính phí: ký tự của văn bản + phần từ điển phát âm làm dài thêm (nếu có)."""
    base = count_chars(req.text)
    if items is None:
        if not req.dictionary:
            return base
        items = plan(req, engine)
    extra = sum(max(0, int(it.get("expansion", 0))) for it in items)
    return base + extra


class _PcmSpool:
    """Ghi float32 từng câu xuống file tạm thay vì giữ cả bản trong RAM (job dài hàng giờ âm thanh).

    Khoảng lặng chỉ được ghi khi có âm thanh tiếp theo → không còn khoảng lặng thừa ở cuối.
    """

    def __init__(self, sr: int):
        self.sr = sr
        self.f = tempfile.NamedTemporaryFile(prefix="dattts-", suffix=".f32", delete=False)
        self.path = self.f.name
        self.samples = 0
        self._pending = 0

    def gap(self, seconds: float) -> None:
        self._pending += max(0, int(round(seconds * self.sr)))

    def flush_gap(self) -> None:
        if self._pending and self.samples:
            self.f.write(np.zeros(self._pending, np.float32).tobytes())
            self.samples += self._pending
        self._pending = 0

    def write(self, wav: np.ndarray) -> None:
        self.flush_gap()
        a = np.asarray(wav, dtype=np.float32).reshape(-1)
        np.clip(a, -1.0, 1.0, out=a)
        self.f.write(a.tobytes())
        self.samples += a.size

    def close(self) -> None:
        if not self.f.closed:
            self.f.close()

    def remove(self) -> None:
        self.close()
        try:
            os.unlink(self.path)
        except OSError:
            pass


def render_to_file(req: RenderRequest, engine, out_path: str,
                   progress: Callable[[int, int], None] | None = None) -> dict:
    """Như ``render`` nhưng ghi âm thanh đã mã hoá vào ``out_path`` (không giữ cả bản trong RAM)."""
    items = plan(req, engine)
    sr = int(engine.sample_rate)
    speed = float(req.speed)
    total = len(items)
    trim = bool(getattr(engine, "trim_silence", True))
    segments: list[dict] = []
    spool = _PcmSpool(sr)
    try:
        for i, it in enumerate(items):
            pieces: list[np.ndarray] = []
            for part in it.get("parts") or [it["spoken"]]:
                try:
                    speakable = bool(_SPEAKABLE.search(part))
                    w = engine.synth(part, it["voice"]) if speakable else np.zeros(0, np.float32)
                except RenderError:
                    raise
                except Exception as e:  # lỗi engine → lỗi máy chủ có thông báo thân thiện
                    raise RenderError("engine_error", status=500) from e
                w = np.asarray(w, dtype=np.float32).reshape(-1)
                if trim and w.size:
                    # Engine tự đệm ~0,2 s lặng quanh mỗi câu → cắt để khoảng nghỉ đúng như GAPS.
                    w = audio.trim_silence(w, sr)
                if w.size:
                    if pieces:
                        pieces.append(audio.silence(sr, GAPS["clause"]))
                    pieces.append(w)
            wav = audio.concat(pieces) if pieces else np.zeros(0, np.float32)
            del pieces
            if wav.size:
                wav = audio.apply_speed(wav, sr, speed)
            if wav.size:
                spool.flush_gap()
                start = spool.samples
                spool.write(wav)
                segments.append({
                    "index": len(segments),
                    "voice": it["voice"],
                    "text": it["text"],
                    "start": round(start / sr, 3),
                    "end": round(spool.samples / sr, 3),
                })
                gap = it["gap_after"] / speed
            else:
                gap = EMPTY_GAP if i < total - 1 else 0.0
            del wav
            if gap > 0 and i < total - 1:
                spool.gap(gap)
            if progress is not None:
                progress(i + 1, total)
        spool.close()
        if not segments or spool.samples == 0:
            raise RenderError("empty_audio", status=422, key="empty_audio.no_output")
        fmt = req.format.lower()
        audio.encode_file(spool.path, sr, fmt, out_path)
        duration = round(spool.samples / sr, 3)
    finally:
        spool.remove()
    return {
        "id": request_key(req, getattr(engine, "name", "unknown")),
        "chars": billable_chars(req, items),
        "duration": duration,
        "format": fmt,
        "segments": segments,
        "srt": audio.to_srt(segments),
        "vtt": audio.to_vtt(segments),
    }


def render(req: RenderRequest, engine, progress: Callable[[int, int], None] | None = None) -> dict:
    fd, out = tempfile.mkstemp(prefix="dattts-", suffix="." + (req.format or "mp3").lower())
    os.close(fd)
    try:
        res = render_to_file(req, engine, out, progress)
        with open(out, "rb") as f:
            res["audio"] = f.read()
    finally:
        try:
            os.unlink(out)
        except OSError:
            pass
    return res
