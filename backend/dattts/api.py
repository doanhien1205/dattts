"""HTTP API của DatTTS (FastAPI). ``create_app(renderer, store, settings)`` — không có trạng thái toàn cục.

Mọi lỗi có dạng ``{"error": {"code", "message"}}``; ``message`` theo ``Accept-Language`` của request
(``vi*`` → tiếng Việt, còn lại → tiếng Anh), lấy từ bảng ``messages.py``.
"""

from __future__ import annotations

import csv
import hmac
import io
import ipaddress
import json
import logging
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .billing import PAID_PLANS, Billing, BillingError, PolarClient, mask_email
from .audio import CONTENT_TYPES
from .engine import DEFAULT_VOICE_ID, voice_id_from_name
from .messages import LocalizedError, error_body, pick_lang, plan_name
from .messages import text as msg_text
from .render import RenderRequest, billable_chars, plan, request_key, validate
from .settings import Settings
from .store import (
    derive_format,
    job_key,
    load_meta,
    sample_blob_name,
)
from .textproc import count_chars

log = logging.getLogger("dattts.api")

__all__ = ["create_app", "VN_TZ", "TTSBody", "WaitlistBody", "CheckoutBody"]

VN_TZ = timezone(timedelta(hours=7), "Asia/Ho_Chi_Minh")  # Việt Nam không có giờ mùa hè

FILE_RE = re.compile(r"^([0-9a-f]{20})\.(mp3|wav|ogg|srt|vtt)$")
JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                      r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.[A-Za-z]{2,24}$")

MEDIA_TYPES = {
    **CONTENT_TYPES,
    "srt": "application/x-subrip; charset=utf-8",
    "vtt": "text/vtt; charset=utf-8",
}
IMMUTABLE = "public, max-age=31536000, immutable"

WAITLIST_KEY = "waitlist"
WAITLIST_BLOB = "waitlist.json"


# ───────────────────────────── Mô hình body ─────────────────────────────


class DictEntry(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")
    from_: str = Field("", alias="from")
    to: str = ""


class TTSBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    text: str
    voice: str | None = None
    speed: float = 1.0
    format: str = "mp3"
    dictionary: list[DictEntry] = Field(default_factory=list)

    def to_request(self) -> RenderRequest:
        return RenderRequest(
            text=self.text,
            voice=(self.voice or "").strip() or DEFAULT_VOICE_ID,
            speed=float(self.speed),
            format=(self.format or "").strip().lower(),
            dictionary=[{"from": e.from_, "to": e.to} for e in self.dictionary],
        )


class WaitlistBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    email: str = Field(max_length=254)
    plan: str | None = Field(default=None, max_length=40)
    note: str | None = Field(default=None, max_length=1000)


class CheckoutBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    plan: str = Field(max_length=40)
    email: str | None = Field(default=None, max_length=254)


WEBHOOK_PATH = "/api/billing/webhook"

# Proxy Cloudflare Pages (SPEC Phần 4): header do Pages Function đặt (đã xoá bản khách tự gửi).
PROXY_SECRET_HEADER = "x-dattts-proxy-secret"
PROXY_CLIENT_IP_HEADER = "x-dattts-client-ip"
PROXY_EXEMPT_PATHS = frozenset({"/api/health"})
WEBHOOK_MAX_BYTES = 2 * 1024 * 1024
CHECKOUT_RATE_PER_HOUR = 10
# Số lần mỗi IP được hỏi Polar xác thực key (cache trượt) mỗi phút — chặn dò key ngẫu nhiên làm cạn
# hạn mức API Polar của tổ chức và chiếm luồng xử lý.
KEY_VALIDATE_RATE_PER_MIN = 20


# ───────────────────────────── Tiện ích ─────────────────────────────


class ApiError(LocalizedError):
    """Lỗi HTTP của API: ``ApiError(code, status=…, key=…, params=…, headers=…)`` (thông điệp: messages.py)."""


def _lang(request: Request | None) -> str:
    return pick_lang(request.headers.get("accept-language") if request is not None else None)


def _err(code: str, message: str, status: int, headers: dict | None = None) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status, headers=headers)


def _err_l(request: Request | None, code: str, status: int, key: str | None = None,
           headers: dict | None = None, **params) -> JSONResponse:
    return _err(code, msg_text(key or code, _lang(request), params), status, headers)


