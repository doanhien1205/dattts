"""Triển khai DatTTS trên Modal (modal 1.x).

    cd backend
    modal secret create dattts ADMIN_TOKEN=... API_KEYS=key1,key2   # bắt buộc tạo trước (có thể để trống giá trị)
    # Thanh toán Polar (tuỳ chọn; thiếu POLAR_ACCESS_TOKEN → billing tắt, web hiện "Sắp mở bán"):
    modal secret create dattts --force ADMIN_TOKEN=... API_KEYS=... \
        PUBLIC_BASE_URL=https://dattts.com POLAR_SERVER=production POLAR_ACCESS_TOKEN=polar_oat_... \
        POLAR_ORGANIZATION_ID=... POLAR_WEBHOOK_SECRET=whsec_... \
        POLAR_PRODUCT_PERSONAL=... POLAR_PRODUCT_UNLIMITED=... POLAR_BENEFIT_PERSONAL=... POLAR_BENEFIT_UNLIMITED=...
    # Webhook Polar: https://<tên miền>/api/billing/webhook (định dạng Raw; sự kiện subscription.*, order.paid,
    # benefit_grant.*, customer.state_changed). Chỉ hàm ``web`` dùng các biến POLAR_*; worker không gọi Polar.
    modal run modal_app.py::download_models                          # tải mô hình vào Volume (một lần)
    modal deploy modal_app.py                                        # web + worker

Tuỳ chọn khi deploy (đọc ở máy chạy lệnh `modal deploy`):
    DATTTS_GPU=T4          worker dùng GPU (PyTorch, cài thêm vieneu[cuda])
    DATTTS_DOMAINS=dattts.com,www.dattts.com   gắn tên miền riêng cho web (cần gói Modal hỗ trợ custom domain)

Thành phần:
    - Volume ``dattts-models`` (/models): trọng số VieNeu, tải ở lần đầu rồi commit.
    - Volume ``dattts-files``  (/files):  file âm thanh/phụ đề đã dựng.
    - Dict   ``dattts-kv``: quota, job, waitlist.
    - ``TTSWorker`` (4 CPU / GPU tuỳ chọn): /api/tts + câu mẫu; nạp mô hình một lần ở ``@modal.enter``.
    - ``TTSJobWorker``: job nền (/api/jobs), mỗi container một job, không làm chậm /api/tts.
    - ``web``: FastAPI (``create_app``) + trang tĩnh web/.
"""

from __future__ import annotations

import os
import sys
from dataclasses import asdict
from pathlib import Path

import modal

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
REPO = HERE.parent
WEB_LOCAL = REPO / "web"
REQUIREMENTS = HERE / "requirements.txt"

MODEL_DIR = "/models"
FILES_DIR = "/files"
WEB_REMOTE = "/root/web"

# Đọc lúc deploy. Trong container, giá trị được truyền lại qua env của image.
GPU = (os.environ.get("DATTTS_GPU") or "").strip() or None
DOMAINS = [d.strip() for d in (os.environ.get("DATTTS_DOMAINS") or "").split(",") if d.strip()]

app = modal.App("dattts")

models_volume = modal.Volume.from_name("dattts-models", create_if_missing=True)
files_volume = modal.Volume.from_name("dattts-files", create_if_missing=True)
# ADMIN_TOKEN, API_KEYS, PUBLIC_BASE_URL, POLAR_* — phải tạo trước khi deploy. Secret được gắn vào ``web``
# (đọc qua ``Settings.from_env()``); không đặt PUBLIC_BASE_URL/POLAR_* trong ``_ENV`` để secret luôn quyết định.
secret = modal.Secret.from_name("dattts")

_ENV = {
    "DATTTS_MODEL_DIR": MODEL_DIR,
    "DATTTS_DATA_DIR": FILES_DIR,
    "WEB_DIR": WEB_REMOTE,
    "TRUST_PROXY": "1",
    "HF_HOME": f"{MODEL_DIR}/hf",
    "DATTTS_GPU": GPU or "",
    "PYTHONUNBUFFERED": "1",
}

base_image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ffmpeg")
    .pip_install_from_requirements(str(REQUIREMENTS))
)
_worker_base = base_image.pip_install("vieneu[cuda]==3.8.3") if GPU else base_image
worker_image = _worker_base.env(_ENV).add_local_python_source("dattts")

