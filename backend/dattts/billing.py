"""Thanh toán qua Polar (polar.sh, Merchant of Record) — SPEC Phần 2.

- ``PolarClient``: gọi Polar API bằng httpx (timeout 10 s, ``Authorization: Bearer <token>``);
  tiêm được ``transport`` (ví dụ ``httpx.MockTransport``) để test không cần mạng.
- ``verify_webhook``: chữ ký Standard Webhooks, chấp nhận cả bí mật kiểu mới lẫn kiểu cũ của Polar.
- ``resolve_api_key``: API key của khách = License Key Polar. Kết quả xác thực được cache trong KV
  theo ``sha256(key)`` — KHÔNG bao giờ lưu hay ghi log key gốc.
- ``Billing``: gom cấu hình + store + client cho API (bảng giá, checkout, quota tháng, webhook).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

import httpx

log = logging.getLogger("dattts.billing")

__all__ = [
    "BillingError",
    "PolarError",
    "PolarClient",
    "Billing",
    "verify_webhook",
    "resolve_api_key",
    "key_hash",
    "mask_email",
    "month_id",
    "month_reset_at",
    "PAID_PLANS",
    "PLAN_NAMES",
    "CHECKOUT_ID_RE",
    "LICENSE_KEY_RE",
]

VN_TZ = timezone(timedelta(hours=7), "Asia/Ho_Chi_Minh")

PAID_PLANS = ("personal", "unlimited")
PLAN_NAMES = {
    "free": "Dùng thử",
    "personal": "Cá nhân",
    "unlimited": "Không giới hạn",
    "internal": "Nội bộ",
}
# License key Polar của DatTTS: tiền tố "DATTTS-" + chữ/số/gạch nối. Sai dạng → 401 ngay, không gọi Polar.
LICENSE_KEY_RE = re.compile(r"^DATTTS-[A-Za-z0-9-]{1,120}$", re.IGNORECASE)
CHECKOUT_ID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

# Cache kết quả xác thực license key (giây)
LK_POSITIVE_TTL = 600
LK_NEGATIVE_TTL = 60
LK_STALE_MAX = 24 * 3600
PLANS_TTL = 600
PLANS_ERROR_TTL = 60

EVENTS_KEY = "billing_events"
EVENTS_MAX = 500
# Sự kiện làm thay đổi quyền dùng key → xoá cache ``lk:*`` của khách đó
INVALIDATING_EVENTS = frozenset({
    "subscription.revoked",
    "subscription.updated",
    "benefit_grant.revoked",
    "customer.state_changed",
})

MSG_UNAVAILABLE = "Hệ thống thanh toán đang tạm gián đoạn. Bạn thử lại sau ít phút nhé."
MSG_DISABLED = "Gói trả phí sắp mở bán. Bạn để lại email ở bảng giá để được báo sớm nhé."
MSG_KEY_RATE = "Bạn thử quá nhiều API key trong thời gian ngắn. Bạn đợi một phút rồi thử lại nhé."
MSG_BAD_EMAIL = ("Hệ thống thanh toán không nhận email này (có thể tên miền gõ nhầm). Bạn kiểm tra lại email "
                 "hoặc để trống — Polar sẽ hỏi email ở trang thanh toán.")
MSG_INVALID_KEY = ("API key không hợp lệ hoặc gói đã hết hạn. Bạn kiểm tra lại key trong email của Polar "
                   "hoặc trong cổng quản lý gói nhé.")


class BillingError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class PolarError(Exception):
    """Polar lỗi mạng (``status`` None), 5xx hoặc phản hồi 4xx không mong đợi."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


# ───────────────────────────── Tiện ích ─────────────────────────────


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def mask_email(email: str | None) -> str | None:
    """``an.nguyen@gmail.com`` → ``a***@gmail.com``."""
    if not email or "@" not in email:
        return None
    local, _, domain = email.strip().rpartition("@")
    if not local or not domain:
        return None
    return f"{local[0]}***@{domain}"


def _now_vn(now: float | None = None) -> datetime:
    return datetime.fromtimestamp(time.time() if now is None else now, VN_TZ)


