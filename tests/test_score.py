import json

import numpy as np
import pandas as pd
import pytest

from common import db
from ml import accuracy, models
from ml.score import Scorer
from pipeline import features as F
from tests.test_arrivals import DAY, START, world  # noqa: F401  (module-scoped fixture)

NOW = START + 100 * 60
UPCOMING = ["on_time", "late", "early", "scheduled"]
SMALL = {"n_estimators": 30, "learning_rate": 0.1, "num_leaves": 7, "min_child_samples": 5, "verbose": -1}


@pytest.fixture(scope="module")
def trained(world):  # noqa: F811
    sim, arrivals, outcomes = world
    db.bulk_load_df(sim.engine, db.arrivals, arrivals)
    ctx = F.build_context(sim.engine, DAY)
    ex = F.arrival_examples(ctx, arrivals)
    ns = F.noshow_examples(ctx, outcomes.assign(noshow=outcomes["noshow"].astype(bool)), {})
    a = models.ArrivalModel(version="a-test").fit(ex, params=SMALL).calibrate(ex)
    n = models.NoShowModel(version="n-test").fit(ns, params=SMALL).calibrate(ns)
    return sim, a, n


def test_board_without_models_shows_official_unaltered(world):  # noqa: F811
    sim = world[0]
    scorer = Scorer(sim.engine)
    board = scorer.score(NOW).board
    assert len(board)
    assert board["p50_at"].isna().all()
    board = board[board["status"].isin(UPCOMING)]
    assert board["official_at"].between(NOW - 60, NOW + 3600).all()
    ctx = F.build_context(sim.engine, DAY)
    T = ctx.poll_times[ctx.poll_times <= NOW].max()
    off = ctx.official[ctx.official["polled_at"] == T][["trip_id", "stop_sequence", "official_at"]]
    m = board.merge(off, on=["trip_id", "stop_sequence"], suffixes=("", "_feed"))
    assert len(m) and (m["official_at"] == m["official_at_feed"]).all()
    assert board["route_short_name"].eq("39A").all()


def test_board_with_models(trained):
    sim, a, n = trained
    scorer = Scorer(sim.engine, arrival=a, noshow=n)
    scorer._models_checked_at = float("inf")  # keep the injected models
    live = scorer.score(NOW)
    board = live.board[live.board["status"].isin(UPCOMING)]
    assert (board["p10_at"] <= board["p50_at"]).all() and (board["p50_at"] <= board["p90_at"]).all()
    assert (board["p10_at"] >= NOW).all()
    assert board["noshow_prob"].between(0, 1).all()
    assert (board.loc[board["has_gps"], "noshow_prob"] == 0).all()
    assert board["model_version"].eq("a-test+n-test").all()

    tu = pd.read_sql("SELECT trip_id, polled_at, trip_rel FROM trip_updates", sim.engine)
    T = tu.loc[tu["polled_at"] <= NOW, "polled_at"].max()
    cancelled = set(tu.loc[(tu["polled_at"] == T) & (tu["trip_rel"] == "CANCELED"), "trip_id"])
    assert not board["trip_id"].isin(cancelled).any()
    on_board = live.board[live.board["trip_id"].isin(cancelled)]
    assert (on_board["status"] == "cancelled").all() and (on_board["noshow_prob"] == 1.0).all()

    scorer.write(live)
    stored = pd.read_sql("SELECT * FROM live_board", sim.engine)
    assert len(stored) == len(live.board)
    assert len(pd.read_sql("SELECT * FROM live_trips", sim.engine)) == len(live.trips)
    logged = pd.read_sql("SELECT * FROM predictions_log", sim.engine)
    assert 0 < len(logged) < len(board)


def test_accuracy_grades_logged_predictions(trained):
    sim, a, n = trained
    scorer = Scorer(sim.engine, arrival=a, noshow=n)
    scorer._models_checked_at = float("inf")
    with sim.engine.begin() as c:
        c.exec_driver_sql("DELETE FROM predictions_log")
    for t in range(START + 30 * 60, START + 150 * 60, 60):
        scorer.write(scorer.score(t))
    rows = accuracy.grade(sim.engine, DAY)
    overall = next(r for r in rows if r["horizon"] == "all")
    assert overall["n"] > 50
    assert np.isfinite(overall["mae_ours_s"]) and 0 <= overall["coverage"] <= 1
    assert {r["horizon"] for r in rows} - {"all"} <= {"0-5", "5-10", "10-20", "20-40", "40-60"}


