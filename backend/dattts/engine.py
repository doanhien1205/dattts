"""Engine TTS: giao thức chung, VieNeuEngine (VieNeu-TTS v3 Turbo) và FakeEngine (kiểm thử)."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import unicodedata
import zlib
from pathlib import Path
from typing import Protocol, runtime_checkable

import numpy as np

log = logging.getLogger("dattts.engine")

__all__ = [
    "Engine",
    "VieNeuEngine",
    "FakeEngine",
    "voice_id_from_name",
    "prefetch_onnx_models",
    "DEFAULT_VOICE_ID",
    "STYLE_LABELS",
]

DEFAULT_VOICE_ID = "truc-ly"

STYLE_LABELS = {"tu_nhien": "Tự nhiên", "tin_tuc": "Tin tức", "doc_truyen": "Kể chuyện"}


@runtime_checkable
class Engine(Protocol):
    name: str
    sample_rate: int

    def voices(self) -> list[dict]: ...

    def synth(self, text: str, voice_id: str) -> np.ndarray: ...


def voice_id_from_name(name: str) -> str:
    """"Trúc Ly"→"truc-ly", "Adam bựa"→"adam-bua", "Thiền Tâm Đức"→"thien-tam-duc"."""
    s = unicodedata.normalize("NFD", str(name or "")).replace("đ", "d").replace("Đ", "D")
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = unicodedata.normalize("NFC", s).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


_REGION_WORDS = {"Bắc", "Trung", "Nam"}


def _vi_description(desc: str) -> str:
    """"Nữ · Nam · Phong cách tin tức" → "Nữ · miền Nam · …": "Nam" đứng một mình dễ đọc thành giới tính."""
    parts = [p.strip() for p in str(desc or "").split("·")]
    if len(parts) >= 2 and parts[1] in _REGION_WORDS:
        parts[1] = "miền " + parts[1]
        return " · ".join(parts)
    return str(desc or "")


def _voice_entry(name: str, meta: dict, order: int) -> dict:
    style = meta.get("style") or "tu_nhien"
    featured = meta.get("featured")
    try:
        featured = int(featured) if featured is not None and int(featured) > 0 else None
    except (TypeError, ValueError):
        featured = None
    return {
        "id": voice_id_from_name(name),
        "name": name,
        "gender": meta.get("gender") or "",
        "region": meta.get("region") or "",
        "style": style,
        "style_label": STYLE_LABELS.get(style, style),
        "featured": featured,
        "description": _vi_description(meta.get("description") or ""),
        "_order": order,
        "_aliases": list(meta.get("aliases") or []),
    }


def _sort_and_strip(entries: list[dict]) -> list[dict]:
    entries = sorted(
        entries,
        key=lambda v: (v["featured"] is None, v["featured"] or 0, v["_order"]),
    )
    return [{k: v for k, v in e.items() if not k.startswith("_")} for e in entries]


# ───────────────────────────── VieNeu ─────────────────────────────

# Bẫy onnxruntime ≥1.2x: file .data ngoài đi qua symlink cache HF bị từ chối
# ("External data path escapes model directory"). Vá OnnxV3LiteEngine._fetch để tải
# thẳng vào thư mục thật (local_dir). Vá một lần cho cả tiến trình.
_PATCH_LOCK = threading.Lock()
_PATCHED = False
_MODEL_ROOT: Path | None = None


def _patched_fetch(repo: str, files, subfolder):
    root = _MODEL_ROOT
    if root is None:  # pragma: no cover
        raise RuntimeError("DatTTS: model_dir chưa được thiết lập")
    d = root / repo.replace("/", "__")
    target = d / (subfolder or "")
    for fn in files:
        if (target / fn).is_file():
            continue  # đã có sẵn → không gọi mạng
        from huggingface_hub import hf_hub_download

        try:
            hf_hub_download(repo, fn, subfolder=subfolder or None, local_dir=d)
        except Exception:
            if fn.endswith(".json"):
                continue
            raise
    return target


def _install_fetch_patch(model_dir: str | os.PathLike) -> None:
    global _PATCHED, _MODEL_ROOT
    with _PATCH_LOCK:
        _MODEL_ROOT = Path(model_dir)
        if _PATCHED:
            return
        import vieneu._v3_turbo_engine.onnx_runtime_lite as L

        L.OnnxV3LiteEngine._fetch = staticmethod(_patched_fetch)
        _PATCHED = True


def prefetch_onnx_models(model_dir: str | os.PathLike, precision: str = "int8") -> Path:
    """Tải sẵn file ONNX (đồ thị + codec) vào ``model_dir`` mà KHÔNG nạp mô hình.

    Dùng trên Modal: tải → commit Volume → rồi mới nạp (ONNX Runtime mmap file .data suốt đời phiên,
    commit/reload Volume khi file đang mở sẽ lỗi).
    """
    import vieneu._v3_turbo_engine.onnx_runtime_lite as L

    global _MODEL_ROOT
    with _PATCH_LOCK:
        _MODEL_ROOT = Path(model_dir)
    Path(model_dir).mkdir(parents=True, exist_ok=True)
    sub = {"int8": "onnx_int8", "fp32": "onnx_update"}.get(str(precision).lower(), "onnx_update")
    _patched_fetch(L._V3_REPO, list(L._GRAPH_FILES), sub)
    _patched_fetch(L._CODEC_REPO, list(L._CODEC_FILES), None)
    return Path(model_dir)


def _voices_json_path() -> Path:
    try:
        from importlib.resources import files

        p = files("vieneu").joinpath("assets/voices_v3_turbo.json")
        return Path(str(p))
    except Exception:  # pragma: no cover
        import importlib.util

        spec = importlib.util.find_spec("vieneu")
        return Path(spec.origin).parent / "assets" / "voices_v3_turbo.json"


class VieNeuEngine:
    """VieNeu-TTS v3 Turbo. CPU → ONNX int8/fp32; device="cuda" → PyTorch.

    ``synth`` được khoá: an toàn khi gọi từ nhiều luồng (chạy tuần tự).
    ``lazy=True`` hoãn nạp mô hình tới lần ``synth``/``load`` đầu (``voices()`` không cần mô hình).
    """

    name = "vieneu-v3-turbo"
    sample_rate = 48000

    def __init__(self, model_dir: str, device: str = "cpu", precision: str = "int8", *, lazy: bool = False,
                 threads: int = 0):
        self.model_dir = str(model_dir)
        self.device = (device or "cpu").lower()
        self.precision = precision
        self.threads = int(threads or 0)
        self._tts = None
        self._lock = threading.Lock()
        self._load_lock = threading.Lock()
        self._voices: list[dict] | None = None
        self._id_to_name: dict[str, str] = {}
        self.load_seconds: float | None = None
        if not lazy:
            self.load()

    # Mô hình
    def load(self):
        if self._tts is not None:
            return self._tts
        with self._load_lock:
            if self._tts is not None:
                return self._tts
            t0 = time.time()
            Path(self.model_dir).mkdir(parents=True, exist_ok=True)
            if "cuda" in self.device:
                # PyTorch tải trọng số qua cache HF → đặt cache nằm trong model_dir (Volume trên Modal).
                os.environ.setdefault("HF_HOME", str(Path(self.model_dir) / "hf"))
                from vieneu import Vieneu

                tts = Vieneu(mode="v3turbo", device="cuda")
            else:
                _install_fetch_patch(self.model_dir)
                from vieneu import Vieneu

                tts = Vieneu(mode="v3turbo", device="cpu", backend="onnx", precision=self.precision,
                             threads=self.threads)
            self._tts = tts
            sr = getattr(tts, "sample_rate", None)
            if sr:
                self.sample_rate = int(sr)
            self.load_seconds = round(time.time() - t0, 2)
            log.info("VieNeu loaded in %.1fs (device=%s, precision=%s)", self.load_seconds, self.device,
                     self.precision)
            return tts

    @property
    def loaded(self) -> bool:
        return self._tts is not None

    # Giọng
    def _ensure_voices(self) -> None:
        if self._voices is not None:
            return
        data = json.loads(_voices_json_path().read_text(encoding="utf-8"))
        entries = [_voice_entry(n, m or {}, i) for i, (n, m) in enumerate(data.get("presets", {}).items())]
        id_to_name: dict[str, str] = {}
        for e in entries:
            id_to_name[e["id"]] = e["name"]
        # Tên cũ (alias) vẫn dùng được: "minh-quan" → "Hải Đăng"
        for e in entries:
            for a in e["_aliases"]:
                id_to_name.setdefault(voice_id_from_name(a), e["name"])
        self._id_to_name = id_to_name
        self._voices = _sort_and_strip(entries)

    def voices(self) -> list[dict]:
        self._ensure_voices()
        return [dict(v) for v in self._voices]

    def voice_name(self, voice_id: str) -> str:
        self._ensure_voices()
        vid = voice_id_from_name(voice_id)
        if vid not in self._id_to_name:
            raise KeyError(f"unknown voice: {voice_id}")
        return self._id_to_name[vid]

    def synth(self, text: str, voice_id: str) -> np.ndarray:
        name = self.voice_name(voice_id)
        text = (text or "").strip()
        if not text:
            return np.zeros(0, dtype=np.float32)
        tts = self.load()
        with self._lock:
            wav = tts.infer(text, voice=name)
        return np.asarray(wav, dtype=np.float32).reshape(-1)


# ───────────────────────────── Fake ─────────────────────────────

_FAKE_VOICES = [
    ("Trúc Ly", {"gender": "female", "region": "Bắc", "style": "tu_nhien", "featured": 1,
                 "description": "Nữ · Bắc · Phong cách tự nhiên"}),
    ("Thiện Minh", {"gender": "male", "region": "Bắc", "style": "doc_truyen", "featured": 2,
                    "description": "Nam · Bắc · Phong cách kể chuyện"}),
    ("Thùy Dung", {"gender": "female", "region": "Nam", "style": "tin_tuc", "featured": 3,
                   "description": "Nữ · Nam · Phong cách tin tức"}),
    ("Quang Sơn", {"gender": "male", "region": "Trung", "style": "tu_nhien",
                   "description": "Nam · Trung · Phong cách tự nhiên"}),
]


class FakeEngine:
    """Engine giả tất định: âm sin ~0,06 s/ký tự, tần số theo giọng. Dùng cho test nhanh."""

    name = "fake"
    trim_silence = False  # âm giả không có khoảng lặng đệm; giữ độ dài tất định cho test

    def __init__(self, sample_rate: int = 48000):
        self.sample_rate = int(sample_rate)
        self._voices = _sort_and_strip([_voice_entry(n, m, i) for i, (n, m) in enumerate(_FAKE_VOICES)])
        self._freq = {v["id"]: 180.0 + 40.0 * i for i, v in enumerate(self._voices)}
        self.calls: list[tuple[str, str]] = []

    def voices(self) -> list[dict]:
        return [dict(v) for v in self._voices]

    def synth(self, text: str, voice_id: str) -> np.ndarray:
        vid = voice_id_from_name(voice_id)
        if vid not in self._freq:
            raise KeyError(f"unknown voice: {voice_id}")
        text = (text or "").strip()
        self.calls.append((text, vid))
        if not text:
            return np.zeros(0, dtype=np.float32)
        n = int(round(0.06 * len(text) * self.sample_rate))
        t = np.arange(n, dtype=np.float32) / self.sample_rate
        phase = (zlib.crc32(text.encode("utf-8")) % 628) / 100.0
        wav = 0.3 * np.sin(2 * np.pi * self._freq[vid] * t + phase)
        fade = min(n // 2, int(0.01 * self.sample_rate))
        if fade:
            ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
            wav[:fade] *= ramp
            wav[-fade:] *= ramp[::-1]
        return wav.astype(np.float32)
