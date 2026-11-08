"""Thanh toán qua Polar (SPEC Phần 2 + 3) — Polar giả bằng httpx.MockTransport, không gọi mạng."""

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
PROD_W = "22222222-2222-4222-8222-22222222222a"
PROD_M = "22222222-2222-4222-8222-222222222222"
PROD_Y = "33333333-3333-4333-8333-333333333333"
BEN_W = "44444444-4444-4444-8444-44444444444a"
BEN_M = "44444444-4444-4444-8444-444444444444"
BEN_Y = "55555555-5555-4555-8555-555555555555"
CHECKOUT_ID = "66666666-6666-4666-8666-666666666666"
CUST = "77777777-7777-4777-8777-777777777777"

NEW_SECRET = "whsec_" + base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()
OLD_SECRET = "whsec_polar-old-style-secret-not-base64!"

POLAR_ENV = dict(
    polar_access_token="polar_oat_test",
    polar_organization_id=ORG,
    polar_webhook_secret=NEW_SECRET,
    polar_product_pro_week=PROD_W,
    polar_product_pro_month=PROD_M,
    polar_product_pro_year=PROD_Y,
    polar_benefit_pro_week=BEN_W,
    polar_benefit_pro_month=BEN_M,
    polar_benefit_pro_year=BEN_Y,
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
        self.prices = {PROD_W: 199, PROD_M: 499, PROD_Y: 4999}
        self.intervals = {PROD_W: "week", PROD_M: "month", PROD_Y: "year"}
        self.checkout_fail: int | None = None  # mã lỗi riêng cho POST /v1/checkouts/
        self.archived: set[str] = set()        # sản phẩm đã lưu trữ (archived)
        self.custom_price: set[str] = set()    # sản phẩm chỉ có giá "khách tự trả" (không có giá cố định)

    def add_key(self, key, benefit=BEN_M, lkid="lk-1", customer=CUST, email="an.nguyen@gmail.com"):
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
            if self.checkout_fail:
                return httpx.Response(self.checkout_fail, json={"detail": [{"loc": ["body", "customer_email"]}]})
            return httpx.Response(201, json={"id": CHECKOUT_ID, "url": f"https://sandbox.polar.sh/checkout/{CHECKOUT_ID}",
                                             "status": "open", "client_secret": "cs", "metadata": body["metadata"],
                                             "product_id": body["products"][0]})
        if m == "GET" and p.startswith("/v1/checkouts/"):
            cid = p.rsplit("/", 1)[1]
            if cid != CHECKOUT_ID:
                return httpx.Response(404, json={"detail": "not found"})
            return httpx.Response(200, json={"id": cid, "status": self.checkout_status, "product_id": PROD_M,
                                             "metadata": {"plan": "pro_month"},
                                             "customer_email": "an.nguyen@gmail.com"})
        if m == "GET" and p.startswith("/v1/products/"):
            pid = p.rsplit("/", 1)[1]
            prices = ([{"amount_type": "custom", "price_currency": "usd"}] if pid in self.custom_price else
                      [{"amount_type": "fixed", "price_amount": self.prices[pid], "price_currency": "usd"}])
            return httpx.Response(200, json={"id": pid, "name": "Gói", "is_archived": pid in self.archived,
                                             "recurring_interval": self.intervals[pid], "recurring_interval_count": 1,
                                             "prices": prices})
        if m == "POST" and p == "/v1/license-keys/validate":
            body = json.loads(request.content)
            if body.get("organization_id") != ORG:  # Polar: organization_id bắt buộc (uuid4)
                return httpx.Response(422, json={"detail": [{"loc": ["body", "organization_id"]}]})
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
                           "POLAR_ORGANIZATION_ID": ORG, "POLAR_PRODUCT_PRO_MONTH": PROD_M,
                           "POLAR_BENEFIT_PRO_MONTH": BEN_M, "POLAR_PRODUCT_PRO_WEEK": PROD_W,
                           "POLAR_BENEFIT_PRO_WEEK": BEN_W, "POLAR_PRODUCT_PRO_YEAR": PROD_Y,
                           "POLAR_BENEFIT_PRO_YEAR": BEN_Y, "PLAN_PRO_MONTH_CHARS": "5",
                           "PLAN_PRO_WEEK_CHARS": "7", "PLAN_PRO_YEAR_MONTHLY_CHARS": "9",
                           "PUBLIC_BASE_URL": "http://127.0.0.1:8765/"})
    assert s.billing_enabled and s.polar_base_url == "https://api.polar.sh"
    assert (s.plan_pro_week_chars, s.plan_pro_month_chars, s.plan_pro_year_monthly_chars) == (7, 5, 9)
    assert [s.plan_chars(p) for p in ("pro_week", "pro_month", "pro_year")] == [7, 5, 9]
    assert s.polar_product("pro_week") == PROD_W and s.polar_benefit("pro_year") == BEN_Y
    # Thiếu POLAR_WEBHOOK_SECRET: vẫn bán (billing bật) nhưng phải được cảnh báo (webhook sẽ 503)
    assert s.polar_product("personal") is None and s.billing_missing() == ["POLAR_WEBHOOK_SECRET"]
    assert s.billing_enabled
    d = Settings.from_env({})
    assert (d.plan_pro_week_chars, d.plan_pro_month_chars, d.plan_pro_year_monthly_chars) == \
        (250_000, 1_000_000, 1_000_000)
    # Biến cũ của Phần 2 không còn tác dụng
    old = Settings.from_env({"POLAR_ACCESS_TOKEN": "x", "POLAR_ORGANIZATION_ID": ORG,
                             "POLAR_PRODUCT_PERSONAL": PROD_M, "POLAR_BENEFIT_PERSONAL": BEN_M})
    assert not old.billing_enabled
    assert not hasattr(old, "polar_product_personal") and not hasattr(old, "plan_unlimited_monthly_chars")
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
    fake.add_key("DATTTS-AAAA", benefit=BEN_Y, lkid="lk-u")
    t0 = 1_800_000_000.0
    info = B.resolve_api_key("DATTTS-AAAA", settings, store, client, now=t0)
    assert info == {"kind": "polar", "license_key_id": "lk-u", "customer_id": CUST,
                    "email": "an.nguyen@gmail.com", "plan": "pro_year"}
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
    assert B.resolve_api_key("DATTTS-NOPE", settings, store, client, now=t0 + 61)["plan"] == "pro_month"
    assert len(fake.calls("/v1/license-keys/validate")) == 2


