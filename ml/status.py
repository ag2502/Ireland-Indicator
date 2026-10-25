"""Live status of every bus: what a rider needs to know besides the time.

Per (trip, stop) on the board: on_time | late | early | scheduled (timetable only, the bus
is not live yet) | cancelled | skipped (diversion: the bus will not stop here) |
departed (already passed this stop, shown for 15 minutes).

Per trip: not_started | running | diverted | cancelled | finished, with where the bus is
(last GPS fix, last stop passed, next stop), the stops it has passed and when, the stops
it will skip, and whether its GPS has left the route line.

GPS beats the feed: once our own track says the bus passed a stop, the stop is departed
even if the official feed (a minute or two stale) still counts down to it.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from common import geo
from common.schedule import Schedule
from pipeline import features as F

LATE_S = 120  # later than this is "late" (the on-time window is -1 / +5 min for scoring;
EARLY_S = -60  # riders care sooner, so the badge turns at 2 minutes)
FRESH_PING_S = 300
DEPARTED_SHOW_S = 900
OFF_ROUTE_M = 150


def known_passages(ctx: F.DayContext, T: int) -> pd.DataFrame:
    """Stops each bus is known (at poll T) to have passed, with stop_sequence."""
    pa = ctx.passages
    if pa.empty:
        return pd.DataFrame(columns=["trip_id", "pos", "stop_id", "passed_at", "stop_sequence"])
    pa = pa[pa["known_at"] <= T]
    return pa.merge(ctx.se[["trip_id", "pos", "stop_sequence"]], on=["trip_id", "pos"])[
        ["trip_id", "pos", "stop_id", "passed_at", "stop_sequence"]
    ]


def latest_pings(vp: pd.DataFrame, now: int, n: int = 2) -> pd.DataFrame:
    """The last `n` fresh GPS fixes of each trip (newest first within a trip). Fresh is by
    the fix's own time: the feed keeps serving a bus's last fix after its GPS goes quiet."""
    v = vp[(vp["polled_at"] <= now) & (vp["ping_at"] >= now - FRESH_PING_S) & vp["lat"].notna()]
    v = v.sort_values(["trip_id", "ping_at"], ascending=[True, False]).drop_duplicates(["trip_id", "ping_at"])
    return v.groupby("trip_id").head(n)


def off_route(sched: Schedule, trips: pd.DataFrame, pings: pd.DataFrame) -> set[str]:
    """Trips whose last two fixes are both far from their route line (a diversion)."""
    shapes = trips.set_index("trip_id")["shape_id"]
    out = set()
    for trip_id, g in pings.groupby("trip_id"):
        if len(g) < 2 or trip_id not in shapes.index:
            continue
        line = sched.line(shapes[trip_id])
        if line is None:
            continue
        offsets = [geo.project_point(line, lon, lat)[1] for lon, lat in zip(g["lon"], g["lat"], strict=True)]
        if min(offsets) > OFF_ROUTE_M:
            out.add(trip_id)
    return out


def feed_flags(ctx: F.DayContext, T: int) -> tuple[set[str], pd.DataFrame]:
    """(trips cancelled at poll T, (trip, stop_sequence) marked SKIPPED at poll T)."""
    tu = ctx.tu[ctx.tu["polled_at"] == T]
    cancelled = set(tu.loc[tu["trip_rel"] == "CANCELED", "trip_id"])
    skipped = tu.loc[tu["stop_rel"] == "SKIPPED", ["trip_id", "stop_sequence"]].dropna().drop_duplicates()
    return cancelled, skipped.astype({"stop_sequence": "int64"})


def feed_progress(ctx: F.DayContext, T: int, cancelled: set[str]) -> pd.Series:
    """trip_id -> pos of the first stop the feed still predicts at poll T, for trips the
    feed shows past their first stop. This is how a bus without GPS is known to be moving
    (a ghost bus stays pinned to its first stop)."""
    off = ctx.official[(ctx.official["polled_at"] == T) & ~ctx.official["trip_id"].isin(cancelled)]
    off = off.merge(ctx.se[["trip_id", "stop_sequence", "pos"]], on=["trip_id", "stop_sequence"])
    first = off.groupby("trip_id")["pos"].min()
    return first[first > 0]


def upcoming_status(feats: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """(status, delay_s) for rows still to come. Delay is from our GPS track when fresh,
    else what the feed says; timetable-only rows have no delay."""
    fresh = (feats["has_gps"] == 1) & (feats["ping_age_s"] <= FRESH_PING_S)
    delay = pd.Series(np.where(fresh, feats["pos_delay_s"], feats["official_delay_s"]), index=feats.index)
    status = pd.Series("on_time", index=feats.index, dtype=object)
    status[delay >= LATE_S] = "late"
    status[delay <= EARLY_S] = "early"
    status[(feats["official_from_schedule"] == 1) & ~fresh] = "scheduled"
    return status, delay.round()


def trip_summaries(
    ctx: F.DayContext,
    board: pd.DataFrame,
    passed: pd.DataFrame,
    pings: pd.DataFrame,
    cancelled: set[str],
    skipped: pd.DataFrame,
    diverted: set[str],
    noshow: pd.Series,
    moving: pd.Series,
    now: int,
) -> pd.DataFrame:
    """One row per trip on the board or sending GPS. A running trip whose GPS has gone
    quiet keeps its last passed stop as its position; once it is well past its scheduled
    end with no fresh fix it is taken to have finished."""
    ids = sorted(set(board["trip_id"]) | set(pings["trip_id"]) | cancelled)
    trips = ctx.trips.set_index("trip_id")
    meta = ctx.se.drop_duplicates("trip_id").set_index("trip_id")
    last_pos = ctx.se.groupby("trip_id")["pos"].max()
    newest = pings.drop_duplicates("trip_id").set_index("trip_id")
    track = ctx.tracks.sort_values("ping_at").drop_duplicates("trip_id", keep="last").set_index("trip_id")
    passed_by = {t: g.sort_values("pos") for t, g in passed.groupby("trip_id")}
    skipped_by = skipped.groupby("trip_id")["stop_sequence"].apply(sorted).to_dict()
    upcoming = board[~board["status"].isin(["departed", "skipped", "cancelled"])]
    nxt = upcoming.sort_values("stop_sequence").drop_duplicates("trip_id").set_index("trip_id")
    stop_at = ctx.se.set_index(["trip_id", "pos"])["stop_id"]
    last_by_feed = {t: stop_at[(t, int(k) - 1)] for t, k in moving.items()}
    seqs_by = {
        t: g.set_index("pos")["stop_sequence"] for t, g in ctx.se[ctx.se["trip_id"].isin(ids)].groupby("trip_id")
    }
    rows = []
    for t in ids:
        if t not in trips.index:
            continue
        p = passed_by.get(t)
        ping = newest.loc[t] if t in newest.index else None
        if t in cancelled:
            status = "cancelled"
        elif p is not None and (
            int(p["pos"].max()) >= int(last_pos.get(t, 10**9))
            or (ping is None and now > trips.loc[t, "end_at"] + DEPARTED_SHOW_S)
        ):
            status = "finished"
        elif t in diverted or t in skipped_by:
            status = "diverted"
        elif ping is not None or p is not None or t in moving.index:
            status = "running"
        else:
            status = "not_started"
        if t in track.index and ping is not None:
            delay = track.loc[t, "pos_delay"]
        elif t in nxt.index:
            delay = nxt.loc[t, "delay_s"]
        else:
            delay = np.nan
        rows.append(
            {
                "service_date": ctx.service_date,
                "trip_id": t,
                "route_id": trips.loc[t, "route_id"],
                "route_short_name": meta.loc[t, "route_short_name"],
                "direction_id": trips.loc[t, "direction_id"],
                "headsign": meta.loc[t, "trip_headsign"],
                "vehicle_id": ping["vehicle_id"] if ping is not None else None,
                "status": status,
                "delay_s": None if pd.isna(delay) else int(round(float(delay))),
                "lat": ping["lat"] if ping is not None else None,
                "lon": ping["lon"] if ping is not None else None,
                "bearing": ping["bearing"] if ping is not None else None,
                "ping_at": ping["ping_at"] if ping is not None else None,
                "last_stop_id": p["stop_id"].iloc[-1] if p is not None else last_by_feed.get(t),
                "next_stop_id": nxt.loc[t, "stop_id"] if t in nxt.index else None,
                "off_route": t in diverted,
                "skipped": json.dumps([int(s) for s in skipped_by.get(t, [])]),
                "passed": json.dumps(_passed(seqs_by.get(t), p, moving.get(t))),
                "start_at": trips.loc[t, "start_at"],
                "end_at": trips.loc[t, "end_at"],
                "noshow_prob": noshow.get(t, np.nan),
            }
        )
    return pd.DataFrame(rows)


def _passed(seqs: pd.Series | None, gps: pd.DataFrame | None, feed_pos: float | None) -> dict[int, int | None]:
    """{stop_sequence: passed_at} for every stop behind the bus. GPS gives the time; stops
    behind it that GPS did not catch (before we saw the bus, or between fixes), or that
    the feed has stopped predicting, are passed at an unknown time (None)."""
    out: dict[int, int | None] = {}
    upto = -1
    if gps is not None:
        out = {int(s): int(a) for s, a in zip(gps["stop_sequence"], gps["passed_at"], strict=True)}
        upto = int(gps["pos"].max())
    if feed_pos is not None:
        upto = max(upto, int(feed_pos) - 1)
    if seqs is not None:
        for pos, seq in seqs.items():
            if int(pos) <= upto:
                out.setdefault(int(seq), None)
    return dict(sorted(out.items()))
