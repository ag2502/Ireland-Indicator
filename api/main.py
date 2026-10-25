"""BusTrust public API (read-only). Reads our own database; never calls the NTA.

Times are UTC epoch seconds; the client formats them in Europe/Dublin. The official
prediction is passed through unaltered in `official_at` beside our range.

Run: uvicorn api.main:app --host 0.0.0.0 --port 8000   (docs at /docs)
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from datetime import timedelta
from functools import lru_cache
from typing import Annotated, Any

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from sqlalchemy import Engine, text

from api import schemas as S
from common import db
from common.config import get_settings
from common.schedule import latest_version
from common.timeutil import local_date, now_epoch, service_day_reference
from pipeline.scores import reliability_pct

ATTRIBUTION = (
    "Unofficial. Not affiliated with Dublin Bus, Go-Ahead Ireland, the NTA or Transport for Ireland. "
    "Contains Irish Public Sector Data (National Transport Authority) licensed under CC BY 4.0. "
    "Weather: Open-Meteo.com (CC BY 4.0)."
)
BOARD_STALE_S = 180
POLL_STALE_S = 300
MIN_TRIPS_FOR_RANK = 20
VERY_LATE_S = 600
RECENT_S = 900  # cancelled and departed buses stay listed this long (ml.status.DEPARTED_SHOW_S)
UPCOMING = {"on_time", "late", "early", "scheduled"}


def create_app(engine: Engine | None = None) -> FastAPI:
    app = FastAPI(
        title="BusTrust API",
        version="1.0",
        description="Honest arrival ranges, no-show risk and route reliability for Dublin buses. " + ATTRIBUTION,
    )
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])
    app.state.engine = engine
    _routes(app)
    return app


def get_engine(request: Request) -> Engine:
    if request.app.state.engine is None:
        request.app.state.engine = db.get_engine()
    return request.app.state.engine


EngineDep = Annotated[Engine, Depends(get_engine)]


def _cache(response: Response, seconds: int) -> None:
    response.headers["Cache-Control"] = f"public, max-age={seconds}"


def _q(engine: Engine, sql: str, **params: Any) -> pd.DataFrame:
    return pd.read_sql(text(sql), engine, params=params)


def _clean(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if hasattr(v, "item"):
        v = v.item()
        return None if isinstance(v, float) and math.isnan(v) else v
    return v


def _records(df: pd.DataFrame) -> list[dict]:
    return [{k: _clean(v) for k, v in r.items()} for r in df.to_dict("records")]


def _since(days: int) -> str:
    return (local_date(now_epoch()) - timedelta(days=days)).strftime("%Y%m%d")


# ------------------------------------------------------------------ cached lookups
@lru_cache(maxsize=4)
def _stops(engine: Engine, version: str) -> pd.DataFrame:
    return _q(
        engine,
        "SELECT stop_id, stop_code, stop_name, stop_lat, stop_lon FROM gtfs_stops WHERE schedule_version = :v",
        v=version,
    )


@lru_cache(maxsize=4)
def _routes_df(engine: Engine, version: str) -> pd.DataFrame:
    return _q(
        engine,
        "SELECT route_id, agency_id, route_short_name, route_long_name FROM gtfs_routes WHERE schedule_version = :v",
        v=version,
    )


def _version(engine: Engine) -> str:
    v = latest_version(engine)
    if v is None:
        raise HTTPException(503, "no schedule loaded yet")
    return v


def _find_stop(engine: Engine, stop: str) -> dict:
    stops = _stops(engine, _version(engine))
    m = stops[(stops["stop_id"] == stop) | (stops["stop_code"] == stop)]
    if m.empty:
        raise HTTPException(404, f"unknown stop {stop!r}")
    return _records(m.head(1))[0]


def _find_route(engine: Engine, route: str) -> pd.DataFrame:
    routes = _routes_df(engine, _version(engine))
    m = routes[(routes["route_id"] == route) | (routes["route_short_name"].str.upper() == route.upper())]
    if m.empty:
        raise HTTPException(404, f"unknown route {route!r}")
    return m


def risk_label(p: float | None) -> str | None:
    if p is None:
        return None
    if p >= 0.999:
        return "cancelled"
    return "high" if p >= 0.3 else "medium" if p >= 0.1 else "low"


# ------------------------------------------------------------------ endpoints
def _routes(app: FastAPI) -> None:  # noqa: C901  (one place to read every endpoint)
    get: Callable = app.get

    @get("/healthz", response_model=S.Health, tags=["ops"])
    def healthz(engine: EngineDep, response: Response) -> dict:
        now = now_epoch()
        polls = _q(engine, "SELECT feed, MAX(polled_at) AS last FROM poll_log WHERE ok GROUP BY feed")
        board = _q(engine, "SELECT MAX(made_at) AS made_at, MAX(model_version) AS mv FROM live_board")
        last = {r["feed"]: int(r["last"]) for r in _records(polls)}
        board_at = _clean(board["made_at"].iloc[0]) if len(board) else None
        problems = [f"{f} feed {now - t} s old" for f, t in last.items() if f != "weather" and now - t > POLL_STALE_S]
        if board_at is None or now - board_at > BOARD_STALE_S:
            problems.append("live board stale")
        if problems:
            response.status_code = 503
        return {
            "status": "degraded" if problems else "ok",
            "problems": problems,
            "now": now,
            "last_poll_at": last,
            "board_made_at": board_at,
            "model_version": _clean(board["mv"].iloc[0]) if len(board) else None,
            "schedule_version": latest_version(engine),
            "feed_mode": get_settings().feed_mode,
        }

    @get("/v1/stops", response_model=list[S.Stop], tags=["stops"])
    def stops(
        engine: EngineDep,
        response: Response,
        q: Annotated[str | None, Query(description="search stop name or number")] = None,
        limit: Annotated[int, Query(ge=1, le=10000)] = 10000,
    ) -> list[dict]:
        """Every stop (search with `q`). Find nearby stops on the device: the client
        downloads this list once a day, so a rider's location never reaches our server."""
        df = _stops(engine, _version(engine))
        if q:
            ql = q.strip().lower()
            hit = df["stop_name"].str.lower().str.contains(ql, regex=False) | (df["stop_code"].astype(str) == ql)
            df = df[hit].assign(exact=lambda x: x["stop_code"].astype(str) == ql)
            df = df.sort_values(["exact", "stop_name"], ascending=[False, True]).drop(columns="exact")
        _cache(response, 3600 if q else 86400)
        return _records(df.head(limit))

    @get("/v1/stops/{stop}", response_model=S.StopDetail, tags=["stops"])
    def stop_detail(stop: str, engine: EngineDep, response: Response) -> dict:
        s = _find_stop(engine, stop)
        routes = _q(
            engine,
            "SELECT DISTINCT r.route_id, r.route_short_name FROM gtfs_stop_times st "
            "JOIN gtfs_trips t ON t.schedule_version = st.schedule_version AND t.trip_id = st.trip_id "
            "JOIN gtfs_routes r ON r.schedule_version = t.schedule_version AND r.route_id = t.route_id "
            "WHERE st.schedule_version = :v AND st.stop_id = :s ORDER BY r.route_short_name",
            v=_version(engine),
            s=s["stop_id"],
        )
        _cache(response, 3600)
        return {**s, "routes": _records(routes)}

    @get("/v1/stops/{stop}/arrivals", response_model=S.Arrivals, tags=["stops"])
    def arrivals(
        stop: str,
        engine: EngineDep,
        response: Response,
        walk_min: Annotated[float | None, Query(ge=0, le=60, description="minutes to walk to the stop")] = None,
        route: Annotated[str | None, Query(description="only this route (short name)")] = None,
    ) -> dict:
        """Buses due in the next hour: official time unaltered, our likely time and 80% range,
        the chance the bus never comes, and its live status (late, cancelled, diverted away
        from this stop, or already gone) with where the bus is now. Buses that left in the
        last 15 minutes stay on the list, marked departed. With `walk_min`, each upcoming row
        gets `leave_by_at`: when to set off to be at the stop by the early edge of the range."""
        s = _find_stop(engine, stop)
        now = now_epoch()
        df = _q(
            engine,
            "SELECT * FROM live_board WHERE stop_id = :s "
            "AND (status IN ('departed', 'cancelled', 'skipped') OR COALESCE(official_at, scheduled_at) >= :a)",
            s=s["stop_id"],
            a=now - 60,
        )
        if route:
            df = df[df["route_short_name"].str.upper() == route.upper()]
        made_at = int(df["made_at"].max()) if len(df) else None
        names = _stops(engine, _version(engine)).set_index("stop_id")["stop_name"]
        rows = [_arrival(r, names, walk_min) for r in _records(df)]
        rows.sort(key=lambda r: r["passed_at"] or r["likely_at"] or r["scheduled_at"] or 0)
        _cache(response, 20)
        return {
            "stop": s,
            "now": now,
            "board_made_at": made_at,
            "stale": made_at is None or now - made_at > BOARD_STALE_S,
            "arrivals": rows,
            "attribution": ATTRIBUTION,
        }

    @get("/v1/routes", response_model=list[S.RouteSummary], tags=["routes"])
    def routes(engine: EngineDep, response: Response) -> list[dict]:
        df = _routes_df(engine, _version(engine))
        _cache(response, 3600)
        return _records(df.sort_values("route_short_name", key=_natural))

    @get("/v1/routes/{route}/reliability", response_model=S.RouteReliability, tags=["routes"])
    def route_reliability(
        route: str, engine: EngineDep, response: Response, days: Annotated[int, Query(ge=1, le=90)] = 7
    ) -> dict:
        r = _find_route(engine, route)
        ids = r["route_id"].tolist()
        ph = ", ".join(f":r{i}" for i in range(len(ids)))
        rp = {f"r{i}": v for i, v in enumerate(ids)}
        daily = _q(
            engine,
            f"SELECT * FROM route_scores_daily WHERE route_id IN ({ph}) AND service_date >= :d ORDER BY service_date",
            d=_since(days),
            **rp,
        )
        heat = _q(engine, f"SELECT weekday, hour, n, on_time_share, median_delay_s FROM route_heatmap "
                          f"WHERE route_id IN ({ph}) ORDER BY weekday, hour", **rp)  # fmt: skip
        seg = _q(
            engine,
            "SELECT g.direction_id, g.from_stop_id, g.to_stop_id, g.n, g.median_lost_s, a.stop_name AS from_name, "
            "b.stop_name AS to_name FROM segment_delays g "
            "LEFT JOIN gtfs_stops a ON a.stop_id = g.from_stop_id AND a.schedule_version = :v "
            "LEFT JOIN gtfs_stops b ON b.stop_id = g.to_stop_id AND b.schedule_version = :v "
            f"WHERE g.route_id IN ({ph}) ORDER BY g.median_lost_s DESC LIMIT 5",
            v=_version(engine),
            **rp,
        )
        _cache(response, 600)
        return {
            "route": r["route_short_name"].iloc[0],
            "route_ids": ids,
            "long_name": r["route_long_name"].iloc[0],
            "days": days,
            "summary": _summarise(daily),
            "by_direction": {str(k): _summarise(g) for k, g in daily.groupby("direction_id")},
            "daily": [{"service_date": d, **_summarise(g)} for d, g in daily.groupby("service_date")],
            "heatmap": _records(heat),
            "slowest_segments": _records(seg),
            "attribution": ATTRIBUTION,
        }

    @get("/v1/leaderboard", response_model=S.Leaderboard, tags=["routes"])
    def leaderboard(engine: EngineDep, response: Response, days: Annotated[int, Query(ge=1, le=90)] = 7) -> dict:
        """Routes ranked by reliability: the share of scheduled trips that ran and kept time."""
        daily = _q(engine, "SELECT * FROM route_scores_daily WHERE service_date >= :d", d=_since(days))
        rows = []
        for name, g in daily.groupby("route_short_name"):
            s = _summarise(g)
            if s["n_trips"] >= MIN_TRIPS_FOR_RANK and s["reliability_pct"] is not None:
                rows.append({"route": name, **s})
        rows.sort(key=lambda x: -x["reliability_pct"])
        for i, r in enumerate(rows, 1):
            r["rank"] = i
        _cache(response, 600)
        return {"days": days, "routes": rows, "attribution": ATTRIBUTION}

    @get("/v1/vehicles", response_model=list[S.Vehicle], tags=["live"])
    def vehicles(
        engine: EngineDep, response: Response, route: Annotated[str | None, Query(description="short name")] = None
    ) -> list[dict]:
        """Every bus with a GPS fix under 5 minutes old: where it is, how late, and whether
        it has left its route line."""
        df = _q(engine, "SELECT * FROM live_trips WHERE lat IS NOT NULL")
        df = df.rename(columns={"route_short_name": "route"})
        if route:
            df = df[df["route"].str.upper() == route.upper()]
        df["off_route"] = df["off_route"].fillna(False).astype(bool)
        _cache(response, 20)
        return _records(df)

    @get("/v1/trips/{trip_id}", response_model=S.Trip, tags=["live"])
    def trip(trip_id: str, engine: EngineDep, response: Response) -> dict:
        """One bus's journey: every stop with when it passed, will skip, or is due, where the
        bus is now and the route line to draw it on."""
        now = now_epoch()
        live = _q(engine, "SELECT * FROM live_trips WHERE trip_id = :t ORDER BY service_date DESC", t=trip_id)
        if live.empty:
            raise HTTPException(404, f"trip {trip_id!r} is not running or due in the next hour")
        t = _records(live.head(1))[0]
        version = latest_version(engine, at=service_day_reference(t["service_date"]) + 12 * 3600) or _version(engine)
        st = _q(
            engine,
            "SELECT st.stop_sequence, st.stop_id, st.arrival_s, s.stop_name, s.stop_lat, s.stop_lon "
            "FROM gtfs_stop_times st LEFT JOIN gtfs_stops s "
            "ON s.schedule_version = st.schedule_version AND s.stop_id = st.stop_id "
            "WHERE st.schedule_version = :v AND st.trip_id = :t ORDER BY st.stop_sequence",
            v=version,
            t=trip_id,
        )
        board = _q(engine, "SELECT stop_sequence, official_at, p50_at FROM live_board WHERE trip_id = :t", t=trip_id)
        due = board.set_index("stop_sequence")
        passed = {int(k): v for k, v in json.loads(t["passed"] or "{}").items()}
        skipped = set(json.loads(t["skipped"] or "[]"))
        ref = service_day_reference(t["service_date"])
        stops = []
        for r in _records(st):
            seq = int(r["stop_sequence"])
            if t["status"] == "cancelled":
                state = "cancelled"
            elif seq in passed:
                state = "passed"
            elif seq in skipped:
                state = "skipped"
            else:
                state = "upcoming"
            official = _clean(due.at[seq, "official_at"]) if seq in due.index and state == "upcoming" else None
            likely = _clean(due.at[seq, "p50_at"]) if seq in due.index and state == "upcoming" else None
            stops.append({
                **{k: r[k] for k in ("stop_sequence", "stop_id", "stop_name", "stop_lat", "stop_lon")},
                "scheduled_at": ref + int(r["arrival_s"]),
                "state": state,
                "passed_at": passed.get(seq),
                "official_at": official,
                "likely_at": likely if likely is not None else official,
            })  # fmt: skip
        shape = _q(
            engine,
            "SELECT s.coords FROM gtfs_trips tr JOIN gtfs_shapes s "
            "ON s.schedule_version = tr.schedule_version AND s.shape_id = tr.shape_id "
            "WHERE tr.schedule_version = :v AND tr.trip_id = :t",
            v=version,
            t=trip_id,
        )
        _cache(response, 20)
        return {
            **t,
            "route": t["route_short_name"],
            "off_route": bool(t["off_route"]),
            "stale": now - t["made_at"] > BOARD_STALE_S,
            "stops": stops,
            "shape": json.loads(shape["coords"].iloc[0]) if len(shape) else [],
            "attribution": ATTRIBUTION,
        }

    @get("/v1/alerts", response_model=S.Alerts, tags=["live"])
    def alerts(engine: EngineDep, response: Response) -> dict:
        """Live disruption picture from our own data, grouped by route: trips cancelled in
        the feed, buses on a diversion, trips likely not to run, and buses 10+ minutes late."""
        now = now_epoch()
        df = _q(engine, "SELECT * FROM live_trips WHERE status != 'finished'")
        reason = pd.Series(None, index=df.index, dtype=object)
        reason[(df["delay_s"] >= VERY_LATE_S) & (df["status"] == "running")] = "very_late"
        reason[(df["noshow_prob"] >= 0.3) & (df["status"] == "not_started")] = "at_risk"
        reason[df["status"] == "diverted"] = "diverted"
        reason[df["status"] == "cancelled"] = "cancelled"
        df = df.assign(reason=reason).dropna(subset=["reason"])
        df = df[(df["reason"] != "cancelled") | (df["end_at"] >= now - RECENT_S)]  # as long as the board shows it
        out = []
        for name, g in df.groupby("route_short_name"):
            n = g["reason"].value_counts()
            out.append({
                "route": name,
                **{k: int(n.get(k, 0)) for k in ("cancelled", "diverted", "at_risk", "very_late")},
                "trips": _records(g.sort_values("start_at")[
                    ["trip_id", "headsign", "start_at", "status", "reason", "delay_s", "noshow_prob"]
                ]),
            })  # fmt: skip
        out.sort(key=lambda a: -(a["cancelled"] * 2 + a["diverted"] * 2 + a["at_risk"] + a["very_late"]))
        _cache(response, 30)
        return {"now": now, "routes": out, "attribution": ATTRIBUTION}

    @get("/v1/accuracy", response_model=S.Accuracy, tags=["trust"])
    def accuracy(engine: EngineDep, response: Response, days: Annotated[int, Query(ge=1, le=365)] = 30) -> dict:
        """How our live predictions did against what actually happened, graded every morning."""
        daily = _q(engine, "SELECT * FROM accuracy_daily WHERE service_date >= :d ORDER BY service_date",
                   d=_since(days))  # fmt: skip
        card = _q(
            engine,
            "SELECT model_version, kind, created_at, card FROM model_registry WHERE promoted ORDER BY created_at DESC",
        )
        models = {}
        for r in _records(card):
            if r["kind"] not in models:
                c = json.loads(r["card"] or "{}")
                models[r["kind"]] = {
                    "version": r["model_version"],
                    "created_at": r["created_at"],
                    "test_days": c.get("test_days"),
                    "evaluation": c.get("evaluation", {}).get("overall"),
                    "headline": c.get("evaluation", {}).get("headline_10_30min"),
                    "top_features": c.get("feature_importance_gain"),
                }
        overall = daily[daily["horizon"] == "all"]
        n = overall["n"].sum()
        summary = None
        if n:
            w = overall["n"] / n
            summary = {
                "n": int(n),
                "mae_official_s": float((overall["mae_official_s"] * w).sum()),
                "mae_ours_s": float((overall["mae_ours_s"] * w).sum()),
                "coverage": float((overall["coverage"] * w).sum()),
            }
        _cache(response, 600)
        return {"days": days, "summary": summary, "daily": _records(daily), "models": models}