def test_resolve_uses_stale_cache_on_polar_5xx(ctx, fake):
    settings, store, client = ctx
    fake.add_key("DATTTS-OK")
    t0 = 1_800_000_000.0
    B.resolve_api_key("DATTTS-OK", settings, store, client, now=t0)
    fake.fail = 502
    info = B.resolve_api_key("DATTTS-OK", settings, store, client, now=t0 + 3 * 3600)  # cache đã hết hạn
    assert info["plan"] == "pro_month"
    fake.fail, fake.network_error = None, True
    assert B.resolve_api_key("DATTTS-OK", settings, store, client, now=t0 + 23 * 3600)["plan"] == "pro_month"
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
    fake.add_key("DATTTS-P", benefit=BEN_M, lkid="a")
    fake.add_key("DATTTS-U", benefit=BEN_Y, lkid="b")
    fake.add_key("DATTTS-X", benefit="99999999-9999-4999-8999-999999999999", lkid="c")
    assert B.resolve_api_key("DATTTS-P", settings, store, client)["plan"] == "pro_month"
    assert B.resolve_api_key("DATTTS-U", settings, store, client)["plan"] == "pro_year"
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
    r = c.get("/api/billing/plans")
    j = r.json()
    assert j["enabled"] is True and j["currency"] == "usd"
    ids = [p["id"] for p in j["plans"]]
    assert ids == ["free", "pro_week", "pro_month", "pro_year"]
    free, week, month, year = j["plans"]
    assert free["price"] == 0 and free["interval"] is None and free["chars"] == 20_000
    assert free["period"] == "day" and free["name"] == "Free"
    assert week == {"id": "pro_week", "name": "Pro Weekly", "price": 199, "currency": "usd", "interval": "week",
                    "interval_count": 1, "chars": 250_000, "period": "week", "available": True,
                    "popular": False, "save_percent": None}
    assert month["price"] == 499 and month["interval"] == "month" and month["chars"] == 1_000_000
    assert month["period"] == "month" and month["popular"] is True and month["save_percent"] == 37
    assert year["price"] == 4999 and year["interval"] == "year" and year["chars"] == 1_000_000
    assert year["period"] == "month" and year["popular"] is False and year["save_percent"] == 16
    assert "accept-language" in r.headers["vary"].lower()
    n = len(fake.requests)
    vi = c.get("/api/billing/plans", headers={"Accept-Language": "vi-VN,vi;q=0.9"}).json()
    assert len(fake.requests) == n  # cache 10 phút (dùng chung cho mọi ngôn ngữ)
    assert [p["name"] for p in vi["plans"]] == ["Dùng thử", "Pro tuần", "Pro tháng", "Pro năm"]
    assert [p["price"] for p in vi["plans"]] == [0, 199, 499, 4999]


def test_save_percent_math():
    assert B.save_percent(499, 199, 4) == 37      # 1 − 4,99/7,96 = 37,31 %
    assert B.save_percent(4999, 499, 12) == 16    # 1 − 49,99/59,88 = 16,52 % → làm tròn XUỐNG 16
    assert B.save_percent(450, 100, 5) == 10      # đúng 10 %
    assert B.save_percent(985, 100, 10) == 1      # 1,5 % → 1
    assert B.save_percent(995, 100, 10) is None   # 0,5 % → 0 → không hiện
    assert B.save_percent(996, 100, 10) is None   # 0,4 % → 0 → không hiện
    assert B.save_percent(400, 100, 4) is None    # không rẻ hơn
    assert B.save_percent(500, 100, 4) is None    # đắt hơn
    assert B.save_percent(None, 199, 4) is None and B.save_percent(499, None, 4) is None
    assert B.save_percent(499, 0, 4) is None and B.save_percent(499, 199, 0) is None


def test_save_percent_computed_from_polar_prices(make_bclient, fake):
    fake.prices = {PROD_W: 300, PROD_M: 900, PROD_Y: 5400}  # 25 % và 50 %
    plans = {p["id"]: p for p in make_bclient().get("/api/billing/plans").json()["plans"]}
    assert plans["pro_month"]["save_percent"] == 25 and plans["pro_year"]["save_percent"] == 50


def test_plan_unavailable_when_one_product_missing(make_bclient, fake, caplog):
    with caplog.at_level("ERROR", logger="dattts.billing"):
        c = make_bclient(polar_product_pro_week=None)
    assert "POLAR_PRODUCT_PRO_WEEK" in caplog.text
    j = c.get("/api/billing/plans").json()
    plans = {p["id"]: p for p in j["plans"]}
    assert j["enabled"] is True
    assert plans["pro_week"]["available"] is False and plans["pro_week"]["price"] is None
    assert plans["pro_week"]["interval"] == "week"  # chu kỳ mặc định khi không có sản phẩm
    assert plans["pro_month"]["available"] is True and plans["pro_month"]["price"] == 499
    assert plans["pro_month"]["save_percent"] is None  # thiếu giá tuần → không tính được
    assert plans["pro_year"]["available"] is True and plans["pro_year"]["save_percent"] == 16
    assert not any(PROD_W in str(r.url) for r in fake.requests)
    r = c.post("/api/billing/checkout", json={"plan": "pro_week"})
    assert r.status_code == 503 and err(r) == "billing_disabled"
    assert c.post("/api/billing/checkout", json={"plan": "pro_year"}).status_code == 200


def test_plans_price_null_on_polar_error(make_bclient, fake):
    fake.fail = 500
    j = make_bclient().get("/api/billing/plans").json()
    assert j["enabled"] is True and [p["price"] for p in j["plans"]] == [0, None, None, None]
    assert [p["save_percent"] for p in j["plans"]] == [None, None, None, None]


def test_plans_disabled(make_bclient, fake):
    c = make_bclient(polar_access_token=None)
    j = c.get("/api/billing/plans").json()
    assert j["enabled"] is False and len(j["plans"]) == 4
    assert all(p["price"] is None and p["available"] is False for p in j["plans"][1:])
    assert [p["interval"] for p in j["plans"][1:]] == ["week", "month", "year"]
    assert fake.requests == []


