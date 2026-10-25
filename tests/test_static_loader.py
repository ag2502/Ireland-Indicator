import pytest
from shapely.geometry import LineString
from sqlalchemy import func, select

from common import db, geo
from common.schedule import Schedule, active_service_ids, latest_version
from ingest import static_loader
from tests.gtfs_fixture import make_zip, stop_dist


@pytest.fixture
def loaded(engine):
    data = make_zip()
    version = static_loader.load(engine, data, agency_ids=["1"])
    return engine, version


def test_load_filters_agency_and_versions(loaded):
    engine, version = loaded
    assert version and len(version) == 16
    with engine.connect() as conn:
        routes = conn.execute(select(db.gtfs_routes.c.route_id)).scalars().all()
        n_st = conn.execute(select(func.count()).select_from(db.gtfs_stop_times)).scalar()
    assert routes == ["R1"]
    assert n_st == 13 * 4  # 12 morning trips + 1 after-midnight trip, 4 stops each


def test_reload_same_zip_is_skipped(loaded):
    engine, _ = loaded
    assert static_loader.load(engine, make_zip(), agency_ids=["1"]) is None


def test_new_zip_gets_new_version(loaded):
    engine, v1 = loaded
    v2 = static_loader.load(engine, make_zip(n_trips=5), agency_ids=["1"])
    assert v2 and v2 != v1
    assert latest_version(engine) == v2


def test_stop_distances_along_shape(loaded):
    engine, version = loaded
    sched = Schedule.load(engine, version, service_dates=["20261102"])
    st = sched.stop_times[sched.stop_times.trip_id == "T0"].sort_values("stop_sequence")
    for i, d in enumerate(st["dist_m"]):
        assert d == pytest.approx(stop_dist(i), abs=5)


def test_schedule_service_dates_and_epochs(loaded):
    engine, version = loaded
    sched = Schedule.load(engine, version, service_dates=["20261102", "20261225"])
    # Christmas is removed by calendar_dates
    assert set(sched.trips["service_date"]) == {"20261102"}
    ep = sched.scheduled_epochs("20261102")
    late = ep[(ep.trip_id == "T12") & (ep.stop_sequence == 1)].iloc[0]
    # 25:10 on Mon 2 Nov is 01:10 GMT on Tue 3 Nov
    from datetime import UTC, datetime

    assert late["scheduled_at"] == datetime(2026, 11, 3, 1, 10, tzinfo=UTC).timestamp()


def test_active_service_ids_weekday_mask():
    import pandas as pd

    cal = pd.DataFrame([{"service_id": "A", "days": "1111100", "start_date": "20261001",
                         "end_date": "20261231"}])
    cd = pd.DataFrame(columns=["service_id", "date", "exception_type"])
    assert active_service_ids(cal, cd, "20261102") == {"A"}  # Monday
    assert active_service_ids(cal, cd, "20261107") == set()  # Saturday


def test_project_stops_in_order_handles_loops():
    # out along x then back: a stop at x=50 on the return leg must not snap to the outbound pass
    line = LineString([(0, 0), (100, 0), (100, 10), (0, 10)])
    lon_lat = []
    from pyproj import Transformer
    back = Transformer.from_crs("EPSG:2157", "EPSG:4326", always_xy=True)
    base_x, base_y = geo.to_itm(-6.26, 53.35)
    line = LineString([(base_x + x, base_y + y) for x, y in line.coords])
    for x, y in [(0, 0), (100, 0), (50, 10)]:
        lon_lat.append(back.transform(base_x + x, base_y + y))
    d = geo.project_stops_in_order(line, lon_lat)
    assert d[0] == pytest.approx(0, abs=1)
    assert d[1] == pytest.approx(100, abs=1)
    assert d[2] == pytest.approx(160, abs=1)
