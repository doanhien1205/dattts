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
node scripts/e2e.mjs                                      # Playwright, against server_local.py on :8765
PYTHON=$PWD/.venv/bin/python node scripts/e2e_billing.mjs # Playwright billing flows with fake Polar (starts its own servers)
```

## Deploy

The backend runs on [Modal](https://modal.com) (`backend/modal_app.py`: a CPU `TTSWorker`, an ASGI web endpoint, a files Volume and a KV Dict). Create the `dattts` secret, download the model once, then deploy:

```bash
modal secret create dattts --force --from-dotenv ~/dattts-secret.env   # keys as in backend/.env.example
modal run backend/modal_app.py::download_models
modal deploy backend/modal_app.py
```

See [README.md](README.md) for the full checklist (Polar, custom domain, legal pages).

## License & credits

Voices by **VieNeu-TTS by Phạm Nguyễn Ngọc Bảo** (Apache-2.0); keep this attribution on the site and in product docs. Audio carries VieNeu's inaudible watermark. Do not use voices to impersonate real people.