def test_checkout_happy_path(make_bclient, fake):
    c = make_bclient()
    r = c.post("/api/billing/checkout", json={"plan": "pro_month", "email": "An@Example.com"})
    assert r.status_code == 200, r.text
    assert r.json() == {"url": f"https://sandbox.polar.sh/checkout/{CHECKOUT_ID}"}
    (req,) = fake.calls("/v1/checkouts/")
    body = json.loads(req.content)
    assert body["products"] == [PROD_M]
    assert body["success_url"] == "https://dattts.com/checkout-success.html?checkout_id={CHECKOUT_ID}"
    assert body["return_url"] == "https://dattts.com/#pricing"
    assert body["metadata"] == {"plan": "pro_month"}
    assert body["customer_email"] == "an@example.com"
    r = c.post("/api/billing/checkout", json={"plan": "pro_year"})
    body = json.loads(fake.calls("/v1/checkouts/")[-1].content)
    assert body["products"] == [PROD_Y] and "customer_email" not in body
    r = c.post("/api/billing/checkout", json={"plan": "PRO_WEEK"})
    body = json.loads(fake.calls("/v1/checkouts/")[-1].content)
    assert r.status_code == 200 and body["products"] == [PROD_W] and body["metadata"] == {"plan": "pro_week"}


def test_checkout_bad_plan_and_email(make_bclient, fake):
    c = make_bclient()
    for bad in ("vip", "personal", "unlimited", "free"):
        r = c.post("/api/billing/checkout", json={"plan": bad})
        assert r.status_code == 400 and err(r) == "bad_plan"
    r = c.post("/api/billing/checkout", json={"plan": "pro_month", "email": "khong-phai-email"})
    assert r.status_code == 400 and err(r) == "bad_email"
    assert fake.calls("/v1/checkouts/") == []


def test_checkout_billing_disabled(make_bclient):
    c = make_bclient(polar_access_token=None)
    r = c.post("/api/billing/checkout", json={"plan": "pro_month"})
    assert r.status_code == 503 and err(r) == "billing_disabled"
    assert c.get(f"/api/billing/checkout/{CHECKOUT_ID}").status_code == 503
    r = c.post("/api/billing/portal", headers={"X-API-Key": "x"})
    assert r.status_code == 503 and err(r) == "billing_disabled"


def test_checkout_polar_error_503(make_bclient, fake):
    fake.fail = 500
    r = make_bclient().post("/api/billing/checkout", json={"plan": "pro_month"})
    assert r.status_code == 503 and err(r) == "billing_unavailable"


def test_checkout_rate_limited(make_bclient):
    c = make_bclient()
    for _ in range(10):
        assert c.post("/api/billing/checkout", json={"plan": "pro_month"}).status_code == 200
    r = c.post("/api/billing/checkout", json={"plan": "pro_month"})
    assert r.status_code == 429 and err(r) == "rate_limited"


def test_checkout_status_masks_email(make_bclient, fake):
    c = make_bclient()
    fake.checkout_status = "succeeded"
    r = c.get(f"/api/billing/checkout/{CHECKOUT_ID}")
    assert r.status_code == 200
    assert r.json() == {"status": "succeeded", "plan": "pro_month", "email": "a***@gmail.com"}


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
    fake.add_key("DATTTS-ME", benefit=BEN_M)
    r = c.get("/api/me", headers={"X-API-Key": "DATTTS-ME"})
    assert r.status_code == 200
    j = r.json()
    assert j["plan"] == "pro_month" and j["plan_name"] == "Pro Monthly" and j["period"] == "month"
    assert j["used"] == 0 and j["limit"] == 1_000_000 and j["email"] == "a***@gmail.com"
    assert j["reset_at"].endswith("T00:00:00+07:00") and j["reset_at"][8:10] == "01"
    j = c.get("/api/me", headers={"X-API-Key": "DATTTS-ME", "Accept-Language": "vi"}).json()
    assert j["plan_name"] == "Pro tháng"
    j = c.get("/api/me", headers={"X-API-Key": "noi-bo"}).json()
    assert j["plan"] == "internal" and j["limit"] is None and j["period"] == "day"
    assert j["plan_name"] == "Internal"
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
    c = make_bclient(plan_pro_month_chars=40, free_daily_chars=1000)
    fake.add_key("DATTTS-Q", benefit=BEN_M, lkid="lk-q")
    h = {"X-API-Key": "DATTTS-Q"}
    r = c.post("/api/tts", json={"text": "Một hai ba bốn năm sáu bảy."}, headers=h)  # 27 ký tự
    assert r.status_code == 200, r.text
    q = r.json()["quota"]
    assert q["used"] == 27 and q["limit"] == 40 and q["remaining"] == 13 and q["plan"] == "pro_month"
    assert q["period"] == "month"
    month = B.month_id()
    assert c.store.kv_get(f"usage:lk-q:{month}") == 27
    # không tính vào quota IP miễn phí
    assert c.get("/api/quota").json()["used"] == 0
    r = c.post("/api/tts", json={"text": "Tám chín mười mười một mười hai."}, headers=h)
    assert r.status_code == 429 and err(r) == "quota_exceeded"
    msg = r.json()["error"]["message"]  # mặc định tiếng Anh: nêu hạn mức, phần còn lại và lúc reset
    assert "Pro Monthly" in msg and "40" in msg and "13" in msg and "this month" in msg
    assert "at 00:00 (UTC+7)" in msg and ", 20" in msg
    r = c.post("/api/tts", json={"text": "Tám chín mười mười một mười hai."},
               headers={**h, "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.5"})
    msg = r.json()["error"]["message"]
    assert r.status_code == 429 and "Gói Pro tháng" in msg and "tháng này" in msg and "00:00 ngày 01/" in msg
    assert c.store.kv_get(f"usage:lk-q:{month}") == 27  # lần bị từ chối không bị trừ
    q = c.get("/api/quota", headers=h).json()
    assert q["used"] == 27 and q["limit"] == 40 and q["remaining"] == 13 and q["period"] == "month"
    assert c.get("/api/me", headers=h).json()["used"] == 27
    # cache hit vẫn miễn phí
    r = c.post("/api/tts", json={"text": "Một hai ba bốn năm sáu bảy."}, headers=h)
    assert r.status_code == 200 and r.json()["cached"] is True


