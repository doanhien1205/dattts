# DatTTS — đặc tả kỹ thuật (hợp đồng giữa các phần)

Mục tiêu: dattts.com, dịch vụ chuyển văn bản thành giọng nói tiếng Việt.
Engine: **VieNeu-TTS v3 Turbo** (`pip install vieneu==3.8.3`, repo HF `pnnbao-ump/VieNeu-TTS-v3-Turbo`,
Apache-2.0, cho phép dùng thương mại kể cả 25 giọng có sẵn). Âm thanh 48 kHz mono float32.
Triển khai: Modal (modal==1.x). Chạy được hoàn toàn ở máy local để phát triển/kiểm thử.

## Môi trường phát triển (máy này)

- venv có sẵn mọi thứ: `/tmp/claude-0/-home-user/36470a4f-7cb5-5035-9ace-05cdda07e46c/scratchpad/venv`
  (vieneu 3.8.3, onnxruntime 1.31, fastapi, uvicorn, pytest, httpx, modal 1.6.3). Kích hoạt bằng `. <venv>/bin/activate`.
- Model đã tải sẵn: `DATTTS_MODEL_DIR=/tmp/claude-0/-home-user/36470a4f-7cb5-5035-9ace-05cdda07e46c/scratchpad/vieneu-models`
- Chromium + Playwright có sẵn (`require('playwright')` trong node).
- ffmpeg có ở `/usr/bin/ffmpeg`. Máy có 4 CPU, không GPU, không có token Modal (không deploy được, chỉ kiểm tra import/cấu trúc).

### Bẫy đã biết khi dùng vieneu (bắt buộc xử lý trong engine.py)
onnxruntime ≥1.2x từ chối file `.data` ngoài nếu đi qua symlink của cache HF
("External data path escapes model directory"). Cách đã kiểm chứng chạy được:

```python
from pathlib import Path
from huggingface_hub import hf_hub_download
import vieneu._v3_turbo_engine.onnx_runtime_lite as L
ROOT = Path(model_dir)
def _fetch(repo, files, subfolder):
    d = ROOT / repo.replace("/", "__")
    for fn in files:
        try: hf_hub_download(repo, fn, subfolder=subfolder or None, local_dir=d)
        except Exception:
            if fn.endswith(".json"): continue
            raise
    return d / (subfolder or "")
L.OnnxV3LiteEngine._fetch = staticmethod(_fetch)
from vieneu import Vieneu
tts = Vieneu(mode="v3turbo", precision="int8")   # CPU/ONNX; GPU: device="cuda" → PyTorch
wav = tts.infer("Xin chào", voice="Trúc Ly")      # np.float32, tts.sample_rate == 48000
```
Đo trên máy này (4 CPU, int8): nạp 5,7 s; tạo nhanh ~2× thời gian thực.
`tts.list_preset_voices()` trả `[(label, name)]`; metadata giọng nằm trong
`vieneu/assets/voices_v3_turbo.json` → `presets[name]` có `gender`, `region` (Bắc/Trung/Nam),
`style` (tu_nhien/tin_tuc/doc_truyen), `featured` (1..10 hoặc không có), `description`.
`tts.infer` tự chuẩn hóa văn bản (số, ngày, tiền, đơn vị) rất tốt — KHÔNG viết lại bộ chuẩn hóa số.
Tham số `apply_watermark` mặc định True (watermark không nghe được); giữ mặc định.

## Bố cục mã

```
backend/
  dattts/__init__.py
  dattts/textproc.py   # thuần Python, không phụ thuộc engine
  dattts/audio.py      # ffmpeg encode, ghép, tốc độ, phụ đề SRT/VTT
  dattts/engine.py     # Engine protocol + VieNeuEngine + FakeEngine
  dattts/render.py     # điều phối: script → câu → engine → ghép → encode
  dattts/store.py      # KV + Blob: LocalStore (thư mục) và ModalStore (Dict + Volume)
  dattts/settings.py   # cấu hình từ biến môi trường
  dattts/api.py        # create_app(...) → FastAPI
  server_local.py      # chạy local: python server_local.py [--fake] [--port 8000]
  modal_app.py         # triển khai Modal
  requirements.txt
  tests/               # pytest; test nhanh dùng FakeEngine; test thật đánh dấu @pytest.mark.real
web/                   # trang tĩnh (index.html, styles.css, app.js, config.js) — được api phục vụ tại "/"
README.md              # hướng dẫn tiếng Việt: chạy local, deploy Modal, trỏ tên miền
```