def _arrival(r: dict, stop_names: pd.Series, walk_min: float | None) -> dict:
    """One live_board row as the rider sees it."""
    upcoming = r["status"] in UPCOMING
    likely = (r["p50_at"] if r["p50_at"] is not None else r["official_at"]) if upcoming else None
    official_source = None
    if r["official_at"] is not None:
        official_source = "realtime" if r["official_at"] != r["scheduled_at"] or r["vehicle_id"] else "timetable"
    last = r["last_stop_id"]
    row = {
        "trip_id": r["trip_id"],
        "route_id": r["route_id"],
        "route": r["route_short_name"],
        "headsign": r["headsign"],
        "status": r["status"],
        "delay_s": r["delay_s"],
        "scheduled_at": r["scheduled_at"],
        "official_at": r["official_at"],
        "official_source": official_source,
        "likely_at": likely,
        "early_at": r["p10_at"] if upcoming else None,
        "late_at": r["p90_at"] if upcoming else None,
        "passed_at": r["passed_at"],
        "noshow_prob": r["noshow_prob"],
        "noshow_risk": risk_label(r["noshow_prob"]),
        "tracked": bool(r["has_gps"]) and r["vehicle_lat"] is not None,
        "vehicle_id": r["vehicle_id"],
        "position": {
            "lat": r["vehicle_lat"],
            "lon": r["vehicle_lon"],
            "ping_at": r["ping_at"],
            "last_stop_id": last,
            "last_stop_name": _clean(stop_names.get(last)) if last else None,
            "stops_away": r["stops_away"] if upcoming else None,
            "off_route": bool(r["off_route"]),
        },
        "leave_by_at": None,
    }
    if walk_min is not None and upcoming:
        edge = r["p10_at"] if r["p10_at"] is not None else r["official_at"]
        row["leave_by_at"] = int(edge - walk_min * 60)
    return row


