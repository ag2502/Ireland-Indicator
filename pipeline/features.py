"""Features for both models, built the same way for training and for live scoring.

Everything is as of the prediction moment T (a trip-updates poll time):
  * a GPS ping is usable from the poll that received it (`polled_at`), not its ping time;
  * a stop passage is usable once the ping after it has been received;
  * segment run times feed the network feature only from completed 5-minute bins before T;
  * weather is the last complete hour before T.

Arrival examples are one row per (T, trip, stop) with the official prediction at T; the
label is actual arrival minus official prediction, in seconds. No-show examples are one
row per (T, trip) before or around the trip's scheduled start; the label is `noshow`.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import Engine, text

from common import db
from common.config import get_settings
from common.schedule import Schedule, latest_version
from common.timeutil import service_day_reference
from ingest import calendar_ie
from pipeline.arrivals import interpolate_stops, load_realtime, track
from pipeline.official import propagate

log = logging.getLogger(__name__)

HORIZON_TARGETS_MIN = [2, 5, 10, 15, 20, 30, 45, 60]
NOSHOW_TARGETS_MIN = [60, 45, 30, 20, 10, 5, 0, -5, -15]
BIN_S = 300
NET_WINDOW_BINS = 6  # network feature looks back 30 minutes
SNAPSHOT_MAX_AGE_S = 600  # a bus's last ping older than this is "not visible"

ARRIVAL_FEATURES = [
    "horizon_s", "official_delay_s", "official_from_schedule", "stops_to_go", "sched_remaining_s",
    "has_vehicle", "has_gps", "ping_age_s", "dist_left_m", "pos_delay_s", "pos_minus_official_s",
    "speed_recent_ms", "route_delay_mean_s", "ahead_gap_m", "ahead_pos_delay_s", "block_slack_s",
    "lost_ahead_s", "lost_ahead_n", "route_id", "direction_id", "stop_id", "hour", "weekday",
    "bank_holiday", "school_holiday", "precip_mm", "wind_kmh",
]  # fmt: skip
NOSHOW_FEATURES = [
    "min_to_start", "in_feed", "has_vehicle", "has_gps", "route_unseen_share", "route_unseen_n",
    "block_prev_seen", "block_slack_s", "route_noshow_7d", "route_id", "direction_id", "hour",
    "weekday", "bank_holiday", "school_holiday", "precip_mm",
]  # fmt: skip
CATEGORICAL = ["route_id", "stop_id"]


@dataclass
class DayContext:
    service_date: str
    se: pd.DataFrame  # scheduled stop times with epochs, dist_m, pos (index within trip)
    trips: pd.DataFrame  # one row per trip: route, direction, shape, block, start/end, prev_trip
    tu: pd.DataFrame  # raw trip_updates rows
    official: pd.DataFrame  # propagated official predictions
    tracks: pd.DataFrame  # trip_id, ping_at, polled_at, dist, pos_delay, speed_recent_ms
    passages: pd.DataFrame  # trip_id, pos, stop_id, passed_at, known_at
    seg_window: pd.DataFrame  # route_id, direction_id, stop_id, pbin, lost_sum, lost_n (30-min window)
    weather: pd.DataFrame  # hour_at, precip_mm, wind_kmh
    poll_times: np.ndarray  # trip-updates poll times


# ---------------------------------------------------------------- context
def build_context(
    engine: Engine,
    service_date: str,
    since: int | None = None,
    sched: Schedule | None = None,
    vp: pd.DataFrame | None = None,
    tu: pd.DataFrame | None = None,
) -> DayContext:
    ref = service_day_reference(service_date)
    if sched is None:
        sched = Schedule.load(engine, latest_version(engine, at=ref + 12 * 3600), service_dates=[service_date])
    se = sched.scheduled_epochs(service_date).sort_values(["trip_id", "stop_sequence"], ignore_index=True)
    vp = load_realtime(engine, service_date, "vehicle_positions") if vp is None else vp
    tu = load_realtime(engine, service_date, "trip_updates") if tu is None else tu
    if since is not None:
        vp, tu = vp[vp["polled_at"] >= since], tu[tu["polled_at"] >= since - 3600]
    live_routes = set(vp["route_id"].dropna()) | set(tu["route_id"].dropna())
    se = se[se["route_id"].isin(live_routes)].reset_index(drop=True)
    se["pos"] = se.groupby("trip_id").cumcount()
    tu = tu[tu["trip_id"].isin(set(se["trip_id"]))]
    vp = vp[vp["trip_id"].isin(set(se["trip_id"]))]

    trips = se.groupby("trip_id", sort=False).agg(
        route_id=("route_id", "first"),
        direction_id=("direction_id", "first"),
        shape_id=("shape_id", "first"),
        block_id=("block_id", "first"),
        start_at=("scheduled_dep_at", "first"),
        end_at=("scheduled_at", "last"),
    )
    trips = trips.sort_values(["block_id", "start_at"])
    same_block = trips["block_id"].eq(trips["block_id"].shift()) & trips["block_id"].fillna("").ne("")
    trips["prev_trip"] = trips.index.to_series().shift().where(same_block)
    trips = trips.reset_index()

    tracks, passages = _tracks(sched, se, vp)
    official = propagate(tu, se) if len(tu) else propagate(tu.iloc[0:0], se)
    weather = pd.read_sql(
        text("SELECT hour_at, precip_mm, wind_kmh FROM weather_hourly WHERE hour_at BETWEEN :a AND :b"),
        engine,
        params={"a": ref - 7200, "b": ref + 32 * 3600},
    )
    return DayContext(
        service_date=service_date,
        se=se,
        trips=trips,
        tu=tu,
        official=official,
        tracks=tracks,
        passages=passages,
        seg_window=_segment_window(se, passages),
        weather=weather,
        poll_times=np.sort(tu["polled_at"].unique()),
    )


def _tracks(sched: Schedule, se: pd.DataFrame, vp: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    tr_out, pa_out = [], []
    stops_by_trip = {t: g for t, g in se.groupby("trip_id", sort=False)}
    for trip_id, pings in vp.groupby("trip_id"):
        stops = stops_by_trip.get(trip_id)
        if stops is None or stops["dist_m"].isna().any():
            continue
        line = sched.line(stops["shape_id"].iloc[0])
        if line is None:
            continue
        tr = track(pings, line)
        if tr.empty:
            continue
        sd, sa = stops["dist_m"].to_numpy(), stops["scheduled_at"].to_numpy().astype(float)
        d, t, k = tr["dist"].to_numpy(), tr["ping_at"].to_numpy(), tr["polled_at"].to_numpy()
        pos_delay = t - np.interp(d, sd, sa)
        # speed over roughly the last 4 minutes of pings
        j = np.searchsorted(t, t - 240, side="right") - 1
        j = np.clip(j, 0, None)
        speed = np.where(t - t[j] > 30, (d - d[j]) / np.maximum(t - t[j], 1), np.nan)
        tr_out.append(
            pd.DataFrame(
                {
                    "trip_id": trip_id,
                    "ping_at": t,
                    "polled_at": k,
                    "dist": d,
                    "pos_delay": pos_delay,
                    "speed_recent_ms": speed,
                }
            )
        )
        arr, _ = interpolate_stops(tr, sd)
        # a passage is known once the first ping timed at or after it has been received
        # (not the first ping at the stop's distance: a bus waiting at a terminus is there
        # long before it departs)
        idx = np.searchsorted(t, np.nan_to_num(arr, nan=np.inf), side="left")
        known = np.where(idx < len(t), k[np.minimum(idx, len(t) - 1)], np.nan)
        ok = ~np.isnan(arr) & ~np.isnan(known)
        pa_out.append(
            pd.DataFrame(
                {
                    "trip_id": trip_id,
                    "pos": stops["pos"].to_numpy()[ok],
                    "stop_id": stops["stop_id"].to_numpy()[ok],
                    "passed_at": arr[ok],
                    "known_at": known[ok],
                    "scheduled_at": sa[ok],
                }
            )
        )
    tracks = (
        pd.concat(tr_out, ignore_index=True)
        if tr_out
        else pd.DataFrame(columns=["trip_id", "ping_at", "polled_at", "dist", "pos_delay", "speed_recent_ms"])
    )
    passages = (
        pd.concat(pa_out, ignore_index=True)
        if pa_out
        else pd.DataFrame(columns=["trip_id", "pos", "stop_id", "passed_at", "known_at", "scheduled_at"])
    )
    return tracks, passages


def _segment_window(se: pd.DataFrame, passages: pd.DataFrame) -> pd.DataFrame:
    """Per (route, direction, from-stop, 5-min bin): sum and count of time lost on the segment
    starting at that stop, over the 30 minutes of completed bins before the bin."""
    cols = ["route_id", "direction_id", "stop_id", "pbin", "lost_sum", "lost_n"]
    if passages.empty:
        return pd.DataFrame(columns=cols)
    p = passages.sort_values(["trip_id", "pos"])
    g = p.groupby("trip_id")
    p = p.assign(
        next_pos=g["pos"].shift(-1),
        next_passed=g["passed_at"].shift(-1),
        next_sched=g["scheduled_at"].shift(-1),
        next_known=g["known_at"].shift(-1),
    )
    p = p[p["next_pos"] == p["pos"] + 1]
    meta = se.drop_duplicates("trip_id").set_index("trip_id")[["route_id", "direction_id"]]
    p = p.join(meta, on="trip_id")
    p["lost"] = (p["next_passed"] - p["passed_at"]) - (p["next_sched"] - p["scheduled_at"])
    p["obin"] = (p["next_known"] // BIN_S).astype("int64")
    agg = p.groupby(["route_id", "direction_id", "stop_id", "obin"])["lost"].agg(["sum", "count"]).reset_index()
    out = []
    for key, grp in agg.groupby(["route_id", "direction_id", "stop_id"]):
        s = grp.set_index("obin")[["sum", "count"]]
        full = np.arange(s.index.min(), s.index.max() + NET_WINDOW_BINS + 1)
        s = s.reindex(full, fill_value=0)
        # window for prediction bin b: observation bins b-6 .. b-1
        roll = s.rolling(NET_WINDOW_BINS, min_periods=1).sum().shift(1).dropna()
        roll = roll[roll["count"] > 0]
        out.append(
            pd.DataFrame(
                {
                    "route_id": key[0],
                    "direction_id": key[1],
                    "stop_id": key[2],
                    "pbin": roll.index,
                    "lost_sum": roll["sum"].to_numpy(),
                    "lost_n": roll["count"].to_numpy(),
                }
            )
        )
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=cols)


# ---------------------------------------------------------------- shared lookups
def snapshot(ctx: DayContext, times: np.ndarray) -> pd.DataFrame:
    """Latest visible GPS state of every trip at each time T, plus the bus ahead on the same
    shape and the route's mean lateness. Columns: T, trip_id, dist, pos_delay, ping_at, ..."""
    if ctx.tracks.empty or len(times) == 0:
        return pd.DataFrame(columns=["T", "trip_id"])
    tr = ctx.tracks.sort_values("polled_at")
    span = tr.groupby("trip_id")["polled_at"].agg(["min", "max"])
    times = np.sort(np.unique(times))
    left = []
    for trip_id, (lo, hi) in span.iterrows():
        a, b = np.searchsorted(times, lo), np.searchsorted(times, hi + SNAPSHOT_MAX_AGE_S, side="right")
        if b > a:
            left.append(pd.DataFrame({"T": times[a:b], "trip_id": trip_id}))
    if not left:
        return pd.DataFrame(columns=["T", "trip_id"])
    left_df = pd.concat(left, ignore_index=True).sort_values("T")
    left_df["T"] = left_df["T"].astype("float64")
    snap = pd.merge_asof(
        left_df,
        tr.assign(polled_at=tr["polled_at"].astype("float64")),
        left_on="T",
        right_on="polled_at",
        by="trip_id",
        direction="backward",
        tolerance=SNAPSHOT_MAX_AGE_S,
    ).dropna(subset=["dist"])
    snap["T"] = snap["T"].astype("int64")
    meta = ctx.trips.set_index("trip_id")[["route_id", "direction_id", "shape_id", "end_at"]]
    snap = snap.join(meta, on="trip_id")
    snap = snap.sort_values(["T", "shape_id", "dist"])
    g = snap.groupby(["T", "shape_id"])
    snap["ahead_gap_m"] = g["dist"].shift(-1) - snap["dist"]
    snap["ahead_pos_delay_s"] = g["pos_delay"].shift(-1)
    rg = snap.groupby(["T", "route_id", "direction_id"])["pos_delay"]
    tot, cnt = rg.transform("sum"), rg.transform("count")
    snap["route_delay_mean_s"] = ((tot - snap["pos_delay"]) / (cnt - 1)).where(cnt > 1)
    return snap.reset_index(drop=True)


