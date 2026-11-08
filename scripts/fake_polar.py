#!/usr/bin/env python
"""Polar giả (fake Polar) để chạy thử thanh toán DatTTS ở máy local — KHÔNG gọi polar.sh thật.

Mô phỏng đúng các endpoint Polar mà ``backend/dattts/billing.py`` dùng:

    POST /v1/checkouts/                          tạo phiên thanh toán → 201 {id, url, status:"open", ...}
    GET  /v1/checkouts/{id}                      đọc phiên (status: open|expired|confirmed|succeeded|failed)
    GET  /v1/products/{id}                       sản phẩm + giá (cent, USD, theo tháng)
    POST /v1/license-keys/validate               (cần token) xác thực license key → 200 hoặc 404
    POST /v1/customer-portal/license-keys/validate  (công khai) như trên
    POST /v1/customer-sessions/                  → 201 {customer_portal_url, token, expires_at}

và vài trang/endpoint riêng của bản giả:

    GET  /checkout/{id}          trang "thanh toán" HTML; nút "Thanh toán" (POST /checkout/{id}/pay) đánh dấu
                                 phiên succeeded, cấp license key mới (DATTTS-…) cho khách, gửi webhook
                                 order.paid + subscription.created tới DatTTS (nếu bật), rồi chuyển hướng 303
                                 về success_url với ``{CHECKOUT_ID}`` đã được thay bằng id phiên.
                                 Thêm ``?result=failed`` vào POST để giả lập thẻ bị từ chối.
    GET  /portal?token=…         "cổng khách hàng" HTML (xem key, nút huỷ gói → thu hồi key + gửi webhook)
    POST /_fake/webhook          gửi một webhook ĐÃ KÝ ĐÚNG tới DatTTS. Body JSON:
                                   {"type": "subscription.revoked",          # bắt buộc
                                    "key": "DATTTS-PERSONAL-TEST",           # hoặc "customer_id": "…"
                                    "revoke": true,                          # thu hồi key của khách trước khi gửi
                                    "data": {...},                           # ghi đè/ bổ sung payload.data
                                    "url": "http://…/api/billing/webhook",   # mặc định --webhook-url
                                    "bad_signature": false}                  # true → ký sai (để test 403)
                                 → {"status_code": 202, "body": {...}, "webhook_id": "…"}
    POST /_fake/revoke           {"key": "…"} thu hồi một key (không gửi webhook)
    POST /_fake/reset            đưa mọi thứ về dữ liệu mẫu ban đầu
    GET  /_fake/state            xem toàn bộ trạng thái (khách, key, phiên, webhook đã gửi)
    GET  /_fake/outbox           "hộp thư" — email Polar lẽ ra gửi cho khách (kèm key mới cấp)

Dữ liệu mẫu (cố định, để điền env cho DatTTS — in ra bằng ``--print-env``):
    key DATTTS-PERSONAL-TEST   → gói Cá nhân,        khách an.nguyen@example.com
    key DATTTS-UNLIMITED-TEST  → gói Không giới hạn, khách binh.tran@example.com
    key DATTTS-REVOKED-TEST    → đã thu hồi (validate trả 404)
    sản phẩm Cá nhân $2.99/tháng, Không giới hạn $9.99/tháng

Cách chạy (dùng venv của backend):

    python scripts/fake_polar.py --port 8899 &
    eval "$(python scripts/fake_polar.py --print-env)"      # đặt POLAR_* trỏ vào Polar giả
    cd backend && PUBLIC_BASE_URL=http://127.0.0.1:8765 python server_local.py --fake --port 8765

Rồi mở http://127.0.0.1:8765/#bang-gia → "Mua gói Cá nhân" → trang thanh toán giả → "Thanh toán"
→ quay về /thanh-toan.html báo thành công. Gửi webhook thử:

    curl -s -X POST http://127.0.0.1:8899/_fake/webhook -H 'Content-Type: application/json' \
         -d '{"type":"subscription.revoked","key":"DATTTS-PERSONAL-TEST","revoke":true}'

Chữ ký webhook theo Standard Webhooks: ``webhook-signature: v1,<base64(HMAC-SHA256(key, id.ts.body))>``
với key = base64decode(secret bỏ "whsec_") (bí mật kiểu mới); ``--legacy-secret`` ký bằng utf-8 của cả
chuỗi "whsec_…" như bí mật Polar kiểu cũ.
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import hmac
import html
import json
import secrets
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

# ───────────── Dữ liệu cố định (khớp --print-env) ─────────────
ORG_ID = "0b6c3a52-6f0a-4d7e-9d38-5a1c2f7e9a01"
PRODUCT_PERSONAL = "1f1e0c2a-2b7d-4c55-8a6e-0d0f5d1a7b11"
PRODUCT_UNLIMITED = "2a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c22"
BENEFIT_PERSONAL = "3b3c4d5e-6f70-4b8c-9dae-1f2a3b4c5d33"
BENEFIT_UNLIMITED = "4c4d5e6f-7081-4c9d-aebf-2a3b4c5d6e44"
CUSTOMER_PERSONAL = "5d5e6f70-8192-4dae-bfc0-3b4c5d6e7f55"
CUSTOMER_UNLIMITED = "6e6f7081-92a3-4ebf-c0d1-4c5d6e7f8066"
CUSTOMER_REVOKED = "7f708192-a3b4-4fc0-d1e2-5d6e7f809177"
DEFAULT_TOKEN = "polar_oat_fake_dattts_local"
# 32 byte cố định → bí mật kiểu mới "whsec_<base64>"
DEFAULT_WEBHOOK_SECRET = "whsec_" + base64.b64encode(b"dattts-fake-polar-webhook-key-32").decode()

TEST_KEYS = {
    "DATTTS-PERSONAL-TEST": ("personal", CUSTOMER_PERSONAL, "granted"),
    "DATTTS-UNLIMITED-TEST": ("unlimited", CUSTOMER_UNLIMITED, "granted"),
    "DATTTS-REVOKED-TEST": ("personal", CUSTOMER_REVOKED, "revoked"),
}


def _iso(ts: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if ts is None else ts, timezone.utc).isoformat().replace("+00:00", "Z")


def _seed() -> dict:
    products = {
        PRODUCT_PERSONAL: {"plan": "personal", "name": "DatTTS Cá nhân", "benefit": BENEFIT_PERSONAL, "price": 299},
        PRODUCT_UNLIMITED: {"plan": "unlimited", "name": "DatTTS Không giới hạn", "benefit": BENEFIT_UNLIMITED, "price": 999},
    }
    customers = {
        CUSTOMER_PERSONAL: {"id": CUSTOMER_PERSONAL, "email": "an.nguyen@example.com", "name": "Nguyễn An"},
        CUSTOMER_UNLIMITED: {"id": CUSTOMER_UNLIMITED, "email": "binh.tran@example.com", "name": "Trần Bình"},
        CUSTOMER_REVOKED: {"id": CUSTOMER_REVOKED, "email": "cu.le@example.com", "name": "Lê Cư"},
    }
    keys = {}
    for k, (plan, cid, status) in TEST_KEYS.items():
        pid = PRODUCT_PERSONAL if plan == "personal" else PRODUCT_UNLIMITED
        keys[k] = {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, "fake-polar/" + k)), "key": k, "customer_id": cid,
                   "benefit_id": products[pid]["benefit"], "status": status, "created_at": _iso()}
    return {"products": products, "customers": customers, "keys": keys, "checkouts": {}, "sessions": {},
            "webhooks": [], "outbox": []}


class FakePolar:
    def __init__(self, base_url: str, token: str, webhook_secret: str, webhook_url: str | None,
                 legacy_secret: bool = False, auto_webhooks: bool = True):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.webhook_secret = webhook_secret
        self.webhook_url = webhook_url
        self.legacy_secret = legacy_secret
        self.auto_webhooks = auto_webhooks
        self.lock = threading.RLock()
        self.state = _seed()

    # ---------- tiện ích ----------
    def product_json(self, pid: str) -> dict:
        p = self.state["products"][pid]
        return {
            "id": pid, "name": p["name"], "description": None, "is_recurring": True, "is_archived": False,
            "organization_id": ORG_ID, "recurring_interval": "month", "created_at": _iso(0), "modified_at": None,
            "prices": [{"id": str(uuid.uuid5(uuid.NAMESPACE_URL, "price/" + pid)), "type": "recurring",
                        "amount_type": "fixed", "price_amount": p["price"], "price_currency": "usd",
                        "recurring_interval": "month", "is_archived": False, "product_id": pid}],
            "benefits": [{"id": p["benefit"], "type": "license_keys", "description": "API key DatTTS"}],
            "metadata": {"plan": p["plan"]},
        }

    def key_json(self, k: dict) -> dict:
        c = self.state["customers"][k["customer_id"]]
        return {
            "id": k["id"], "organization_id": ORG_ID, "customer_id": k["customer_id"],
            "customer": {"id": c["id"], "email": c["email"], "name": c["name"], "metadata": {}},
            "benefit_id": k["benefit_id"], "key": k["key"], "display_key": "****-" + k["key"][-6:],
            "status": k["status"], "limit_activations": None, "usage": 0, "limit_usage": None,
            "validations": 0, "last_validated_at": _iso(), "expires_at": None, "activation": None,
        }

    def checkout_json(self, co: dict) -> dict:
        out = {k: v for k, v in co.items() if not k.startswith("_")}
        return copy.deepcopy(out)

    def sign(self, msg_id: str, ts: str, body: bytes, bad: bool = False) -> str:
        if self.legacy_secret:
            key = self.webhook_secret.encode()
        else:
            raw = self.webhook_secret[len("whsec_"):] if self.webhook_secret.startswith("whsec_") else self.webhook_secret
            key = base64.b64decode(raw)
        if bad:
            key = b"wrong-" + key
        mac = hmac.new(key, f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()
        return "v1," + base64.b64encode(mac).decode()

    def send_webhook(self, etype: str, data: dict, url: str | None = None, bad_signature: bool = False) -> dict:
        url = url or self.webhook_url
        payload = {"type": etype, "timestamp": _iso(), "data": data}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        msg_id = "msg_" + secrets.token_hex(12)
        ts = str(int(time.time()))
        headers = {"content-type": "application/json", "webhook-id": msg_id, "webhook-timestamp": ts,
                   "webhook-signature": self.sign(msg_id, ts, body, bad_signature),
                   "user-agent": "fake-polar-webhooks"}
        rec = {"id": msg_id, "type": etype, "url": url, "at": _iso(), "status_code": None, "body": None}
        if not url:
            rec["error"] = "chưa đặt --webhook-url"
        else:
            try:
                r = httpx.post(url, content=body, headers=headers, timeout=10)
                rec["status_code"] = r.status_code
                try:
                    rec["body"] = r.json()
                except ValueError:
                    rec["body"] = r.text[:500]
            except httpx.HTTPError as e:
                rec["error"] = f"{type(e).__name__}: {e}"
        with self.lock:
            self.state["webhooks"].append(rec)
            self.state["webhooks"] = self.state["webhooks"][-200:]
        return {"webhook_id": msg_id, **{k: v for k, v in rec.items() if k != "id"}}

    def subscription_data(self, customer_id: str, status: str, product_id: str | None = None) -> dict:
        c = self.state["customers"][customer_id]
        if product_id is None:
            bens = {k["benefit_id"] for k in self.state["keys"].values() if k["customer_id"] == customer_id}
            product_id = next((pid for pid, p in self.state["products"].items() if p["benefit"] in bens),
                              PRODUCT_PERSONAL)
        p = self.state["products"][product_id]
        return {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"sub/{customer_id}/{product_id}")), "status": status,
                "customer_id": customer_id, "product_id": product_id,
                "customer": {"id": customer_id, "email": c["email"], "name": c["name"]},
                "amount": p["price"], "currency": "usd", "recurring_interval": "month",
                "metadata": {"plan": p["plan"]}, "modified_at": _iso()}

    def revoke_customer(self, customer_id: str) -> int:
        n = 0
        with self.lock:
            for k in self.state["keys"].values():
                if k["customer_id"] == customer_id and k["status"] == "granted":
                    k["status"] = "revoked"
                    n += 1
        return n


def create_app(fp: FakePolar) -> FastAPI:
    app = FastAPI(title="Fake Polar (DatTTS)", docs_url=None, redoc_url=None, openapi_url=None)

    def err(status: int, detail: str, etype: str = "ResourceNotFound") -> JSONResponse:
        return JSONResponse({"error": etype, "detail": detail}, status_code=status)

    def authed(request: Request) -> bool:
        auth = request.headers.get("authorization") or ""
        return hmac.compare_digest(auth.encode(), f"Bearer {fp.token}".encode())

    async def body_json(request: Request) -> dict:
        try:
            d = await request.json()
        except ValueError:
            return {}
        return d if isinstance(d, dict) else {}

    def unauthorized() -> JSONResponse:
        return err(401, "Invalid or missing access token", "Unauthorized")

    # ───────── API Polar ─────────
    @app.post("/v1/checkouts/")
    async def create_checkout(request: Request):
        if not authed(request):
            return unauthorized()
        b = await body_json(request)
        prods = b.get("products") or []
        if not isinstance(prods, list) or len(prods) != 1 or prods[0] not in fp.state["products"]:
            return err(422, "products: phải có đúng một sản phẩm hợp lệ", "RequestValidationError")
        if not b.get("success_url"):
            return err(422, "success_url bắt buộc trong bản giả", "RequestValidationError")
        pid = prods[0]
        cid = str(uuid.uuid4())
        now = time.time()
        co = {
            "id": cid, "url": f"{fp.base_url}/checkout/{cid}", "status": "open",
            "client_secret": "polar_c_" + secrets.token_urlsafe(24), "expires_at": _iso(now + 3600),
            "created_at": _iso(now), "success_url": b["success_url"], "return_url": b.get("return_url"),
            "customer_id": None, "customer_email": b.get("customer_email"), "metadata": b.get("metadata") or {},
            "product_id": pid, "products": [fp.product_json(pid)], "amount": fp.state["products"][pid]["price"],
            "currency": "usd", "organization_id": ORG_ID,
        }
        with fp.lock:
            fp.state["checkouts"][cid] = co
        return JSONResponse(fp.checkout_json(co), status_code=201)

    @app.get("/v1/checkouts/{checkout_id}")
    def get_checkout(checkout_id: str, request: Request):
        if not authed(request):
            return unauthorized()
        co = fp.state["checkouts"].get(checkout_id)
        if not co:
            return err(404, "Checkout not found")
        return fp.checkout_json(co)

    @app.get("/v1/products/{product_id}")
    def get_product(product_id: str, request: Request):
        if not authed(request):
            return unauthorized()
        if product_id not in fp.state["products"]:
            return err(404, "Product not found")
        return fp.product_json(product_id)

    async def validate(request: Request, need_token: bool):
        if need_token and not authed(request):
            return unauthorized()
        b = await body_json(request)
        key, org = b.get("key"), b.get("organization_id")
        if not isinstance(key, str) or not key:
            return err(422, "key bắt buộc", "RequestValidationError")
        if org and org != ORG_ID:
            return err(404, "License key not found")
        k = fp.state["keys"].get(key)
        if not k or k["status"] != "granted":
            return err(404, "License key not found")
        if b.get("benefit_id") and b["benefit_id"] != k["benefit_id"]:
            return err(404, "License key not found")
        return fp.key_json(k)

    @app.post("/v1/license-keys/validate")
    async def validate_server(request: Request):
        return await validate(request, True)

    @app.post("/v1/customer-portal/license-keys/validate")
    async def validate_public(request: Request):
        return await validate(request, False)

    @app.post("/v1/customer-sessions/")
    async def customer_session(request: Request):
        if not authed(request):
            return unauthorized()
        b = await body_json(request)
        cid = b.get("customer_id")
        if cid not in fp.state["customers"]:
            return err(422, "customer_id không tồn tại", "RequestValidationError")
        tok = "polar_cst_" + secrets.token_urlsafe(18)
        with fp.lock:
            fp.state["sessions"][tok] = {"customer_id": cid, "return_url": b.get("return_url")}
        return JSONResponse({"id": str(uuid.uuid4()), "token": tok, "customer_id": cid,
                             "expires_at": _iso(time.time() + 3600),
                             "customer_portal_url": f"{fp.base_url}/portal?customer_session_token={quote(tok)}"},
                            status_code=201)

    # ───────── Trang HTML giả ─────────
    page_css = ("body{font:16px/1.5 system-ui,sans-serif;max-width:560px;margin:40px auto;padding:0 16px;"
                "color:#1b1b1f;background:#f6f6f9}.card{background:#fff;border:1px solid #ddd;border-radius:12px;"
                "padding:24px}button{font:inherit;padding:10px 18px;border-radius:8px;border:0;background:#0062ff;"
                "color:#fff;cursor:pointer}.ghost{background:#eee;color:#222}.warn{color:#a00}code{word-break:break-all}"
                ".tag{display:inline-block;font-size:12px;background:#ffe9a8;padding:2px 8px;border-radius:99px}")

    def page(title: str, inner: str) -> HTMLResponse:
        return HTMLResponse(f"<!doctype html><html lang='vi'><meta charset='utf-8'><meta name='viewport' "
                            f"content='width=device-width,initial-scale=1'><title>{html.escape(title)}</title>"
                            f"<style>{page_css}</style><body><p class='tag'>POLAR GIẢ · chỉ để thử</p>"
                            f"<div class='card'>{inner}</div></body></html>")

    @app.get("/checkout/{checkout_id}")
    def checkout_page(checkout_id: str):
        co = fp.state["checkouts"].get(checkout_id)
        if not co:
            return page("Không tìm thấy", "<h1>Không tìm thấy phiên thanh toán</h1>")
        p = fp.state["products"][co["product_id"]]
        amount = f"${p['price'] / 100:.2f}"
        cancel = (f"<a href='{html.escape(co['return_url'])}' id='fakeCancel'>Quay lại</a>"
                  if co.get("return_url") else "")
        if co["status"] != "open":
            return page("Phiên đã đóng", f"<h1>Phiên này đã {html.escape(co['status'])}</h1>{cancel}")
        email = html.escape(co.get("customer_email") or "khach.moi@example.com")
        return page("Thanh toán " + p["name"], f"""