def month_id(now: float | None = None) -> str:
    return f"{_now_vn(now):%Y-%m}"


def month_reset_at(now: float | None = None) -> datetime:
    d = _now_vn(now)
    y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    return datetime(y, m, 1, tzinfo=VN_TZ)


def _fmt_n(n: int) -> str:
    return f"{int(n):,}".replace(",", ".")


def _header(headers: Any, name: str) -> str:
    if headers is None:
        return ""
    v = headers.get(name)
    if v is None and isinstance(headers, Mapping):
        low = name.lower()
        for k, val in headers.items():
            if str(k).lower() == low:
                v = val
                break
    return str(v or "").strip()


# ───────────────────────────── Webhook ─────────────────────────────


def _webhook_keys(secret: str) -> list[bytes]:
    keys: list[bytes] = []
    s = (secret or "").strip()
    if not s:
        return keys
    raw = s[len("whsec_"):] if s.startswith("whsec_") else s
    try:
        k = base64.b64decode(raw, validate=True)
        if k:
            keys.append(k)  # (a) bí mật kiểu mới: Standard Webhooks
    except (binascii.Error, ValueError):
        pass
    keys.append(s.encode("utf-8"))  # (b) bí mật Polar kiểu cũ: utf-8 của cả chuỗi "whsec_..."
    return keys


def verify_webhook(body: bytes, headers, secret: str, tolerance: int = 300, now: float | None = None) -> bool:
    """Xác minh chữ ký Standard Webhooks (``webhook-id``/``webhook-timestamp``/``webhook-signature``)."""
    if not secret:
        return False
    msg_id = _header(headers, "webhook-id")
    ts = _header(headers, "webhook-timestamp")
    sig_header = _header(headers, "webhook-signature")
    if not msg_id or not ts or not sig_header:
        return False
    try:
        ts_int = int(ts)
    except ValueError:
        return False
    cur = time.time() if now is None else now
    if abs(cur - ts_int) > tolerance:
        return False
    if isinstance(body, str):
        body = body.encode("utf-8")
    signed = msg_id.encode() + b"." + ts.encode() + b"." + (body or b"")
    wanted: list[bytes] = []
    for part in sig_header.split():
        version, _, sig = part.partition(",")
        if version != "v1" or not sig:
            continue
        try:
            wanted.append(base64.b64decode(sig, validate=True))
        except (binascii.Error, ValueError):
            continue
    if not wanted:
        return False
    ok = False
    for key in _webhook_keys(secret):
        expected = hmac.new(key, signed, hashlib.sha256).digest()
        for w in wanted:  # so sánh hằng thời gian, không dừng sớm
            ok |= hmac.compare_digest(expected, w)
    return ok


# ───────────────────────────── Polar API ─────────────────────────────


