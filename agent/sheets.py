"""Google Sheets sink: append one row of feedback per call.

Uses a Google Apps Script Web App (no Google Cloud project, service account, or
key required). Attach `scripts/apps_script.gs` to your Sheet, deploy it as a web
app ("Execute as: Me", "Who has access: Anyone"), and put the resulting URL in
.env as SHEET_WEBHOOK_URL (and an optional SHEET_WEBHOOK_TOKEN).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

HEADERS = [
    "timestamp_utc",
    "local_time",
    "store_id",
    "brand",
    "store_name",
    "region",
    "rating",
    "feedback_transcript",
    "summary",
    "issue_tag",
    "sentiment",
]

@dataclass
class FeedbackRow:
    """One feedback record. Summary fields stay blank for happy (>= threshold) calls."""

    store_id: str = ""
    brand: str = ""
    store_name: str = ""
    region: str = ""
    rating: Optional[float] = None  # whole (5) or half-step (4.5)
    feedback_transcript: str = ""
    summary: str = ""
    issue_tag: str = ""
    sentiment: str = ""
    store_timezone: str = "UTC"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_values(self) -> list:
        local = self.timestamp
        try:
            local = self.timestamp.astimezone(ZoneInfo(self.store_timezone))
        except Exception:
            pass
        return [
            self.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            local.strftime("%Y-%m-%d %H:%M:%S %Z"),
            self.store_id,
            self.brand,
            self.store_name,
            self.region,
            "" if self.rating is None else self.rating,
            self.feedback_transcript,
            self.summary,
            self.issue_tag,
            self.sentiment,
        ]


def append_feedback(row: FeedbackRow) -> None:
    """Append a feedback row to the Sheet via its Apps Script web app."""
    import requests

    url = os.environ.get("SHEET_WEBHOOK_URL")
    if not url:
        raise RuntimeError("SHEET_WEBHOOK_URL is not set")

    payload = {
        "token": os.environ.get("SHEET_WEBHOOK_TOKEN", ""),
        "headers": HEADERS,
        "row": row.to_values(),
    }
    # Apps Script 302-redirects to googleusercontent; requests follows it.
    resp = requests.post(url, json=payload, timeout=15, allow_redirects=True)
    resp.raise_for_status()

    try:
        data = resp.json()
    except ValueError:
        raise RuntimeError(f"Unexpected response from web app: {resp.text[:200]}")
    if not data.get("ok"):
        raise RuntimeError(f"Web app rejected the row: {data.get('error')}")


if __name__ == "__main__":
    # Manual smoke test: appends a fake row. Requires .env + credentials.
    from dotenv import load_dotenv

    load_dotenv()
    demo = FeedbackRow(
        store_id="303",
        brand="Fazoli's",
        store_name="Fazoli's - Lexington",
        region="Southeast",
        rating=4,
        feedback_transcript="The AI kept mishearing my order and added extra breadsticks.",
        summary="Customer's order was misheard; unwanted items added.",
        issue_tag="misheard-order",
        sentiment="negative",
        store_timezone="America/New_York",
    )
    append_feedback(demo)
    print("Appended a test row to the sheet.")