# Trường lỗi kiểu dữ liệu (pydantic) → (mã lỗi, khoá thông điệp)
_FIELD_ERRORS = {
    "text": ("empty_text", "empty_text"),
    "speed": ("bad_speed", "bad_speed"),
    "format": ("bad_format", "bad_format"),
    "dictionary": ("bad_dictionary", "bad_dictionary"),
    "voice": ("unknown_voice", "unknown_voice.invalid"),
    "email": ("bad_email", "bad_email"),
    "plan": ("bad_request", "bad_request.plan"),
    "note": ("bad_request", "bad_request.note"),
}


def _validation_error(exc: RequestValidationError) -> tuple[str, str]:
    """→ (mã lỗi, khoá thông điệp)."""
    for e in exc.errors():
        loc = [p for p in e.get("loc", ()) if p != "body"]
        typ = e.get("type", "")
        if typ in ("json_invalid", "model_attributes_type") or not loc:
            return "bad_json", "bad_json"
        field = str(loc[0])
        if field == "text" and typ != "missing":
            return "bad_text", "bad_text"
        if field in _FIELD_ERRORS:
            return _FIELD_ERRORS[field]
    return "bad_request", "bad_request"


def _bytes_response(request: Request, data: bytes, media_type: str, headers: dict) -> Response:
    """Trả bytes, hỗ trợ ``Range`` (để trình phát tua được) và ``If-None-Match``."""
    total = len(data)
    headers = {"Accept-Ranges": "bytes", **headers}
    etag = headers.get("ETag")
    if etag and request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    rng = (request.headers.get("range") or "").strip()
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng) if rng else None
    if m and (m.group(1) or m.group(2)):
        if m.group(1):
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else total - 1
        else:
            start, end = max(0, total - int(m.group(2))), total - 1
        end = min(end, total - 1)
        if start >= total or start > end:
            return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{total}"})
        headers["Content-Range"] = f"bytes {start}-{end}/{total}"
        return Response(data[start:end + 1], status_code=206, media_type=media_type, headers=headers)
    return Response(data, media_type=media_type, headers=headers)


_READ_CHUNK = 256 * 1024


def _iter_file(path: Path, start: int, length: int):
    with open(path, "rb") as f:
        f.seek(start)
        left = length
        while left > 0:
            buf = f.read(min(_READ_CHUNK, left))
            if not buf:
                break
            left -= len(buf)
            yield buf


def _file_response(request: Request, path: Path, media_type: str, headers: dict) -> Response:
    """Như ``_bytes_response`` nhưng đọc file theo luồng (seek + read): WAV hàng trăm MB không bị nạp vào RAM."""
    total = path.stat().st_size
    headers = {"Accept-Ranges": "bytes", **headers}
    etag = headers.get("ETag")
    if etag and request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    start, end, status = 0, total - 1, 200
    rng = (request.headers.get("range") or "").strip()
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng) if rng else None
    if m and (m.group(1) or m.group(2)):
        if m.group(1):
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else total - 1
        else:
            start, end = max(0, total - int(m.group(2))), total - 1
        end = min(end, total - 1)
        if start >= total or start > end:
            return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{total}"})
        headers["Content-Range"] = f"bytes {start}-{end}/{total}"
        status = 206
    length = max(0, end - start + 1)
    headers["Content-Length"] = str(length)
    if request.method == "HEAD":
        return Response(status_code=status, media_type=media_type, headers=headers)
    return StreamingResponse(_iter_file(path, start, length), status_code=status, media_type=media_type,
                             headers=headers)


class _BodyTooLarge(Exception):
    pass


class _BodyLimit:
    """Chặn body quá lớn TRƯỚC khi parse JSON (tránh tốn CPU/RAM cho request rác)."""

    def __init__(self, app, max_bytes: int, path_limits: dict[str, int] | None = None):
        self.app, self.max_bytes, self.path_limits = app, max_bytes, dict(path_limits or {})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") not in ("POST", "PUT", "PATCH"):
            return await self.app(scope, receive, send)
        # Webhook Polar có giới hạn riêng (không phụ thuộc JOB_MAX_CHARS) để không bao giờ bị chặn nhầm.
        max_bytes = self.path_limits.get(scope.get("path") or "", self.max_bytes)
        for k, v in scope.get("headers") or []:
            if k == b"content-length":
                try:
                    n = int(v)
                except ValueError:
                    n = 0
                if n > max_bytes:
                    return await self._reject(scope, send)
        seen = 0

        async def limited():
            nonlocal seen
            msg = await receive()
            if msg.get("type") == "http.request":
                seen += len(msg.get("body") or b"")
                if seen > max_bytes:
                    raise _BodyTooLarge()
            return msg

        try:
            await self.app(scope, limited, send)
        except _BodyTooLarge:
            await self._reject(scope, send)

    async def _reject(self, scope, send):
        al = b"".join(v for k, v in scope.get("headers") or [] if k == b"accept-language").decode("latin-1")
        body = json.dumps({"error": {"code": "too_long", "message": msg_text("too_long", pick_lang(al))}},
                          ensure_ascii=False).encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                                (b"connection", b"close")]})
        await send({"type": "http.response.body", "body": body})