## Giao diện Python (bắt buộc đúng tên/chữ ký)

```python
# textproc.py
@dataclass class Segment: voice: str | None; text: str          # voice = id giọng hoặc None (mặc định)
def clean_text(text: str) -> str                                 # NFC, bỏ ký tự điều khiển, gộp khoảng trắng (giữ xuống dòng đoạn)
def count_chars(text: str) -> int                                # len(clean_text(text)) — ký tự tính phí
def parse_script(text: str) -> list[Segment]                     # cú pháp nhiều giọng, xem dưới
def apply_dictionary(text: str, entries: list[dict]) -> str      # entries [{"from": "...", "to": "..."}], khớp nguyên từ, không phân biệt hoa thường, ưu tiên cụm dài
def apply_builtin_rules(text: str) -> str                        # sửa những chỗ engine đọc kém: "Q3/2026"→"quý 3 năm 2026", "NĐ-CP"→"nghị định chính phủ", "USD"→"đô la Mỹ", "VND"/"VNĐ"→"đồng", "dattts.com"→"Đạt tê tê ét chấm com", "DatTTS"→"Đạt tê tê ét", ...
def split_sentences(text: str, max_chars: int = 220) -> list[tuple[str, str]]   # [(câu gốc, kiểu ngắt sau câu: "sentence"|"paragraph"|"clause")], không cắt giữa từ, không làm hỏng số "1.000", "3,5", "TP.HCM", "v.v."

# Cú pháp nhiều giọng: thẻ [Tên giọng] hoặc [id-giong] ở bất kỳ đâu; văn bản sau thẻ dùng giọng đó tới thẻ kế tiếp.
# Văn bản trước thẻ đầu tiên dùng giọng mặc định. "[Trúc Ly] Chào anh. [Thiện Minh] Chào em."
# Dấu ngoặc vuông không khớp tên giọng nào thì GIỮ NGUYÊN là văn bản thường? → Không: parse_script không biết danh sách giọng,
# nên nó trả Segment(voice="<chuỗi trong ngoặc>") và render.py là nơi resolve; tên không tồn tại → lỗi 400 "unknown_voice".

# engine.py
class Engine(Protocol):
    name: str                      # "vieneu-v3-turbo" | "fake"
    sample_rate: int
    def voices(self) -> list[dict] # mỗi phần tử: {"id","name","gender","region","style","style_label","featured","description"}
    def synth(self, text: str, voice_id: str) -> np.ndarray   # float32 mono [-1,1]
def voice_id_from_name(name: str) -> str   # "Trúc Ly"→"truc-ly", "Adam bựa"→"adam-bua", "Thiền Tâm Đức"→"thien-tam-duc" (bỏ dấu, đ→d, slug)
class VieNeuEngine:  __init__(self, model_dir: str, device: str = "cpu", precision: str = "int8")  # có lock: synth tuần tự an toàn luồng
class FakeEngine:    __init__(self, sample_rate=48000)  # tạo âm sin ngắn, độ dài ~ 0.06 s/ký tự; có 4 giọng giả cùng định dạng id/name/region…; tất định
DEFAULT_VOICE_ID = "truc-ly"   # FakeEngine cũng phải có id này

# audio.py
def apply_speed(wav, sr, speed: float) -> np.ndarray        # 0.7–1.5, giữ cao độ (ffmpeg atempo hoặc tương đương), speed==1 trả nguyên
def silence(sr, seconds) -> np.ndarray
def encode(wav, sr, fmt: str) -> bytes                      # "mp3" (128k, 44.1kHz OK), "wav" (pcm16), "ogg" (opus hoặc vorbis); dùng ffmpeg qua stdin/stdout
def to_srt(segments: list[dict]) -> str                     # segments [{"index","text","start","end"}] (giây)
def to_vtt(segments: list[dict]) -> str
CONTENT_TYPES = {"mp3":"audio/mpeg","wav":"audio/wav","ogg":"audio/ogg","srt":"application/x-subrip","vtt":"text/vtt"}

# render.py
@dataclass class RenderRequest: text: str; voice: str = DEFAULT_VOICE_ID; speed: float = 1.0; format: str = "mp3"; dictionary: list[dict] = field(default_factory=list)
def request_key(req: RenderRequest, engine_name: str) -> str   # sha256 của mọi thứ ảnh hưởng tới âm thanh → 20 ký tự hex; dùng làm id file
def plan(req, engine) -> list[dict]    # [{"index","voice","text"(gốc, để phụ đề),"spoken"(sau từ điển+luật),"gap_after"(giây)}]; resolve tên giọng → id; lỗi → RenderError(code, message_vi, status)
def render(req, engine, progress: Callable[[int,int],None] | None = None) -> dict
    # trả {"id","chars","duration","format","segments":[{"index","voice","text","start","end"}],
    #      "audio": bytes, "srt": str, "vtt": str}
    # gap: 0.12 s sau "clause", 0.3 s sau "sentence", 0.6 s sau "paragraph" hoặc đổi giọng.
    # tốc độ áp lên từng câu trước khi ghép (để timestamp phụ đề đúng).
class RenderError(Exception): code: str; message: str; status: int
```

