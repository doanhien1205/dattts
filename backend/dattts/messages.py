"""Bilingual (English/Vietnamese) user-facing error messages — SPEC Phần 3.

Every ``error.message`` the API returns comes from ``MESSAGES``. The language is chosen per request from
``Accept-Language`` (``vi*`` → Vietnamese, anything else → English; English is the default).

- A *message key* is either an error code (``"bad_speed"``) or ``"<code>.<variant>"`` when one code has
  several wordings (``"quota_exceeded.paid_partial"``). The error code sent to clients is always the part
  before the dot.
- Templates use ``str.format`` placeholders. Parameters are formatted per language before substitution:
  ``int`` → ``1,000,000`` (en) / ``1.000.000`` (vi); ``datetime`` → reset time; the parameters named
  ``plan`` and ``period`` are translated (plan id → plan name, ``"week"`` → "week"/"tuần").
- ``LocalizedError`` is the base of ``ApiError``/``BillingError``/``RenderError``: it carries the code,
  message key, parameters and HTTP status; ``.message`` is the English text (for logs and old callers).
"""

from __future__ import annotations

import string
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

__all__ = [
    "LANGS",
    "DEFAULT_LANG",
    "MESSAGES",
    "PLAN_NAMES",
    "PERIODS",
    "ERROR_CODES",
    "LocalizedError",
    "pick_lang",
    "text",
    "plan_name",
    "fmt_number",
    "fmt_reset",
    "error_body",
    "placeholders",
]

LANGS = ("en", "vi")
DEFAULT_LANG = "en"
VN_TZ = timezone(timedelta(hours=7), "Asia/Ho_Chi_Minh")

PLAN_NAMES: dict[str, dict[str, str]] = {
    "free": {"en": "Free", "vi": "Dùng thử"},
    "pro_week": {"en": "Pro Weekly", "vi": "Pro tuần"},
    "pro_month": {"en": "Pro Monthly", "vi": "Pro tháng"},
    "pro_year": {"en": "Pro Yearly", "vi": "Pro năm"},
    "internal": {"en": "Internal", "vi": "Nội bộ"},
}

# "this <period>" — used inside quota messages
PERIODS: dict[str, dict[str, str]] = {
    "day": {"en": "today", "vi": "hôm nay"},
    "week": {"en": "this week", "vi": "tuần này"},
    "month": {"en": "this month", "vi": "tháng này"},
}

