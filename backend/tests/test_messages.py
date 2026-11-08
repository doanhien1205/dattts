"""Thông điệp lỗi song ngữ (SPEC Phần 3): bảng messages.py, chọn ngôn ngữ theo Accept-Language."""

import re
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dattts import messages as M
from dattts.api import create_app
from dattts.engine import FakeEngine
from dattts.settings import Settings
from dattts.store import LocalRenderer, LocalStore

DATTTS = Path(__file__).resolve().parents[1] / "dattts"


# ───────────────────────────── bảng thông điệp ─────────────────────────────


def test_every_message_has_both_languages_with_same_placeholders():
    assert M.LANGS == ("en", "vi") and M.DEFAULT_LANG == "en"
    for key, entry in M.MESSAGES.items():
        assert set(entry) == {"en", "vi"}, key
        en, vi = entry["en"].strip(), entry["vi"].strip()
        assert en and vi and en != vi, key
        assert M.placeholders(en) == M.placeholders(vi), key
        # bản tiếng Anh không lẫn chữ tiếng Việt có dấu
        assert not re.search(r"[ăâđêôơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ]", en, re.I), key
    for table in (M.PLAN_NAMES, M.PERIODS):
        for k, entry in table.items():
            assert set(entry) == {"en", "vi"} and all(entry.values()), k


def _codes_used_in_source() -> set[str]:
    """Mọi mã lỗi xuất hiện trong mã nguồn backend (raise …Error("code", …) / _err_l(…, "code", …))."""
    pat = re.compile(r'(?:ApiError|BillingError|RenderError|LocalizedError)\(\s*"([a-z_]+)"'
                     r'|_err_l\(\s*request,\s*"([a-z_]+)"'
                     r'|"code":\s*"([a-z_]+)"')
    codes = set()
    for f in DATTTS.glob("*.py"):
        for m in pat.finditer(f.read_text(encoding="utf-8")):
            codes.add(next(g for g in m.groups() if g))
    return codes


def test_every_error_code_in_source_has_a_message():
    used = _codes_used_in_source()
    assert {"empty_text", "quota_exceeded", "bad_plan", "invalid_api_key", "too_long", "not_found"} <= used
    missing = used - set(M.MESSAGES)
    assert not missing, f"thiếu thông điệp cho: {sorted(missing)}"


def test_every_message_key_used_in_source_exists():
    keys = set()
    for f in DATTTS.glob("*.py"):
        keys |= set(re.findall(r'key\s*=\s*f?"([a-z_]+\.[a-z_{}]+)"', f.read_text(encoding="utf-8")))
    keys |= {"too_long.sync", "too_long.job", "too_long.sync_dict", "too_long.job_dict"}  # khoá dựng động
    keys = {k for k in keys if "{" not in k}
    assert keys and not (keys - set(M.MESSAGES)), sorted(keys - set(M.MESSAGES))
    # mỗi khoá "<code>.<biến thể>" thuộc về một mã lỗi có thông điệp gốc
    for k in M.MESSAGES:
        assert k.split(".", 1)[0] in M.MESSAGES, k


def test_validation_field_errors_map_to_existing_messages():
    from dattts.api import _FIELD_ERRORS
    for code, key in _FIELD_ERRORS.values():
        assert code in M.MESSAGES and key in M.MESSAGES and key.split(".")[0] == code


# ───────────────────────────── Accept-Language ─────────────────────────────


@pytest.mark.parametrize("header,lang", [
    (None, "en"), ("", "en"), ("vi", "vi"), ("vi-VN", "vi"), ("VI-vn,vi;q=0.9", "vi"),
    ("en-US,en;q=0.9", "en"), ("en-US,en;q=0.9,vi;q=0.8", "en"), ("vi-VN,vi;q=0.9,en-US;q=0.8", "vi"),
    ("en;q=0.5,vi;q=0.9", "vi"), ("fr-FR", "en"), ("fr-FR,vi;q=0.5", "vi"), ("*", "en"),
    ("vi;q=0", "en"), ("de, en;q=0.1", "en"), ("vie", "en"), ("garbage;;;q=x", "en"),
])
def test_pick_lang(header, lang):
    assert M.pick_lang(header) == lang