class PolarClient:
    def __init__(self, token: str, base_url: str = "https://sandbox-api.polar.sh",
                 organization_id: str | None = None, transport: httpx.BaseTransport | None = None,
                 timeout: float = 10.0):
        self.organization_id = organization_id
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            transport=transport,
            timeout=timeout,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json",
                     "User-Agent": "dattts-backend"},
        )

    @classmethod
    def from_settings(cls, settings, transport: httpx.BaseTransport | None = None) -> "PolarClient":
        return cls(settings.polar_access_token or "", settings.polar_base_url,
                   organization_id=settings.polar_organization_id, transport=transport)

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, json_body: dict | None = None,
                 ok_statuses: tuple[int, ...] = (200, 201)) -> httpx.Response:
        try:
            r = self._http.request(method, path, json=json_body)
        except httpx.HTTPError as e:
            raise PolarError(f"lỗi mạng khi gọi Polar {method} {path.split('?')[0]}: {type(e).__name__}") from None
        if r.status_code in ok_statuses:
            return r
        raise PolarError(f"Polar trả {r.status_code} cho {method} {path}", r.status_code)

    @staticmethod
    def _json(r: httpx.Response) -> dict:
        try:
            data = r.json()
        except ValueError:
            raise PolarError("Polar trả dữ liệu không phải JSON", r.status_code) from None
        if not isinstance(data, dict):
            raise PolarError("Polar trả dữ liệu không đúng dạng", r.status_code)
        return data

    def create_checkout(self, product_id: str, success_url: str, return_url: str, metadata: dict,
                        customer_email: str | None = None) -> dict:
        body: dict[str, Any] = {"products": [product_id], "success_url": success_url,
                                "return_url": return_url, "metadata": dict(metadata or {})}
        if customer_email:
            body["customer_email"] = customer_email
        return self._json(self._request("POST", "/v1/checkouts/", body))

    def get_checkout(self, checkout_id: str) -> dict:
        return self._json(self._request("GET", f"/v1/checkouts/{checkout_id}"))

    def get_product(self, product_id: str) -> dict:
        return self._json(self._request("GET", f"/v1/products/{product_id}"))

    def validate_license_key(self, key: str) -> dict | None:
        # organization_id là trường BẮT BUỘC của Polar (LicenseKeyValidate); luôn gửi.
        body: dict[str, Any] = {"key": key, "organization_id": self.organization_id or ""}
        try:
            r = self._request("POST", "/v1/license-keys/validate", body)
        except PolarError as e:
            if e.status == 404:  # thu hồi/hết hạn/không tồn tại
                return None
            if e.status == 422:  # body sai (thường là POLAR_ORGANIZATION_ID sai) — lỗi cấu hình, không phải key sai
                log.error("Polar từ chối body validate license key (422): kiểm tra POLAR_ORGANIZATION_ID")
            raise
        return self._json(r)

    def create_customer_session(self, customer_id: str, return_url: str) -> str:
        data = self._json(self._request("POST", "/v1/customer-sessions/",
                                        {"customer_id": customer_id, "return_url": return_url}))
        url = data.get("customer_portal_url")
        if not url:
            raise PolarError("Polar không trả customer_portal_url")
        return str(url)


# ───────────────────────────── License key ─────────────────────────────


def _benefit_plans(settings) -> dict[str, str]:
    out = {}
    if settings.polar_benefit_personal:
        out[settings.polar_benefit_personal] = "personal"
    if settings.polar_benefit_unlimited:
        out[settings.polar_benefit_unlimited] = "unlimited"
    return out


def _is_internal(key: str, settings) -> bool:
    ok = False
    for valid in settings.api_keys:  # so sánh hằng thời gian, không dừng sớm
        ok |= hmac.compare_digest(key.encode(), valid.encode())
    return ok


def _info_from_validated(v: dict, settings) -> dict | None:
    if not isinstance(v, dict) or v.get("status", "granted") != "granted":
        return None
    plan = _benefit_plans(settings).get(str(v.get("benefit_id") or ""))
    if plan is None:  # benefit lạ (sản phẩm khác của tổ chức) → không hợp lệ
        return None
    exp = v.get("expires_at")
    if exp:
        try:
            if datetime.fromisoformat(str(exp).replace("Z", "+00:00")).timestamp() < time.time():
                return None
        except ValueError:
            pass
    customer = v.get("customer") or {}
    lkid = v.get("id")
    if not lkid:
        return None
    return {
        "kind": "polar",
        "license_key_id": str(lkid),
        "customer_id": str(v.get("customer_id") or customer.get("id") or ""),
        "email": customer.get("email"),
        "plan": plan,
    }


def _kv_update(store, key: str, fn: Callable[[Any], Any]) -> Any:
    """Đọc–sửa–ghi nguyên tử nếu store hỗ trợ (``kv_update`` khoá cả giữa các container)."""
    upd = getattr(store, "kv_update", None)
    if upd is not None:
        return upd(key, fn)
    new = fn(store.kv_get(key))
    store.kv_set(key, new)
    return new


def _index_customer(store, customer_id: str, h: str) -> None:
    if not customer_id:
        return
    ik = f"lkc:{customer_id}"
    if h in (store.kv_get(ik) or []):
        return

    def add(cur):
        cur = list(cur or [])
        if h not in cur:
            cur.append(h)
        return cur[-50:]

    _kv_update(store, ik, add)


