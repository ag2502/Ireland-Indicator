"""Measure label error against arrivals timed by hand at a stop (the Week 2 stopwatch check).

CSV columns: stop_code, route, observed_local ("2026-10-28 08:14:05"), notes (optional).
Each observation is matched to the reconstructed arrival of that route at that stop whose
time is closest; the report is the distribution of (reconstructed - observed).
"""

from __future__ import annotations

import argparse
import logging

import numpy as np
import pandas as pd
from sqlalchemy import Engine, text

from common import db

log = logging.getLogger(__name__)


def compare(engine: Engine, obs: pd.DataFrame, max_match_s: int = 600) -> pd.DataFrame:
    obs = obs.copy()
    obs["observed_at"] = pd.to_datetime(obs["observed_local"]).dt.tz_localize("Europe/Dublin").astype("int64") // 10**9
    out = []
    for row in obs.itertuples():
        cands = pd.read_sql(
            text(
                "SELECT a.actual_at, a.method, a.trip_id FROM arrivals a "
                "JOIN gtfs_stops s ON s.stop_id = a.stop_id AND s.schedule_version = a.schedule_version "
                "JOIN gtfs_routes r ON r.route_id = a.route_id AND r.schedule_version = a.schedule_version "
                "WHERE s.stop_code = :code AND r.route_short_name = :route "
                "AND a.actual_at BETWEEN :lo AND :hi"
            ),
            engine,
            params={
                "code": str(row.stop_code),
                "route": str(row.route),
                "lo": row.observed_at - max_match_s,
                "hi": row.observed_at + max_match_s,
            },
        )
        if cands.empty:
            out.append({**row._asdict(), "error_s": np.nan, "method": None})
            continue
        best = cands.iloc[(cands["actual_at"] - row.observed_at).abs().argmin()]
        out.append({**row._asdict(), "error_s": best.actual_at - row.observed_at, "method": best.method})
    return pd.DataFrame(out)


def run(argv: list[str] | None = None) -> int:
    """CLI: bustrust stopwatch observations.csv"""
    p = argparse.ArgumentParser(prog="bustrust stopwatch")
    p.add_argument("csv")
    args = p.parse_args(argv)
    res = compare(db.get_engine(), pd.read_csv(args.csv))
    matched = res["error_s"].dropna()
    print(res[["stop_code", "route", "observed_local", "method", "error_s"]].to_string(index=False))
    if len(matched):
        print(
            f"\nmatched {len(matched)}/{len(res)}; median error {matched.median():+.0f} s, "
            f"median |error| {matched.abs().median():.0f} s, p90 |error| {matched.abs().quantile(0.9):.0f} s"
        )
    return 0
