"""Thanh toán qua Polar (polar.sh, Merchant of Record) — SPEC Phần 2 + Phần 3 (gói Pro tuần/tháng/năm).

- ``PolarClient``: gọi Polar API bằng httpx (timeout 10 s, ``Authorization: Bearer <token>``);
  tiêm được ``transport`` (ví dụ ``httpx.MockTransport``) để test không cần mạng.
- ``verify_webhook``: chữ ký Standard Webhooks, chấp nhận cả bí mật kiểu mới lẫn kiểu cũ của Polar.
- ``resolve_api_key``: API key của khách = License Key Polar. Kết quả xác thực được cache trong KV
  theo ``sha256(key)`` — KHÔNG bao giờ lưu hay ghi log key gốc.
- ``Billing``: gom cấu hình + store + client cho API (bảng giá, checkout, quota theo kỳ, webhook).
- Quota trả phí theo kỳ của gói: pro_week → ``usage:<lk_id>:<ISO-YEAR>-W<ww>`` (tuần ISO giờ VN, reset
  thứ Hai 00:00 UTC+7); pro_month/pro_year → ``usage:<lk_id>:<YYYY-MM>``.
- Lỗi là ``BillingError`` (``LocalizedError``): thông điệp song ngữ lấy từ ``messages.py``.
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

from .messages import DEFAULT_LANG, LocalizedError, plan_name
from .settings import PAID_PLANS, PLAN_PERIODS

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
    "week_id",
    "week_reset_at",
    "period_id",
    "period_reset_at",
    "save_percent",
    "PAID_PLANS",
    "PLAN_PERIODS",
    "PLAN_INTERVALS",
    "CHECKOUT_ID_RE",
    "LICENSE_KEY_RE",
]

VN_TZ = timezone(timedelta(hours=7), "Asia/Ho_Chi_Minh")

# Chu kỳ thanh toán mặc định của từng gói (dùng khi Polar không trả recurring_interval)
PLAN_INTERVALS = {"pro_week": "week", "pro_month": "month", "pro_year": "year"}
# "Save X%": gói → (gói tham chiếu, số kỳ tham chiếu trong một chu kỳ của gói)
SAVE_REFERENCE = {"pro_month": ("pro_week", 4), "pro_year": ("pro_month", 12)}
POPULAR_PLAN = "pro_month"
# License key Polar của DatTTS: tiền tố "DATTTS-" + chữ/số/gạch nối. Sai dạng → 401 ngay, không gọi Polar.
LICENSE_KEY_RE = re.compile(r"^DATTTS-[A-Za-z0-9-]{1,120}$", re.IGNORECASE)
CHECKOUT_ID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

# Cache kết quả xác thực license key (giây)
LK_POSITIVE_TTL = 600
LK_NEGATIVE_TTL = 60
LK_STALE_MAX = 24 * 3600
# Bị giới hạn tần suất (on_miss chặn) thì chỉ dùng cache cũ trong khoảng ngắn này: giới hạn đếm theo IP và
# người cầm key bị lộ có thể cố tình kích hoạt nó (gửi key rác) để key cũ đã đổi (rotate) sống tiếp.
# Polar lỗi thật thì vẫn dùng tới LK_STALE_MAX.
LK_RATE_LIMITED_STALE_MAX = 15 * 60
PLANS_TTL = 600
PLANS_ERROR_TTL = 60

# Sổ cái dùng ký tự lâu dài theo license key (bằng chứng cho chính sách hoàn tiền "< 10 %" và chống mất
# quota giữa kỳ khi mục KV hết hạn — modal.Dict xoá mục sau 7 ngày không dùng). Sao lưu thêm vào blob.
LEDGER_DAYS_MAX = 400
LEDGER_PERIODS_MAX = 60
_SAFE_ID_RE = re.compile(r"[A-Za-z0-9-]{1,100}")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

EVENTS_KEY = "billing_events"
EVENTS_MAX = 500
# Sự kiện làm thay đổi quyền dùng key → xoá cache ``lk:*`` của khách đó
INVALIDATING_EVENTS = frozenset({
    "subscription.revoked",
    "subscription.updated",
    "benefit_grant.revoked",
    "benefit_grant.updated",   # đổi (rotate) license key / cấp lại quyền
    "customer.state_changed",
    "order.refunded",          # hoàn tiền: buộc hỏi lại Polar ngay (key bị thu hồi nếu chủ shop revoke)
    "refund.created",
    "refund.updated",
})


class BillingError(LocalizedError):
    """Lỗi thanh toán/key: ``BillingError(code, status=…, key=…, params=…)`` — thông điệp trong messages.py."""


def _unavailable() -> BillingError:
    return BillingError("billing_unavailable", status=503)


def _invalid_key() -> BillingError:
    return BillingError("invalid_api_key", status=401)


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


def week_id(now: float | None = None) -> str:
    """Tuần ISO theo giờ VN: ``2026-W45`` (năm ISO, có thể khác năm dương lịch quanh 1/1)."""
    y, w, _ = _now_vn(now).isocalendar()
    return f"{y}-W{w:02d}"


def week_reset_at(now: float | None = None) -> datetime:
    """Thứ Hai kế tiếp, 00:00 giờ VN."""
    d = _now_vn(now)
    start = datetime(d.year, d.month, d.day, tzinfo=VN_TZ) - timedelta(days=d.weekday())
    return start + timedelta(days=7)


def period_id(period: str, now: float | None = None) -> str:
    return week_id(now) if period == "week" else month_id(now)


def period_reset_at(period: str, now: float | None = None) -> datetime:
    return week_reset_at(now) if period == "week" else month_reset_at(now)


def save_percent(price: int | None, ref_price: int | None, ref_count: int) -> int | None:
    """Phần trăm tiết kiệm so với mua ``ref_count`` kỳ của gói tham chiếu, LÀM TRÒN XUỐNG (SPEC Phần 3:
    không bao giờ quảng cáo mức tiết kiệm cao hơn thực tế). Không đủ dữ liệu hoặc không rẻ hơn → None.

    4,99 so với 4 × 1,99 → 37 (37,31 %); 49,99 so với 12 × 4,99 → 16 (16,52 %).
    """
    if price is None or ref_price is None or ref_count <= 0:
        return None
    full = int(ref_price) * int(ref_count)
    if full <= 0 or price >= full:
        return None
    # Số học nguyên (không lỗi làm tròn số thực): floor(100 * (full - price) / full)
    pct = 100 * (full - int(price)) // full
    return int(pct) if pct > 0 else None


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
        raise PolarError(f"Polar trả {r.status_code} cho {method} {path.split('?')[0]}", r.status_code)

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
    """benefit_id → gói (chỉ các gói có benefit được cấu hình)."""
    return {b: plan for plan in PAID_PLANS if (b := settings.polar_benefit(plan))}


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


def _safe_id(x: str) -> str:
    x = str(x or "")
    return x if _SAFE_ID_RE.fullmatch(x) else hashlib.sha256(x.encode("utf-8")).hexdigest()[:40]


def ledger_key(license_key_id: str) -> str:
    return f"ledger:{license_key_id}"


def ledger_blob(license_key_id: str) -> str:
    return f"ledger-{_safe_id(license_key_id)}.json"


def ledger_customer_key(customer_id: str) -> str:
    return f"ledger_cust:{customer_id}"


def ledger_customer_blob(customer_id: str) -> str:
    return f"ledger-c-{_safe_id(customer_id)}.json"


def _blob_json(store, name: str) -> Any:
    try:
        raw = store.blob_get(name)
        return json.loads(raw) if raw else None
    except Exception:  # pragma: no cover — blob hỏng/không đọc được
        log.exception("không đọc được blob %s", name)
        return None


def _blob_put_json(store, name: str, value: Any) -> None:
    try:
        store.blob_put(name, json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    except Exception:  # pragma: no cover
        log.exception("không sao lưu được %s", name)


def _apply_delta(counts: dict, key: str, delta: int, keep: int) -> dict:
    """Cộng ``delta`` vào ``counts[key]``; delta âm (hoàn quota) trừ dần từ mục mới nhất, không xuống dưới 0."""
    counts = {k: int(v) for k, v in (counts or {}).items()}
    if delta >= 0:
        counts[key] = counts.get(key, 0) + delta
    else:
        need = -delta
        for k in ([key] if key in counts else []) + [k for k in sorted(counts, reverse=True) if k != key]:
            take = min(need, counts[k])
            counts[k] -= take
            need -= take
            if not need:
                break
    return {k: counts[k] for k in sorted(counts)[-keep:]}


def load_ledger(store, license_key_id: str) -> dict | None:
    """Sổ cái của một key (KV; KV mất/hết hạn → khôi phục từ blob)."""
    cur = store.kv_get(ledger_key(license_key_id))
    if isinstance(cur, dict):
        return cur
    cur = _blob_json(store, ledger_blob(license_key_id))
    if isinstance(cur, dict) and cur.get("license_key_id") == license_key_id:
        return cur
    return None


def ledger_add(store, lkid: str | None, bucket: str, delta: int, now: float | None, plan: str | None = None,
                customer_id: str | None = None) -> None:
    if not lkid or not delta:
        return
    try:
        d = _now_vn(now)
        at, day = d.isoformat(timespec="seconds"), f"{d:%Y-%m-%d}"
        k = ledger_key(lkid)
        restored = load_ledger(store, lkid) if store.kv_get(k) is None else None

        def upd(cur):
            cur = dict(cur if isinstance(cur, dict) else (restored or {}))
            if not cur:
                cur = {"license_key_id": lkid, "first_at": at, "total": 0, "days": {}, "periods": {}}
            cur["license_key_id"] = lkid
            if customer_id:
                cur["customer_id"] = customer_id
            if plan:
                cur["plan"] = plan
            cur["last_at"] = at
            cur["total"] = max(0, int(cur.get("total") or 0) + delta)
            cur["days"] = _apply_delta(cur.get("days") or {}, day, delta, LEDGER_DAYS_MAX)
            cur["periods"] = _apply_delta(cur.get("periods") or {}, bucket, delta, LEDGER_PERIODS_MAX)
            return cur

        new = _kv_update(store, k, upd)
        _blob_put_json(store, ledger_blob(lkid), new)
        cid = new.get("customer_id")
        if cid:
            ck = ledger_customer_key(cid)
            lst = store.kv_get(ck)
            if lst is None:
                lst = _blob_json(store, ledger_customer_blob(cid))
            if not isinstance(lst, list) or lkid not in lst:
                seed = lst if isinstance(lst, list) else []
                lst = _kv_update(store, ck,
                                 lambda cur: (lambda l: l if lkid in l else (l + [lkid])[-200:])(
                                     list(cur if isinstance(cur, list) else seed)))
                _blob_put_json(store, ledger_customer_blob(cid), lst)
    except Exception:  # pragma: no cover — sổ cái lỗi không được làm hỏng lượt tạo giọng đã trả tiền
        log.exception("không ghi được sổ cái dùng ký tự của %s", lkid)


_BUCKET_RE = re.compile(r"^\d{4}-(\d{2}|W\d{2})$")


def adjust_quota(store, qk: str | None, delta: int, now: float | None = None) -> None:
    """Cộng/trừ quota đã trừ trước (hoàn khi job lỗi, tính lại khi job vẫn xong). Khoá
    ``usage:<license_key_id>:<kỳ>`` của key Polar → cập nhật cả sổ cái lâu dài."""
    if not qk or not delta:
        return
    store.kv_incr(qk, int(delta))
    parts = qk.split(":")
    if len(parts) == 3 and parts[0] == "usage" and _BUCKET_RE.fullmatch(parts[2]):
        ledger_add(store, parts[1], parts[2], int(delta), now)


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
        raise _invalid_key()
    if _is_internal(key, settings):
        return {"kind": "internal"}
    if not settings.billing_enabled or client is None or not LICENSE_KEY_RE.fullmatch(key):
        # Không đúng dạng key Polar của DatTTS → từ chối ngay: không gọi Polar, không ghi cache.
        raise _invalid_key()
    t = time.time() if now is None else now
    h = key_hash(key)
    ck = f"lk:{h}"
    cached = store.kv_get(ck)
    if isinstance(cached, dict) and cached.get("ok") and (cached.get("info") or {}).get("plan") not in PAID_PLANS:
        cached = None  # bộ đệm từ gói đã bỏ (personal/unlimited) → hỏi lại Polar
    if isinstance(cached, dict) and t < float(cached.get("exp") or 0):
        return _cached_result(cached)
    age = t - float(cached.get("at") or 0) if isinstance(cached, dict) else None
    stale_ok = age is not None and age <= LK_STALE_MAX
    if on_miss is not None:
        try:
            on_miss()
        except BillingError:
            if age is not None and age <= LK_RATE_LIMITED_STALE_MAX:
                return _cached_result(cached)
            raise
    try:
        v = client.validate_license_key(key)
    except PolarError as e:
        log.warning("không xác thực được license key %s…: %s", h[:12], e)
        if stale_ok:
            return _cached_result(cached)
        raise _unavailable() from None
    info = _info_from_validated(v, settings) if v is not None else None
    if info is None:
        store.kv_set(ck, {"ok": False, "at": t, "exp": t + LK_NEGATIVE_TTL})
        raise _invalid_key()
    store.kv_set(ck, {"ok": True, "info": info, "at": t, "exp": t + LK_POSITIVE_TTL})
    try:
        _index_customer(store, info["customer_id"], h)
    except Exception:  # pragma: no cover
        log.exception("không ghi được chỉ mục lkc")
    return dict(info)


def _cached_result(cached: dict) -> dict:
    if cached.get("ok") and isinstance(cached.get("info"), dict):
        return dict(cached["info"])
    raise _invalid_key()


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
            effect = ("các gói thiếu sản phẩm/benefit sẽ không bán" if settings.billing_enabled
                      else "billing TẮT (không bán gói nào) cho tới khi đặt đủ biến")
            if "POLAR_WEBHOOK_SECRET" in missing:
                effect += "; mọi webhook Polar bị từ chối (503) nên thu hồi key không có hiệu lực ngay"
            log.error("Cấu hình Polar thiếu %s — %s", ", ".join(missing), effect)

    @property
    def enabled(self) -> bool:
        return bool(self.settings.billing_enabled and self.client is not None)

    def require_enabled(self) -> None:
        if not self.enabled:
            raise BillingError("billing_disabled", status=503)

    # ---------- gói ----------
    def plan_limit(self, plan: str) -> int:
        """Hạn mức ký tự trong một kỳ (``plan_period``) của gói."""
        return self.settings.plan_chars(plan)

    @staticmethod
    def plan_period(plan: str) -> str:
        return PLAN_PERIODS.get(plan, "month")

    def product_id(self, plan: str) -> str | None:
        return self.settings.polar_product(plan)

    def _price(self, plan: str) -> dict:
        """Giá của gói từ Polar. ``sellable`` False khi sản phẩm đã lưu trữ (archived) hoặc không có giá cố định
        → gói không bán (checkout chắc chắn bị Polar từ chối)."""
        default_interval = PLAN_INTERVALS.get(plan)
        empty = {"price": None, "currency": "usd", "interval": default_interval, "interval_count": 1,
                 "sellable": True}
        product_id = self.product_id(plan)
        if not product_id or not self.enabled or not self.settings.polar_plan_configured(plan):
            return empty
        p = self.client.get_product(product_id)
        if p.get("is_archived"):
            log.error("sản phẩm Polar của gói %s đã bị lưu trữ (archived) — gói không bán", plan)
            return {**empty, "sellable": False}
        for pr in p.get("prices") or []:
            if not isinstance(pr, dict) or pr.get("is_archived") or pr.get("amount_type") != "fixed":
                continue
            # Polar đặt chu kỳ ở cấp SẢN PHẨM (Product.recurring_interval); trường cùng tên trên giá chỉ còn
            # ở schema Legacy* → dự phòng; không có cả hai → chu kỳ mặc định của gói.
            return {"price": int(pr.get("price_amount") or 0),
                    "currency": str(pr.get("price_currency") or "usd").lower(),
                    "interval": p.get("recurring_interval") or pr.get("recurring_interval") or default_interval,
                    "interval_count": int(p.get("recurring_interval_count") or 1), "sellable": True}
        log.error("sản phẩm Polar của gói %s không có giá cố định còn hiệu lực — gói không bán", plan)
        return {**empty, "sellable": False}

    def _plans_data(self, now: float | None = None) -> dict:
        t = time.time() if now is None else now
        with self._lock:
            c = self._plans_cache
            if c and t < c["exp"]:
                return c["data"]
        failed = False
        paid: dict[str, dict] = {}
        for plan in PAID_PLANS:
            try:
                price = self._price(plan)
            except PolarError as e:
                log.warning("không lấy được giá gói %s: %s", plan, e)
                failed = True
                # Lỗi tạm thời: vẫn bán (giá hiện ở trang thanh toán), cache ngắn PLANS_ERROR_TTL.
                price = {"price": None, "currency": "usd", "interval": PLAN_INTERVALS.get(plan),
                         "interval_count": 1, "sellable": True}
            sellable = price.pop("sellable", True)
            paid[plan] = {"id": plan, **price, "chars": self.plan_limit(plan), "period": self.plan_period(plan),
                          "available": bool(self.enabled and self.settings.polar_plan_configured(plan) and sellable),
                          "popular": plan == POPULAR_PLAN, "save_percent": None}
        for plan, (ref, count) in SAVE_REFERENCE.items():
            cur, base = paid.get(plan), paid.get(ref)
            if cur and base and cur["currency"] == base["currency"]:
                cur["save_percent"] = save_percent(cur["price"], base["price"], count)
        free = {"id": "free", "price": 0, "currency": "usd", "interval": None, "interval_count": None,
                "chars": int(self.settings.free_daily_chars), "period": "day", "available": True,
                "popular": False, "save_percent": None}
        data = {"enabled": self.enabled, "currency": "usd", "plans": [free] + [paid[p] for p in PAID_PLANS]}
        with self._lock:
            self._plans_cache = {"data": data, "exp": t + (PLANS_ERROR_TTL if failed else PLANS_TTL)}
        return data

    def _plan_sellable(self, plan: str) -> bool:
        """Theo bảng giá đang cache (không gọi Polar): False chỉ khi đã biết chắc gói không bán được."""
        with self._lock:
            c = self._plans_cache
            data = c.get("data") if c and time.time() < c["exp"] else None
        if not data:
            return True
        return next((bool(p.get("available")) for p in data["plans"] if p["id"] == plan), True)

    def plans(self, lang: str = DEFAULT_LANG, now: float | None = None) -> dict:
        """Bảng giá (giá thật từ Polar, cache 10 phút; lỗi → ``price: null``). ``name`` theo ngôn ngữ."""
        data = json.loads(json.dumps(self._plans_data(now)))
        for p in data["plans"]:
            p["name"] = plan_name(p["id"], lang)
        return data

    # ---------- checkout ----------
    def create_checkout(self, plan: str, email: str | None = None) -> str:
        self.require_enabled()
        if plan not in PAID_PLANS:
            raise BillingError("bad_plan", status=400)
        product = self.product_id(plan)
        if not product or not self.settings.polar_plan_configured(plan):
            raise BillingError("billing_disabled", status=503, key="billing_disabled.plan")
        if not self._plan_sellable(plan):  # sản phẩm đã lưu trữ / không có giá → đừng gửi sang Polar
            raise BillingError("billing_disabled", status=503, key="billing_disabled.plan")
        base = self.settings.public_base
        try:
            co = self.client.create_checkout(
                product,
                success_url=base + "/checkout-success.html?checkout_id={CHECKOUT_ID}",
                return_url=base + "/#pricing",
                metadata={"plan": plan},
                customer_email=email or None,
            )
        except PolarError as e:
            if e.status == 422 and email:  # Polar kiểm email kỹ hơn (tên miền nhận được thư…)
                raise BillingError("bad_email", status=400, key="bad_email.polar") from None
            log.warning("tạo checkout lỗi: %s", e)
            raise _unavailable() from None
        url = co.get("url")
        if not url:
            raise _unavailable()
        return str(url)

    def checkout_status(self, checkout_id: str) -> dict:
        self.require_enabled()
        if not CHECKOUT_ID_RE.fullmatch(checkout_id or ""):
            raise BillingError("bad_checkout_id", status=400)
        try:
            co = self.client.get_checkout(checkout_id)
        except PolarError as e:
            if e.status in (404, 422):
                raise BillingError("checkout_not_found", status=404) from None
            log.warning("đọc checkout lỗi: %s", e)
            raise _unavailable() from None
        plan = (co.get("metadata") or {}).get("plan") if isinstance(co.get("metadata"), dict) else None
        if plan not in PAID_PLANS:
            by_product = {self.product_id(p): p for p in PAID_PLANS if self.product_id(p)}
            plan = by_product.get(co.get("product_id"))
        return {"status": co.get("status") or "open", "plan": plan,
                "email": mask_email(co.get("customer_email"))}

    def portal_url(self, who: dict) -> str:
        self.require_enabled()
        if not who or who.get("kind") != "polar" or not who.get("customer_id"):
            raise BillingError("not_polar_key", status=400)
        try:
            return self.client.create_customer_session(who["customer_id"], self.settings.public_base)
        except PolarError as e:
            log.warning("tạo customer session lỗi: %s", e)
            raise _unavailable() from None

    # ---------- key ----------
    def resolve_api_key(self, key: str, now: float | None = None,
                        on_miss: Callable[[], None] | None = None) -> dict:
        return resolve_api_key(key, self.settings, self.store, self.client if self.enabled else None, now,
                               on_miss=on_miss)

    # ---------- quota theo kỳ (tuần ISO / tháng, giờ VN) ----------
    @staticmethod
    def usage_key(license_key_id: str, bucket: str | None = None) -> str:
        return f"usage:{license_key_id}:{bucket or month_id()}"

    def usage(self, who: dict, now: float | None = None) -> dict:
        period = self.plan_period(who["plan"])
        used = self._period_used(who["license_key_id"], period_id(period, now))
        limit = self.plan_limit(who["plan"])
        return {"used": used, "limit": limit, "remaining": max(0, limit - used),
                "reset_at": period_reset_at(period, now).isoformat(), "plan": who["plan"], "period": period}

    def charge(self, who: dict, chars: int, now: float | None = None) -> str:
        """Trừ hạn mức kỳ hiện tại của key Polar; vượt → 429. Trả khoá KV đã trừ (để hoàn khi lỗi)."""
        period = self.plan_period(who["plan"])
        bucket = period_id(period, now)
        uk = self.usage_key(who["license_key_id"], bucket)
        limit = self.plan_limit(who["plan"])
        if self.store.kv_get(uk) is None:
            # Bucket KV hết hạn giữa kỳ (7 ngày không dùng trên Modal) → nạp lại từ sổ cái, không reset quota.
            seed = self._period_used(who["license_key_id"], bucket)
            if seed:
                _kv_update(self.store, uk, lambda cur: cur if cur is not None else seed)
        new = self.store.kv_incr(uk, chars)
        if new > limit:
            self.store.kv_incr(uk, -chars)
            remaining = max(0, limit - (new - chars))
            params = {"plan": who["plan"], "limit": limit, "period": period,
                      "reset": period_reset_at(period, now).isoformat()}
            if remaining <= 0:
                raise BillingError("quota_exceeded", status=429, key="quota_exceeded.paid_used_up", params=params)
            raise BillingError("quota_exceeded", status=429, key="quota_exceeded.paid_partial",
                               params={**params, "remaining": remaining, "chars": int(chars)})
        self._track_usage_key(bucket, who)
        self._ledger_add(who.get("license_key_id"), bucket, int(chars), now, plan=who.get("plan"),
                         customer_id=who.get("customer_id"))
        return uk

    def refund(self, uk: str, chars: int, now: float | None = None) -> None:
        """Hoàn quota đã trừ bằng ``charge`` (job lỗi): trả lại cả bucket kỳ lẫn sổ cái."""
        adjust_quota(self.store, uk, -int(chars or 0), now)

    # ---------- sổ cái lâu dài ----------
    def ledger(self, license_key_id: str) -> dict | None:
        return load_ledger(self.store, license_key_id)

    def _ledger_add(self, lkid: str | None, bucket: str, delta: int, now: float | None, plan: str | None = None,
                    customer_id: str | None = None) -> None:
        ledger_add(self.store, lkid, bucket, delta, now, plan=plan, customer_id=customer_id)

    def _period_used(self, lkid: str, bucket: str) -> int:
        """Số ký tự đã dùng trong kỳ: bucket KV, KV mất/hết hạn → lấy từ sổ cái (không reset giữa kỳ)."""
        v = self.store.kv_get(self.usage_key(lkid, bucket))
        if v is not None:
            return int(v)
        led = self.ledger(lkid) or {}
        return int((led.get("periods") or {}).get(bucket) or 0)

    def admin_usage(self, license_key_id: str | None = None, customer_id: str | None = None,
                    since: str | None = None) -> dict:
        """Tra cứu sổ cái theo key hoặc khách. ``since`` (YYYY-MM-DD, giờ VN, thường là ngày thanh toán) →
        ``used_since`` = ký tự đã dùng từ ngày đó; ``percent_of_period`` so với hạn mức MỘT kỳ của gói."""
        if since is not None and not DATE_RE.fullmatch(since):
            raise BillingError("bad_request", status=400)
        if license_key_id:
            ids = [license_key_id]
        elif customer_id:
            lst = self.store.kv_get(ledger_customer_key(customer_id))
            if lst is None:
                lst = _blob_json(self.store, ledger_customer_blob(customer_id))
            ids = list(lst) if isinstance(lst, list) else []
        else:
            raise BillingError("bad_request", status=400)
        keys = []
        for lkid in ids:
            led = self.ledger(str(lkid))
            if not led:
                continue
            days = {k: int(v) for k, v in (led.get("days") or {}).items()}
            used = sum(v for k, v in days.items() if k >= since) if since else int(led.get("total") or 0)
            limit = self.plan_limit(led.get("plan") or "")
            keys.append({**led, "since": since or (str(led.get("first_at") or "")[:10] or None),
                         "used_since": used, "period_limit": limit or None,
                         "percent_of_period": round(100 * used / limit, 2) if limit else None})
        return {"license_key_id": license_key_id, "customer_id": customer_id, "keys": keys}

    def _track_usage_key(self, bucket: str, who: dict) -> None:
        """Chỉ mục ``usage_keys:<kỳ>`` → {license_key_id: plan} để admin cộng tổng theo gói."""
        ik = f"usage_keys:{bucket}"
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
            raise BillingError("billing_disabled", status=503)
        if not verify_webhook(body, headers, secret):
            raise BillingError("bad_signature", status=403)
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
        """Tổng ký tự đã dùng theo gói trong kỳ hiện tại (tuần này cho pro_week, tháng này cho gói còn lại)."""
        m, w = month_id(now), week_id(now)
        usage = {p: 0 for p in PAID_PLANS}
        customers = {p: 0 for p in PAID_PLANS}
        for bucket in (m, w):
            for lkid, plan in (self.store.kv_get(f"usage_keys:{bucket}") or {}).items():
                if plan not in usage or period_id(self.plan_period(plan), now) != bucket:
                    continue
                usage[plan] += int(self.store.kv_get(self.usage_key(lkid, bucket)) or 0)
                customers[plan] += 1
        events = list(self.store.kv_get(EVENTS_KEY) or [])
        by_type: dict[str, int] = {}
        for e in events:
            by_type[e.get("type", "unknown")] = by_type.get(e.get("type", "unknown"), 0) + 1
        return {
            "enabled": self.enabled,
            "server": self.settings.polar_server,
            "month": m,
            "week": w,
            "usage_by_plan": usage,
            "keys_by_plan": customers,
            "events_total": len(events),
            "events_by_type": by_type,
            "events": list(reversed(events[-50:])),
        }
