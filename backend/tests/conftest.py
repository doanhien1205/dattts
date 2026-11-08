import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

DEFAULT_MODEL_DIR = "/tmp/claude-0/-home-user/36470a4f-7cb5-5035-9ace-05cdda07e46c/scratchpad/vieneu-models"


def model_dir() -> str:
    return os.environ.get("DATTTS_MODEL_DIR") or DEFAULT_MODEL_DIR


@pytest.fixture
def fake_engine():
    from dattts.engine import FakeEngine

    return FakeEngine()


@pytest.fixture(scope="session")
def real_engine():
    """VieNeuEngine thật, nạp một lần cho cả phiên test. Bỏ qua nếu thiếu mô hình/thư viện."""
    md = model_dir()
    if not Path(md).is_dir():
        pytest.skip(f"không có thư mục mô hình {md}")
    try:
        import vieneu  # noqa: F401
    except Exception as e:  # pragma: no cover
        pytest.skip(f"chưa cài vieneu: {e}")
    from dattts.engine import VieNeuEngine

    return VieNeuEngine(md)
