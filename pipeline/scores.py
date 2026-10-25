"""Route reliability scores. No ML: standard headway arithmetic and plain shares.

Frequent services (scheduled headway of 12 minutes or less) are judged on waiting time.
With headways h_i between consecutive buses at a stop, a rider turning up at random waits

    AWT = sum(h_i^2) / (2 * sum(h_i))

and excess wait time is EWT = AWT_actual - AWT_scheduled. Infrequent services are judged
on punctuality: the share of departures between 1 minute early and 5 minutes late.
Bunching is an actual headway under a quarter of the scheduled one.
"""

from __future__ import annotations

import argparse
import logging
from datetime import timedelta

import numpy as np
import pandas as pd
from sqlalchemy import Engine, text

from common import db
from common.schedule import Schedule, latest_version
from common.timeutil import local_date, now_epoch, service_day_reference, to_local

log = logging.getLogger(__name__)

FREQUENT_HEADWAY_S = 12 * 60
EARLY_S, LATE_S = -60, 300
DAY_START_H, DAY_END_H = 7, 19  # judge headways in the daytime, away from first/last bus


def awt(headways: np.ndarray) -> float:
    h = np.asarray(headways, dtype=float)
    h = h[h > 0]
    return float((h**2).sum() / (2 * h.sum())) if len(h) else float("nan")


def on_time(delay_s: pd.Series) -> pd.Series:
    return (delay_s >= EARLY_S) & (delay_s <= LATE_S)


def _reference_stop(se: pd.DataFrame) -> str:
    """The stop most trips of this route-direction serve, nearest the middle of the route."""
    counts = se.groupby("stop_id").agg(n=("trip_id", "nunique"), seq=("stop_sequence", "median"))
    top = counts[counts["n"] == counts["n"].max()]
    mid = se["stop_sequence"].median()
    return str((top["seq"] - mid).abs().idxmin())


def headway_scores(se: pd.DataFrame, arr: pd.DataFrame, outcomes: pd.DataFrame) -> dict:
    """AWT/EWT and bunching for one route-direction-day at its reference stop."""
    ref = _reference_stop(se)
    s = se[se["stop_id"] == ref][["trip_id", "scheduled_at"]]
    hours = s["scheduled_at"].map(lambda t: to_local(int(t)).hour)
    s = s[(hours >= DAY_START_H) & (hours < DAY_END_H)]
    if len(s) < 4:
        return {}
    ran = set(outcomes.loc[~outcomes["noshow"], "trip_id"]) if len(outcomes) else set()
    a = arr[arr["stop_id"] == ref].set_index("trip_id")["actual_at"]
    # trips that ran but lack a label here: scheduled time shifted by the trip's median delay
    trip_delay = (arr["actual_at"] - arr["scheduled_at"]).groupby(arr["trip_id"]).median()
    s = s.assign(
        actual_at=s["trip_id"].map(a),
        ran=s["trip_id"].isin(ran) | s["trip_id"].isin(a.index),
    )
    fill = s["scheduled_at"] + s["trip_id"].map(trip_delay).fillna(0)
    s["actual_at"] = s["actual_at"].fillna(fill.where(s["ran"]))
    sched_h = np.diff(np.sort(s["scheduled_at"].to_numpy()))
    act = np.sort(s.loc[s["ran"], "actual_at"].to_numpy())
    act_h = np.diff(act)
    awt_s, awt_a = awt(sched_h), awt(act_h)
    median_sched = float(np.median(sched_h)) if len(sched_h) else np.nan
    bunched = int((act_h < 0.25 * median_sched).sum()) if len(act_h) else 0
    return {
        "awt_sched_s": awt_s,
        "awt_actual_s": awt_a,
        "ewt_s": awt_a - awt_s,
        "n_bunched": bunched,
        "n_headways": len(act_h),
        "frequent": bool(median_sched <= FREQUENT_HEADWAY_S),
    }


def day_scores(se: pd.DataFrame, arrivals: pd.DataFrame, outcomes: pd.DataFrame, service_date: str) -> pd.DataFrame:
    rows = []
    gps = arrivals  # both methods count for punctuality; method is kept for audits
    for (route_id, direction), g in se.groupby(["route_id", "direction_id"]):
        o = outcomes[(outcomes["route_id"] == route_id) & (outcomes["direction_id"] == direction)]
        if o.empty:
            continue
        a = gps[(gps["route_id"] == route_id) & (gps["direction_id"] == direction)]
        # punctuality at every labelled stop except the last (buses run early into termini)
        last_seq = g.groupby("trip_id")["stop_sequence"].max()
        a = a[a["stop_sequence"] < a["trip_id"].map(last_seq)]
        delay = a["actual_at"] - a["scheduled_at"]
        row = {
            "service_date": service_date,
            "route_id": route_id,
            "direction_id": int(direction),
            "route_short_name": g["route_short_name"].iloc[0],
            "n_trips": len(o),
            "n_noshow": int(o["noshow"].sum()),
            "n_departures": len(a),
            "n_on_time": int(on_time(delay).sum()),
            "mean_delay_s": float(delay.mean()) if len(delay) else None,
            "frequent": False,
            "n_bunched": 0,
            "n_headways": 0,
        }
        row.update(headway_scores(g, a, o))
        rows.append(row)
    return pd.DataFrame(rows)


