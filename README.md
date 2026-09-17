# Drive-Thru Voice Feedback

A prototype for collecting customer feedback about an **automated (AI voice) drive-through**.
A single QR sticker at the mic lets a customer talk to an AI agent: it asks the store number
and a 1–10 rating, and — only if the rating is low — what could have been better. Low-rated
feedback is summarized by an open-source LLM and every call is logged to a Google Sheet the
dev team reviews.

> Two deliverables: **[`PRODUCT.md`](./PRODUCT.md)** describes the real product (a phone call
> over Twilio). This repo is the **functional prototype**, which skips telephony and runs the
> exact same agent **in the browser** so it can be demoed live.

## Flow

```
Scan QR → web page → in-browser LiveKit voice agent
  → "say the store number"  (validated against data/stores.csv → brand/region/timezone)
  → "rate 1–10"
  → if < 7: "what could we do better?"  (feedback captured)
  → Groq LLM summarizes low ratings → row appended to Google Sheet
```

**Why the agent *asks* for the store number:** a phone call carries no GPS and US carriers
don't sell real-time caller location, so location can't be auto-detected. The agent asks, and
brand / city / region / timezone are looked up from a store registry — the same code path for
6 stores or 5,000. See `PRODUCT.md`.

## Stack

| Piece            | Choice                                              |
|------------------|-----------------------------------------------------|
| Voice agent      | [LiveKit Agents](https://docs.livekit.io/agents/) (browser, WebRTC) |
| STT / LLM / TTS  | **Groq** — Whisper + Llama + PlayAI TTS (one API key) |
| Summarization    | Groq Llama (`agent/summarize.py`)                   |
| Store registry   | `data/stores.csv` (`agent/stores.py`)               |
| Storage          | Google Sheets (`agent/sheets.py`)                   |
| QR               | `scripts/make_qr.py`                                |

## Setup

1. **Install deps** (Python 3.11+):
   ```bash
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```
2. **Accounts / keys** — copy `.env.example` to `.env` and fill in:
   - **LiveKit Cloud** (free): create a project → copy `LIVEKIT_URL`, `LIVEKIT_API_KEY`,
     `LIVEKIT_API_SECRET`.
   - **Groq** (free): create an API key → `GROQ_API_KEY`.
   - **Google Sheets** (no Google Cloud project or key needed): create a Sheet, open
     **Extensions → Apps Script**, paste `scripts/apps_script.gs`, then **Deploy → New
     deployment → Web app** (Execute as: Me, Who has access: Anyone). Copy the web app URL
     into `SHEET_WEBHOOK_URL`. (This avoids the `iam.disableServiceAccountKeyCreation` org
     policy that blocks service-account keys on many Google accounts.)

## Run & verify (build order — each step is independently testable)

```bash
# 1. Registry lookup (no keys needed)
python -m agent.stores

# 2. Sheets sink — appends one fake row (needs Google creds + FEEDBACK_SHEET_ID)
python -m agent.sheets

# 3. Summarizer — structured JSON from sample feedback (needs GROQ_API_KEY)
python -m agent.summarize

# 4. Start the voice agent worker (needs LiveKit + Groq)
python -m agent.main dev

# 5. Serve the web page (new terminal)
python -m web.token          # http://localhost:8080

# 6. Generate the QR (points at PUBLIC_URL)
python scripts/make_qr.py
```

Open **http://localhost:8080**, tap **Start feedback**, and talk. When the call ends, a row
appears in the Sheet.

**On-phone demo:** localhost isn't reachable from a phone. Expose the web server with a tunnel
(`ngrok http 8080`), set `PUBLIC_URL` to the `https://…` URL, regenerate the QR, and scan it.

**Fallback (if live demo flakes):** see `sample/demo_transcript.txt` + run `python -m agent.sheets`
to show a row landing, backed by a screen recording.

## Sheet columns

`timestamp_utc · local_time · store_id · brand · store_name · region · rating ·
feedback_transcript · summary · issue_tag · sentiment`

(For ratings ≥ 7 the feedback/summary/issue_tag/sentiment cells are intentionally blank.)

## Not in the prototype (see `PRODUCT.md` roadmap)

Twilio phone call, multi-region dashboards / database, auth, multi-language, barge-in & noise
tuning, recording-consent UX, and scale/cost hardening.