def test_number_and_reset_formatting():
    assert M.fmt_number(1_000_000, "en") == "1,000,000" and M.fmt_number(1_000_000, "vi") == "1.000.000"
    d = datetime.fromisoformat("2026-11-16T00:00:00+07:00")
    assert M.fmt_reset(d, "en") == "on Mon, Nov 16, 2026 at 00:00 (UTC+7)"
    assert M.fmt_reset(d, "vi") == "00:00 ngày 16/11/2026 (giờ Việt Nam)"
    # mốc UTC được đổi sang giờ VN
    assert M.fmt_reset(datetime.fromisoformat("2026-11-30T17:00:00+00:00"), "vi").startswith("00:00 ngày 01/12/2026")
    assert M.text("quota_exceeded.free_used_up", "en", limit=20000).startswith("You've used all 20,000 free")
    assert M.text("quota_exceeded.free_used_up", "vi", limit=20000).startswith("Bạn đã dùng hết 20.000 ký tự")
    assert M.text("bad_dictionary.unknown_variant", "vi") == M.MESSAGES["bad_dictionary"]["vi"]
    assert M.text("bad_speed", "xx") == M.MESSAGES["bad_speed"]["en"]
    assert M.plan_name("pro_year", "vi") == "Pro năm" and M.plan_name("pro_year", "en") == "Pro Yearly"


def test_localized_error_roundtrip():
    e = M.LocalizedError("too_long", status=413, key="too_long.sync", params={"chars": 4000, "max": 3000})
    assert e.message == M.text("too_long.sync", "en", chars=4000, max=3000) and "4,000" in e.message
    assert "4.000" in e.localized("vi")
    d = e.to_dict()
    assert d["key"] == "too_long.sync" and d["params"] == {"chars": 4000, "max": 3000}
    assert M.error_body(d, "vi") == {"code": "too_long", "message": e.localized("vi")}
    # bản ghi cũ (chưa có key): mã có trong bảng → thông điệp chung; mã lạ → giữ nguyên
    assert M.error_body({"code": "engine_error", "message": "cũ"}, "en")["message"] == M.text("engine_error")
    assert M.error_body({"code": "weird", "message": "giữ"}, "en") == {"code": "weird", "message": "giữ"}
    # message tường minh không kèm key → giữ nguyên văn bản
    assert M.LocalizedError("x", "literal", 400).localized("vi") == "literal"


# ───────────────────────────── API ─────────────────────────────


@pytest.fixture
def client(tmp_path):
    store = LocalStore(str(tmp_path))
    renderer = LocalRenderer(FakeEngine(), store)
    c = TestClient(create_app(renderer, store, Settings.from_env({}, web_dir=None, free_daily_chars=30)))
    yield c
    renderer.close()


VI = {"Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8"}
EN = {"Accept-Language": "en-US,en;q=0.9"}


def _msg(r):
    return r.json()["error"]["message"]


def test_api_errors_follow_accept_language(client):
    cases = [
        ("post", "/api/tts", {"json": {"text": "   "}}, 400, "empty_text"),
        ("post", "/api/tts", {"json": {"text": "Xin chào", "speed": 3}}, 400, "bad_speed"),
        ("post", "/api/tts", {"json": {"text": "Xin chào", "format": "flac"}}, 400, "bad_format"),
        ("post", "/api/tts", {"json": {"text": "[Ai Đó] Xin chào"}}, 400, "unknown_voice"),
        ("post", "/api/tts", {"json": {"text": "Xin chào", "dictionary": [{"from": "a", "to": "b"}] * 201}},
         400, "bad_dictionary"),
        ("get", "/api/jobs/khong-co-job-nay", {}, 404, "job_not_found"),
        ("get", "/api/files/0123456789abcdef0123.mp3", {}, 404, "file_not_found"),
        ("get", "/api/khong-ton-tai", {}, 404, "not_found"),
        ("get", "/api/me", {}, 401, "api_key_required"),
        ("post", "/api/billing/checkout", {"json": {"plan": "pro_month"}}, 503, "billing_disabled"),
        ("post", "/api/waitlist", {"json": {"email": "khong-phai-email"}}, 400, "bad_email"),
    ]
    for method, path, kw, status, code in cases:
        en = getattr(client, method)(path, headers=EN, **kw)
        vi = getattr(client, method)(path, headers=VI, **kw)
        default = getattr(client, method)(path, **kw)
        assert en.status_code == vi.status_code == status, (path, en.text)
        assert en.json()["error"]["code"] == vi.json()["error"]["code"] == code
        assert _msg(en) != _msg(vi), path
        assert _msg(default) == _msg(en), path  # mặc định tiếng Anh
        assert set(en.json()["error"]) == {"code", "message"}


