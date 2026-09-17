"""Summarize low-rating feedback with an open-source LLM (Llama via Groq).

Given the customer's spoken feedback, return a compact, structured record the
dev team can filter on: a one-line summary, an issue tag from a fixed taxonomy,
and sentiment. Only called when a rating is below the feedback threshold.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

# Fixed taxonomy keeps the sheet filterable instead of a free-for-all of tags.
ISSUE_TAGS = [
    "misheard-order",
    "wrong-order",
    "slow-service",
    "didnt-understand",
    "hard-to-hear",
    "menu-unavailable",
    "payment-issue",
    "rude-or-unhelpful",
    "other",
]

_SYSTEM_PROMPT = (
    "You analyze customer feedback about an AI voice agent used at fast-food "
    "drive-throughs. Given the customer's words, respond with STRICT JSON only "
    "(no markdown, no prose) using exactly these keys:\n"
    '  "summary": a single neutral sentence (max 20 words),\n'
    '  "issue_tag": one of ' + ", ".join(ISSUE_TAGS) + ",\n"
    '  "sentiment": one of "negative", "neutral", "positive".\n'
    "Pick the single best issue_tag. Use \"other\" only if nothing else fits."
)


@dataclass
class Summary:
    summary: str
    issue_tag: str
    sentiment: str


def _fallback(feedback: str) -> Summary:
    text = (feedback or "").strip()
    short = (text[:117] + "...") if len(text) > 120 else text
    return Summary(summary=short or "No feedback captured.", issue_tag="other", sentiment="negative")


def summarize_feedback(feedback: str) -> Summary:
    """Return a structured Summary for the given feedback text.

    Falls back to a safe default if the API call or JSON parse fails, so a bad
    LLM response never blocks logging the row.
    """
    feedback = (feedback or "").strip()
    if not feedback:
        return _fallback(feedback)

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return _fallback(feedback)

    try:
        from groq import Groq

        client = Groq(api_key=api_key)
        resp = client.chat.completions.create(
            model=os.environ.get("GROQ_LLM_MODEL", "llama-3.3-70b-versatile"),
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": feedback},
            ],
        )
        data = json.loads(resp.choices[0].message.content)
        tag = data.get("issue_tag", "other")
        if tag not in ISSUE_TAGS:
            tag = "other"
        sentiment = data.get("sentiment", "negative")
        if sentiment not in ("negative", "neutral", "positive"):
            sentiment = "negative"
        return Summary(
            summary=str(data.get("summary", "")).strip() or _fallback(feedback).summary,
            issue_tag=tag,
            sentiment=sentiment,
        )
    except Exception as exc:  # noqa: BLE001 - never let summarization break logging
        print(f"[summarize] falling back due to: {exc}")
        return _fallback(feedback)


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    samples = [
        "The AI couldn't understand me at all, I had to repeat my order like four times.",
        "It kept adding extra fries I never asked for and got the drink wrong.",
        "Honestly the line took forever, waited like ten minutes at the speaker.",
    ]
    for s in samples:
        out = summarize_feedback(s)
        print(f"\nIN : {s}\nOUT: {out}")