def _kv_delete(store, key: str) -> None:
    fn = getattr(store, "kv_delete", None)
    if fn is not None:
        fn(key)
    else:
        store.kv_set(key, None)


def resolve_api_key(key: str, settings, store, client: PolarClient | None, now: float | None = None,
                    on_miss: Callable[[], None] | None = None) -> dict:
    """Xác định khách từ ``X-API-Key``.

    → ``{"kind":"internal"}`` (key trong ``API_KEYS``) hoặc
      ``{"kind":"polar","license_key_id","customer_id","email","plan"}``.
    Key sai → ``BillingError(invalid_api_key, 401)``; Polar lỗi và không có cache → 503 ``billing_unavailable``.
    ``on_miss``: gọi trước mỗi lần phải hỏi Polar (cache trượt) — API dùng để giới hạn theo IP; nó ném
    ``BillingError`` để chặn (khi đó vẫn dùng cache cũ ≤ 24 giờ nếu có).
    """
    key = (key or "").strip()
    if not key:
        raise BillingError("invalid_api_key", MSG_INVALID_KEY, 401)
    if _is_internal(key, settings):
        return {"kind": "internal"}
    if not settings.billing_enabled or client is None or not LICENSE_KEY_RE.fullmatch(key):
        # Không đúng dạng key Polar của DatTTS → từ chối ngay: không gọi Polar, không ghi cache.
        raise BillingError("invalid_api_key", MSG_INVALID_KEY, 401)
    t = time.time() if now is None else now
    h = key_hash(key)
    ck = f"lk:{h}"
    cached = store.kv_get(ck)
    if isinstance(cached, dict) and t < float(cached.get("exp") or 0):
        return _cached_result(cached)
    stale_ok = isinstance(cached, dict) and t - float(cached.get("at") or 0) <= LK_STALE_MAX
    if on_miss is not None:
        try:
            on_miss()
        except BillingError:
            if stale_ok:
                return _cached_result(cached)
            raise
    try:
        v = client.validate_license_key(key)
    except PolarError as e:
        log.warning("không xác thực được license key %s…: %s", h[:12], e)
        if stale_ok:
            return _cached_result(cached)
        raise BillingError("billing_unavailable", MSG_UNAVAILABLE, 503) from None
    info = _info_from_validated(v, settings) if v is not None else None
    if info is None:
        store.kv_set(ck, {"ok": False, "at": t, "exp": t + LK_NEGATIVE_TTL})
        raise BillingError("invalid_api_key", MSG_INVALID_KEY, 401)
    store.kv_set(ck, {"ok": True, "info": info, "at": t, "exp": t + LK_POSITIVE_TTL})
    try:
        _index_customer(store, info["customer_id"], h)
    except Exception:  # pragma: no cover
        log.exception("không ghi được chỉ mục lkc")
    return dict(info)


def _cached_result(cached: dict) -> dict:
    if cached.get("ok") and isinstance(cached.get("info"), dict):
        return dict(cached["info"])
    raise BillingError("invalid_api_key", MSG_INVALID_KEY, 401)


def invalidate_customer(store, customer_id: str) -> int:
    """Xoá cache ``lk:*`` của một khách (theo chỉ mục ``lkc:<customer_id>``). Trả số mục đã xoá."""
    if not customer_id:
        return 0
    ik = f"lkc:{customer_id}"
    hashes = list(store.kv_get(ik) or [])
    for h in hashes:
        _kv_delete(store, f"lk:{h}")
    _kv_delete(store, ik)
    return len(hashes)


# ───────────────────────────── Billing ─────────────────────────────


