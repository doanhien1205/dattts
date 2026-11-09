"""Proxy Cloudflare Pages phía trước Modal (SPEC Phần 4): tin X-DatTTS-Client-IP chỉ khi secret đúng;
REQUIRE_PROXY chặn truy cập thẳng URL modal.run (trừ /api/health)."""

import pytest
from fastapi.testclient import TestClient

from dattts.api import create_app
from dattts.engine import FakeEngine
from dattts.messages import MESSAGES
from dattts.settings import Settings
from dattts.store import LocalRenderer, LocalStore

SECRET = "s3cr3t-" + "x" * 40
TEXT = "Mười ký tự"  # 10 ký tự tính phí
# Văn bản khác nhau cùng 10 ký tự (văn bản trùng lấy từ cache, không trừ quota)
TEXT2, TEXT3 = "Mười ký tữ", "Mười kí tự"


@pytest.fixture
def make_client(tmp_path):
    made = []

    def _make(env=None, **overrides):
        overrides.setdefault("web_dir", None)
        store = LocalStore(str(tmp_path / f"data{len(made)}"))
        renderer = LocalRenderer(FakeEngine(), store)
        settings = Settings.from_env(env or {}, **overrides)
        c = TestClient(create_app(renderer, store, settings))
        made.append((c, renderer))
        return c

    yield _make
    for _, r in made:
        r.close()


def proxied(ip, secret=SECRET, **extra):
    return {"X-DatTTS-Proxy-Secret": secret, "X-DatTTS-Client-IP": ip, **extra}


def used(c, headers=None):
    r = c.get("/api/quota", headers=headers or {})
    assert r.status_code == 200, r.text
    return r.json()["used"]


def test_settings_from_env():
    s = Settings.from_env({"PROXY_SHARED_SECRET": "  abc  ", "REQUIRE_PROXY": "1"})
    assert s.proxy_shared_secret == "abc" and s.require_proxy and s.proxy_guard_enabled
    s = Settings.from_env({"REQUIRE_PROXY": "1"})
    assert s.proxy_shared_secret is None and s.require_proxy and not s.proxy_guard_enabled
    s = Settings.from_env({})
    assert s.proxy_shared_secret is None and not s.require_proxy


def test_message_bilingual():
    m = MESSAGES["direct_access_forbidden"]
    assert m["en"] and m["vi"] and m["en"] != m["vi"]


def test_quota_keyed_by_forwarded_ip(make_client):
    c = make_client({"PROXY_SHARED_SECRET": SECRET}, free_daily_chars=10)
    assert c.post("/api/tts", json={"text": TEXT}, headers=proxied("203.0.113.5")).status_code == 200
    assert used(c, proxied("203.0.113.5")) == 10
    assert used(c, proxied("198.51.100.9")) == 0          # IP khách khác → quota riêng
    assert used(c) == 0                                     # IP kết nối (testclient) không bị trừ
    r = c.post("/api/tts", json={"text": TEXT2}, headers=proxied("203.0.113.5"))
    assert r.status_code == 429 and r.json()["error"]["code"] == "quota_exceeded"


def test_ipv6_normalized(make_client):
    c = make_client({"PROXY_SHARED_SECRET": SECRET}, free_daily_chars=10)
    c.post("/api/tts", json={"text": TEXT}, headers=proxied("2001:DB8:0:0::1"))
    assert used(c, proxied("2001:db8::1")) == 10


def test_spoofed_header_without_secret_ignored(make_client):
    c = make_client({"PROXY_SHARED_SECRET": SECRET}, free_daily_chars=10)
    c.post("/api/tts", json={"text": TEXT}, headers={"X-DatTTS-Client-IP": "203.0.113.5"})
    assert used(c) == 10                                   # tính theo IP kết nối thật
    assert used(c, proxied("203.0.113.5")) == 0


def test_spoofed_header_when_no_secret_configured(make_client):
    c = make_client({}, free_daily_chars=10)               # backend chưa đặt secret → không tin header
    c.post("/api/tts", json={"text": TEXT}, headers=proxied("203.0.113.5", secret=""))
    assert c.post("/api/tts", json={"text": TEXT2},
                  headers=proxied("203.0.113.6", secret="anything")).status_code == 429
    assert used(c) == 10
    assert c.post("/api/tts", json={"text": TEXT3}, headers=proxied("203.0.113.7")).status_code == 429


@pytest.mark.parametrize("bad", ["wrong", SECRET[:-1], SECRET + "x", "", SECRET.upper()])
def test_wrong_secret_ignored(make_client, bad):
    c = make_client({"PROXY_SHARED_SECRET": SECRET}, free_daily_chars=10)
    c.post("/api/tts", json={"text": TEXT}, headers=proxied("203.0.113.5", secret=bad))
    assert used(c) == 10
    assert used(c, proxied("203.0.113.5")) == 0


@pytest.mark.parametrize("bad_ip", ["", "not-an-ip", "999.1.1.1", "1.2.3.4, 5.6.7.8", "1.2.3"])
def test_invalid_ip_ignored(make_client, bad_ip):
    c = make_client({"PROXY_SHARED_SECRET": SECRET}, free_daily_chars=10)
    c.post("/api/tts", json={"text": TEXT}, headers=proxied(bad_ip))
    assert used(c) == 10                                   # quay về IP kết nối
    assert used(c, proxied("203.0.113.5")) == 0


def test_invalid_ip_falls_back_to_xff(make_client):
    c = make_client({"PROXY_SHARED_SECRET": SECRET, "TRUST_PROXY": "1"}, free_daily_chars=10)
    c.post("/api/tts", json={"text": TEXT}, headers=proxied("garbage", **{"X-Forwarded-For": "192.0.2.44"}))
    assert used(c, {"X-Forwarded-For": "192.0.2.44"}) == 10


