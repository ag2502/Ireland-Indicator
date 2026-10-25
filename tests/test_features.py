import numpy as np
import pandas as pd
import pytest

from pipeline import features as F
from tests.test_arrivals import DAY, END, world  # noqa: F401  (module-scoped fixture)


@pytest.fixture(scope="module")
def ctx_and_examples(world):  # noqa: F811
    sim, arrivals, outcomes = world
    from common import db

    engine = sim.engine
    db.bulk_load_df(engine, db.arrivals, arrivals)
    ctx = F.build_context(engine, DAY)
    ex = F.arrival_examples(ctx, arrivals)
    ns = F.noshow_examples(ctx, outcomes.assign(noshow=outcomes["noshow"].astype(bool)), {})
    return ctx, ex, ns, arrivals


def test_arrival_examples_have_all_features(ctx_and_examples):
    _, ex, _, _ = ctx_and_examples
    assert len(ex) > 50
    missing = [c for c in F.ARRIVAL_FEATURES if c not in ex]
    assert not missing
    assert ex["horizon_s"].between(-120, 3800).all()
    assert (ex["y"] == ex["actual_at"] - ex["official_at"]).all()
    assert set(ex["official_from_schedule"]) == {0, 1}


def test_no_future_information(ctx_and_examples):
    _, ex, _, _ = ctx_and_examples
    gps = ex[ex["has_gps"] == 1]
    assert len(gps)
    assert (gps["ping_at"] <= gps["T"]).all(), "a ping from after T leaked in"
    assert (ex["T"] < ex["actual_at"] + 3600).all()
    # the official prediction used must come from a poll at or before T
    assert ex["T"].isin(set(np.asarray(_poll_times(ctx_and_examples)))).all()


def _poll_times(c):
    return c[0].poll_times


def test_passages_known_only_after_they_happen(ctx_and_examples):
    ctx = ctx_and_examples[0]
    assert (ctx.passages["known_at"] >= ctx.passages["passed_at"] - 60).all()


def test_pos_delay_tracks_true_delay(ctx_and_examples, world):  # noqa: F811
    _, ex, _, _ = ctx_and_examples
    sim = world[0]
    truth = sim.truth(DAY)[0][["trip_id", "stop_sequence", "true_delay"]]
    gps = ex[(ex["has_gps"] == 1) & (ex["stops_to_go"] <= 1)].merge(truth, on=["trip_id", "stop_sequence"])
    # right before the stop, the position-implied delay should be close to the real one
    assert (gps["pos_delay_s"] - gps["true_delay"]).abs().median() < 90


def test_noshow_examples(ctx_and_examples):
    _, _, ns, _ = ctx_and_examples
    assert len(ns) and not [c for c in F.NOSHOW_FEATURES if c not in ns]
    assert set(ns["y"]) <= {0, 1}
    # a trip with GPS before T is running
    assert (ns.loc[ns["has_gps"] == 1, "y"] == 0).all()


def test_lost_ahead_is_zero_when_stop_reached():
    ctx = F.DayContext(
        service_date=DAY,
        se=pd.DataFrame(
            {"trip_id": ["A"] * 3, "pos": [0, 1, 2], "stop_id": list("xyz"), "route_id": "R", "direction_id": 0}
        ),
        trips=pd.DataFrame(),
        tu=pd.DataFrame(),
        official=pd.DataFrame(),
        tracks=pd.DataFrame(),
        passages=pd.DataFrame(),
        seg_window=pd.DataFrame(
            {
                "route_id": "R",
                "direction_id": 0,
                "stop_id": ["x", "y"],
                "pbin": [10, 10],
                "lost_sum": [60.0, 120.0],
                "lost_n": [1.0, 2.0],
            }
        ),
        weather=pd.DataFrame(),
        poll_times=np.array([]),
    )
    df = pd.DataFrame({"T": [3000, 3000, 3000], "trip_id": "A", "pos": [2, 1, 0]})
    out = F._lost_ahead(ctx, df, pd.Series([0, 1, 0]))
    assert out["lost_ahead_s"].tolist() == [120.0, 0.0, 0.0]  # x: 60/1 + y: 120/2
    assert out["lost_ahead_n"].tolist() == [3.0, 0.0, 0.0]
