#!/usr/bin/env python3
"""Kiểm tra web/_headers có đủ hash CSP cho mọi script inline trong web/*.html.

    python3 scripts/csp_hashes.py          # thoát mã 1 và in hash còn thiếu nếu lệch
"""
import base64
import glob
import hashlib
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
want = set()
for f in sorted(glob.glob(os.path.join(ROOT, "web", "*.html"))):
    html = open(f, encoding="utf-8").read()
    for m in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S):
        want.add("'sha256-" + base64.b64encode(hashlib.sha256(m.group(1).encode()).digest()).decode() + "'")
    if re.search(r"\son[a-z]+=\"", html):
        sys.exit(f"{f}: có trình xử lý sự kiện inline (onclick=…), CSP sẽ chặn")
have = set(re.findall(r"'sha256-[^']+'", open(os.path.join(ROOT, "web", "_headers"), encoding="utf-8").read()))
missing, extra = sorted(want - have), sorted(have - want)
if missing or extra:
    print("Thiếu trong web/_headers:", " ".join(missing) or "-")
    print("Thừa trong web/_headers:", " ".join(extra) or "-")
    sys.exit(1)
print(f"OK: {len(want)} hash khớp")