def test_forwarded_ip_beats_xff(make_client):
    c = make_client({"PROXY_SHARED_SECRET": SECRET, "TRUST_PROXY": "1"}, free_daily_chars=10)
    c.post("/api/tts", json={"text": TEXT}, headers=proxied("203.0.113.5", **{"X-Forwarded-For": "192.0.2.44"}))
    assert used(c, {"X-Forwarded-For": "192.0.2.44"}) == 0
    assert used(c, proxied("203.0.113.5")) == 10


@pytest.mark.parametrize("method,path,body", [
    ("get", "/api/voices", None),
    ("get", "/api/quota", None),
    ("post", "/api/tts", {"text": TEXT}),
    ("get", "/api/billing/plans", None),
    ("post", "/api/billing/webhook", {"type": "x"}),     # webhook Polar cũng phải đi qua proxy
    ("get", "/api/files/0123456789abcdef0123.mp3", None),
    ("get", "/api/does-not-exist", None),
    ("get", "/api", None),
    ("options", "/api/tts", None),
])
def test_require_proxy_blocks_direct(make_client, method, path, body):
    c = make_client({"PROXY_SHARED_SECRET": SECRET, "REQUIRE_PROXY": "1"})
    kw = {"json": body} if body is not None else {}
    for headers in ({}, {"X-DatTTS-Proxy-Secret": "wrong"}, {"X-DatTTS-Client-IP": "1.2.3.4"}):
        r = getattr(c, method)(path, headers=headers, **kw)
        assert r.status_code == 403, (path, headers, r.status_code)
        assert r.json() == {"error": {"code": "direct_access_forbidden",
                                      "message": MESSAGES["direct_access_forbidden"]["en"]}}
        assert r.headers.get("x-content-type-options") == "nosniff"
    r = getattr(c, method)(path, headers={"Accept-Language": "vi-VN,vi;q=0.9"}, **kw)
    assert r.json()["error"]["message"] == MESSAGES["direct_access_forbidden"]["vi"]


def test_require_proxy_allows_proxied(make_client):
    c = make_client({"PROXY_SHARED_SECRET": SECRET, "REQUIRE_PROXY": "1"})
    h = proxied("203.0.113.5")
    assert c.get("/api/voices", headers=h).status_code == 200
    assert c.get("/api/quota", headers=h).status_code == 200
    assert c.post("/api/tts", json={"text": TEXT}, headers=h).status_code == 200
    # chỉ có secret, không có IP → vẫn qua (IP lấy theo logic cũ)
    assert c.get("/api/voices", headers={"X-DatTTS-Proxy-Secret": SECRET}).status_code == 200


def test_require_proxy_health_exempt(make_client):
    c = make_client({"PROXY_SHARED_SECRET": SECRET, "REQUIRE_PROXY": "1"})
    r = c.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert c.get("/api/health/").status_code == 403      # chỉ đúng đường dẫn /api/health được miễn


def test_require_proxy_does_not_touch_static(make_client, tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<h1>hi</h1>", encoding="utf-8")
    c = make_client({"PROXY_SHARED_SECRET": SECRET, "REQUIRE_PROXY": "1"}, web_dir=str(web))
    assert c.get("/").status_code == 200
    assert c.get("/api/voices").status_code == 403


def test_require_proxy_without_secret_is_noop(make_client):
    c = make_client({"REQUIRE_PROXY": "1"})
    assert c.get("/api/voices").status_code == 200


def test_cors_preflight_via_proxy(make_client):
    c = make_client({"PROXY_SHARED_SECRET": SECRET, "REQUIRE_PROXY": "1"})
    r = c.options("/api/tts", headers={**proxied("203.0.113.5"), "Origin": "https://dattts.com",
                                       "Access-Control-Request-Method": "POST"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "https://dattts.com"


def test_ipv6_same_64_shares_quota(make_client):
    c = make_client({"PROXY_SHARED_SECRET": SECRET}, free_daily_chars=10)
    assert c.post("/api/tts", json={"text": TEXT}, headers=proxied("2001:db8::1")).status_code == 200
    assert used(c, proxied("2001:db8::2")) == 10                       # cùng /64 → chung quota
    assert used(c, proxied("2001:db8:0:0:ffff:ffff:ffff:ffff")) == 10
    r = c.post("/api/tts", json={"text": TEXT2}, headers=proxied("2001:db8::2"))
    assert r.status_code == 429 and r.json()["error"]["code"] == "quota_exceeded"
    assert used(c, proxied("2001:db8:0:1::1")) == 0                    # /64 khác → quota riêng


def test_ipv4_mapped_counts_as_ipv4(make_client):
    c = make_client({"PROXY_SHARED_SECRET": SECRET}, free_daily_chars=10)
    c.post("/api/tts", json={"text": TEXT}, headers=proxied("::ffff:203.0.113.5"))
    assert used(c, proxied("203.0.113.5")) == 10
    assert used(c, proxied("203.0.113.6")) == 0


def test_waitlist_keeps_full_ipv6(make_client, tmp_path):
    c = make_client({"PROXY_SHARED_SECRET": SECRET})
    r = c.post("/api/waitlist", json={"email": "a@example.com"}, headers=proxied("2001:db8::abcd"))
    assert r.status_code == 200, r.text
    items = LocalStore(str(tmp_path / "data0")).kv_get("waitlist")
    assert items[0]["ip"] == "2001:db8::abcd"                          # waitlist vẫn ghi IP đầy đủ