def _natural(s: pd.Series) -> pd.Series:
    """Route names in human order: 4, 7, 13, 39, 39A, 145, C1, E1, N4 ..."""
    num = s.str.extract(r"^(\d+)")[0].astype(float).fillna(1e6)
    return num * 1000 + s.str.len() + s.map(lambda x: sum(ord(c) for c in x) / 1e4)


def _summarise(g: pd.DataFrame) -> dict:
    if g.empty:
        return {k: None for k in S.ScoreSummary.model_fields} | {"n_trips": 0}
    t = g[["n_trips", "n_noshow", "n_departures", "n_on_time", "n_bunched", "n_headways"]].sum()
    freq = g[g["frequent"].astype(bool) & g["ewt_s"].notna()]
    w = freq["n_headways"].clip(lower=1)
    rel = reliability_pct(t["n_trips"], t["n_noshow"], t["n_departures"], t["n_on_time"])
    return {
        "n_trips": int(t["n_trips"]),
        "reliability_pct": None if math.isnan(rel) else round(rel, 1),
        "noshow_pct": round(100 * t["n_noshow"] / t["n_trips"], 1) if t["n_trips"] else None,
        "on_time_pct": round(100 * t["n_on_time"] / t["n_departures"], 1) if t["n_departures"] else None,
        "bunched_pct": round(100 * t["n_bunched"] / t["n_headways"], 1) if t["n_headways"] else None,
        "ewt_s": round(float((freq["ewt_s"] * w).sum() / w.sum()), 0) if len(freq) else None,
        "mean_delay_s": round(float(g["mean_delay_s"].mean()), 0) if g["mean_delay_s"].notna().any() else None,
    }


app = create_app()
