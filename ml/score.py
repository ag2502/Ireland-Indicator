"""Live scoring: once a minute, right after the poller writes, turn the latest official
predictions into the board the API serves.

For every bus due at any stop within the next hour (in the feed, or only in the
timetable) it writes one live_board row: the official time unaltered, our p10/p50/p90,
the no-show risk and the live status (see ml.status). Buses that passed a stop in the
last 15 minutes, cancelled trips and stops a diversion skips get rows too, so a rider
sees why a bus is missing. live_trips holds one row per trip: where it is and what it
is doing. Trips cancelled in the feed get risk 1 by rule; trips already moving on GPS
get 0. Rows whose official horizon is near a target horizon also go to
predictions_log, which is how the accuracy page is graded the next morning.

With no promoted model yet, the board still fills: official and scheduled times only.
"""

from __future__ import annotations

import argparse
import logging
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sqlalchemy import Engine, Table, text

from common import db
from common.config import get_settings
from common.schedule import Schedule, latest_version
from common.timeutil import local_date, now_epoch, service_day_reference, to_local
from ml import status as S
from ml import train
from ml.models import ArrivalModel, NoShowModel
from pipeline import features as F

log = logging.getLogger(__name__)

WINDOW_S = 3600  # realtime history behind each context (tracks, segment window)
LOG_TOLERANCE_S = 30  # a row is logged when its horizon is this close to a target
NOSHOW_FROM_MIN = -15  # trips that started more than this long ago are not scored for no-show
MODEL_REFRESH_S = 600


@dataclass
class Live:
    board: pd.DataFrame  # one row per (trip, stop): upcoming, departed, cancelled or skipped
    trips: pd.DataFrame  # one row per trip: status and where the bus is