MESSAGES: dict[str, dict[str, str]] = {
    # ── request validation ──
    "empty_text": {
        "en": "Please enter some text to read.",
        "vi": "Bạn chưa nhập văn bản cần đọc.",
    },
    "bad_text": {
        "en": "The text must be a string.",
        "vi": "Văn bản phải là một chuỗi ký tự.",
    },
    "bad_speed": {
        "en": "Speed must be between 0.7× and 1.5×.",
        "vi": "Tốc độ đọc phải nằm trong khoảng 0,7× đến 1,5×.",
    },
    "bad_format": {
        "en": "Unsupported format. Choose MP3, WAV or OGG.",
        "vi": "Định dạng không hỗ trợ. Hãy chọn MP3, WAV hoặc OGG.",
    },
    "bad_dictionary": {
        "en": "Invalid pronunciation dictionary: each entry needs a text “from” and “to”.",
        "vi": "Từ điển phát âm không hợp lệ: mỗi mục cần có “from” và “to” dạng chữ.",
    },
    "bad_dictionary.max_entries": {
        "en": "The pronunciation dictionary can have at most {max} entries.",
        "vi": "Từ điển phát âm tối đa {max} mục.",
    },
    "bad_dictionary.max_len": {
        "en": "Each side of a dictionary entry can be at most {max} characters.",
        "vi": "Mỗi vế trong từ điển tối đa {max} ký tự.",
    },
    "unknown_voice": {
        "en": "Voice “{name}” not found. Try, for example: {examples}.",
        "vi": "Không tìm thấy giọng “{name}”. Bạn có thể dùng, ví dụ: {examples}.",
    },
    "unknown_voice.invalid": {
        "en": "Invalid voice name.",
        "vi": "Tên giọng đọc không hợp lệ.",
    },
    "unknown_voice.none_available": {
        "en": "No voices are available right now.",
        "vi": "Chưa có giọng đọc nào khả dụng.",
    },
    "unknown_voice.not_found": {
        "en": "This voice does not exist.",
        "vi": "Không tìm thấy giọng đọc này.",
    },
    "empty_audio": {
        "en": "The text only contains punctuation — there are no words or numbers to read.",
        "vi": "Văn bản chỉ có dấu câu, không có chữ hay số nào để đọc.",
    },
    "empty_audio.no_output": {
        "en": "Couldn't produce audio from this text. Try rewording it.",
        "vi": "Không tạo được âm thanh từ văn bản này. Bạn thử viết lại câu nhé.",
    },
    "bad_json": {
        "en": "The request body is not valid JSON.",
        "vi": "Dữ liệu gửi lên không phải JSON hợp lệ.",
    },
    "bad_request": {
        "en": "Invalid request.",
        "vi": "Yêu cầu chưa hợp lệ.",
    },
    "bad_request.plan": {
        "en": "Invalid plan name.",
        "vi": "Tên gói không hợp lệ.",
    },
    "bad_request.note": {
        "en": "The note is too long (max 1,000 characters).",
        "vi": "Ghi chú quá dài (tối đa 1.000 ký tự).",
    },
    "bad_email": {
        "en": "That email address doesn't look right. Please check it.",
        "vi": "Email chưa đúng định dạng. Bạn kiểm tra lại giúp nhé.",
    },
    "bad_email.polar": {
        "en": "The payment provider rejected this email (maybe a typo in the domain). Check it, or leave it "
              "empty — Polar will ask for your email on the checkout page.",
        "vi": "Hệ thống thanh toán không nhận email này (có thể tên miền gõ nhầm). Bạn kiểm tra lại email "
              "hoặc để trống — Polar sẽ hỏi email ở trang thanh toán.",
    },
    # ── length ──
    "too_long": {
        "en": "The request body is too large. Each request can contain up to 100,000 characters of text.",
        "vi": "Dữ liệu gửi lên quá lớn. Mỗi lần tạo tối đa văn bản 100.000 ký tự.",
    },
    "too_long.sync": {
        "en": "The text is {chars} characters, over the {max}-character limit. "
              "Use background mode (/api/jobs) for long text.",
        "vi": "Văn bản dài {chars} ký tự, vượt giới hạn {max} ký tự. "
              "Hãy dùng chế độ tạo nền (/api/jobs) cho văn bản dài.",
    },
    "too_long.sync_dict": {
        "en": "The text is {chars} characters (including what the pronunciation dictionary adds), over the "
              "{max}-character limit. Use background mode (/api/jobs) for long text.",
        "vi": "Văn bản dài {chars} ký tự (tính cả phần từ điển phát âm thêm vào), vượt giới hạn {max} ký tự. "
              "Hãy dùng chế độ tạo nền (/api/jobs) cho văn bản dài.",
    },
    "too_long.job": {
        "en": "The text is {chars} characters, over the {max}-character limit. "
              "Background jobs take up to {max} characters — split your text into parts.",
        "vi": "Văn bản dài {chars} ký tự, vượt giới hạn {max} ký tự. "
              "Mỗi lần tạo nền tối đa {max} ký tự, bạn hãy chia văn bản thành nhiều phần.",
    },
    "too_long.job_dict": {
        "en": "The text is {chars} characters (including what the pronunciation dictionary adds), over the "
              "{max}-character limit. Background jobs take up to {max} characters — split your text into parts.",
        "vi": "Văn bản dài {chars} ký tự (tính cả phần từ điển phát âm thêm vào), vượt giới hạn {max} ký tự. "
              "Mỗi lần tạo nền tối đa {max} ký tự, bạn hãy chia văn bản thành nhiều phần.",
    },
    # ── quota ──
    "quota_exceeded": {
        "en": "You've reached your character limit.",
        "vi": "Bạn đã dùng hết hạn mức ký tự.",
    },
    "quota_exceeded.free_used_up": {
        "en": "You've used all {limit} free characters for today. The limit resets at midnight Vietnam "
              "time (UTC+7) — or upgrade to Pro.",
        "vi": "Bạn đã dùng hết {limit} ký tự miễn phí hôm nay. Hạn mức sẽ làm mới lúc 0 giờ (giờ Việt Nam) "
              "— hoặc nâng cấp lên gói Pro.",
    },
    "quota_exceeded.free_partial": {
        "en": "You have {remaining} free characters left today, but this text has {chars}. "
              "Shorten it, come back tomorrow, or upgrade to Pro.",
        "vi": "Hôm nay bạn chỉ còn {remaining} ký tự miễn phí, văn bản này có {chars} ký tự. "
              "Hãy rút gọn, quay lại vào ngày mai hoặc nâng cấp lên gói Pro.",
    },
    "quota_exceeded.paid_used_up": {
        "en": "Your {plan} plan has used all {limit} characters {period}. Your limit resets {reset}.",
        "vi": "Gói {plan} đã dùng hết {limit} ký tự của {period}. Hạn mức sẽ làm mới vào {reset}.",
    },
    "quota_exceeded.paid_partial": {
        "en": "Your {plan} plan has {remaining} characters left {period} (limit {limit}), but this text has "
              "{chars}. Your limit resets {reset}.",
        "vi": "Gói {plan} chỉ còn {remaining} ký tự trong {period} (hạn mức {limit} ký tự), văn bản này có "
              "{chars} ký tự. Hạn mức sẽ làm mới vào {reset}.",
    },
    # ── jobs, files, generic HTTP ──
    "queue_error": {
        "en": "Couldn't queue your request. Please try again in a few minutes.",
        "vi": "Chưa đưa được yêu cầu vào hàng đợi. Bạn thử lại sau ít phút nhé.",
    },
    "job_not_found": {
        "en": "Request not found (it may have expired).",
        "vi": "Không tìm thấy yêu cầu này (có thể đã hết hạn).",
    },
    "job_stale": {
        "en": "The request was interrupted. Please create it again.",
        "vi": "Yêu cầu bị gián đoạn. Bạn tạo lại giúp nhé.",
    },
    "engine_error": {
        "en": "The voice engine hit a problem. Please try again in a few minutes.",
        "vi": "Máy đọc gặp sự cố khi tạo giọng. Bạn thử lại sau ít phút nhé.",
    },
    "file_not_found": {
        "en": "File not found.",
        "vi": "Không tìm thấy tệp này.",
    },
    "not_found": {
        "en": "Not found.",
        "vi": "Không tìm thấy nội dung bạn yêu cầu.",
    },
    "method_not_allowed": {
        "en": "Method not allowed.",
        "vi": "Phương thức này không được hỗ trợ.",
    },
    "http_error": {
        "en": "Invalid request.",
        "vi": "Yêu cầu chưa hợp lệ.",
    },
    "internal_error": {
        "en": "Something went wrong on our side. Please try again in a few minutes.",
        "vi": "Máy chủ đang gặp sự cố. Bạn thử lại sau ít phút nhé.",
    },
    # ── auth ──
    "api_key_required": {
        "en": "Send your API key in the X-API-Key header.",
        "vi": "Bạn cần gửi API key trong header X-API-Key.",
    },
    "unauthorized": {
        "en": "A valid admin token is required.",
        "vi": "Bạn cần mã quản trị hợp lệ.",
    },
    "invalid_api_key": {
        "en": "Invalid API key, or your plan has ended. Check the key in your Polar email or in the "
              "customer portal.",
        "vi": "API key không hợp lệ hoặc gói đã hết hạn. Bạn kiểm tra lại key trong email của Polar "
              "hoặc trong cổng quản lý gói nhé.",
    },
    "rate_limited": {
        "en": "Too many requests. Please wait a moment and try again.",
        "vi": "Bạn gửi quá nhiều yêu cầu. Bạn đợi một lát rồi thử lại nhé.",
    },
    "rate_limited.free": {
        "en": "Too many requests in one minute. Wait a minute and try again — Pro keys have no per-minute limit.",
        "vi": "Bạn tạo quá nhiều lượt trong một phút. Bạn đợi một phút rồi thử lại nhé — key Pro không bị giới hạn này.",
    },
    "quota_exceeded.free_capacity": {
        "en": "The free tier is at capacity for today. It reopens at midnight Vietnam time (UTC+7) — or "
              "upgrade to Pro to keep going now.",
        "vi": "Hôm nay gói miễn phí đã hết lượt chung. Gói sẽ mở lại lúc 0 giờ (giờ Việt Nam) — hoặc nâng cấp "
              "lên gói Pro để dùng tiếp ngay.",
    },
    "rate_limited.keys": {
        "en": "Too many API keys tried in a short time. Wait a minute and try again.",
        "vi": "Bạn thử quá nhiều API key trong thời gian ngắn. Bạn đợi một phút rồi thử lại nhé.",
    },
    "rate_limited.checkout": {
        "en": "Too many checkout sessions in one hour. Please try again in a few minutes.",
        "vi": "Bạn đã tạo quá nhiều phiên thanh toán trong một giờ. Bạn thử lại sau ít phút nhé.",
    },
    # ── billing ──
    "billing_disabled": {
        "en": "Pro is coming soon. Join the waitlist in the Pricing section on dattts.com and we’ll email you when it opens.",
        "vi": "Gói Pro sắp mở bán. Bạn để lại email trong danh sách chờ ở mục Bảng giá trên dattts.com, chúng tôi sẽ báo khi gói mở nhé.",
    },
    "billing_disabled.plan": {
        "en": "This plan isn't on sale yet. Please check back later.",
        "vi": "Gói này chưa mở bán. Bạn quay lại sau nhé.",
    },
    "billing_unavailable": {
        "en": "Payments are temporarily unavailable. Please try again in a few minutes.",
        "vi": "Hệ thống thanh toán đang tạm gián đoạn. Bạn thử lại sau ít phút nhé.",
    },
    "bad_plan": {
        "en": "Invalid plan. Choose Pro Weekly, Pro Monthly or Pro Yearly.",
        "vi": "Gói không hợp lệ. Hãy chọn gói Pro tuần, Pro tháng hoặc Pro năm.",
    },
    "bad_checkout_id": {
        "en": "Invalid checkout ID.",
        "vi": "Mã thanh toán không hợp lệ.",
    },
    "checkout_not_found": {
        "en": "Checkout session not found.",
        "vi": "Không tìm thấy phiên thanh toán này.",
    },
    "not_polar_key": {
        "en": "Only API keys bought through Polar have a customer portal.",
        "vi": "Chỉ API key mua qua Polar mới có cổng quản lý gói.",
    },
    "bad_signature": {
        "en": "Invalid webhook signature.",
        "vi": "Chữ ký webhook không hợp lệ.",
    },
    # ── deployment (SPEC Phần 4) ──
    "direct_access_forbidden": {
        "en": "Direct access to this server is not allowed. Please use https://dattts.com.",
        "vi": "Không được gọi thẳng máy chủ này. Hãy dùng https://dattts.com.",
    },
}