<h1 id="fakeProduct">{html.escape(p['name'])}</h1>
<p><b id="fakeAmount">{amount}</b> / tháng</p>
<form method="post" action="/checkout/{checkout_id}/pay">
  <p><label>Email <input name="email" id="fakeEmail" value="{email}"></label></p>
  <p><label>Số thẻ <input name="card" value="4242 4242 4242 4242" readonly></label></p>
  <p><button type="submit" id="fakePay">Thanh toán {amount}</button>
     <button type="submit" class="ghost" id="fakeFail" formaction="/checkout/{checkout_id}/pay?result=failed">Giả lập thẻ bị từ chối</button></p>
</form>{cancel}""")

    @app.post("/checkout/{checkout_id}/pay")
    async def pay(checkout_id: str, request: Request, result: str | None = None):
        co = fp.state["checkouts"].get(checkout_id)
        if not co:
            return page("Không tìm thấy", "<h1>Không tìm thấy phiên thanh toán</h1>")
        form = {}
        try:
            raw = (await request.body()).decode()
            from urllib.parse import parse_qs
            form = {k: v[0] for k, v in parse_qs(raw).items()}
        except Exception:
            pass
        email = (form.get("email") or co.get("customer_email") or "khach.moi@example.com").strip().lower()
        new_key = None
        with fp.lock:
            if co["status"] != "open":
                pass
            elif result == "failed":
                co["status"] = "failed"
            else:
                cust = next((c for c in fp.state["customers"].values() if c["email"] == email), None)
                if cust is None:
                    cust = {"id": str(uuid.uuid4()), "email": email, "name": email.split("@")[0]}
                    fp.state["customers"][cust["id"]] = cust
                p = fp.state["products"][co["product_id"]]
                new_key = "DATTTS-" + "-".join(secrets.token_hex(4).upper() for _ in range(3))
                fp.state["keys"][new_key] = {"id": str(uuid.uuid4()), "key": new_key, "customer_id": cust["id"],
                                             "benefit_id": p["benefit"], "status": "granted", "created_at": _iso()}
                co.update(status="succeeded", customer_id=cust["id"], customer_email=email)
                fp.state["outbox"].append({"to": email, "at": _iso(), "subject": f"Your {p['name']} license key",
                                           "key": new_key, "checkout_id": checkout_id})
        if new_key and fp.auto_webhooks and fp.webhook_url:
            cust_id = co["customer_id"]
            sub = fp.subscription_data(cust_id, "active", co["product_id"])

            def fire():
                fp.send_webhook("order.paid", {"id": str(uuid.uuid4()), "status": "paid", "customer_id": cust_id,
                                               "customer": sub["customer"], "product_id": co["product_id"],
                                               "metadata": co["metadata"], "total_amount": sub["amount"],
                                               "currency": "usd", "billing_reason": "subscription_create"})
                fp.send_webhook("subscription.created", sub)
            threading.Thread(target=fire, daemon=True).start()
        target = co["success_url"].replace("{CHECKOUT_ID}", checkout_id)
        return RedirectResponse(target, status_code=303)

    @app.get("/portal")
    def portal(customer_session_token: str = ""):
        s = fp.state["sessions"].get(customer_session_token)
        if not s:
            return page("Phiên hết hạn", "<h1>Phiên cổng khách hàng không hợp lệ</h1>")
        c = fp.state["customers"][s["customer_id"]]
        rows = "".join(
            f"<li><code>{html.escape(k['key'][:10])}…{html.escape(k['key'][-4:])}</code> — {html.escape(k['status'])}</li>"
            for k in fp.state["keys"].values() if k["customer_id"] == c["id"])
        back = f"<p><a href='{html.escape(s['return_url'])}'>Quay lại DatTTS</a></p>" if s.get("return_url") else ""
        return page("Cổng khách hàng", f"""
