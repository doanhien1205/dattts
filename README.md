# DatTTS — chuyển văn bản thành giọng nói tiếng Việt

DatTTS ([dattts.com](https://dattts.com)) là dịch vụ đọc văn bản tiếng Việt thành giọng nói tự nhiên, đủ dấu hỏi dấu ngã. Bạn dán văn bản, chọn một trong 25 giọng Bắc, Trung, Nam, rồi nhận file MP3, WAV hoặc OGG kèm phụ đề SRT và VTT.

- **Engine:** [VieNeu-TTS v3 Turbo](https://huggingface.co/pnnbao-ump/VieNeu-TTS-v3-Turbo) chạy bằng ONNX trên CPU. GPU là tuỳ chọn. Âm thanh 48 kHz.
- **Nhiều giọng trong một bài:** gõ `[Trúc Ly] Chào anh. [Thiện Minh] Chào em.` để mỗi đoạn đọc bằng một giọng.
- **Karaoke theo câu:** câu đang đọc được tô sáng, bấm vào câu nào thì nhảy tới câu đó.
- **Từ điển phát âm:** tự đặt cách đọc cho từ viết tắt hoặc tên riêng.
- **Bộ luật đọc sẵn:** xử lý những chỗ mô hình hay đọc sai, ví dụ `Q3/2026`, `NĐ-CP`, `USD`, `150k` hay `TP.HN`.
- **Văn bản dài** (tới 100.000 ký tự) chạy nền và có thanh tiến độ.
- **Miễn phí** 20.000 ký tự mỗi ngày cho mỗi địa chỉ IP. Ai có API key thì không bị giới hạn.

## Kiến trúc

```
                   Trình duyệt (web/: index.html, app.js, styles.css, config.js)
                                 │  fetch /api/*   (config.js: DATTTS_API = "" hoặc URL API)
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
| `web/` | Trang tĩnh: studio, bảng giá, danh sách chờ |
| `scripts/e2e.mjs` | Kiểm thử đầu-cuối bằng Playwright với máy chủ thật |

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
| `SYNC_MAX_CHARS` | `3000` | Giới hạn của `/api/tts`. Dài hơn thì dùng `/api/jobs`. |
| `JOB_MAX_CHARS` | `100000` | Giới hạn của một job |
| `API_KEYS` | trống | Key nội bộ không giới hạn, cách nhau bằng dấu phẩy (khách trả phí dùng License Key của Polar, không cần khai ở đây) |
| `ADMIN_TOKEN` | trống | Bật `/api/admin/waitlist` và `/api/admin/billing`. Để trống thì các endpoint này trả 404. |
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
| `POLAR_ACCESS_TOKEN` | trống | Organization Access Token. **Để trống thì billing tắt**: web hiện "Sắp mở bán", endpoint billing trả 503 `billing_disabled`. Billing chỉ bật khi có cả token, `POLAR_ORGANIZATION_ID` và ít nhất một gói đủ sản phẩm + benefit; thiếu biến nào thì log khi khởi động ghi `Cấu hình Polar thiếu …` |
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

**Kiểm thử đầu-cuối (Playwright, máy chủ thật)**

```bash
cd backend && python server_local.py --port 8765 &      # đợi in "Uvicorn running"
cd .. && npm i -D playwright && npx playwright install chromium   # nếu máy chưa có
node scripts/e2e.mjs                                    # E2E_BASE=http://127.0.0.1:8765 (mặc định)
```

Bộ e2e chạy trên máy chủ thật và kiểm tra các việc sau:

- trang tải mà không có lỗi console, đủ 25 giọng, nghe được giọng mẫu;
- tạo giọng cho văn bản có số và tiền tệ, sau đó tua để kiểm tra karaoke tô đúng câu;
- tải MP3, SRT, VTT và WAV;
- đoạn nhiều giọng, từ điển phát âm, văn bản dài chạy qua job có tiến độ;
- quota giảm sau khi tạo, đăng ký danh sách chờ;
- màn hình 390 px không bị tràn ngang, chế độ tối hiển thị đúng.

Các biến tuỳ chọn:

- `E2E_OUT=/đường/dẫn.json`: ghi lại id các file đã tạo.
- `E2E_SHOTS=/thư/mục`: lưu ảnh chụp màn hình.

`e2e.mjs` chạy với billing tắt (không đặt `POLAR_ACCESS_TOKEN`). Nếu billing bật, bước danh sách chờ chỉ kiểm tra nút "Mua gói".

**Kiểm thử đầu-cuối phần thanh toán (Polar giả, không cần tài khoản Polar)**

```bash
PYTHON=$PWD/.venv/bin/python DATTTS_MODEL_DIR=$PWD/models node scripts/e2e_billing.mjs
```

Script tự bật Polar giả (cổng 8899) và `server_local.py` (cổng 8765) với dữ liệu tạm, nên hai cổng này phải đang trống. Nó chạy ba giai đoạn rồi tự tắt máy chủ:

1. **Billing bật, engine giả.** Kiểm tra:
   - bảng giá hiện $1.99 / $4.99 / $49.99 lấy từ Polar (giả);
   - bấm "Get Pro" sang trang thanh toán giả, rồi quay về `checkout-success.html` báo thành công;
   - dán key vừa cấp, phòng thu hiện gói Pro và hạn mức 1,000,000;
   - tạo giọng trừ vào hạn mức tháng, còn quota IP miễn phí giữ nguyên;
   - "Quản lý gói" mở cổng khách hàng;
   - key đã thu hồi bị báo lỗi và gỡ khỏi trình duyệt;
   - webhook `subscription.revoked` làm key ngừng chạy ngay, còn webhook ký sai bị trả 403.
2. **Billing bật, engine VieNeu thật.** Gọi `/api/tts` một lần bằng key trả phí. Đặt `E2E_REAL=0` để bỏ qua giai đoạn này.
3. **Billing tắt.** Kiểm tra trang hiện "Sắp mở bán", form chờ vẫn dùng được và checkout trả 503.

## Triển khai lên Modal

`backend/modal_app.py` gồm:

- **`TTSWorker`:** 4 CPU, 6 GB RAM, nạp mô hình một lần cho mỗi container. Tắt sau 5 phút không có việc (`scaledown_window=300`).
- **`web`:** FastAPI cùng trang tĩnh, mỗi container nhận tối đa 50 request đồng thời.
- **Volume `dattts-models`:** chứa mô hình.
- **Volume `dattts-files`:** chứa file âm thanh và phụ đề.
- **Dict `dattts-kv`:** chứa quota, job và danh sách chờ.

Các bước:

```bash
# 1. Cài Modal và đăng nhập
pip install modal
modal token new                        # mở trình duyệt để xác thực

# 2. Tạo secret (bắt buộc có trước khi deploy; giá trị có thể để trống)
modal secret create dattts ADMIN_TOKEN=$(openssl rand -hex 24) API_KEYS=key-cua-ban-1,key-cua-ban-2
#   Nếu trang web đặt ở tên miền khác, thêm ALLOWED_ORIGINS=https://dattts.com,https://www.dattts.com,...

# 3. Tải mô hình vào Volume (chạy một lần, có đọc thử một câu)
modal run backend/modal_app.py::download_models

# 4. Deploy
modal deploy backend/modal_app.py
#    Modal in ra URL dạng https://<workspace>--dattts-web.modal.run
```

Kiểm tra sau khi deploy:

```bash
curl https://<workspace>--dattts-web.modal.run/api/health
# {"ok":true,"engine":"vieneu-v3-turbo","voices":25,...}
```

Các tuỳ chọn được đọc trên máy bạn lúc chạy `modal deploy`:

```bash
DATTTS_GPU=T4 modal deploy backend/modal_app.py                               # worker chạy GPU
DATTTS_DOMAINS=dattts.com,www.dattts.com modal deploy backend/modal_app.py    # gắn tên miền riêng
```

Xem log bằng `modal app logs dattts`. Xem danh sách chờ:

```bash
curl -H "Authorization: Bearer $ADMIN_TOKEN" "https://<url>/api/admin/waitlist?format=csv"
```

### Chi phí: CPU hay GPU?

Modal tính tiền theo giây cho CPU, RAM và GPU trong lúc container chạy. Hãy xem giá hiện tại tại [modal.com/pricing](https://modal.com/pricing); các gói thường có một khoản credit miễn phí mỗi tháng.

- **CPU (mặc định, khuyên dùng khi mới bắt đầu):** bản int8 trên 4 CPU đọc nhanh hơn thời gian thực khoảng 2 lần. Chỉ trả tiền khi đang có người dùng, và chi phí cho mỗi phút âm thanh thấp.
- **GPU (`DATTTS_GPU=T4`, `L4`, …):** nhanh hơn với văn bản dài và khi nhiều người dùng cùng lúc, nhưng giá mỗi giây cao hơn nhiều. Đáng bật khi lưu lượng đã đều. Lưu ý: nhánh GPU (PyTorch, `vieneu[cuda]`) chưa được chạy thử thực tế, nên hãy kiểm tra bằng `download_models` trước.
- **`scaledown_window`:** worker giữ ấm 300 giây và web giữ 600 giây sau request cuối. Tăng giá trị này thì ít khởi động nguội hơn nhưng tốn tiền chờ.
- **`min_containers`:** mặc định là 0, nên khi không có ai dùng thì không tốn tiền, nhưng request đầu tiên phải chờ container khởi động và nạp mô hình (khoảng 10–30 giây). Nếu muốn luôn sẵn sàng, thêm `min_containers=1` vào `@app.cls(...)` của `TTSWorker` trong `modal_app.py`. Khi đó bạn trả tiền cho 4 CPU chạy suốt 24/7, nên hãy cân nhắc chỉ đặt cho `web` (rẻ, 1 CPU).
- **Cache:** cùng văn bản, giọng, tốc độ, định dạng và từ điển thì trả file cũ ngay, không tốn công dựng lại và không trừ quota.

## Thanh toán với Polar

DatTTS bán gói qua **Polar** (polar.sh). Polar là *Merchant of Record*, tức bên bán hàng chính thức: Polar thu tiền thẻ quốc tế, Apple Pay và Google Pay, xuất hoá đơn, lo thuế bán hàng ở nước ngoài, rồi chuyển tiền về tài khoản Stripe của bạn. DatTTS không cần hệ thống tài khoản riêng. **API key của khách chính là License Key do Polar cấp** (tiền tố `DATTTS`). Polar gửi key qua email khi khách thanh toán và tự thu hồi key khi gói hết hạn.

Trong `web/config.js`, giá được niêm yết bằng USD và có thêm số tiền quy đổi sang VNĐ để tham khảo. Khi chưa có `POLAR_ACCESS_TOKEN`, trang hiện "Sắp mở bán" cùng form danh sách chờ.

> Làm hết các bước dưới đây ở **sandbox** trước (<https://sandbox.polar.sh>). Sandbox và production là hai hệ thống tách biệt hoàn toàn: tài khoản, sản phẩm, benefit, token và webhook đều phải tạo lại khi chuyển sang thật.

### Bước 1. Tạo tài khoản và tổ chức

1. Vào <https://sandbox.polar.sh> và đăng nhập bằng GitHub hoặc email.
2. Tạo **Organization**, ví dụ tên `DatTTS` với slug `dattts`.
3. Vào **Settings → General** và copy **Organization ID** (dạng UUID). Đây là giá trị `POLAR_ORGANIZATION_ID`.
4. Điền website `https://dattts.com`, email hỗ trợ `support@dattts.com` và mô tả sản phẩm. Lúc duyệt tài khoản, Polar sẽ xem trang web, ba trang pháp lý (`/dieu-khoan.html`, `/bao-mat.html`, `/hoan-tien.html`) và bảng giá. Trước khi gửi duyệt, hãy điền tên pháp lý vào ba trang đó (xem mục TODO).

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

UUID thật nằm trong `backend/.env.example`. DatTTS đọc giá và chu kỳ (`recurring_interval` của sản phẩm) trực tiếp từ Polar, nên khi bạn đổi giá trên Polar, bảng giá trên web cũng tự đổi theo (cache 10 phút). Nhãn "Save X%" cũng tính từ giá thật (làm tròn tới số nguyên gần nhất): gói tháng so với 4 × giá tuần (hiện **37%**), gói năm so với 12 × giá tháng (hiện **17%**, chính xác là 16,5%).

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

- **URL:** `https://dattts.com/api/billing/webhook`. Nếu chưa trỏ tên miền thì dùng `https://<workspace>--dattts-web.modal.run/api/billing/webhook`.
- **Format:** `Raw`
- **Events**, chọn các sự kiện sau:
  - `subscription.created`, `subscription.active`, `subscription.updated`, `subscription.canceled`, `subscription.uncanceled`, `subscription.revoked`
  - `order.paid`
  - `benefit_grant.created`, `benefit_grant.revoked`
  - `customer.state_changed`

Sau khi tạo, copy **Secret** (dạng `whsec_...`). Đây là giá trị `POLAR_WEBHOOK_SECRET`.

DatTTS kiểm tra chữ ký của webhook. Webhook sai chữ ký nhận 403, còn webhook hợp lệ nhận 202. Khi có sự kiện `subscription.revoked`, `subscription.updated`, `benefit_grant.revoked` hoặc `customer.state_changed`, DatTTS xoá ngay bộ nhớ đệm key của khách đó, nên key bị thu hồi sẽ ngừng chạy lập tức. Mọi sự kiện đều được ghi lại và xem được qua `/api/admin/billing`.

Polar gửi lại tối đa 10 lần. Nếu endpoint lỗi 10 lần liên tiếp, Polar sẽ **tắt** webhook. Vì vậy, sau mỗi lần deploy, hãy vào trang webhook xem tab **Deliveries**.

### Bước 7. Đưa cấu hình vào Modal secret

`modal secret create --force` ghi đè **toàn bộ** secret, nên phải điền lại cả `ADMIN_TOKEN` và `API_KEYS` cũ:

```bash
modal secret create dattts --force \
  ADMIN_TOKEN=<mã-quản-trị-cũ> \
  API_KEYS=<key-nội-bộ-cũ> \
  PUBLIC_BASE_URL=https://dattts.com \
  POLAR_SERVER=sandbox \
  POLAR_ACCESS_TOKEN=polar_oat_xxxxxxxxxxxxxxxxxxxxxxxx \
  POLAR_ORGANIZATION_ID=<uuid-tổ-chức> \
  POLAR_WEBHOOK_SECRET=whsec_xxxxxxxxxxxxxxxxxxxxxxxx \
  POLAR_PRODUCT_PRO_WEEK=<uuid-sản-phẩm-Pro-tuần> \
  POLAR_BENEFIT_PRO_WEEK=<uuid-benefit-Pro-tuần> \
  POLAR_PRODUCT_PRO_MONTH=<uuid-sản-phẩm-Pro-tháng> \
  POLAR_BENEFIT_PRO_MONTH=<uuid-benefit-Pro-tháng> \
  POLAR_PRODUCT_PRO_YEAR=<uuid-sản-phẩm-Pro-năm> \
  POLAR_BENEFIT_PRO_YEAR=<uuid-benefit-Pro-năm> \
  PLAN_PRO_WEEK_CHARS=250000 \
  PLAN_PRO_MONTH_CHARS=1000000 \
  PLAN_PRO_YEAR_MONTHLY_CHARS=1000000

modal deploy backend/modal_app.py      # secret chỉ có hiệu lực ở container mới, nên phải deploy lại
```

Kiểm tra lại cấu hình:

```bash
curl -s https://dattts.com/api/billing/plans          # "enabled": true, giá 199 / 499 / 4999, save_percent 37 / 17
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
2. Ở trang thanh toán của Polar sandbox, dùng thẻ thử của Stripe: **4242 4242 4242 4242**, ngày hết hạn bất kỳ trong tương lai (ví dụ 12/34), CVC bất kỳ (ví dụ 123), và một email thật của bạn để nhận key.
3. Polar chuyển về `https://dattts.com/checkout-success.html?checkout_id=...`. Trang này phải báo thanh toán thành công trong vài giây.
4. Mở email từ Polar và copy key `DATTTS-...`. Dán key vào ô trên trang cảm ơn, hoặc dùng nút nhập API key trong phòng thu. Phòng thu phải hiện gói **Pro Monthly** và hạn mức **0 / 1,000,000** ký tự tháng này.
5. Tạo thử một đoạn. Số ký tự đã dùng trong tháng phải tăng lên, còn lượt miễn phí theo IP giữ nguyên.
6. Bấm **Quản lý gói** và kiểm tra rằng cổng khách hàng Polar mở ra. Lưu ý: khách tự huỷ trong cổng chỉ có hiệu lực **cuối kỳ** (`cancel_at_period_end`, chỉ có `subscription.canceled`), key vẫn dùng được tới hết kỳ. Để thử thu hồi ngay, vào dashboard Polar sandbox → **Customers** (hoặc **Subscriptions**) → chọn subscription → **Revoke**. Sau đó kiểm tra ở **Settings → Webhooks → Deliveries** rằng `subscription.revoked` và `benefit_grant.revoked` được nhận với mã 202, và key bị từ chối (401, phòng thu báo key hết hiệu lực và gỡ key).
7. Trong Polar, vào **Settings → Webhooks → Deliveries**. Mọi lần gửi phải có mã 202.

Nếu cần thử nhiều lần, có thể đặt giá $0 hoặc tạo mã giảm giá 100% ở sandbox.

### Bước 9. Chuyển sang production

1. Đăng nhập <https://polar.sh> (production) và **làm lại bước 1–6** ở đây: tổ chức, tài khoản nhận tiền, 3 sản phẩm, 3 benefit License Keys, token và webhook. Mọi UUID, token và secret đều khác bản sandbox (UUID production của tổ chức "Dat TTS" đã có trong `backend/.env.example`).
2. Gửi tài khoản cho Polar duyệt và chờ được duyệt.
3. Chạy lại `modal secret create dattts --force ...` như bước 7, đổi thành `POLAR_SERVER=production` và điền các giá trị production. **Không** đặt `POLAR_API_BASE`.
4. Chạy `modal deploy backend/modal_app.py`, kiểm tra `/api/billing/plans`, rồi tự mua thử một gói bằng thẻ thật và hoàn tiền cho chính mình trong Polar.

Muốn tạm dừng bán, chỉ cần bỏ `POLAR_ACCESS_TOKEN` khỏi secret rồi deploy lại. Trang sẽ quay về "Sắp mở bán", và mọi phần khác vẫn chạy bình thường.

### Chạy với Polar giả ở máy local

`scripts/fake_polar.py` là một Polar giả, mô phỏng các endpoint mà DatTTS dùng. Nó không gọi polar.sh và không cần tài khoản. Bản giả gồm:

- trang thanh toán giả và cổng khách hàng giả;
- một nút để gửi webhook đã ký đúng;
- sẵn các key thử: `DATTTS-PRO-WEEK-TEST`, `DATTTS-PRO-MONTH-TEST`, `DATTTS-PRO-YEAR-TEST` (Pro tuần / tháng / năm) và `DATTTS-REVOKED-TEST` (đã bị thu hồi);
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
  -d '{"type":"subscription.revoked","key":"DATTTS-PRO-MONTH-TEST","revoke":true}'
```

Các endpoint khác của bản giả:

- `/_fake/state`: xem toàn bộ trạng thái;
- `/_fake/reset`: đưa về dữ liệu mẫu ban đầu;
- `/_fake/revoke`: thu hồi key mà không gửi webhook.

Xem đầy đủ trong docstring của file.

## Trỏ tên miền dattts.com

### Cách A: tên miền riêng của Modal (đơn giản nhất, một nguồn duy nhất)

Modal cho phép gắn tên miền riêng vào web endpoint qua tham số `custom_domains` của `@modal.asgi_app`. `modal_app.py` đã đọc tham số này từ `DATTTS_DOMAINS`. **Tính năng này cần gói Modal có hỗ trợ custom domain.** Hãy xem gói nào có và cách cấu hình DNS hiện tại trong tài liệu của Modal (mục *Custom domains* trong hướng dẫn về web endpoint, tại [modal.com/docs](https://modal.com/docs)).

1. Thêm tên miền `dattts.com` trong trang cài đặt workspace của Modal, rồi tạo bản ghi DNS (CNAME/A) mà Modal yêu cầu tại nhà đăng ký tên miền hoặc Cloudflare. Nếu dùng Cloudflare, tắt proxy (đám mây xám) để Modal cấp được chứng chỉ.
2. Chạy `DATTTS_DOMAINS=dattts.com,www.dattts.com modal deploy backend/modal_app.py`.
3. Giữ `web/config.js` như mặc định (`DATTTS_API = ""`), vì trang và API cùng nguồn.

### Cách B: Cloudflare Pages cho `web/`, API trên Modal

Dùng cách này khi gói Modal của bạn chưa có custom domain, hoặc bạn muốn trang tĩnh nằm trên CDN.

1. Sửa `web/config.js`:
   ```js
   window.DATTTS_API = window.DATTTS_API ?? "https://<workspace>--dattts-web.modal.run";
   ```
2. Cloudflare Dashboard → Workers & Pages → Create → Pages, rồi kết nối repo hoặc tải lên thư mục `web/`. Không cần lệnh build, và thư mục xuất ra là `web`. Có thể dùng CLI thay cho dashboard: `npx wrangler pages deploy web --project-name dattts`.
3. Trong Pages → Custom domains, thêm `dattts.com` và `www.dattts.com`. Nếu tên miền đã nằm trên Cloudflare thì DNS được tạo tự động.
4. Đảm bảo `ALLOWED_ORIGINS` trong secret `dattts` có `https://dattts.com,https://www.dattts.com` (đây là giá trị mặc định). API đã bật CORS cho mọi endpoint, kể cả tua audio bằng `Range`, và việc này đã được kiểm thử khi trang và API ở hai nguồn khác nhau.

## Việc còn phải làm (TODO)

- **Tự xoá file cũ.** Blob trong Volume `dattts-files` hiện được giữ vô thời hạn, nên `web/bao-mat.html` chỉ hứa "lưu trong bộ nhớ đệm, xoá khi bạn yêu cầu qua support@dattts.com" (xoá tay). Nếu muốn hứa thời hạn cố định (ví dụ 30 ngày), trước hết thêm một hàm Modal chạy theo lịch (`@app.function(schedule=modal.Period(days=1))`) xoá blob cũ, kèm bản tương đương cho `LocalStore`, rồi mới sửa trang.
- **Điền tên pháp lý** thay cho `[Tên chủ sở hữu / doanh nghiệp]` trong `web/dieu-khoan.html`, `web/bao-mat.html`, `web/hoan-tien.html` trước khi gửi Polar duyệt tài khoản.
- Trang pháp lý ghi bộ đếm ký tự theo key chỉ giữ trong tháng dương lịch đang dùng, chính sách hoàn tiền xét "dưới 10% hạn mức tháng theo bộ đếm của chúng tôi", và bản băm key trong bộ đệm tối đa 24 giờ. Nếu đổi cách lưu ở backend (ví dụ lưu bộ đếm theo kỳ thanh toán Polar trên Volume), hãy cập nhật `web/bao-mat.html` và `web/hoan-tien.html`.

- **Bộ đếm ký tự theo tháng trên Modal.** Mục trong `modal.Dict` hết hạn sau 7 ngày không dùng, nên khách trả phí nghỉ hơn 7 ngày trong tháng có thể bị đặt lại bộ đếm (có lợi cho khách). Nếu cần chính xác, hãy sao lưu bộ đếm sang Volume giống như danh sách chờ.

## Giấy phép và ghi công

- **Mô hình:** VieNeu-TTS v3 Turbo, giấy phép **Apache-2.0**. Theo model card, các giọng có sẵn (preset) được phép dùng thương mại.
- **Ghi công bắt buộc:** giữ dòng **"VieNeu-TTS by Phạm Nguyễn Ngọc Bảo"** trên trang web và trong tài liệu sản phẩm. Kèm theo file LICENSE (Apache-2.0) của mô hình khi phân phối lại trọng số.
- vieneu chèn watermark không nghe được vào âm thanh (`apply_watermark=True`, mặc định). DatTTS giữ nguyên mặc định này.
- Trước khi kinh doanh, hãy đọc lại model card trên Hugging Face, vì điều khoản có thể được cập nhật. Không dùng giọng để mạo danh người thật.

## Ví dụ dùng API bằng curl

Thay `API=http://127.0.0.1:8000` bằng URL Modal hoặc `https://dattts.com` khi đã triển khai. Mọi lỗi đều có dạng `{"error":{"code":"...","message":"<thông báo tiếng Việt>"}}`.

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
  -d '{"email":"ban@example.vn","plan":"ca-nhan"}'
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
