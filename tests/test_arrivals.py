from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString

from ingest import static_loader
from ingest.poller import Poller
from ingest.simulator import Simulator
from pipeline import arrivals as arr
from pipeline.official import propagate
from tests.gtfs_fixture import make_zip

DAY = "20261102"
START = int(datetime(2026, 11, 2, 6, 30, tzinfo=UTC).timestamp())
END = int(datetime(2026, 11, 2, 9, 30, tzinfo=UTC).timestamp())


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    from common import db
    from common.config import Settings

    eng = db.get_engine(f"sqlite:///{tmp_path_factory.mktemp('arr') / 'a.db'}")
    db.init_db(eng)
    static_loader.load(eng, make_zip(), agency_ids=["1"])
    sim = Simulator(eng, routes=["39A"], ghost_rate=0.12, gps_missing_rate=0.15, seed=3)
    poller = Poller(eng, Settings(feed_mode="simulated"), simulator=sim, archive=False, keep_routes=None)
    for t in range(START, END, 60):
        for feed, key in poller.plan_tick():
            poller.poll_once(feed, key, t=t)
    arrivals, outcomes = arr.build_day(eng, DAY, now=END + 7200)
    return sim, arrivals, outcomes


def test_gps_arrivals_match_truth(world):
    sim, arrivals, _ = world
    truth = sim.truth(DAY)[0][["trip_id", "stop_sequence", "true_at"]]
    m = arrivals[arrivals.method == "gps_interp"].merge(truth, on=["trip_id", "stop_sequence"])
    assert len(m) >= 10
    err = (m["actual_at"] - m["true_at"]).abs()
    # 2-minute ping cadence, up to 45 s stale, 7 m noise: labels within ~half a minute
    assert err.median() < 30, err.describe()


def test_trips_without_any_gps_get_no_arrivals(world):
    sim, arrivals, _ = world
    trips = sim.truth(DAY)[1]
    no_gps = set(trips.index[~trips.has_gps])
    assert no_gps, "fixture should contain trips without GPS"
    assert not no_gps & set(arrivals["trip_id"])


def test_ghosts_get_no_arrivals_and_are_noshows(world):
    sim, arrivals, outcomes = world
    trips = sim.truth(DAY)[1]
    ghosts = set(trips.index[trips.ghost])
    assert ghosts, "fixture should contain ghosts"
    assert not ghosts & set(arrivals["trip_id"])
    o = outcomes.set_index("trip_id")
    assert o.loc[sorted(ghosts & set(o.index)), "noshow"].all()
    ran = set(trips.index[trips.has_gps]) & set(o.index)
    assert not o.loc[sorted(ran), "noshow"].any()


def test_track_rejects_off_route_and_teleports():
    line = LineString([(0, 0), (5000, 0)])
    from pyproj import Transformer

    back = Transformer.from_crs("EPSG:2157", "EPSG:4326", always_xy=True)
    bx, by = 700000, 735000
    line = LineString([(bx, by), (bx + 5000, by)])
    pts = [(0, 100, 5), (60, 400, 5), (120, 900, 400), (180, 4900, 5), (240, 1300, 5)]
    rows = []
    for t, x, y in pts:
        lon, lat = back.transform(bx + x, by + y)
        rows.append({"ping_at": t, "lat": lat, "lon": lon})
    tr = arr.track(pd.DataFrame(rows), line)
    assert tr["ping_at"].tolist() == [0, 60, 240]  # off-route and 80 m/s jump dropped
    assert np.all(np.diff(tr["dist"]) >= 0)


def test_propagate_inherits_nearest_preceding_update():
    sched = pd.DataFrame(
        {"trip_id": "T", "stop_sequence": [1, 2, 3, 4], "stop_id": list("abcd"), "scheduled_at": [0, 100, 200, 300]}
    )
    tu = pd.DataFrame(
        [
            {
                "polled_at": 50,
                "trip_id": "T",
                "stop_sequence": 2,
                "arrival_delay": 60,
                "arrival_at": None,
                "departure_delay": None,
                "trip_rel": "SCHEDULED",
                "stop_rel": "SCHEDULED",
                "vehicle_id": "V",
            },
            {
                "polled_at": 50,
                "trip_id": "T",
                "stop_sequence": 4,
                "arrival_delay": None,
                "arrival_at": 420,
                "departure_delay": None,
                "trip_rel": "SCHEDULED",
                "stop_rel": "SCHEDULED",
                "vehicle_id": "V",
            },
        ]
    )
    out = propagate(tu, sched).set_index("stop_sequence")
    assert 1 not in out.index  # already passed
    assert out.loc[2, "official_at"] == 160 and out.loc[3, "official_at"] == 260
    assert out.loc[4, "official_at"] == 420  # absolute time wins at its own stop


def test_propagate_skipped_stop_keeps_upstream_delay():
    sched = pd.DataFrame(
        {"trip_id": "T", "stop_sequence": [1, 2, 3, 4], "stop_id": list("abcd"), "scheduled_at": [0, 100, 200, 300]}
    )
    base = {"polled_at": 50, "trip_id": "T", "arrival_at": None, "departure_delay": None, "trip_rel": "SCHEDULED",
            "vehicle_id": "V"}  # fmt: skip
    tu = pd.DataFrame(
        [
            {**base, "stop_sequence": 2, "arrival_delay": 90, "stop_rel": "SCHEDULED"},
            {**base, "stop_sequence": 3, "arrival_delay": None, "stop_rel": "SKIPPED"},
        ]
    )
    out = propagate(tu, sched).set_index("stop_sequence")
    assert 3 not in out.index  # no prediction at a stop the bus will not serve
    assert out.loc[4, "official_at"] == 390  # but the delay carries on past it
    nd = tu.assign(stop_rel=["SCHEDULED", "NO_DATA"])
    assert set(propagate(nd, sched)["stop_sequence"]) == {2}  # NO_DATA blocks downstream
