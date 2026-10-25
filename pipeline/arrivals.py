"""Reconstruct when each bus actually reached each stop, and whether each trip ran.

The feeds give predictions and positions, not arrivals, so we build the label:

1. Project each GPS ping onto its trip's route shape (distance travelled), searching only
   a window ahead of the previous ping so loops and out-and-back routes stay correct.
2. Interpolate the moment the bus crossed each stop's distance along the shape.
3. Where pings are missing on a trip that ran (has some GPS), fall back to the last official
   prediction before the stop dropped out of the trip update.
4. Record which method produced each arrival and the gap between bracketing pings.

Trip outcomes: a trip is a no-show if it was never seen with GPS in its window or was
cancelled in the feed. Trips that overlap a feed gap are left unlabelled.
"""

from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd
from shapely.geometry import Point
from shapely.ops import substring
from sqlalchemy import Engine, text

from common import db, geo
from common.schedule import Schedule, latest_version
from common.timeutil import local_date, now_epoch, service_day_reference
from pipeline.official import propagate

log = logging.getLogger(__name__)

MAX_OFFSET_M = 150  # a ping further than this from the shape is on another road
MAX_SPEED_MS = 25  # 90 km/h: anything faster between pings is a GPS glitch
MAX_BRACKET_S = 600  # don't interpolate across gaps longer than 10 minutes


def load_realtime(engine: Engine, service_date: str, table: str) -> pd.DataFrame:
    ref = service_day_reference(service_date)
    return pd.read_sql(
        text(f"SELECT * FROM {table} WHERE start_date = :d OR (start_date IS NULL AND polled_at BETWEEN :a AND :b)"),
        engine,
        params={"d": service_date, "a": ref, "b": ref + 30 * 3600},
    )


def feed_gaps(engine: Engine, start: int, end: int) -> pd.DataFrame:
    return pd.read_sql(
        text("SELECT feed, start_at, end_at FROM feed_gaps WHERE end_at >= :a AND start_at <= :b"),
        engine,
        params={"a": start, "b": end},
    )


# ---------------------------------------------------------------- GPS track
def track(pings: pd.DataFrame, line) -> pd.DataFrame:
    """Distance along shape for each ping of one trip, monotone, glitches dropped."""
    p = pings.dropna(subset=["ping_at", "lat", "lon"]).drop_duplicates("ping_at").sort_values("ping_at")
    if p.empty:
        return p.assign(dist=[])
    xs, ys = geo.to_itm(p["lon"].to_numpy(), p["lat"].to_numpy())
    length = line.length
    dists, keep = [], []
    prev_d, prev_t = None, None
    for x, y, t in zip(np.atleast_1d(xs), np.atleast_1d(ys), p["ping_at"].to_numpy(), strict=True):
        pt = Point(x, y)
        if prev_d is None:
            lo, hi = 0.0, length
        else:
            lo = max(prev_d - 100, 0.0)
            hi = min(prev_d + MAX_SPEED_MS * max(t - prev_t, 1) + 300, length)
        window = substring(line, lo, hi) if (lo, hi) != (0.0, length) else line
        d = lo + window.project(pt)
        offset = line.interpolate(d).distance(pt)
        if offset > MAX_OFFSET_M:
            keep.append(False)
            dists.append(np.nan)
            continue
        if prev_d is not None and (d - prev_d) / max(t - prev_t, 1) > MAX_SPEED_MS:
            keep.append(False)
            dists.append(np.nan)
            continue
        d = max(d, prev_d) if prev_d is not None else d
        keep.append(True)
        dists.append(d)
        prev_d, prev_t = d, t
    p = p.assign(dist=dists)[keep]
    return p


