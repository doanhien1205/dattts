#!/usr/bin/env python
"""Kiểm tra nhanh (smoke test) cấu hình Polar THẬT của DatTTS — chỉ các thao tác an toàn.

Làm gì (không tạo/sửa/xoá sản phẩm, benefit, webhook, khách hàng hay mã giảm giá):
    1. GET /v1/products/{id} cho 3 gói Pro → in tên, chu kỳ, giá; tính "Save X%" như /api/billing/plans.
    2. POST /v1/license-keys/validate với một key GIẢ → phải "không tìm thấy" (404).
    3. (chỉ khi có --checkout) tạo MỘT phiên checkout chưa thanh toán cho pro_month (tự hết hạn),
       in trạng thái và host của URL có phải polar.sh không. Không in URL, token hay bí mật.

Đọc cấu hình từ biến môi trường (như backend): POLAR_ACCESS_TOKEN, POLAR_SERVER, POLAR_ORGANIZATION_ID,
POLAR_PRODUCT_PRO_*, POLAR_BENEFIT_PRO_*, PUBLIC_BASE_URL. Ví dụ:

    set -a; . /đường/dẫn/polar.env; set +a
    backend/.venv/bin/python scripts/polar_smoke.py              # chỉ đọc
    backend/.venv/bin/python scripts/polar_smoke.py --checkout   # + tạo 1 phiên checkout chưa trả tiền

Mã thoát 0 khi mọi kiểm tra đạt, 1 nếu có lỗi.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import urlparse

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from dattts.billing import (  # noqa: E402
    PLAN_INTERVALS,
    SAVE_REFERENCE,
    Billing,
    PolarClient,
    PolarError,
    save_percent,
)
from dattts.settings import PAID_PLANS, Settings  # noqa: E402

FAKE_KEY = "DATTTS-SMOKE-TEST-0000-0000-NOT-A-REAL-KEY"


def _usd(cents: int | None) -> str:
    return "?" if cents is None else f"${cents / 100:.2f}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Smoke test an toàn với Polar thật")
    ap.add_argument("--checkout", action="store_true",
                    help="tạo MỘT phiên checkout (chưa thanh toán, tự hết hạn) cho pro_month")
    a = ap.parse_args(argv)

    s = Settings.from_env()
    ok = True
    print(f"Polar: server={s.polar_server} base={s.polar_base_url} token={'có' if s.polar_access_token else 'THIẾU'} "
          f"org={s.polar_organization_id or 'THIẾU'}")
    if not s.polar_access_token or not s.polar_organization_id:
        print("FAIL thiếu POLAR_ACCESS_TOKEN hoặc POLAR_ORGANIZATION_ID")
        return 1
    if s.billing_missing():
        print("WARN thiếu biến:", ", ".join(s.billing_missing()))
    print(f"billing_enabled={s.billing_enabled}")

    client = PolarClient.from_settings(s)
    billing = Billing(s, store=None, client=client)
    try:
        # 1) Sản phẩm + giá
        prices: dict[str, dict] = {}
        for plan in PAID_PLANS:
            pid = s.polar_product(plan)
            if not pid:
                print(f"FAIL {plan}: thiếu POLAR_PRODUCT_{plan.upper()}")
                ok = False
                continue
            try:
                prod = client.get_product(pid)
            except PolarError as e:
                print(f"FAIL {plan}: GET product lỗi ({e.status})")
                ok = False
                continue
            info = billing._price(plan)
            prices[plan] = info
            benefit_ids = {b.get("id") for b in prod.get("benefits") or [] if isinstance(b, dict)}
            ben_ok = s.polar_benefit(plan) in benefit_ids if benefit_ids else None
            interval_ok = info["interval"] == PLAN_INTERVALS[plan]
            print(f"{'OK  ' if interval_ok and info['price'] else 'FAIL'} {plan}: name={prod.get('name')!r} "
                  f"interval={prod.get('recurring_interval')!r}→{info['interval']} "
                  f"price={info['price']} ({_usd(info['price'])} {info['currency']}) "
                  f"archived={bool(prod.get('is_archived'))} benefit_khớp={ben_ok}")
            ok &= bool(interval_ok and info["price"]) and ben_ok is not False
        for plan, (ref, count) in SAVE_REFERENCE.items():
            if plan in prices and ref in prices:
                pct = save_percent(prices[plan]["price"], prices[ref]["price"], count)
                print(f"     save_percent {plan} (so với {count} × {ref}) = {pct}")

        # 2) Key giả → không tìm thấy
        try:
            v = client.validate_license_key(FAKE_KEY)
            print(f"{'OK  ' if v is None else 'FAIL'} validate key giả → {'không tìm thấy (404)' if v is None else 'HỢP LỆ?!'}")
            ok &= v is None
        except PolarError as e:
            print(f"FAIL validate key giả lỗi ({e.status})")
            ok = False

        # 3) Một phiên checkout (tuỳ chọn)
        if a.checkout:
            pid = s.polar_product("pro_month")
            base = s.public_base
            try:
                co = client.create_checkout(pid, success_url=base + "/checkout-success.html?checkout_id={CHECKOUT_ID}",
                                            return_url=base + "/#pricing", metadata={"plan": "pro_month"})
                host = (urlparse(str(co.get("url") or "")).hostname or "").lower()
                is_polar = host == "polar.sh" or host.endswith(".polar.sh")
                print(f"{'OK  ' if is_polar and co.get('status') == 'open' else 'FAIL'} checkout pro_month: "
                      f"status={co.get('status')} url_host_polar_sh={is_polar}")
                ok &= is_polar and co.get("status") == "open"
            except PolarError as e:
                print(f"FAIL tạo checkout lỗi ({e.status})")
                ok = False
    finally:
        client.close()
    print("KẾT QUẢ:", "ĐẠT" if ok else "CÓ LỖI")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
