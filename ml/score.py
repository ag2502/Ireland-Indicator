"""Live scoring: once a minute, right after the poller writes, turn the latest official
predictions into the board the API serves.

For every bus due at any stop within the next hour (in the feed, or only in the
timetable) it writes one live_board row: the official time unaltered, our p10/p50/p90 and
the no-show risk. Trips cancelled in the feed get risk 1 by rule; trips already moving
on GPS get 0. Rows whose official horizon is near a target horizon also go to
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
from sqlalchemy import Engine, text

from common import db
from common.config import get_settings
from common.schedule import Schedule, latest_version
from common.timeutil import local_date, now_epoch, service_day_reference, to_local
from ml import train
from ml.models import ArrivalModel, NoShowModel
from pipeline import features as F

log = logging.getLogger(__name__)

WINDOW_S = 3600  # realtime history behind each context (tracks, segment window)
LOG_TOLERANCE_S = 30  # a row is logged when its horizon is this close to a target
NOSHOW_FROM_MIN = -15  # trips that started more than this long ago are not scored for no-show
MODEL_REFRESH_S = 600


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
    def board_rows(self, ctx: F.DayContext, now: int) -> pd.DataFrame:
        """(trip, stop) pairs due in the next hour as seen at the latest poll, with features."""
        polls = ctx.poll_times[ctx.poll_times <= now]
        T = int(polls[-1]) if len(polls) else now
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

    def cancelled(self, ctx: F.DayContext, T: int) -> set[str]:
        tu = ctx.tu[ctx.tu["polled_at"] == T]
        return set(tu.loc[tu["trip_rel"] == "CANCELED", "trip_id"])

    def noshow_probs(self, ctx: F.DayContext, feats: pd.DataFrame, T: int) -> pd.Series:
        trips = pd.DataFrame({"trip_id": feats["trip_id"].unique()}).assign(T=T)
        start = trips["trip_id"].map(ctx.trips.set_index("trip_id")["start_at"])
        trips = trips[(start - T) / 60 >= NOSHOW_FROM_MIN]
        prob = pd.Series(np.nan, index=pd.Index(feats["trip_id"].unique()))
        if self.noshow is not None and len(trips):
            nf = F.noshow_rows(ctx, trips, self._route_rates(ctx.service_date))
            p = pd.Series(self.noshow.predict(nf), index=nf["trip_id"].to_numpy())
            p[nf["has_gps"].to_numpy() == 1] = 0.0
            prob.loc[p.index] = p
        running = feats.loc[feats["has_gps"] == 1, "trip_id"].unique()
        prob.loc[running] = 0.0 if self.noshow is not None else np.nan
        for t in self.cancelled(ctx, T):
            prob.loc[t] = 1.0
        return prob

    def _route_rates(self, service_date: str) -> dict[str, float]:
        if service_date not in self._rates:
            self._rates = {service_date: F.route_noshow_rates(self.engine, service_date)}
        return self._rates[service_date]

    def score(self, now: int | None = None) -> pd.DataFrame:
        now = now or now_epoch()
        self.refresh_models()
        vp, tu = self._realtime(now)
        boards = []
        for d in self.service_dates(now):
            dvp = vp[(vp["start_date"] == d) | vp["start_date"].isna()]
            dtu = tu[(tu["start_date"] == d) | tu["start_date"].isna()]
            ctx = F.build_context(self.engine, d, since=now - WINDOW_S, sched=self._schedule(d), vp=dvp, tu=dtu)
            feats = self.board_rows(ctx, now)
            if feats.empty:
                continue
            T = int(feats["T"].iloc[0])
            out = feats[["trip_id", "stop_sequence", "stop_id", "route_id", "scheduled_at", "official_at",
                         "vehicle_id", "has_gps", "horizon_s"]].copy()  # fmt: skip
            out["service_date"] = d
            if self.arrival is not None:
                pred = self.arrival.predict(feats)
                for q in ("p10", "p50", "p90"):
                    out[f"{q}_at"] = (feats["official_at"] + pred[q]).round().astype("int64")
                # a bus cannot arrive before now; keep the range ordered after clipping
                for q in ("p10_at", "p50_at", "p90_at"):
                    out[q] = out[q].clip(lower=now)
            else:
                out[["p10_at", "p50_at", "p90_at"]] = None
            out["noshow_prob"] = out["trip_id"].map(self.noshow_probs(ctx, feats, T))
            meta = ctx.se.drop_duplicates("trip_id").set_index("trip_id")[["route_short_name", "trip_headsign"]]
            out = out.join(meta, on="trip_id").rename(columns={"trip_headsign": "headsign"})
            boards.append(out)
        if not boards:
            return pd.DataFrame()
        board = pd.concat(boards, ignore_index=True)
        board["made_at"] = now
        board["model_version"] = self.model_version()
        board["has_gps"] = board["has_gps"].astype(bool)
        return board

    def write(self, board: pd.DataFrame) -> None:
        cols = [c.name for c in db.live_board.columns]
        db.replace_table(self.engine, db.live_board, _records(board.reindex(columns=cols)))
        if board.empty or self.arrival is None:
            return
        h = board["horizon_s"].to_numpy()
        near = np.zeros(len(board), dtype=bool)
        for target in F.HORIZON_TARGETS_MIN:
            near |= np.abs(h - target * 60) <= LOG_TOLERANCE_S
        logged = board[near].reindex(columns=[c.name for c in db.predictions_log.columns])
        db.insert_rows(self.engine, db.predictions_log, _records(logged))


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
            board = scorer.score(args.at)
            scorer.write(board)
            log.info("board: %d rows, %d stops, %.1f s", len(board), board["stop_id"].nunique() if len(board) else 0,
                     time.monotonic() - started)  # fmt: skip
        except Exception:
            log.exception("scoring failed; the previous board stays up")
        if args.once or args.at:
            return 0
        time.sleep(60 - (time.time() % 60) + args.delay)
