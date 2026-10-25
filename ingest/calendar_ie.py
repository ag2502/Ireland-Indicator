"""Bank holidays, school holidays and big events: a small hand-maintained CSV.

School dates are the Department of Education's standardised calendar as best known; check
gov.ie each term. Add big events (Croke Park, Aviva) as kind=event, one row per day.
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import pandas as pd

CSV = Path(__file__).with_name("calendar_ie.csv")


@lru_cache
def _days() -> dict[str, set[date]]:
    df = pd.read_csv(CSV, comment="#")
    out: dict[str, set[date]] = {"bank_holiday": set(), "school_holiday": set(), "event": set()}
    for row in df.itertuples():
        d, end = date.fromisoformat(row.start_date), date.fromisoformat(row.end_date)
        while d <= end:
            out.setdefault(row.kind, set()).add(d)
            d += timedelta(days=1)
    return out


def is_bank_holiday(d: date) -> bool:
    return d in _days()["bank_holiday"]


def is_school_holiday(d: date) -> bool:
    # weekends are not "school holidays" for the purposes of the flag
    return d in _days()["school_holiday"] and d.weekday() < 5


def is_event_day(d: date) -> bool:
    return d in _days()["event"]


def flags(dates: pd.Series) -> pd.DataFrame:
    """Vectorised flags for a Series of datetime.date."""
    days = _days()
    return pd.DataFrame(
        {
            "bank_holiday": dates.map(lambda d: d in days["bank_holiday"]).astype(int),
            "school_holiday": dates.map(lambda d: d in days["school_holiday"] and d.weekday() < 5).astype(int),
            "event_day": dates.map(lambda d: d in days["event"]).astype(int),
        },
        index=dates.index,
    )