## HTTP API (FastAPI, tiền tố /api, JSON UTF-8)

Lỗi luôn có dạng `{"error": {"code": "...", "message": "<tiếng Việt cho người dùng>"}}`.

| Phương thức | Đường dẫn | Ghi chú |
|---|---|---|
| GET | /api/health | `{"ok":true,"engine":"vieneu-v3-turbo","voices":25}` |
| GET | /api/voices | `{"default":"truc-ly","voices":[{id,name,gender,region,style,style_label,featured,description,sample_url}]}`; `style_label`: tu_nhien→"Tự nhiên", tin_tuc→"Tin tức", doc_truyen→"Kể chuyện"; sắp xếp featured trước |
| GET | /api/voices/{id}/sample.mp3 | câu mẫu ngắn của giọng đó, tạo một lần rồi cache; không tính quota |
| POST | /api/tts | body `{text, voice?, speed?, format?, dictionary?}`; đồng bộ, tối đa `SYNC_MAX_CHARS`=3000 ký tự (vượt → 413 `too_long`, gợi ý dùng /api/jobs) |
| POST | /api/jobs | cùng body, tối đa `JOB_MAX_CHARS`=100000; trả 202 `{"job_id","status":"queued"}` |
| GET | /api/jobs/{job_id} | `{"job_id","status":"queued|running|done|error","progress":0..1,"done":int,"total":int,"result"?:<như /api/tts>,"error"?:{code,message}}` |
| GET | /api/files/{id}.{mp3|wav|ogg|srt|vtt} | trả file; `?download=1` → `Content-Disposition: attachment; filename="dattts-{id}.{ext}"` |
| GET | /api/quota | `{"used","limit","remaining","reset_at"}` (ISO, nửa đêm giờ VN UTC+7) |
| POST | /api/waitlist | `{email, plan?, note?}` → `{"ok":true,"position":n}`; email hợp lệ; trùng email không tạo thêm (trả vị trí cũ) |
| GET | /api/admin/waitlist | header `Authorization: Bearer <ADMIN_TOKEN>`; thiếu/sai → 401 |