def load_day(engine: Engine, service_date: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    p = {"d": service_date}
    arrivals = pd.read_sql(text("SELECT * FROM arrivals WHERE service_date = :d"), engine, params=p)
    outcomes = pd.read_sql(text("SELECT * FROM trip_outcomes WHERE service_date = :d"), engine, params=p)
    if outcomes.empty:
        return arrivals, outcomes, pd.DataFrame()
    outcomes["noshow"] = outcomes["noshow"].astype(bool)
    ref = service_day_reference(service_date)
    sched = Schedule.load(engine, latest_version(engine, at=ref + 12 * 3600), service_dates=[service_date])
    se = sched.scheduled_epochs(service_date)
    se = se[se["route_id"].isin(set(outcomes["route_id"]))]
    return arrivals, outcomes, se


def heatmap(arrivals: pd.DataFrame) -> pd.DataFrame:
    """On-time share and median delay by route, weekday and local hour."""
    if arrivals.empty:
        return pd.DataFrame()
    local = pd.to_datetime(arrivals["scheduled_at"], unit="s", utc=True).dt.tz_convert("Europe/Dublin")
    delay = arrivals["actual_at"] - arrivals["scheduled_at"]
    g = pd.DataFrame(
        {
            "route_id": arrivals["route_id"],
            "weekday": local.dt.weekday,
            "hour": local.dt.hour,
            "on_time": on_time(delay),
            "delay": delay,
        }
    ).groupby(["route_id", "weekday", "hour"])
    return g.agg(
        n=("delay", "size"), on_time_share=("on_time", "mean"), median_delay_s=("delay", "median")
    ).reset_index()


def segments(arrivals: pd.DataFrame) -> pd.DataFrame:
    """Median time lost (actual minus scheduled run time) per consecutive stop pair."""
    a = arrivals[arrivals["method"] == "gps_interp"].sort_values(["service_date", "trip_id", "stop_sequence"])
    if a.empty:
        return pd.DataFrame()
    grp = a.groupby(["service_date", "trip_id"])
    a = a.assign(
        to_stop_id=grp["stop_id"].shift(-1),
        next_seq=grp["stop_sequence"].shift(-1),
        run_actual=grp["actual_at"].shift(-1) - a["actual_at"],
        run_sched=grp["scheduled_at"].shift(-1) - a["scheduled_at"],
    )
    a = a[a["next_seq"] == a["stop_sequence"] + 1]
    a = a.assign(lost=a["run_actual"] - a["run_sched"])
    out = (
        a.groupby(["route_id", "direction_id", "stop_id", "to_stop_id"])
        .agg(from_seq=("stop_sequence", "median"), n=("lost", "size"), median_lost_s=("lost", "median"))
        .reset_index()
        .rename(columns={"stop_id": "from_stop_id"})
    )
    out["from_seq"] = out["from_seq"].astype(int)
    return out[out["n"] >= 5]


def run(argv: list[str] | None = None) -> int:
    """CLI: bustrust scores [--days N] [--window 28]"""
    p = argparse.ArgumentParser(prog="bustrust scores")
    p.add_argument("--days", type=int, default=1, help="recompute daily scores for the last N days")
    p.add_argument("--window", type=int, default=28, help="days behind the heatmap and segments")
    args = p.parse_args(argv)
    engine = db.init_db()
    today = local_date(now_epoch())
    for k in range(args.days, -1, -1):
        d = (today - timedelta(days=k)).strftime("%Y%m%d")
        arrivals, outcomes, se = load_day(engine, d)
        if se.empty:
            continue
        scores = day_scores(se, arrivals, outcomes, d)
        db.upsert_rows(
            engine, db.route_scores_daily, scores.to_dict("records"), keys=["service_date", "route_id", "direction_id"]
        )
        log.info("%s: scored %d route-directions", d, len(scores))

    since = (today - timedelta(days=args.window)).strftime("%Y%m%d")
    window = pd.read_sql(text("SELECT * FROM arrivals WHERE service_date >= :d"), engine, params={"d": since})
    now = now_epoch()
    hm = heatmap(window).assign(window_days=args.window, computed_at=now)
    seg = segments(window).assign(computed_at=now)
    db.replace_table(engine, db.route_heatmap, hm.to_dict("records"))
    db.replace_table(engine, db.segment_delays, seg.to_dict("records"))
    log.info("heatmap %d cells, %d segments", len(hm), len(seg))
    return 0


def reliability_pct(n_trips: float, n_noshow: float, n_departures: float, n_on_time: float) -> float:
    """Share of scheduled trips that ran *and* kept time: comparable across all routes."""
    if not n_trips or not n_departures:
        return float("nan")
    return 100 * (1 - n_noshow / n_trips) * (n_on_time / n_departures)