def test_paid_quota_resets_monthly(make_bclient, fake, monkeypatch):
    c = make_bclient(plan_pro_month_chars=30)
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
    body = _event("order.paid", metadata={"plan": "pro_month"})
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
    fake.add_key("DATTTS-A1", benefit=BEN_M, lkid="a1")
    fake.add_key("DATTTS-A2", benefit=BEN_Y, lkid="a2", customer="c2")
    c.post("/api/tts", json={"text": "Một hai ba."}, headers={"X-API-Key": "DATTTS-A1"})
    c.post("/api/tts", json={"text": "Bốn năm sáu bảy."}, headers={"X-API-Key": "DATTTS-A2"})
    fake.add_key("DATTTS-A3", benefit=BEN_W, lkid="a3", customer="c3")
    c.post("/api/tts", json={"text": "Tám chín."}, headers={"X-API-Key": "DATTTS-A3"})
    body = _event("order.paid")
    c.post("/api/billing/webhook", content=body, headers=sign(body, NEW_SECRET, "ev1"))
    r = c.get("/api/admin/billing", headers={"Authorization": "Bearer adm-123"})
    assert r.status_code == 200
    j = r.json()
    assert j["usage_by_plan"]["pro_month"] > 0 and j["usage_by_plan"]["pro_year"] > 0
    assert j["usage_by_plan"]["pro_week"] > 0 and j["week"] == B.week_id() and j["month"] == B.month_id()
    assert j["keys_by_plan"] == {"pro_week": 1, "pro_month": 1, "pro_year": 1}
    assert j["events_total"] == 1 and j["events_by_type"] == {"order.paid": 1}
    assert make_bclient().get("/api/admin/billing").status_code == 404  # không có ADMIN_TOKEN


def test_polar_client_sends_auth_and_base(fake):
    s = Settings.from_env({}, **POLAR_ENV)
    cl = B.PolarClient.from_settings(s, transport=httpx.MockTransport(fake))
    cl.get_product(PROD_M)
    req = fake.requests[-1]
    assert str(req.url) == f"https://polar.test/v1/products/{PROD_M}"
    assert req.headers["authorization"] == "Bearer polar_oat_test"
    assert cl.validate_license_key("DATTTS-NONE") is None
    fake.add_key("DATTTS-YES")
    assert cl.validate_license_key("DATTTS-YES")["id"] == "lk-1"


# ───────────────────────────── Sửa lỗi review thanh toán ─────────────────────────────


def test_malformed_keys_rejected_without_calling_polar(make_bclient, fake):
    c = make_bclient()
    for k in ("sai", "random-" + "x" * 30, "DATTTS-", "DATTTS-a b", "DATTTS-" + "A" * 300):
        r = c.get("/api/me", headers={"X-API-Key": k})
        assert r.status_code == 401 and err(r) == "invalid_api_key", k
    assert fake.calls("/v1/license-keys/validate") == []
    raw = (c.store.root / "kv.json").read_text(encoding="utf-8") if (c.store.root / "kv.json").exists() else ""
    assert '"lk:' not in raw


def test_key_validation_rate_limited_per_ip(make_bclient, fake):
    c = make_bclient()
    fake.add_key("DATTTS-GOOD-1")
    codes = [c.get("/api/quota", headers={"X-API-Key": f"DATTTS-RAND-{i:04d}"}).status_code for i in range(25)]
    assert codes[:20] == [401] * 20 and set(codes[20:]) == {429}
    assert len(fake.calls("/v1/license-keys/validate")) == 20
    r = c.post("/api/tts", json={"text": "Xin chào."}, headers={"X-API-Key": "DATTTS-GOOD-1"})
    assert r.status_code == 429 and err(r) == "rate_limited" and r.headers["retry-after"] == "60"
    assert len(fake.calls("/v1/license-keys/validate")) == 20
    # Không gửi key → vẫn dùng thử bình thường
    assert c.post("/api/tts", json={"text": "Xin chào."}).status_code == 200


def test_rate_limited_validation_falls_back_to_stale_cache(tmp_path, fake):
    settings = Settings.from_env({}, **POLAR_ENV)
    store = LocalStore(str(tmp_path / "kv"))
    client = B.PolarClient.from_settings(settings, transport=httpx.MockTransport(fake))
    fake.add_key("DATTTS-STALE")
    t0 = 1_800_000_000.0
    B.resolve_api_key("DATTTS-STALE", settings, store, client, now=t0)

    def deny():
        raise B.BillingError("rate_limited", status=429)

    n = len(fake.requests)
    assert B.resolve_api_key("DATTTS-STALE", settings, store, client, now=t0 + 700, on_miss=deny)["plan"] == "pro_month"
    assert len(fake.requests) == n
    with pytest.raises(B.BillingError) as e:
        B.resolve_api_key("DATTTS-OTHER", settings, store, client, now=t0, on_miss=deny)
    assert e.value.status == 429


@pytest.mark.parametrize("missing", ["polar_organization_id", "both_benefits", "both_products"])  # "both" = tất cả
def test_partial_polar_config_disables_billing(make_bclient, fake, caplog, missing):
    over = {"polar_organization_id": {"polar_organization_id": None},
            "both_benefits": {f"polar_benefit_{p}": None for p in ("pro_week", "pro_month", "pro_year")},
            "both_products": {f"polar_product_{p}": None for p in ("pro_week", "pro_month", "pro_year")}}[missing]
    with caplog.at_level("ERROR", logger="dattts.billing"):
        c = make_bclient(**over)
    assert "Cấu hình Polar thiếu" in caplog.text and "billing TẮT" in caplog.text
    j = c.get("/api/billing/plans").json()
    assert j["enabled"] is False and not any(p["available"] for p in j["plans"] if p["id"] != "free")
    r = c.post("/api/billing/checkout", json={"plan": "pro_month"})
    assert r.status_code == 503 and err(r) == "billing_disabled"
    assert fake.requests == []