Phản hồi của /api/tts (và `result` của job):
```json
{"id":"…20hex…","chars":136,"duration":7.61,"format":"mp3","cached":false,
 "audio_url":"/api/files/<id>.mp3","srt_url":"/api/files/<id>.srt","vtt_url":"/api/files/<id>.vtt",
 "segments":[{"index":0,"voice":"truc-ly","text":"Xin chào, mình là DatTTS.","start":0.0,"end":1.92}],
 "quota":{"used":136,"limit":20000,"remaining":19864,"reset_at":"…"}}
```

Quy tắc:
- Validate: text rỗng → 400 `empty_text`; speed ngoài [0.7,1.5] → 400 `bad_speed`; format lạ → 400 `bad_format`; dictionary tối đa 200 mục, mỗi vế ≤ 100 ký tự → 400 `bad_dictionary`; giọng lạ → 400 `unknown_voice` (message liệt kê vài tên giọng hợp lệ).
- Quota mỗi ngày theo khách: API key hợp lệ (header `X-API-Key`, danh sách trong env `API_KEYS`, phân tách bằng dấu phẩy) → không giới hạn; còn lại theo IP, `FREE_DAILY_CHARS`=20000. IP lấy từ `request.client.host`; chỉ tin `X-Forwarded-For` khi `TRUST_PROXY=1` (Modal đặt =1), lấy phần tử thứ `TRUST_PROXY_HOPS` (mặc định 1) TÍNH TỪ PHẢI — phần bên trái do khách tự gửi, không tin được. Hết quota → 429 `quota_exceeded`. Kết quả cache (cùng request_key) KHÔNG tính quota. Job tính quota khi nhận job.
- Cache: nếu blob `<id>.<format>` và `<id>.json` (metadata) đã có thì trả ngay `cached:true`.
- CORS: cho phép `ALLOWED_ORIGINS` (mặc định `https://dattts.com,https://www.dattts.com,http://localhost:8000,http://127.0.0.1:8000`).
- `/` phục vụ thư mục `web/` (StaticFiles html=True) nếu tồn tại (đường dẫn qua env `WEB_DIR`).

## Store (store.py)

```python
class Store(Protocol):
    def kv_get(self, key: str) -> Any | None
    def kv_set(self, key: str, value: Any) -> None            # value JSON-serializable
    def kv_incr(self, key: str, amount: int) -> int           # trả giá trị mới; nguyên tử trong một tiến trình
    def blob_put(self, name: str, data: bytes) -> None
    def blob_get(self, name: str) -> bytes | None
    def blob_exists(self, name: str) -> bool
class LocalStore(Store): __init__(self, root: str)   # kv trong root/kv.json (có lock), blob trong root/files/
class ModalStore(Store): __init__(self, dict_name="dattts-kv", volume_name="dattts-files", mount="/files")  # modal.Dict + modal.Volume (commit sau put, reload khi get trượt)
```

## Renderer (đặt trong api.py hoặc store.py — tuỳ, nhưng api chỉ dùng qua giao diện này)
```python
class Renderer(Protocol):
    def voices(self) -> list[dict]
    def engine_name(self) -> str
    def render(self, req: RenderRequest) -> dict          # như render.render nhưng ĐÃ lưu blob, trả metadata (không có bytes)
    def submit(self, job_id: str, req: RenderRequest) -> None   # chạy nền, cập nhật kv "job:<id>"
    def sample(self, voice_id: str) -> bytes
class LocalRenderer  # engine trong tiến trình + ThreadPoolExecutor(1)
# Modal: renderer gọi lớp TTSWorker qua .remote() / .spawn()
```

