"""Response shapes: they generate the /docs page and keep the web app's types honest."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Health(BaseModel):
    status: str
    problems: list[str]
    now: int
    last_poll_at: dict[str, int]
    board_made_at: int | None
    model_version: str | None
    schedule_version: str | None


class Stop(BaseModel):
    stop_id: str
    stop_code: str | None
    stop_name: str | None
    stop_lat: float
    stop_lon: float


class RouteRef(BaseModel):
    route_id: str
    route_short_name: str | None


class StopDetail(Stop):
    routes: list[RouteRef]


StopStatus = Literal["on_time", "late", "early", "scheduled", "cancelled", "skipped", "departed"]
TripStatus = Literal["not_started", "running", "diverted", "cancelled", "finished"]


class BusPosition(BaseModel):
    lat: float | None = Field(description="last GPS fix, if it is under 5 minutes old")
    lon: float | None
    ping_at: int | None
    last_stop_id: str | None = Field(description="last stop the bus passed")
    last_stop_name: str | None
    stops_away: int | None
    off_route: bool = Field(description="its GPS is off the route line: a diversion")


class Arrival(BaseModel):
    trip_id: str
    route_id: str | None
    route: str | None
    headsign: str | None
    status: StopStatus = Field(
        description="on_time | late | early (by GPS or the feed) | scheduled (timetable only, the bus is not live "
        "yet) | cancelled | skipped (diverted away from this stop) | departed (passed in the last 15 min)"
    )
    delay_s: int | None = Field(description="how late the bus is running now (negative is early)")
    scheduled_at: int | None
    official_at: int | None = Field(description="the official prediction, unaltered; none once cancelled or gone")
    official_source: str | None = Field(description="realtime, or timetable when the bus is not in the live feed yet")
    likely_at: int | None = Field(description="our median; equals official_at when no model is live")
    early_at: int | None = Field(description="10th percentile: 9 in 10 buses come no earlier")
    late_at: int | None = Field(description="90th percentile: 9 in 10 buses come no later")
    passed_at: int | None = Field(description="departed rows: when the bus passed this stop")
    noshow_prob: float | None
    noshow_risk: str | None = Field(description="low | medium | high | cancelled")
    tracked: bool = Field(description="the bus is sending GPS right now")
    vehicle_id: str | None
    position: BusPosition
    leave_by_at: int | None = None


class Arrivals(BaseModel):
    stop: Stop
    now: int
    board_made_at: int | None
    stale: bool
    arrivals: list[Arrival]
    attribution: str


class RouteSummary(BaseModel):
    route_id: str
    agency_id: str | None
    route_short_name: str | None
    route_long_name: str | None


class ScoreSummary(BaseModel):
    n_trips: int
    reliability_pct: float | None = Field(None, description="share of scheduled trips that ran and kept time")
    noshow_pct: float | None = None
    on_time_pct: float | None = Field(None, description="departures between 1 min early and 5 min late")
    bunched_pct: float | None = Field(None, description="gaps under a quarter of the scheduled headway")
    ewt_s: float | None = Field(None, description="excess wait time on frequent routes, seconds")
    mean_delay_s: float | None = None


class DailyScore(ScoreSummary):
    service_date: str


class RouteReliability(BaseModel):
    route: str
    route_ids: list[str]
    long_name: str | None
    days: int
    summary: ScoreSummary
    by_direction: dict[str, ScoreSummary]
    daily: list[DailyScore]
    heatmap: list[dict[str, Any]]
    slowest_segments: list[dict[str, Any]]
    attribution: str


class LeaderRow(ScoreSummary):
    route: str
    rank: int


class Leaderboard(BaseModel):
    days: int
    routes: list[LeaderRow]
    attribution: str


class Vehicle(BaseModel):
    vehicle_id: str | None
    trip_id: str | None
    route_id: str | None
    route: str | None
    direction_id: int | None
    headsign: str | None
    status: TripStatus
    delay_s: int | None
    lat: float
    lon: float
    bearing: float | None
    ping_at: int | None
    next_stop_id: str | None
    off_route: bool


class TripStop(BaseModel):
    stop_sequence: int
    stop_id: str
    stop_name: str | None
    stop_lat: float | None
    stop_lon: float | None
    scheduled_at: int
    state: Literal["passed", "upcoming", "skipped", "cancelled"]
    passed_at: int | None
    official_at: int | None
    likely_at: int | None


class Trip(BaseModel):
    trip_id: str
    route_id: str | None
    route: str | None
    direction_id: int | None
    headsign: str | None
    service_date: str
    status: TripStatus
    delay_s: int | None
    vehicle_id: str | None
    lat: float | None
    lon: float | None
    bearing: float | None
    ping_at: int | None
    last_stop_id: str | None
    next_stop_id: str | None
    off_route: bool
    noshow_prob: float | None
    start_at: int | None
    end_at: int | None
    made_at: int
    stale: bool
    stops: list[TripStop]
    shape: list[list[float]] = Field(description="route line as [lon, lat] pairs")
    attribution: str


class AlertTrip(BaseModel):
    trip_id: str
    headsign: str | None
    start_at: int | None
    status: TripStatus
    reason: Literal["cancelled", "diverted", "at_risk", "very_late"]
    delay_s: int | None
    noshow_prob: float | None


class AlertRoute(BaseModel):
    route: str
    cancelled: int
    diverted: int
    at_risk: int
    very_late: int
    trips: list[AlertTrip]


class Alerts(BaseModel):
    now: int
    routes: list[AlertRoute]
    attribution: str


class Accuracy(BaseModel):
    days: int
    summary: dict[str, Any] | None
    daily: list[dict[str, Any]]
    models: dict[str, Any]