# Every error code the API can send (the part of a message key before the dot).
ERROR_CODES = frozenset(k.split(".", 1)[0] for k in MESSAGES)

_FORMATTER = string.Formatter()


def placeholders(template: str) -> set[str]:
    return {f for _, f, _, _ in _FORMATTER.parse(template) if f}


def pick_lang(accept_language: str | None) -> str:
    """``Accept-Language`` → ``"vi"`` or ``"en"``.

    Languages are ranked by q-value (ties keep header order); the first ``vi*`` or ``en*`` wins.
    Anything else (missing header, ``fr``, ``*``) → English.
    """
    ranked: list[tuple[float, int, str]] = []
    for i, part in enumerate((accept_language or "").split(",")):
        bits = [b.strip() for b in part.split(";")]
        tag = bits[0].lower()
        if not tag:
            continue
        q = 1.0
        for b in bits[1:]:
            if b.lower().startswith("q="):
                try:
                    q = float(b[2:])
                except ValueError:
                    q = 0.0
        if q <= 0:
            continue
        ranked.append((-q, i, tag))
    for _, _, tag in sorted(ranked):
        primary = tag.split("-", 1)[0]
        if primary in LANGS:
            return primary
    return DEFAULT_LANG


def _lang(lang: str | None) -> str:
    return lang if lang in LANGS else DEFAULT_LANG


