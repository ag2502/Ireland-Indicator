import numpy as np
import pandas as pd
import pytest

from common import db
from ml import accuracy, models
from ml.score import Scorer
from pipeline import features as F
from tests.test_arrivals import DAY, START, world  # noqa: F401  (module-scoped fixture)

NOW = START + 100 * 60
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
    board = scorer.score(NOW)
    assert len(board)
    assert board["p50_at"].isna().all()
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
    board = scorer.score(NOW)
    assert (board["p10_at"] <= board["p50_at"]).all() and (board["p50_at"] <= board["p90_at"]).all()
    assert (board["p10_at"] >= NOW).all()
    assert board["noshow_prob"].between(0, 1).all()
    assert (board.loc[board["has_gps"], "noshow_prob"] == 0).all()
    assert board["model_version"].eq("a-test+n-test").all()

    tu = pd.read_sql("SELECT trip_id, polled_at, trip_rel FROM trip_updates", sim.engine)
    T = tu.loc[tu["polled_at"] <= NOW, "polled_at"].max()
    cancelled = set(tu.loc[(tu["polled_at"] == T) & (tu["trip_rel"] == "CANCELED"), "trip_id"])
    on_board = board[board["trip_id"].isin(cancelled)]
    assert (on_board["noshow_prob"] == 1.0).all()

    scorer.write(board)
    live = pd.read_sql("SELECT * FROM live_board", sim.engine)
    assert len(live) == len(board)
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
