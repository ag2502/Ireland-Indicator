"""Grade yesterday's live predictions against the reconstructed arrivals.

Joins predictions_log to GPS-interpolated arrivals and writes one accuracy_daily row per
horizon bucket (plus "all"): MAE of the official time and of our median, the 90th
percentile absolute errors, and how often the real arrival fell inside our range. This is
what the public accuracy page shows, so it uses only live predictions, never a replay.
"""

from __future__ import annotations

import argparse
import logging
from datetime import timedelta

import numpy as np
import pandas as pd
from sqlalchemy import Engine, text

from common import db
from common.timeutil import local_date, now_epoch
from pipeline.official import horizon_bucket

log = logging.getLogger(__name__)


def grade(engine: Engine, service_date: str) -> list[dict]:
    df = pd.read_sql(
        text(
            "SELECT p.made_at, p.official_at, p.p10_at, p.p50_at, p.p90_at, p.model_version, a.actual_at "
            "FROM predictions_log p JOIN arrivals a ON a.service_date = p.service_date "
            "AND a.trip_id = p.trip_id AND a.stop_sequence = p.stop_sequence "
            "WHERE p.service_date = :d AND a.method = 'gps_interp' AND p.p50_at IS NOT NULL"
        ),
        engine,
        params={"d": service_date},
    )
    if df.empty:
        return []
    # several predictions per (trip, stop) at one target: all count, as riders saw each
    df["bucket"] = horizon_bucket(((df["official_at"] - df["made_at"]) / 60).to_numpy()).to_numpy()
    rows = []
    for bucket, g in [("all", df), *df.groupby("bucket")]:
        ae_off = (g["actual_at"] - g["official_at"]).abs()
        ae_ours = (g["actual_at"] - g["p50_at"]).abs()
        rows.append(
            {
                "service_date": service_date,
                "horizon": str(bucket),
                "n": len(g),
                "mae_official_s": float(ae_off.mean()),
                "mae_ours_s": float(ae_ours.mean()),
                "p90_abs_official_s": float(np.quantile(ae_off, 0.9)),
                "p90_abs_ours_s": float(np.quantile(ae_ours, 0.9)),
                "coverage": float(g["actual_at"].between(g["p10_at"], g["p90_at"]).mean()),
                "mean_width_s": float((g["p90_at"] - g["p10_at"]).mean()),
                "model_version": g["model_version"].mode().iloc[0],
            }
        )
    return rows


def run(argv: list[str] | None = None) -> int:
    """CLI: bustrust accuracy [--date YYYYMMDD] [--days N]"""
    p = argparse.ArgumentParser(prog="bustrust accuracy")
    p.add_argument("--date", help="service date to grade (default: yesterday)")
    p.add_argument("--days", type=int, default=1)
    args = p.parse_args(argv)
    engine = db.init_db()
    end = local_date(now_epoch()) - timedelta(days=1)
    dates = [args.date] if args.date else [(end - timedelta(days=i)).strftime("%Y%m%d") for i in range(args.days)]
    for d in dates:
        rows = grade(engine, d)
        db.upsert_rows(engine, db.accuracy_daily, rows, keys=["service_date", "horizon"])
        overall = next((r for r in rows if r["horizon"] == "all"), None)
        if overall:
            log.info(
                "%s: n=%d, MAE official %.0f s, ours %.0f s, coverage %.2f",
                d, overall["n"], overall["mae_official_s"], overall["mae_ours_s"], overall["coverage"],
            )  # fmt: skip
        else:
            log.info("%s: no graded predictions", d)
    return 0
