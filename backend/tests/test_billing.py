"""Thanh toán qua Polar (SPEC Phần 2) — Polar giả bằng httpx.MockTransport, không gọi mạng."""

import base64
import hashlib
import hmac
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from dattts import billing as B
from dattts.api import create_app
from dattts.engine import FakeEngine
from dattts.settings import Settings
from dattts.store import LocalRenderer, LocalStore

ORG = "11111111-1111-4111-8111-111111111111"
PROD_P = "22222222-2222-4222-8222-222222222222"
PROD_U = "33333333-3333-4333-8333-333333333333"
BEN_P = "44444444-4444-4444-8444-444444444444"
BEN_U = "55555555-5555-4555-8555-555555555555"
CHECKOUT_ID = "66666666-6666-4666-8666-666666666666"
CUST = "77777777-7777-4777-8777-777777777777"

NEW_SECRET = "whsec_" + base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()
OLD_SECRET = "whsec_polar-old-style-secret-not-base64!"

POLAR_ENV = dict(
    polar_access_token="polar_oat_test",
    polar_organization_id=ORG,
    polar_webhook_secret=NEW_SECRET,
    polar_product_personal=PROD_P,
    polar_product_unlimited=PROD_U,
    polar_benefit_personal=BEN_P,
    polar_benefit_unlimited=BEN_U,
    polar_api_base="https://polar.test",
    public_base_url="https://dattts.com",
)