<h1 id="portalTitle">Cổng khách hàng</h1>
<p id="portalEmail">{html.escape(c['email'])}</p>
<ul>{rows}</ul>
<form method="post" action="/portal/cancel?customer_session_token={quote(customer_session_token)}">
  <button type="submit" class="ghost" id="portalCancel">Huỷ gói ngay (thu hồi key + gửi webhook)</button>
</form>{back}""")

    @app.post("/portal/cancel")
    def portal_cancel(customer_session_token: str = ""):
        s = fp.state["sessions"].get(customer_session_token)
        if not s:
            return page("Phiên hết hạn", "<h1>Phiên cổng khách hàng không hợp lệ</h1>")
        n = fp.revoke_customer(s["customer_id"])
        r = fp.send_webhook("subscription.revoked", fp.subscription_data(s["customer_id"], "canceled"))
        return page("Đã huỷ", f"<h1>Đã huỷ gói</h1><p>Thu hồi {n} key. Webhook: {html.escape(str(r.get('status_code')))}</p>")

    # ───────── Điều khiển bản giả ─────────
    @app.post("/_fake/webhook")
    async def fake_webhook(request: Request):
        b = await body_json(request)
        etype = str(b.get("type") or "").strip()
        if not etype:
            return err(422, "type bắt buộc", "RequestValidationError")
        cid = b.get("customer_id")
        if not cid and b.get("key"):
            k = fp.state["keys"].get(b["key"])
            if not k:
                return err(404, "không có key này trong Polar giả")
            cid = k["customer_id"]
        revoked = 0
        if cid and cid not in fp.state["customers"]:
            return err(404, "không có khách này trong Polar giả")
        if cid and b.get("revoke"):
            revoked = fp.revoke_customer(cid)
        if etype.startswith("customer.") and cid:
            c = fp.state["customers"][cid]
            data = {"id": cid, "email": c["email"], "name": c["name"], "active_subscriptions": [],
                    "granted_benefits": []}
        elif cid:
            status = "canceled" if etype in ("subscription.revoked", "subscription.canceled") else "active"
            data = fp.subscription_data(cid, status)
        else:
            data = {}
        if isinstance(b.get("data"), dict):
            data.update(b["data"])
        res = fp.send_webhook(etype, data, url=b.get("url"), bad_signature=bool(b.get("bad_signature")))
        res["revoked_keys"] = revoked
        return res

    @app.post("/_fake/revoke")
    async def fake_revoke(request: Request):
        b = await body_json(request)
        k = fp.state["keys"].get(b.get("key"))
        if not k:
            return err(404, "không có key này")
        k["status"] = "revoked"
        return {"ok": True, "key": k["key"], "status": k["status"]}

    @app.post("/_fake/reset")
    def fake_reset():
        with fp.lock:
            fp.state = _seed()
        return {"ok": True}

    @app.get("/_fake/state")
    def fake_state():
        return fp.state

    @app.get("/_fake/outbox")
    def fake_outbox():
        return {"emails": fp.state["outbox"]}

    @app.get("/")
    def root():
        return {"fake_polar": True, "organization_id": ORG_ID, "docs": "xem docstring scripts/fake_polar.py"}

    return app


def print_env(port: int, token: str, secret: str) -> None:
    env = {
        "POLAR_SERVER": "sandbox",
        "POLAR_API_BASE": f"http://127.0.0.1:{port}",
        "POLAR_ACCESS_TOKEN": token,
        "POLAR_ORGANIZATION_ID": ORG_ID,
        "POLAR_WEBHOOK_SECRET": secret,
        "POLAR_PRODUCT_PERSONAL": PRODUCT_PERSONAL,
        "POLAR_PRODUCT_UNLIMITED": PRODUCT_UNLIMITED,
        "POLAR_BENEFIT_PERSONAL": BENEFIT_PERSONAL,
        "POLAR_BENEFIT_UNLIMITED": BENEFIT_UNLIMITED,
    }
    for k, v in env.items():
        print(f"export {k}='{v}'")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Polar giả cho DatTTS (chỉ để thử ở máy local)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--base-url", default=None, help="URL công khai của Polar giả (mặc định http://host:port)")
    ap.add_argument("--token", default=DEFAULT_TOKEN, help="Organization Access Token giả mà DatTTS phải gửi")
    ap.add_argument("--webhook-secret", default=DEFAULT_WEBHOOK_SECRET)
    ap.add_argument("--legacy-secret", action="store_true", help="ký webhook kiểu Polar cũ (utf-8 của cả chuỗi whsec_…)")
    ap.add_argument("--webhook-url", default="http://127.0.0.1:8765/api/billing/webhook",
                    help="nơi nhận webhook (rỗng = không gửi)")
    ap.add_argument("--no-auto-webhooks", action="store_true", help="không tự gửi order.paid/subscription.created khi trả tiền")
    ap.add_argument("--print-env", action="store_true", help="in các lệnh export POLAR_* cho DatTTS rồi thoát")
    a = ap.parse_args(argv)
    if a.print_env:
        print_env(a.port, a.token, a.webhook_secret)
        return
    base = a.base_url or f"http://{a.host}:{a.port}"
    fp = FakePolar(base, a.token, a.webhook_secret, a.webhook_url or None, a.legacy_secret, not a.no_auto_webhooks)
    print(f"Polar giả đang chạy tại {base} (tổ chức {ORG_ID})", flush=True)
    uvicorn.run(create_app(fp), host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