class Billing:
    """Gom logic thanh toán cho API. Không có trạng thái toàn cục (trừ cache bảng giá trong tiến trình)."""

    def __init__(self, settings, store, client: PolarClient | None = None):
        self.settings = settings
        self.store = store
        if client is None and settings.billing_enabled:
            client = PolarClient.from_settings(settings)
        self.client = client
        self._plans_cache: dict[str, Any] = {}
        self._lock = threading.Lock()
        missing = settings.billing_missing()
        if missing:
            log.error("Cấu hình Polar thiếu %s — %s", ", ".join(missing),
                      "các gói thiếu sản phẩm/benefit sẽ không bán" if settings.billing_enabled
                      else "billing TẮT (không bán gói nào) cho tới khi đặt đủ biến")

    @property
    def enabled(self) -> bool:
        return bool(self.settings.billing_enabled and self.client is not None)

    def require_enabled(self) -> None:
        if not self.enabled:
            raise BillingError("billing_disabled", MSG_DISABLED, 503)

    # ---------- gói ----------
    def monthly_limit(self, plan: str) -> int:
        if plan == "unlimited":
            return int(self.settings.plan_unlimited_monthly_chars)
        return int(self.settings.plan_personal_monthly_chars)

    def product_id(self, plan: str) -> str | None:
        return {"personal": self.settings.polar_product_personal,
                "unlimited": self.settings.polar_product_unlimited}.get(plan)

    def _price(self, product_id: str | None) -> dict:
        empty = {"price": None, "currency": "usd", "interval": "month"}
        if not product_id or not self.enabled:
            return empty
        p = self.client.get_product(product_id)
        if p.get("is_archived"):
            return empty
        for pr in p.get("prices") or []:
            if pr.get("is_archived") or pr.get("amount_type") != "fixed":
                continue
            # Polar hiện đặt chu kỳ ở cấp SẢN PHẨM (Product.recurring_interval); trường cùng tên trên
            # giá chỉ còn ở schema Legacy* → dùng làm dự phòng.
            return {"price": int(pr.get("price_amount") or 0),
                    "currency": str(pr.get("price_currency") or "usd").lower(),
                    "interval": p.get("recurring_interval") or pr.get("recurring_interval") or None,
                    "interval_count": int(p.get("recurring_interval_count") or 1)}
        return empty

    def plans(self, now: float | None = None) -> dict:
        t = time.time() if now is None else now
        with self._lock:
            c = self._plans_cache
            if c and t < c["exp"]:
                return json.loads(json.dumps(c["data"]))
        failed = False
        paid = []
        for plan in PAID_PLANS:
            try:
                price = self._price(self.product_id(plan))
            except PolarError as e:
                log.warning("không lấy được giá gói %s: %s", plan, e)
                failed = True
                price = {"price": None, "currency": "usd", "interval": "month"}
            limit = self.monthly_limit(plan)
            paid.append({"id": plan, "name": PLAN_NAMES[plan], **price, "monthly_chars": limit,
                         "chars": f"{_fmt_n(limit)}/tháng",
                         "available": bool(self.enabled and self.settings.polar_plan_configured(plan))})
        data = {
            "enabled": self.enabled,
            "plans": [{"id": "free", "name": PLAN_NAMES["free"], "price": 0, "currency": "usd", "interval": None,
                       "chars": f"{_fmt_n(self.settings.free_daily_chars)}/ngày",
                       "daily_chars": int(self.settings.free_daily_chars)}] + paid,
        }
        with self._lock:
            self._plans_cache = {"data": data, "exp": t + (PLANS_ERROR_TTL if failed else PLANS_TTL)}
        return json.loads(json.dumps(data))

    # ---------- checkout ----------
    def create_checkout(self, plan: str, email: str | None = None) -> str:
        self.require_enabled()
        if plan not in PAID_PLANS:
            raise BillingError("bad_plan", "Gói không hợp lệ. Hãy chọn gói Cá nhân hoặc Không giới hạn.", 400)
        product = self.product_id(plan)
        if not product or not self.settings.polar_plan_configured(plan):
            raise BillingError("billing_disabled", "Gói này chưa mở bán. Bạn quay lại sau nhé.", 503)
        base = self.settings.public_base
        try:
            co = self.client.create_checkout(
                product,
                success_url=base + "/thanh-toan.html?checkout_id={CHECKOUT_ID}",
                return_url=base + "/#bang-gia",
                metadata={"plan": plan},
                customer_email=email or None,
            )
        except PolarError as e:
            if e.status == 422 and email:  # Polar kiểm email kỹ hơn (tên miền nhận được thư…)
                raise BillingError("bad_email", MSG_BAD_EMAIL, 400) from None
            log.warning("tạo checkout lỗi: %s", e)
            raise BillingError("billing_unavailable", MSG_UNAVAILABLE, 503) from None
        url = co.get("url")
        if not url:
            raise BillingError("billing_unavailable", MSG_UNAVAILABLE, 503)
        return str(url)

    def checkout_status(self, checkout_id: str) -> dict:
        self.require_enabled()
        if not CHECKOUT_ID_RE.fullmatch(checkout_id or ""):
            raise BillingError("bad_checkout_id", "Mã thanh toán không hợp lệ.", 400)
        try:
            co = self.client.get_checkout(checkout_id)
        except PolarError as e:
            if e.status in (404, 422):
                raise BillingError("checkout_not_found", "Không tìm thấy phiên thanh toán này.", 404) from None
            log.warning("đọc checkout lỗi: %s", e)
            raise BillingError("billing_unavailable", MSG_UNAVAILABLE, 503) from None
        plan = (co.get("metadata") or {}).get("plan")
        if plan not in PAID_PLANS:
            plan = {self.settings.polar_product_personal: "personal",
                    self.settings.polar_product_unlimited: "unlimited"}.get(co.get("product_id"))
        return {"status": co.get("status") or "open", "plan": plan,
                "email": mask_email(co.get("customer_email"))}

    def portal_url(self, who: dict) -> str:
        self.require_enabled()
        if not who or who.get("kind") != "polar" or not who.get("customer_id"):
            raise BillingError("not_polar_key", "Chỉ API key mua qua Polar mới có cổng quản lý gói.", 400)
        try:
            return self.client.create_customer_session(who["customer_id"], self.settings.public_base)
        except PolarError as e:
            log.warning("tạo customer session lỗi: %s", e)
            raise BillingError("billing_unavailable", MSG_UNAVAILABLE, 503) from None

    # ---------- key ----------
    def resolve_api_key(self, key: str, now: float | None = None,
                        on_miss: Callable[[], None] | None = None) -> dict:
        return resolve_api_key(key, self.settings, self.store, self.client if self.enabled else None, now,
                               on_miss=on_miss)

    # ---------- quota tháng ----------
    @staticmethod
    def usage_key(license_key_id: str, month: str | None = None) -> str:
        return f"usage:{license_key_id}:{month or month_id()}"

    def usage(self, who: dict, now: float | None = None) -> dict:
        m = month_id(now)
        used = int(self.store.kv_get(self.usage_key(who["license_key_id"], m)) or 0)
        limit = self.monthly_limit(who["plan"])
        return {"used": used, "limit": limit, "remaining": max(0, limit - used),
                "reset_at": month_reset_at(now).isoformat(), "plan": who["plan"], "period": "month"}

    def charge(self, who: dict, chars: int, now: float | None = None) -> str:
        """Trừ hạn mức tháng của key Polar; vượt → 429. Trả khoá KV đã trừ (để hoàn khi lỗi)."""
        m = month_id(now)
        uk = self.usage_key(who["license_key_id"], m)
        limit = self.monthly_limit(who["plan"])
        new = self.store.kv_incr(uk, chars)
        if new > limit:
            self.store.kv_incr(uk, -chars)
            remaining = max(0, limit - (new - chars))
            reset = month_reset_at(now).strftime("%d/%m/%Y")
            name = PLAN_NAMES.get(who["plan"], who["plan"])
            if remaining <= 0:
                msg = (f"Gói {name} đã dùng hết {_fmt_n(limit)} ký tự của tháng này. "
                       f"Hạn mức sẽ làm mới vào ngày {reset}.")
            else:
                msg = (f"Gói {name} chỉ còn {_fmt_n(remaining)} ký tự trong tháng này (hạn mức "
                       f"{_fmt_n(limit)} ký tự/tháng), văn bản này có {_fmt_n(chars)} ký tự. "
                       f"Hạn mức sẽ làm mới vào ngày {reset}.")
            raise BillingError("quota_exceeded", msg, 429)
        self._track_month_key(m, who)
        return uk

    def _track_month_key(self, month: str, who: dict) -> None:
        """Chỉ mục ``usage_keys:<YYYY-MM>`` → {license_key_id: plan} để admin cộng tổng theo gói."""
        ik = f"usage_keys:{month}"
        lkid, plan = who["license_key_id"], who["plan"]
        try:
            cur = self.store.kv_get(ik) or {}
            if cur.get(lkid) != plan:
                def put(cur):
                    cur = dict(cur or {})
                    cur[lkid] = plan
                    return cur
                _kv_update(self.store, ik, put)  # nguyên tử cả giữa các container
        except Exception:  # pragma: no cover
            log.exception("không ghi được chỉ mục usage_keys")

    # ---------- webhook ----------
    def handle_webhook(self, body: bytes, headers) -> dict:
        secret = self.settings.polar_webhook_secret
        if not secret:
            raise BillingError("billing_disabled", MSG_DISABLED, 503)
        if not verify_webhook(body, headers, secret):
            raise BillingError("bad_signature", "Chữ ký webhook không hợp lệ.", 403)
        wid = _header(headers, "webhook-id")[:200]
        if self.store.kv_incr(f"wh:{wid}", 1) > 1:
            return {"ok": True, "duplicate": True}
        try:
            return self._process_event(wid, body)
        except Exception:
            self.store.kv_incr(f"wh:{wid}", -1)  # cho phép Polar gửi lại
            raise

    def _process_event(self, wid: str, body: bytes) -> dict:
        try:
            payload = json.loads(body or b"{}")
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        etype = str(payload.get("type") or "unknown")[:80]
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        if etype.startswith("customer."):
            customer_id = data.get("id")
        else:
            customer_id = data.get("customer_id") or (data.get("customer") or {}).get("id")
        customer_id = str(customer_id or "")[:100] or None
        invalidated = 0
        if etype in INVALIDATING_EVENTS and customer_id:
            invalidated = invalidate_customer(self.store, customer_id)
        plan = (data.get("metadata") or {}).get("plan") if isinstance(data.get("metadata"), dict) else None
        email = data.get("email") if etype.startswith("customer.") else (data.get("customer") or {}).get("email")
        event = {"id": wid, "type": etype, "at": datetime.now(VN_TZ).isoformat(timespec="seconds"),
                 "customer_id": customer_id, "plan": plan if plan in PAID_PLANS else None,
                 "email": mask_email(email if isinstance(email, str) else None),
                 "status": data.get("status") if isinstance(data.get("status"), str) else None,
                 "invalidated": invalidated}
        _kv_update(self.store, EVENTS_KEY,  # nguyên tử cả giữa các container
                   lambda cur: (list(cur or []) + [event])[-EVENTS_MAX:])
        return {"ok": True, "type": etype}

    # ---------- admin ----------
    def admin_summary(self, now: float | None = None) -> dict:
        m = month_id(now)
        index = self.store.kv_get(f"usage_keys:{m}") or {}
        usage = {p: 0 for p in PAID_PLANS}
        customers = {p: 0 for p in PAID_PLANS}
        for lkid, plan in index.items():
            n = int(self.store.kv_get(self.usage_key(lkid, m)) or 0)
            usage[plan] = usage.get(plan, 0) + n
            customers[plan] = customers.get(plan, 0) + 1
        events = list(self.store.kv_get(EVENTS_KEY) or [])
        by_type: dict[str, int] = {}
        for e in events:
            by_type[e.get("type", "unknown")] = by_type.get(e.get("type", "unknown"), 0) + 1
        return {
            "enabled": self.enabled,
            "server": self.settings.polar_server,
            "month": m,
            "usage_by_plan": usage,
            "keys_by_plan": customers,
            "events_total": len(events),
            "events_by_type": by_type,
            "events": list(reversed(events[-50:])),
        }
