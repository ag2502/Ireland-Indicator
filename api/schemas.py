"""Response shapes: they generate the /docs page and keep the web app's types honest."""

from __future__ import annotations

from typing import Any

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


class Arrival(BaseModel):
    trip_id: str
    route_id: str | None
    route: str | None
    headsign: str | None
    scheduled_at: int | None
    official_at: int = Field(description="the official prediction, unaltered")
    official_source: str = Field(description="realtime, or timetable when the bus is not in the live feed yet")
    likely_at: int = Field(description="our median; equals official_at when no model is live")
    early_at: int | None = Field(description="10th percentile: 9 in 10 buses come no earlier")
    late_at: int | None = Field(description="90th percentile: 9 in 10 buses come no later")
    noshow_prob: float | None
    noshow_risk: str | None = Field(description="low | medium | high | cancelled")
    tracked: bool = Field(description="the bus is sending GPS right now")
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
    lat: float
    lon: float
    bearing: float | None
    ping_at: int | None


class AlertRoute(BaseModel):
    route: str
    cancelled: int
    at_risk: int
    trips: list[dict[str, Any]]


class Alerts(BaseModel):
    now: int
    routes: list[AlertRoute]
    attribution: str


class Accuracy(BaseModel):
    days: int
    summary: dict[str, Any] | None
    daily: list[dict[str, Any]]
    models: dict[str, Any]
