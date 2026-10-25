"""A realistic stand-in for the NTA realtime feeds, built from the real static schedule.

It exists so the whole system runs, trains and demos before an NTA key arrives, and so
tests have ground truth. For each service day it draws a hidden "true world":

* each trip starts late by a random amount, more at peak, and inherits lateness from the
  previous trip on the same block (bus);
* each segment runs slower than scheduled by a congestion factor that depends on hour,
  route, day (some days are rainy) and noise, so lateness grows along the trip at peak;
* a few percent of trips are ghosts: they never run. Some are cancelled in the feed, most
  are not, so they count down and never arrive (the NTA's own definition).

The "official" feed behaves like a typical AVL system: it reports the delay observed at
the last stop passed (a minute or two stale) and propagates it unchanged downstream. That
is exactly the gap the arrival-range model is meant to close.

Everything is a deterministic function of (seed, service date, trip), so repeated polls
see one consistent world.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from datetime import timedelta

import numpy as np
import pandas as pd
from google.transit import gtfs_realtime_pb2 as rt
from sqlalchemy import Engine

from common import geo
from common.schedule import Schedule, latest_version
from common.timeutil import local_date, to_local

log = logging.getLogger(__name__)

DEFAULT_ROUTES = ["39A", "46A", "145", "16", "C1", "G1", "E1", "15", "13", "7", "N4", "H1"]


def _seed(*parts: object) -> int:
    return int.from_bytes(hashlib.blake2b("|".join(map(str, parts)).encode(), digest_size=8).digest(), "little")


def _peak(hour: np.ndarray) -> np.ndarray:
    """0..1 congestion intensity by local hour (morning and evening peaks)."""
    am = np.exp(-(((hour - 8.25) / 1.1) ** 2))
    pm = np.exp(-(((hour - 17.5) / 1.3) ** 2))
    return np.maximum(am, 0.85 * pm)


@dataclass
class DayWorld:
    service_date: str
    stops: pd.DataFrame  # one row per (trip, stop) with true_at, sched_at, dist_m
    trips: pd.DataFrame  # one row per trip: ghost, cancel_at, has_gps, vehicle_id, start delay
    rainy: bool = False
    by_trip: dict[str, pd.DataFrame] = field(default_factory=dict)


class Simulator:
    def __init__(
        self,
        engine: Engine,
        routes: list[str] | None = None,
        seed: int = 7,
        ghost_rate: float = 0.02,
        gps_missing_rate: float = 0.03,
    ):
        self.engine = engine
        self.route_names = routes or DEFAULT_ROUTES
        self.seed = seed
        self.ghost_rate = ghost_rate
        self.gps_missing_rate = gps_missing_rate
        self._days: dict[str, DayWorld] = {}
        self._schedules: dict[str, Schedule] = {}

    # ---------------------------------------------------------------- world
    def _schedule(self, service_date: str) -> Schedule:
        if service_date not in self._schedules:
            version = latest_version(self.engine)
            sched = Schedule.load(self.engine, version, service_dates=[service_date])
            names = set(sched.routes["route_short_name"])
            wanted = [r for r in self.route_names if r in names]
            if not wanted:  # unknown names: fall back to the busiest routes
                wanted = sched.trips["route_short_name"].value_counts().index[:10].tolist()
            sched.trips = sched.trips[sched.trips["route_short_name"].isin(wanted)].reset_index(drop=True)
            sched.stop_times = sched.stop_times[sched.stop_times["trip_id"].isin(set(sched.trips["trip_id"]))]
            self._schedules[service_date] = sched
            if len(self._schedules) > 3:
                self._schedules.pop(next(iter(self._schedules)))
        return self._schedules[service_date]

    @property
    def route_ids(self) -> set[str]:
        any_sched = next(iter(self._schedules.values()), None)
        return set(any_sched.trips["route_id"]) if any_sched is not None else set()

    def day(self, service_date: str) -> DayWorld:
        if service_date in self._days:
            return self._days[service_date]
        sched = self._schedule(service_date)
        st = sched.scheduled_epochs(service_date).sort_values(["trip_id", "stop_sequence"], ignore_index=True)
        rng = np.random.default_rng(_seed(self.seed, service_date))

        trips = st.groupby("trip_id", sort=False).agg(
            route_id=("route_id", "first"),
            route_short_name=("route_short_name", "first"),
            block_id=("block_id", "first"),
            start_at=("scheduled_dep_at", "first"),
            end_at=("scheduled_at", "last"),
        )
        n = len(trips)
        hours = np.array([to_local(int(t)).hour + to_local(int(t)).minute / 60 for t in trips["start_at"]])
        peak = _peak(hours)
        rainy = rng.random() < 0.35  # Dublin
        route_pain = {r: 0.6 + rng.random() * 0.8 for r in trips["route_id"].unique()}
        pain = trips["route_id"].map(route_pain).to_numpy()

        # start delay: mostly small, heavier tail at peak and on painful routes
        start_delay = rng.gamma(1.5, 30 + 70 * peak * pain, n) - 40
        # block carry-over: a late previous trip on the same bus makes this one late
        trips = trips.assign(start_delay=start_delay)
        trips = trips.sort_values(["block_id", "start_at"])
        carry = np.zeros(n)
        prev_end: dict[str, float] = {}
        for i, (blk, s_at, e_at, d) in enumerate(
            zip(trips["block_id"], trips["start_at"], trips["end_at"], trips["start_delay"], strict=True)
        ):
            if blk and blk in prev_end:
                carry[i] = max(0.0, prev_end[blk] - s_at)
            prev_end[blk] = e_at + max(d + carry[i], 0) + 180  # minimum turnaround
        trips["start_delay"] = trips["start_delay"].to_numpy() + np.minimum(carry, 900) * 0.5
        trips = trips.loc[st["trip_id"].unique()]

        ghost_p = self.ghost_rate * (0.6 + pain) * (1 + 0.8 * (hours > 19))
        ghost = rng.random(n) < ghost_p
        cancelled = ghost & (rng.random(n) < 0.3)
        cancel_lead = rng.uniform(60, 1500, n)  # seconds before scheduled start
        trips = trips.assign(
            ghost=ghost,
            cancelled=cancelled,
            cancel_at=np.where(cancelled, trips["start_at"] - cancel_lead, np.nan),
            has_gps=~ghost & (rng.random(n) > self.gps_missing_rate),
            vehicle_id=[f"V{_seed(service_date, b) % 9000 + 1000}" for b in trips["block_id"]],
            pain=pain,
            peak=peak,
        )

        # segment run times: congestion factor x scheduled time, plus dwell noise
        sched_run = st.groupby("trip_id")["scheduled_at"].diff().fillna(0).to_numpy()
        trip_idx = st["trip_id"].map({t: i for i, t in enumerate(trips.index)}).to_numpy()
        seg_hour = np.array([to_local(int(t)).hour for t in st["scheduled_at"]]) + 0.5
        cong = 1 + 0.22 * _peak(seg_hour) * pain[trip_idx] + (0.08 if rainy else 0)
        noise = rng.normal(0, 18, len(st)) * np.sqrt(np.maximum(sched_run, 1) / 90)
        extra = sched_run * (cong - 1) + noise
        extra[sched_run == 0] = 0
        # buses rarely run early past timing points: clamp cumulative early running
        delay = trips["start_delay"].to_numpy()[trip_idx] + pd.Series(extra).groupby(trip_idx).cumsum().to_numpy()
        delay = np.maximum(delay, -90)
        true_at = st["scheduled_at"].to_numpy() + delay
        # arrivals must be non-decreasing along a trip
        true_at = pd.Series(true_at).groupby(trip_idx).cummax().to_numpy()
        stops = st.assign(true_at=true_at.round(), true_delay=(true_at - st["scheduled_at"]).round())
        world = DayWorld(service_date, stops, trips, rainy=rainy)
        world.by_trip = {t: g for t, g in stops.groupby("trip_id", sort=False)}
        self._days[service_date] = world
        if len(self._days) > 3:
            self._days.pop(next(iter(self._days)))
        log.info(
            "sim day %s: %d trips, %d ghosts, rainy=%s",
            service_date,
            n,
            int(ghost.sum()),
            rainy,
        )
        return world

    def _worlds_at(self, t: int) -> list[DayWorld]:
        d = local_date(t)
        return [self.day((d - timedelta(days=k)).strftime("%Y%m%d")) for k in (1, 0)]

    # ---------------------------------------------------------------- truth
    def truth(self, service_date: str) -> tuple[pd.DataFrame, pd.DataFrame]:
        """(per-stop true arrivals, per-trip ghost flags), for tests and label checks."""
        w = self.day(service_date)
        return w.stops, w.trips

    # ---------------------------------------------------------------- feeds
    def _header(self, t: int) -> rt.FeedMessage:
        msg = rt.FeedMessage()
        msg.header.gtfs_realtime_version = "2.0"
        msg.header.incrementality = rt.FeedHeader.FULL_DATASET
        msg.header.timestamp = t
        return msg

    def _active(self, w: DayWorld, t: int, lookahead: int = 5400) -> pd.DataFrame:
        tr = w.trips
        return tr[(tr["start_at"] - lookahead <= t) & (tr["end_at"] + tr["start_delay"].clip(lower=0) + 1800 >= t)]

    def trip_updates(self, t: int) -> bytes:
        msg = self._header(t)
        for w in self._worlds_at(t):
            for trip_id, tr in self._active(w, t).iterrows():
                g = w.by_trip[trip_id]
                if not tr.ghost:
                    passed = g[g["true_at"] <= t - 60]  # AVL latency: a minute stale
                    if len(passed) == len(g):
                        continue  # trip finished
                    if len(passed) == 0 and tr.start_at - t > 1800:
                        continue  # not yet on the board
                ent = msg.entity.add()
                ent.id = f"{w.service_date}-{trip_id}"
                tu = ent.trip_update
                tu.trip.trip_id = trip_id
                tu.trip.route_id = tr.route_id
                tu.trip.start_date = w.service_date
                tu.timestamp = t
                if tr.cancelled and t >= tr.cancel_at:
                    tu.trip.schedule_relationship = rt.TripDescriptor.CANCELED
                    continue
                if tr.ghost:
                    if t > tr.end_at:
                        msg.entity.pop()
                        continue
                    # ghosts count down on schedule until their time passes
                    nxt = g[g["scheduled_at"] >= t - 120]
                    if len(nxt) == 0:
                        msg.entity.pop()
                        continue
                    row, delay = nxt.iloc[0], 0
                else:
                    tu.vehicle.id = tr.vehicle_id
                    if len(passed):
                        delay = int(passed["true_delay"].iloc[-1])
                        row = g.iloc[len(passed)]
                    else:
                        # before departure: the start delay becomes visible ~10 min ahead
                        visible = max(0.0, min(1.0, (600 - (tr.start_at - t)) / 600))
                        delay = int(tr.start_delay * visible)
                        row = g.iloc[0]
                stu = tu.stop_time_update.add()
                stu.stop_sequence = int(row.stop_sequence)
                stu.stop_id = row.stop_id
                stu.arrival.delay = delay
                stu.departure.delay = delay
        return msg.SerializeToString()

    def vehicles(self, t: int) -> bytes:
        msg = self._header(t)
        rng = np.random.default_rng(_seed(self.seed, "vp", t))
        for w in self._worlds_at(t):
            for trip_id, tr in self._active(w, t, lookahead=0).iterrows():
                if tr.ghost or not tr.has_gps:
                    continue
                g = w.by_trip[trip_id]
                ping = int(t - rng.integers(0, 45))  # pings arrive up to 45 s stale
                ta, dist = g["true_at"].to_numpy(), g["dist_m"].to_numpy()
                if ping < ta[0] - 300 or ping > ta[-1] or np.isnan(dist).any():
                    continue
                d = float(np.interp(ping, ta, dist))
                line = self._schedules[w.service_date].line(g["shape_id"].iloc[0])
                if line is None:
                    continue
                lon, lat = geo.point_at(line, d)
                lat += rng.normal(0, 6e-5)  # ~7 m GPS noise
                lon += rng.normal(0, 1e-4)
                ent = msg.entity.add()
                ent.id = f"{w.service_date}-{trip_id}-vp"
                vp = ent.vehicle
                vp.trip.trip_id = trip_id
                vp.trip.route_id = tr.route_id
                vp.trip.start_date = w.service_date
                vp.vehicle.id = tr.vehicle_id
                vp.position.latitude = lat
                vp.position.longitude = lon
                vp.timestamp = ping
        return msg.SerializeToString()

    def feed(self, name: str, t: int) -> bytes:
        return self.trip_updates(t) if name == "trip_updates" else self.vehicles(t)


def sim_routes_from_env() -> list[str] | None:
    import os

    raw = os.environ.get("BT_SIM_ROUTES", "")
    return [r.strip() for r in raw.split(",") if r.strip()] or None


def make_simulator(engine: Engine) -> Simulator:
    return Simulator(engine, routes=sim_routes_from_env())


def run_backfill(argv: list[str] | None = None) -> int:
    """CLI: bustrust simulate-backfill --days 14 [--end YYYY-MM-DDTHH:MM]

    Replays simulated polls minute by minute through the real poller (parse and store),
    skipping the raw archive. Gives the models history before real data exists.
    """
    import argparse
    from datetime import UTC, datetime

    from common import db
    from common.config import get_settings
    from ingest.poller import Poller

    p = argparse.ArgumentParser(prog="bustrust simulate-backfill")
    p.add_argument("--days", type=float, default=14)
    p.add_argument("--end", help="UTC end time (default: now)")
    p.add_argument("--step", type=int, default=60, help="seconds between polls")
    args = p.parse_args(argv)
    engine = db.init_db()
    settings = get_settings().model_copy(update={"feed_mode": "simulated"})
    end = int(datetime.fromisoformat(args.end).replace(tzinfo=UTC).timestamp()) if args.end else int(time.time())
    end -= end % args.step
    start = end - int(args.days * 86400)
    poller = Poller(engine, settings, simulator=make_simulator(engine), archive=False)
    poller.keep_routes = None
    log.info("backfilling %s to %s", to_local(start), to_local(end))
    t, i = start, 0
    while t <= end:
        for feed, key in poller.plan_tick():
            poller.poll_once(feed, key, t=t)
        t += args.step
        i += 1
        if i % 720 == 0:
            log.info("backfill at %s", to_local(t))
    return 0