def test_live_status_departed_cancelled_and_where_the_bus_is(world):  # noqa: F811
    sim = world[0]
    scorer = Scorer(sim.engine)
    ghosts = set(sim.truth(DAY)[1].query("ghost").index)
    st = pd.read_sql("SELECT trip_id, stop_sequence FROM gtfs_stop_times", sim.engine)
    sched_seqs = st.groupby("trip_id")["stop_sequence"].apply(list).to_dict()
    seen: dict[str, int] = {}
    located = 0
    keys = ["trip_id", "stop_sequence"]
    for now in range(START + 30 * 60, START + 150 * 60, 300):
        live = scorer.score(now)
        board, trips = live.board, live.trips
        assert set(board["status"]) <= {*UPCOMING, "departed", "cancelled", "skipped"}
        for k, v in board["status"].value_counts().items():
            seen[k] = seen.get(k, 0) + v

        dep = board[board["status"] == "departed"]
        by_gps = dep[dep["passed_at"].notna()]
        assert by_gps["passed_at"].between(now - 900, now).all()
        assert (by_gps["passed_at"] % 1 == 0).all()  # whole epoch seconds: the API model is int
        assert (by_gps["delay_s"] == (by_gps["passed_at"] - by_gps["scheduled_at"]).round()).all()
        # without GPS, a bus has left a stop when the feed stops predicting it
        assert (dep.loc[dep["passed_at"].isna(), "scheduled_at"] >= now - 900).all()
        up = board[board["status"].isin(UPCOMING)]
        # a stop a bus has left is never also shown as still to come for that bus
        assert up.merge(dep[keys], on=keys).empty
        assert (up.loc[up["status"] == "late", "delay_s"] >= 120).all()
        assert (up.loc[up["status"] == "early", "delay_s"] <= -60).all()
        assert not up.loc[up["status"] == "scheduled", "has_gps"].any()
        assert (board.loc[board["status"] == "cancelled", "noshow_prob"] == 1.0).all()

        assert trips["trip_id"].is_unique
        running = trips[trips["status"] == "running"]
        # every running bus can be located: by a fresh GPS fix, or by the last stop it passed
        assert (running["lat"].notna() | running["last_stop_id"].notna()).all()
        assert (running.loc[running["lat"].notna(), "ping_at"] >= now - 300).all()
        assert not set(running["trip_id"]) & ghosts
        located += int(running["lat"].notna().sum())
        for r in running.itertuples():
            # every stop behind the bus counts as passed, so a journey view has no gaps
            seqs = sorted(int(k) for k in json.loads(r.passed))
            assert seqs == sorted(sched_seqs[r.trip_id])[: len(seqs)]
        on_road = board[board["vehicle_lat"].notna()]
        assert (on_road["trip_id"].isin(trips.loc[trips["lat"].notna(), "trip_id"])).all()
    assert seen.get("departed") and seen.get("cancelled") and seen.get("on_time")
    assert located


def test_diversion_shows_skipped_stops_and_off_route_bus(tmp_path):
    from common.config import Settings
    from ingest import static_loader
    from ingest.poller import Poller
    from ingest.simulator import Simulator
    from tests.gtfs_fixture import make_zip

    eng = db.get_engine(f"sqlite:///{tmp_path / 'd.db'}")
    db.init_db(eng)
    static_loader.load(eng, make_zip(), agency_ids=["1"])
    sim = Simulator(eng, routes=["39A"], ghost_rate=0.0, gps_missing_rate=0.0, detour_rate=0.5, seed=5)
    poller = Poller(eng, Settings(feed_mode="simulated"), simulator=sim, archive=False, keep_routes=None)
    scorer = Scorer(eng)
    seen_skipped, seen_off_route = set(), set()
    for t in range(START, START + 150 * 60, 60):
        for feed, key in poller.plan_tick():
            poller.poll_once(feed, key, t=t)
        if t % 300 == 0:
            live = scorer.score(t)
            seen_skipped |= set(live.board.loc[live.board["status"] == "skipped", "trip_id"])
            seen_off_route |= set(live.trips.loc[live.trips["off_route"].astype(bool), "trip_id"])
            now_skipping = set(live.board.loc[live.board["status"] == "skipped", "trip_id"])
            assert (live.trips.loc[live.trips["trip_id"].isin(now_skipping), "status"] == "diverted").all()
    detoured = set(sim.truth(DAY)[1].query("detour").index)
    assert seen_skipped and seen_skipped <= detoured
    assert seen_off_route and seen_off_route <= detoured