web_image = base_image.env(_ENV).add_local_python_source("dattts")
if WEB_LOCAL.is_dir():
    web_image = web_image.add_local_dir(str(WEB_LOCAL), WEB_REMOTE)


def _model_files_snapshot() -> set[str]:
    root = Path(MODEL_DIR)
    if not root.is_dir():
        return set()
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}


def _device() -> str:
    return "cuda" if os.environ.get("DATTTS_GPU") else "cpu"


def _commit_models() -> None:
    """Commit Volume mô hình; lỗi chỉ ghi log (Modal cũng tự commit khi container thoát)."""
    try:
        models_volume.commit()
    except Exception as e:  # pragma: no cover - chỉ chạy trên Modal
        print(f"Cảnh báo: chưa commit được Volume dattts-models: {e}")


def _fetch_models() -> bool:
    """Tải file mô hình vào Volume TRƯỚC khi nạp engine. Trả True nếu có file mới.

    Phải tải + commit trước khi ONNX Runtime mmap các file .data: commit kèm reload Volume,
    mà reload thất bại khi còn file đang mở.
    """
    before = _model_files_snapshot()
    if _device() == "cuda":
        # PyTorch tải trọng số qua cache HF (HF_HOME nằm trong /models)
        from huggingface_hub import snapshot_download

        snapshot_download("pnnbao-ump/VieNeu-TTS-v3-Turbo")
    else:
        from dattts.engine import prefetch_onnx_models

        prefetch_onnx_models(MODEL_DIR)
    return _model_files_snapshot() != before


def _load_engine():
    """Tải mô hình (nếu thiếu) → commit Volume → rồi mới nạp VieNeu."""
    from dattts.engine import VieNeuEngine

    try:
        if _fetch_models():
            _commit_models()
            print(f"Đã tải mô hình vào Volume dattts-models ({len(_model_files_snapshot())} file)")
    except Exception as e:  # tải trước thất bại → để engine tự tải khi nạp (không commit sau đó)
        print(f"Cảnh báo: tải trước mô hình lỗi ({e}); engine sẽ tự tải khi nạp")
    engine = VieNeuEngine(MODEL_DIR, device=_device())
    print(f"VieNeu sẵn sàng sau {engine.load_seconds} s")
    return engine


@app.cls(
    image=worker_image,
    cpu=4,
    memory=6144,
    gpu=GPU,
    scaledown_window=300,
    timeout=2 * 60 * 60,  # job 100.000 ký tự trên CPU có thể mất hàng chục phút
    volumes={MODEL_DIR: models_volume, FILES_DIR: files_volume},
    secrets=[secret],
)
@modal.concurrent(max_inputs=4, target_inputs=2)  # engine có khoá: các câu được xếp xen kẽ
class TTSWorker:
    @modal.enter()
    def load(self):
        from dattts.store import ModalStore

        self.engine = _load_engine()
        self.store = ModalStore(mount=FILES_DIR)

    @modal.method()
    def voices(self) -> list[dict]:
        return self.engine.voices()

    @modal.method()
    def render(self, req: dict) -> dict:
        """Dựng + lưu file vào Volume. Lỗi người dùng trả về dạng dict (ngoại lệ tuỳ biến khó pickle)."""
        from dattts.render import RenderError
        from dattts.store import render_and_store, request_from_dict

        try:
            return render_and_store(self.engine, self.store, request_from_dict(req))
        except RenderError as e:
            return {"error": {"code": e.code, "message": e.message, "status": e.status}}

    @modal.method()
    def run_job(self, job_id: str, req: dict) -> str:
        from dattts.store import render_and_store, request_from_dict, run_job

        rec = run_job(self.store, job_id, request_from_dict(req),
                      lambda r, p: render_and_store(self.engine, self.store, r, p))
        return rec["status"]

    @modal.method()
    def sample(self, voice_id: str) -> bytes:
        from dattts.render import RenderRequest, render
        from dattts.store import sample_text

        name = self.engine.voice_name(voice_id)
        return render(RenderRequest(text=sample_text(name), voice=voice_id, format="mp3"), self.engine)["audio"]


