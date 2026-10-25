from datetime import UTC, date, datetime

from sqlalchemy import func, select

from common import db
from common.config import Settings
from common.timeutil import (
    day_length_hours,
    format_gtfs_time,
    gtfs_time_to_epoch,
    parse_gtfs_time,
    service_dates_around,
    to_local,
)


def utc(*args: int) -> int:
    return int(datetime(*args, tzinfo=UTC).timestamp())


def test_parse_gtfs_time_past_midnight():
    assert parse_gtfs_time("25:10:00") == 25 * 3600 + 600
    assert format_gtfs_time(90600) == "25:10:00"


def test_summer_time_is_utc_plus_one():
    # 08:00 Irish Summer Time on 20 Oct 2026 is 07:00 UTC
    assert gtfs_time_to_epoch("20261020", "08:00:00") == utc(2026, 10, 20, 7)


def test_winter_time_is_utc():
    assert gtfs_time_to_epoch("20261102", "08:00:00") == utc(2026, 11, 2, 8)


def test_after_midnight_belongs_to_previous_service_day():
    # 25:10 on service day Tue 3 Nov is 01:10 local on Wed 4 Nov (GMT, so 01:10 UTC)
    assert gtfs_time_to_epoch("20261103", "25:10:00") == utc(2026, 11, 4, 1, 10)


def test_clock_change_day_is_25_hours():
    assert day_length_hours(date(2026, 10, 25)) == 25
    assert day_length_hours(date(2026, 3, 29)) == 23
    assert day_length_hours(date(2026, 10, 26)) == 24


def test_clock_change_day_uses_noon_minus_12h_reference():
    # On 25 Oct noon is GMT, so the reference is 00:00 UTC and 12:00:00 maps to noon local.
    assert to_local(gtfs_time_to_epoch("20261025", "12:00:00")).hour == 12
    # Two schedule times one hour apart stay one hour apart across the 02:00 fall-back.
    a = gtfs_time_to_epoch("20261025", "01:30:00")
    b = gtfs_time_to_epoch("20261025", "02:30:00")
    assert b - a == 3600


def test_service_dates_around_includes_yesterday():
    ts = utc(2026, 11, 4, 0, 30)
    assert service_dates_around(ts) == [date(2026, 11, 3), date(2026, 11, 4)]


def test_settings_split_csv(monkeypatch):
    monkeypatch.setenv("BT_NTA_API_KEYS", "abc, def")
    monkeypatch.setenv("BT_AGENCY_IDS", "1")
    s = Settings()
    assert s.nta_api_keys == ["abc", "def"]
    assert s.agency_ids == ["1"]


def test_insert_ignore_and_upsert(engine):
    row = {
        "service_date": "20261020", "trip_id": "t1", "stop_sequence": 1, "stop_id": "s",
        "route_id": "r", "scheduled_at": 100, "actual_at": 160, "method": "gps_interp",
    }
    db.insert_rows(engine, db.arrivals, [row])
    db.insert_rows(engine, db.arrivals, [row], ignore_conflicts=True)
    db.upsert_rows(
        engine, db.arrivals, [{**row, "actual_at": 200}],
        keys=["service_date", "trip_id", "stop_sequence"],
    )
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(db.arrivals)).scalar() == 1
        assert conn.execute(select(db.arrivals.c.actual_at)).scalar() == 200


def test_replace_table(engine):
    base = {"made_at": 1, "stop_id": "s"}
    db.replace_table(engine, db.live_board, [base, base])
    db.replace_table(engine, db.live_board, [base])
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(db.live_board)).scalar() == 1