def fmt_number(n: int, lang: str | None = DEFAULT_LANG) -> str:
    s = f"{int(n):,}"
    return s.replace(",", ".") if _lang(lang) == "vi" else s


_MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def fmt_reset(dt: datetime, lang: str | None = DEFAULT_LANG) -> str:
    """Reset time in Vietnam time: ``on Mon, Nov 9, 2026 at 00:00 (UTC+7)`` / ``0 giờ ngày 09/11/2026 (giờ Việt Nam)``."""
    d = dt.astimezone(VN_TZ) if dt.tzinfo else dt.replace(tzinfo=VN_TZ)
    if _lang(lang) == "vi":
        return f"{d:%H:%M} ngày {d:%d/%m/%Y} (giờ Việt Nam)"
    wd = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[d.weekday()]
    return f"on {wd}, {_MONTHS_EN[d.month - 1]} {d.day}, {d.year} at {d:%H:%M} (UTC+7)"


def plan_name(plan: str | None, lang: str | None = DEFAULT_LANG) -> str:
    names = PLAN_NAMES.get(plan or "")
    return names[_lang(lang)] if names else str(plan or "")


def _fmt_param(name: str, value: Any, lang: str) -> str:
    if name == "plan":
        return plan_name(value, lang)
    if name == "period" and value in PERIODS:
        return PERIODS[value][lang]
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return fmt_number(value, lang)
    if isinstance(value, datetime):
        return fmt_reset(value, lang)
    if isinstance(value, str) and name in ("reset", "reset_at"):
        try:
            return fmt_reset(datetime.fromisoformat(value), lang)
        except ValueError:
            return value
    return str(value)


