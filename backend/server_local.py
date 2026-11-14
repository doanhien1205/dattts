#!/usr/bin/env python
"""Chạy DatTTS ở máy local.

    python server_local.py            # engine VieNeu thật (cần DATTTS_MODEL_DIR hoặc --model-dir)
    python server_local.py --fake     # engine giả, khởi động tức thì (phát triển giao diện/test)

Mở http://127.0.0.1:8000 — trang web/ được phục vụ tại "/", API tại "/api".
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


def build_app(fake: bool, data_dir: str, model_dir: str | None = None, threads: int = 0, warm: bool = True):
    from dattts.api import create_app
    from dattts.engine import FakeEngine, VieNeuEngine
    from dattts.settings import Settings
    from dattts.store import LocalRenderer, LocalStore

    settings = Settings.from_env()
    if not os.environ.get("WEB_DIR") and (REPO / "web").is_dir():
        settings.web_dir = str(REPO / "web")
    # Engine giả dùng thư mục dữ liệu riêng để mẫu giọng giả không lẫn với giọng thật.
    root = Path(data_dir) / ("fake" if fake else "vieneu")
    store = LocalStore(str(root))
    if fake:
        engine = FakeEngine()
    else:
        md = model_dir or os.environ.get("DATTTS_MODEL_DIR") or settings.model_dir
        device = "cuda" if settings.gpu else "cpu"
        engine = VieNeuEngine(md, device=device, lazy=True, threads=threads)
    purged = store.blob_purge(settings.file_retention_days)  # local: dọn một lần lúc khởi động
    if purged:
        logging.getLogger("dattts").info("Đã xoá %d file cũ hơn %d ngày", purged, settings.file_retention_days)
    renderer = LocalRenderer(engine, store)
    if warm and not fake:
        t0 = time.time()
        renderer.warmup()
        logging.getLogger("dattts").info("Đã nạp mô hình trong %.1f s", time.time() - t0)
    return create_app(renderer, store, settings)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="Máy chủ DatTTS chạy local")
    p.add_argument("--fake", action="store_true", help="dùng engine giả (không cần mô hình)")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--data-dir", default="./.data", help="nơi lưu file âm thanh và kv.json")
    p.add_argument("--model-dir", default=None, help="thư mục mô hình VieNeu (mặc định $DATTTS_MODEL_DIR)")
    p.add_argument("--threads", type=int, default=0, help="số luồng ONNX (0 = tự chọn)")
    p.add_argument("--no-warmup", action="store_true", help="không nạp mô hình trước khi nhận request")
    a = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = build_app(a.fake, a.data_dir, a.model_dir, a.threads, warm=not a.no_warmup)

    import uvicorn

    print(f"DatTTS đang chạy tại http://{a.host}:{a.port}  (engine: {'giả' if a.fake else 'VieNeu'})", flush=True)
    uvicorn.run(app, host=a.host, port=a.port, log_level="info")


if __name__ == "__main__":
    main()