class _VoiceView:
    """Engine "giả" chỉ có danh sách giọng — để ``plan()`` kiểm tra request trước khi tính quota."""

    def __init__(self, renderer):
        self._r = renderer
        self.name = renderer.engine_name()
        fn = getattr(renderer, "voice_name", None)
        if fn is not None:
            self.voice_name = fn

    def voices(self):
        return self._r.voices()


# ───────────────────────────── App ─────────────────────────────


def create_app(renderer, store, settings: Settings | None = None, *, polar: PolarClient | None = None) -> FastAPI:
    """``polar``: tiêm PolarClient (test dùng httpx.MockTransport); mặc định tạo từ settings nếu billing bật."""
    settings = settings or Settings.from_env()
    billing = Billing(settings, store, polar)
    app = FastAPI(title="DatTTS API", version=__version__, docs_url="/api/docs", redoc_url=None,
                  openapi_url="/api/openapi.json")
    app.state.renderer, app.state.store, app.state.settings = renderer, store, settings
    app.state.billing = billing

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_methods=["GET", "POST", "HEAD", "OPTIONS"],
        allow_headers=["Content-Type", "X-API-Key", "Authorization", "Range"],
        expose_headers=["Content-Disposition", "Content-Range", "Accept-Ranges"],
        max_age=600,
    )

    # Văn bản tiếng Việt UTF-8 ≤ 3 byte/ký tự; JSON ensure_ascii (\uXXXX) ≤ 6 byte; cộng từ điển (≤ 200×2×100).
    max_body = int(settings.job_max_chars) * 6 + 512 * 1024
    app.add_middleware(_BodyLimit, max_bytes=max_body, path_limits={WEBHOOK_PATH: WEBHOOK_MAX_BYTES})

    def via_proxy(request: Request) -> bool:
        """Request đi qua Pages Function: có ``X-DatTTS-Proxy-Secret`` khớp PROXY_SHARED_SECRET (hằng thời gian)."""
        cached = getattr(request.state, "via_proxy", None)
        if cached is not None:
            return cached
        expected = settings.proxy_shared_secret
        got = request.headers.get(PROXY_SECRET_HEADER)
        ok = bool(expected and got is not None
                  and hmac.compare_digest(got.encode("utf-8"), expected.encode("utf-8")))
        request.state.via_proxy = ok
        return ok

    if settings.require_proxy and not settings.proxy_shared_secret:
        log.warning("REQUIRE_PROXY=1 nhưng chưa đặt PROXY_SHARED_SECRET → bỏ qua (không chặn truy cập trực tiếp)")
    elif settings.proxy_shared_secret and len(settings.proxy_shared_secret) < 32:
        log.warning("PROXY_SHARED_SECRET ngắn hơn 32 ký tự — nên dùng: openssl rand -hex 32")

    @app.middleware("http")
    async def _require_proxy(request: Request, call_next):
        # Đăng ký TRƯỚC _security_headers (nằm trong nó) và SAU _BodyLimit/CORS (nằm ngoài chúng):
        # chặn trước khi đọc body hay trả header CORS.
        path = request.url.path
        if (settings.proxy_guard_enabled and (path == "/api" or path.startswith("/api/"))
                and path not in PROXY_EXEMPT_PATHS and not via_proxy(request)):
            return _err_l(request, "direct_access_forbidden", 403)
        return await call_next(request)

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return resp

    waitlist_lock = threading.Lock()
    sample_locks: dict[str, threading.Lock] = {}
    sample_locks_guard = threading.Lock()
    voices_cache: dict[str, Any] = {}

    # ---------- lỗi (thông điệp theo Accept-Language) ----------
    @app.exception_handler(LocalizedError)  # ApiError, BillingError, RenderError
    async def _localized_error(request: Request, exc: LocalizedError):
        headers = exc.headers
        if isinstance(exc, BillingError) and headers is None:
            if exc.status == 401:
                headers = {"WWW-Authenticate": 'ApiKey header="X-API-Key"'}
            elif exc.status == 429 and exc.code == "rate_limited":
                headers = {"Retry-After": "60"}
        return _err(exc.code, exc.localized(_lang(request)), exc.status, headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        code, key = _validation_error(exc)
        return _err_l(request, code, 400, key)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 404:
            return _err_l(request, "not_found", 404)
        if exc.status_code == 405:
            return _err_l(request, "method_not_allowed", 405)
        return _err_l(request, "http_error", exc.status_code)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):  # pragma: no cover - phòng hờ
        log.exception("lỗi không mong muốn: %s", exc)
        return _err_l(request, "internal_error", 500)

    # ---------- khách & quota ----------
    def client_ip(request: Request) -> str:
        # 1) Qua Pages Function (secret đúng): IP khách = X-DatTTS-Client-IP (CF-Connecting-IP), nếu là IP hợp lệ.
        if via_proxy(request):
            raw = (request.headers.get(PROXY_CLIENT_IP_HEADER) or "").strip()
            try:
                return str(ipaddress.ip_address(raw))
            except ValueError:
                pass  # IP hỏng → dùng logic cũ bên dưới
        # 2) Proxy thêm IP khách vào CUỐI X-Forwarded-For; phần đầu do khách tự gửi nên không tin được.
        # Lấy phần tử thứ ``trust_proxy_hops`` tính từ phải.
        if settings.trust_proxy:
            xff = ",".join(request.headers.getlist("x-forwarded-for"))
            hops = [h.strip() for h in xff.split(",") if h.strip()]
            if hops:
                n = max(1, int(settings.trust_proxy_hops or 1))
                return hops[-n] if len(hops) >= n else hops[0]
        return request.client.host if request.client else "unknown"

    def limit_ip(request: Request) -> str:
        """Khoá cho quota miễn phí & rate limit: IPv6 gộp theo /64 (một khách thường giữ cả /64 nên
        đổi địa chỉ trong đó không được quota mới); IPv4-mapped (::ffff:a.b.c.d) → IPv4.
        Waitlist/log vẫn ghi IP đầy đủ qua ``client_ip``."""
        ip = client_ip(request)
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return ip
        if addr.version == 6:
            if addr.ipv4_mapped is not None:
                return str(addr.ipv4_mapped)
            return str(ipaddress.ip_network(f"{addr}/64", strict=False))
        return str(addr)

    def who(request: Request) -> dict | None:
        """Khách theo ``X-API-Key``: None (không gửi), ``{"kind":"internal"}`` hoặc ``{"kind":"polar",…}``.

        Key sai → 401 ``invalid_api_key`` (không rơi về quota miễn phí). Kết quả nhớ trong request.
        """
        if hasattr(request.state, "who"):
            return request.state.who
        k = (request.headers.get("x-api-key") or "").strip()

        def limit_validations() -> None:
            rk = f"rl:lk:{today_vn():%Y-%m-%dT%H:%M}:{limit_ip(request)}"
            if store.kv_incr(rk, 1) > KEY_VALIDATE_RATE_PER_MIN:
                raise BillingError("rate_limited", status=429, key="rate_limited.keys")

        res = billing.resolve_api_key(k, on_miss=limit_validations) if k else None
        request.state.who = res
        return res

    def require_key(request: Request) -> dict:
        w = who(request)
        if w is None:
            raise ApiError("api_key_required", status=401, headers={"WWW-Authenticate": 'ApiKey header="X-API-Key"'})
        return w

    def today_vn() -> datetime:
        return datetime.now(VN_TZ)

    def reset_at() -> str:
        now = today_vn()
        nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return nxt.isoformat()

    def quota_key(request: Request) -> str:
        return f"quota:{today_vn():%Y-%m-%d}:ip:{limit_ip(request)}"

    def internal_usage_key() -> str:
        return f"usage:{today_vn():%Y-%m-%d}:apikey"

    def quota_info(request: Request) -> dict:
        w = who(request)
        if w is not None and w["kind"] == "internal":
            return {"used": 0, "limit": None, "remaining": None, "reset_at": reset_at(), "period": "day",
                    "unlimited": True}
        if w is not None:
            u = billing.usage(w)
            return {k: u[k] for k in ("used", "limit", "remaining", "reset_at", "period", "plan")}
        used = int(store.kv_get(quota_key(request)) or 0)
        limit = int(settings.free_daily_chars)
        return {"used": used, "limit": limit, "remaining": max(0, limit - used), "reset_at": reset_at(),
                "period": "day"}

    def charge(request: Request, chars: int) -> tuple[str | None, int]:
        """Trừ quota trước khi dựng (tránh đua); hết → 429. Trả (khoá quota, số ký tự đã trừ).

        Key nội bộ: không giới hạn; key Polar: hạn mức tháng của gói (không đụng quota IP); còn lại: quota IP/ngày.
        """
        w = who(request)
        if w is not None and w["kind"] == "internal":
            store.kv_incr(internal_usage_key(), chars)
            return None, 0
        if w is not None:
            return billing.charge(w, chars), chars
        qk = quota_key(request)
        limit = int(settings.free_daily_chars)
        rpm = int(settings.free_requests_per_min or 0)
        if rpm > 0:
            rk = f"rl:tts:{today_vn():%Y-%m-%dT%H:%M}:{limit_ip(request)}"
            if store.kv_incr(rk, 1) > rpm:
                raise ApiError("rate_limited", status=429, key="rate_limited.free", headers={"Retry-After": "60"})
        new = store.kv_incr(qk, chars)
        cap = int(settings.free_global_daily_chars or 0)
        if new <= limit and cap > 0:
            # Trần chung toàn hệ thống: trừ sau quota IP để người đã hết quota riêng không ăn vào trần chung.
            gk = free_total_key(qk)
            if store.kv_incr(gk, chars) > cap:
                store.kv_incr(gk, -chars)
                store.kv_incr(qk, -chars)
                raise ApiError("quota_exceeded", status=429, key="quota_exceeded.free_capacity")
        if new > limit:
            store.kv_incr(qk, -chars)
            remaining = max(0, limit - (new - chars))
            if remaining <= 0:
                raise ApiError("quota_exceeded", status=429, key="quota_exceeded.free_used_up",
                               params={"limit": limit})
            raise ApiError("quota_exceeded", status=429, key="quota_exceeded.free_partial",
                           params={"remaining": remaining, "chars": int(chars)})
        return qk, chars

    def free_total_key(qk: str) -> str:
        # qk = "quota:<YYYY-MM-DD>:ip:<ip>" → bộ đếm chung của đúng ngày đó (hoàn tiền qua nửa đêm vẫn đúng ngày).
        return f"quota:{qk.split(':', 2)[1]}:free_total"

    def refund(qk: str | None, chars: int) -> None:
        if qk and chars:
            if qk.startswith("quota:") and ":ip:" in qk and int(settings.free_global_daily_chars or 0) > 0:
                store.kv_incr(free_total_key(qk), -int(chars))
            billing.refund(qk, chars)  # key Polar: hoàn cả bucket kỳ lẫn sổ cái lâu dài; IP: chỉ bucket

    # ---------- kết quả ----------
    def public_result(meta: dict, cached: bool, request: Request) -> dict:
        rid, fmt = meta["id"], meta["format"]
        return {
            "id": rid,
            "chars": meta["chars"],
            "duration": meta["duration"],
            "format": fmt,
            "cached": bool(cached),
            "audio_url": f"/api/files/{rid}.{fmt}",
            "srt_url": f"/api/files/{rid}.srt",
            "vtt_url": f"/api/files/{rid}.vtt",
            "segments": meta["segments"],
            "quota": quota_info(request),
        }

    def too_long(chars: int, max_chars: int, mode: str, with_dict: bool = False) -> ApiError:
        # mode: "sync" (/api/tts, gợi ý dùng /api/jobs) | "job" (gợi ý chia nhỏ văn bản)
        return ApiError("too_long", status=413, key=f"too_long.{mode}{'_dict' if with_dict else ''}",
                        params={"chars": int(chars), "max": int(max_chars)})

    def prepare(body: TTSBody, max_chars: int, mode: str) -> tuple[RenderRequest, int]:
        req = body.to_request()
        # Kiểm tra độ dài thô trước mọi xử lý tốn CPU (clean_text duyệt từng ký tự).
        if isinstance(req.text, str) and len(req.text) > max_chars * 2 + 1000:
            raise too_long(len(req.text), max_chars, mode)
        validate(req)
        chars = count_chars(req.text)
        if chars > max_chars:
            raise too_long(chars, max_chars, mode)
        items = plan(req, _VoiceView(renderer))  # giọng lạ… → 400 trước khi trừ quota
        # Từ điển phát âm có thể làm văn bản dài ra nhiều lần → tính phí và giới hạn theo văn bản thực đọc.
        billed = billable_chars(req, items)
        if billed > max_chars:
            raise too_long(billed, max_chars, mode, with_dict=True)
        return req, billed

    def get_voices() -> list[dict]:
        if "v" not in voices_cache:
            voices_cache["v"] = renderer.voices()
        return voices_cache["v"]

    # ---------- endpoint ----------
    @app.get("/api/health")
    def health():
        return {"ok": True, "engine": renderer.engine_name(), "voices": len(get_voices()), "version": __version__}

    @app.get("/api/voices")
    def voices():
        vs = get_voices()
        ids = [v["id"] for v in vs]
        default = DEFAULT_VOICE_ID if DEFAULT_VOICE_ID in ids else (ids[0] if ids else None)
        out = [{**v, "sample_url": f"/api/voices/{v['id']}/sample.mp3"} for v in vs]
        return JSONResponse({"default": default, "voices": out}, headers={"Cache-Control": "public, max-age=300"})

    @app.api_route("/api/voices/{voice_id}/sample.mp3", methods=["GET", "HEAD"])
    def voice_sample(voice_id: str, request: Request):
        vid = voice_id_from_name(voice_id)
        if not vid or vid not in {v["id"] for v in get_voices()}:
            raise ApiError("unknown_voice", status=404, key="unknown_voice.not_found")
        name = sample_blob_name(vid)
        data = store.blob_get(name)
        if data is None:
            with sample_locks_guard:
                lock = sample_locks.setdefault(vid, threading.Lock())
            with lock:  # nhiều người bấm nghe cùng lúc → chỉ tạo một lần
                data = store.blob_get(name)
                if data is None:
                    data = renderer.sample(vid)
                    store.blob_put(name, data)
        return _bytes_response(request, data, CONTENT_TYPES["mp3"],
                               {"Cache-Control": "public, max-age=86400", "ETag": f'"{name}"'})

    @app.post("/api/tts")
    def tts(body: TTSBody, request: Request):
        req, chars = prepare(body, settings.sync_max_chars, "sync")
        meta = load_meta(store, request_key(req, renderer.engine_name()), req.format)
        if meta is None:
            # Cùng văn bản đã có ở định dạng khác → chỉ đổi định dạng, không tính thêm ký tự.
            meta = derive_format(store, req, renderer.engine_name())
        if meta is not None:
            who(request)  # key sai vẫn bị từ chối
            return public_result(meta, True, request)
        qk, charged = charge(request, chars)
        try:
            meta = renderer.render(req)
        except BaseException:
            refund(qk, charged)
            raise
        return public_result(meta, False, request)

    @app.post("/api/jobs", status_code=202)
    def create_job(body: TTSBody, request: Request):
        req, chars = prepare(body, settings.job_max_chars, "job")
        job_id = secrets.token_urlsafe(12)
        now = time.time()
        rec: dict[str, Any] = {"job_id": job_id, "status": "queued", "progress": 0.0, "done": 0, "total": 0,
                               "chars": chars, "created_at": now, "updated_at": now}
        meta = load_meta(store, request_key(req, renderer.engine_name()), req.format)
        if meta is None:
            meta = derive_format(store, req, renderer.engine_name())
        if meta is not None:
            who(request)
            rec.update(status="done", progress=1.0, done=1, total=1, result=meta, cached=True)
            store.kv_set(job_key(job_id), rec)
            return JSONResponse({"job_id": job_id, "status": "done"}, status_code=202)
        qk, charged = charge(request, chars)
        rec.update(quota_key=qk, charged=charged)
        store.kv_set(job_key(job_id), rec)
        try:
            renderer.submit(job_id, req)
        except Exception:
            log.exception("không gửi được job %s", job_id)
            refund(qk, charged)
            err = ApiError("queue_error", status=503)
            rec.update(status="error", charged=0, error=err.to_dict())
            store.kv_set(job_key(job_id), rec)
            raise err
        return JSONResponse({"job_id": job_id, "status": "queued"}, status_code=202)

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str, request: Request):
        rec = store.kv_get(job_key(job_id)) if JOB_ID_RE.fullmatch(job_id) else None
        if not rec:
            raise ApiError("job_not_found", status=404)
        status = rec.get("status")
        # Xếp hàng: chỉ có created_at/updated_at lúc nhận job → dùng ngưỡng dài hơn nhiều (phải chờ job trước).
        # Đang chạy: progress cập nhật updated_at liên tục → ngưỡng ngắn.
        limit = settings.job_queue_stale_seconds if status == "queued" else settings.job_stale_seconds
        if status in ("queued", "running") and time.time() - float(rec.get("updated_at") or 0) > limit:
            rec = store.kv_get(job_key(job_id)) or rec  # đọc lại ngay trước khi ghi
            if rec.get("status") == status and not rec.get("refunded"):
                rec.update(status="error", updated_at=time.time(), error=ApiError("job_stale").to_dict())
                if rec.get("charged"):
                    refund(rec.get("quota_key"), int(rec["charged"]))
                    rec["refunded"] = True
                store.kv_set(job_key(job_id), rec)
        out: dict[str, Any] = {
            "job_id": job_id,
            "status": rec.get("status", "queued"),
            "progress": float(rec.get("progress") or 0.0),
            "done": int(rec.get("done") or 0),
            "total": int(rec.get("total") or 0),
        }
        if out["status"] == "done" and rec.get("result"):
            out["result"] = public_result(rec["result"], bool(rec.get("cached")), request)
        if out["status"] == "error":
            out["error"] = error_body(rec.get("error") or {"code": "engine_error"}, _lang(request))
        return out

    @app.api_route("/api/files/{name}", methods=["GET", "HEAD"])
    def files(name: str, request: Request, download: str | None = None):
        m = FILE_RE.fullmatch(name)
        path_fn = getattr(store, "blob_path", None)
        path = data = None
        if m and path_fn is not None:
            path = path_fn(name)
        elif m:
            data = store.blob_get(name)
        if path is None and data is None:
            raise ApiError("file_not_found", status=404)
        ext = m.group(2)
        headers = {"Cache-Control": IMMUTABLE, "ETag": f'"{name}"'}
        if download not in (None, "", "0", "false"):
            headers["Content-Disposition"] = f'attachment; filename="dattts-{m.group(1)}.{ext}"'
        if path is not None:
            return _file_response(request, path, MEDIA_TYPES[ext], headers)
        return _bytes_response(request, data, MEDIA_TYPES[ext], headers)

    @app.get("/api/quota")
    def quota(request: Request):
        return JSONResponse(quota_info(request), headers={"Cache-Control": "no-store"})

    # ---------- waitlist ----------
    def load_waitlist() -> list[dict]:
        items = store.kv_get(WAITLIST_KEY)
        if items is None:  # KV mất/hết hạn → khôi phục từ bản sao trong blob
            raw = store.blob_get(WAITLIST_BLOB)
            try:
                items = json.loads(raw) if raw else []
            except ValueError:
                items = []
        return list(items or [])

    @app.post("/api/waitlist")
    def waitlist(body: WaitlistBody, request: Request):
        email = body.email.strip().lower()
        if not EMAIL_RE.fullmatch(email):
            raise ApiError("bad_email", status=400)
        with waitlist_lock:
            items = load_waitlist()
            for i, it in enumerate(items):
                if it.get("email") == email:
                    return {"ok": True, "position": i + 1, "already": True}
            items.append({
                "email": email,
                "plan": (body.plan or "").strip() or None,
                "note": (body.note or "").strip() or None,
                "created_at": datetime.now(VN_TZ).isoformat(timespec="seconds"),
                "ip": client_ip(request),
            })
            store.kv_set(WAITLIST_KEY, items)
            try:
                store.blob_put(WAITLIST_BLOB, json.dumps(items, ensure_ascii=False).encode("utf-8"))
            except Exception:  # pragma: no cover
                log.exception("không sao lưu được waitlist")
            return {"ok": True, "position": len(items), "already": False}

    def require_admin(request: Request) -> None:
        if not settings.admin_token:
            raise ApiError("not_found", status=404)
        auth = request.headers.get("authorization") or ""
        token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
        if not hmac.compare_digest(token.encode(), settings.admin_token.encode()):
            raise ApiError("unauthorized", status=401, headers={"WWW-Authenticate": "Bearer"})

    @app.get("/api/admin/waitlist")
    def admin_waitlist(request: Request, format: str | None = None):
        require_admin(request)
        items = load_waitlist()
        if format == "csv":
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=["position", "email", "plan", "note", "created_at", "ip"])
            w.writeheader()
            for i, it in enumerate(items):
                w.writerow({"position": i + 1, **{k: it.get(k) or "" for k in w.fieldnames[1:]}})
            return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                            headers={"Content-Disposition": 'attachment; filename="dattts-waitlist.csv"',
                                     "Cache-Control": "no-store"})
        return JSONResponse({"count": len(items), "items": items}, headers={"Cache-Control": "no-store"})

    # ---------- thanh toán (Polar) ----------
    no_store = {"Cache-Control": "no-store"}

    @app.get("/api/billing/plans")
    def billing_plans(request: Request):
        return JSONResponse(billing.plans(_lang(request)),
                            headers={"Cache-Control": "public, max-age=60", "Vary": "Accept-Language"})

    @app.post("/api/billing/checkout")
    def billing_checkout(body: CheckoutBody, request: Request):
        billing.require_enabled()
        plan = (body.plan or "").strip().lower()
        if plan not in PAID_PLANS:
            raise ApiError("bad_plan", status=400)
        email = (body.email or "").strip().lower() or None
        if email is not None and not EMAIL_RE.fullmatch(email):
            raise ApiError("bad_email", status=400)
        rk = f"rl:checkout:{today_vn():%Y-%m-%dT%H}:{limit_ip(request)}"
        if store.kv_incr(rk, 1) > CHECKOUT_RATE_PER_HOUR:
            raise ApiError("rate_limited", status=429, key="rate_limited.checkout", headers={"Retry-After": "600"})
        return JSONResponse({"url": billing.create_checkout(plan, email)}, headers=no_store)

    @app.get("/api/billing/checkout/{checkout_id}")
    def billing_checkout_status(checkout_id: str):
        return JSONResponse(billing.checkout_status(checkout_id), headers=no_store)

    @app.get("/api/me")
    def me(request: Request):
        w = require_key(request)
        lang = _lang(request)
        if w["kind"] == "internal":
            used = int(store.kv_get(internal_usage_key()) or 0)
            out = {"plan": "internal", "plan_name": plan_name("internal", lang), "used": used, "limit": None,
                   "remaining": None, "reset_at": reset_at(), "period": "day", "email": None}
        else:
            u = billing.usage(w)
            out = {"plan": w["plan"], "plan_name": plan_name(w["plan"], lang), "used": u["used"],
                   "limit": u["limit"], "remaining": u["remaining"], "reset_at": u["reset_at"],
                   "period": u["period"], "email": mask_email(w.get("email"))}
        return JSONResponse(out, headers={**no_store, "Vary": "Accept-Language"})

    @app.post("/api/billing/portal")
    def billing_portal(request: Request):
        billing.require_enabled()
        w = require_key(request)
        return JSONResponse({"url": billing.portal_url(w)}, headers=no_store)

    @app.post(WEBHOOK_PATH, status_code=202)
    async def billing_webhook(request: Request):
        body = await request.body()
        # Store (modal.Dict) là I/O đồng bộ → chạy trong threadpool, không chặn event loop.
        res = await run_in_threadpool(billing.handle_webhook, body, request.headers)
        return JSONResponse(res, status_code=202)

    @app.get("/api/admin/billing")
    def admin_billing(request: Request):
        require_admin(request)
        return JSONResponse(billing.admin_summary(), headers=no_store)

    @app.get("/api/admin/usage")
    def admin_usage(request: Request, license_key_id: str | None = None, customer_id: str | None = None,
                    since: str | None = None):
        """Sổ cái dùng ký tự của một key / một khách (để áp dụng chính sách hoàn tiền "< 10 %")."""
        require_admin(request)
        return JSONResponse(billing.admin_usage((license_key_id or "").strip()[:100] or None,
                                                (customer_id or "").strip()[:100] or None,
                                                (since or "").strip() or None), headers=no_store)

    # /api/* không khớp → lỗi JSON (không rơi xuống trang tĩnh)
    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "HEAD", "PUT", "DELETE", "PATCH"],
                   include_in_schema=False)
    def api_not_found(rest: str):
        raise ApiError("not_found", status=404)

    if settings.web_dir and Path(settings.web_dir).is_dir():
        app.mount("/", StaticFiles(directory=settings.web_dir, html=True), name="web")

    return app