class _Blank(dict):
    def __missing__(self, key):  # thiếu tham số → để trống thay vì lỗi
        return ""


def text(key: str, lang: str | None = DEFAULT_LANG, params: Mapping[str, Any] | None = None, **kw) -> str:
    """Localized message for ``key``. Unknown ``key.variant`` falls back to the bare code."""
    lang = _lang(lang)
    entry = MESSAGES.get(key) or MESSAGES.get(key.split(".", 1)[0])
    if entry is None:
        entry = MESSAGES["internal_error"]
    vals = {**dict(params or {}), **kw}
    formatted = _Blank({k: _fmt_param(k, v, lang) for k, v in vals.items()})
    return entry[lang].format_map(formatted)


class LocalizedError(Exception):
    """Error with a stable ``code``, a message ``key`` (``MESSAGES``), parameters and an HTTP status.

    Passing an explicit ``message`` without ``key`` keeps that literal text (no translation).
    """

    def __init__(self, code: str, message: str | None = None, status: int = 400, *, key: str | None = None,
                 params: Mapping[str, Any] | None = None, headers: dict | None = None):
        if key is None and message is None:
            key = code
        self.code = code
        self.key = key
        self.params = dict(params or {})
        self.status = status
        self.headers = headers
        self.message = message if message is not None else text(key, DEFAULT_LANG, self.params)
        super().__init__(self.message)

    def localized(self, lang: str | None) -> str:
        if self.key and (self.key in MESSAGES or self.key.split(".", 1)[0] in MESSAGES):
            return text(self.key, lang, self.params)
        return self.message

    def to_dict(self) -> dict:
        """JSON-serialisable form (stored with jobs; re-localized when read)."""
        d = {"code": self.code, "message": self.message}
        if self.key:
            d["key"] = self.key
            if self.params:
                d["params"] = self.params
        return d


def error_body(err: Mapping[str, Any] | None, lang: str | None) -> dict:
    """``{"code","message"[, "key","params"]}`` (as stored) → ``{"code","message"}`` in ``lang``."""
    err = dict(err or {})
    code = str(err.get("code") or "internal_error")
    key = err.get("key")
    if isinstance(key, str) and key:
        params = err.get("params") if isinstance(err.get("params"), dict) else {}
        return {"code": code, "message": text(key, lang, params)}
    if code in MESSAGES:  # bản ghi cũ (chưa có key) → thông điệp chung của mã lỗi
        return {"code": code, "message": text(code, lang)}
    return {"code": code, "message": str(err.get("message") or text("internal_error", lang))}