def test_plan_without_benefit_not_sold(make_bclient, fake, caplog):
    with caplog.at_level("ERROR", logger="dattts.billing"):
        c = make_bclient(polar_benefit_pro_year=None)
    assert "POLAR_BENEFIT_PRO_YEAR" in caplog.text
    j = c.get("/api/billing/plans").json()
    avail = {p["id"]: p.get("available") for p in j["plans"]}
    assert j["enabled"] is True and avail["pro_month"] is True and avail["pro_year"] is False
    r = c.post("/api/billing/checkout", json={"plan": "pro_year"})
    assert r.status_code == 503 and err(r) == "billing_disabled"
    assert c.post("/api/billing/checkout", json={"plan": "pro_month"}).status_code == 200


def test_validate_always_sends_org_and_422_is_unavailable(tmp_path, fake, caplog):
    settings = Settings.from_env({}, **POLAR_ENV)
    store = LocalStore(str(tmp_path / "kv"))
    client = B.PolarClient.from_settings(settings, transport=httpx.MockTransport(fake))
    fake.add_key("DATTTS-ORG")
    B.resolve_api_key("DATTTS-ORG", settings, store, client)
    assert json.loads(fake.calls("/v1/license-keys/validate")[-1].content)["organization_id"] == ORG
    # Org sai → Polar 422: lỗi cấu hình → 503, KHÔNG cache như key sai
    bad = Settings.from_env({}, **{**POLAR_ENV, "polar_organization_id": "sai-org"})
    bclient = B.PolarClient.from_settings(bad, transport=httpx.MockTransport(fake))
    with caplog.at_level("ERROR", logger="dattts.billing"), pytest.raises(B.BillingError) as e:
        B.resolve_api_key("DATTTS-ORG", bad, LocalStore(str(tmp_path / "kv2")), bclient)
    assert e.value.status == 503 and e.value.code == "billing_unavailable"
    assert "POLAR_ORGANIZATION_ID" in caplog.text
    assert '"lk:' not in ((tmp_path / "kv2" / "kv.json").read_text() if (tmp_path / "kv2" / "kv.json").exists() else "")


def test_price_interval_from_product_level(make_bclient, fake):
    j = make_bclient().get("/api/billing/plans").json()
    assert [(p["interval"], p["interval_count"]) for p in j["plans"][1:]] == \
        [("week", 1), ("month", 1), ("year", 1)]  # fake: chu kỳ chỉ ở cấp sản phẩm


def test_price_interval_legacy_price_fallback(tmp_path):
    def legacy(request):
        return httpx.Response(200, json={"id": PROD_M, "is_archived": False, "prices": [
            {"amount_type": "fixed", "price_amount": 299, "price_currency": "usd", "recurring_interval": "year"}]})
    settings = Settings.from_env({}, **POLAR_ENV)
    b = B.Billing(settings, LocalStore(str(tmp_path / "kv")),
                  B.PolarClient.from_settings(settings, transport=httpx.MockTransport(legacy)))
    assert b._price("pro_month")["interval"] == "year"


def test_price_interval_falls_back_to_plan_default(tmp_path):
    def bare(request):  # không có recurring_interval ở cả sản phẩm lẫn giá
        return httpx.Response(200, json={"id": "x", "is_archived": False, "prices": [
            {"amount_type": "fixed", "price_amount": 199, "price_currency": "usd"}]})
    settings = Settings.from_env({}, **POLAR_ENV)
    b = B.Billing(settings, LocalStore(str(tmp_path / "kv")),
                  B.PolarClient.from_settings(settings, transport=httpx.MockTransport(bare)))
    assert [b._price(p)["interval"] for p in ("pro_week", "pro_month", "pro_year")] == ["week", "month", "year"]


def test_checkout_422_email_is_bad_email_not_outage(make_bclient, fake):
    c = make_bclient()
    fake.checkout_fail = 422
    r = c.post("/api/billing/checkout", json={"plan": "pro_month", "email": "an@gmail.con"})
    assert r.status_code == 400 and err(r) == "bad_email"
    fake.checkout_fail = 500
    r = c.post("/api/billing/checkout", json={"plan": "pro_month", "email": "an@gmail.com"})
    assert r.status_code == 503 and err(r) == "billing_unavailable"


