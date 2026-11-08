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
| `API_KEYS` | trống | Các key không giới hạn, cách nhau bằng dấu phẩy |
| `ADMIN_TOKEN` | trống | Bật `/api/admin/waitlist`. Để trống thì endpoint trả 404. |
| `TRUST_PROXY` | `0` | Đặt `1` để lấy IP từ `X-Forwarded-For` khi chạy sau proxy (lấy phần tử do proxy thêm vào, tính từ phải) |
| `TRUST_PROXY_HOPS` | `1` | Số proxy tin cậy đứng trước app; IP khách là phần tử thứ N tính từ phải của `X-Forwarded-For` |
| `JOB_QUEUE_STALE_SECONDS` | `14400` | Job còn xếp hàng quá lâu (giây) thì coi như mất và hoàn ký tự |
| `ALLOWED_ORIGINS` | `https://dattts.com,https://www.dattts.com,http://localhost:8000,http://127.0.0.1:8000` | Danh sách CORS |
| `DATTTS_MODEL_DIR` | `./models` | Thư mục mô hình |
| `DATTTS_GPU` | trống | Đặt giá trị (ví dụ `T4`) để chạy engine trên GPU |
| `WEB_DIR` | `<repo>/web` | Thư mục trang tĩnh |

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

- **Xoá file sau 30 ngày.** `web/bao-mat.html` và `web/dieu-khoan.html` cam kết: file âm thanh, phụ đề và văn bản đi kèm (`<id>.<định dạng>`, `<id>.json`, `.srt`, `.vtt`) bị xoá sau 30 ngày kể từ lần tạo. Backend hiện **chưa** làm việc này (blob trong Volume `dattts-files` được giữ vô thời hạn). Cần thêm một hàm Modal chạy theo lịch (ví dụ `@app.function(schedule=modal.Period(days=1))`) xoá blob cũ hơn 30 ngày, kèm bản tương đương cho `LocalStore`. Cho tới khi xong, hãy sửa lại câu chữ trong hai trang đó.
- **Điền tên pháp lý** thay cho `[Tên chủ sở hữu / doanh nghiệp]` trong `web/dieu-khoan.html`, `web/bao-mat.html`, `web/hoan-tien.html` trước khi gửi Polar duyệt tài khoản.
- Trang pháp lý ghi bộ đếm ký tự theo key giữ tối đa 24 tháng và bản băm key trong bộ đệm tối đa 24 giờ. Nếu đổi cách lưu ở backend, hãy cập nhật `web/bao-mat.html`.

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

# Dùng API key (không giới hạn ký tự)
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
| `quota_exceeded` (429) | Đã hết ký tự miễn phí hôm nay |
| `invalid_api_key` (401) | API key không đúng hoặc gói đã hết hạn |

---

VieNeu-TTS by Phạm Nguyễn Ngọc Bảo · DatTTS
