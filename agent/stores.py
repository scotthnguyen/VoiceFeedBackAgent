"""Store registry: turn a spoken store number into brand / location / region.

The registry is the source of truth. In the prototype it's a small CSV; in
production it would be a DB table with the same columns and identical lookup code.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, asdict
from pathlib import Path

_CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "stores.csv"


_STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "Washington, D.C.",
}


@dataclass(frozen=True)
class Store:
    store_id: str
    brand: str
    name: str
    city: str
    state: str
    region: str
    timezone: str

    @property
    def state_name(self) -> str:
        """Full spoken state name (e.g. 'Wyoming'), falling back to the raw value."""
        return _STATE_NAMES.get(self.state.upper(), self.state)

    def as_dict(self) -> dict:
        return asdict(self)


def _load(csv_path: Path = _CSV_PATH) -> dict[str, Store]:
    registry: dict[str, Store] = {}
    with csv_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            store = Store(
                store_id=row["store_id"].strip(),
                brand=row["brand"].strip(),
                name=row["name"].strip(),
                city=row["city"].strip(),
                state=row["state"].strip(),
                region=row["region"].strip(),
                timezone=row["timezone"].strip(),
            )
            registry[store.store_id] = store
    return registry


# Loaded once at import; O(1) lookups keyed by store_id.
_REGISTRY: dict[str, Store] = _load()


_WORD_DIGITS = {
    "zero": "0", "oh": "0", "o": "0", "nought": "0",
    "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}


def normalize_store_id(spoken: str) -> str | None:
    """Pull a store id out of free-form speech.

    Speech-to-text may return digits ("store 101") or words ("one oh one").
    We first grab any literal digits; if there are none, we fall back to
    mapping number words to digits and concatenating them.
    """
    if not spoken:
        return None

    digits = re.sub(r"\D", "", spoken)
    if digits:
        return digits

    words = re.findall(r"[a-z]+", spoken.lower())
    from_words = "".join(_WORD_DIGITS[w] for w in words if w in _WORD_DIGITS)
    return from_words or None


def lookup(spoken_or_id: str) -> Store | None:
    """Return the Store for a spoken utterance or raw id, or None if unknown."""
    store_id = normalize_store_id(spoken_or_id)
    if store_id is None:
        return None
    return _REGISTRY.get(store_id)


def all_stores() -> list[Store]:
    return list(_REGISTRY.values())


if __name__ == "__main__":
    # Quick manual check: python -m agent.stores
    print(f"Loaded {len(_REGISTRY)} stores from {_CSV_PATH}")
    for s in all_stores():
        print(f"  {s.store_id}  {s.brand:<16} {s.city}, {s.state}  [{s.region}]")
    print()
    for probe in ["101", "store 303 please", "one oh one", "999", ""]:
        hit = lookup(probe)
        label = f"{hit.brand} ({hit.city})" if hit else "NOT FOUND"
        print(f"  lookup({probe!r:>20}) -> {label}")
