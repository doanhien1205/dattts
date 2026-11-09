#!/usr/bin/env bash
# Triển khai backend DatTTS lên Modal (SPEC Phần 4). Chạy: scripts/deploy_modal.sh --help
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
SECRET_NAME="dattts"
ENV_FILE="${HOME}/dattts-secret.env"
FIRST_RUN=0
DRY_RUN=0
SKIP_SECRET=0
SKIP_SMOKE=0
WEB_URL=""
EXPECTED_VOICES="${DATTTS_EXPECTED_VOICES:-25}"  # số giọng mong đợi ở /api/voices

usage() {
  cat <<'EOF'
Cách dùng: scripts/deploy_modal.sh [tuỳ chọn]

Triển khai backend DatTTS lên Modal:
  1. kiểm tra lệnh `modal` đã cài và đã đăng nhập (modal token new);
  2. tạo/cập nhật secret `dattts` từ file dotenv (--env-file);
  3. (--first-run) tải mô hình vào Volume: modal run backend/modal_app.py::download_models;
  4. modal deploy backend/modal_app.py, in URL web (https://<workspace>--dattts-web.modal.run);
  5. kiểm tra nhanh /api/health, /api/voices (25 giọng), /api/billing/plans.

Tuỳ chọn:
  --env-file PATH   file dotenv chứa secret (mặc định: ~/dattts-secret.env). Mẫu: deploy/secret.env.example.
                    Bị từ chối nếu file nằm trong repo hoặc quyền rộng hơn 600 (sửa: chmod 600 PATH).
  --first-run       lần đầu triển khai: tải mô hình vào Volume trước khi deploy.
  --skip-secret     không cập nhật secret (dùng secret `dattts` đang có trên Modal).
  --skip-smoke      không kiểm tra sau khi deploy.
  --url URL         URL web đã biết (không dò từ output của `modal deploy`).
  --dry-run         chỉ in các lệnh sẽ chạy (vẫn kiểm tra file secret), không gọi Modal.
  -h, --help        in hướng dẫn này.

Ví dụ:
  cp deploy/secret.env.example ~/dattts-secret.env && chmod 600 ~/dattts-secret.env
  scripts/deploy_modal.sh --first-run            # lần đầu
  scripts/deploy_modal.sh                        # các lần sau (cập nhật code + secret)
EOF
}

say()  { printf '==> %s\n' "$*"; }
warn() { printf 'CẢNH BÁO: %s\n' "$*" >&2; }
die()  { printf 'LỖI: %s\n' "$*" >&2; exit 1; }

run() {
  # In lệnh (dạng an toàn để copy) rồi chạy; --dry-run thì chỉ in.
  printf '+'; printf ' %q' "$@"; printf '\n'
  if [[ "$DRY_RUN" == 1 ]]; then return 0; fi
  "$@"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file) [[ $# -ge 2 ]] || die "--env-file cần một đường dẫn"; ENV_FILE="$2"; shift 2 ;;
    --env-file=*) ENV_FILE="${1#*=}"; shift ;;
    --first-run) FIRST_RUN=1; shift ;;
    --skip-secret) SKIP_SECRET=1; shift ;;
    --skip-smoke) SKIP_SMOKE=1; shift ;;
    --url) [[ $# -ge 2 ]] || die "--url cần một địa chỉ"; WEB_URL="$2"; shift 2 ;;
    --url=*) WEB_URL="${1#*=}"; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "tuỳ chọn không hợp lệ: $1" ;;
  esac
done

# ── Đọc giá trị một biến trong file dotenv (không in ra màn hình) ──
env_value() {
  local key="$1" line val
  line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${key}=" "$ENV_FILE" | tail -n 1 || true)"
  [[ -n "$line" ]] || return 0
  val="${line#*=}"
  val="${val%$'\r'}"
  # bỏ dấu nháy bao quanh (nếu có)
  if [[ "$val" =~ ^\"(.*)\"$ || "$val" =~ ^\'(.*)\'$ ]]; then val="${BASH_REMATCH[1]}"; fi
  printf '%s' "$val"
}

file_mode() {
  # GNU stat (Linux) hoặc BSD stat (macOS)
  stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1"
}

check_env_file() {
  [[ -e "$ENV_FILE" ]] || die "không thấy file secret: $ENV_FILE
  Tạo từ mẫu:  cp '$REPO/deploy/secret.env.example' '$ENV_FILE' && chmod 600 '$ENV_FILE'
  rồi điền các giá trị <...> trong file."
  [[ -f "$ENV_FILE" ]] || die "$ENV_FILE không phải file thường"
  [[ -r "$ENV_FILE" ]] || die "không đọc được $ENV_FILE"

  # Đường dẫn thật (giải symlink) — tương thích bash 3.2 / macOS
  local real
  if command -v python3 >/dev/null 2>&1; then
    real="$(python3 -c 'import os,sys;print(os.path.realpath(sys.argv[1]))' "$ENV_FILE")"
  else
    real="$(cd "$(dirname "$ENV_FILE")" && pwd -P)/$(basename "$ENV_FILE")"
  fi
  case "$real" in
    "$REPO"/*) die "file secret nằm TRONG repo ($real) — dễ lỡ tay commit bí mật.
  Hãy chuyển ra ngoài, ví dụ: mv '$ENV_FILE' ~/dattts-secret.env && chmod 600 ~/dattts-secret.env" ;;
  esac

  local mode
  mode="$(file_mode "$real")" || die "không đọc được quyền của $real"
  [[ "$mode" =~ ^[0-7]+$ ]] || die "quyền file không hợp lệ: $mode"
  if (( (8#$mode & 8#7177) != 0 )); then
    die "quyền của $real là $mode, rộng hơn 600 (người khác trên máy có thể đọc bí mật).
  Sửa bằng: chmod 600 '$real'"
  fi

  if grep -Eq '^[[:space:]]*[A-Za-z_][A-Za-z0-9_]*=.*<[^>]*>' "$ENV_FILE"; then
    die "file secret còn giá trị mẫu dạng <...> — hãy điền giá trị thật cho các dòng:
$(grep -En '^[[:space:]]*[A-Za-z_][A-Za-z0-9_]*=.*<[^>]*>' "$ENV_FILE" | sed -E 's/=.*/=<…>/; s/^/    dòng /')"
  fi

  local pss rp
  pss="$(env_value PROXY_SHARED_SECRET)"
  rp="$(env_value REQUIRE_PROXY)"
  [[ -n "$pss" ]] || die "thiếu PROXY_SHARED_SECRET trong $ENV_FILE (tạo bằng: openssl rand -hex 32)"
  (( ${#pss} >= 32 )) || die "PROXY_SHARED_SECRET quá ngắn (${#pss} ký tự, cần ≥ 32). Tạo bằng: openssl rand -hex 32"
  case "$(printf '%s' "$rp" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|on) ;;
    *) warn "REQUIRE_PROXY chưa bật (=1): ai biết URL modal.run có thể gọi thẳng API, vượt quota theo IP." ;;
  esac
  [[ -n "$(env_value ADMIN_TOKEN)" ]] || warn "ADMIN_TOKEN trống: trang /api/admin/* bị tắt."
  [[ -n "$(env_value POLAR_ACCESS_TOKEN)" ]] || warn "POLAR_ACCESS_TOKEN trống: chưa bán gói Pro (web hiện \"Coming soon\")."
  say "File secret hợp lệ: $real (quyền $mode)"
}

check_modal() {
  command -v modal >/dev/null 2>&1 || die "chưa có lệnh \`modal\`. Cài: pip install modal python-dotenv   rồi đăng nhập: modal token new"
  if [[ "$SKIP_SECRET" == 0 ]]; then
    # `modal secret create --from-dotenv` cần gói python-dotenv (modal KHÔNG tự cài) trong đúng Python chạy lệnh modal.
    local py
    py="$(head -n 1 "$(command -v modal)" 2>/dev/null | sed -n 's/^#![[:space:]]*//p')"
    [[ -n "$py" && "$py" != *" "* && -x "$py" ]] || py="python3"
    "$py" -c 'import dotenv' >/dev/null 2>&1 \
      || die "thiếu gói python-dotenv (cần cho modal secret create --from-dotenv). Cài: $py -m pip install python-dotenv"
  fi
  if [[ "$DRY_RUN" == 1 ]]; then
    say "(dry-run) bỏ qua kiểm tra đăng nhập Modal"
    return 0
  fi
  if ! modal token info >/dev/null 2>&1; then
    die "Modal chưa đăng nhập (hoặc token hết hạn). Chạy: modal token new"
  fi
  say "Modal đã đăng nhập ($(modal profile current 2>/dev/null || echo 'profile mặc định'))"
}

# ── HTTP: gửi kèm X-DatTTS-Proxy-Secret qua stdin (không lộ trong danh sách tiến trình) ──
http_get() {
  local url="$1" secret="$2"
  printf 'X-DatTTS-Proxy-Secret: %s\n' "$secret" | curl -fsS --max-time 120 -H @- "$url"
}

json_eval() {
  # json_eval '<biểu thức python với biến d>'  — đọc JSON từ stdin
  python3 -c 'import json,sys; d=json.load(sys.stdin); print(eval(sys.argv[1]))' "$1" 2>/dev/null
}

smoke() {
  local base="${WEB_URL%/}" secret health n plans i
  secret="$(env_value PROXY_SHARED_SECRET)"
  command -v curl >/dev/null 2>&1 || die "cần curl để kiểm tra sau deploy"
  command -v python3 >/dev/null 2>&1 || die "cần python3 để đọc JSON khi kiểm tra"
  say "Kiểm tra $base/api/health (container lạnh có thể mất ~1 phút)…"
  health=""
  for i in 1 2 3 4 5 6; do
    if health="$(curl -fsS --max-time 60 "$base/api/health" 2>/dev/null)"; then break; fi
    health=""
    sleep $(( i * 5 ))
  done
  [[ -n "$health" ]] || die "/api/health không phản hồi. Xem log: modal app logs dattts"
  [[ "$(printf '%s' "$health" | json_eval 'd.get("ok") is True')" == "True" ]] \
    || die "/api/health trả về không ổn: $health"
  say "  /api/health OK"

  n="$(http_get "$base/api/voices" "$secret" | json_eval 'len(d["voices"])')" \
    || die "/api/voices lỗi. Nếu là 403 direct_access_forbidden: PROXY_SHARED_SECRET trong file khác secret trên Modal (chạy lại không có --skip-secret)."
  [[ "$n" == "$EXPECTED_VOICES" ]] || die "/api/voices trả $n giọng, mong đợi $EXPECTED_VOICES"
  say "  /api/voices OK ($n giọng)"

  plans="$(http_get "$base/api/billing/plans" "$secret" | json_eval '"bật" if d.get("enabled") else "tắt"')" \
    || die "/api/billing/plans lỗi"
  say "  /api/billing/plans OK (bán hàng: $plans)"

  # Truy cập thẳng không có secret phải bị chặn khi REQUIRE_PROXY=1
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 60 "$base/api/voices" || true)"
  if [[ "$code" == 403 ]]; then
    say "  Gọi thẳng URL modal.run bị chặn đúng (403 direct_access_forbidden)"
  else
    warn "gọi thẳng $base/api/voices không qua proxy trả HTTP $code (mong đợi 403). Kiểm tra REQUIRE_PROXY=1."
  fi
}

cd "$REPO"
[[ -f backend/modal_app.py ]] || die "không thấy backend/modal_app.py (script phải nằm trong repo DatTTS)"

if [[ "$SKIP_SECRET" == 0 || "$SKIP_SMOKE" == 0 ]]; then
  check_env_file
fi
check_modal

if [[ "$SKIP_SECRET" == 0 ]]; then
  say "Cập nhật Modal secret '$SECRET_NAME' từ $ENV_FILE (ghi đè toàn bộ)…"
  run modal secret create "$SECRET_NAME" --force --from-dotenv "$ENV_FILE"
fi

if [[ "$FIRST_RUN" == 1 ]]; then
  say "Lần đầu: tải mô hình vào Volume dattts-models (vài phút)…"
  run modal run backend/modal_app.py::download_models
fi

say "Deploy backend/modal_app.py…"
if [[ "$DRY_RUN" == 1 ]]; then
  run modal deploy backend/modal_app.py
  out=""
else
  log="$(mktemp)"
  trap 'rm -f "$log"' EXIT
  printf '+ modal deploy backend/modal_app.py\n'
  modal deploy backend/modal_app.py 2>&1 | tee "$log"
  out="$(cat "$log")"
fi

if [[ -z "$WEB_URL" ]]; then
  WEB_URL="$(printf '%s\n' "$out" | grep -Eo 'https://[A-Za-z0-9.-]*dattts-web[A-Za-z0-9.-]*\.modal\.run' | head -n 1 || true)"
fi

if [[ "$DRY_RUN" == 1 ]]; then
  say "(dry-run) xong — không có gì được thay đổi."
  exit 0
fi

if [[ -z "$WEB_URL" ]]; then
  warn "không dò được URL web trong output. Xem trên modal.com (App dattts → web) rồi chạy lại với --skip-secret --url <URL>."
  exit 1
fi
say "URL web Modal: $WEB_URL"

if [[ "$SKIP_SMOKE" == 0 ]]; then
  smoke
fi

PAGES_FIRST=""
[[ "$FIRST_RUN" == 1 ]] && PAGES_FIRST="--create-project "
cat <<EOF

Xong. Việc tiếp theo (nếu chưa làm):
  - Đặt biến Cloudflare Pages: MODAL_ORIGIN=$WEB_URL và PROXY_SHARED_SECRET (giống trong $ENV_FILE):
      scripts/deploy_pages.sh ${PAGES_FIRST}--set-secrets --modal-origin $WEB_URL --env-file $ENV_FILE
  - Kiểm tra trang thật: curl https://dattts.com/api/health
EOF
