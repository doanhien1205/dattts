"""Kiểm tra cấu trúc modal_app (không deploy, không cần token) và ModalRenderer với worker giả."""

import pytest

modal = pytest.importorskip("modal")

from fastapi.testclient import TestClient  # noqa: E402

from dattts.api import create_app  # noqa: E402
from dattts.engine import FakeEngine  # noqa: E402
from dattts.settings import Settings  # noqa: E402
from dattts.store import LocalStore, render_and_store, request_from_dict, run_job  # noqa: E402


def test_modal_app_structure():
    import modal_app

    assert modal_app.app.name == "dattts"
    assert isinstance(modal_app.app, modal.App)
    assert isinstance(modal_app.web, modal.Function) and isinstance(modal_app.download_models, modal.Function)
    for m in ("render", "run_job", "sample", "voices"):
        assert hasattr(modal_app.TTSWorker, m)
    assert modal_app.MODEL_DIR == "/models" and modal_app.FILES_DIR == "/files"


class _Remote:
    def __init__(self, fn):
        self.fn = fn
        self.spawned = []

    def remote(self, *a):
        return self.fn(*a)

    def spawn(self, *a):
        self.spawned.append(a)
        return self.fn(*a)


class _FakeWorker:
    """Giống TTSWorker nhưng chạy tại chỗ với FakeEngine (đồng bộ)."""

    def __init__(self, store):
        from dattts.render import RenderError

        eng = FakeEngine()
        self.engine = eng

        def _render(d):
            try:
                return render_and_store(eng, store, request_from_dict(d))
            except RenderError as e:
                return {"error": {"code": e.code, "message": e.message, "status": e.status}}

        self.render = _Remote(_render)
        self.run_job = _Remote(lambda jid, d: run_job(store, jid, request_from_dict(d),
                                                      lambda r, p: render_and_store(eng, store, r, p))["status"])
        self.sample = _Remote(lambda vid: b"ID3-fake-sample")


def test_modal_renderer_end_to_end(tmp_path):
    import modal_app

    store = LocalStore(str(tmp_path))
    worker = _FakeWorker(store)
    r = modal_app.ModalRenderer(store, worker=worker)
    assert r.engine_name() == "vieneu-v3-turbo"
    assert len(r.voices()) == 25  # danh mục giọng thật đọc tại chỗ, không cần mô hình
    # Danh mục là VieNeu nhưng worker giả dùng FakeEngine → dùng giọng có ở cả hai
    c = TestClient(create_app(r, store, Settings.from_env({}, web_dir=None)))
    assert c.get("/api/health").json()["voices"] == 25
    s = c.get("/api/voices/truc-ly/sample.mp3")
    assert s.status_code == 200 and s.content == b"ID3-fake-sample"
    ok = c.post("/api/tts", json={"text": "Xin chào Modal."})
    assert ok.status_code == 200, ok.text
    assert c.get(ok.json()["audio_url"]).status_code == 200
    j = c.post("/api/jobs", json={"text": "Một câu nền."}).json()
    assert worker.run_job.spawned and worker.run_job.spawned[0][0] == j["job_id"]
    assert c.get(f"/api/jobs/{j['job_id']}").json()["status"] == "done"


def test_modal_renderer_maps_worker_errors(tmp_path):
    import modal_app
    from dattts.render import RenderError, RenderRequest

    class W:
        render = _Remote(lambda d: {"error": {"code": "empty_audio", "message": "Không tạo được.", "status": 422}})

    r = modal_app.ModalRenderer(LocalStore(str(tmp_path)), worker=W())
    with pytest.raises(RenderError) as ei:
        r.render(RenderRequest(text="x"))
    assert ei.value.code == "empty_audio" and ei.value.status == 422
