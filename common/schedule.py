"""In-memory view of one static schedule version, limited to chosen service dates."""

from __future__ import annotations

import json
from datetime import date
from functools import cached_property

import pandas as pd
from shapely.geometry import LineString
from sqlalchemy import Engine, text

from common import geo
from common.timeutil import parse_service_date, service_day_reference


def latest_version(engine: Engine, at: int | None = None) -> str | None:
    """The newest loaded version as of epoch `at` (default: now). Realtime data matches the
    version that was current when it was polled."""
    sql = "SELECT schedule_version FROM schedule_versions WHERE loaded_at IS NOT NULL"
    params: dict = {}
    if at is not None:
        sql += " AND loaded_at <= :at"
        params["at"] = at
    sql += " ORDER BY loaded_at DESC LIMIT 1"
    with engine.connect() as conn:
        row = conn.execute(text(sql), params).first()
        if row is None and at is not None:  # data older than our first load: use the oldest
            row = conn.execute(text(
                "SELECT schedule_version FROM schedule_versions WHERE loaded_at IS NOT NULL "
                "ORDER BY loaded_at ASC LIMIT 1"
            )).first()
    return row[0] if row else None


class Schedule:
    def __init__(self, version: str, routes: pd.DataFrame, stops: pd.DataFrame,
                 trips: pd.DataFrame, stop_times: pd.DataFrame, shapes: pd.DataFrame,
                 calendar: pd.DataFrame, calendar_dates: pd.DataFrame,
                 service_dates: list[str]):
        self.version = version
        self.routes = routes
        self.stops = stops
        self.trips = trips  # one row per (service_date, trip_id) actually running
        self.stop_times = stop_times
        self.shapes = shapes
        self.calendar = calendar
        self.calendar_dates = calendar_dates
        self.service_dates = service_dates

    # ---------------------------------------------------------------- loading
    @classmethod
    def load(cls, engine: Engine, version: str | None = None,
             service_dates: list[str | date] | None = None) -> Schedule:
        version = version or latest_version(engine)
        if version is None:
            raise RuntimeError("no static schedule loaded; run `bustrust load-schedule` first")
        v = {"v": version}
        q = lambda sql: pd.read_sql(text(sql), engine, params=v)  # noqa: E731
        calendar = q("SELECT service_id, days, start_date, end_date FROM gtfs_calendar WHERE schedule_version = :v")
        cal_dates = q("SELECT service_id, date, exception_type FROM gtfs_calendar_dates WHERE schedule_version = :v")
        routes = q("SELECT route_id, agency_id, route_short_name, route_long_name FROM gtfs_routes WHERE schedule_version = :v")
        stops = q("SELECT stop_id, stop_code, stop_name, stop_lat, stop_lon FROM gtfs_stops WHERE schedule_version = :v")
        all_trips = q("SELECT trip_id, route_id, service_id, direction_id, shape_id, block_id, "
                      "trip_headsign, first_dep_s, last_arr_s FROM gtfs_trips WHERE schedule_version = :v")
        dates = [parse_service_date(d).strftime("%Y%m%d") for d in (service_dates or [])]
        if dates:
            frames = []
            for d in dates:
                sids = active_service_ids(calendar, cal_dates, d)
                frames.append(all_trips[all_trips["service_id"].isin(sids)].assign(service_date=d))
            trips = pd.concat(frames, ignore_index=True)
        else:
            trips = all_trips.assign(service_date=None)
        sids = sorted(set(trips["service_id"]))
        if dates and sids:
            # filter in SQL via the trip join; service_ids are few, trip_ids are many
            placeholders = ", ".join(f":s{i}" for i in range(len(sids)))
            params = {"v": version, **{f"s{i}": s for i, s in enumerate(sids)}}
            st = pd.read_sql(text(
                "SELECT st.trip_id, st.stop_sequence, st.stop_id, st.arrival_s, st.departure_s, st.dist_m "
                "FROM gtfs_stop_times st JOIN gtfs_trips t "
                "ON t.schedule_version = st.schedule_version AND t.trip_id = st.trip_id "
                f"WHERE st.schedule_version = :v AND t.service_id IN ({placeholders})"
            ), engine, params=params)
        elif dates:
            st = pd.DataFrame(columns=["trip_id", "stop_sequence", "stop_id", "arrival_s", "departure_s", "dist_m"])
        else:
            st = q("SELECT trip_id, stop_sequence, stop_id, arrival_s, departure_s, dist_m "
                   "FROM gtfs_stop_times WHERE schedule_version = :v")
        st = st.sort_values(["trip_id", "stop_sequence"], ignore_index=True)
        shape_ids = set(trips["shape_id"].dropna())
        shapes = q("SELECT shape_id, coords, length_m FROM gtfs_shapes WHERE schedule_version = :v")
        shapes = shapes[shapes["shape_id"].isin(shape_ids)].set_index("shape_id")
        trips = trips.merge(routes[["route_id", "route_short_name"]], on="route_id", how="left")
        return cls(version, routes, stops, trips, st, shapes, calendar, cal_dates, dates)

    # ---------------------------------------------------------------- lookups
    @cached_property
    def _lines(self) -> dict[str, LineString]:
        return {}

    def line(self, shape_id: str) -> LineString | None:
        if shape_id not in self._lines:
            if shape_id not in self.shapes.index:
                return None
            self._lines[shape_id] = geo.shape_line(json.loads(self.shapes.at[shape_id, "coords"]))
        return self._lines[shape_id]

    @cached_property
    def stop_times_by_trip(self) -> dict[str, pd.DataFrame]:
        return {tid: g for tid, g in self.stop_times.groupby("trip_id", sort=False)}

    @cached_property
    def trip_info(self) -> pd.DataFrame:
        return self.trips.drop_duplicates("trip_id").set_index("trip_id")

    @cached_property
    def stop_names(self) -> dict[str, str]:
        return dict(zip(self.stops["stop_id"], self.stops["stop_name"], strict=True))

    def scheduled_epochs(self, service_date: str) -> pd.DataFrame:
        """Stop times of the trips running on `service_date`, with UTC epoch columns."""
        ref = service_day_reference(service_date)
        trips = self.trips[self.trips["service_date"] == service_date]
        st = self.stop_times[self.stop_times["trip_id"].isin(set(trips["trip_id"]))]
        st = st.merge(
            trips[["trip_id", "route_id", "route_short_name", "direction_id", "shape_id",
                   "trip_headsign", "block_id"]], on="trip_id")
        return st.assign(
            service_date=service_date,
            scheduled_at=ref + st["arrival_s"].astype("int64"),
            scheduled_dep_at=ref + st["departure_s"].astype("int64"),
        )


def active_service_ids(calendar: pd.DataFrame, cal_dates: pd.DataFrame, service_date: str) -> set[str]:
    d = parse_service_date(service_date)
    ds = d.strftime("%Y%m%d")
    wd = d.weekday()
    on = set()
    if len(calendar):
        m = (calendar["start_date"] <= ds) & (calendar["end_date"] >= ds) & (
            calendar["days"].str[wd] == "1")
        on = set(calendar.loc[m, "service_id"])
    if len(cal_dates):
        today = cal_dates[cal_dates["date"] == ds]
        on |= set(today.loc[today["exception_type"] == 1, "service_id"])
        on -= set(today.loc[today["exception_type"] == 2, "service_id"])
    return on