def test_validation_errors_bilingual(client):
    r_en = client.post("/api/tts", content=b"{khong-json", headers={**EN, "Content-Type": "application/json"})
    r_vi = client.post("/api/tts", content=b"{khong-json", headers={**VI, "Content-Type": "application/json"})
    assert r_en.json()["error"] == {"code": "bad_json", "message": M.MESSAGES["bad_json"]["en"]}
    assert r_vi.json()["error"] == {"code": "bad_json", "message": M.MESSAGES["bad_json"]["vi"]}
    r = client.post("/api/tts", json={"text": 123}, headers=VI)
    assert r.json()["error"] == {"code": "bad_text", "message": M.MESSAGES["bad_text"]["vi"]}
    r = client.post("/api/tts", json={"text": "x", "speed": "nhanh"})
    assert r.json()["error"] == {"code": "bad_speed", "message": M.MESSAGES["bad_speed"]["en"]}
    r = client.post("/api/waitlist", json={"email": "a@b.co", "note": "x" * 2000}, headers=VI)
    assert r.json()["error"] == {"code": "bad_request", "message": M.MESSAGES["bad_request.note"]["vi"]}


def test_quota_messages_bilingual(client):
    assert client.post("/api/tts", json={"text": "Một hai ba bốn năm sáu bảy."}).status_code == 200  # 27/30
    en = client.post("/api/tts", json={"text": "Tám chín mười."}, headers=EN)
    vi = client.post("/api/tts", json={"text": "Tám chín mười."}, headers=VI)
    assert en.status_code == vi.status_code == 429
    assert _msg(en).startswith("You have 3 free characters left today")
    assert _msg(vi).startswith("Hôm nay bạn chỉ còn 3 ký tự miễn phí")


def test_too_long_messages_bilingual(client):
    text = "Một câu. " * 400  # > 3000 ký tự
    en, vi = (client.post("/api/tts", json={"text": text}, headers=h) for h in (EN, VI))
    assert en.status_code == vi.status_code == 413
    assert "/api/jobs" in _msg(en) and "3,000" in _msg(en) and "Use background mode" in _msg(en)
    assert "/api/jobs" in _msg(vi) and "3.000" in _msg(vi) and "chế độ tạo nền" in _msg(vi)


def test_body_limit_413_bilingual(tmp_path):
    store = LocalStore(str(tmp_path))
    renderer = LocalRenderer(FakeEngine(), store)
    c = TestClient(create_app(renderer, store, Settings.from_env({}, web_dir=None, job_max_chars=10)))
    big = b'{"text":"' + b"a" * 700_000 + b'"}'
    for h, lang in ((EN, "en"), (VI, "vi"), ({}, "en")):
        r = c.post("/api/tts", content=big, headers={**h, "Content-Type": "application/json"})
        assert r.status_code == 413 and r.json()["error"] == {"code": "too_long",
                                                              "message": M.MESSAGES["too_long"][lang]}
    renderer.close()


def test_job_error_localized_when_read(client):
    store = client.app.state.store
    from dattts.render import RenderError
    from dattts.store import job_key
    err = RenderError("empty_audio", status=422, key="empty_audio.no_output")
    store.kv_set(job_key("job-loi-12345"), {"job_id": "job-loi-12345", "status": "error", "error": err.to_dict(),
                                            "updated_at": 0})
    en = client.get("/api/jobs/job-loi-12345", headers=EN).json()
    vi = client.get("/api/jobs/job-loi-12345", headers=VI).json()
    assert en["error"] == {"code": "empty_audio", "message": M.MESSAGES["empty_audio.no_output"]["en"]}
    assert vi["error"] == {"code": "empty_audio", "message": M.MESSAGES["empty_audio.no_output"]["vi"]}


def test_stale_job_error_localized(client):
    from dattts.store import job_key
    client.app.state.store.kv_set(job_key("job-treo-12345"), {"job_id": "job-treo-12345", "status": "running",
                                                              "updated_at": 0, "created_at": 0})
    vi = client.get("/api/jobs/job-treo-12345", headers=VI).json()
    assert vi["error"] == {"code": "job_stale", "message": M.MESSAGES["job_stale"]["vi"]}
    en = client.get("/api/jobs/job-treo-12345").json()
    assert en["error"] == {"code": "job_stale", "message": M.MESSAGES["job_stale"]["en"]}