## Modal (modal_app.py)
- `app = modal.App("dattts")`; image `debian_slim(python_version="3.12")` + `apt_install("ffmpeg")` + pip theo requirements + `add_local_python_source("dattts")` + `add_local_dir(<repo>/web, "/root/web")`.
- Volume `dattts-models` (mount `/models`, `DATTTS_MODEL_DIR=/models`), Volume `dattts-files` (mount `/files`), Dict `dattts-kv`.
- Secret `dattts` (ADMIN_TOKEN, API_KEYS) — README hướng dẫn tạo.
- `@app.cls(cpu=4, memory=6144, scaledown_window=300, volumes=…)` `TTSWorker` với `@modal.enter()` nạp VieNeuEngine; phương thức `render`, `run_job`, `sample`, `voices`. Có tùy chọn GPU qua biến `DATTTS_GPU` (ví dụ `"T4"`), khi đó device="cuda".
- Hàm `download_models` (chạy một lần: `modal run modal_app.py::download_models`).
- `@app.function(...) @modal.concurrent(max_inputs=50) @modal.asgi_app() def web()` dùng `create_app(ModalRenderer, ModalStore, settings)`.
- Phải dùng đúng API của modal 1.x có trong venv (kiểm bằng cách đọc package `modal` đã cài, ví dụ `modal.App.cls`, `modal.concurrent`, `modal.Dict.from_name(..., create_if_missing=True)`, `modal.Volume.from_name(..., create_if_missing=True)`, `modal.FunctionCall`). `python -c "import modal_app"` phải chạy không lỗi.

## Web (web/)
- Giữ nhận diện của trang hiện tại (bảng màu, phông, logo chữ "đ"); copy tiếng Việt tự nhiên.
- `config.js`: `window.DATTTS_API = window.DATTTS_API ?? ""` (rỗng = cùng nguồn).
- Studio: lưới 25 giọng thật từ /api/voices (lọc vùng Bắc/Trung/Nam, giới tính, phong cách; nút nghe mẫu), ô văn bản + đếm ký tự + quota, tốc độ, định dạng, nút "Tạo giọng đọc"; >3000 ký tự tự dùng /api/jobs với thanh tiến độ; trình phát với karaoke theo câu (đồng bộ `audio.currentTime` với `segments`, bấm câu để tua); tải MP3/WAV/OGG + SRT + VTT (`?download=1`); lịch sử 10 bản gần nhất (localStorage, bọc try/catch).
- Nhiều giọng: nút chèn thẻ `[Tên giọng]` tại con trỏ, mẫu "Podcast 2 giọng".
- Từ điển phát âm: bảng thêm/xoá cặp từ → cách đọc, lưu localStorage, gửi kèm request.
- Thanh điệu ma/mà/má/mả/mã/mạ: phát qua API (giọng đang chọn), dự phòng speechSynthesis.
- Đăng ký chờ (waitlist) ở bảng giá → POST /api/waitlist.
- Không có API (lỗi mạng) → banner "đang ở chế độ thử trên trình duyệt" và dùng speechSynthesis như bản cũ.
- Đáp ứng 360px trở lên, chủ đề sáng/tối, `prefers-reduced-motion`, focus thấy được, nhãn aria.

---

# Phần 2 — Thanh toán qua Polar (polar.sh, Merchant of Record) — thay cho VietQR

Polar là bên bán hàng chính thức (MoR): thu tiền thẻ quốc tế/Apple Pay/Google Pay, lo thuế, trả tiền về Stripe Connect Express (Việt Nam được hỗ trợ). Giá niêm yết bằng USD.
Không tự xây hệ thống tài khoản: **API key của khách = License Key do Polar cấp** (benefit "License Keys", prefix `DATTTS`), gắn vào mỗi sản phẩm gói. Khách nhận key qua email của Polar và xem/xoay key trong cổng khách hàng Polar. Hết gói → Polar tự thu hồi key.

