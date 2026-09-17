# CLAUDE.md

## What this is
Job-interview prototype: a **drive-through AI-voice feedback** system (Presto-style domain).
A single QR at the mic → customer talks to an AI agent → agent asks store number + 1–10 rating
→ if rating < 7, asks what went wrong → open-source LLM summarizes → row logged to a Google Sheet
for the dev team.

Two deliverables:
- **`PRODUCT.md`** — production vision (real phone call over Twilio, dashboards, scale). Doc only.
- **This repo** — a *functional* prototype that skips telephony and runs the same LiveKit agent
  **in the browser** so it can be demoed live.

## Flow
```
QR → web page (web/index.html) → in-browser LiveKit agent
  → "say the store number" (validated against data/stores.csv → brand/region/timezone)
  → "rate 1–10"
  → if < 7: "what could we do better?" (spoken reply captured deterministically)
  → Groq LLM summarizes low ratings → row appended to Google Sheet (Apps Script web app)
```
Location is **asked, not detected** — a phone call has no GPS and carriers don't sell caller
location; region/timezone are looked up from the store registry (same code path for 6 or 5,000).

## Stack & key files
- `agent/main.py` — LiveKit voice agent (STT→LLM→TTS) + conversation state machine.
- `agent/stores.py` — registry lookup; handles digits ("101") and words ("one oh one").
- `agent/summarize.py` — feedback text → Groq LLM → `{summary, issue_tag, sentiment}` (safe fallback).
- `agent/sheets.py` — appends a `FeedbackRow` to the Sheet via HTTP POST to an Apps Script web app.
- `web/token.py` — Flask: serves the page + mints LiveKit tokens. `web/index.html` — mic client.
- `scripts/make_qr.py` — QR encoding the web URL. `scripts/apps_script.gs` — the Sheet sink + `formatSheet()`.
- `data/stores.csv` — 6 seed stores, one per brand (Carl's Jr., Hardee's, Fazoli's, Taco John's,
  Wienerschnitzel, Captain D's): `store_id,brand,name,city,state,region,timezone`.

## AI stack
- STT: Groq `whisper-large-v3-turbo` · LLM: Groq `llama-3.3-70b-versatile` · TTS: **ElevenLabs**
  `eleven_turbo_v2_5` (needs `ELEVEN_API_KEY`; voice/model via `ELEVENLABS_VOICE_ID`/`ELEVENLABS_TTS_MODEL`).
  (TTS was Groq Orpheus; swapped to ElevenLabs for lower latency + reliability.)
- Sheet row: `timestamp_utc, local_time, store_id, brand, store_name, region, rating,
  feedback_transcript, summary, issue_tag, sentiment` (feedback/summary/tags blank when rating ≥ 7).

## Setup / run
```bash
source .venv/bin/activate                 # deps already installed (Python 3.14)
python -m agent.stores                     # registry test (no keys)
python -m agent.summarize                  # LLM summary test (needs GROQ_API_KEY)
python -m agent.sheets                     # writes a fake row (needs SHEET_WEBHOOK_URL)
python -m agent.main dev                   # voice agent worker
python -m web.token                        # web page at localhost:8080 (use localhost for mic)
python scripts/make_qr.py                  # QR for PUBLIC_URL (ngrok for on-phone demo)
```
`.env` holds keys (gitignored): LIVEKIT_URL/API_KEY/API_SECRET, GROQ_API_KEY, GROQ_*_MODEL,
GROQ_TTS_VOICE=autumn, SHEET_WEBHOOK_URL. Rating tone tiers (tone only — feedback is always
collected): PRAISE_THRESHOLD=8 (>=8 warm praise), LOW_THRESHOLD=4 (<4 apologize), middle = neutral.
A spoken range like "6 or 7" is parsed as the LOWER number. (FEEDBACK_THRESHOLD is retired.)
Ratings support half-steps: "4.5" / "four point five" / "four and a half" all store 4.5 (whole
ratings stay ints, e.g. 5 not 5.0). Each rating tier asks EXACTLY ONE follow-up question then
ends — no "anything else?" beat. LOW asks "how could we make your experience better?", MIDDLING
"what could we have done better?", GREAT "is there anything we could do even better?".

## Hard-won gotchas (don't regress)
- **Google Sheets uses an Apps Script web app, NOT a service account** — the account has
  `iam.disableServiceAccountKeyCreation` org policy that blocks SA keys. Deploy `apps_script.gs`
  as a web app (Execute as: Me, Access: Anyone) and paste the `/exec` URL into `SHEET_WEBHOOK_URL`.
- **Groq `playai-tts` is decommissioned** → use `canopylabs/orpheus-v1-english`; it needs one-time
  terms acceptance in the console; valid voices: `autumn diana hannah austin daniel troy` (not "tara").
- **⚠️ Groq free-tier TTS rate limits are THE #1 flakiness trap** — Orpheus caps ~100 requests/day
  (resets daily) AND a tight per-minute burst limit. During a live call, TTS returns
  `429 Too Many Requests`; LiveKit retries 3× then raises → **no audio = "agent went silent" or
  individual spoken lines dropped** (greeting, store confirmation, goodbye, "anything else?") while
  STT/LLM/tools all still work and the Sheet row still logs. This masquerades as conversation-logic
  bugs — check the worker log for `429`/`Too Many Requests` FIRST. A full call ≈ 5–6 TTS requests,
  so ~15 calls/day and rapid/stacked calls trip the per-minute limit (even greeting→confirmation a
  few seconds apart). Single manual requests pass, so isolated probes mislead. Real fix = upgrade
  the Groq tier; for demos, space out calls and keep the recorded fallback ready. Read live budget:
  the TTS response headers `x-ratelimit-remaining-requests` / `-remaining-tokens`.
- **`python -m agent.main dev` does NOT hot-reload** (livekit-agents 1.6.6 removed in-process reload;
  it prints "use `lk agent dev`"). Code edits take effect only after you kill + restart the worker —
  edits look broken/ineffective if the running worker predates them. Confirm the process start time
  is after your last edit.
- **Python 3.14 + LiveKit function tools**: don't annotate tool params with types imported inside a
  function (e.g. `RunContext`) — deferred annotation eval can't resolve them → NameError → LLM fails.
- **Feedback capture is deterministic, not via a tool**: `record_rating` sets `_collecting`;
  `on_user_turn_completed` appends *every* subsequent turn to `_feedback_parts` (so multi-sentence
  complaints are captured in full). We log once at `wrap_up`, not on the first turn. (An earlier
  `record_feedback` tool made the model stuff its own question in as the feedback; an even earlier
  version logged only the first turn and dropped the rest.)
- **Greeting is deterministic**, spoken via `session.say(..., allow_interruptions=False)` right after
  `session.start()` — NOT via `generate_reply`. This guarantees the exact opening line and stops early
  mic noise / a slow-cold first LLM turn from swallowing it. It's added to chat ctx so the LLM knows
  it already greeted and goes straight to `lookup_store` on the caller's answer.
- **Call ends via `end_call` tool, three ways log the row**: after the customer answers the final
  question and has nothing more, the LLM calls the `end_call` tool → `wrap_up` speaks the closing
  line (always ends "have a good day"), logs, and closes. The LLM is told NOT to say goodbye — the
  system speaks it (guarantees the exact sign-off). Two fallbacks both hit the idempotent
  `_finalize`: (a) silence auto-end → `wrap_up`; (b) **`ctx.add_shutdown_callback(_finalize)`
  logs on participant disconnect** — critical, because a caller who hangs up before the goodbye
  used to save nothing. `_finalize` no-ops if `rating is None` (don't log ratingless hangups).
- **Silence auto-end is gated on the AGENT being idle**, not just the caller going quiet. Track
  `agent_state_changed` (busy = thinking/speaking, idle = listening) + `user_state_changed` (away);
  only wrap up when both line up (`_arm_end_if_idle`), after a short grace (`END_GRACE_SECONDS`, ~2s,
  cancelled if they speak). The old version fired `wrap_up` the instant the caller went "away" — which
  on a longer/pausier answer fired mid-turn, skipped the "anything else?" beat, and let the goodbye
  collide with the in-flight reply so nothing was heard. `wrap_up` also calls `session.interrupt()`
  before speaking the goodbye and says it `allow_interruptions=False` so the sign-off always plays.
- **Don't await `session.aclose()` from inside the session's own task** (e.g. the `end_call` tool) —
  schedule it on a separate task (`asyncio.create_task(self._close_session())`) or it can deadlock.
- **Speak the goodbye BEFORE logging** in `wrap_up` (say + `wait_for_playout` first, then summarize +
  Sheet POST) so the caller isn't left in silence during the ~2s LLM/HTTP work.
- LiveKit worker has empty `agent_name` → auto-dispatches into every new room (why the browser
  connection gets an agent). Mic needs `localhost` or HTTPS (LAN IP over http won't grant mic).
  **Run exactly ONE worker**: two workers both auto-join every call → overlapping agent audio (the
  web client's `track.attach` swaps tracks mid-utterance) AND double the TTS request rate (→ 429s).
  `pkill -f "agent.main dev"` before starting a fresh one.
- **Don't declare `record_rating(rating: int)`** — llama-3.3 intermittently emits the tool arg as a
  string ("3"), Groq rejects it server-side ("expected integer, but got string"), LiveKit retries 4×
  and the turn dies silently (call stalls after the rating with no follow-up). Declare `rating: str`
  and coerce via `_parse_rating` (handles int/"3"/"a three"/"ten"). Same trap applies to any numeric
  tool param the model fills from speech.

## Status
- ✅ Backend pipeline verified end-to-end (store lookup → summarize → sheet) with real Groq calls.
- ✅ Live browser voice conversation working (greet → store → confirm → rate).
- ✅ Both branches now invite a spoken reply: low ratings get "what went wrong?" + empathetic
  "anything else?"; good ratings get "anything we could do even better?". All turns captured,
  logged once, ends via `end_call` or an idle-gated silence timeout with a spoken goodbye.
- ✅ Greeting is deterministic (spoken via `session.say`); silence auto-end waits for the agent to be
  idle so it no longer cuts off the closing question/goodbye (see gotchas).
- ⚠️ Verified working end-to-end in a live call (greeting → "303" → correctly looked up Fazoli's,
  Lexington KY → confirmation), BUT the free Groq tier's TTS 429s under repeated/rapid testing and
  drops the audio. The pipeline is sound; the bottleneck is the TTS rate limit. Upgrade the tier or
  pace calls before demoing (see the ⚠️ rate-limit gotcha).
- ✅ Web UI is a phone-call mock: a drive-thru **sign** shows a random real store (from `/stores`)
  + a QR (`/qr.png`); tapping it opens an iOS-style **call screen** that auto-starts the agent.
  No "Start" button. `web/index.html` has two views (`#poster`, `#call`); `web/token.py` gained
  `/stores` and `/qr.png`. NOTE: the shown store number is what the caller reads aloud when asked —
  the verbal store-lookup step is intact (not auto-passed).
- Optional next: richer summary prompt, tiny read-only dashboard over the Sheet, Twilio phone entry
  (see `PRODUCT.md` roadmap).

## Plan (source of truth: /Users/scottnguyen/.claude/plans/mighty-napping-robin.md)
Prototype (done): scaffold → PRODUCT.md → registry → sheets sink → summarizer → LiveKit agent →
web client → QR. Demo strategy: primary = scan/opens link, talk, row lands in Sheet; fallback =
`sample/demo_transcript.txt` + pre-seeded Sheet + a recording. Out of scope (roadmap in PRODUCT.md):
Twilio phone call, multi-region DB/dashboards, auth, multi-language, barge-in/noise tuning, consent/retention.
