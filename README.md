# DatTTS — chuyển văn bản thành giọng nói tiếng Việt

DatTTS ([dattts.com](https://dattts.com)) là dịch vụ đọc văn bản tiếng Việt thành giọng nói tự nhiên, đủ dấu hỏi dấu ngã. Bạn dán văn bản, chọn một trong 25 giọng Bắc, Trung, Nam, rồi nhận file MP3, WAV hoặc OGG kèm phụ đề SRT và VTT.

- **Engine:** [VieNeu-TTS v3 Turbo](https://huggingface.co/pnnbao-ump/VieNeu-TTS-v3-Turbo) chạy bằng ONNX trên CPU. GPU là tuỳ chọn. Âm thanh 48 kHz.
- **Nhiều giọng trong một bài:** gõ `[Trúc Ly] Chào anh. [Thiện Minh] Chào em.` để mỗi đoạn đọc bằng một giọng.
- **Karaoke theo câu:** câu đang đọc được tô sáng, bấm vào câu nào thì nhảy tới câu đó.
- **Từ điển phát âm:** tự đặt cách đọc cho từ viết tắt hoặc tên riêng.
- **Bộ luật đọc sẵn:** xử lý những chỗ mô hình hay đọc sai, ví dụ `Q3/2026`, `NĐ-CP`, `USD`, `150k` hay `TP.HN`.
- **Văn bản dài** (tới 100.000 ký tự) chạy nền và có thanh tiến độ.
- **Song ngữ, hướng ra thế giới:** giao diện mặc định tiếng Anh ("The Vietnamese voice studio"), có nút chuyển **EN | VI**; thông báo lỗi của API theo `Accept-Language`. Bản giới thiệu tiếng Anh: [README.en.md](README.en.md).
- **Miễn phí** 20.000 ký tự mỗi ngày cho mỗi địa chỉ IP. Gói **Pro** bán qua Polar theo tuần / tháng / năm (xem [Gói và giá](#gói-và-giá)).

## Gói và giá

| Gói (plan id) | Giá | Hạn mức | Sản phẩm Polar (production) | Benefit License Key (production) |
|---|---|---|---|---|
| `free` | $0 | 20.000 ký tự / ngày / IP | — | — |
| `pro_week` | $1.99 / tuần | 250.000 ký tự / tuần (tuần ISO, reset thứ Hai 00:00 giờ VN) | `3d54ccce-5b59-4903-a23b-18fd8c9e6fe1` | `e02132d4-8b7c-4779-a271-cf778cada689` |
| `pro_month` · "Most popular" · "Save 37%" | $4.99 / tháng | 1.000.000 ký tự / tháng | `5cdb0d3a-edf1-46aa-ba4f-b1e4303e60bf` | `de10e5c4-5357-4ec8-81ca-5b2848226ac0` |
| `pro_year` · "Save 16%" | $49.99 / năm | 1.000.000 ký tự / **tháng** | `673f4798-5c0b-4a6f-a6bc-002054bcd621` | `170cee60-1cd2-428e-8e46-a50e753189fb` |

Tổ chức Polar "Dat TTS": `cd16dd37-dd25-4e92-aaaa-23509784d129`. Ba gói Pro có cùng tính năng, chỉ khác chu kỳ. Giá và "Save X%" đọc trực tiếp từ Polar (không hard-code); phần trăm **làm tròn xuống** để không bao giờ quảng cáo cao hơn mức tiết kiệm thật (49,99 so với 12 × 4,99 = 16,52 % → "Save 16%"). Sản phẩm bị lưu trữ (archived) hoặc không có giá cố định trên Polar → gói đó tự hiện "không bán" và không tạo checkout. Biến môi trường tương ứng: `POLAR_PRODUCT_PRO_WEEK|MONTH|YEAR`, `POLAR_BENEFIT_PRO_WEEK|MONTH|YEAR`, `PLAN_PRO_WEEK_CHARS`, `PLAN_PRO_MONTH_CHARS`, `PLAN_PRO_YEAR_MONTHLY_CHARS` (mẫu đầy đủ: `backend/.env.example`). Các gói cũ "Cá nhân"/"Không giới hạn" và biến `POLAR_*_PERSONAL/UNLIMITED`, `PLAN_PERSONAL_*/PLAN_UNLIMITED_*` đã bỏ hẳn.

## Kiến trúc

```
                   Trình duyệt (web/: index.html, app.js, styles.css, config.js)
                                 │  fetch /api/*   (config.js: DATTTS_API = "" — cùng nguồn dattts.com)
                                 ▼
          Production: Cloudflare Pages Function functions/api/[[path]].js (proxy → URL modal.run)
                                 ▼
 ┌──────────────────────── dattts/api.py — FastAPI (create_app) ───────────────────────┐
 │  /api/tts  /api/jobs  /api/voices  /api/files  /api/quota  /api/waitlist   +  "/"   │
 │  kiểm tra đầu vào · quota theo IP / API key · cache theo request_key · CORS         │
 └───────────────┬───────────────────────────────────────────────┬─────────────────────┘
                 │ Renderer                                       │ Store (KV + Blob)
                 ▼                                                ▼
  Local:  LocalRenderer (ThreadPoolExecutor 1)        Local:  LocalStore  (.data/kv.json, .data/files/)
  Modal:  ModalRenderer → TTSWorker.remote/.spawn     Modal:  ModalStore  (Dict dattts-kv, Volume dattts-files)
                 │
                 ▼
  dattts/render.py:  văn bản → parse_script (nhiều giọng) → từ điển + luật đọc → tách câu
                     → engine.synth từng câu → tốc độ → cắt lặng → ghép (khoảng nghỉ 0,12/0,3/0,6 s)
                     → audio.encode (ffmpeg: mp3/wav/ogg) + phụ đề SRT/VTT
                 │
                 ▼
  dattts/engine.py:  VieNeuEngine (vieneu 3.8.3, ONNX int8; GPU: PyTorch)  |  FakeEngine (để test)
```

Mã nguồn:

| Đường dẫn | Vai trò |
|---|---|
| `backend/dattts/textproc.py` | Chuẩn hoá văn bản, cú pháp nhiều giọng, từ điển, luật đọc, tách câu |
| `backend/dattts/audio.py` | Đổi tốc độ, mã hoá bằng ffmpeg, phụ đề SRT/VTT, cắt khoảng lặng |
| `backend/dattts/engine.py` | `VieNeuEngine`, `FakeEngine`, danh sách giọng |
| `backend/dattts/render.py` | Điều phối toàn bộ quá trình dựng một bản đọc |
| `backend/dattts/store.py` | `LocalStore`, `ModalStore`, `LocalRenderer`, chạy job |
| `backend/dattts/api.py` | HTTP API |
| `backend/server_local.py` | Chạy máy chủ ở máy local |
| `backend/modal_app.py` | Triển khai lên Modal |
| `functions/api/[[path]].js` | Cloudflare Pages Function: proxy `/api/*` từ dattts.com sang Modal |
| `scripts/deploy_modal.sh`, `scripts/deploy_pages.sh` | Script deploy Modal / Cloudflare Pages (`--help`) |
| `deploy/secret.env.example` | Mẫu đầy đủ biến của Modal secret `dattts` |
| `backend/dattts/billing.py` | Polar: checkout, xác thực License Key, quota theo tuần/tháng, webhook |
| `backend/dattts/messages.py` | Bảng thông báo lỗi tiếng Anh / tiếng Việt |
| `web/` | Trang tĩnh: studio, bảng giá, danh sách chờ, `checkout-success.html`, trang pháp lý EN (`terms/privacy/refund.html`) và VI (`dieu-khoan/bao-mat/hoan-tien.html`); chuỗi giao diện trong `web/i18n.js` |
| `scripts/e2e.mjs` | Kiểm thử đầu-cuối bằng Playwright với máy chủ thật |
| `scripts/e2e_billing.mjs` | Kiểm thử đầu-cuối phần thanh toán với Polar giả |
| `scripts/fake_polar.py` | Polar giả để chạy thử thanh toán ở máy local |
| `scripts/polar_smoke.py` | Kiểm tra nhanh cấu hình Polar thật (chỉ đọc) |

## Chạy ở máy local

Cần Python 3.12 hoặc 3.13 và `ffmpeg` có trong `PATH`.

```bash
git clone <repo> dattts && cd dattts
python3 -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements.txt

cd backend
# Lần đầu, mô hình (khoảng vài trăm MB) tự tải từ Hugging Face về DATTTS_MODEL_DIR.
export DATTTS_MODEL_DIR=$PWD/../models
python server_local.py              # http://127.0.0.1:8000
```

Mở <http://127.0.0.1:8000>. Trang web được phục vụ ở `/` và API ở `/api`.

Các tuỳ chọn của `server_local.py`:

| Tuỳ chọn | Ý nghĩa |
|---|---|
| `--fake` | Dùng engine giả (tiếng bíp). Khởi động tức thì, hợp để làm giao diện. |
| `--port 8000` / `--host 127.0.0.1` | Cổng và địa chỉ lắng nghe |
| `--data-dir ./.data` | Nơi lưu file âm thanh, quota và danh sách chờ |
| `--model-dir DIR` | Thư mục mô hình (mặc định lấy `$DATTTS_MODEL_DIR`) |
| `--threads N` | Số luồng ONNX (0 là tự chọn) |
| `--no-warmup` | Không nạp mô hình trước, chờ tới request đầu tiên mới nạp |

Biến môi trường (xem `backend/dattts/settings.py`):

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `FREE_DAILY_CHARS` | `20000` | Số ký tự miễn phí mỗi ngày cho mỗi IP (tính lại lúc nửa đêm giờ Việt Nam) |
| `FREE_GLOBAL_DAILY_CHARS` | `1000000` | Trần **tổng** ký tự miễn phí mỗi ngày của cả hệ thống (chặn hoá đơn máy khi bị lạm dụng bằng nhiều IP; khoảng $1,3 tiền máy/ngày ở mức mặc định). `0` = không giới hạn. Không ảnh hưởng khách Pro. |
| `FREE_REQUESTS_PER_MIN` | `12` | Số lượt tạo giọng mỗi phút cho mỗi IP không có key. `0` = tắt (đặt `0` khi chạy `scripts/e2e*.mjs`). |
| `FILE_RETENTION_DAYS` | `30` | Tự xoá file âm thanh/phụ đề sau N ngày (hàm Modal `purge_old_files` chạy mỗi ngày; local dọn lúc khởi động). `0` = giữ mãi — khi đó phải sửa lại trang Bảo mật. |
| `SYNC_MAX_CHARS` | `3000` | Giới hạn của `/api/tts`. Dài hơn thì dùng `/api/jobs`. |
| `JOB_MAX_CHARS` | `100000` | Giới hạn của một job |
| `API_KEYS` | trống | Key nội bộ không giới hạn, cách nhau bằng dấu phẩy (khách trả phí dùng License Key của Polar, không cần khai ở đây) |
| `ADMIN_TOKEN` | trống | Bật `/api/admin/waitlist`, `/api/admin/billing` và `/api/admin/usage`. Để trống thì các endpoint này trả 404. |
| `TRUST_PROXY` | `0` | Đặt `1` để lấy IP từ `X-Forwarded-For` khi chạy sau proxy (lấy phần tử do proxy thêm vào, tính từ phải) |
| `TRUST_PROXY_HOPS` | `1` | Số proxy tin cậy đứng trước app; IP khách là phần tử thứ N tính từ phải của `X-Forwarded-For` |
| `JOB_QUEUE_STALE_SECONDS` | `14400` | Job còn xếp hàng quá lâu (giây) thì coi như mất và hoàn ký tự |
| `ALLOWED_ORIGINS` | `https://dattts.com,https://www.dattts.com,http://localhost:8000,http://127.0.0.1:8000` | Danh sách CORS |
| `DATTTS_MODEL_DIR` | `./models` | Thư mục mô hình |
| `DATTTS_GPU` | trống | Đặt giá trị (ví dụ `T4`) để chạy engine trên GPU |
| `WEB_DIR` | `<repo>/web` | Thư mục trang tĩnh |
| `PUBLIC_BASE_URL` | `https://dattts.com` | Địa chỉ công khai của trang, dùng làm `success_url`/`return_url` cho Polar. Local: `http://127.0.0.1:8765` |
| `POLAR_SERVER` | `sandbox` | `sandbox` → `https://sandbox-api.polar.sh`, `production` → `https://api.polar.sh` |
| `POLAR_API_BASE` | trống | Ghi đè base URL của Polar (dùng với Polar giả: `http://127.0.0.1:8899`) |
| `POLAR_ACCESS_TOKEN` | trống | Organization Access Token. **Để trống thì billing tắt**: web hiện "Coming soon" (VI: "Sắp mở bán"), endpoint billing trả 503 `billing_disabled`. Billing chỉ bật khi có cả token, `POLAR_ORGANIZATION_ID` và ít nhất một gói đủ sản phẩm + benefit; thiếu biến nào thì log khi khởi động ghi `Cấu hình Polar thiếu …` |
| `POLAR_ORGANIZATION_ID` | trống | UUID tổ chức Polar. **Bắt buộc** (Polar cần khi xác thực key); thiếu thì billing tắt |
| `POLAR_WEBHOOK_SECRET` | trống | Bí mật webhook `whsec_...` |
| `POLAR_PRODUCT_PRO_WEEK` / `_MONTH` / `_YEAR` | trống | UUID sản phẩm gói Pro tuần / tháng / năm (ID production có sẵn trong `backend/.env.example`) |
| `POLAR_BENEFIT_PRO_WEEK` / `_MONTH` / `_YEAR` | trống | UUID benefit License Keys của từng gói (từ đây suy ra gói của một key). Gói thiếu sản phẩm hoặc benefit thì không bán (`available: false`), các gói còn lại vẫn bán |
| `PLAN_PRO_WEEK_CHARS` | `250000` | Hạn mức gói Pro tuần, tính theo tuần ISO giờ VN (reset thứ Hai 00:00 UTC+7) |
| `PLAN_PRO_MONTH_CHARS` | `1000000` | Hạn mức tháng gói Pro tháng |
| `PLAN_PRO_YEAR_MONTHLY_CHARS` | `1000000` | Hạn mức **mỗi tháng** của gói Pro năm |

Mẫu đầy đủ (không có bí mật): `backend/.env.example`. Thông báo lỗi của API có hai thứ tiếng: header `Accept-Language` bắt đầu bằng `vi` → tiếng Việt, còn lại → tiếng Anh (mặc định). Bảng thông điệp nằm ở `backend/dattts/messages.py`.

Trên máy 4 CPU, mô hình nạp trong khoảng 3–6 giây. Tốc độ tạo nhanh hơn thời gian thực khoảng 1,5–2,5 lần: đoạn 180 ký tự mất khoảng 7 giây, còn một job 3.300 ký tự (khoảng 3 phút âm thanh) mất khoảng 75 giây.

## Kiểm thử

**Unit test và test tích hợp (pytest)**

```bash
cd backend
pip install -r requirements-dev.txt
pytest -q                    # toàn bộ, gồm cả test dùng mô hình thật (cần DATTTS_MODEL_DIR)
pytest -q -m "not real"      # chỉ test nhanh với FakeEngine
pytest -q -m real            # chỉ test với mô hình thật
```

**Proxy Cloudflare Pages (Node 22, không cần wrangler/mạng)**

```bash
node scripts/test_proxy.mjs                 # chạy functions/api/[[path]].js với máy chủ echo tại chỗ
bash -n scripts/deploy_modal.sh scripts/deploy_pages.sh && shellcheck scripts/*.sh   # nếu có shellcheck
```

Chạy thử cả chuỗi qua runtime Cloudflare thật ở máy local (`wrangler pages dev`):

```bash
S=$(python -c 'import secrets;print(secrets.token_urlsafe(36))')
(cd backend && PROXY_SHARED_SECRET=$S REQUIRE_PROXY=1 python server_local.py --port 8765 &)
(umask 077; printf 'MODAL_ORIGIN=http://127.0.0.1:8765\nPROXY_SHARED_SECRET=%s\n' "$S" > .dev.vars)   # quyền 600; đã nằm trong .gitignore
npx wrangler@4.151.3 pages dev web --port 8788       # mở http://127.0.0.1:8788
E2E_BASE=http://127.0.0.1:8788 node scripts/e2e.mjs  # toàn bộ giao diện đi qua proxy
rm .dev.vars                                          # xong thì xoá
```

**Kiểm thử đầu-cuối (Playwright, máy chủ thật)**

```bash
cd backend && python server_local.py --port 8765 &      # đợi in "Uvicorn running"
cd .. && npm i -D playwright && npx playwright install chromium   # nếu máy chưa có
node scripts/e2e.mjs                                    # E2E_BASE=http://127.0.0.1:8765 (mặc định)
```

Bộ e2e chạy trên máy chủ thật và kiểm tra các việc sau:

- trang tải mà không có lỗi console, mặc định tiếng Anh, đủ 25 giọng, nghe được giọng mẫu;
- nút EN | VI đổi ngôn ngữ tại chỗ (số kiểu 1,000 / 1.000, header `Accept-Language` theo ngôn ngữ đang chọn), `?lang=`, trình duyệt `vi-VN` tự sang tiếng Việt, neo cũ `#bang-gia` chuyển sang `#pricing`;
- tạo giọng cho văn bản có số và tiền tệ, sau đó tua để kiểm tra karaoke tô đúng câu;
- tải MP3, SRT, VTT và WAV;
- đoạn nhiều giọng, từ điển phát âm, văn bản dài chạy qua job có tiến độ;
- quota giảm sau khi tạo, đăng ký danh sách chờ;
- màn hình 390 px không bị tràn ngang, chế độ tối hiển thị đúng.

Các biến tuỳ chọn:

- `E2E_OUT=/đường/dẫn.json`: ghi lại id các file đã tạo.
- `E2E_SHOTS=/thư/mục`: lưu ảnh chụp màn hình.

`e2e.mjs` chạy với billing tắt (không đặt `POLAR_ACCESS_TOKEN`): nút "Coming soon" đưa tới form chờ. Nếu billing bật, bước danh sách chờ chỉ kiểm tra nút "Get Pro".

**Kiểm thử đầu-cuối phần thanh toán (Polar giả, không cần tài khoản Polar)**

```bash
PYTHON=$PWD/.venv/bin/python DATTTS_MODEL_DIR=$PWD/models node scripts/e2e_billing.mjs   # E2E_REAL=0 để bỏ giai đoạn engine thật
```

Script tự bật Polar giả (cổng 8899) và `server_local.py` (cổng 8765) với dữ liệu tạm, nên hai cổng này phải đang trống. Nó chạy ba giai đoạn rồi tự tắt máy chủ:

1. **Billing bật, engine giả.** Kiểm tra:
   - bảng giá với nút Weekly / Monthly / Yearly hiện $1.99 / $4.99 / $49.99 lấy từ Polar (giả), nhãn "Most popular", "Save 37%", "Save 16%"; chuyển sang VI thì hiện "4,99 US$" kèm quy đổi VND tham khảo;
   - mua **từng** chu kỳ: "Get Pro" → trang thanh toán giả → `checkout-success.html` báo thành công (EN, đổi sang VI tại chỗ) → dán key → phòng thu hiện đúng gói (Pro Weekly / Monthly / Yearly) và hạn mức đúng kỳ (250,000/tuần hoặc 1,000,000/tháng);
   - tạo giọng trừ vào đúng bucket quota (`usage:<key>:<năm>-W<tuần>` cho gói tuần, `usage:<key>:<năm-tháng>` cho gói tháng/năm), còn quota IP miễn phí giữ nguyên;
   - "Manage plan" mở cổng khách hàng; key đã thu hồi bị báo lỗi và gỡ khỏi trình duyệt;
   - webhook `subscription.revoked` làm key ngừng chạy ngay, còn webhook ký sai bị trả 403;
   - thông báo lỗi tiếng Anh / tiếng Việt theo `Accept-Language` (API và trong giao diện);
   - trang pháp lý EN ↔ VI, `/thanh-toan.html` cũ chuyển sang `/checkout-success.html` giữ nguyên query.
2. **Billing bật, engine VieNeu thật.** Gọi `/api/tts` một lần bằng key Pro năm. Đặt `E2E_REAL=0` để bỏ qua giai đoạn này.
3. **Billing tắt.** Kiểm tra trang hiện "Coming soon", form chờ vẫn dùng được, checkout trả 503 (thông báo EN/VI).

## Triển khai lên Modal + Cloudflare

Gói **Modal Starter** (miễn phí, chỉ trả tiền máy chạy) **không** gắn được tên miền riêng — tính năng đó cần gói Team ($250/tháng). Vì vậy DatTTS chạy như sau:

```
Trình duyệt / webhook Polar ──► https://dattts.com  (Cloudflare Pages, miễn phí)
   ├─ trang tĩnh: thư mục web/
   └─ /api/*  ──► Pages Function functions/api/[[path]].js
                    ──► https://<workspace>--dattts-web.modal.run/api/*   (Modal: FastAPI + worker đọc giọng)
```

- **Cloudflare Pages** phục vụ trang `web/` và chuyển nguyên mọi request `/api/*` (cả body thô của webhook Polar, tua audio `Range`/206) sang Modal. Pages Function thêm hai header: `X-DatTTS-Proxy-Secret` (mật khẩu chung) và `X-DatTTS-Client-IP` (IP thật của khách, lấy từ Cloudflare). Header `X-DatTTS-*` do khách tự gửi bị xoá trước.
- **Modal** chỉ tin `X-DatTTS-Client-IP` khi mật khẩu chung đúng (so sánh hằng thời gian), nên quota miễn phí theo IP vẫn đúng. Với `REQUIRE_PROXY=1`, mọi `/api/*` gọi thẳng URL `modal.run` bị chặn **403 `direct_access_forbidden`** (trừ `/api/health`), nên không ai vượt quota bằng cách gọi thẳng Modal.
- Trang và API cùng nguồn `https://dattts.com`, nên `web/config.js` giữ nguyên `DATTTS_API = ""`.

`backend/modal_app.py` gồm:

- **`TTSWorker`:** 4 CPU, 6 GB RAM, nạp mô hình một lần cho mỗi container. Tắt sau 5 phút không có việc (`scaledown_window=300`).
- **`TTSJobWorker`:** chạy job văn bản dài, mỗi container một job.
- **`web`:** FastAPI cùng trang tĩnh, mỗi container nhận tối đa 50 request đồng thời. Đọc cấu hình từ secret `dattts`.
- **Volume `dattts-models`:** chứa mô hình. **Volume `dattts-files`:** chứa file âm thanh và phụ đề. **Dict `dattts-kv`:** quota, job, danh sách chờ.

### Cần chuẩn bị

- Tài khoản [Modal](https://modal.com) (gói Starter) và Python 3.10+ trên máy bạn.
- Tài khoản [Cloudflare](https://dash.cloudflare.com) (miễn phí) và Node.js 18+ (để chạy `npx wrangler`).
- Tên miền `dattts.com`.
- `curl`, `openssl` (có sẵn trên macOS/Linux).

### Bước 1. Tạo file secret (ngoài repo)

```bash
cp deploy/secret.env.example ~/dattts-secret.env
chmod 600 ~/dattts-secret.env
openssl rand -hex 32     # → dán vào PROXY_SHARED_SECRET
openssl rand -hex 24     # → dán vào ADMIN_TOKEN
nano ~/dattts-secret.env # điền mọi giá trị <...> (Polar: xem "Thanh toán với Polar", bước 5–7)
```

File này chứa bí mật: **không** để trong thư mục repo, **không** gửi qua chat. Script deploy từ chối file nằm trong repo, file có quyền rộng hơn `600`, hoặc còn giá trị mẫu `<...>`. Chưa muốn bán hàng thì để trống `POLAR_ACCESS_TOKEN` và `POLAR_WEBHOOK_SECRET`. Ba biến quan trọng cho kiến trúc này:

| Biến | Giá trị | Ý nghĩa |
|---|---|---|
| `PROXY_SHARED_SECRET` | chuỗi ngẫu nhiên ≥ 32 ký tự | Mật khẩu chung giữa Cloudflare Pages và Modal. **Hai nơi phải giống hệt.** |
| `REQUIRE_PROXY` | `1` | Chặn gọi thẳng `/api/*` trên URL modal.run (403), trừ `/api/health`. |
| `TRUST_PROXY` | `1` | Dự phòng: lấy IP từ `X-Forwarded-For` của Modal khi request không qua Pages. |

### Bước 2. Deploy backend lên Modal

```bash
pip install modal python-dotenv                # python-dotenv: cần cho `modal secret create --from-dotenv` (modal không tự cài)
modal token new                                   # mở trình duyệt để đăng nhập (một lần)
scripts/deploy_modal.sh --first-run               # lần đầu: tạo secret, tải mô hình, deploy, kiểm tra
# các lần sau (đổi code hoặc secret):
scripts/deploy_modal.sh
```

Script làm lần lượt: kiểm tra `modal` đã đăng nhập và có `python-dotenv` → `modal secret create dattts --force --from-dotenv ~/dattts-secret.env` (ghi đè **toàn bộ** secret) → (`--first-run`) `modal run backend/modal_app.py::download_models` → `modal deploy backend/modal_app.py` → in URL web dạng `https://<workspace>--dattts-web.modal.run` → kiểm tra `/api/health`, `/api/voices` (25 giọng), `/api/billing/plans`, và xác nhận gọi thẳng không có mật khẩu bị 403. Tuỳ chọn khác: `scripts/deploy_modal.sh --help` (`--env-file`, `--skip-secret`, `--url`, `--dry-run`…).

**Ghi lại URL web** mà script in ra — bước 3 cần nó (cuối script có in sẵn lệnh bước 3; lần `--first-run` lệnh đó đã kèm `--create-project`). Secret chỉ có hiệu lực ở container mới, nên sau khi đổi secret luôn phải deploy lại (script đã làm).

Làm tay không dùng script:

```bash
modal secret create dattts --force --from-dotenv ~/dattts-secret.env
modal run backend/modal_app.py::download_models   # chỉ lần đầu
modal deploy backend/modal_app.py
curl https://<workspace>--dattts-web.modal.run/api/health   # {"ok":true,"engine":"vieneu-v3-turbo","voices":25,...}
```

### Bước 3. Deploy trang + proxy lên Cloudflare Pages

```bash
npx wrangler@4.151.3 login                        # mở trình duyệt để đăng nhập Cloudflare (một lần)
scripts/deploy_pages.sh --create-project --set-secrets \
    --modal-origin https://<workspace>--dattts-web.modal.run \
    --env-file ~/dattts-secret.env                # lần đầu
scripts/deploy_pages.sh                           # các lần sau (đổi web/ hoặc functions/)
```

- `--create-project` tạo project Pages tên `dattts` (production branch `main`) nếu chưa có; thiếu cờ này mà project chưa tồn tại thì script cũng tự tạo.
- Lần đầu (project vừa tạo) script kiểm tra sau deploy tại `https://dattts.pages.dev`, vì `dattts.com` chỉ được gắn ở bước 4; các lần sau kiểm tra `https://dattts.com`. Đổi bằng `--site-url URL`.
- `--set-secrets` đặt hai biến **bí mật (đã mã hoá)** của Pages bằng `wrangler pages secret put`: `MODAL_ORIGIN` = URL web Modal ở bước 2, và `PROXY_SHARED_SECRET` lấy từ file secret (giá trị truyền qua stdin, không in ra màn hình).
- Sau đó script chạy `npx wrangler@4.151.3 pages deploy web --project-name dattts` **từ gốc repo**, để wrangler gửi kèm thư mục `functions/` (proxy `/api/*`). Phiên bản wrangler được cố định trong script.

Muốn đặt biến bằng giao diện: Cloudflare → **Workers & Pages** → `dattts` → **Settings → Variables and Secrets** → **Add** → chọn loại **Secret** (Encrypt) cho cả `MODAL_ORIGIN` và `PROXY_SHARED_SECRET` (môi trường Production). Biến mới chỉ áp dụng cho lần deploy **sau**, nên chạy lại `scripts/deploy_pages.sh`.

Lưu ý: sau khi đổi `PROXY_SHARED_SECRET`, phải đổi **ở cả hai nơi** (file secret → `scripts/deploy_modal.sh`, rồi `scripts/deploy_pages.sh --set-secrets ...`). Lệch nhau thì mọi `/api/*` bị 403.

### Bước 4. Trỏ tên miền dattts.com

Cách khuyên dùng — **nameserver của dattts.com nằm trên Cloudflare**:

1. Cloudflare → **Add a domain** → nhập `dattts.com` → chọn gói Free. Cloudflare đưa ra hai nameserver (dạng `xxx.ns.cloudflare.com`).
2. Vào nơi bạn mua tên miền (ví dụ Mắt Bão, PA Việt Nam, Namecheap…), đổi nameserver của `dattts.com` sang hai nameserver đó. Có thể mất vài giờ (tối đa 24–48 giờ) để có hiệu lực.
3. Cloudflare → **Workers & Pages** → `dattts` → **Custom domains** → **Set up a custom domain** → thêm `dattts.com`, rồi thêm tiếp `www.dattts.com`. Vì DNS đã ở Cloudflare, bản ghi được tạo tự động và chứng chỉ HTTPS được cấp trong vài phút.

Nếu **không** muốn chuyển nameserver: vẫn thêm `dattts.com` trong **Custom domains** của Pages; Cloudflare sẽ hướng dẫn tạo bản ghi `CNAME` trỏ về `dattts.pages.dev` tại nhà cung cấp DNS hiện tại. Lưu ý: tên miền gốc (`dattts.com`, không có `www`) chỉ dùng được CNAME khi nhà cung cấp DNS hỗ trợ "CNAME flattening"/ALIAS — nếu không, hãy chuyển nameserver sang Cloudflare như trên.

`ALLOWED_ORIGINS` mặc định đã có `https://dattts.com` và `https://www.dattts.com`; `PUBLIC_BASE_URL=https://dattts.com` đã có trong file mẫu.

### Bước 5. Kiểm tra sau khi lên mạng

```bash
curl https://dattts.com/api/health                       # {"ok":true,...}
curl -s https://dattts.com/api/voices | head -c 200      # danh sách giọng (đi qua proxy)
curl -s -o /dev/null -w '%{http_code}\n' https://<workspace>--dattts-web.modal.run/api/voices   # 403 = đã chặn gọi thẳng
curl -s -H "Authorization: Bearer $ADMIN_TOKEN" "https://dattts.com/api/admin/waitlist?format=csv"
```

Mở `https://dattts.com`, đọc thử một đoạn, tua audio. Xem log backend: `modal app logs dattts`. Log của proxy: Cloudflare → `dattts` → **Deployments** → chọn bản deploy → **Functions** → *Real-time logs*.

### Bước 6. Webhook Polar

URL webhook **giữ nguyên** `https://dattts.com/api/billing/webhook` (định dạng Raw) — webhook đi qua Pages như mọi `/api/*`, proxy chuyển nguyên body thô nên chữ ký vẫn đúng. **Không** dùng URL `modal.run` cho webhook: với `REQUIRE_PROXY=1` nó luôn bị 403. Sau mỗi lần deploy, xem Polar → **Settings → Webhooks → Deliveries**: mọi lần gửi phải là 202.

### Chi phí ước tính

| Khoản | Chi phí |
|---|---|
| Modal Starter | $0/tháng + tiền máy chạy theo giây; Modal tặng **$30 credit mỗi tháng** |
| CPU | ~$0.047 / core-giờ |
| RAM | ~$0.008 / GiB-giờ |
| `TTSWorker` 4 CPU + 6 GiB | 4 × 0.047 + 6 × 0.008 ≈ **$0.24 / giờ khi đang chạy**, tự tắt sau 5 phút rảnh |
| `web` 1 CPU + 2 GiB | ≈ $0.06 / giờ khi đang chạy, tự tắt sau 10 phút rảnh |
| Cloudflare Pages + Functions | $0 (gói Free: 100.000 request Functions/ngày, dùng chung với Workers, reset 00:00 UTC). Trang tĩnh không tính; **mọi** `/api/*` đều tính, kể cả mỗi lần trang hỏi tiến độ job (1 s cho 10 lần đầu, sau đó giãn dần tới 10 s — job 100.000 ký tự ≈ 240 lần hỏi). Hết hạn mức thì `/api/*` lỗi tới 00:00 UTC; lưu lượng lớn thì lên Workers Paid ($5/tháng, 10 triệu request) |

Ví dụ: worker chạy tổng cộng 3 giờ/ngày ≈ $0.72/ngày ≈ $22/tháng — vẫn nằm trong $30 credit. Khi không có ai dùng thì không tốn tiền. Giá có thể đổi; xem [modal.com/pricing](https://modal.com/pricing).

### CPU hay GPU?

- **CPU (mặc định, khuyên dùng khi mới bắt đầu):** bản int8 trên 4 CPU đọc nhanh hơn thời gian thực khoảng 2 lần. Chỉ trả tiền khi đang có người dùng, và chi phí cho mỗi phút âm thanh thấp.
- **GPU (`DATTTS_GPU=T4 scripts/deploy_modal.sh`, hoặc `L4`, …):** nhanh hơn với văn bản dài và khi nhiều người dùng cùng lúc, nhưng giá mỗi giây cao hơn nhiều. Đáng bật khi lưu lượng đã đều. Lưu ý: nhánh GPU (PyTorch, `vieneu[cuda]`) chưa được chạy thử thực tế, nên hãy chạy `--first-run` (đọc thử) trước.
- **`scaledown_window`:** worker giữ ấm 300 giây và web giữ 600 giây sau request cuối. Tăng giá trị này thì ít khởi động nguội hơn nhưng tốn tiền chờ.
- **`min_containers`:** mặc định là 0, nên khi không có ai dùng thì không tốn tiền, nhưng request đầu tiên phải chờ container khởi động và nạp mô hình (khoảng 10–30 giây; proxy không đặt giới hạn thời gian chờ riêng). Nếu muốn luôn sẵn sàng, thêm `min_containers=1` vào `@app.cls(...)` của `TTSWorker` trong `modal_app.py` — khi đó trả tiền 4 CPU chạy 24/7 (~$175/tháng), nên hãy cân nhắc chỉ đặt cho `web` (rẻ, 1 CPU).
- **Giới hạn 150 giây của Modal:** mỗi request HTTP tới hàm web bị Modal cắt ở 150 giây (`timeout` của hàm không nâng được mức này); Modal trả `303` về chính URL đó để hỏi lại kết quả. Proxy Pages tự theo các `303` đó (tối đa 8 lần), nên `/api/tts` gần 3.000 ký tự khi nhiều người dùng cùng lúc hoặc container nguội vẫn trả kết quả bình thường. Đừng gọi thẳng URL modal.run cho `/api/tts` dài — client phải tự theo `303`.
- **Cache:** cùng văn bản, giọng, tốc độ, định dạng và từ điển thì trả file cũ ngay, không tốn công dựng lại và không trừ quota. File `/api/files/*` có `Cache-Control: immutable` nên Cloudflare có thể cache ở edge; các `/api/*` khác không bị proxy cache.

### Xử lý sự cố

| Triệu chứng | Nguyên nhân thường gặp | Cách sửa |
|---|---|---|
| `502` `upstream_unavailable` ("Máy chủ DatTTS tạm thời không phản hồi") | Pages không gọi được Modal: chưa đặt / đặt sai `MODAL_ORIGIN` hoặc `PROXY_SHARED_SECRET` trên Pages, app Modal chưa deploy hoặc bị dừng | `curl https://<workspace>--dattts-web.modal.run/api/health`. Nếu Modal OK: kiểm tra biến Pages (bước 3) rồi chạy lại `scripts/deploy_pages.sh`. Nếu Modal lỗi: `modal app logs dattts`, chạy lại `scripts/deploy_modal.sh` |
| `403` `direct_access_forbidden` trên `https://dattts.com/api/...` | `PROXY_SHARED_SECRET` ở Pages **khác** trong Modal secret | Đặt lại cùng một giá trị ở cả hai nơi: `scripts/deploy_modal.sh` rồi `scripts/deploy_pages.sh --set-secrets --modal-origin <URL> --env-file ~/dattts-secret.env` |
| `403` `direct_access_forbidden` khi gọi URL `modal.run` | Đúng thiết kế (`REQUIRE_PROXY=1`) | Dùng `https://dattts.com`. Riêng `/api/health` luôn mở để kiểm tra |
| Webhook Polar bị **tắt** (endpoint disabled) | Polar tự tắt endpoint sau ~10 lần gửi lỗi liên tiếp (ví dụ lúc Modal/Pages lỗi, hoặc webhook trỏ vào URL modal.run bị 403) | Sửa nguyên nhân, kiểm tra URL là `https://dattts.com/api/billing/webhook`, rồi vào Polar → **Settings → Webhooks** → mở endpoint → **Enable** lại; ở tab **Deliveries** bấm gửi lại (redeliver) các sự kiện thất bại |
| Webhook trả `503` | Thiếu `POLAR_WEBHOOK_SECRET` trong Modal secret | Thêm vào `~/dattts-secret.env`, chạy `scripts/deploy_modal.sh` |
| Webhook trả `403 bad_signature` | `POLAR_WEBHOOK_SECRET` sai (lấy nhầm sandbox/production) | Copy lại Secret của đúng endpoint trong Polar, deploy lại |
| Mọi khách chung một quota miễn phí | Request không qua Pages, hoặc secret lệch (backend không tin `X-DatTTS-Client-IP`) | Kiểm tra như dòng 403 ở trên; đảm bảo `REQUIRE_PROXY=1` |
| Request đầu tiên chậm 10–30 giây | Container lạnh | Bình thường; xem `min_containers` ở trên |

### Cách thay thế: tên miền riêng của Modal (gói Team)

Nếu bạn dùng gói **Modal Team** (có custom domain), có thể bỏ Cloudflare Pages: Modal phục vụ cả trang lẫn API ở `dattts.com`. Thêm tên miền trong trang cài đặt workspace của Modal, tạo bản ghi DNS mà Modal yêu cầu (nếu DNS ở Cloudflare thì tắt proxy — đám mây xám — để Modal cấp chứng chỉ), rồi deploy với `DATTTS_DOMAINS=dattts.com,www.dattts.com scripts/deploy_modal.sh`. Khi đó bỏ `REQUIRE_PROXY` (hoặc đặt `0`) trong file secret, vì không còn proxy nào gửi mật khẩu chung; quota theo IP dùng `TRUST_PROXY=1`. Xem mục *Custom domains* trong tài liệu web endpoint của Modal tại [modal.com/docs](https://modal.com/docs).

## Thanh toán với Polar

DatTTS bán gói qua **Polar** (polar.sh). Polar là *Merchant of Record*, tức bên bán hàng chính thức: Polar thu tiền thẻ quốc tế, Apple Pay và Google Pay, xuất hoá đơn, lo thuế bán hàng ở nước ngoài, rồi chuyển tiền về tài khoản Stripe của bạn. DatTTS không cần hệ thống tài khoản riêng. **API key của khách chính là License Key do Polar cấp** (tiền tố `DATTTS`). Polar **không** gửi key trong email: email biên nhận của Polar có nút **"Access purchase"** mở cổng khách hàng, khách xem và chép key ở đó (trang web đã hướng dẫn đúng như vậy). Polar tự thu hồi key khi gói kết thúc (hết kỳ cuối sau khi huỷ, hoặc khi bạn revoke).

Giá niêm yết bằng USD; ở chế độ tiếng Việt trang có thêm số tiền quy đổi sang VNĐ để tham khảo (tỷ giá trong `web/config.js`). Khi chưa có `POLAR_ACCESS_TOKEN`, trang hiện "Coming soon" / "Sắp mở bán" cùng form danh sách chờ.

### Trạng thái hiện tại: Polar production ĐÃ cấu hình sẵn (08/11/2026)

Trên **production** (<https://polar.sh>, tổ chức "Dat TTS", id `cd16dd37-dd25-4e92-aaaa-23509784d129`) đã có sẵn:

- 3 sản phẩm Subscription: DatTTS Pro (weekly) $1.99, DatTTS Pro (monthly) $4.99, DatTTS Pro (yearly) $49.99;
- 3 benefit License Keys (prefix `DATTTS`), mỗi sản phẩm một benefit (ID ở bảng [Gói và giá](#gói-và-giá));
- webhook trỏ tới `https://dattts.com/api/billing/webhook`, **API version 2027-01, định dạng Raw**;
- một Organization Access Token và webhook secret (không nằm trong repo).

Đã kiểm tra ngày 08/11/2026 bằng `scripts/polar_smoke.py` và Playwright: API đọc đúng giá 199/499/4999 cent và chu kỳ week/month/year, bảng giá trên web hiện $1.99 / $4.99 / $49.99 với "Save 37%" / "Save 17%" (trước khi đổi sang làm tròn xuống; nay là "Save 16%"), bấm "Get Pro" (Monthly) mở trang thanh toán polar.sh hiện **$4.99 / mo** (không trả tiền). Trang thanh toán đang báo **"Dat TTS is in test mode"**: Polar chưa duyệt tổ chức nên chỉ nhận đơn miễn phí hoặc giảm giá 100%.

**Việc bạn còn phải làm (theo thứ tự):**

1. **Xoay access token.** Token hiện tại đã bị dán vào khung chat, coi như lộ. Vào **Settings → Developers**, xoá token cũ, tạo token mới **thời hạn dài** (ví dụ 1 năm, ghi lịch xoay trước khi hết hạn) với đúng các scope ở Bước 5. Nếu nghi webhook secret cũng lộ, tạo lại secret của webhook (Settings → Webhooks → endpoint → Regenerate secret).
2. **Điền tên pháp lý** thay cho `[Legal name]` trong `web/terms.html`, `web/privacy.html` và `[Tên pháp lý]` trong `web/dieu-khoan.html`, `web/bao-mat.html` (kiểm bằng `E2E_RELEASE=1 node scripts/e2e_billing.mjs`).
3. **Tạo Modal secret và deploy:** điền `~/dattts-secret.env` (Bước 7 bên dưới) rồi chạy `scripts/deploy_modal.sh --first-run` (mục [Triển khai lên Modal + Cloudflare](#triển-khai-lên-modal--cloudflare), bước 1–2).
4. **Trỏ dattts.com** về Cloudflare Pages (bước 3–4 của mục [Triển khai lên Modal + Cloudflare](#triển-khai-lên-modal--cloudflare)). Webhook production đã trỏ sẵn tới `https://dattts.com/api/billing/webhook`, nên trước khi tên miền chạy, các lần gửi webhook sẽ lỗi; sau khi trỏ xong hãy xem tab **Deliveries** và bật lại webhook nếu Polar đã tự tắt.
5. **Gửi Polar duyệt tổ chức** (Settings → General, kèm tài khoản nhận tiền Stripe ở Bước 2). Khi được duyệt, dòng "test mode" biến mất và thẻ thật mới thanh toán được.
6. **Mua thử thật bằng mã giảm giá 100%:** trong dashboard Polar vào **Products → Discounts → New discount**, tạo mã (ví dụ `DATTEST100`) giảm 100%, giới hạn 1–2 lần dùng, áp cho gói Pro tháng. Mở `https://dattts.com/#pricing` → Monthly → **Get Pro**, nhập mã ở "Add discount code", dùng email thật. Kiểm tra: trang `checkout-success.html` báo thành công, email biên nhận của Polar có nút "Access purchase" mở cổng khách hàng, trong đó có key `DATTTS-…` (ghi lại xem email thật có những gì để chắc chữ trên trang khớp), dán key vào phòng thu thấy **Pro Monthly** và **0 / 1,000,000**, tạo thử một đoạn, tab **Deliveries** của webhook toàn mã 202. Sau đó thu hồi subscription thử trong dashboard và xoá mã giảm giá.

Các bước 1–9 dưới đây là hướng dẫn đầy đủ (dùng khi cần làm lại từ đầu, hoặc dựng bản **sandbox** song song để thử). Sandbox và production là hai hệ thống tách biệt hoàn toàn: tài khoản, sản phẩm, benefit, token và webhook đều phải tạo lại ở mỗi bên.

### Bước 1. Tạo tài khoản và tổ chức

1. Vào <https://sandbox.polar.sh> và đăng nhập bằng GitHub hoặc email.
2. Tạo **Organization**, ví dụ tên `DatTTS` với slug `dattts`.
3. Vào **Settings → General** và copy **Organization ID** (dạng UUID). Đây là giá trị `POLAR_ORGANIZATION_ID`.
4. Điền website `https://dattts.com`, email hỗ trợ `support@dattts.com` và mô tả sản phẩm. Lúc duyệt tài khoản, Polar sẽ xem trang web, ba trang pháp lý tiếng Anh (`/terms.html`, `/privacy.html`, `/refund.html`; bản tiếng Việt ở `/dieu-khoan.html`, `/bao-mat.html`, `/hoan-tien.html`) và bảng giá. Trước khi gửi duyệt, hãy điền tên pháp lý vào các trang đó (xem mục TODO).

### Bước 2. Tài khoản nhận tiền (Stripe Connect Express)

Polar chuyển tiền qua **Stripe Connect Express**. Stripe Connect Express hỗ trợ Việt Nam.

1. Vào **Finance → Payout account → Setup**. Polar mở trang đăng ký Stripe Express.
2. Chọn quốc gia **Vietnam**. Tài khoản ngân hàng nhận tiền phải mở **tại Việt Nam và bằng VND**. Stripe tự đổi USD sang VND khi chuyển, nên sẽ có chênh lệch tỷ giá và phí chuyển đổi.
3. Chọn loại tài khoản:
   - **Cá nhân (Individual):** dùng họ tên và số CCCD của bạn, kèm tài khoản ngân hàng đứng tên chính bạn. Cách này nhanh nhất để bắt đầu.
   - **Doanh nghiệp (Company):** dùng tên công ty, mã số thuế và giấy chứng nhận đăng ký kinh doanh, kèm tài khoản ngân hàng của công ty. Nên chọn cách này khi doanh thu đã đều, vì có lợi cho việc kê khai thuế.
4. Hoàn tất xác minh danh tính. Polar chỉ chuyển tiền sau khi tài khoản đã được duyệt.

Ở sandbox, bạn chỉ cần bước này nếu muốn thử luồng nhận tiền. Thanh toán thử vẫn chạy khi chưa có tài khoản nhận tiền.

### Bước 3. Ba sản phẩm Pro (tuần / tháng / năm)

Ở production, ba sản phẩm đã được tạo sẵn, đều là **Subscription**, giá cố định bằng USD, cùng tính năng (25 giọng, MP3/WAV/OGG, SRT/VTT, nhiều giọng, API, dùng thương mại), chỉ khác chu kỳ:

| Gói (plan id) | Giá | Hạn mức | Biến môi trường (sản phẩm / benefit) |
|---|---|---|---|
| `pro_week` | **$1.99 / tuần** | 250,000 ký tự / tuần | `POLAR_PRODUCT_PRO_WEEK` / `POLAR_BENEFIT_PRO_WEEK` |
| `pro_month` — "Most popular" | **$4.99 / tháng** | 1,000,000 ký tự / tháng | `POLAR_PRODUCT_PRO_MONTH` / `POLAR_BENEFIT_PRO_MONTH` |
| `pro_year` | **$49.99 / năm** | 1,000,000 ký tự / tháng | `POLAR_PRODUCT_PRO_YEAR` / `POLAR_BENEFIT_PRO_YEAR` |

UUID thật nằm trong `backend/.env.example`. DatTTS đọc giá và chu kỳ (`recurring_interval` của sản phẩm) trực tiếp từ Polar, nên khi bạn đổi giá trên Polar, bảng giá trên web cũng tự đổi theo (cache 10 phút). Nhãn "Save X%" cũng tính từ giá thật (**làm tròn xuống**, theo SPEC): gói tháng so với 4 × giá tuần (hiện **37%**), gói năm so với 12 × giá tháng (hiện **16%**, chính xác là 16,52%). Muốn hiện "Save 17%" thì phải hạ giá năm xuống ≤ $49.70.

Phí Polar là 5% + 50¢ mỗi giao dịch, cộng khoảng 1,5% cho thẻ phát hành ngoài Mỹ, nên gói tuần $1.99 mất khoảng 30% phí mỗi lần gia hạn; gói tháng và đặc biệt gói năm (50¢ chỉ tính một lần mỗi năm) có lợi hơn nhiều cho bạn — đó là lý do gói tháng được gắn "Most popular".

### Bước 4. Gắn benefit License Keys (đây chính là API key)

Làm cho từng sản phẩm. Mở sản phẩm, vào **Benefits → New benefit → License Keys**:

- **Description:** ví dụ "API key DatTTS — Pro (monthly)".
- **Prefix:** `DATTTS`
- **Expiration:** không đặt. Key tự bị thu hồi khi gói kết thúc.
- **Activation limit / Usage limit:** để trống. DatTTS tự đếm ký tự theo tuần/tháng.

Lưu lại, gắn benefit vào sản phẩm, rồi copy **Benefit ID**:

- benefit của gói Pro tuần là `POLAR_BENEFIT_PRO_WEEK`;
- benefit của gói Pro tháng là `POLAR_BENEFIT_PRO_MONTH`;
- benefit của gói Pro năm là `POLAR_BENEFIT_PRO_YEAR`.

Mỗi gói phải có **benefit riêng**, vì DatTTS dựa vào `benefit_id` của key để biết khách đang dùng gói nào. Key thuộc benefit lạ sẽ bị coi là không hợp lệ.

### Bước 5. Tạo Organization Access Token

Vào **Settings → Developers → New token**. Đặt tên, ví dụ `dattts-backend`. Đặt thời hạn dài, nhưng nhớ ghi lịch để xoay token trước khi hết hạn. Chọn **đúng các scope** sau:

```
checkouts:read  checkouts:write  products:read  customer_sessions:write  license_keys:read  license_keys:write
```

Copy token (dạng `polar_oat_...`) và cất ngay, vì Polar chỉ hiện token một lần. Đây là giá trị `POLAR_ACCESS_TOKEN`.

### Bước 6. Tạo webhook

Vào **Settings → Webhooks → Add endpoint**:

- **URL:** `https://dattts.com/api/billing/webhook` (đi qua Cloudflare Pages). **Không** dùng URL `modal.run`: với `REQUIRE_PROXY=1` nó bị 403 và Polar sẽ tự tắt webhook.
- **Format:** `Raw` (webhook production hiện dùng API version **2027-01**)
- **Events**, chọn các sự kiện sau:
  - `subscription.created`, `subscription.active`, `subscription.updated`, `subscription.canceled`, `subscription.uncanceled`, `subscription.revoked`
  - `order.paid`
  - `benefit_grant.created`, `benefit_grant.updated`, `benefit_grant.revoked`
  - `customer.state_changed`
  - `order.refunded`, `refund.created`, `refund.updated`

  **Webhook production đã tạo trước đó chưa có** `benefit_grant.updated`, `order.refunded`, `refund.created`, `refund.updated`: hãy mở webhook đó và tick thêm bốn sự kiện này (DatTTS dùng chúng để xoá cache key ngay khi khách đổi key hoặc được hoàn tiền).

Sau khi tạo, copy **Secret** (dạng `whsec_...`). Đây là giá trị `POLAR_WEBHOOK_SECRET`.

DatTTS kiểm tra chữ ký của webhook. Webhook sai chữ ký nhận 403, còn webhook hợp lệ nhận 202. Khi có sự kiện `subscription.revoked`, `subscription.updated`, `benefit_grant.updated`, `benefit_grant.revoked`, `customer.state_changed`, `order.refunded`, `refund.created` hoặc `refund.updated`, DatTTS xoá ngay bộ nhớ đệm key của khách đó, nên key bị thu hồi sẽ ngừng chạy lập tức. Không có webhook thì key đã đổi/thu hồi vẫn dùng được tối đa 10 phút (bộ đệm), hoặc tối đa 15 phút nếu IP đó đang bị giới hạn tần suất kiểm tra key; chỉ khi chính Polar lỗi thì key cũ trong bộ đệm mới được dùng tới 24 giờ. Thiếu `POLAR_WEBHOOK_SECRET` thì mọi webhook bị 503 — log khởi động và `scripts/polar_smoke.py` sẽ cảnh báo. Mọi sự kiện đều được ghi lại và xem được qua `/api/admin/billing`.

Polar gửi lại tối đa 10 lần. Nếu endpoint lỗi 10 lần liên tiếp, Polar sẽ **tắt** webhook. Vì vậy, sau mỗi lần deploy, hãy vào trang webhook xem tab **Deliveries**.

### Bước 7. Đưa cấu hình vào Modal secret

**Cách khuyên dùng:** điền các biến dưới đây vào `~/dattts-secret.env` (mẫu: `deploy/secret.env.example`) rồi chạy `scripts/deploy_modal.sh` — xem [Triển khai lên Modal + Cloudflare](#triển-khai-lên-modal--cloudflare).

`modal secret create --force` ghi đè **toàn bộ** secret, nên phải điền lại cả `ADMIN_TOKEN`, `API_KEYS` và `PROXY_SHARED_SECRET` cũ. Lệnh đầy đủ cho **production** (các UUID là ID thật, công khai; chỉ thay `<...>` bằng bí mật của bạn):

```bash
modal secret create dattts --force \
  ADMIN_TOKEN=<mã-quản-trị, ví dụ: openssl rand -hex 24> \
  API_KEYS=<key-nội-bộ, cách nhau bằng dấu phẩy, có thể để trống> \
  PROXY_SHARED_SECRET=<giống hệt biến cùng tên ở Cloudflare Pages, openssl rand -hex 32> \
  REQUIRE_PROXY=1 \
  TRUST_PROXY=1 \
  PUBLIC_BASE_URL=https://dattts.com \
  POLAR_SERVER=production \
  POLAR_ACCESS_TOKEN=<polar_oat_... token MỚI sau khi xoay> \
  POLAR_WEBHOOK_SECRET=<whsec_... secret của webhook production> \
  POLAR_ORGANIZATION_ID=cd16dd37-dd25-4e92-aaaa-23509784d129 \
  POLAR_PRODUCT_PRO_WEEK=3d54ccce-5b59-4903-a23b-18fd8c9e6fe1 \
  POLAR_BENEFIT_PRO_WEEK=e02132d4-8b7c-4779-a271-cf778cada689 \
  POLAR_PRODUCT_PRO_MONTH=5cdb0d3a-edf1-46aa-ba4f-b1e4303e60bf \
  POLAR_BENEFIT_PRO_MONTH=de10e5c4-5357-4ec8-81ca-5b2848226ac0 \
  POLAR_PRODUCT_PRO_YEAR=673f4798-5c0b-4a6f-a6bc-002054bcd621 \
  POLAR_BENEFIT_PRO_YEAR=170cee60-1cd2-428e-8e46-a50e753189fb \
  PLAN_PRO_WEEK_CHARS=250000 \
  PLAN_PRO_MONTH_CHARS=1000000 \
  PLAN_PRO_YEAR_MONTHLY_CHARS=1000000
```

Mẹo để token không nằm trong lịch sử shell: ghi các dòng `KEY=VALUE` vào một file ngoài repo (ví dụ `~/dattts-secret.env`, `chmod 600`) rồi chạy `modal secret create dattts --force --from-dotenv ~/dattts-secret.env`. Với **sandbox**, đổi `POLAR_SERVER=sandbox` và thay mọi UUID bằng ID sandbox của bạn.

```bash
modal deploy backend/modal_app.py      # secret chỉ có hiệu lực ở container mới, nên phải deploy lại (script tự làm)
```

Kiểm tra lại cấu hình:

```bash
curl -s https://dattts.com/api/billing/plans          # "enabled": true, giá 199 / 499 / 4999, save_percent 37 / 16
curl -s -H "Authorization: Bearer $ADMIN_TOKEN" https://dattts.com/api/admin/billing
```

Nếu `price` là `null`, nghĩa là token thiếu scope `products:read`, UUID sản phẩm bị sai, hoặc đang trộn sandbox với production.

Kiểm tra nhanh cấu hình Polar thật từ máy bạn (chỉ đọc sản phẩm và thử một key giả; thêm `--checkout` để tạo **một** phiên thanh toán chưa trả tiền, tự hết hạn):

```bash
set -a; . ./polar.env; set +a          # file KEY=VALUE chứa POLAR_* (đừng commit)
.venv/bin/python scripts/polar_smoke.py [--checkout]
```

### Bước 8. Thử ở sandbox

1. Mở `https://dattts.com/#pricing`, chọn **Monthly** và bấm **Get Pro**.
2. Ở trang thanh toán của Polar sandbox, dùng thẻ thử của Stripe: **4242 4242 4242 4242**, ngày hết hạn bất kỳ trong tương lai (ví dụ 12/34), CVC bất kỳ (ví dụ 123), và một email thật của bạn (để nhận biên nhận có nút "Access purchase").
3. Polar chuyển về `https://dattts.com/checkout-success.html?checkout_id=...`. Trang này phải báo thanh toán thành công trong vài giây.
4. Mở email biên nhận của Polar, bấm **Access purchase** để vào cổng khách hàng và copy key `DATTTS-...`. Dán key vào ô trên trang cảm ơn, hoặc dùng nút nhập API key trong phòng thu. Phòng thu phải hiện gói **Pro Monthly** và hạn mức **0 / 1,000,000** ký tự tháng này.
5. Tạo thử một đoạn. Số ký tự đã dùng trong tháng phải tăng lên, còn lượt miễn phí theo IP giữ nguyên.
6. Bấm **Quản lý gói** và kiểm tra rằng cổng khách hàng Polar mở ra. Lưu ý: khách tự huỷ trong cổng chỉ có hiệu lực **cuối kỳ** (`cancel_at_period_end`, chỉ có `subscription.canceled`), key vẫn dùng được tới hết kỳ. Để thử thu hồi ngay, vào dashboard Polar sandbox → **Customers** (hoặc **Subscriptions**) → chọn subscription → **Revoke**. Sau đó kiểm tra ở **Settings → Webhooks → Deliveries** rằng `subscription.revoked` và `benefit_grant.revoked` được nhận với mã 202, và key bị từ chối (401, phòng thu báo key hết hiệu lực và gỡ key).
7. Trong Polar, vào **Settings → Webhooks → Deliveries**. Mọi lần gửi phải có mã 202.

Nếu cần thử nhiều lần, có thể đặt giá $0 hoặc tạo mã giảm giá 100% ở sandbox.

### Bước 9. Production

Ở production, bước 1, 3, 4, 6 **đã làm xong** (xem "Trạng thái hiện tại" ở trên). Còn lại: tài khoản nhận tiền (bước 2), xoay token (bước 5), gửi duyệt tổ chức, chạy lệnh ở bước 7 với `POLAR_SERVER=production` (**không** đặt `POLAR_API_BASE`), `modal deploy`, kiểm tra `/api/billing/plans`, rồi mua thử bằng mã giảm giá 100% như mục "Việc bạn còn phải làm".

Muốn tạm dừng bán, chỉ cần bỏ `POLAR_ACCESS_TOKEN` khỏi secret rồi deploy lại. Trang sẽ quay về "Coming soon", và mọi phần khác vẫn chạy bình thường.

### Chạy với Polar giả ở máy local

`scripts/fake_polar.py` là một Polar giả, mô phỏng các endpoint mà DatTTS dùng. Nó không gọi polar.sh và không cần tài khoản. Bản giả gồm:

- trang thanh toán giả và cổng khách hàng giả;
- một nút để gửi webhook đã ký đúng;
- sẵn các key thử: `DATTTS-WEEK-TEST`, `DATTTS-MONTH-TEST`, `DATTTS-YEAR-TEST` (Pro tuần / tháng / năm) và `DATTTS-REVOKED-TEST` (đã bị thu hồi);
- giá giống Polar thật: $1.99 / tuần, $4.99 / tháng, $49.99 / năm.

```bash
. .venv/bin/activate
python scripts/fake_polar.py --port 8899 &                    # Polar giả
eval "$(python scripts/fake_polar.py --print-env)"            # đặt POLAR_* trỏ vào Polar giả
cd backend && PUBLIC_BASE_URL=http://127.0.0.1:8765 python server_local.py --fake --port 8765
```

Sau đó mở <http://127.0.0.1:8765/#pricing> và bấm **Get Pro**. Trên trang Polar giả, bấm **Thanh toán**. Key mới cấp nằm ở <http://127.0.0.1:8899/_fake/outbox>.

Để gửi thử một webhook, ví dụ huỷ gói và thu hồi key:

```bash
curl -s -X POST http://127.0.0.1:8899/_fake/webhook -H 'Content-Type: application/json' \
  -d '{"type":"subscription.revoked","key":"DATTTS-MONTH-TEST","revoke":true}'
```

Các endpoint khác của bản giả:

- `/_fake/state`: xem toàn bộ trạng thái;
- `/_fake/reset`: đưa về dữ liệu mẫu ban đầu;
- `/_fake/revoke`: thu hồi key mà không gửi webhook.

Xem đầy đủ trong docstring của file.

## Vận hành: CI, theo dõi, trần chi phí

- **CI** (`.github/workflows/ci.yml`): mỗi lần push/PR chạy pytest (không cần mô hình), import `modal_app`, test proxy, kiểm tra hash CSP và cú pháp script deploy.
- **Theo dõi uptime** (`.github/workflows/monitor.yml`): 15 phút một lần gọi trang chủ, `/api/health`, `/api/voices`, `/api/billing/plans`; lỗi thì GitHub gửi email cho bạn. **Bật bằng cách** vào repo → Settings → Secrets and variables → Actions → **Variables** → thêm `MONITOR_URL=https://dattts.com` (chưa đặt thì workflow tự bỏ qua). Lưu ý: lần kiểm tra sau khi máy nghỉ có thể làm Modal khởi động container `web` (rẻ, 1 CPU), không đụng tới worker giọng đọc.
- **Trần chi phí gói miễn phí**: `FREE_GLOBAL_DAILY_CHARS` và `FREE_REQUESTS_PER_MIN` (bảng biến môi trường). Ngoài ra nên đặt **Spend limit** trong Modal (Settings → Usage and billing) làm chốt chặn cuối.
- **Header bảo mật** cho trang tĩnh: `web/_headers` (CSP, HSTS, chống nhúng iframe). Sửa script inline trong HTML thì chạy `python3 scripts/csp_hashes.py` và cập nhật hash.
- **SEO**: `web/robots.txt`, `web/sitemap.xml`, ảnh chia sẻ `web/og.png`, trang `web/404.html`.
- **Giấy phép**: `NOTICE` ghi công các thành phần bên thứ ba. Repo **chưa có `LICENSE`** cho mã của chính DatTTS — bạn tự quyết (để riêng tư thì không cần; muốn mở mã thì thêm MIT/Apache-2.0).

## Việc còn phải làm (TODO)

- **Tự xoá file cũ: đã có.** Hàm Modal `purge_old_files` chạy 03:17 mỗi ngày (giờ VN), xoá file render cũ hơn `FILE_RETENTION_DAYS` (30) ngày; câu mẫu và sổ cái không bị đụng. Trang Bảo mật (EN + VI) đã ghi "30 ngày". Đổi số ngày thì sửa cả hai trang. Chạy tay: `modal run backend/modal_app.py::purge_old_files`.
- **Điền tên pháp lý** thay cho `[Legal name]` (`web/terms.html`, `web/privacy.html`) và `[Tên pháp lý]` (`web/dieu-khoan.html`, `web/bao-mat.html`) trước khi gửi Polar duyệt tài khoản. Bản tiếng Anh là bản chính.
- **Xoay Polar access token** (đã lộ trong chat) — xem "Việc bạn còn phải làm" ở phần Polar.
- Chính sách hoàn tiền (`refund.html` / `hoan-tien.html`): gói tuần hoàn trong 3 ngày nếu dùng dưới 25.000 ký tự; gói tháng/năm hoàn trong 14 ngày nếu dùng dưới 100.000 ký tự (10% hạn mức một kỳ), tính **mọi ký tự đã dùng từ lúc trừ tiền tới lúc yêu cầu**, chỉ áp dụng lần mua đầu. Cách kiểm tra: xem mục "Quy trình hoàn tiền" bên dưới.
- **Lưu trữ dữ liệu (trang bảo mật phải khớp với code):** bộ đệm xác thực `lk:<sha256>` chứa mã license key, mã khách, gói và **email** khách; hỏi lại Polar mỗi 10 phút, xoá ngay khi có webhook thu hồi, còn lại nằm trong `modal.Dict` tới khi Dict tự xoá (7 ngày không dùng; `LocalStore` giữ mãi). Bộ đếm IP/giới hạn tần suất cũng vậy. **Sổ cái lượt dùng** theo key (`ledger:<license_key_id>`, sao lưu ở blob `ledger-<id>.json` và chỉ mục theo khách `ledger-c-<customer_id>.json` trong Volume) giữ không thời hạn, không chứa văn bản hay email; xoá tay khi khách yêu cầu (sau khi gói đã kết thúc). Nếu đổi cách lưu ở backend, hãy cập nhật trang bảo mật và hoàn tiền (cả EN và VI).

### Quy trình hoàn tiền (cho chủ shop)

1. Khách gửi email kèm mã đơn. Trong dashboard Polar mở đơn đó, ghi lại **ngày trừ tiền** và **Customer ID**.
2. Tra lượt dùng từ ngày trừ tiền (giờ VN):
   ```bash
   curl -s -H "Authorization: Bearer $ADMIN_TOKEN" "https://dattts.com/api/admin/usage?customer_id=<CUSTOMER_ID>&since=2026-11-02"
   ```
   Mỗi key có `used_since` (ký tự đã dùng từ ngày `since`), `period_limit` và `percent_of_period`. Dưới 10 (%) và còn trong hạn (3 ngày gói tuần, 14 ngày gói tháng/năm, lần mua đầu) → được hoàn. Có thể tra một key bằng `license_key_id=<id>`. Sổ cái giữ cả khi bộ đếm theo kỳ trong `modal.Dict` đã hết hạn.
3. Trong Polar: **Refund** đơn hàng, **và** huỷ/thu hồi subscription ngay (**Revoke**, không phải "cancel at period end") để Polar thu hồi license key. Chỉ bấm Refund thì key vẫn chạy tới hết kỳ — trang hoàn tiền hứa key bị thu hồi.
4. Ở webhook → **Deliveries**, kiểm tra `order.refunded` / `subscription.revoked` / `benefit_grant.revoked` trả 202. Thử gọi `/api/me` bằng key đó (nếu khách gửi) phải nhận 401.

## Giấy phép và ghi công

- **Mô hình:** VieNeu-TTS v3 Turbo, giấy phép **Apache-2.0**. Theo model card, các giọng có sẵn (preset) được phép dùng thương mại.
- **Ghi công bắt buộc:** giữ dòng **"VieNeu-TTS by Phạm Nguyễn Ngọc Bảo"** trên trang web và trong tài liệu sản phẩm (chân trang tiếng Việt hiện "VieNeu-TTS của Phạm Nguyễn Ngọc Bảo"). Kèm theo file LICENSE (Apache-2.0) của mô hình khi phân phối lại trọng số.
- vieneu chèn watermark không nghe được vào âm thanh (`apply_watermark=True`, mặc định). DatTTS giữ nguyên mặc định này.
- Trước khi kinh doanh, hãy đọc lại model card trên Hugging Face, vì điều khoản có thể được cập nhật. Không dùng giọng để mạo danh người thật.

## Ví dụ dùng API bằng curl

Thay `API=http://127.0.0.1:8000` bằng URL Modal hoặc `https://dattts.com` khi đã triển khai. Mọi lỗi đều có dạng `{"error":{"code":"...","message":"..."}}`; `message` bằng tiếng Việt khi gửi `Accept-Language: vi`, còn lại bằng tiếng Anh.

```bash
API=http://127.0.0.1:8000

# Kiểm tra máy chủ
curl -s $API/api/health

# Danh sách 25 giọng (id, tên, vùng miền, giới tính, phong cách, link nghe mẫu)
curl -s $API/api/voices

# Nghe mẫu một giọng
curl -s -o mau-truc-ly.mp3 $API/api/voices/truc-ly/sample.mp3

# Đọc một đoạn ngắn (≤ 3.000 ký tự), trả JSON gồm audio_url, srt_url, vtt_url và mốc thời gian từng câu
curl -s -X POST $API/api/tts -H 'Content-Type: application/json' \
  -d '{"text":"Xin chào, mình là DatTTS. Giá chỉ 49.000đ một tháng!","voice":"truc-ly","speed":1.1,"format":"mp3"}'

# Tải file về (id lấy từ kết quả bên trên)
curl -s -o ban-doc.mp3 "$API/api/files/<id>.mp3?download=1"
curl -s -o phu-de.srt  "$API/api/files/<id>.srt?download=1"

# Hai giọng và từ điển phát âm
curl -s -X POST $API/api/tts -H 'Content-Type: application/json' -d '{
  "text": "[Trúc Ly] Chào anh, hôm nay KPI thế nào? [Thiện Minh] Vượt mục tiêu rồi em ạ.",
  "dictionary": [{"from": "KPI", "to": "chỉ số hiệu suất"}]
}'

# Văn bản dài (tới 100.000 ký tự): tạo job rồi hỏi tiến độ
curl -s -X POST $API/api/jobs -H 'Content-Type: application/json' \
  --data-binary @<(jq -Rs '{text: ., voice: "thien-minh", format: "ogg"}' < truyen.txt)
# → {"job_id":"abc...","status":"queued"}
curl -s $API/api/jobs/<job_id>
# → {"status":"running","progress":0.42,"done":21,"total":50}  ...  {"status":"done","result":{...}}

# Xem quota hôm nay
curl -s $API/api/quota

# Dùng API key trả phí (License Key Polar, bắt đầu bằng DATTTS) — trừ vào hạn mức kỳ (tuần/tháng) của gói
curl -s $API/api/me -H 'X-API-Key: DATTTS-XXXXXXXX-XXXXXXXX' -H 'Accept-Language: vi'
# → {"plan":"pro_month","plan_name":"Pro tháng","used":67,"limit":1000000,"remaining":999933,"reset_at":"2026-12-01T00:00:00+07:00","period":"month","email":"a***@gmail.com"}

# Bảng giá (giá lấy từ Polar, đơn vị cent)
curl -s $API/api/billing/plans

# Dùng key nội bộ trong API_KEYS (không giới hạn ký tự)
curl -s -X POST $API/api/tts -H 'Content-Type: application/json' -H 'X-API-Key: key-cua-ban-1' \
  -d '{"text":"Bản tin sáng nay có ba điểm chính."}'

# Đăng ký danh sách chờ
curl -s -X POST $API/api/waitlist -H 'Content-Type: application/json' \
  -d '{"email":"ban@example.vn","plan":"pro_month"}'
```

Các tham số của `/api/tts` và `/api/jobs`:

| Trường | Bắt buộc | Mặc định | Ghi chú |
|---|---|---|---|
| `text` | có | | Có thể chứa thẻ `[Tên giọng]` hoặc `[id-giong]` |
| `voice` | không | `truc-ly` | Id hoặc tên giọng, lấy từ `/api/voices` |
| `speed` | không | `1.0` | Từ 0,7 đến 1,5. Cao độ giữ nguyên. |
| `format` | không | `mp3` | `mp3`, `wav` hoặc `ogg` |
| `dictionary` | không | `[]` | Tối đa 200 mục `{"from","to"}`, mỗi vế không quá 100 ký tự |

Mã lỗi thường gặp:

| Mã | Ý nghĩa |
|---|---|
| `empty_text` | Chưa có văn bản |
| `unknown_voice` | Không có giọng này |
| `bad_speed` | Tốc độ nằm ngoài khoảng 0,7–1,5 |
| `bad_format` | Định dạng không hỗ trợ |
| `too_long` (413) | Văn bản quá dài cho `/api/tts`, hãy dùng `/api/jobs` |
| `quota_exceeded` (429) | Đã hết ký tự miễn phí hôm nay, hoặc hết hạn mức tuần/tháng của gói Pro (thông báo nêu lúc reset) |
| `invalid_api_key` (401) | API key không đúng hoặc gói đã hết hạn (key không có dạng `DATTTS-…` bị từ chối ngay, không hỏi Polar) |
| `rate_limited` (429) | Một IP thử quá 20 key chưa có trong bộ đệm mỗi phút (`Retry-After: 60`), hoặc tạo quá 10 phiên thanh toán mỗi giờ |
| `bad_email` (400) | Email sai định dạng, hoặc Polar không nhận email đó khi tạo phiên thanh toán |
| `api_key_required` (401) | Endpoint cần header `X-API-Key` (`/api/me`, `/api/billing/portal`) |
| `billing_disabled` (503) | Chưa bật thanh toán (thiếu `POLAR_ACCESS_TOKEN`, `POLAR_ORGANIZATION_ID`, hoặc sản phẩm/benefit của gói) |
| `billing_unavailable` (503) | Polar đang lỗi, thử lại sau (hoặc Polar trả 422 khi xác thực key: kiểm tra `POLAR_ORGANIZATION_ID`, log ghi rõ) |

---

VieNeu-TTS by Phạm Nguyễn Ngọc Bảo · DatTTS
