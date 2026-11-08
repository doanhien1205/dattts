"""Lưu trữ (KV + blob) và bộ dựng âm thanh (Renderer) dùng chung cho API.

- ``LocalStore``: thư mục trên đĩa (``root/kv.json`` + ``root/files/``).
- ``ModalStore``: ``modal.Dict`` cho KV, ``modal.Volume`` cho file.
- ``LocalRenderer``: engine chạy trong tiến trình; mọi lần gọi ``engine.synth`` đi qua
  một ``ThreadPoolExecutor(max_workers=1)`` nên engine không bao giờ bị dùng song song.
- ``render_and_store`` / ``run_job``: logic dùng chung với worker Modal.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import shutil
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable

from . import render as _render
from .render import RenderError, RenderRequest

log = logging.getLogger("dattts.store")

__all__ = [
    "Store",
    "LocalStore",
    "ModalStore",
    "Renderer",
    "LocalRenderer",
    "render_and_store",
    "derive_format",
    "load_meta",
    "run_job",
    "job_key",
    "sample_text",
    "sample_blob_name",
    "request_from_dict",
    "safe_blob_name",
]

_BLOB_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def safe_blob_name(name: str) -> str:
    """Chỉ cho phép tên file phẳng (không thư mục, không '..')."""
    if not isinstance(name, str) or not _BLOB_NAME.fullmatch(name) or ".." in name:
        raise ValueError(f"tên blob không hợp lệ: {name!r}")
    return name


# ───────────────────────────── Store ─────────────────────────────


@runtime_checkable
class Store(Protocol):
    def kv_get(self, key: str) -> Any | None: ...

    def kv_set(self, key: str, value: Any) -> None: ...

    def kv_incr(self, key: str, amount: int) -> int: ...

    def blob_put(self, name: str, data: bytes) -> None: ...

    def blob_get(self, name: str) -> bytes | None: ...

    def blob_exists(self, name: str) -> bool: ...


class LocalStore:
    """KV trong ``root/kv.json`` (ghi nguyên tử, có khoá), blob trong ``root/files/``."""

    def __init__(self, root: str):
        self.root = Path(root)
        self.files = self.root / "files"
        self.files.mkdir(parents=True, exist_ok=True)
        self._kv_path = self.root / "kv.json"
        self._lock = threading.RLock()
        self._kv: dict[str, Any] = {}
        if self._kv_path.is_file():
            try:
                self._kv = json.loads(self._kv_path.read_text(encoding="utf-8")) or {}
            except (OSError, ValueError):
                log.warning("kv.json hỏng, bắt đầu lại từ rỗng")
                self._kv = {}

    def _flush(self) -> None:
        tmp = self._kv_path.with_suffix(f".tmp{os.getpid()}.{threading.get_ident()}")
        tmp.write_text(json.dumps(self._kv, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self._kv_path)

    def kv_get(self, key: str) -> Any | None:
        with self._lock:
            v = self._kv.get(key)
            # bản sao sâu để người gọi sửa thoải mái
            return None if v is None else json.loads(json.dumps(v))

    def kv_set(self, key: str, value: Any) -> None:
        with self._lock:
            self._kv[key] = json.loads(json.dumps(value))
            self._flush()

    def kv_delete(self, key: str) -> None:
        with self._lock:
            if self._kv.pop(key, None) is not None:
                self._flush()

    def kv_incr(self, key: str, amount: int) -> int:
        with self._lock:
            v = int(self._kv.get(key) or 0) + int(amount)
            self._kv[key] = v
            self._flush()
            return v

    def _path(self, name: str) -> Path:
        return self.files / safe_blob_name(name)

    def blob_put(self, name: str, data: bytes) -> None:
        p = self._path(name)
        tmp = p.with_name(f".{p.name}.tmp{os.getpid()}.{threading.get_ident()}")
        tmp.write_bytes(data)
        os.replace(tmp, p)

    def blob_put_file(self, name: str, src: str) -> None:
        """Như ``blob_put`` nhưng chép từ file (không nạp cả file vào RAM)."""
        p = self._path(name)
        tmp = p.with_name(f".{p.name}.tmp{os.getpid()}.{threading.get_ident()}")
        shutil.copyfile(src, tmp)
        os.replace(tmp, p)

    def blob_get(self, name: str) -> bytes | None:
        try:
            return self._path(name).read_bytes()
        except (FileNotFoundError, ValueError):
            return None

    def blob_path(self, name: str) -> Path | None:
        """Đường dẫn file blob (để phục vụ theo luồng/Range) hoặc None nếu chưa có."""
        try:
            p = self._path(name)
        except ValueError:
            return None
        return p if p.is_file() else None

    def blob_exists(self, name: str) -> bool:
        try:
            return self._path(name).is_file()
        except ValueError:
            return False


class ModalStore:
    """KV = ``modal.Dict``; blob = file trong ``modal.Volume`` gắn ở ``mount``.

    Ghi blob → ``commit()``; đọc trượt → ``reload()`` rồi thử lại (file do container khác ghi).
    ``kv_incr`` dùng khoá phân tán trên chính Dict (``put(..., skip_if_exists=True)`` là phép
    "tạo nếu chưa có" nguyên tử phía máy chủ) → cộng/trừ quota đúng cả khi nhiều container
    (web tự co giãn, worker hoàn quota) cùng ghi một khoá.
    Lưu ý: mục trong Dict hết hạn sau 7 ngày không dùng — dữ liệu cần giữ lâu (waitlist)
    được API sao lưu thêm vào blob.
    """

    LOCK_TTL = 10.0      # khoá bị bỏ quá lâu (container chết giữa chừng) → được chiếm lại
    LOCK_WAIT = 8.0      # chờ tối đa rồi mới ghi không khoá (ghi log)
    RELOAD_RETRIES = 3

    def __init__(self, dict_name: str = "dattts-kv", volume_name: str = "dattts-files", mount: str = "/files",
                 *, dict_obj=None, volume_obj=None):
        if dict_obj is None or volume_obj is None:
            import modal

            dict_obj = dict_obj if dict_obj is not None else modal.Dict.from_name(dict_name, create_if_missing=True)
            volume_obj = volume_obj if volume_obj is not None else modal.Volume.from_name(volume_name,
                                                                                          create_if_missing=True)
        self.dict = dict_obj
        self.volume = volume_obj
        self.mount = Path(mount)
        self._lock = threading.RLock()
        self._reload_lock = threading.Lock()
        self._last_reload = 0.0  # thời điểm BẮT ĐẦU lần reload thành công gần nhất

    def kv_get(self, key: str) -> Any | None:
        return self.dict.get(key, None)

    def kv_set(self, key: str, value: Any) -> None:
        self.dict.put(key, value)

    def kv_delete(self, key: str) -> None:
        try:
            self.dict.pop(key)
        except KeyError:
            pass

    def _acquire(self, lock_key: str) -> str | None:
        token = uuid.uuid4().hex
        deadline = time.time() + self.LOCK_WAIT
        delay = 0.005
        while True:
            if self.dict.put(lock_key, {"o": token, "t": time.time()}, skip_if_exists=True):
                return token
            cur = self.dict.get(lock_key, None)
            if isinstance(cur, dict) and time.time() - float(cur.get("t") or 0) > self.LOCK_TTL:
                try:  # chủ khoá đã chết → gỡ khoá cũ (chỉ khi vẫn là khoá đó)
                    if self.dict.get(lock_key, None) == cur:
                        self.dict.pop(lock_key)
                except KeyError:
                    pass
                continue
            if time.time() > deadline:
                log.warning("kv_incr: chờ khoá %s quá lâu, ghi không khoá", lock_key)
                return None
            time.sleep(delay + random.random() * delay)
            delay = min(delay * 2, 0.1)

    def _release(self, lock_key: str, token: str | None) -> None:
        if token is None:
            return
        try:
            cur = self.dict.get(lock_key, None)
            if isinstance(cur, dict) and cur.get("o") == token:
                self.dict.pop(lock_key)
        except Exception:  # pragma: no cover
            log.exception("không gỡ được khoá %s", lock_key)

    def kv_incr(self, key: str, amount: int) -> int:
        lock_key = f"lock:{key}"
        with self._lock:  # trong container: tuần tự; giữa các container: khoá trên Dict
            token = self._acquire(lock_key)
            try:
                v = int(self.dict.get(key, 0) or 0) + int(amount)
                self.dict.put(key, v)
                return v
            finally:
                self._release(lock_key, token)

    def _path(self, name: str) -> Path:
        return self.mount / safe_blob_name(name)

    def _reload(self, since: float) -> None:
        """Reload Volume để thấy file container khác vừa commit.

        Nhiều request trượt cùng lúc chỉ cần MỘT reload bắt đầu sau thời điểm trượt (``since``):
        request đến sau chờ reload đang chạy rồi dùng luôn kết quả, không bị bỏ qua.
        Reload lỗi (ví dụ đang có file mở) → thử lại vài lần.
        """
        with self._reload_lock:
            if self._last_reload >= since:
                return
            for attempt in range(self.RELOAD_RETRIES):
                started = time.time()
                try:
                    self.volume.reload()
                    self._last_reload = started
                    return
                except Exception as e:  # pragma: no cover - chỉ chạy trên Modal
                    log.warning("volume.reload() lỗi (lần %d): %s", attempt + 1, e)
                    time.sleep(0.1 * (attempt + 1))

    def blob_put(self, name: str, data: bytes) -> None:
        p = self._path(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.tmp{os.getpid()}.{threading.get_ident()}")
        tmp.write_bytes(data)
        os.replace(tmp, p)
        self._commit()

    def blob_put_file(self, name: str, src: str) -> None:
        p = self._path(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.tmp{os.getpid()}.{threading.get_ident()}")
        shutil.copyfile(src, tmp)
        os.replace(tmp, p)
        self._commit()

    def _commit(self) -> None:
        with self._lock:
            try:
                self.volume.commit()
            except Exception as e:  # pragma: no cover
                log.warning("volume.commit() lỗi: %s", e)

    def blob_path(self, name: str) -> Path | None:
        try:
            p = self._path(name)
        except ValueError:
            return None
        if p.is_file():
            return p
        self._reload(time.time())
        return p if p.is_file() else None

    def blob_get(self, name: str) -> bytes | None:
        p = self.blob_path(name)
        if p is None:
            return None
        try:
            return p.read_bytes()
        except FileNotFoundError:
            return None

    def blob_exists(self, name: str) -> bool:
        return self.blob_path(name) is not None


# ───────────────────────────── Dùng chung ─────────────────────────────


def job_key(job_id: str) -> str:
    return f"job:{job_id}"


def sample_blob_name(voice_id: str) -> str:
    return f"sample-{voice_id}.mp3"


def sample_text(voice_name: str) -> str:
    return f"Xin chào, mình là {voice_name}. Rất vui được đọc cho bạn nghe hôm nay."


def request_from_dict(d: dict) -> RenderRequest:
    return RenderRequest(
        text=d.get("text", ""),
        voice=d.get("voice") or _render.DEFAULT_VOICE_ID,
        speed=float(d.get("speed", 1.0)),
        format=(d.get("format") or "mp3").lower(),
        dictionary=list(d.get("dictionary") or []),
    )


def load_meta(store, key: str, fmt: str) -> dict | None:
    """Metadata của bản đã dựng (cache) nếu cả file âm thanh lẫn ``<id>.json`` đều có."""
    if not store.blob_exists(f"{key}.{fmt}"):
        return None
    raw = store.blob_get(f"{key}.json")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _put_file(store, name: str, path: str) -> None:
    put_file = getattr(store, "blob_put_file", None)
    if put_file is not None:
        put_file(name, path)
    else:
        with open(path, "rb") as f:
            store.blob_put(name, f.read())


def _store_result(store, rid: str, fmt: str, audio_path: str, res: dict, engine_name: str) -> dict:
    _put_file(store, f"{rid}.{fmt}", audio_path)
    store.blob_put(f"{rid}.srt", res["srt"].encode("utf-8"))
    store.blob_put(f"{rid}.vtt", res["vtt"].encode("utf-8"))
    meta = {
        "id": rid,
        "chars": res["chars"],
        "duration": res["duration"],
        "format": fmt,
        "segments": res["segments"],
        "engine": engine_name,
        "created_at": round(time.time(), 3),
    }
    store.blob_put(f"{rid}.json", json.dumps(meta, ensure_ascii=False).encode("utf-8"))
    return meta


def render_and_store(engine, store, req: RenderRequest, progress: Callable[[int, int], None] | None = None) -> dict:
    """Dựng âm thanh và ghi blob ``<id>.<fmt>``, ``<id>.srt``, ``<id>.vtt``, ``<id>.json``.

    Trả metadata (không có bytes). ``<id>.json`` ghi cuối cùng: có nó nghĩa là bộ file đã đủ.
    Âm thanh đi qua file tạm (không giữ cả bản trong RAM).
    """
    fmt = (req.format or "mp3").lower()
    name = getattr(engine, "name", "unknown")
    key = _render.request_key(req, name)
    cached = load_meta(store, key, fmt)
    if cached is not None:
        if progress is not None:
            progress(1, 1)
        return cached
    fd, out = tempfile.mkstemp(prefix="dattts-", suffix="." + fmt)
    os.close(fd)
    try:
        res = _render.render_to_file(req, engine, out, progress)
        return _store_result(store, res["id"], fmt, out, res, name)
    finally:
        try:
            os.unlink(out)
        except OSError:
            pass


# Ưu tiên nguồn chất lượng cao nhất khi đổi định dạng
_SOURCE_ORDER = ("wav", "ogg", "mp3")


def derive_format(store, req: RenderRequest, engine_name: str) -> dict | None:
    """Đã có bản dựng của CÙNG yêu cầu ở định dạng khác → đổi định dạng (ffmpeg), không dựng lại giọng.

    Trả metadata mới, hoặc None nếu chưa có bản nào. Bản mới nghe y hệt bản người dùng đã nghe.
    """
    from dataclasses import replace

    from . import audio as _audio

    fmt = (req.format or "mp3").lower()
    rid = _render.request_key(req, engine_name)
    for src_fmt in _SOURCE_ORDER:
        if src_fmt == fmt:
            continue
        src_key = _render.request_key(replace(req, format=src_fmt), engine_name)
        meta = load_meta(store, src_key, src_fmt)
        if meta is None:
            continue
        path_fn = getattr(store, "blob_path", None)
        src_path = path_fn(f"{src_key}.{src_fmt}") if path_fn else None
        tmp_src = None
        fd, out = tempfile.mkstemp(prefix="dattts-", suffix="." + fmt)
        os.close(fd)
        try:
            if src_path is None:
                data = store.blob_get(f"{src_key}.{src_fmt}")
                if data is None:
                    continue
                fd2, tmp_src = tempfile.mkstemp(prefix="dattts-src-", suffix="." + src_fmt)
                with os.fdopen(fd2, "wb") as f:
                    f.write(data)
                src_path = tmp_src
            _audio.transcode_file(str(src_path), fmt, out)
            res = {**meta, "srt": _audio.to_srt(meta["segments"]),
                   "vtt": _audio.to_vtt(meta["segments"])}
            return _store_result(store, rid, fmt, out, res, meta.get("engine") or engine_name)
        except Exception:
            log.exception("không đổi được %s → %s", src_key, fmt)
            return None
        finally:
            for f in (out, tmp_src):
                if f:
                    try:
                        os.unlink(f)
                    except OSError:
                        pass
    return None


_JOB_ACTIVE = ("queued", "running")


def run_job(store, job_id: str, req: RenderRequest, render_fn: Callable[..., dict],
            min_interval: float = 0.5) -> dict:
    """Chạy một job: cập nhật ``kv['job:<id>']`` (status/progress/result); lỗi → hoàn quota (một lần).

    Job đã bị API đánh dấu hỏng/hoàn quota (quá hạn khi còn xếp hàng) thì KHÔNG chạy nữa.
    Mỗi lần ghi đều đọc lại bản ghi để không đè mất thay đổi từ API.
    """
    k = job_key(job_id)
    rec = store.kv_get(k) or {"job_id": job_id, "created_at": time.time(), "status": "queued"}
    if rec.get("status", "queued") not in _JOB_ACTIVE or rec.get("refunded"):
        log.info("bỏ qua job %s (status=%s)", job_id, rec.get("status"))
        return rec
    rec.update(status="running", progress=0.0, started_at=time.time(), updated_at=time.time())
    store.kv_set(k, rec)
    last = [0.0]

    def write(**fields) -> dict:
        cur = store.kv_get(k) or rec
        cur.update(fields)
        store.kv_set(k, cur)
        return cur

    def progress(done: int, total: int) -> None:
        now = time.time()
        if done < total and now - last[0] < min_interval:
            return
        last[0] = now
        write(done=int(done), total=int(total), progress=round(done / total, 4) if total else 0.0,
              updated_at=now)

    try:
        meta = render_fn(req, progress)
        final = {"status": "done", "progress": 1.0, "result": meta, "error": None}
    except RenderError as e:
        final = {"status": "error", "error": e.to_dict()}
    except Exception:  # pragma: no cover - phòng hờ
        log.exception("job %s lỗi", job_id)
        final = {"status": "error",
                 "error": {"code": "engine_error",
                           "message": "Máy đọc gặp sự cố khi tạo giọng. Bạn thử lại sau ít phút nhé."}}
    cur = store.kv_get(k) or rec
    cur.update(final, updated_at=time.time())
    if cur["status"] == "done":
        cur.pop("error", None)
        cur["total"] = cur.get("total") or 1
        cur["done"] = cur["total"]
    qk, charged = cur.get("quota_key"), int(cur.get("charged") or 0)
    try:
        if cur["status"] == "error" and qk and charged and not cur.get("refunded"):
            store.kv_incr(qk, -charged)
            cur["refunded"] = True
        elif cur["status"] == "done" and qk and charged and cur.get("refunded"):
            # API đã hoàn quota vì tưởng job hỏng, nhưng job vẫn xong → tính lại cho đúng.
            store.kv_incr(qk, charged)
            cur["refunded"] = False
    except Exception:  # pragma: no cover
        log.exception("không cập nhật được quota cho job %s", job_id)
    store.kv_set(k, cur)
    return cur


# ───────────────────────────── Renderer ─────────────────────────────


@runtime_checkable
class Renderer(Protocol):
    def voices(self) -> list[dict]: ...

    def engine_name(self) -> str: ...

    def render(self, req: RenderRequest) -> dict: ...

    def submit(self, job_id: str, req: RenderRequest) -> None: ...

    def sample(self, voice_id: str) -> bytes: ...


class _SerialEngine:
    """Bọc engine: ``synth`` luôn chạy trên executor 1 luồng; thuộc tính khác chuyển thẳng."""

    def __init__(self, engine, executor: ThreadPoolExecutor):
        self._engine = engine
        self._executor = executor

    def synth(self, text: str, voice_id: str):
        return self._executor.submit(self._engine.synth, text, voice_id).result()

    def __getattr__(self, item):
        return getattr(self._engine, item)


class LocalRenderer:
    """Engine trong tiến trình.

    Chỉ ``engine.synth`` bị tuần tự hoá (ThreadPoolExecutor(max_workers=1)); phần còn lại
    (ghép, encode ffmpeg) chạy ở luồng gọi. Nhờ vậy một job dài không chặn hẳn /api/tts:
    các câu của hai yêu cầu được xếp xen kẽ nhau trong hàng đợi của engine.
    """

    def __init__(self, engine, store, job_workers: int = 2):
        self.engine = engine
        self.store = store
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dattts-engine")
        self._jobs = ThreadPoolExecutor(max_workers=max(1, job_workers), thread_name_prefix="dattts-job")
        self._serial = _SerialEngine(engine, self._executor)
        self._voices: list[dict] | None = None

    def voices(self) -> list[dict]:
        if self._voices is None:
            self._voices = self.engine.voices()
        return [dict(v) for v in self._voices]

    def voice_name(self, voice_id: str) -> str:
        fn = getattr(self.engine, "voice_name", None)
        if fn is not None:
            return fn(voice_id)
        for v in self.voices():
            if v["id"] == voice_id:
                return v["name"]
        raise KeyError(voice_id)

    def engine_name(self) -> str:
        return str(getattr(self.engine, "name", "unknown"))

    def render(self, req: RenderRequest) -> dict:
        return render_and_store(self._serial, self.store, req)

    def submit(self, job_id: str, req: RenderRequest) -> None:
        self._jobs.submit(run_job, self.store, job_id, req,
                          lambda r, p: render_and_store(self._serial, self.store, r, p))

    def sample(self, voice_id: str) -> bytes:
        name = next((v["name"] for v in self.voices() if v["id"] == voice_id), voice_id)
        res = _render.render(RenderRequest(text=sample_text(name), voice=voice_id, format="mp3"), self._serial)
        return res["audio"]

    def warmup(self) -> None:
        """Nạp mô hình trước (nếu engine hỗ trợ) để request đầu không phải chờ."""
        load = getattr(self.engine, "load", None)
        if load is not None:
            self._executor.submit(load).result()

    def close(self) -> None:
        self._jobs.shutdown(wait=False, cancel_futures=True)
        self._executor.shutdown(wait=False, cancel_futures=True)


def req_to_dict(req: RenderRequest) -> dict:
    return asdict(req)