@dataclass
class Scorer:
    engine: Engine
    horizon_s: int = 3600
    arrival: ArrivalModel | None = None
    noshow: NoShowModel | None = None
    _schedules: dict[tuple[str, str], Schedule] = field(default_factory=dict)
    _models_checked_at: float = 0.0
    _rates: dict[str, dict[str, float]] = field(default_factory=dict)

    # ------------------------------------------------------------ models
    def refresh_models(self, force: bool = False) -> None:
        if not force and time.monotonic() - self._models_checked_at < MODEL_REFRESH_S:
            return
        self._models_checked_at = time.monotonic()
        try:
            a, n = train.load_current("arrival"), train.load_current("noshow")
        except Exception:  # a broken registry must not stop the board
            log.exception("could not load models; keeping %s / %s", self._v(self.arrival), self._v(self.noshow))
            return
        if self._v(a) != self._v(self.arrival) or self._v(n) != self._v(self.noshow):
            log.info("models: arrival %s, noshow %s", self._v(a), self._v(n))
        self.arrival, self.noshow = a, n  # type: ignore[assignment]

    @staticmethod
    def _v(m: ArrivalModel | NoShowModel | None) -> str | None:
        return m.version if m else None

    def model_version(self) -> str | None:
        parts = [v for v in (self._v(self.arrival), self._v(self.noshow)) if v]
        return "+".join(parts) or None

    # ------------------------------------------------------------ data
    def _schedule(self, service_date: str) -> Schedule:
        ref = service_day_reference(service_date)
        version = latest_version(self.engine, at=ref + 12 * 3600)
        if version is None:
            raise RuntimeError("no static schedule loaded; run `bustrust load-schedule`")
        key = (version, service_date)
        if key not in self._schedules:
            self._schedules = {k: v for k, v in self._schedules.items() if k[1] >= service_date[:6]}
            self._schedules[key] = Schedule.load(self.engine, version, service_dates=[service_date])
        return self._schedules[key]

    def _realtime(self, now: int) -> tuple[pd.DataFrame, pd.DataFrame]:
        p = {"a": now - WINDOW_S - 3600, "b": now}
        vp = pd.read_sql(
            text("SELECT * FROM vehicle_positions WHERE polled_at BETWEEN :a AND :b"), self.engine, params=p
        )
        tu = pd.read_sql(text("SELECT * FROM trip_updates WHERE polled_at BETWEEN :a AND :b"), self.engine, params=p)
        return vp, tu

    @staticmethod
    def service_dates(now: int) -> list[str]:
        today = local_date(now)
        dates = [today.strftime("%Y%m%d")]
        if to_local(now).hour < 5:  # yesterday's 24:xx-29:xx trips are still out
            dates.insert(0, (today - pd.Timedelta(days=1)).strftime("%Y%m%d"))
        return dates

    # ------------------------------------------------------------ scoring
    @staticmethod
    def latest_poll(ctx: F.DayContext, now: int) -> int:
        polls = ctx.poll_times[ctx.poll_times <= now]
        return int(polls[-1]) if len(polls) else now

    def board_rows(self, ctx: F.DayContext, now: int, T: int) -> pd.DataFrame:
        """(trip, stop) pairs due in the next hour as seen at poll T, with features."""
        off = ctx.official[ctx.official["polled_at"] == T].drop(columns="polled_at")
        off = off[(off["official_at"] >= now - 60) & (off["official_at"] <= now + self.horizon_s)]

        # timetable-only: trips not in this poll's updates (not yet started, or no data)
        in_poll = set(ctx.tu.loc[ctx.tu["polled_at"] == T, "trip_id"])
        se = ctx.se[~ctx.se["trip_id"].isin(in_poll)]
        se = se[(se["scheduled_at"] >= now) & (se["scheduled_at"] <= now + self.horizon_s)]
        sched = se[["trip_id", "stop_sequence", "stop_id", "scheduled_at"]].assign(
            official_at=se["scheduled_at"], official_delay=np.nan, upd_seq=np.nan, vehicle_id=None
        )
        rows = pd.concat([off, sched], ignore_index=True).assign(T=T)
        if rows.empty:
            return rows
        rows["T"] = rows["T"].astype("int64")
        rows = rows.merge(ctx.se[["trip_id", "stop_sequence", "pos"]], on=["trip_id", "stop_sequence"])
        return F.arrival_rows(ctx, rows)

    def noshow_probs(self, ctx: F.DayContext, feats: pd.DataFrame, T: int, cancelled: set[str]) -> pd.Series:
        ids = pd.Index(feats["trip_id"].unique()) if len(feats) else pd.Index([], dtype=object)
        prob = pd.Series(np.nan, index=ids.union(pd.Index(sorted(cancelled))), dtype=float)
        trips = pd.DataFrame({"trip_id": ids}).assign(T=T)
        start = trips["trip_id"].map(ctx.trips.set_index("trip_id")["start_at"])
        trips = trips[(start - T) / 60 >= NOSHOW_FROM_MIN]
        if self.noshow is not None and len(trips):
            nf = F.noshow_rows(ctx, trips, self._route_rates(ctx.service_date))
            p = pd.Series(self.noshow.predict(nf), index=nf["trip_id"].to_numpy())
            p[nf["has_gps"].to_numpy() == 1] = 0.0
            prob.loc[p.index] = p
        if len(feats):
            running = feats.loc[feats["has_gps"] == 1, "trip_id"].unique()
            prob.loc[running] = 0.0 if self.noshow is not None else np.nan
        prob.loc[list(cancelled)] = 1.0
        return prob

    def _route_rates(self, service_date: str) -> dict[str, float]:
        if service_date not in self._rates:
            self._rates = {service_date: F.route_noshow_rates(self.engine, service_date)}
        return self._rates[service_date]

    def score(self, now: int | None = None) -> Live:
        now = now or now_epoch()
        self.refresh_models()
        vp, tu = self._realtime(now)
        boards, trips = [], []
        for d in self.service_dates(now):
            dvp = vp[(vp["start_date"] == d) | vp["start_date"].isna()]
            dtu = tu[(tu["start_date"] == d) | tu["start_date"].isna()]
            sched = self._schedule(d)
            ctx = F.build_context(self.engine, d, since=now - WINDOW_S, sched=sched, vp=dvp, tu=dtu)
            board, live = self._score_day(ctx, sched, dvp, now)
            boards.append(board)
            trips.append(live)
        board, live = _stack(boards, db.live_board), _stack(trips, db.live_trips)
        board["made_at"] = now
        board["model_version"] = self.model_version()
        board["has_gps"] = board["has_gps"].astype(bool)
        live["made_at"] = now
        return Live(board, live)

    def _score_day(
        self, ctx: F.DayContext, sched: Schedule, vp: pd.DataFrame, now: int
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        T = self.latest_poll(ctx, now)
        cancelled, skipped = S.feed_flags(ctx, T)
        passed = S.known_passages(ctx, now)
        moving = S.feed_progress(ctx, T, cancelled)
        reached = passed.groupby("trip_id")["pos"].max()
        pings = S.latest_pings(vp[vp["trip_id"].isin(ctx.trips["trip_id"])], now)
        diverted = S.off_route(sched, ctx.trips, pings)

        feats = self.board_rows(ctx, now, T)
        if len(feats):
            # GPS beats the feed: a stop the bus has already passed is not still to come
            gone = feats["pos"] <= feats["trip_id"].map(reached).fillna(-1)
            feats = feats[~gone & ~feats["trip_id"].isin(cancelled)].reset_index(drop=True)
        noshow = self.noshow_probs(ctx, feats, T, cancelled)

        cols = ["trip_id", "stop_sequence", "stop_id", "route_id", "scheduled_at", "official_at", "vehicle_id",
                "has_gps", "horizon_s"]  # fmt: skip
        up = feats.reindex(columns=cols).copy()
        if len(feats):
            if self.arrival is not None:
                pred = self.arrival.predict(feats)
                for q in ("p10", "p50", "p90"):
                    # a bus cannot arrive before now; the range stays ordered after clipping
                    up[f"{q}_at"] = (feats["official_at"] + pred[q]).round().astype("int64").clip(lower=now)
            up["status"], up["delay_s"] = S.upcoming_status(feats)
            up["stops_away"] = feats["stops_to_go"].clip(lower=0)
        trip_route = ctx.trips.set_index("trip_id")["route_id"]
        se = ctx.se[["trip_id", "pos", "stop_sequence", "stop_id", "scheduled_at"]]

        dep = passed[passed["passed_at"] >= now - S.DEPARTED_SHOW_S].merge(
            se[["trip_id", "pos", "scheduled_at"]], on=["trip_id", "pos"]
        )
        dep = dep.assign(
            route_id=dep["trip_id"].map(trip_route),
            status="departed",
            delay_s=(dep["passed_at"] - dep["scheduled_at"]).round(),
            has_gps=True,
        ).drop(columns="pos")
        # buses without GPS: stops the feed has stopped predicting are behind them (when it left
        # is unknown, so these show for 15 minutes after the scheduled time)
        fed = se.merge(moving.rename("first_pos"), left_on="trip_id", right_index=True)
        fed = fed[(fed["pos"] < fed["first_pos"]) & (fed["pos"] > fed["trip_id"].map(reached).fillna(-1))]
        fed = fed[fed["scheduled_at"] >= now - S.DEPARTED_SHOW_S].drop(columns=["pos", "first_pos"])
        fed = fed.assign(route_id=fed["trip_id"].map(trip_route), status="departed", has_gps=False)

        # cancelled stops from 15 minutes back too: the rider still waiting needs to know
        window = (se["scheduled_at"] >= now - S.DEPARTED_SHOW_S) & (se["scheduled_at"] <= now + self.horizon_s)
        can = se[window & se["trip_id"].isin(cancelled)].drop(columns="pos")
        can = can.assign(route_id=can["trip_id"].map(trip_route), status="cancelled", has_gps=False)

        sk = se.merge(skipped, on=["trip_id", "stop_sequence"])
        sk = sk[(sk["pos"] > sk["trip_id"].map(reached).fillna(-1)) & ~sk["trip_id"].isin(cancelled)]
        sk = sk[(sk["scheduled_at"] >= now - 300) & (sk["scheduled_at"] <= now + self.horizon_s)].drop(columns="pos")
        sk = sk.assign(route_id=sk["trip_id"].map(trip_route), status="skipped", has_gps=False)

        parts = [x for x in (up, dep, fed, can, sk) if len(x)]
        board = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
        board = board.reindex(columns=list(dict.fromkeys([*cols, "status", "delay_s", *board.columns])))
        if board.empty:
            board = board.astype(object)  # reindexed empty columns default to float; trip_id must join as str
        board["service_date"] = ctx.service_date
        board["noshow_prob"] = board["trip_id"].map(noshow)
        meta = ctx.se.drop_duplicates("trip_id").set_index("trip_id")[["route_short_name", "trip_headsign"]]
        board = board.join(meta, on="trip_id").rename(columns={"trip_headsign": "headsign"})
        # where the bus is, on every row of its trip
        newest = pings.drop_duplicates("trip_id").set_index("trip_id")
        board["vehicle_lat"] = board["trip_id"].map(newest["lat"])
        board["vehicle_lon"] = board["trip_id"].map(newest["lon"])
        board["ping_at"] = board["trip_id"].map(newest["ping_at"])
        board["vehicle_id"] = board["vehicle_id"].fillna(board["trip_id"].map(newest["vehicle_id"]))
        board["off_route"] = board["trip_id"].isin(diverted)

        live = S.trip_summaries(ctx, board, passed, pings, cancelled, skipped, diverted, noshow, moving, now)
        board["last_stop_id"] = board["trip_id"].map(live.set_index("trip_id")["last_stop_id"]) if len(live) else None
        return board, live

    def write(self, live: Live) -> None:
        board = live.board
        db.replace_table(
            self.engine, db.live_board, _records(board.reindex(columns=[c.name for c in db.live_board.columns]))
        )
        db.replace_table(
            self.engine, db.live_trips, _records(live.trips.reindex(columns=[c.name for c in db.live_trips.columns]))
        )
        if board.empty or self.arrival is None:
            return
        board = board[board["p50_at"].notna()]
        h = board["horizon_s"].to_numpy(dtype=float)
        near = np.zeros(len(board), dtype=bool)
        for target in F.HORIZON_TARGETS_MIN:
            near |= np.abs(h - target * 60) <= LOG_TOLERANCE_S
        logged = board[near].reindex(columns=[c.name for c in db.predictions_log.columns])
        db.insert_rows(self.engine, db.predictions_log, _records(logged))


def _stack(frames: list[pd.DataFrame], table: Table) -> pd.DataFrame:
    """Concatenate per-day frames, with every column of the table present (extras kept)."""
    cols = [c.name for c in table.columns]
    frames = [f for f in frames if len(f)]
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=cols)
    return df.reindex(columns=[*cols, *(c for c in df.columns if c not in cols)])


