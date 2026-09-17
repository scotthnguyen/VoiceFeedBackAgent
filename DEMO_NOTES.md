# Demo Notes — What You're Watching vs. What I'd Ship

*Presentation companion. The live demo is a deliberately simplified slice; this is the
real design it stands in for. Full detail lives in [`PRODUCT.md`](PRODUCT.md).*

---

## The one-liner

**What you're seeing:** you scan a QR, a web page opens, and you talk to the AI feedback
agent *in your browser*. Store lookup → rating → one follow-up question → the feedback is
summarized by an open-weight LLM and a row is appended to a Google Sheet.

**What I'd actually build:** the *exact same conversation and the exact same backend*, but the
customer's phone dials a real number over **Twilio** instead of opening a browser tab.

The only thing that changes between this demo and production is the **entry channel**
(browser WebRTC → real phone call). The AI pipeline and the data sink you're watching are
already the ones I'd ship.

---

## Why the demo is browser-based (on purpose)

A real phone call needs a Twilio phone number and a SIP trunk into LiveKit — telephony
plumbing that costs money per minute and takes hours to provision, and makes the *idea* no
clearer in a 5-minute demo. So the demo runs the identical LiveKit agent over an in-browser
WebRTC session. You still speak, it still listens, it still logs. The browser is just a
stand-in microphone for a phone.

This is intentional, not a shortcut: it lets anyone try it instantly from a QR with zero
setup, and it isolates the thing worth evaluating (does the agent hold a good feedback
conversation and produce clean, structured signal?) from the thing that's just wiring (SIP).

---

## The voice pipeline, stage by stage

A voice agent isn't one model — it's a real-time pipeline, and every stage has to be fast
enough that the caller never feels the lag. Here's the exact path each turn takes:

```
Caller audio  ──►  VAD  ──►  ASR (STT)  ──►  turn detection  ──►  LLM  ──►  TTS  ──►  Agent audio
                    │           │                │                 │          │
              Silero VAD   Groq Whisper     LiveKit          Kimi K2 on   ElevenLabs
              detects      large-v3-turbo   turn-detector    Groq —       eleven_turbo_v2_5
              speech vs.   transcribes      decides the      responds +   synthesizes
              silence      speech → text    caller is done   calls tools  the reply
                                            speaking

        ◄────────────── barge-in: caller can interrupt; agent stops and listens ──────────────►
        (acoustic echo cancellation keeps the agent from hearing its own voice)
```

- **VAD (Silero)** — voice-activity detection. Knows when someone is actually speaking vs.
  background noise, so we're not transcribing engine hum.
- **ASR / STT (Groq `whisper-large-v3-turbo`)** — open-weight Whisper turns speech into text,
  fast and cheap on Groq.
- **Turn detection (LiveKit `turn-detector`)** — decides when the caller has *finished* their
  thought, not just paused. This is what stops the agent from talking over someone who's
  mid-sentence.
- **LLM (Kimi K2 on Groq)** — drives the dialogue and calls function tools (`lookup_store`,
  `record_rating`, `end_call`) so the logged record is always well-formed.
- **TTS (ElevenLabs `eleven_turbo_v2_5`)** — low-latency, natural-sounding voice back to the
  caller.
- **Barge-in + AEC** — the caller can interrupt at any time and the agent yields; echo
  cancellation stops the agent from transcribing its own voice.

---

## The AI stack (specific models)

| Stage | Technology | Notes |
|-------|-----------|-------|
| **VAD** | Silero VAD | Speech-vs-silence gating. |
| **ASR / STT** | Groq `whisper-large-v3-turbo` | Open-weight Whisper, hosted on Groq — fast + cheap. |
| **Turn detection** | LiveKit `turn-detector` | End-of-turn prediction so we don't cut people off. |
| **LLM** | **Kimi K2** (`moonshotai/kimi-k2-instruct`, on Groq) | **Open-weight**, strong at tool-calling; drives dialogue + tools. |
| **TTS** | ElevenLabs `eleven_turbo_v2_5` | Low-latency voice. |
| **Summarize + tag** | **Kimi K2** on Groq | Same open-weight model → `{summary, issue_tag, sentiment}`. |
| **Store registry** | `data/stores.csv` (→ DB table at scale) | Spoken number → brand / region / timezone. Same code path for 6 or 5,000 stores. |
| **Data sink** | **Google Sheets API** via an Apps Script web app | HTTP POST appends one row. Exactly what the demo uses and what I'd keep. |

