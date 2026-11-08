import json
import threading
import time

import pytest

from dattts.engine import FakeEngine
from dattts.render import RenderError, RenderRequest, request_key
from dattts.store import (
    LocalRenderer,
    LocalStore,
    Renderer,
    Store,
    job_key,
    load_meta,
    render_and_store,
    request_from_dict,
    run_job,
    safe_blob_name,
)


def test_local_store_kv_roundtrip_and_persistence(tmp_path):
    s = LocalStore(str(tmp_path))
    assert isinstance(s, Store)
    assert s.kv_get("x") is None
    s.kv_set("x", {"a": [1, 2], "b": "Trúc Ly"})
    assert s.kv_get("x") == {"a": [1, 2], "b": "Trúc Ly"}
    assert s.kv_incr("n", 5) == 5
    assert s.kv_incr("n", -2) == 3
    s2 = LocalStore(str(tmp_path))  # đọc lại từ kv.json
    assert s2.kv_get("x")["b"] == "Trúc Ly"
    assert s2.kv_get("n") == 3
    assert json.loads((tmp_path / "kv.json").read_text(encoding="utf-8"))["n"] == 3


def test_local_store_kv_returns_copies(tmp_path):
    s = LocalStore(str(tmp_path))
    s.kv_set("l", [1])
    v = s.kv_get("l")
    v.append(2)
    assert s.kv_get("l") == [1]


def test_local_store_kv_incr_is_atomic_across_threads(tmp_path):
    s = LocalStore(str(tmp_path))

    def work():
        for _ in range(50):
            s.kv_incr("c", 1)

    ts = [threading.Thread(target=work) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert s.kv_get("c") == 400


def test_local_store_blobs(tmp_path):
    s = LocalStore(str(tmp_path))
    assert not s.blob_exists("a.mp3")
    assert s.blob_get("a.mp3") is None
    s.blob_put("a.mp3", b"\x00\x01")
    assert s.blob_exists("a.mp3")
    assert s.blob_get("a.mp3") == b"\x00\x01"
    assert (tmp_path / "files" / "a.mp3").read_bytes() == b"\x00\x01"


@pytest.mark.parametrize("bad", ["../kv.json", "..", "a/b.mp3", "/etc/passwd", ".hidden", "a..b", "", "x\\y"])
def test_safe_blob_name_rejects_traversal(tmp_path, bad):
    with pytest.raises(ValueError):
        safe_blob_name(bad)
    s = LocalStore(str(tmp_path / "d"))
    (tmp_path / "secret.txt").write_text("bí mật")
    assert s.blob_get(bad) is None
    assert s.blob_exists(bad) is False
    with pytest.raises(ValueError):
        s.blob_put(bad, b"x")


def test_render_and_store_writes_blobs_and_caches(tmp_path):
    s = LocalStore(str(tmp_path))
    eng = FakeEngine()
    req = RenderRequest(text="Xin chào. Mình là DatTTS.", format="wav")
    meta = render_and_store(eng, s, req)
    rid = meta["id"]
    assert rid == request_key(req, "fake")
    for ext in ("wav", "srt", "vtt", "json"):
        assert s.blob_exists(f"{rid}.{ext}"), ext
    assert "audio" not in meta and meta["format"] == "wav" and len(meta["segments"]) == 2
    assert json.loads(s.blob_get(f"{rid}.json"))["duration"] == meta["duration"]
    assert load_meta(s, rid, "wav")["id"] == rid
    assert load_meta(s, rid, "mp3") is None
    n = len(eng.calls)
    again = render_and_store(eng, s, req)
    assert again["id"] == rid and len(eng.calls) == n  # cache: không gọi engine nữa


def test_run_job_done_with_progress(tmp_path):
    s = LocalStore(str(tmp_path))
    eng = FakeEngine()
    seen = []

    def fn(req, progress):
        meta = render_and_store(eng, s, req, progress)
        seen.append(s.kv_get(job_key("job12345")))
        return meta

    s.kv_set(job_key("job12345"), {"job_id": "job12345", "status": "queued"})
    rec = run_job(s, "job12345", RenderRequest(text="Một. Hai. Ba."), fn, min_interval=0)
    assert rec["status"] == "done" and rec["progress"] == 1.0
    assert rec["done"] == rec["total"] == 3
    assert s.kv_get(job_key("job12345"))["result"]["id"] == rec["result"]["id"]
    assert seen[0]["status"] == "running" and seen[0]["done"] == 3


def test_run_job_error_refunds_quota(tmp_path):
    s = LocalStore(str(tmp_path))
    s.kv_incr("quota:x", 100)
    s.kv_set(job_key("jobabcdef"), {"job_id": "jobabcdef", "status": "queued", "quota_key": "quota:x",
                                     "charged": 40})

    def boom(req, progress):
        raise RenderError("engine_error", "Máy đọc gặp sự cố.", 500)

    rec = run_job(s, "jobabcdef", RenderRequest(text="x"), boom)
    assert rec["status"] == "error" and rec["error"]["code"] == "engine_error"
    assert s.kv_get("quota:x") == 60 and rec["refunded"]


def test_request_from_dict_defaults():
    r = request_from_dict({"text": "Chào"})
    assert r.voice == "truc-ly" and r.speed == 1.0 and r.format == "mp3" and r.dictionary == []
    r = request_from_dict({"text": "a", "voice": "Thiện Minh", "speed": "1.2", "format": "OGG"})
    assert r.speed == 1.2 and r.format == "ogg"


class _CountingEngine(FakeEngine):
    def __init__(self):
        super().__init__()
        self.active = 0
        self.max_active = 0
        self._g = threading.Lock()

    def synth(self, text, voice_id):
        with self._g:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(0.01)
        try:
            return super().synth(text, voice_id)
        finally:
            with self._g:
                self.active -= 1


def test_local_renderer_never_runs_engine_concurrently(tmp_path):
    eng = _CountingEngine()
    r = LocalRenderer(eng, LocalStore(str(tmp_path)))
    assert isinstance(r, Renderer)
    errors = []

    def work(i):
        try:
            r.render(RenderRequest(text=f"Câu số {i}. Câu tiếp theo {i}."))
        except Exception as e:  # pragma: no cover
            errors.append(e)

    ts = [threading.Thread(target=work, args=(i,)) for i in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    r.close()
    assert not errors
    assert eng.max_active == 1
    assert len(eng.calls) == 12


def test_local_renderer_sample_and_engine_name(tmp_path):
    r = LocalRenderer(FakeEngine(), LocalStore(str(tmp_path)))
    assert r.engine_name() == "fake"
    data = r.sample("thien-minh")
    assert data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")
    assert r.voice_name("thien-minh") == "Thiện Minh"
    r.close()