## Cấu hình (env, trong Modal secret `dattts`)
| Biến | Ý nghĩa |
|---|---|
| `POLAR_SERVER` | `sandbox` (mặc định) hoặc `production` → base `https://sandbox-api.polar.sh` / `https://api.polar.sh` |
| `POLAR_API_BASE` | ghi đè base URL (dùng cho test với Polar giả) |
| `POLAR_ACCESS_TOKEN` | Organization Access Token, scope: `checkouts:read checkouts:write products:read customer_sessions:write license_keys:read license_keys:write` |
| `POLAR_ORGANIZATION_ID` | UUID tổ chức |
| `POLAR_WEBHOOK_SECRET` | `whsec_...` |
| `POLAR_PRODUCT_PERSONAL`, `POLAR_PRODUCT_UNLIMITED` | UUID sản phẩm |
| `POLAR_BENEFIT_PERSONAL`, `POLAR_BENEFIT_UNLIMITED` | UUID benefit License Key của từng gói → xác định gói từ key |
| `PLAN_PERSONAL_MONTHLY_CHARS` (1000000), `PLAN_UNLIMITED_MONTHLY_CHARS` (20000000, "dùng hợp lý") | hạn mức/tháng |
| `PUBLIC_BASE_URL` | `https://dattts.com` (dùng cho success_url/return_url); local: `http://127.0.0.1:8765` |
Thiếu `POLAR_ACCESS_TOKEN` → billing tắt: các endpoint billing trả 503 `billing_disabled`, web ẩn nút mua và hiện "sắp mở bán", mọi thứ khác vẫn chạy. `API_KEYS` cũ giữ nguyên (key nội bộ, không giới hạn).

## Module `backend/dattts/billing.py`
```python
class PolarClient:   # httpx, timeout 10 s, Authorization: Bearer <token>; base từ settings; tiêm được httpx transport cho test
    def create_checkout(self, product_id, success_url, return_url, metadata, customer_email=None) -> dict   # POST /v1/checkouts/ {"products":[id], ...} → {"id","url","status",...}
    def get_checkout(self, checkout_id) -> dict            # GET /v1/checkouts/{id}  (status: open|expired|confirmed|succeeded|failed)
    def get_product(self, product_id) -> dict              # GET /v1/products/{id} → name, prices[{amount_type, price_amount (cent), price_currency, recurring_interval}], is_archived
    def validate_license_key(self, key) -> dict | None     # POST /v1/license-keys/validate {"key","organization_id"} (server-side, có token) → ValidatedLicenseKey; 404 → None
    def create_customer_session(self, customer_id, return_url) -> str   # POST /v1/customer-sessions/ → customer_portal_url
def verify_webhook(body: bytes, headers, secret: str, tolerance=300) -> bool
    # Standard Webhooks: signed = f"{webhook-id}.{webhook-timestamp}.{body}", HMAC-SHA256, header "webhook-signature" = các mục "v1,<base64>" cách nhau bằng dấu cách.
    # Thử 2 khoá: (a) base64decode(secret bỏ tiền tố "whsec_") — bí mật kiểu mới; (b) utf8 bytes của toàn bộ chuỗi "whsec_..." — bí mật Polar kiểu cũ. So sánh hằng thời gian; timestamp lệch > tolerance → False.
```
- Xác thực key: `resolve_api_key(key)` → `{"kind":"internal"}` nếu thuộc API_KEYS; nếu không, gọi validate (cache kết quả trong kv `lk:<sha256(key)>`: hợp lệ 10 phút, không hợp lệ 60 giây; KHÔNG lưu key gốc) → `{"kind":"polar","license_key_id","customer_id","email","plan"}` với plan suy từ `benefit_id`; benefit lạ → coi như không hợp lệ. Polar lỗi mạng/5xx: dùng cache cũ nếu còn (kể cả đã hết hạn tới 24 giờ), không có → 503 `billing_unavailable`.
- Quota trả phí: kv `usage:<license_key_id>:<YYYY-MM>` theo giờ VN; vượt → 429 `quota_exceeded` (message nêu hạn mức tháng và ngày reset). Key Polar KHÔNG bị tính vào quota IP miễn phí. Key sai → 401 `invalid_api_key` (không rơi về quota miễn phí, để khách biết key hỏng).