> **Why Kimi K2 (open-weight)?** Cost scales with call minutes. Open-weight models served on
> Groq are dramatically cheaper and faster than closed APIs, and I'm not locked to one vendor
> — Kimi K2 in particular is very strong at the tool-calling this agent depends on. *(Note:
> Kimi K2 isn't enabled on the current free-tier Groq key, so the live demo runs an available
> open-weight model — Llama 3.3 70B — until the key/tier is upgraded. The code is one env var
> away from Kimi.)*

> **Why Google Sheets and not a "real" database?** At this scale it *is* the right tool: the
> Sheets API (via an Apps Script web app) is free, needs no infra, and the dev team can read,
> filter, sort, and chart feedback in a tool they already know. The same row schema drops into
> a database later *if* volume demands it — but I wouldn't add that complexity before it's
> needed.

---

## Why the drive-thru is genuinely hard (and why the design respects that)

I want to be upfront: the *ordering* AI that this feedback system supports is a genuinely
tricky product to get right. There are so many factors working against clean speech
recognition at a drive-thru mic:

- **Accents and dialects** — customers come from everywhere; the ASR and LLM have to handle
  non-native speakers and strong regional accents without asking people to repeat themselves.
- **Different menus per brand** — Carl's Jr., Hardee's, Fazoli's, Taco John's,
  Wienerschnitzel, and Captain D's have *completely* different items and naming. An ordering
  agent has to know the right menu for the store it's serving.
- **Background noise** — idling engines, wind across the mic, music, kids and passengers
  talking over each other. VAD and noise suppression exist precisely for this.
- **Crosstalk and self-corrections** — "I'll have the… no wait, make it two." People change
  their minds mid-order and talk over each other.
- **Mishears and homophones** — "no onions" vs. "no unions," numbers, sizes. This is where
  confirm-on-low-confidence matters.
- **Speed** — people are in a car, in a hurry. Every stage of the pipeline has to be fast or
  the whole thing feels broken.

**This is exactly why the feedback loop matters.** When the ordering AI mishears an accent or
trips over a menu item, that failure is normally invisible to the team building it — the
customer just drives off annoyed. This system turns each of those moments into a tagged,
structured signal (`misheard-order`, `didn't-understand`, `hard-to-hear`, …) routed straight
to the people who can fix it — and it's built to be forgiving of the *same* hard audio
conditions the ordering agent faces: it confirms the store by readback, listens fully through
pauses, and captures the whole complaint, not just the first sentence.

---

## What I'd do instead — the real phone-call flow

```
Scan QR at the mic                 One QR, whole fleet
  ↓
Phone dials Twilio number          Twilio Programmable Voice
  ↓
SIP trunk into LiveKit             Telephony reaches the agent
  ↓
LiveKit voice agent                VAD → ASR (Groq Whisper) → turn detection → LLM (Kimi K2) → TTS (ElevenLabs)
  · store lookup → confirm brand/city
  · rate 1–10 (half-steps ok, e.g. 4.5)
  · one follow-up question matched to the rating
  ↓
Summarize + tag                    Kimi K2 on Groq → summary, issue_tag, sentiment
  ↓
Append row via Google Sheets API   Apps Script web app — same sink as the demo
```

The Twilio + SIP steps are the **only** production-specific pieces. Everything from the agent
down is identical to what you're watching.

Two design decisions worth saying out loud:

- **Location is asked, never detected.** A phone call carries no GPS, and US carriers no
  longer sell real-time caller location. So the agent asks for the store number and confirms
  it; brand, city, region, and timezone are *looked up* from the registry — same code path at
  any fleet size.