def _context_cols(df: pd.DataFrame, ctx: DayContext, tcol: str = "T") -> pd.DataFrame:
    local = pd.to_datetime(df[tcol], unit="s", utc=True).dt.tz_convert("Europe/Dublin")
    df = df.assign(hour=local.dt.hour, weekday=local.dt.weekday)
    flags = calendar_ie.flags(local.dt.date)
    df = df.assign(bank_holiday=flags["bank_holiday"].to_numpy(), school_holiday=flags["school_holiday"].to_numpy())
    hour_at = (df[tcol] // 3600) * 3600 - 3600  # last complete hour
    w = ctx.weather.set_index("hour_at")
    df["precip_mm"] = hour_at.map(w["precip_mm"]) if len(w) else np.nan
    df["wind_kmh"] = hour_at.map(w["wind_kmh"]) if len(w) else np.nan
    return df


def _block_slack(ctx: DayContext, snap: pd.DataFrame, df: pd.DataFrame) -> pd.Series:
    """Scheduled start of this trip minus when the previous trip on the same bus is expected
    to finish (its scheduled end plus its current lateness). Negative means it will start late."""
    prev = ctx.trips.set_index("trip_id")[["prev_trip", "start_at"]]
    d = df[["T", "trip_id"]].join(prev, on="trip_id")
    if snap.empty:
        return pd.Series(np.nan, index=df.index)
    ps = snap[["T", "trip_id", "pos_delay", "end_at"]].rename(columns={"trip_id": "prev_trip"})
    d = d.merge(ps, on=["T", "prev_trip"], how="left")
    return pd.Series((d["start_at"] - (d["end_at"] + d["pos_delay"])).to_numpy(), index=df.index)


# ---------------------------------------------------------------- arrival examples
def arrival_rows(ctx: DayContext, rows: pd.DataFrame) -> pd.DataFrame:
    """Add features to rows with columns T, trip_id, pos, stop_sequence, stop_id, scheduled_at,
    official_at, official_delay (NaN when from schedule), upd_seq, vehicle_id."""
    if rows.empty:
        return rows.assign(**{c: [] for c in ARRIVAL_FEATURES if c not in rows})
    df = rows.reset_index(drop=True).copy()
    meta = ctx.trips.set_index("trip_id")[["route_id", "direction_id", "start_at"]]
    df = df.join(meta, on="trip_id", rsuffix="_trip")
    df["horizon_s"] = df["official_at"] - df["T"]
    df["official_delay_s"] = df["official_delay"]
    df["official_from_schedule"] = df["official_delay"].isna().astype(int)
    df["has_vehicle"] = df["vehicle_id"].notna().astype(int)

    snap = snapshot(ctx, df["T"].unique())
    own = snap[["T", "trip_id", "ping_at", "dist", "pos_delay", "speed_recent_ms", "ahead_gap_m",
                "ahead_pos_delay_s", "route_delay_mean_s"]] if len(snap) else None  # fmt: skip
    if own is not None:
        df = df.merge(own, on=["T", "trip_id"], how="left")
    else:
        for c in ["ping_at", "dist", "pos_delay", "speed_recent_ms", "ahead_gap_m", "ahead_pos_delay_s",
                  "route_delay_mean_s"]:  # fmt: skip
            df[c] = np.nan
    stop_dist = ctx.se.set_index(["trip_id", "pos"])["dist_m"]
    df["has_gps"] = df["dist"].notna().astype(int)
    df["ping_age_s"] = df["T"] - df["ping_at"]
    df["dist_left_m"] = (
        pd.Series(list(zip(df["trip_id"], df["pos"], strict=True))).map(stop_dist).to_numpy() - df["dist"]
    )
    df["pos_delay_s"] = df["pos_delay"]
    df["pos_minus_official_s"] = df["pos_delay"] - df["official_delay"]
    # scheduled time still to run: from the bus's position (or the trip start) to the stop
    sched_at_pos = df["scheduled_at"] - df["dist_left_m"].clip(lower=0) / _sched_speed(ctx, df)
    df["sched_remaining_s"] = np.where(
        df["has_gps"] == 1, df["scheduled_at"] - sched_at_pos, df["scheduled_at"] - df["start_at"]
    )
    df["block_slack_s"] = _block_slack(ctx, snap, df).to_numpy()

    # current position index along the trip: last passage known at T, else update position
    k = _progress_pos(ctx, df)
    df["stops_to_go"] = df["pos"] - k
    df[["lost_ahead_s", "lost_ahead_n"]] = _lost_ahead(ctx, df, k)
    df = _context_cols(df, ctx)
    return df


def _sched_speed(ctx: DayContext, df: pd.DataFrame) -> pd.Series:
    """Scheduled average speed of each trip (m/s), to turn distance left into scheduled time."""
    g = ctx.se.groupby("trip_id")
    speed = (g["dist_m"].max() - g["dist_m"].min()) / (g["scheduled_at"].max() - g["scheduled_at"].min()).clip(lower=60)
    return df["trip_id"].map(speed).clip(lower=1.0).fillna(5.0)


def _progress_pos(ctx: DayContext, df: pd.DataFrame) -> pd.Series:
    out = pd.Series(np.nan, index=df.index)
    if len(ctx.passages):
        pa = ctx.passages.sort_values("known_at")[["trip_id", "known_at", "pos"]]
        pa = pa.assign(known_at=pa["known_at"].astype("float64"))
        left = df[["T", "trip_id"]].reset_index().sort_values("T")
        left["T"] = left["T"].astype("float64")
        m = pd.merge_asof(left, pa, left_on="T", right_on="known_at", by="trip_id", direction="backward")
        m = m.set_index("index")["pos"]
        out = m.reindex(df.index)
        # merge_asof returns the latest-known passage; positions only increase, so it is the max
    upd_pos = pd.Series(list(zip(df["trip_id"], df["upd_seq"], strict=True))).map(
        ctx.se.set_index(["trip_id", "stop_sequence"])["pos"]
    )
    out = out.fillna(pd.Series(upd_pos.to_numpy(), index=df.index) - 1)
    return out.fillna(0).clip(lower=0)


def _lost_ahead(ctx: DayContext, df: pd.DataFrame, k: pd.Series) -> pd.DataFrame:
    """Sum over segments between the bus and the stop of the mean time other buses on the
    route lost there in the last 30 minutes, and how many observations back it."""
    res = pd.DataFrame({"lost_ahead_s": np.nan, "lost_ahead_n": 0.0}, index=df.index)
    if ctx.seg_window.empty:
        return res
    q = pd.DataFrame({"trip_id": df["trip_id"], "pbin": (df["T"] // BIN_S).astype("int64"),
                      "k": k.astype(int), "s": df["pos"].astype(int)})  # fmt: skip
    keys = q[["trip_id", "pbin"]].drop_duplicates()
    stops = ctx.se[["trip_id", "pos", "stop_id", "route_id", "direction_id"]]
    exp = keys.merge(stops, on="trip_id")
    exp = exp.merge(ctx.seg_window, on=["route_id", "direction_id", "stop_id", "pbin"], how="left")
    exp["mean"] = (exp["lost_sum"] / exp["lost_n"]).fillna(0)
    exp["lost_n"] = exp["lost_n"].fillna(0)
    exp = exp.sort_values(["trip_id", "pbin", "pos"])
    g = exp.groupby(["trip_id", "pbin"])
    exp["c_mean"] = g["mean"].cumsum()
    exp["c_n"] = g["lost_n"].cumsum()
    c = exp.set_index(["trip_id", "pbin", "pos"])[["c_mean", "c_n"]]
    # segments k .. s-1 => C(s-1) - C(k-1)
    hi = c.reindex(pd.MultiIndex.from_arrays([q["trip_id"], q["pbin"], q["s"] - 1])).fillna(0).to_numpy()
    lo = c.reindex(pd.MultiIndex.from_arrays([q["trip_id"], q["pbin"], q["k"] - 1])).fillna(0).to_numpy()
    diff = np.where((q["s"] > q["k"]).to_numpy()[:, None], hi - lo, 0.0)
    res["lost_ahead_s"] = diff[:, 0]
    res["lost_ahead_n"] = diff[:, 1]
    return res


def _snap_to_polls(times: np.ndarray, polls: np.ndarray) -> np.ndarray:
    """Latest poll at or before each time (NaN if none)."""
    idx = np.searchsorted(polls, times, side="right") - 1
    return np.where(idx >= 0, polls[np.clip(idx, 0, None)], np.nan)


def arrival_examples(
    ctx: DayContext, arrivals: pd.DataFrame, methods: tuple[str, ...] = ("gps_interp",)
) -> pd.DataFrame:
    """Training rows: for each labelled (trip, stop), the polls whose official horizon is
    nearest each target horizon, plus schedule-only rows from before the trip was in the feed."""
    lab = arrivals[arrivals["method"].isin(methods)][["trip_id", "stop_sequence", "actual_at"]]
    if lab.empty or not len(ctx.poll_times):
        return pd.DataFrame()
    off = ctx.official.merge(lab, on=["trip_id", "stop_sequence"])
    off = off.assign(horizon=off["official_at"] - off["polled_at"])
    off = off[(off["horizon"] >= 0) & (off["horizon"] <= 3700)]
    picks = []
    for target in HORIZON_TARGETS_MIN:
        diff = (off["horizon"] - target * 60).abs()
        sel = off.assign(diff=diff)[diff <= 90]
        picks.append(sel.loc[sel.groupby(["trip_id", "stop_sequence"])["diff"].idxmin()])
    rows = pd.concat(picks).drop_duplicates(["polled_at", "trip_id", "stop_sequence"])
    rows = rows.rename(columns={"polled_at": "T"})

    # schedule-only rows: times before the trip first appeared in the trip-updates feed
    first_tu = ctx.tu.groupby("trip_id")["polled_at"].min()
    sl = ctx.se.merge(lab, on=["trip_id", "stop_sequence"])
    sched_rows = []
    for target in HORIZON_TARGETS_MIN:
        T = _snap_to_polls((sl["scheduled_at"] - target * 60).to_numpy(), ctx.poll_times)
        cand = sl.assign(T=T)
        cand = cand[cand["T"].notna() & (cand["T"] < cand["trip_id"].map(first_tu).fillna(np.inf))]
        sched_rows.append(cand)
    srows = pd.concat(sched_rows).drop_duplicates(["T", "trip_id", "stop_sequence"])
    srows = srows.assign(official_at=srows["scheduled_at"], official_delay=np.nan, upd_seq=np.nan, vehicle_id=None)

    keep = ["T", "trip_id", "stop_sequence", "stop_id", "scheduled_at", "official_at", "official_delay", "upd_seq",
            "vehicle_id", "actual_at"]  # fmt: skip
    rows = pd.concat([rows[keep], srows[keep]], ignore_index=True)
    rows["T"] = rows["T"].astype("int64")
    rows = rows.merge(ctx.se[["trip_id", "stop_sequence", "pos"]], on=["trip_id", "stop_sequence"])
    feats = arrival_rows(ctx, rows)
    feats["y"] = feats["actual_at"] - feats["official_at"]
    feats["service_date"] = ctx.service_date
    return feats


# ---------------------------------------------------------------- no-show examples
def noshow_rows(ctx: DayContext, rows: pd.DataFrame, route_noshow_7d: dict[str, float] | None = None) -> pd.DataFrame:
    """Add features to rows with columns T, trip_id."""
    if rows.empty:
        return rows
    df = rows.reset_index(drop=True).copy()
    meta = ctx.trips.set_index("trip_id")[["route_id", "direction_id", "start_at", "prev_trip"]]
    df = df.join(meta, on="trip_id")
    df["min_to_start"] = (df["start_at"] - df["T"]) / 60

    tu = ctx.tu.sort_values("polled_at")
    seen = tu.groupby("trip_id")["polled_at"].min()
    veh = tu[tu["vehicle_id"].notna()].groupby("trip_id")["polled_at"].min()
    gps = ctx.tracks.groupby("trip_id")["polled_at"].min() if len(ctx.tracks) else pd.Series(dtype=float)
    df["in_feed"] = (df["trip_id"].map(seen) <= df["T"]).astype(int)
    df["has_vehicle"] = (df["trip_id"].map(veh) <= df["T"]).astype(int)
    df["has_gps"] = (df["trip_id"].map(gps) <= df["T"]).astype(int)
    prev_gps = df["prev_trip"].map(gps)
    df["block_prev_seen"] = np.where(df["prev_trip"].isna(), np.nan, (prev_gps <= df["T"]).astype(float))

    # among this route-direction's trips due to start 5-60 min before T, how many have no GPS yet
    tr = ctx.trips[["trip_id", "route_id", "direction_id", "start_at"]].assign(
        first_gps=lambda x: x["trip_id"].map(gps)
    )
    shares, counts = [], []
    by_rd = {k: g for k, g in tr.groupby(["route_id", "direction_id"])}
    for t, r, d in zip(df["T"], df["route_id"], df["direction_id"], strict=True):
        g = by_rd.get((r, d))
        if g is None:
            shares.append(np.nan)
            counts.append(0)
            continue
        due = g[(g["start_at"] <= t - 300) & (g["start_at"] >= t - 3600)]
        counts.append(len(due))
        shares.append(float((~(due["first_gps"] <= t)).mean()) if len(due) else np.nan)
    df["route_unseen_share"] = shares
    df["route_unseen_n"] = counts

    snap = snapshot(ctx, df["T"].unique())
    df["block_slack_s"] = _block_slack(ctx, snap, df).to_numpy()
    df["route_noshow_7d"] = df["route_id"].map(route_noshow_7d or {})
    return _context_cols(df, ctx)


def noshow_examples(ctx: DayContext, outcomes: pd.DataFrame, route_noshow_7d: dict[str, float]) -> pd.DataFrame:
    lab = outcomes[["trip_id", "noshow"]]
    trips = ctx.trips.merge(lab, on="trip_id")
    if trips.empty or not len(ctx.poll_times):
        return pd.DataFrame()
    cancelled = ctx.tu[ctx.tu["trip_rel"] == "CANCELED"].groupby("trip_id")["polled_at"].min()
    rows = []
    for target in NOSHOW_TARGETS_MIN:
        T = _snap_to_polls((trips["start_at"] - target * 60).to_numpy(), ctx.poll_times)
        rows.append(trips[["trip_id", "noshow"]].assign(T=T))
    r = pd.concat(rows).dropna(subset=["T"]).drop_duplicates(["T", "trip_id"])
    r["T"] = r["T"].astype("int64")
    # a trip already cancelled in the feed is handled by rule (risk = 1), not by the model
    r = r[~(r["trip_id"].map(cancelled) <= r["T"])]
    feats = noshow_rows(ctx, r, route_noshow_7d)
    feats["y"] = feats["noshow"].astype(int)
    feats["service_date"] = ctx.service_date
    return feats


def route_noshow_rates(engine: Engine, before: str, days: int = 7) -> dict[str, float]:
    """No-show share per route over the `days` service dates before `before` (exclusive)."""
    start = (pd.Timestamp(before) - pd.Timedelta(days=days)).strftime("%Y%m%d")
    o = pd.read_sql(
        text(
            "SELECT route_id, AVG(CASE WHEN noshow THEN 1.0 ELSE 0.0 END) AS r FROM trip_outcomes "
            "WHERE service_date >= :a AND service_date < :b GROUP BY route_id"
        ),
        engine,
        params={"a": start, "b": before},
    )
    return dict(zip(o["route_id"], o["r"], strict=True))


# ---------------------------------------------------------------- CLI
def build_day(engine: Engine, service_date: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    p = {"d": service_date}
    arrivals = pd.read_sql(text("SELECT * FROM arrivals WHERE service_date = :d"), engine, params=p)
    outcomes = pd.read_sql(text("SELECT * FROM trip_outcomes WHERE service_date = :d"), engine, params=p)
    if arrivals.empty and outcomes.empty:
        return pd.DataFrame(), pd.DataFrame()
    ctx = build_context(engine, service_date)
    outcomes["noshow"] = outcomes["noshow"].astype(bool)
    arr = arrival_examples(ctx, arrivals)
    ns = noshow_examples(ctx, outcomes, route_noshow_rates(engine, service_date))
    return arr, ns


def write_parquet(df: pd.DataFrame, kind: str, service_date: str) -> Path | None:
    if df.empty:
        return None
    out = Path(get_settings().parquet_dir) / kind / f"{service_date}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    return out


def run(argv: list[str] | None = None) -> int:
    """CLI: bustrust features [--date YYYYMMDD | --days N]"""
    from pipeline.arrivals import dates_back

    p = argparse.ArgumentParser(prog="bustrust features")
    p.add_argument("--date")
    p.add_argument("--days", type=int, default=1)
    args = p.parse_args(argv)
    engine = db.get_engine()
    for d in [args.date] if args.date else dates_back(args.days):
        arr, ns = build_day(engine, d)
        write_parquet(arr, "arrival", d)
        write_parquet(ns, "noshow", d)
        log.info("%s: %d arrival examples, %d no-show examples", d, len(arr), len(ns))
    return 0