def _records(df: pd.DataFrame) -> list[dict]:
    """DataFrame -> rows with plain Python values (None for NaN), as SQLAlchemy expects."""
    obj = df.astype(object).where(df.notna(), None)
    return [{k: (v.item() if isinstance(v, np.generic) else v) for k, v in r.items()} for r in obj.to_dict("records")]


def run(argv: list[str] | None = None) -> int:
    """CLI: bustrust score [--once] [--at EPOCH]"""
    p = argparse.ArgumentParser(prog="bustrust score")
    p.add_argument("--once", action="store_true")
    p.add_argument("--at", type=int, help="score as of this epoch (replays history; implies --once)")
    p.add_argument("--delay", type=int, default=15, help="seconds after each minute to run (after the poller)")
    args = p.parse_args(argv)
    scorer = Scorer(db.init_db(), horizon_s=get_settings().horizon_min * 60)
    scorer.refresh_models(force=True)
    while True:
        started = time.monotonic()
        try:
            live = scorer.score(args.at)
            scorer.write(live)
            log.info("board: %d rows, %d trips, %.1f s", len(live.board), len(live.trips),
                     time.monotonic() - started)  # fmt: skip
        except Exception:
            log.exception("scoring failed; the previous board stays up")
        if args.once or args.at:
            return 0
        time.sleep(60 - (time.time() % 60) + args.delay)
