#!/usr/bin/env bash
# Triển khai trang web/ + proxy functions/ lên Cloudflare Pages (SPEC Phần 4). Chạy: scripts/deploy_pages.sh --help
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
# Phiên bản wrangler cố định (không dùng "latest" để lần deploy nào cũng giống nhau).
WRANGLER_VERSION="4.151.3"
PROJECT="dattts"
BRANCH="main"
SITE_URL=""   # rỗng = tự chọn: https://<project>.pages.dev khi project vừa tạo, ngược lại https://dattts.com
ENV_FILE="${HOME}/dattts-secret.env"
MODAL_ORIGIN=""
SET_SECRETS=0
CREATE_PROJECT=0
SKIP_SMOKE=0
DRY_RUN=0

usage() {
  cat <<EOF
Cách dùng: scripts/deploy_pages.sh [tuỳ chọn]

Đưa thư mục web/ (trang tĩnh) và functions/ (proxy /api/* → Modal) lên Cloudflare Pages bằng
npx wrangler@${WRANGLER_VERSION} pages deploy web --project-name ${PROJECT}
Lệnh chạy từ GỐC repo để wrangler nhận thư mục ./functions.

Trước khi chạy: đăng nhập Cloudflare (npx wrangler@${WRANGLER_VERSION} login) hoặc đặt CLOUDFLARE_API_TOKEN.

Tuỳ chọn:
  --set-secrets          đặt biến bí mật của Pages: MODAL_ORIGIN và PROXY_SHARED_SECRET
                         (wrangler pages secret put). Cần --modal-origin; PROXY_SHARED_SECRET đọc từ --env-file.
  --modal-origin URL     URL web Modal, dạng https://<workspace>--dattts-web.modal.run
  --env-file PATH        file dotenv có PROXY_SHARED_SECRET (mặc định: ~/dattts-secret.env, quyền 600, ngoài repo)
  --create-project       tạo project Pages "${PROJECT}" nếu chưa có (lần đầu; production branch = --branch).
                         Không có cờ này mà project chưa tồn tại thì script cũng tự tạo.
  --project-name NAME    tên project Pages (mặc định: ${PROJECT})
  --branch NAME          nhánh deploy (mặc định: ${BRANCH} = production)
  --site-url URL         địa chỉ để kiểm tra sau deploy (mặc định: https://<project>.pages.dev nếu
                         project vừa được tạo — lúc đó dattts.com chưa gắn; còn lại https://dattts.com)
  --skip-smoke           không kiểm tra sau deploy
  --dry-run              chỉ in các lệnh sẽ chạy
  -h, --help             in hướng dẫn này

Ví dụ lần đầu:
  scripts/deploy_pages.sh --create-project --set-secrets \\
      --modal-origin https://<workspace>--dattts-web.modal.run --env-file ~/dattts-secret.env
Các lần sau (chỉ cập nhật web/ và functions/):
  scripts/deploy_pages.sh
EOF
}

say()  { printf '==> %s\n' "$*"; }
warn() { printf 'CẢNH BÁO: %s\n' "$*" >&2; }
die()  { printf 'LỖI: %s\n' "$*" >&2; exit 1; }

run() {
  printf '+'; printf ' %q' "$@"; printf '\n'
  if [[ "$DRY_RUN" == 1 ]]; then return 0; fi
  "$@"
}

wrangler() { run npx --yes "wrangler@${WRANGLER_VERSION}" "$@"; }

need_arg() { [[ $# -ge 2 && -n "$2" ]] || die "$1 cần một giá trị"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --set-secrets) SET_SECRETS=1; shift ;;
    --create-project) CREATE_PROJECT=1; shift ;;
    --modal-origin) need_arg "$@"; MODAL_ORIGIN="$2"; shift 2 ;;
    --modal-origin=*) MODAL_ORIGIN="${1#*=}"; shift ;;
    --env-file) need_arg "$@"; ENV_FILE="$2"; shift 2 ;;
    --env-file=*) ENV_FILE="${1#*=}"; shift ;;
    --project-name) need_arg "$@"; PROJECT="$2"; shift 2 ;;
    --project-name=*) PROJECT="${1#*=}"; shift ;;
    --branch) need_arg "$@"; BRANCH="$2"; shift 2 ;;
    --branch=*) BRANCH="${1#*=}"; shift ;;
    --site-url) need_arg "$@"; SITE_URL="$2"; shift 2 ;;
    --site-url=*) SITE_URL="${1#*=}"; shift ;;
    --skip-smoke) SKIP_SMOKE=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "tuỳ chọn không hợp lệ: $1" ;;
  esac
done

env_value() {
  local key="$1" line val
  line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${key}=" "$ENV_FILE" | tail -n 1 || true)"
  [[ -n "$line" ]] || return 0
  val="${line#*=}"
  val="${val%$'\r'}"
  if [[ "$val" =~ ^\"(.*)\"$ || "$val" =~ ^\'(.*)\'$ ]]; then val="${BASH_REMATCH[1]}"; fi
  printf '%s' "$val"
}

file_mode() { stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1"; }

check_env_file() {
  [[ -f "$ENV_FILE" && -r "$ENV_FILE" ]] || die "không đọc được file secret: $ENV_FILE (mẫu: deploy/secret.env.example)"
  local real mode
  if command -v python3 >/dev/null 2>&1; then
    real="$(python3 -c 'import os,sys;print(os.path.realpath(sys.argv[1]))' "$ENV_FILE")"
  else
    real="$(cd "$(dirname "$ENV_FILE")" && pwd -P)/$(basename "$ENV_FILE")"
  fi
  case "$real" in
    "$REPO"/*) die "file secret nằm TRONG repo ($real). Hãy chuyển ra ngoài, ví dụ ~/dattts-secret.env" ;;
  esac
  mode="$(file_mode "$real")" || die "không đọc được quyền của $real"
  [[ "$mode" =~ ^[0-7]+$ ]] || die "quyền file không hợp lệ: $mode"
  (( (8#$mode & 8#7177) == 0 )) || die "quyền của $real là $mode, rộng hơn 600. Sửa: chmod 600 '$real'"
}

cd "$REPO"
[[ -d web ]] || die "không thấy thư mục web/"
[[ -f "functions/api/[[path]].js" ]] || die "không thấy functions/api/[[path]].js (proxy /api/* → Modal)"
command -v npx >/dev/null 2>&1 || die "chưa có npx. Cài Node.js 18+ (https://nodejs.org) rồi chạy lại."

if [[ -n "${CLOUDFLARE_API_TOKEN:-}" ]]; then
  say "Dùng CLOUDFLARE_API_TOKEN từ môi trường"
elif [[ "$DRY_RUN" == 1 ]]; then
  say "(dry-run) bỏ qua kiểm tra đăng nhập Cloudflare"
else
  # `wrangler whoami` luôn thoát 0 → đọc nội dung
  who="$(npx --yes "wrangler@${WRANGLER_VERSION}" whoami 2>&1 || true)"
  if grep -qi "not authenticated" <<<"$who"; then
    die "chưa đăng nhập Cloudflare. Chạy: npx wrangler@${WRANGLER_VERSION} login   (hoặc đặt CLOUDFLARE_API_TOKEN)"
  fi
  say "Đã đăng nhập Cloudflare"
fi

# 0 = project đã có, 1 = chưa có, 2 = không xác định được (lỗi mạng/quyền…)
project_exists() {
  local out
  out="$(npx --yes "wrangler@${WRANGLER_VERSION}" pages project list --json 2>/dev/null)" || return 2
  command -v python3 >/dev/null 2>&1 || return 2
  printf '%s' "$out" | python3 -c '
import json, sys
raw = sys.stdin.read(); i = raw.find("[")
try:
    names = {p.get("Project Name") for p in json.loads(raw[i:])} if i >= 0 else None
except ValueError:
    names = None
sys.exit(2 if names is None else (0 if sys.argv[1] in names else 1))' "$PROJECT"
}

NEW_PROJECT=0
if [[ "$DRY_RUN" == 1 ]]; then
  if [[ "$CREATE_PROJECT" == 1 ]]; then
    wrangler pages project create "$PROJECT" --production-branch "$BRANCH"
    NEW_PROJECT=1
  fi
else
  rc=0; project_exists || rc=$?
  if [[ "$rc" == 0 ]]; then
    [[ "$CREATE_PROJECT" == 1 ]] && say "Project Pages '$PROJECT' đã có — bỏ qua bước tạo."
  elif [[ "$rc" == 1 || "$CREATE_PROJECT" == 1 ]]; then
    say "Tạo project Pages '$PROJECT' (production branch: $BRANCH)…"
    wrangler pages project create "$PROJECT" --production-branch "$BRANCH"
    NEW_PROJECT=1
  else
    warn "không kiểm tra được project '$PROJECT' có tồn tại không; nếu bước sau báo project not found, chạy lại với --create-project."
  fi
fi
if [[ -z "$SITE_URL" ]]; then
  if [[ "$NEW_PROJECT" == 1 ]]; then SITE_URL="https://${PROJECT}.pages.dev"; else SITE_URL="https://dattts.com"; fi
fi

if [[ "$SET_SECRETS" == 1 ]]; then
  [[ -n "$MODAL_ORIGIN" ]] || die "--set-secrets cần --modal-origin https://<workspace>--dattts-web.modal.run"
  [[ "$MODAL_ORIGIN" =~ ^https://[^/[:space:]]+/?$ ]] \
    || die "MODAL_ORIGIN phải dạng https://<workspace>--dattts-web.modal.run (không có đường dẫn): $MODAL_ORIGIN"
  check_env_file
  secret="$(env_value PROXY_SHARED_SECRET)"
  [[ -n "$secret" ]] || die "thiếu PROXY_SHARED_SECRET trong $ENV_FILE"
  (( ${#secret} >= 32 )) || die "PROXY_SHARED_SECRET quá ngắn (cần ≥ 32 ký tự)"
  [[ "$secret" != *"<"* ]] || die "PROXY_SHARED_SECRET vẫn là giá trị mẫu <...>"
  say "Đặt biến bí mật Pages MODAL_ORIGIN và PROXY_SHARED_SECRET (giá trị truyền qua stdin, không in ra)…"
  if [[ "$DRY_RUN" == 1 ]]; then
    wrangler pages secret put MODAL_ORIGIN --project-name "$PROJECT"
    wrangler pages secret put PROXY_SHARED_SECRET --project-name "$PROJECT"
  else
    printf '%s' "${MODAL_ORIGIN%/}" | wrangler pages secret put MODAL_ORIGIN --project-name "$PROJECT"
    printf '%s' "$secret" | wrangler pages secret put PROXY_SHARED_SECRET --project-name "$PROJECT"
  fi
  unset secret
else
  say "Nhắc: Pages cần 2 biến bí mật MODAL_ORIGIN và PROXY_SHARED_SECRET (giống Modal secret)."
  say "      Chưa đặt thì chạy lại với: --set-secrets --modal-origin <URL modal.run> [--env-file PATH]"
  say "      hoặc: Cloudflare → Workers & Pages → $PROJECT → Settings → Variables and Secrets (chọn Encrypt)."
fi

say "Deploy web/ + functions/ lên Pages project '$PROJECT' (nhánh $BRANCH)…"
wrangler pages deploy web --project-name "$PROJECT" --branch "$BRANCH" --commit-dirty=true

if [[ "$DRY_RUN" == 1 ]]; then
  say "(dry-run) xong — không có gì được thay đổi."
  exit 0
fi

if [[ "$SKIP_SMOKE" == 0 ]]; then
  base="${SITE_URL%/}"
  say "Kiểm tra $base/api/health qua proxy (container Modal lạnh có thể mất ~1 phút)…"
  ok=0
  for i in 1 2 3 4 5 6; do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 60 "$base/api/health" || true)"
    if [[ "$code" == 200 ]]; then ok=1; break; fi
    sleep $(( i * 5 ))
  done
  if [[ "$ok" == 1 ]]; then
    say "  $base/api/health OK"
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 60 "$base/api/voices" || true)"
    case "$code" in
      200) say "  $base/api/voices OK (proxy gửi đúng PROXY_SHARED_SECRET)" ;;
      403) warn "/api/voices trả 403 direct_access_forbidden: PROXY_SHARED_SECRET ở Pages KHÁC Modal secret." ;;
      502) warn "/api/voices trả 502 upstream_unavailable: kiểm tra MODAL_ORIGIN của Pages." ;;
      *) warn "/api/voices trả HTTP $code" ;;
    esac
  else
    warn "$base/api/health trả HTTP ${code:-?}. Nếu tên miền mới gắn, DNS/chứng chỉ có thể cần vài phút."
    warn "Chưa gắn dattts.com? Kiểm tra bằng địa chỉ Pages: --site-url https://${PROJECT}.pages.dev"
    warn "502 = Pages không gọi được Modal (MODAL_ORIGIN sai/chưa đặt, hoặc Modal chưa deploy)."
  fi
fi