## HTTP API bổ sung
| Phương thức | Đường dẫn | Ghi chú |
|---|---|---|
| GET | /api/billing/plans | `{"enabled":bool,"plans":[{"id":"free","name":"Dùng thử","price":0,"currency":"usd","interval":null,"chars":"20.000/ngày"},{"id":"personal","name","price"(cent),"currency","interval":"month","monthly_chars"},{"id":"unlimited",...}]}` — lấy giá thật từ Polar (cache 10 phút), lỗi thì `price:null` |
| POST | /api/billing/checkout | `{plan:"personal"|"unlimited", email?}` → `{"url"}`; success_url = `PUBLIC_BASE_URL + "/thanh-toan.html?checkout_id={CHECKOUT_ID}"` (giữ nguyên chuỗi `{CHECKOUT_ID}`), return_url = `PUBLIC_BASE_URL + "/#bang-gia"`, metadata `{"plan":...}`; giới hạn 10 lần/giờ/IP |
| GET | /api/billing/checkout/{checkout_id} | `{"status","plan","email"(che bớt: a***@gmail.com)}` — để trang cảm ơn biết thanh toán xong chưa; id phải đúng dạng UUID |
| GET | /api/me | cần `X-API-Key` → `{"plan","plan_name","used","limit","reset_at","email"(che bớt)}`; key nội bộ → plan "internal", limit null |
| POST | /api/billing/portal | cần `X-API-Key` (key Polar) → `{"url": customer_portal_url}` (return_url = PUBLIC_BASE_URL) |
| POST | /api/billing/webhook | xác minh chữ ký (sai → 403); 202 cho mọi sự kiện hợp lệ; xử lý trong ≤2 s: `subscription.revoked`, `subscription.updated`, `benefit_grant.revoked`, `customer.state_changed` → xoá cache `lk:*` của customer đó (lưu chỉ mục kv `lkc:<customer_id>` → danh sách hash); ghi nhật ký sự kiện gọn (`billing_events`, tối đa 500 mục) cho admin; idempotent theo `webhook-id` |
| GET | /api/admin/billing | Bearer ADMIN_TOKEN → số sự kiện gần đây, tổng ký tự đã dùng theo gói tháng này |
/api/quota khi có `X-API-Key` hợp lệ trả hạn mức tháng của key đó.

## Web
- Bảng giá lấy từ /api/billing/plans: hiển thị giá USD (ví dụ "$2.99 / tháng") kèm "≈ 76.000đ" (tỷ giá cố định trong config.js `DATTTS_VND_PER_USD = 25400`, ghi chú "quy đổi tham khảo"). Nút "Mua gói …" → POST checkout → `location.href = url`. Billing tắt → nút "Sắp mở bán" + form chờ như cũ.
- Trang `web/thanh-toan.html`: đọc `checkout_id`, hỏi /api/billing/checkout/{id} mỗi 2 s tới khi `succeeded` (tối đa 60 s), rồi hướng dẫn: "Polar đã gửi API key (bắt đầu bằng DATTTS) tới email …; dán key vào Studio để dùng". Có ô dán key ngay tại trang.
- Studio: nút "Nhập API key" → hộp nhập (lưu localStorage, gửi header `X-API-Key` cho mọi request /api); hiện gói + hạn mức tháng từ /api/me; nút "Quản lý gói" → /api/billing/portal; nút "Đăng xuất key". Key sai → thông báo rõ và gỡ key.
- FAQ: bỏ VietQR/MoMo/ZaloPay; ghi: thanh toán bằng thẻ Visa/Mastercard/JCB/Amex (kể cả thẻ ghi nợ quốc tế của ngân hàng Việt Nam), Apple Pay, Google Pay qua Polar; hoá đơn do Polar phát hành; huỷ bất kỳ lúc nào trong cổng khách hàng.
- Trang pháp lý (Polar duyệt tài khoản cần có): `web/dieu-khoan.html` (Điều khoản dịch vụ), `web/bao-mat.html` (Chính sách bảo mật), `web/hoan-tien.html` (Chính sách hoàn tiền: hoàn trong 14 ngày nếu dùng < 10% hạn mức, yêu cầu qua support@dattts.com; Polar xử lý hoàn tiền). Mỗi trang có tóm tắt tiếng Anh ở cuối (Polar review bằng tiếng Anh), nêu rõ Polar là Merchant of Record/reseller, email hỗ trợ support@dattts.com, giấy phép VieNeu-TTS. Footer mọi trang link tới 3 trang này.