@app.cls(
    image=worker_image,
    cpu=4,
    memory=6144,
    gpu=GPU,
    scaledown_window=120,
    timeout=2 * 60 * 60,  # job 100.000 ký tự trên CPU có thể mất cả giờ
    volumes={MODEL_DIR: models_volume, FILES_DIR: files_volume},
    secrets=[secret],
)
class TTSJobWorker:
    """Job nền chạy ở container riêng, mỗi container một job: job dài không làm chậm /api/tts
    (TTSWorker) và không tranh khoá engine với nhau."""

    @modal.enter()
    def load(self):
        from dattts.store import ModalStore

        self.engine = _load_engine()
        self.store = ModalStore(mount=FILES_DIR)

    @modal.method()
    def run_job(self, job_id: str, req: dict) -> str:
        from dattts.store import render_and_store, request_from_dict, run_job

        rec = run_job(self.store, job_id, request_from_dict(req),
                      lambda r, p: render_and_store(self.engine, self.store, r, p))
        return rec["status"]


class ModalRenderer:
    """Renderer cho container web: danh sách giọng đọc tại chỗ (không cần mô hình),
    còn việc dựng âm thanh gọi sang ``TTSWorker`` qua ``.remote()`` / ``.spawn()``."""

    def __init__(self, store, worker=None, job_worker=None):
        from dattts.engine import VieNeuEngine

        self.store = store
        self._catalog = VieNeuEngine(MODEL_DIR, lazy=True)  # chỉ đọc voices_v3_turbo.json
        self._worker = worker
        self._job_worker = job_worker if job_worker is not None else worker

    @property
    def worker(self):
        if self._worker is None:
            self._worker = TTSWorker()
        return self._worker

    @property
    def job_worker(self):
        if self._job_worker is None:
            self._job_worker = TTSJobWorker()
        return self._job_worker

    def voices(self) -> list[dict]:
        return self._catalog.voices()

    def voice_name(self, voice_id: str) -> str:
        return self._catalog.voice_name(voice_id)

    def engine_name(self) -> str:
        return self._catalog.name

    def render(self, req) -> dict:
        from dattts.render import RenderError

        out = self.worker.render.remote(asdict(req))
        if "error" in out:
            e = out["error"]
            raise RenderError(e["code"], e["message"], int(e.get("status", 400)))
        return out

    def submit(self, job_id: str, req) -> None:
        self.job_worker.run_job.spawn(job_id, asdict(req))

    def sample(self, voice_id: str) -> bytes:
        return self.worker.sample.remote(voice_id)


@app.function(
    image=worker_image,
    cpu=2,
    memory=4096,
    gpu=GPU,  # DATTTS_GPU=T4 → engine chạy device="cuda", cần GPU thật để đọc thử
    timeout=30 * 60,
    volumes={MODEL_DIR: models_volume},
)
def download_models() -> dict:
    """Tải mô hình vào Volume và đọc thử một câu: ``modal run modal_app.py::download_models``."""
    import time

    t0 = time.time()
    engine = _load_engine()  # tải → commit → rồi mới nạp (không commit khi file .data đang được mmap)
    wav = engine.synth("Xin chào, đây là DatTTS.", "truc-ly")
    info = {"seconds": round(time.time() - t0, 1), "files": len(_model_files_snapshot()),
            "sample_seconds": round(len(wav) / engine.sample_rate, 2)}
    print(info)
    return info


@app.function(
    image=web_image,
    cpu=1,
    memory=2048,
    scaledown_window=600,
    timeout=10 * 60,  # /api/tts chờ worker (có thể khởi động lạnh); mặc định 300 s là sát

    volumes={FILES_DIR: files_volume},
    secrets=[secret],
)
@modal.concurrent(max_inputs=50)
@modal.asgi_app(custom_domains=DOMAINS or None)
def web():
    from dattts.api import create_app
    from dattts.settings import Settings
    from dattts.store import ModalStore

    settings = Settings.from_env()
    store = ModalStore(dict_name="dattts-kv", volume_name="dattts-files", mount=FILES_DIR)
    return create_app(ModalRenderer(store), store, settings)


@app.local_entrypoint()
def main():
    """``modal run modal_app.py`` — tải mô hình và in thông tin."""
    print(download_models.remote())
