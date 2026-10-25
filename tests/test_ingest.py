from datetime import UTC, datetime

import numpy as np
import pytest
from google.transit import gtfs_realtime_pb2 as rt
from sqlalchemy import select

from common import db
from common.config import Settings
from ingest import feeds, static_loader
from ingest.poller import Poller
from ingest.simulator import Simulator
from tests.gtfs_fixture import make_zip

T0 = int(datetime(2026, 11, 2, 7, 30, tzinfo=UTC).timestamp())  # Mon 07:30 GMT


@pytest.fixture
def sim_engine(engine):
    static_loader.load(engine, make_zip(), agency_ids=["1"])
    return engine


@pytest.fixture
def sim(sim_engine):
    return Simulator(sim_engine, routes=["39A"], ghost_rate=0.15)


def _tu_message() -> bytes:
    msg = rt.FeedMessage()
    msg.header.gtfs_realtime_version = "2.0"
    msg.header.timestamp = 1000
    e = msg.entity.add()
    e.id = "a"
    e.trip_update.trip.trip_id = "T1"
    e.trip_update.trip.route_id = "R1"
    e.trip_update.trip.start_date = "20261102"
    stu = e.trip_update.stop_time_update.add()
    stu.stop_sequence = 2
    stu.stop_id = "ST1"
    stu.arrival.delay = 120
    c = msg.entity.add()
    c.id = "b"
    c.trip_update.trip.trip_id = "T2"
    c.trip_update.trip.route_id = "R1"
    c.trip_update.trip.schedule_relationship = rt.TripDescriptor.CANCELED
    o = msg.entity.add()
    o.id = "c"
    o.trip_update.trip.trip_id = "X"
    o.trip_update.trip.route_id = "OTHER"
    return msg.SerializeToString()


def test_parse_trip_updates_keeps_cancellations_and_filters_routes():
    rows, n, header = feeds.parse("trip_updates", _tu_message(), polled_at=5, keep_routes={"R1"})
    assert n == 3 and header == 1000
    assert [r["trip_id"] for r in rows] == ["T1", "T2"]
    assert rows[0]["arrival_delay"] == 120 and rows[0]["stop_sequence"] == 2
    assert rows[1]["trip_rel"] == "CANCELED" and rows[1]["stop_id"] is None


class FakeResp:
    def __init__(self, code: int, body: bytes):
        self.status_code, self.content = code, body


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, headers):
        self.calls.append((url, headers["x-api-key"]))
        return self.responses.pop(0)


def _nta_poller(engine, responses, **kw):
    s = Settings(feed_mode="nta", nta_api_keys=["k1", "k2"], raw_archive=str(engine.url.database) + ".raw", **kw)
    return Poller(engine, s, client=FakeClient(responses), keep_routes={"R1"})


def test_poller_stores_rows_and_logs(engine):
    p = _nta_poller(engine, [FakeResp(200, _tu_message())])
    assert p.poll_once("trip_updates", t=T0)
    with engine.connect() as conn:
        assert len(conn.execute(select(db.trip_updates)).all()) == 2
        log = conn.execute(select(db.poll_log)).one()
    assert log.ok and log.n_entities == 3
    assert p.client.calls[0] == ("https://api.nationaltransport.ie/gtfsr/v2/TripUpdates", "k1")


def test_poller_backs_off_on_429_and_records_gap(engine):
    p = _nta_poller(engine, [FakeResp(200, _tu_message()), FakeResp(429, b"slow down"), FakeResp(200, _tu_message())])
    assert p.poll_once("trip_updates", t=T0)
    assert not p.poll_once("trip_updates", t=T0 + 120)
    assert p.state["trip_updates"].next_allowed_at > 0
    assert p.poll_once("trip_updates", t=T0 + 600)
    with engine.connect() as conn:
        gaps = conn.execute(select(db.feed_gaps)).all()
    assert [(g.start_at, g.end_at) for g in gaps] == [(T0, T0 + 600)]


def test_one_key_alternates_feeds_two_keys_poll_both(engine):
    p = _nta_poller(engine, [])
    assert [p.plan_tick() for _ in range(3)] == [[("trip_updates", 0)], [("vehicles", 0)], [("trip_updates", 0)]]
    p2 = _nta_poller(engine, [], use_both_keys=True)
    assert p2.plan_tick() == [("trip_updates", 0), ("vehicles", 1)]


def test_bad_payload_does_not_crash(engine):
    p = _nta_poller(engine, [FakeResp(200, b"\xff\x00garbage")])
    assert not p.poll_once("trip_updates", t=T0)


def test_simulator_world_is_deterministic(sim_engine):
    a = Simulator(sim_engine, routes=["39A"]).truth("20261102")[0]
    b = Simulator(sim_engine, routes=["39A"]).truth("20261102")[0]
    assert np.array_equal(a["true_at"].to_numpy(), b["true_at"].to_numpy())


def test_simulator_feeds_are_consistent(sim):
    stops, trips = sim.truth("20261102")
    ghosts = set(trips.index[trips.ghost])
    vp, _, _ = feeds.parse("vehicles", sim.vehicles(T0), T0)
    tu, _, _ = feeds.parse("trip_updates", sim.trip_updates(T0), T0)
    assert vp, "some buses should be on the road at 07:30"
    assert not ghosts & {r["trip_id"] for r in vp}, "ghost trips never report GPS"
    # true arrivals are non-decreasing along each trip
    assert (stops.groupby("trip_id")["true_at"].diff().dropna() >= 0).all()
    # every trip-update row names a trip in the world
    assert {r["trip_id"] for r in tu} <= set(trips.index)


def test_simulator_positions_move_forward(sim):
    a = {r["trip_id"]: r["lon"] for r in feeds.parse("vehicles", sim.vehicles(T0), T0)[0]}
    b = {r["trip_id"]: r["lon"] for r in feeds.parse("vehicles", sim.vehicles(T0 + 240), T0 + 240)[0]}
    common_trips = set(a) & set(b)
    assert common_trips
    moved = [b[t] - a[t] for t in common_trips]
    assert np.median(moved) > 0  # the fixture route runs east