def test_event_log_and_month_index_atomic_across_containers(tmp_path):
    import threading as _th

    from test_review_fixes import _FakeDict, _FakeVolume
    from dattts.store import ModalStore

    d = _FakeDict()
    settings = Settings.from_env({}, **POLAR_ENV)
    bills = [B.Billing(settings, ModalStore(mount=str(tmp_path), dict_obj=d, volume_obj=_FakeVolume(tmp_path, {})),
                       client=object()) for _ in range(2)]

    def work(b, n):
        for i in range(15):
            b._process_event(f"wh-{n}-{i}", json.dumps({"type": "order.paid", "data": {}}).encode())
            b._track_usage_key("2026-11", {"license_key_id": f"lk-{n}-{i}", "plan": "pro_month"})

    ts = [_th.Thread(target=work, args=(b, n)) for n, b in enumerate(bills)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(d.d["billing_events"]) == 30
    assert len(d.d["usage_keys:2026-11"]) == 30
    assert not any(k.startswith("lock:") for k in d.d)


# ───────────────────────────── Phần 3: kỳ tuần ISO (giờ VN) ─────────────────────────────

from datetime import datetime as _dt  # noqa: E402

_VN = B.VN_TZ


def _vn(*a) -> float:
    return _dt(*a, tzinfo=_VN).timestamp()


def test_week_id_and_reset_iso_weeks_in_vn_time():
    sun = _vn(2026, 11, 15, 23, 59)   # Chủ nhật 23:59 giờ VN (= 16:59 UTC)
    mon = _vn(2026, 11, 16, 0, 0)     # Thứ Hai 00:00 giờ VN (= 17:00 UTC Chủ nhật)
    assert B.week_id(sun) == "2026-W46" and B.week_id(mon) == "2026-W47"
    assert B.week_reset_at(sun).isoformat() == "2026-11-16T00:00:00+07:00"
    assert B.week_reset_at(mon).isoformat() == "2026-11-23T00:00:00+07:00"
    # Theo giờ VN, không theo UTC: 16:59 UTC Chủ nhật vẫn là tuần cũ, 17:00 UTC đã là tuần mới
    from datetime import timezone as _tz
    assert _dt.fromtimestamp(mon, _tz.utc).weekday() == 6 and _dt.fromtimestamp(mon, _tz.utc).hour == 17
    # Năm ISO khác năm dương lịch: 2026 có 53 tuần; 01/01/2027 (thứ Sáu) vẫn thuộc 2026-W53
    assert B.week_id(_vn(2026, 12, 31, 12)) == "2026-W53"
    assert B.week_id(_vn(2027, 1, 3, 23, 59)) == "2026-W53"
    assert B.week_id(_vn(2027, 1, 4, 0, 0)) == "2027-W01"
    assert B.week_id(_vn(2026, 1, 5)) == "2026-W02" and B.week_id(_vn(2026, 3, 2, 8)) == "2026-W10"
    assert B.period_id("week", sun) == "2026-W46" and B.period_id("month", sun) == "2026-11"
    assert B.period_reset_at("month", sun).isoformat() == "2026-12-01T00:00:00+07:00"


def test_week_bucket_rollover_sunday_to_monday_vn(tmp_path, fake):
    settings = Settings.from_env({}, **POLAR_ENV, plan_pro_week_chars=100)
    b = B.Billing(settings, LocalStore(str(tmp_path / "kv")),
                  B.PolarClient.from_settings(settings, transport=httpx.MockTransport(fake)))
    who = {"kind": "polar", "license_key_id": "lk-wk", "plan": "pro_week"}
    sun, mon = _vn(2026, 11, 15, 23, 59), _vn(2026, 11, 16, 0, 0)
    assert b.charge(who, 80, now=sun) == "usage:lk-wk:2026-W46"
    with pytest.raises(B.BillingError) as e:
        b.charge(who, 30, now=sun)
    assert e.value.code == "quota_exceeded" and e.value.status == 429
    assert e.value.key == "quota_exceeded.paid_partial"
    assert "this week" in e.value.message and "Mon, Nov 16, 2026 at 00:00 (UTC+7)" in e.value.message
    vi = e.value.localized("vi")
    assert "tuần này" in vi and "00:00 ngày 16/11/2026" in vi and "Pro tuần" in vi
    u = b.usage(who, now=sun)
    assert u == {"used": 80, "limit": 100, "remaining": 20, "reset_at": "2026-11-16T00:00:00+07:00",
                 "plan": "pro_week", "period": "week"}
    # Một phút sau (thứ Hai 00:00 giờ VN) → kỳ mới, hạn mức đầy lại
    u = b.usage(who, now=mon)
    assert u["used"] == 0 and u["remaining"] == 100 and u["reset_at"] == "2026-11-23T00:00:00+07:00"
    assert b.charge(who, 30, now=mon) == "usage:lk-wk:2026-W47"
    assert b.store.kv_get("usage:lk-wk:2026-W46") == 80 and b.store.kv_get("usage:lk-wk:2026-W47") == 30
    assert b.store.kv_get("usage_keys:2026-W47") == {"lk-wk": "pro_week"}
    # Gói tháng/năm không đổi kỳ ở ranh giới tuần
    for plan in ("pro_month", "pro_year"):
        w = {"kind": "polar", "license_key_id": f"lk-{plan}", "plan": plan}
        assert b.charge(w, 1, now=sun) == b.charge(w, 1, now=mon) == f"usage:lk-{plan}:2026-11"
        assert b.usage(w, now=mon)["period"] == "month"


def test_pro_week_api_quota_and_me(make_bclient, fake, monkeypatch):
    c = make_bclient(plan_pro_week_chars=40)
    fake.add_key("DATTTS-WEEK", benefit=BEN_W, lkid="lk-week")
    h = {"X-API-Key": "DATTTS-WEEK"}
    sun = _dt.fromtimestamp(_vn(2026, 11, 15, 23, 59), _VN)
    monkeypatch.setattr(B, "_now_vn", lambda now=None: sun)
    r = c.post("/api/tts", json={"text": "Một hai ba bốn năm sáu bảy."}, headers=h)  # 27 ký tự
    assert r.status_code == 200, r.text
    q = r.json()["quota"]
    assert q == {"used": 27, "limit": 40, "remaining": 13, "reset_at": "2026-11-16T00:00:00+07:00",
                 "period": "week", "plan": "pro_week"}
    assert c.store.kv_get("usage:lk-week:2026-W46") == 27
    me = c.get("/api/me", headers=h).json()
    assert me["plan"] == "pro_week" and me["period"] == "week" and me["plan_name"] == "Pro Weekly"
    r = c.post("/api/tts", json={"text": "Tám chín mười mười một mười hai."}, headers=h)
    assert r.status_code == 429 and err(r) == "quota_exceeded"
    mon = _dt.fromtimestamp(_vn(2026, 11, 16, 0, 0), _VN)
    monkeypatch.setattr(B, "_now_vn", lambda now=None: mon)
    r = c.post("/api/tts", json={"text": "Tám chín mười mười một mười hai."}, headers=h)
    assert r.status_code == 200 and r.json()["quota"]["reset_at"] == "2026-11-23T00:00:00+07:00"
    assert c.get("/api/quota", headers=h).json()["used"] == r.json()["quota"]["used"] > 0


def test_quota_period_free_and_internal(make_bclient):
    c = make_bclient(api_keys=frozenset({"noi-bo"}))
    assert c.get("/api/quota").json()["period"] == "day"
    assert c.get("/api/quota", headers={"X-API-Key": "noi-bo"}).json()["period"] == "day"


def test_checkout_status_plan_from_product_when_metadata_missing(make_bclient, fake):
    c = make_bclient()
    orig = fake.__call__

    def no_meta(request):
        r = orig(request)
        if request.method == "GET" and request.url.path.startswith("/v1/checkouts/") and r.status_code == 200:
            return httpx.Response(200, json={**r.json(), "metadata": {}, "product_id": PROD_W})
        return r

    c2 = make_bclient()
    c2.app.state.billing.client = B.PolarClient.from_settings(c2.settings, transport=httpx.MockTransport(no_meta))
    assert c2.get(f"/api/billing/checkout/{CHECKOUT_ID}").json()["plan"] == "pro_week"
    assert c.get(f"/api/billing/checkout/{CHECKOUT_ID}").json()["plan"] == "pro_month"


def test_billing_errors_bilingual(make_bclient, fake):
    c = make_bclient()
    r = c.post("/api/billing/checkout", json={"plan": "vip"})
    assert r.json()["error"]["message"].startswith("Invalid plan")
    r = c.post("/api/billing/checkout", json={"plan": "vip"}, headers={"Accept-Language": "vi"})
    assert r.json()["error"] == {"code": "bad_plan", "message": "Gói không hợp lệ. Hãy chọn gói Pro tuần, "
                                 "Pro tháng hoặc Pro năm."}
    r = c.get("/api/me", headers={"X-API-Key": "DATTTS-SAI", "Accept-Language": "vi-VN"})
    assert r.status_code == 401 and r.json()["error"]["message"].startswith("API key không hợp lệ")
    r = c.get("/api/me", headers={"X-API-Key": "DATTTS-SAI2", "Accept-Language": "en-GB"})
    assert r.json()["error"]["message"].startswith("Invalid API key")
    d = make_bclient(polar_access_token=None)
    r = d.post("/api/billing/checkout", json={"plan": "pro_month"}, headers={"Accept-Language": "fr-FR"})
    assert r.status_code == 503 and r.json()["error"]["message"].startswith("Pro is coming soon. Join the waitlist")


def test_cached_key_with_removed_plan_is_revalidated(ctx, fake):
    settings, store, client = ctx
    fake.add_key("DATTTS-OLD", benefit=BEN_Y, lkid="lk-old")
    h = hashlib.sha256(b"DATTTS-OLD").hexdigest()
    t0 = 1_800_000_000.0
    store.kv_set(f"lk:{h}", {"ok": True, "at": t0, "exp": t0 + 600,
                             "info": {"kind": "polar", "license_key_id": "lk-old", "customer_id": CUST,
                                      "email": None, "plan": "personal"}})
    assert B.resolve_api_key("DATTTS-OLD", settings, store, client, now=t0 + 1)["plan"] == "pro_year"
    assert len(fake.calls("/v1/license-keys/validate")) == 1


# ───────────────────────────── vòng review 3 ─────────────────────────────


def test_archived_or_unpriced_product_is_not_available(make_bclient, fake, caplog):
    fake.archived.add(PROD_Y)
    fake.custom_price.add(PROD_W)
    c = make_bclient()
    with caplog.at_level("ERROR", logger="dattts.billing"):
        plans = {p["id"]: p for p in c.get("/api/billing/plans").json()["plans"]}
    assert "archived" in caplog.text
    assert plans["pro_year"]["available"] is False and plans["pro_year"]["price"] is None
    assert plans["pro_week"]["available"] is False and plans["pro_week"]["price"] is None
    assert plans["pro_month"]["available"] is True and plans["pro_month"]["price"] == 499
    # Không gửi sản phẩm đã lưu trữ sang Polar (chắc chắn bị từ chối) — báo "chưa bán" thay vì 503 lỗi Polar
    r = c.post("/api/billing/checkout", json={"plan": "pro_year"})
    assert r.status_code == 503 and err(r) == "billing_disabled"
    assert fake.calls("/v1/checkouts/") == []
    assert c.post("/api/billing/checkout", json={"plan": "pro_month"}).status_code == 200


def test_transient_price_error_keeps_plan_buyable(make_bclient, fake):
    fake.fail = 500
    c = make_bclient()
    plans = {p["id"]: p for p in c.get("/api/billing/plans").json()["plans"]}
    assert all(plans[p]["available"] is True and plans[p]["price"] is None for p in ("pro_week", "pro_month", "pro_year"))
    fake.fail = None
    assert c.post("/api/billing/checkout", json={"plan": "pro_year"}).status_code == 200


def test_missing_webhook_secret_is_warned_but_billing_stays_on(make_bclient, caplog):
    with caplog.at_level("ERROR", logger="dattts.billing"):
        c = make_bclient(polar_webhook_secret=None)
    assert "POLAR_WEBHOOK_SECRET" in caplog.text and "webhook" in caplog.text
    assert c.settings.billing_enabled and c.settings.billing_missing() == ["POLAR_WEBHOOK_SECRET"]
    assert c.get("/api/billing/plans").json()["enabled"] is True
    body = _event("subscription.revoked")
    assert c.post("/api/billing/webhook", content=body, headers=sign(body, NEW_SECRET)).status_code == 503
    # Đủ biến → không cảnh báo gì
    assert Settings.from_env({}, **POLAR_ENV).billing_missing() == []


@pytest.mark.parametrize("etype", ["benefit_grant.updated", "order.refunded", "refund.created", "refund.updated"])
def test_rotation_and_refund_events_invalidate_key_cache(make_bclient, fake, etype):
    c = make_bclient()
    fake.add_key("DATTTS-ROT")
    assert c.get("/api/me", headers={"X-API-Key": "DATTTS-ROT"}).status_code == 200
    del fake.keys["DATTTS-ROT"]  # Polar: key đã đổi / đã thu hồi sau hoàn tiền
    assert c.get("/api/me", headers={"X-API-Key": "DATTTS-ROT"}).status_code == 200  # còn trong cache
    body = _event(etype)
    r = c.post("/api/billing/webhook", content=body, headers=sign(body, NEW_SECRET, "rot-" + etype))
    assert r.status_code == 202 or r.status_code == 200
    r = c.get("/api/me", headers={"X-API-Key": "DATTTS-ROT"})
    assert r.status_code == 401 and err(r) == "invalid_api_key"


def test_rate_limit_cannot_keep_a_rotated_key_alive(tmp_path, fake):
    """Người cầm key lộ tự kích hoạt giới hạn tần suất (gửi key rác) → cache cũ chỉ còn dùng ≤ 15 phút."""
    settings = Settings.from_env({}, **POLAR_ENV)
    store = LocalStore(str(tmp_path / "kv"))
    client = B.PolarClient.from_settings(settings, transport=httpx.MockTransport(fake))
    fake.add_key("DATTTS-LEAK")
    t0 = 1_800_000_000.0
    B.resolve_api_key("DATTTS-LEAK", settings, store, client, now=t0)
    del fake.keys["DATTTS-LEAK"]  # khách đã đổi key trong cổng Polar

    def deny():
        raise B.BillingError("rate_limited", status=429)

    assert B.resolve_api_key("DATTTS-LEAK", settings, store, client, now=t0 + 14 * 60, on_miss=deny)["plan"]
    for dt in (16 * 60, 3600, 80_000):
        with pytest.raises(B.BillingError) as e:
            B.resolve_api_key("DATTTS-LEAK", settings, store, client, now=t0 + dt, on_miss=deny)
        assert e.value.status == 429
    with pytest.raises(B.BillingError) as e:  # không bị chặn → hỏi Polar → key cũ bị từ chối
        B.resolve_api_key("DATTTS-LEAK", settings, store, client, now=t0 + 16 * 60)
    assert e.value.status == 401


def _billing(tmp_path, fake):
    settings = Settings.from_env({}, **POLAR_ENV)
    store = LocalStore(str(tmp_path / "kv"))
    client = B.PolarClient.from_settings(settings, transport=httpx.MockTransport(fake))
    return B.Billing(settings, store, client), store


def test_usage_ledger_survives_kv_expiry_and_spans_periods(tmp_path, fake):
    b, store = _billing(tmp_path, fake)
    who = {"kind": "polar", "license_key_id": "lk-9", "customer_id": CUST, "plan": "pro_month"}
    oct25 = _vn(2026, 10, 25, 10, 0)
    nov2 = _vn(2026, 11, 2, 10, 0)
    b.charge(who, 900_000, now=oct25)
    b.charge(who, 1_000, now=nov2)
    # modal.Dict xoá mục sau 7 ngày không dùng: mất bucket tháng 10 và cả sổ cái trong KV
    for k in ("usage:lk-9:2026-10", "ledger:lk-9", f"ledger_cust:{CUST}"):
        store.kv_delete(k)
    led = b.ledger("lk-9")  # khôi phục từ blob
    assert led["total"] == 901_000 and led["first_at"].startswith("2026-10-25")
    assert led["days"] == {"2026-10-25": 900_000, "2026-11-02": 1_000}
    assert led["periods"] == {"2026-10": 900_000, "2026-11": 1_000}
    assert "email" not in led and led["customer_id"] == CUST
    # Tra cứu theo khách, tính từ ngày thanh toán (vắt qua hai tháng)
    j = b.admin_usage(customer_id=CUST, since="2026-10-25")
    assert [k["used_since"] for k in j["keys"]] == [901_000]
    assert j["keys"][0]["percent_of_period"] == 90.1 and j["keys"][0]["period_limit"] == 1_000_000
    assert b.admin_usage(license_key_id="lk-9", since="2026-11-01")["keys"][0]["used_since"] == 1_000
    with pytest.raises(B.BillingError):
        b.admin_usage(license_key_id="lk-9", since="25/10/2026")
    with pytest.raises(B.BillingError):
        b.admin_usage()


def test_period_quota_not_reset_when_bucket_expires_mid_period(tmp_path, fake):
    b, store = _billing(tmp_path, fake)
    who = {"kind": "polar", "license_key_id": "lk-q", "customer_id": CUST, "plan": "pro_month"}
    b.charge(who, 999_000, now=_vn(2026, 11, 2, 10, 0))
    store.kv_delete("usage:lk-q:2026-11")  # hết hạn sau 7 ngày không dùng
    later = _vn(2026, 11, 20, 10, 0)
    assert b.usage(who, now=later)["used"] == 999_000
    with pytest.raises(B.BillingError) as e:
        b.charge(who, 5_000, now=later)
    assert e.value.status == 429
    b.charge(who, 1_000, now=later)
    assert b.usage(who, now=later)["remaining"] == 0


def test_failed_job_refund_updates_ledger(tmp_path, fake):
    b, store = _billing(tmp_path, fake)
    who = {"kind": "polar", "license_key_id": "lk-r", "customer_id": CUST, "plan": "pro_week"}
    t = _vn(2026, 11, 4, 9, 0)
    uk = b.charge(who, 5_000, now=t)
    b.refund(uk, 5_000, now=t)
    led = b.ledger("lk-r")
    assert led["total"] == 0 and sum(led["days"].values()) == 0 and store.kv_get(uk) == 0
    # run_job (máy chủ dựng âm thanh) hoàn quota qua cùng đường
    from dattts.store import run_job
    from dattts.render import RenderError, RenderRequest
    uk = b.charge(who, 700, now=t)
    store.kv_set("job:j1", {"job_id": "j1", "status": "queued", "quota_key": uk, "charged": 700})

    def boom(req, progress):
        raise RenderError("engine_error", status=500)

    run_job(store, "j1", RenderRequest(text="x", voice="truc-ly"), boom)
    assert b.ledger("lk-r")["total"] == 0 and store.kv_get(uk) == 0


def test_admin_usage_endpoint(make_bclient, fake):
    c = make_bclient(admin_token="adm-123")
    fake.add_key("DATTTS-U1", benefit=BEN_M, lkid="u1")
    assert c.post("/api/tts", json={"text": "Một hai ba."}, headers={"X-API-Key": "DATTTS-U1"}).status_code == 200
    h = {"Authorization": "Bearer adm-123"}
    assert c.get("/api/admin/usage?license_key_id=u1").status_code == 401
    r = c.get("/api/admin/usage?license_key_id=u1", headers=h)
    assert r.status_code == 200
    k = r.json()["keys"][0]
    assert k["license_key_id"] == "u1" and k["used_since"] == k["total"] > 0 and k["plan"] == "pro_month"
    r = c.get(f"/api/admin/usage?customer_id={CUST}", headers=h)
    assert [x["license_key_id"] for x in r.json()["keys"]] == ["u1"]
    assert c.get("/api/admin/usage", headers=h).status_code == 400
    assert c.get("/api/admin/usage?license_key_id=u1&since=bad", headers=h).status_code == 400
    assert c.get("/api/admin/usage?license_key_id=nope", headers=h).json()["keys"] == []


def test_vi_voice_description_names_the_region():
    from dattts.engine import FakeEngine as FE
    desc = {v["name"]: v["description"] for v in FE().voices()}
    assert desc["Thùy Dung"] == "Nữ · miền Nam · Phong cách tin tức"
    assert desc["Quang Sơn"] == "Nam · miền Trung · Phong cách tự nhiên"
