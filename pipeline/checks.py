"""Daily data checks. Each writes a row to data_checks; any `fail` makes the command exit 1
so cron and CI surface it.

Checks (thresholds in CHECKS):
  poll_success      share of realtime polls that succeeded in the last 24 h
  max_poll_gap      longest gap between successful polls of either feed, minutes
  gps_coverage      share of yesterday's labelled trips seen with GPS
  impossible_speed  share of consecutive pings implying more than 120 km/h
  stop_order        share of trips whose arrivals go backwards in stop order
  schedule_join     share of trip_ids in recent trip updates found in the schedule
"""

from __future__ import annotations

import argparse
import logging

import numpy as np
import pandas as pd
from sqlalchemy import Engine, text

from common import db, geo
from common.schedule import latest_version
from common.timeutil import local_date, now_epoch

log = logging.getLogger(__name__)

# name: (warn if beyond, fail if beyond, "min" when low is bad / "max" when high is bad)
CHECKS = {
    "poll_success": (0.99, 0.95, "min"),
    "max_poll_gap": (5, 30, "max"),
    "gps_coverage": (0.85, 0.6, "min"),
    "impossible_speed": (0.005, 0.02, "max"),
    "stop_order": (0.01, 0.05, "max"),
    "schedule_join": (0.98, 0.9, "min"),
}


def status(name: str, value: float) -> str:
    warn, fail, kind = CHECKS[name]
    if value is None or np.isnan(value):
        return "warn"
    if kind == "min":
        return "fail" if value < fail else "warn" if value < warn else "ok"
    return "fail" if value > fail else "warn" if value > warn else "ok"


def compute(engine: Engine, now: int | None = None) -> dict[str, tuple[float, str]]:
    now = now or now_epoch()
    day_ago = now - 86400
    out: dict[str, tuple[float, str]] = {}
    q = lambda sql, **p: pd.read_sql(text(sql), engine, params=p)  # noqa: E731

    polls = q(
        "SELECT polled_at, feed, ok FROM poll_log WHERE polled_at >= :a AND feed IN ('trip_updates','vehicles')",
        a=day_ago,
    )
    out["poll_success"] = (float(polls["ok"].astype(bool).mean()) if len(polls) else np.nan, "")
    ok = polls[polls["ok"].astype(bool)].sort_values("polled_at")
    gap = float(np.diff(np.r_[ok["polled_at"].to_numpy(), now]).max()) / 60 if len(ok) else np.nan
    out["max_poll_gap"] = (gap, "minutes")

    yday = (local_date(now) - pd.Timedelta(days=1)).strftime("%Y%m%d")
    trips = q("SELECT ran, cancelled_in_feed FROM trip_outcomes WHERE service_date = :d", d=yday)
    if len(trips):
        not_cancelled = trips[~trips["cancelled_in_feed"].astype(bool)]
        out["gps_coverage"] = (float(not_cancelled["ran"].astype(bool).mean()), f"{len(trips)} trips on {yday}")
    else:
        out["gps_coverage"] = (np.nan, f"no outcomes for {yday}")

    vp = q(
        "SELECT trip_id, ping_at, lat, lon FROM vehicle_positions WHERE polled_at >= :a ORDER BY trip_id, ping_at",
        a=day_ago,
    ).drop_duplicates(["trip_id", "ping_at"])
    if len(vp) > 1:
        same = vp["trip_id"].eq(vp["trip_id"].shift())
        dt = vp["ping_at"].diff()
        dist = np.array(
            [
                geo.haversine_m(a, b, c, d)
                for a, b, c, d in zip(vp["lat"].shift(), vp["lon"].shift(), vp["lat"], vp["lon"], strict=True)
            ]
        )
        speed = dist / dt.where(dt > 0)
        pairs = same & dt.gt(0)
        out["impossible_speed"] = (float((speed[pairs] > 33.3).mean()) if pairs.any() else 0.0, "")
    else:
        out["impossible_speed"] = (np.nan, "no pings")

    arr = q("SELECT trip_id, stop_sequence, actual_at FROM arrivals WHERE service_date = :d", d=yday)
    if len(arr):
        arr = arr.sort_values(["trip_id", "stop_sequence"])
        back = arr.groupby("trip_id")["actual_at"].diff() < -30
        out["stop_order"] = (float(back.groupby(arr["trip_id"]).any().mean()), "")
    else:
        out["stop_order"] = (np.nan, "no arrivals")

    version = latest_version(engine)
    tu_trips = q("SELECT DISTINCT trip_id FROM trip_updates WHERE polled_at >= :a", a=now - 3600)
    if len(tu_trips) and version:
        known = q("SELECT trip_id FROM gtfs_trips WHERE schedule_version = :v", v=version)
        out["schedule_join"] = (float(tu_trips["trip_id"].isin(set(known["trip_id"])).mean()), version)
    else:
        out["schedule_join"] = (np.nan, "no recent trip updates")
    return {k: (v, status(k, v), d) for k, (v, d) in out.items()}  # type: ignore[misc]


def run(argv: list[str] | None = None) -> int:
    """CLI: bustrust checks"""
    argparse.ArgumentParser(prog="bustrust checks").parse_args(argv)
    engine = db.init_db()
    now = now_epoch()
    results = compute(engine, now)
    rows = []
    for name, (value, st, detail) in results.items():  # type: ignore[misc]
        log.log(logging.WARNING if st != "ok" else logging.INFO, "%-17s %-4s %s %s", name, st, value, detail)
        rows.append(
            {
                "run_at": now,
                "check": name,
                "status": st,
                "value": None if value is None or np.isnan(value) else value,
                "detail": detail,
            }
        )
    db.insert_rows(engine, db.data_checks, rows)
    return 1 if any(r["status"] == "fail" for r in rows) else 0
