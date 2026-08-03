"""US news-event calendar (CPI / NFP / FOMC) — static CSV shipped in repo.

Dates scraped from bls.gov / federalreserve.gov (see scripts/build_news_csv.py).
The CSV is user-editable; call reload_events() after changing it.
"""
import csv
from functools import lru_cache
from pathlib import Path

CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "reference" / "us_news_events.csv"


@lru_cache(maxsize=1)
def _load() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    if not CSV_PATH.exists():
        return out
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out.setdefault(row["date"], []).append(row)
    return out


def events_for_date(date_str: str) -> list[dict]:
    """[{date, event, name, time_et}] for one YYYY-MM-DD date."""
    return _load().get(date_str, [])


def flags_for_date(date_str: str) -> str:
    """Compact flag string for day_features, e.g. 'CPI' or 'CPI+FOMC'."""
    return "+".join(sorted({e["event"] for e in events_for_date(date_str)}))


def all_event_types() -> list[str]:
    return ["CPI", "NFP", "FOMC"]


def reload_events():
    _load.cache_clear()
