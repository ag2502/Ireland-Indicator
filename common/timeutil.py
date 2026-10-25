"""Time handling. Storage is UTC epoch seconds; Europe/Dublin is for display and calendars.

GTFS defines a stop time as seconds after "noon minus 12 hours" on the service day, local
time. That is midnight except on the two clock-change days, and it is why 25:10:00 is a
valid time belonging to the previous service day.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

DUBLIN = ZoneInfo("Europe/Dublin")


def now_epoch() -> int:
    return int(datetime.now(UTC).timestamp())


def parse_gtfs_time(value: str) -> int:
    """'25:10:00' -> 90600 seconds after the service day's reference point."""
    h, m, s = value.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def format_gtfs_time(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def parse_service_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return datetime.strptime(value, "%Y%m%d").date()


def service_day_reference(service_date: str | date, tz: ZoneInfo = DUBLIN) -> int:
    """Epoch seconds of noon-minus-12h local time on the service date (the GTFS origin)."""
    d = parse_service_date(service_date)
    noon = datetime(d.year, d.month, d.day, 12, tzinfo=tz)
    return int(noon.timestamp()) - 12 * 3600


def gtfs_time_to_epoch(service_date: str | date, gtfs_time: str | int) -> int:
    """Turn a service date plus a GTFS time (string or seconds) into UTC epoch seconds."""
    secs = parse_gtfs_time(gtfs_time) if isinstance(gtfs_time, str) else int(gtfs_time)
    return service_day_reference(service_date) + secs


def to_local(epoch: int) -> datetime:
    return datetime.fromtimestamp(epoch, tz=DUBLIN)


def to_utc(epoch: int) -> datetime:
    return datetime.fromtimestamp(epoch, tz=UTC)


def local_date(epoch: int) -> date:
    return to_local(epoch).date()


def service_dates_around(epoch: int) -> list[date]:
    """Service dates whose trips can be running at this instant: today and yesterday
    (a 25:30 trip from yesterday is still on the road after midnight)."""
    today = local_date(epoch)
    return [today - timedelta(days=1), today]


def day_length_hours(d: date) -> float:
    """23, 24 or 25: used by the clock-change tests and the data checks."""
    start = datetime(d.year, d.month, d.day, tzinfo=DUBLIN)
    nxt = d + timedelta(days=1)
    end = datetime(nxt.year, nxt.month, nxt.day, tzinfo=DUBLIN)
    return (end.timestamp() - start.timestamp()) / 3600
