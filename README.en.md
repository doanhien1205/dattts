# DatTTS — the Vietnamese voice studio

[dattts.com](https://dattts.com) turns Vietnamese text into natural speech. Paste a script, pick one of 25 Northern, Central or Southern voices, and download MP3/WAV/OGG with SRT/VTT subtitles — from the web studio or a simple REST API.

Built for anyone who needs **Vietnamese** voice-over: video localization, e-learning and language apps, IVR/apps/games, audiobooks and podcasts for the Vietnamese diaspora, and developers.

The full owner's guide (deployment, Polar setup, domain, TODOs) is in Vietnamese: [README.md](README.md).

## Features

- **Engine:** [VieNeu-TTS v3 Turbo](https://huggingface.co/pnnbao-ump/VieNeu-TTS-v3-Turbo) (Apache-2.0), ONNX int8 on CPU (GPU optional), 48 kHz audio. Numbers, dates and currency are read the Vietnamese way.
- Multi-voice scripts (`[Trúc Ly] Chào anh. [Thiện Minh] Chào em.`), pronunciation dictionary, sentence-level karaoke player, long texts (up to 100,000 characters) as background jobs.
- Bilingual UI: English by default with an **EN | VI** switch (`?lang=vi`, saved choice, or a `vi*` browser language). API error messages follow `Accept-Language` (`vi*` → Vietnamese, otherwise English).
- Billing through **Polar** (Merchant of Record: cards, Apple Pay, Google Pay, tax and invoices). A customer's API key *is* the Polar License Key (prefix `DATTTS`); the customer copies it from Polar's customer portal (linked from the "Access purchase" button in Polar's receipt email). DatTTS uses 25 preset voices only — no voice cloning.

## Plans

| Plan | Price | Allowance |
|---|---|---|
| Free | $0 | 20,000 characters / day / IP |
| Pro Weekly (`pro_week`) | $1.99 / week | 250,000 characters / week (ISO week, Vietnam time) |
| Pro Monthly (`pro_month`) — most popular | $4.99 / month | 1,000,000 characters / month |
| Pro Yearly (`pro_year`) | $49.99 / year | 1,000,000 characters / month |

Prices and "Save X%" badges are read live from Polar. Product/benefit IDs and every setting are listed in [`backend/.env.example`](backend/.env.example).

## Quickstart (local)

Requires Python 3.12+ and `ffmpeg` on `PATH`.

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements.txt
cd backend
export DATTTS_MODEL_DIR=$PWD/../models     # the model downloads from Hugging Face on first run
python server_local.py                     # http://127.0.0.1:8000  (add --fake for an instant fake engine)
```

Try the API:

```bash
curl -s -X POST http://127.0.0.1:8000/api/tts -H 'Content-Type: application/json' \
  -d '{"text":"Xin chào, mình là DatTTS.","voice":"truc-ly","format":"mp3"}'
# → {"id":"…","audio_url":"/api/files/<id>.mp3","srt_url":…,"segments":[…],"quota":{…}}
curl -s http://127.0.0.1:8000/api/me -H 'X-API-Key: DATTTS-…'          # plan, used / limit, period
```

Try checkout without a Polar account, using the bundled fake Polar:

```bash
python scripts/fake_polar.py --port 8899 &
eval "$(python scripts/fake_polar.py --print-env)"
cd backend && PUBLIC_BASE_URL=http://127.0.0.1:8765 python server_local.py --fake --port 8765
# open http://127.0.0.1:8765/#pricing → Get Pro; test keys: DATTTS-WEEK-TEST, DATTTS-MONTH-TEST, DATTTS-YEAR-TEST
```

## Tests

```bash
cd backend && pytest -q                                   # unit + integration (real-engine tests need DATTTS_MODEL_DIR)
node scripts/test_proxy.mjs                               # Cloudflare Pages proxy function against a local echo server
node scripts/e2e.mjs                                      # Playwright, against server_local.py on :8765
PYTHON=$PWD/.venv/bin/python node scripts/e2e_billing.mjs # Playwright billing flows with fake Polar (starts its own servers)
```

## Deploy

Production runs on **Modal Starter** (no custom domains on that plan) behind **Cloudflare Pages**:

```
Browser / Polar webhook ──► https://dattts.com (Cloudflare Pages)
   ├─ static files: web/
   └─ /api/*  ──► Pages Function functions/api/[[path]].js ──► https://<workspace>--dattts-web.modal.run/api/*
```

The Pages Function streams method, path, query, raw body (Polar webhook signatures stay valid), Range/206 and all
response headers through unchanged. It strips hop-by-hop headers and any client-sent `X-DatTTS-*`, then adds
`X-DatTTS-Proxy-Secret` and `X-DatTTS-Client-IP` (from `CF-Connecting-IP`). The backend trusts that IP only when the
secret matches (`PROXY_SHARED_SECRET`, constant-time compare); with `REQUIRE_PROXY=1` every direct `/api/*` call to
the modal.run URL gets `403 direct_access_forbidden` (except `/api/health`). If Modal is unreachable the proxy
returns `502 upstream_unavailable` (EN/VI by `Accept-Language`).

```bash
cp deploy/secret.env.example ~/dattts-secret.env && chmod 600 ~/dattts-secret.env   # fill in <...>; keep it outside the repo
pip install modal python-dotenv && modal token new   # python-dotenv is needed by `modal secret create --from-dotenv`
scripts/deploy_modal.sh --first-run          # secret → download model → modal deploy → smoke tests; prints the modal.run URL
npx wrangler@4.151.3 login
scripts/deploy_pages.sh --create-project --set-secrets \
    --modal-origin https://<workspace>--dattts-web.modal.run --env-file ~/dattts-secret.env
```

On the first run the Pages smoke test targets `https://dattts.pages.dev` (the custom domain is attached afterwards).
Modal cuts each web request at 150 s and answers `303` to the same URL; the Pages Function follows those 303s itself
(up to 8), so long `/api/tts` calls still work through the proxy. Every `/api/*` request, including job-progress polls
(1 s for the first 10, then backing off to 10 s), counts toward the Workers Free quota of 100,000 requests/day.

Then add `dattts.com` and `www.dattts.com` under Pages → Custom domains (easiest with the domain's nameservers on
Cloudflare). Pages needs two encrypted variables, `MODAL_ORIGIN` and `PROXY_SHARED_SECRET`; the latter must equal the
one in the Modal `dattts` secret. The Polar webhook URL stays `https://dattts.com/api/billing/webhook`.
Cost: Modal Starter is $0 + usage with $30/month credit; the 4 CPU + 6 GiB worker costs ≈ $0.24/hour while running
and scales to zero after 5 idle minutes; Pages is free. Alternative: on Modal Team, use
`DATTTS_DOMAINS=dattts.com,www.dattts.com scripts/deploy_modal.sh` and skip Pages (set `REQUIRE_PROXY=0`).

See [README.md](README.md) (Vietnamese) for the full step-by-step guide, troubleshooting, Polar and legal pages.

## License & credits

Voices by **VieNeu-TTS by Phạm Nguyễn Ngọc Bảo** (Apache-2.0); keep this attribution on the site and in product docs. Audio carries VieNeu's inaudible watermark. Do not use voices to impersonate real people.
