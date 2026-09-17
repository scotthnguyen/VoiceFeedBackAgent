# Drive-Thru Voice Feedback — Product Vision

*This is the product document for where the prototype goes. The code in this repo is a
functional slice (browser-based agent, one Google Sheet); this describes the real thing.*

## Problem

Drive-throughs are increasingly run by AI voice agents. When the AI mishears an order, stalls,
or can't understand an accent, that failure is invisible to the team building the agent — the
customer just drives off annoyed. There's no low-friction channel that turns a bad (or good)
experience into structured, actionable signal for the dev team, tagged by store and region.

## Goal

Let a customer give feedback in seconds, right at the mic, by talking to an AI — and route
that feedback, summarized and categorized, to the people who can fix the ordering agent.
Measure it by: feedback volume per store, issue-type trends over time, and whether flagged
issues drop after a fix ships.

## User flow (production)

```
QR sticker at the mic → customer's phone dials one number (Twilio)
  → LiveKit voice agent answers
  → "say the store number" → validate against the store registry → confirm brand/city
  → "rate 1–10"
  → if below threshold: "what could we have done better?"
  → agent thanks & ends
  → LLM summarizes → structured record → analytics store + dashboards
```

The conversation logic is identical to the prototype; production swaps the **entry channel**
from an in-browser WebRTC session to a real phone call.

## Why the agent asks for location (and doesn't detect it)

A phone call carries no GPS, and (post-2019, in the US) carriers no longer sell real-time
subscriber location commercially — it's effectively E911-only. So there is **no reliable way to
auto-detect which store a caller is standing at.** The design consequence:

- The agent **asks** for the store number and confirms it by readback.
- Everything else — brand, city, state, **region**, **timezone** — is derived from a **store
  registry** (source of truth). Region/timezone are attributes you *look up*, never detect.
- Optional weak enrichment: **Twilio Lookup** can return the caller number's *registered* region
  (from the number prefix) as a sanity cross-check — not physical location.

This is why **one identical QR works fleet-wide**: uniqueness would only help if each store also
had its own phone number (expensive to buy and manage at scale), and even then a plain `tel:`
QR can't carry store identity into the call. Asking is the robust path.

## Telephony (Twilio)

- Twilio Programmable Voice number → **SIP trunk** into LiveKit; inbound calls are routed to the
  agent worker.
- **One shared number vs. regional numbers:** start with one; add regional/toll-free numbers only
  if call volume or localization requires it (identity still comes from the spoken store number).
- **Concurrency & scale:** LiveKit agent workers scale horizontally; size by peak concurrent calls.
- **Abuse/spam:** rate-limit per caller, cap call duration, drop silent calls.

## Agent (LiveKit)

- Pipeline: **STT → LLM → TTS**, with VAD/turn detection and **barge-in** (let customers interrupt).
- **Noise handling** for the drive-thru environment (noise suppression, confidence thresholds,
  confirm-on-low-confidence).
- **Multi-language** (detect + respond in the caller's language).
- Deterministic data capture via function tools (store lookup, rating, feedback) so the logged
  record is always well-formed regardless of phrasing.

## Data & analytics

- **Store registry** (`store_id | brand | name | city | state | region | timezone`) as the source
  of truth — a DB table in production; the prototype's CSV has identical columns.
- **Feedback store**: a real database (not a spreadsheet) as system of record; Google Sheets
  remains as one *export/target* for quick browsing.
- **Issue taxonomy**: a fixed, evolvable set of tags (misheard-order, wrong-order, slow-service,
  didn't-understand, hard-to-hear, payment-issue, …) so feedback is filterable, not free-text.
- **Sentiment** + rating trends.
- **Dashboards & alerts**: per-store and per-region rollups, week-over-week trend, and alerts when
  an issue tag spikes at a store/region (e.g. "misheard-order up 3× in West this week").
- **Close the loop**: correlate issue-tag rates before/after an agent change to prove fixes work.

## Privacy & compliance

- **Recording consent** disclosure at call start where required; store transcripts, not raw audio,
  unless audio is explicitly needed.
- **PII**: redact obvious PII from transcripts; the flow deliberately avoids collecting names,
  payment info, etc.
- **Retention**: define a retention window and deletion policy for transcripts.

## Scale & cost

- **Prototype**: entirely free tiers (LiveKit Cloud, Groq, Google service account) and no telephony.
- **Production cost drivers**: Twilio per-minute + number rental, STT/LLM/TTS usage, infra for
  workers and the data store. Open-weight models (via Groq or self-hosted) keep inference cheap;
  cost scales with call minutes, so short, focused calls matter.
- **QR strategy**: one identical `tel:` QR for the whole fleet; store identity comes from voice,
  so there are no per-store codes or numbers to generate, track, or repair.

## Roadmap

1. **Prototype** (this repo): browser agent, single Sheet, 6 seed stores. Proves the conversation,
   registry lookup, summarization, and logging end-to-end.
2. **Pilot**: add Twilio phone entry; real database + a basic dashboard; a handful of live stores.
3. **Rollout**: multi-region, dashboards + alerting, multi-language, consent/retention hardening,
   fleet-wide registry.
4. **Future channels**: SMS follow-up, in-browser voice (already built here) as an alternate entry,
   proactive post-order feedback prompts.