- **One identical QR for the whole fleet.** Store identity comes from the spoken number, so
  there are no per-store codes or phone numbers to generate, track, or repair.

---

## Demo vs. production, component by component

| Component        | Demo (this repo)                                  | Production                                              |
|------------------|---------------------------------------------------|---------------------------------------------------------|
| **Entry**        | QR → web page → in-browser WebRTC                 | QR → phone dials Twilio number → SIP trunk → LiveKit    |
| **Voice pipeline** | VAD → Whisper → turn-detect → LLM → ElevenLabs  | **Identical** (plus stronger noise suppression, i18n)   |
| **LLM**          | Open-weight on Groq (Llama 3.3 70B today)         | **Kimi K2** on Groq — open-weight, better tool-calling  |
| **Summarize**    | Same LLM → summary / tag / sentiment              | **Kimi K2** — same open-weight model                    |
| **Store lookup** | `data/stores.csv`, 6 seed stores                  | Same schema as a DB table, fleet-wide                   |
| **Data sink**    | **Google Sheets API** (Apps Script web app)       | **Identical** — same Sheets API                         |
| **Analytics**    | Read / sort / chart the Sheet directly            | Charts + alerts layered on the same Sheet (or a DB later) |
| **Scale**        | Single worker, free tiers                         | Workers scale horizontally by peak concurrent calls     |
| **Compliance**   | Out of scope                                      | Recording-consent disclosure, PII redaction, retention  |

---

## What stays *identical* — why the demo actually de-risks the real thing

These are the same code in the demo and in production, so the demo is genuine proof, not a
mockup:

1. **The full voice pipeline** — VAD, ASR, turn detection, LLM-with-tools, TTS, barge-in.
2. **The conversation logic** — greeting, store readback confirmation, rating parse (including
   half-steps like "4.5"), rating-tiered follow-up, deterministic feedback capture, graceful
   call-ending.
3. **The registry lookup** — spoken number → brand / region / timezone, handling digits
   ("505") and words ("five oh five"). Same lookup for 6 stores or 5,000.
4. **The data sink** — the same Google Sheets API append, with the same row schema.

Swapping the browser for a phone (Twilio → SIP) and the model to Kimi K2 are the only real
changes. That's the whole point of the simplification.

---

## If I had production time & budget — the build order

1. **Pilot:** provision one Twilio number → SIP trunk into LiveKit; upgrade the Groq tier and
   switch the LLM to Kimi K2; keep the same Google Sheets sink; add a simple charts tab over
   the Sheet; onboard a handful of live stores.
2. **Rollout:** multi-region rollups and spike alerting ("misheard-order up 3× in the West
   this week"), multi-language, stronger noise suppression, and consent/retention hardening.
3. **Close the loop:** correlate issue-tag rates before/after an agent change to *prove* a fix
   worked — the metric that justifies the whole system.
4. **Scale the sink only if needed:** move the same row schema from Sheets into a database
   *when* volume demands it — not before.

---

## Q&A ammo (things people tend to ask)

- **"Which model does the thinking and summarizing?"** Kimi K2 — Moonshot AI's open-weight
  model, served on Groq. Open-weight keeps it cheap, fast, and vendor-swappable, and it's
  strong at the tool-calling this agent relies on.
- **"Isn't a Google Sheet too basic for production?"** Not at this scale — the Sheets API is
  free, zero-infra, and the dev team can read/filter/chart it immediately. The row schema
  moves to a database later *if* volume requires it.
- **"How does it handle accents / noise / a busy drive-thru?"** That's the hard part, and it's
  a pipeline problem: VAD gates out noise, turn detection waits for the caller to actually
  finish, and the agent confirms rather than guesses when confidence is low. Production adds
  heavier noise suppression and multi-language support.
- **"Why not just detect the store from the phone?"** No GPS on a call; carriers don't sell
  live location. Asking + registry lookup is robust and scales identically.
- **"Why one QR everywhere?"** Per-store numbers are expensive to manage, and a plain `tel:`
  QR can't carry store identity into a call anyway. Voice carries it instead.