def interpolate_stops(tr: pd.DataFrame, stop_dist: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(arrival epoch, bracket gap seconds) per stop distance; NaN where not covered.

    While a bus dwells its distance repeats; the arrival is the first moment it reached
    that distance, so ties keep the earliest ping."""
    if len(tr) < 2:
        return np.full(len(stop_dist), np.nan), np.full(len(stop_dist), np.nan)
    d = tr["dist"].to_numpy()
    t = tr["ping_at"].to_numpy().astype(float)
    first = np.r_[True, np.diff(d) > 0.5]
    d, t = d[first], t[first]
    if len(d) < 2:
        return np.full(len(stop_dist), np.nan), np.full(len(stop_dist), np.nan)
    arr = np.interp(stop_dist, d, t, left=np.nan, right=np.nan)
    idx = np.clip(np.searchsorted(d, stop_dist), 1, len(d) - 1)
    gap = t[idx] - t[idx - 1]
    arr[gap > MAX_BRACKET_S] = np.nan
    # a stop just before the first ping: allow a short extrapolation at the trip start
    head = (stop_dist < d[0]) & (d[0] - stop_dist < 120)
    if head.any():
        speed = max((d[1] - d[0]) / max(t[1] - t[0], 1), 3.0)
        arr[head] = t[0] - (d[0] - stop_dist[head]) / speed
        gap[head] = t[1] - t[0]
    # the origin stop is a departure: a bus waiting at the terminus is "there" early, so
    # use the last moment it was still at that distance
    d_all = tr["dist"].to_numpy()
    t_all = tr["ping_at"].to_numpy().astype(float)
    last = np.r_[np.diff(d_all) > 0.5, True]
    if last.sum() >= 2 and stop_dist[0] >= d_all[0] - 30:
        dep = np.interp(stop_dist[0] + 15, d_all[last], t_all[last], left=np.nan, right=np.nan)
        if not np.isnan(dep):
            arr[0] = dep
    return arr, np.where(np.isnan(arr), np.nan, gap)


# ---------------------------------------------------------------- per day
def build_day(
    engine: Engine, service_date: str, version: str | None = None, now: int | None = None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Arrivals and trip outcomes for one service date."""
    ref = service_day_reference(service_date)
    version = version or latest_version(engine, at=ref + 12 * 3600)
    sched = Schedule.load(engine, version, service_dates=[service_date])
    se = sched.scheduled_epochs(service_date)
    vp = load_realtime(engine, service_date, "vehicle_positions")
    tu = load_realtime(engine, service_date, "trip_updates")
    trips_today = set(se["trip_id"])
    vp = vp[vp["trip_id"].isin(trips_today)]
    tu = tu[tu["trip_id"].isin(trips_today)]
    # routes with no realtime at all that day (feed outage, or not simulated) are unlabelled
    live_routes = set(vp["route_id"].dropna()) | set(tu["route_id"].dropna())
    se = se[se["route_id"].isin(live_routes)]
    if se.empty:
        return pd.DataFrame(), pd.DataFrame()

    # GPS arrivals
    out = []
    for trip_id, pings in vp.groupby("trip_id"):
        stops = se[se["trip_id"] == trip_id].sort_values("stop_sequence")
        line = sched.line(stops["shape_id"].iloc[0]) if len(stops) else None
        if line is None or stops["dist_m"].isna().any():
            continue
        tr = track(pings, line)
        arr, gap = interpolate_stops(tr, stops["dist_m"].to_numpy())
        out.append(stops.assign(actual_at=arr, uncertainty_s=gap, method="gps_interp"))
    gps = pd.concat(out, ignore_index=True) if out else se.iloc[0:0].assign(actual_at=[], uncertainty_s=[], method=[])
    gps = gps[gps["actual_at"].notna()]

    # fallback: last official prediction before the stop left the board
    off = propagate(tu, se)
    # only for trips that demonstrably ran (some GPS), so ghosts never get schedule-shaped arrivals
    off = off[off["vehicle_id"].notna() & off["trip_id"].isin(set(vp["trip_id"]))]
    last = off.sort_values("polled_at").drop_duplicates(["trip_id", "stop_sequence"], keep="last")
    # only trust it if the stop dropped off while the trip kept reporting (i.e. it was passed)
    trip_last_poll = tu.groupby("trip_id")["polled_at"].max()
    last = last[last["polled_at"] < last["trip_id"].map(trip_last_poll)]
    have = set(zip(gps["trip_id"], gps["stop_sequence"], strict=True))
    fb_mask = [(t, s) not in have for t, s in zip(last["trip_id"], last["stop_sequence"], strict=True)]
    fb = last[fb_mask].merge(se.drop(columns=["stop_id", "scheduled_at"]), on=["trip_id", "stop_sequence"], how="inner")
    fb = fb.assign(actual_at=fb["official_at"], uncertainty_s=np.nan, method="prediction")

    keep = [
        "service_date", "trip_id", "stop_sequence", "stop_id", "route_id", "direction_id",
        "scheduled_at", "actual_at", "method", "uncertainty_s",
    ]  # fmt: skip
    arrivals = pd.concat([gps.assign(service_date=service_date), fb.assign(service_date=service_date)])
    arrivals = arrivals.assign(schedule_version=version)[[*keep, "schedule_version"]]
    arrivals["actual_at"] = arrivals["actual_at"].round().astype("int64")
    # a stop the feed said would be skipped (diversion) was driven past, not served
    skipped = tu.loc[tu["stop_rel"] == "SKIPPED", ["trip_id", "stop_sequence"]].dropna().drop_duplicates()
    if len(skipped):
        key = skipped.astype({"stop_sequence": "int64"}).assign(_skip=True)
        arrivals = arrivals.merge(key, on=["trip_id", "stop_sequence"], how="left")
        arrivals = arrivals[arrivals["_skip"].isna()].drop(columns="_skip")

    # trip outcomes
    trips = se.groupby("trip_id").agg(
        route_id=("route_id", "first"),
        direction_id=("direction_id", "first"),
        scheduled_start_at=("scheduled_dep_at", "min"),
        scheduled_end_at=("scheduled_at", "max"),
    )
    gps_trips = set(vp["trip_id"])
    cancelled = set(tu.loc[tu["trip_rel"] == "CANCELED", "trip_id"])
    first_seen = (
        pd.concat([vp[["trip_id", "polled_at"]], tu[["trip_id", "polled_at"]]]).groupby("trip_id")["polled_at"].min()
    )
    trips = trips.assign(
        service_date=service_date,
        ran=trips.index.isin(gps_trips),
        cancelled_in_feed=trips.index.isin(cancelled),
        first_seen_at=trips.index.map(first_seen),
    )
    trips["noshow"] = ~trips["ran"] | trips["cancelled_in_feed"]
    # unlabelled: trips not finished yet, or overlapping a vehicles-feed gap
    now = now if now is not None else now_epoch()
    gaps = feed_gaps(engine, ref, ref + 30 * 3600)
    gaps = gaps[gaps["feed"] == "vehicles"]
    in_gap = np.zeros(len(trips), dtype=bool)
    for g in gaps.itertuples():
        in_gap |= (trips["scheduled_start_at"] <= g.end_at).to_numpy() & (
            trips["scheduled_end_at"] >= g.start_at
        ).to_numpy()
    done = (trips["scheduled_end_at"] + 3600 < now).to_numpy()
    trips = trips[done & ~in_gap].reset_index()
    outcome_cols = [
        "service_date", "trip_id", "route_id", "direction_id", "scheduled_start_at",
        "ran", "cancelled_in_feed", "noshow", "first_seen_at",
    ]  # fmt: skip
    trips = trips[outcome_cols]
    trips["first_seen_at"] = trips["first_seen_at"].astype("Int64")
    return arrivals.reset_index(drop=True), trips


def write_day(engine: Engine, service_date: str, now: int | None = None) -> tuple[int, int]:
    arrivals, outcomes = build_day(engine, service_date, now=now)
    with engine.begin() as conn:  # idempotent: rebuild the day
        conn.execute(db.arrivals.delete().where(db.arrivals.c.service_date == service_date))
        conn.execute(db.trip_outcomes.delete().where(db.trip_outcomes.c.service_date == service_date))
    db.bulk_load_df(engine, db.arrivals, arrivals)
    db.bulk_load_df(engine, db.trip_outcomes, outcomes)
    if len(arrivals):
        log.info(
            "%s: %d arrivals (%s), %d trips, %d no-shows",
            service_date,
            len(arrivals),
            arrivals["method"].value_counts().to_dict(),
            len(outcomes),
            int(outcomes["noshow"].sum()) if len(outcomes) else 0,
        )
    return len(arrivals), len(outcomes)


def dates_back(days: int, end: date | None = None) -> list[str]:
    end = end or local_date(now_epoch())
    return [(end - timedelta(days=k)).strftime("%Y%m%d") for k in range(days, -1, -1)]


def run(argv: list[str] | None = None) -> int:
    """CLI: bustrust build-labels [--date YYYYMMDD | --days N]"""
    p = argparse.ArgumentParser(prog="bustrust build-labels")
    p.add_argument("--date")
    p.add_argument("--days", type=int, default=1, help="rebuild the last N days plus today")
    args = p.parse_args(argv)
    engine = db.init_db()
    for d in [args.date] if args.date else dates_back(args.days):
        write_day(engine, d)
    return 0
