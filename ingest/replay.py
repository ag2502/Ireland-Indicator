"""Rebuild the realtime tables from the raw archive, for a restore or after a parser fix.

Every successful poll stores its exact response as `<feed>/YYYY/MM/DD/HHMMSS.pb.gz` (UTC)
under BT_RAW_ARCHIVE. Replaying a UTC day re-parses each file with the poll time taken
from its key, so the rows match what the poller wrote. The day's existing rows are
deleted first, so a replay can run twice. Never calls the NTA.

Not restored: poll_log and feed_gaps (failed polls leave no file), predictions_log (what
we showed riders live is not in the feed).
"""

from __future__ import annotations

import argparse
import gzip
import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Engine, delete, func, select

from common import db, storage
from common.config import get_settings
from ingest import feeds
from ingest.poller import operator_routes

log = logging.getLogger(__name__)


def polled_at(key: str) -> int:
    """`vehicles/2026/10/25/071502.pb.gz` -> epoch seconds of that UTC instant."""
    _, y, m, d, rest = key.split("/")
    hms = rest.split(".")[0]
    return int(datetime(int(y), int(m), int(d), int(hms[:2]), int(hms[2:4]), int(hms[4:6]), tzinfo=UTC).timestamp())


def replay_day(
    engine: Engine, base: str, day: date, keep_routes: set[str] | None, force: bool = False
) -> dict[str, int]:
    start = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp())
    counts: dict[str, int] = {}
    for feed in feeds.FEEDS:
        keys = storage.list_keys(base, f"{feed}/{day:%Y/%m/%d}")
        if not keys:
            continue
        table = db.trip_updates if feed == "trip_updates" else db.vehicle_positions
        in_day = (table.c.polled_at >= start, table.c.polled_at < start + 86400)
        with engine.begin() as conn:
            have = conn.execute(select(func.count(func.distinct(table.c.polled_at))).where(*in_day)).scalar() or 0
            if have > len(keys) and not force:
                log.warning(
                    "%s %s: %d polls stored but %d archived; skipped (--force to replace)", day, feed, have, len(keys)
                )
                continue
            conn.execute(delete(table).where(*in_day))
        n = 0
        for key in keys:
            rows, _, _ = feeds.parse(feed, gzip.decompress(storage.get_bytes(base, key)), polled_at(key), keep_routes)
            n += db.insert_rows(engine, table, rows)
        counts[feed] = n
        log.info("%s %s: %d files, %d rows", day, feed, len(keys), n)
    return counts


def run(argv: list[str] | None = None) -> int:
    """CLI: bustrust replay-raw --start YYYY-MM-DD [--end YYYY-MM-DD] [--force]   (UTC days, inclusive)"""
    p = argparse.ArgumentParser(prog="bustrust replay-raw")
    p.add_argument("--start", required=True, type=date.fromisoformat)
    p.add_argument("--end", type=date.fromisoformat, help="default: same as --start")
    p.add_argument("--force", action="store_true", help="replace days that hold polls the archive lacks")
    args = p.parse_args(argv)
    engine = db.init_db()
    keep = operator_routes(engine)
    day, end = args.start, args.end or args.start
    while day <= end:
        replay_day(engine, get_settings().raw_archive, day, keep, args.force)
        day += timedelta(days=1)
    return 0