class FakePolar:
    """Polar giả: ghi lại request, trả dữ liệu theo cấu hình."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.keys: dict[str, dict] = {}       # key → ValidatedLicenseKey
        self.fail: int | None = None          # trả mã lỗi này cho mọi request (ví dụ 500)
        self.network_error = False
        self.checkout_status = "open"
        self.prices = {PROD_P: 299, PROD_U: 999}

    def add_key(self, key, benefit=BEN_P, lkid="lk-1", customer=CUST, email="an.nguyen@gmail.com"):
        self.keys[key] = {"id": lkid, "status": "granted", "benefit_id": benefit, "customer_id": customer,
                          "customer": {"id": customer, "email": email}, "key": key, "display_key": "****",
                          "usage": 0, "limit_usage": None, "expires_at": None}

    def calls(self, path):
        return [r for r in self.requests if r.url.path == path]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.network_error:
            raise httpx.ConnectError("boom", request=request)
        if self.fail:
            return httpx.Response(self.fail, json={"detail": "fail"})
        assert request.headers["authorization"] == "Bearer polar_oat_test"
        p, m = request.url.path, request.method
        if m == "POST" and p == "/v1/checkouts/":
            body = json.loads(request.content)
            return httpx.Response(201, json={"id": CHECKOUT_ID, "url": f"https://sandbox.polar.sh/checkout/{CHECKOUT_ID}",
                                             "status": "open", "client_secret": "cs", "metadata": body["metadata"],
                                             "product_id": body["products"][0]})
        if m == "GET" and p.startswith("/v1/checkouts/"):
            cid = p.rsplit("/", 1)[1]
            if cid != CHECKOUT_ID:
                return httpx.Response(404, json={"detail": "not found"})
            return httpx.Response(200, json={"id": cid, "status": self.checkout_status, "product_id": PROD_P,
                                             "metadata": {"plan": "personal"},
                                             "customer_email": "an.nguyen@gmail.com"})
        if m == "GET" and p.startswith("/v1/products/"):
            pid = p.rsplit("/", 1)[1]
            return httpx.Response(200, json={"id": pid, "name": "Gói", "is_archived": False, "prices": [
                {"amount_type": "fixed", "price_amount": self.prices[pid], "price_currency": "usd",
                 "recurring_interval": "month"}]})
        if m == "POST" and p == "/v1/license-keys/validate":
            body = json.loads(request.content)
            assert body["organization_id"] == ORG
            v = self.keys.get(body["key"])
            return httpx.Response(200, json=v) if v else httpx.Response(404, json={"detail": "not found"})
        if m == "POST" and p == "/v1/customer-sessions/":
            body = json.loads(request.content)
            return httpx.Response(201, json={"customer_portal_url": f"https://polar.sh/portal?c={body['customer_id']}",
                                             "token": "t", "expires_at": "2099-01-01T00:00:00Z"})
        return httpx.Response(404, json={"detail": "no route"})


@pytest.fixture
def fake():
    return FakePolar()


@pytest.fixture
def make_bclient(tmp_path, fake):
    made = []

    def _make(**overrides):
        cfg = {**POLAR_ENV, "web_dir": None, **overrides}
        store = LocalStore(str(tmp_path / f"data{len(made)}"))
        renderer = LocalRenderer(FakeEngine(), store)
        settings = Settings.from_env({}, **cfg)
        polar = B.PolarClient.from_settings(settings, transport=httpx.MockTransport(fake)) \
            if settings.billing_enabled else None
        c = TestClient(create_app(renderer, store, settings, polar=polar))
        c.store, c.renderer, c.settings, c.fake = store, renderer, settings, fake
        made.append(c)
        return c

    yield _make
    for c in made:
        c.renderer.close()


def err(resp):
    body = resp.json()
    assert set(body) == {"error"}, body
    assert body["error"]["message"]
    return body["error"]["code"]


def sign(body: bytes, secret: str, msg_id="msg_1", ts=None, old_style=False):
    ts = str(int(time.time()) if ts is None else ts)
    if old_style:
        key = secret.encode()
    else:
        key = base64.b64decode(secret[len("whsec_"):])
    sig = base64.b64encode(hmac.new(key, f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()).decode()
    return {"webhook-id": msg_id, "webhook-timestamp": ts, "webhook-signature": f"v1,{sig}"}


# ───────────────────────────── settings ─────────────────────────────


def test_settings_from_env():
    s = Settings.from_env({"POLAR_ACCESS_TOKEN": "x", "POLAR_SERVER": "production",
                           "PLAN_PERSONAL_MONTHLY_CHARS": "5", "PUBLIC_BASE_URL": "http://127.0.0.1:8765/"})
    assert s.billing_enabled and s.polar_base_url == "https://api.polar.sh"
    assert s.plan_personal_monthly_chars == 5 and s.plan_unlimited_monthly_chars == 20_000_000
    assert s.public_base == "http://127.0.0.1:8765"
    d = Settings.from_env({})
    assert not d.billing_enabled and d.polar_base_url == "https://sandbox-api.polar.sh"
    assert Settings.from_env({"POLAR_API_BASE": "http://fake:9/"}).polar_base_url == "http://fake:9"


# ───────────────────────────── verify_webhook ─────────────────────────────


def test_verify_webhook_new_style_secret():
    body = b'{"type":"order.paid"}'
    assert B.verify_webhook(body, sign(body, NEW_SECRET), NEW_SECRET)


def test_verify_webhook_old_style_secret():
    body = b'{"type":"order.paid"}'
    assert B.verify_webhook(body, sign(body, OLD_SECRET, old_style=True), OLD_SECRET)
    # bí mật kiểu mới nhưng Polar ký theo kiểu cũ (utf-8 cả chuỗi) → vẫn nhận
    assert B.verify_webhook(body, sign(body, NEW_SECRET, old_style=True), NEW_SECRET)


def test_verify_webhook_rejects_wrong_signature_and_tampering():
    body = b'{"type":"order.paid"}'
    h = sign(body, NEW_SECRET)
    assert not B.verify_webhook(body + b" ", h, NEW_SECRET)
    assert not B.verify_webhook(body, h, "whsec_" + base64.b64encode(b"other-key-000000").decode())
    assert not B.verify_webhook(body, {**h, "webhook-id": "msg_2"}, NEW_SECRET)
    assert not B.verify_webhook(body, {**h, "webhook-signature": "v1,!!!notb64"}, NEW_SECRET)
    assert not B.verify_webhook(body, {**h, "webhook-signature": h["webhook-signature"].replace("v1,", "v2,")},
                                NEW_SECRET)
    assert not B.verify_webhook(body, {}, NEW_SECRET)
    assert not B.verify_webhook(body, h, "")


def test_verify_webhook_stale_timestamp():
    body = b"{}"
    old = int(time.time()) - 301
    assert not B.verify_webhook(body, sign(body, NEW_SECRET, ts=old), NEW_SECRET)
    future = int(time.time()) + 301
    assert not B.verify_webhook(body, sign(body, NEW_SECRET, ts=future), NEW_SECRET)
    assert B.verify_webhook(body, sign(body, NEW_SECRET, ts=old), NEW_SECRET, tolerance=600)
    assert not B.verify_webhook(body, {**sign(body, NEW_SECRET), "webhook-timestamp": "abc"}, NEW_SECRET)


def test_verify_webhook_multiple_signatures_in_header():
    body = b'{"a":1}'
    good = sign(body, NEW_SECRET)
    bogus = "v1," + base64.b64encode(b"x" * 32).decode()
    h = {**good, "webhook-signature": f"{bogus} v1a,zzz {good['webhook-signature']}"}
    assert B.verify_webhook(body, h, NEW_SECRET)
    h2 = {**good, "webhook-signature": f"{bogus} {bogus}"}
    assert not B.verify_webhook(body, h2, NEW_SECRET)


def test_verify_webhook_case_insensitive_headers():
    body = b"{}"
    h = {k.upper(): v for k, v in sign(body, NEW_SECRET).items()}
    assert B.verify_webhook(body, h, NEW_SECRET)


# ───────────────────────────── resolve_api_key ─────────────────────────────


@pytest.fixture
def ctx(tmp_path, fake):
    settings = Settings.from_env({}, **POLAR_ENV, api_keys=frozenset({"internal-1"}))
    store = LocalStore(str(tmp_path / "kv"))
    client = B.PolarClient.from_settings(settings, transport=httpx.MockTransport(fake))
    return settings, store, client


def test_resolve_internal_key_without_polar(ctx, fake):
    settings, store, client = ctx
    assert B.resolve_api_key("internal-1", settings, store, client) == {"kind": "internal"}
    assert fake.requests == []


def test_resolve_polar_key_positive_cache_10_min(ctx, fake):
    settings, store, client = ctx
    fake.add_key("DATTTS-AAAA", benefit=BEN_U, lkid="lk-u")
    t0 = 1_800_000_000.0
    info = B.resolve_api_key("DATTTS-AAAA", settings, store, client, now=t0)
    assert info == {"kind": "polar", "license_key_id": "lk-u", "customer_id": CUST,
                    "email": "an.nguyen@gmail.com", "plan": "unlimited"}
    # chỉ lưu hash, không lưu key gốc
    raw = (store.root / "kv.json").read_text(encoding="utf-8")
    assert "DATTTS-AAAA" not in raw
    assert store.kv_get("lk:" + hashlib.sha256(b"DATTTS-AAAA").hexdigest())["ok"] is True
    assert store.kv_get(f"lkc:{CUST}") == [hashlib.sha256(b"DATTTS-AAAA").hexdigest()]
    n = len(fake.requests)
    B.resolve_api_key("DATTTS-AAAA", settings, store, client, now=t0 + 599)
    assert len(fake.requests) == n
    B.resolve_api_key("DATTTS-AAAA", settings, store, client, now=t0 + 601)
    assert len(fake.requests) == n + 1


def test_resolve_negative_cache_60_s(ctx, fake):
    settings, store, client = ctx
    t0 = 1_800_000_000.0
    for t in (t0, t0 + 30):
        with pytest.raises(B.BillingError) as e:
            B.resolve_api_key("DATTTS-NOPE", settings, store, client, now=t)
        assert e.value.code == "invalid_api_key" and e.value.status == 401
    assert len(fake.calls("/v1/license-keys/validate")) == 1
    fake.add_key("DATTTS-NOPE")  # vừa mua xong → sau 60 s được nhận
    assert B.resolve_api_key("DATTTS-NOPE", settings, store, client, now=t0 + 61)["plan"] == "personal"
    assert len(fake.calls("/v1/license-keys/validate")) == 2


def test_resolve_uses_stale_cache_on_polar_5xx(ctx, fake):
    settings, store, client = ctx
    fake.add_key("DATTTS-OK")
    t0 = 1_800_000_000.0
    B.resolve_api_key("DATTTS-OK", settings, store, client, now=t0)
    fake.fail = 502
    info = B.resolve_api_key("DATTTS-OK", settings, store, client, now=t0 + 3 * 3600)  # cache đã hết hạn
    assert info["plan"] == "personal"
    fake.fail, fake.network_error = None, True
    assert B.resolve_api_key("DATTTS-OK", settings, store, client, now=t0 + 23 * 3600)["plan"] == "personal"
    with pytest.raises(B.BillingError) as e:  # quá 24 giờ → không dùng nữa
        B.resolve_api_key("DATTTS-OK", settings, store, client, now=t0 + 25 * 3600)
    assert e.value.code == "billing_unavailable" and e.value.status == 503


def test_resolve_503_when_nothing_cached(ctx, fake):
    settings, store, client = ctx
    fake.fail = 500
    with pytest.raises(B.BillingError) as e:
        B.resolve_api_key("DATTTS-NEW", settings, store, client)
    assert e.value.code == "billing_unavailable" and e.value.status == 503
    fake.fail, fake.network_error = None, True
    with pytest.raises(B.BillingError) as e:
        B.resolve_api_key("DATTTS-NEW", settings, store, client)
    assert e.value.status == 503


def test_benefit_to_plan_mapping_and_unknown_benefit(ctx, fake):
    settings, store, client = ctx
    fake.add_key("DATTTS-P", benefit=BEN_P, lkid="a")
    fake.add_key("DATTTS-U", benefit=BEN_U, lkid="b")
    fake.add_key("DATTTS-X", benefit="99999999-9999-4999-8999-999999999999", lkid="c")
    assert B.resolve_api_key("DATTTS-P", settings, store, client)["plan"] == "personal"
    assert B.resolve_api_key("DATTTS-U", settings, store, client)["plan"] == "unlimited"
    with pytest.raises(B.BillingError) as e:
        B.resolve_api_key("DATTTS-X", settings, store, client)
    assert e.value.code == "invalid_api_key"
    fake.add_key("DATTTS-R", lkid="d")
    fake.keys["DATTTS-R"]["status"] = "revoked"
    with pytest.raises(B.BillingError):
        B.resolve_api_key("DATTTS-R", settings, store, client)


def test_resolve_billing_disabled_rejects_unknown_key(tmp_path):
    settings = Settings.from_env({}, api_keys=frozenset({"k"}))
    store = LocalStore(str(tmp_path / "kv"))
    assert B.resolve_api_key("k", settings, store, None) == {"kind": "internal"}
    with pytest.raises(B.BillingError) as e:
        B.resolve_api_key("DATTTS-ZZZ", settings, store, None)
    assert e.value.status == 401


def test_mask_email_and_month_helpers():
    assert B.mask_email("an.nguyen@gmail.com") == "a***@gmail.com"
    assert B.mask_email(None) is None and B.mask_email("khong-email") is None
    # 31/12 17:30 UTC = 01/01 00:30 giờ VN
    t = 1_798_738_200.0  # 2026-12-31T17:30:00Z
    assert B.month_id(t) == "2027-01"
    assert B.month_reset_at(t).isoformat() == "2027-02-01T00:00:00+07:00"


# ───────────────────────────── API: bảng giá & checkout ─────────────────────────────


def test_plans_enabled_with_prices(make_bclient, fake):
    c = make_bclient()
    j = c.get("/api/billing/plans").json()
    assert j["enabled"] is True
    ids = [p["id"] for p in j["plans"]]
    assert ids == ["free", "personal", "unlimited"]
    free, pers, unl = j["plans"]
    assert free["price"] == 0 and free["interval"] is None and free["chars"] == "20.000/ngày"
    assert pers["price"] == 299 and pers["currency"] == "usd" and pers["interval"] == "month"
    assert pers["monthly_chars"] == 1_000_000 and pers["name"] == "Cá nhân"
    assert unl["price"] == 999 and unl["monthly_chars"] == 20_000_000
    n = len(fake.requests)
    c.get("/api/billing/plans")
    assert len(fake.requests) == n  # cache 10 phút


def test_plans_price_null_on_polar_error(make_bclient, fake):
    fake.fail = 500
    j = make_bclient().get("/api/billing/plans").json()
    assert j["enabled"] is True and [p["price"] for p in j["plans"]] == [0, None, None]


def test_plans_disabled(make_bclient, fake):
    c = make_bclient(polar_access_token=None)
    j = c.get("/api/billing/plans").json()
    assert j["enabled"] is False and len(j["plans"]) == 3
    assert j["plans"][1]["price"] is None
    assert fake.requests == []


def test_checkout_happy_path(make_bclient, fake):
    c = make_bclient()
    r = c.post("/api/billing/checkout", json={"plan": "personal", "email": "An@Example.com"})
    assert r.status_code == 200, r.text
    assert r.json() == {"url": f"https://sandbox.polar.sh/checkout/{CHECKOUT_ID}"}
    (req,) = fake.calls("/v1/checkouts/")
    body = json.loads(req.content)
    assert body["products"] == [PROD_P]
    assert body["success_url"] == "https://dattts.com/thanh-toan.html?checkout_id={CHECKOUT_ID}"
    assert body["return_url"] == "https://dattts.com/#bang-gia"
    assert body["metadata"] == {"plan": "personal"}
    assert body["customer_email"] == "an@example.com"
    r = c.post("/api/billing/checkout", json={"plan": "unlimited"})
    body = json.loads(fake.calls("/v1/checkouts/")[-1].content)
    assert body["products"] == [PROD_U] and "customer_email" not in body


def test_checkout_bad_plan_and_email(make_bclient, fake):
    c = make_bclient()
    r = c.post("/api/billing/checkout", json={"plan": "vip"})
    assert r.status_code == 400 and err(r) == "bad_plan"
    r = c.post("/api/billing/checkout", json={"plan": "personal", "email": "khong-phai-email"})
    assert r.status_code == 400 and err(r) == "bad_email"
    assert fake.calls("/v1/checkouts/") == []


def test_checkout_billing_disabled(make_bclient):
    c = make_bclient(polar_access_token=None)
    r = c.post("/api/billing/checkout", json={"plan": "personal"})
    assert r.status_code == 503 and err(r) == "billing_disabled"
    assert c.get(f"/api/billing/checkout/{CHECKOUT_ID}").status_code == 503
    r = c.post("/api/billing/portal", headers={"X-API-Key": "x"})
    assert r.status_code == 503 and err(r) == "billing_disabled"


def test_checkout_polar_error_503(make_bclient, fake):
    fake.fail = 500
    r = make_bclient().post("/api/billing/checkout", json={"plan": "personal"})
    assert r.status_code == 503 and err(r) == "billing_unavailable"


def test_checkout_rate_limited(make_bclient):
    c = make_bclient()
    for _ in range(10):
        assert c.post("/api/billing/checkout", json={"plan": "personal"}).status_code == 200
    r = c.post("/api/billing/checkout", json={"plan": "personal"})
    assert r.status_code == 429 and err(r) == "rate_limited"


def test_checkout_status_masks_email(make_bclient, fake):
    c = make_bclient()
    fake.checkout_status = "succeeded"
    r = c.get(f"/api/billing/checkout/{CHECKOUT_ID}")
    assert r.status_code == 200
    assert r.json() == {"status": "succeeded", "plan": "personal", "email": "a***@gmail.com"}


def test_checkout_status_invalid_id(make_bclient, fake):
    c = make_bclient()
    for bad in ("abc", "../../v1/products", CHECKOUT_ID + "0"):
        r = c.get(f"/api/billing/checkout/{bad}")
        assert r.status_code in (400, 404) and err(r) in ("bad_checkout_id", "not_found")
    r = c.get("/api/billing/checkout/not-a-uuid")
    assert r.status_code == 400 and err(r) == "bad_checkout_id"
    assert fake.calls(f"/v1/checkouts/not-a-uuid") == []
    r = c.get("/api/billing/checkout/88888888-8888-4888-8888-888888888888")
    assert r.status_code == 404 and err(r) == "checkout_not_found"


# ───────────────────────────── API: key, /api/me, quota ─────────────────────────────


def test_me_polar_internal_invalid(make_bclient, fake):
    c = make_bclient(api_keys=frozenset({"noi-bo"}))
    fake.add_key("DATTTS-ME", benefit=BEN_P)
    r = c.get("/api/me", headers={"X-API-Key": "DATTTS-ME"})
    assert r.status_code == 200
    j = r.json()
    assert j["plan"] == "personal" and j["plan_name"] == "Cá nhân"
    assert j["used"] == 0 and j["limit"] == 1_000_000 and j["email"] == "a***@gmail.com"
    assert j["reset_at"].endswith("T00:00:00+07:00") and j["reset_at"][8:10] == "01"
    j = c.get("/api/me", headers={"X-API-Key": "noi-bo"}).json()
    assert j["plan"] == "internal" and j["limit"] is None
    r = c.get("/api/me", headers={"X-API-Key": "DATTTS-SAI"})
    assert r.status_code == 401 and err(r) == "invalid_api_key"
    r = c.get("/api/me")
    assert r.status_code == 401 and err(r) == "api_key_required"


def test_invalid_key_does_not_fall_back_to_free(make_bclient):
    c = make_bclient()
    r = c.post("/api/tts", json={"text": "Xin chào."}, headers={"X-API-Key": "DATTTS-HONG"})
    assert r.status_code == 401 and err(r) == "invalid_api_key"
    assert c.get("/api/quota").json()["used"] == 0


def test_paid_quota_counting_and_429(make_bclient, fake):
    c = make_bclient(plan_personal_monthly_chars=40, free_daily_chars=1000)
    fake.add_key("DATTTS-Q", benefit=BEN_P, lkid="lk-q")
    h = {"X-API-Key": "DATTTS-Q"}
    r = c.post("/api/tts", json={"text": "Một hai ba bốn năm sáu bảy."}, headers=h)  # 27 ký tự
    assert r.status_code == 200, r.text
    q = r.json()["quota"]
    assert q["used"] == 27 and q["limit"] == 40 and q["remaining"] == 13 and q["plan"] == "personal"
    month = B.month_id()
    assert c.store.kv_get(f"usage:lk-q:{month}") == 27
    # không tính vào quota IP miễn phí
    assert c.get("/api/quota").json()["used"] == 0
    r = c.post("/api/tts", json={"text": "Tám chín mười mười một mười hai."}, headers=h)
    assert r.status_code == 429 and err(r) == "quota_exceeded"
    msg = r.json()["error"]["message"]
    assert "40" in msg and "13" in msg and "/" in msg  # nêu hạn mức tháng và ngày reset
    assert c.store.kv_get(f"usage:lk-q:{month}") == 27  # lần bị từ chối không bị trừ
    q = c.get("/api/quota", headers=h).json()
    assert q["used"] == 27 and q["limit"] == 40 and q["remaining"] == 13
    assert c.get("/api/me", headers=h).json()["used"] == 27
    # cache hit vẫn miễn phí
    r = c.post("/api/tts", json={"text": "Một hai ba bốn năm sáu bảy."}, headers=h)
    assert r.status_code == 200 and r.json()["cached"] is True


def test_paid_quota_resets_monthly(make_bclient, fake, monkeypatch):
    c = make_bclient(plan_personal_monthly_chars=30)
    fake.add_key("DATTTS-M", lkid="lk-m")
    h = {"X-API-Key": "DATTTS-M"}
    assert c.post("/api/tts", json={"text": "Một hai ba bốn năm sáu bảy."}, headers=h).status_code == 200
    assert c.post("/api/tts", json={"text": "Tám chín mười một."}, headers=h).status_code == 429
    real_month = B.month_id
    monkeypatch.setattr(B, "month_id", lambda now=None: "2099-01")
    r = c.post("/api/tts", json={"text": "Tám chín mười một."}, headers=h)
    assert r.status_code == 200
    assert c.store.kv_get("usage:lk-m:2099-01") == r.json()["quota"]["used"] > 0
    assert c.store.kv_get(f"usage:lk-m:{real_month()}") == 27


def test_paid_jobs_charge_monthly_bucket(make_bclient, fake):
    c = make_bclient(free_daily_chars=10)
    fake.add_key("DATTTS-J", lkid="lk-j")
    h = {"X-API-Key": "DATTTS-J"}
    text = " ".join(f"Câu số {i} trong bài." for i in range(10))
    r = c.post("/api/jobs", json={"text": text}, headers=h)
    assert r.status_code == 202, r.text
    used = c.store.kv_get(f"usage:lk-j:{B.month_id()}")
    assert used and used > 10
    assert c.get("/api/quota").json()["used"] == 0
    for _ in range(200):
        s = c.get(f"/api/jobs/{r.json()['job_id']}", headers=h).json()
        if s["status"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert s["status"] == "done" and s["result"]["quota"]["used"] == used


def test_internal_key_still_unlimited(make_bclient, fake):
    c = make_bclient(api_keys=frozenset({"noi-bo"}), free_daily_chars=5)
    r = c.post("/api/tts", json={"text": "Một hai ba bốn năm sáu bảy."}, headers={"X-API-Key": "noi-bo"})
    assert r.status_code == 200 and r.json()["quota"]["limit"] is None
    assert fake.calls("/v1/license-keys/validate") == []


def test_polar_down_without_cache_returns_503_for_tts(make_bclient, fake):
    c = make_bclient()
    fake.fail = 503
    r = c.post("/api/tts", json={"text": "Xin chào."}, headers={"X-API-Key": "DATTTS-DOWN"})
    assert r.status_code == 503 and err(r) == "billing_unavailable"


def test_portal(make_bclient, fake):
    c = make_bclient(api_keys=frozenset({"noi-bo"}))
    fake.add_key("DATTTS-PORT")
    r = c.post("/api/billing/portal", headers={"X-API-Key": "DATTTS-PORT"})
    assert r.status_code == 200 and r.json() == {"url": f"https://polar.sh/portal?c={CUST}"}
    body = json.loads(fake.calls("/v1/customer-sessions/")[0].content)
    assert body == {"customer_id": CUST, "return_url": "https://dattts.com"}
    r = c.post("/api/billing/portal")
    assert r.status_code == 401
    r = c.post("/api/billing/portal", headers={"X-API-Key": "noi-bo"})
    assert r.status_code == 400 and err(r) == "not_polar_key"
    r = c.post("/api/billing/portal", headers={"X-API-Key": "DATTTS-SAI"})
    assert r.status_code == 401 and err(r) == "invalid_api_key"


# ───────────────────────────── webhook ─────────────────────────────


def _event(etype, customer=CUST, **data):
    if etype.startswith("customer."):
        d = {"id": customer, "email": "an.nguyen@gmail.com", **data}
    else:
        d = {"id": "sub_1", "customer_id": customer, "status": "revoked", **data}
    return json.dumps({"type": etype, "timestamp": "2026-11-08T00:00:00Z", "data": d}).encode()


def test_webhook_bad_signature_403(make_bclient):
    c = make_bclient()
    body = _event("subscription.revoked")
    h = sign(body, NEW_SECRET)
    r = c.post("/api/billing/webhook", content=body + b" ", headers=h)
    assert r.status_code == 403 and err(r) == "bad_signature"
    r = c.post("/api/billing/webhook", content=body)
    assert r.status_code == 403


def test_webhook_202_old_style_secret(make_bclient):
    c = make_bclient(polar_webhook_secret=OLD_SECRET)
    body = _event("order.paid", metadata={"plan": "personal"})
    r = c.post("/api/billing/webhook", content=body, headers=sign(body, OLD_SECRET, old_style=True))
    assert r.status_code == 202


def test_webhook_without_secret_503(make_bclient):
    c = make_bclient(polar_webhook_secret=None)
    body = _event("order.paid")
    assert c.post("/api/billing/webhook", content=body, headers=sign(body, NEW_SECRET)).status_code == 503


def test_webhook_idempotent_and_invalidates_cache(make_bclient, fake):
    c = make_bclient()
    fake.add_key("DATTTS-W", lkid="lk-w")
    h = {"X-API-Key": "DATTTS-W"}
    assert c.get("/api/me", headers=h).status_code == 200
    hk = "lk:" + hashlib.sha256(b"DATTTS-W").hexdigest()
    assert c.store.kv_get(hk) is not None
    assert len(fake.calls("/v1/license-keys/validate")) == 1
    # Polar thu hồi key, rồi gửi webhook
    del fake.keys["DATTTS-W"]
    body = _event("subscription.revoked")
    hdr = sign(body, NEW_SECRET, msg_id="evt_revoke")
    r = c.post("/api/billing/webhook", content=body, headers={**hdr, "Content-Type": "application/json",
                                                              "Origin": "https://evil.example"})
    assert r.status_code == 202 and r.json()["ok"] is True
    assert c.store.kv_get(hk) is None and c.store.kv_get(f"lkc:{CUST}") is None
    r = c.get("/api/me", headers=h)
    assert r.status_code == 401 and err(r) == "invalid_api_key"  # không còn dùng cache 10 phút
    # gửi lại cùng webhook-id → 202, không ghi nhật ký lần hai
    r = c.post("/api/billing/webhook", content=body, headers=hdr)
    assert r.status_code == 202 and r.json().get("duplicate") is True
    events = c.store.kv_get("billing_events")
    assert [e["id"] for e in events] == ["evt_revoke"]
    assert events[0]["type"] == "subscription.revoked" and events[0]["invalidated"] == 1
    assert "DATTTS-W" not in (c.store.root / "kv.json").read_text(encoding="utf-8")


def test_webhook_customer_state_changed_and_ignored_events(make_bclient, fake):
    c = make_bclient()
    fake.add_key("DATTTS-S")
    c.get("/api/me", headers={"X-API-Key": "DATTTS-S"})
    hk = "lk:" + hashlib.sha256(b"DATTTS-S").hexdigest()
    body = _event("order.paid")
    assert c.post("/api/billing/webhook", content=body, headers=sign(body, NEW_SECRET, "e1")).status_code == 202
    assert c.store.kv_get(hk) is not None  # order.paid không xoá cache
    body = _event("customer.state_changed")
    assert c.post("/api/billing/webhook", content=body, headers=sign(body, NEW_SECRET, "e2")).status_code == 202
    assert c.store.kv_get(hk) is None


def test_webhook_events_capped(make_bclient):
    c = make_bclient()
    c.store.kv_set("billing_events", [{"id": f"old{i}", "type": "order.paid"} for i in range(500)])
    body = _event("order.paid")
    c.post("/api/billing/webhook", content=body, headers=sign(body, NEW_SECRET, "new"))
    ev = c.store.kv_get("billing_events")
    assert len(ev) == 500 and ev[-1]["id"] == "new" and ev[0]["id"] == "old1"


def test_webhook_not_blocked_by_body_limit(make_bclient):
    c = make_bclient(job_max_chars=10)  # giới hạn body chung rất nhỏ
    body = _event("order.paid", padding="x" * 700_000)
    r = c.post("/api/billing/webhook", content=body, headers=sign(body, NEW_SECRET, "big"))
    assert r.status_code == 202
    big = b"x" * (3 * 1024 * 1024)
    assert c.post("/api/billing/webhook", content=big, headers=sign(big, NEW_SECRET, "huge")).status_code == 413


# ───────────────────────────── admin ─────────────────────────────


def test_admin_billing_auth_and_summary(make_bclient, fake):
    c = make_bclient(admin_token="adm-123")
    assert c.get("/api/admin/billing").status_code == 401
    assert c.get("/api/admin/billing", headers={"Authorization": "Bearer sai"}).status_code == 401
    fake.add_key("DATTTS-A1", benefit=BEN_P, lkid="a1")
    fake.add_key("DATTTS-A2", benefit=BEN_U, lkid="a2", customer="c2")
    c.post("/api/tts", json={"text": "Một hai ba."}, headers={"X-API-Key": "DATTTS-A1"})
    c.post("/api/tts", json={"text": "Bốn năm sáu bảy."}, headers={"X-API-Key": "DATTTS-A2"})
    body = _event("order.paid")
    c.post("/api/billing/webhook", content=body, headers=sign(body, NEW_SECRET, "ev1"))
    r = c.get("/api/admin/billing", headers={"Authorization": "Bearer adm-123"})
    assert r.status_code == 200
    j = r.json()
    assert j["usage_by_plan"]["personal"] > 0 and j["usage_by_plan"]["unlimited"] > 0
    assert j["keys_by_plan"] == {"personal": 1, "unlimited": 1}
    assert j["events_total"] == 1 and j["events_by_type"] == {"order.paid": 1}
    assert make_bclient().get("/api/admin/billing").status_code == 404  # không có ADMIN_TOKEN


def test_polar_client_sends_auth_and_base(fake):
    s = Settings.from_env({}, **POLAR_ENV)
    cl = B.PolarClient.from_settings(s, transport=httpx.MockTransport(fake))
    cl.get_product(PROD_P)
    req = fake.requests[-1]
    assert str(req.url) == f"https://polar.test/v1/products/{PROD_P}"
    assert req.headers["authorization"] == "Bearer polar_oat_test"
    assert cl.validate_license_key("DATTTS-NONE") is None
    fake.add_key("DATTTS-YES")
    assert cl.validate_license_key("DATTTS-YES")["id"] == "lk-1"
