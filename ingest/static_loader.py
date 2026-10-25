"""Download, version and load the NTA static GTFS (GTFS_Realtime.zip).

The NTA reissues the zip often and warns that route, trip and stop IDs can change between
versions, and that realtime data only matches its own version. So every distinct zip is
kept, gets a `schedule_version` (first 16 hex chars of its sha256), and is loaded with that
version stamped on every row. Unchanged zips are skipped.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import zipfile
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
from sqlalchemy import Engine, func, select

from common import db, geo, storage
from common.config import get_settings
from common.timeutil import now_epoch

log = logging.getLogger(__name__)


def download(url: str | None = None) -> bytes:
    url = url or get_settings().static_gtfs_url
    with httpx.Client(timeout=300, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.content


def version_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _read(zf: zipfile.ZipFile, name: str, **kw) -> pd.DataFrame:
    with zf.open(name) as f:
        return pd.read_csv(f, dtype=str, keep_default_na=False, **kw)


def _secs(col: pd.Series) -> pd.Series:
    parts = col.str.split(":", expand=True).astype(int)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def parse(
    data: bytes, agency_ids: list[str], route_short_names: list[str] | None = None
) -> dict[str, pd.DataFrame]:
    """Parse the zip into frames shaped like the gtfs_* tables (without schedule_version)."""
    zf = zipfile.ZipFile(io.BytesIO(data))
    routes = _read(zf, "routes.txt")
    routes = routes[routes["agency_id"].isin(agency_ids)]
    if route_short_names:
        routes = routes[routes["route_short_name"].isin(route_short_names)]
    routes = routes.assign(route_type=routes["route_type"].astype(int))

    trips = _read(zf, "trips.txt")
    trips = trips[trips["route_id"].isin(set(routes["route_id"]))].copy()
    for c in ("block_id", "shape_id", "trip_headsign", "direction_id"):
        if c not in trips:
            trips[c] = ""
    trips["direction_id"] = pd.to_numeric(trips["direction_id"], errors="coerce").fillna(0)
    trips["direction_id"] = trips["direction_id"].astype(int)
    keep_trips = set(trips["trip_id"])

    # stop_times is the big one (~10M rows nationally): stream it and keep our trips only
    chunks = []
    usecols = ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"]
    with zf.open("stop_times.txt") as f:
        for chunk in pd.read_csv(f, dtype=str, usecols=usecols, chunksize=1_000_000):
            chunk = chunk[chunk["trip_id"].isin(keep_trips)]
            if len(chunk):
                chunks.append(chunk)
    st = pd.concat(chunks, ignore_index=True)
    st["arrival_s"] = _secs(st["arrival_time"])
    st["departure_s"] = _secs(st["departure_time"])
    st["stop_sequence"] = st["stop_sequence"].astype(int)
    st = st.drop(columns=["arrival_time", "departure_time"]).sort_values(
        ["trip_id", "stop_sequence"], ignore_index=True
    )

    span = st.groupby("trip_id").agg(first_dep_s=("departure_s", "min"), last_arr_s=("arrival_s", "max"))
    trips = trips.join(span, on="trip_id")

    stops = _read(zf, "stops.txt")
    stops = stops[stops["stop_id"].isin(set(st["stop_id"]))].copy()
    stops["stop_lat"] = stops["stop_lat"].astype(float)
    stops["stop_lon"] = stops["stop_lon"].astype(float)

    keep_shapes = set(trips["shape_id"]) - {""}
    shape_chunks = []
    with zf.open("shapes.txt") as f:
        for chunk in pd.read_csv(f, dtype=str, chunksize=1_000_000):
            chunk = chunk[chunk["shape_id"].isin(keep_shapes)]
            if len(chunk):
                shape_chunks.append(chunk)
    shp = pd.concat(shape_chunks, ignore_index=True) if shape_chunks else pd.DataFrame(
        columns=["shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence"]
    )
    shp["shape_pt_sequence"] = shp["shape_pt_sequence"].astype(int)
    shp = shp.sort_values(["shape_id", "shape_pt_sequence"])
    shapes_rows = []
    lines = {}
    for shape_id, g in shp.groupby("shape_id", sort=False):
        coords = np.column_stack([g["shape_pt_lon"].astype(float), g["shape_pt_lat"].astype(float)])
        line = geo.shape_line(coords)
        lines[shape_id] = line
        shapes_rows.append({
            "shape_id": shape_id,
            "coords": json.dumps(np.round(coords, 6).tolist(), separators=(",", ":")),
            "length_m": round(line.length, 1),
        })
    shapes = pd.DataFrame(shapes_rows, columns=["shape_id", "coords", "length_m"])

    st["dist_m"] = _stop_distances(st, trips, stops, lines)

    calendar = _read(zf, "calendar.txt") if "calendar.txt" in zf.namelist() else pd.DataFrame()
    if len(calendar):
        days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        calendar["days"] = calendar[days].agg("".join, axis=1)
        calendar = calendar[["service_id", "days", "start_date", "end_date"]]
    cal_dates = (
        _read(zf, "calendar_dates.txt") if "calendar_dates.txt" in zf.namelist() else pd.DataFrame()
    )
    if len(cal_dates):
        cal_dates["exception_type"] = cal_dates["exception_type"].astype(int)

    feed_info = _read(zf, "feed_info.txt") if "feed_info.txt" in zf.namelist() else pd.DataFrame()
    return {
        "routes": routes, "trips": trips, "stop_times": st, "stops": stops, "shapes": shapes,
        "calendar": calendar, "calendar_dates": cal_dates, "feed_info": feed_info,
    }


def _stop_distances(st: pd.DataFrame, trips: pd.DataFrame, stops: pd.DataFrame, lines: dict) -> np.ndarray:
    """Distance along the shape for every stop_times row. Trips sharing a shape and a stop
    pattern are projected once (a few thousand patterns versus millions of rows)."""
    shape_of = trips.set_index("trip_id")["shape_id"]
    pos = stops.set_index("stop_id")[["stop_lon", "stop_lat"]]
    out = np.full(len(st), np.nan)
    trip_ids = st["trip_id"].to_numpy()
    bounds = np.flatnonzero(np.r_[True, trip_ids[1:] != trip_ids[:-1], True])
    cache: dict[tuple, list[float]] = {}
    stop_ids = st["stop_id"].to_numpy()
    for a, b in zip(bounds[:-1], bounds[1:], strict=True):
        shape_id = shape_of.get(trip_ids[a], "")
        line = lines.get(shape_id)
        if line is None:
            continue
        pattern = (shape_id, tuple(stop_ids[a:b]))
        if pattern not in cache:
            ll = pos.loc[list(pattern[1])]
            cache[pattern] = geo.project_stops_in_order(
                line, list(zip(ll["stop_lon"], ll["stop_lat"], strict=True))
            )
        out[a:b] = cache[pattern]
    return out


def load(engine: Engine, data: bytes, agency_ids: list[str] | None = None,
         route_short_names: list[str] | None = None) -> str | None:
    """Load a zip if its version is new. Returns the version loaded, or None if skipped."""
    version = version_of(data)
    with engine.connect() as conn:
        exists = conn.execute(
            select(db.schedule_versions.c.loaded_at).where(
                db.schedule_versions.c.schedule_version == version
            )
        ).first()
    if exists and exists[0]:
        log.info("schedule %s already loaded", version)
        return None
    frames = parse(data, agency_ids or get_settings().agency_ids, route_short_names)
    fi = frames["feed_info"]
    meta = {
        "schedule_version": version,
        "feed_version": fi["feed_version"].iloc[0] if len(fi) and "feed_version" in fi else None,
        "feed_start": fi["feed_start_date"].iloc[0] if len(fi) and "feed_start_date" in fi else None,
        "feed_end": fi["feed_end_date"].iloc[0] if len(fi) and "feed_end_date" in fi else None,
        "fetched_at": now_epoch(),
        "n_trips": len(frames["trips"]),
    }
    targets = {
        "routes": db.gtfs_routes, "trips": db.gtfs_trips, "stop_times": db.gtfs_stop_times,
        "stops": db.gtfs_stops, "shapes": db.gtfs_shapes, "calendar": db.gtfs_calendar,
        "calendar_dates": db.gtfs_calendar_dates,
    }
    for name, table in targets.items():
        frame = frames[name].assign(schedule_version=version)
        n = db.bulk_load_df(engine, table, frame)
        log.info("schedule %s: %s rows -> %s", version, n, table.name)
    # set last, so a crash mid-load is retried; strictly increasing so the newest load wins
    with engine.connect() as conn:
        prev = conn.execute(select(func.max(db.schedule_versions.c.loaded_at))).scalar() or 0
    meta["loaded_at"] = max(now_epoch(), prev + 1)
    db.upsert_rows(engine, db.schedule_versions, [meta], keys=["schedule_version"])
    return version


def run(argv: list[str] | None = None) -> int:
    """CLI: bustrust load-schedule [--zip PATH] [--routes 39A,46A]"""
    p = argparse.ArgumentParser(prog="bustrust load-schedule")
    p.add_argument("--zip", help="load a local zip instead of downloading")
    p.add_argument("--routes", help="comma-separated route short names (dev subset)")
    args = p.parse_args(argv)
    s = get_settings()
    data = Path(args.zip).read_bytes() if args.zip else download()
    version = version_of(data)
    key = f"{version}.zip"
    if not storage.exists(s.static_archive, key):
        storage.put_bytes(s.static_archive, key, data)  # keep every version
    engine = db.init_db()
    routes = args.routes.split(",") if args.routes else None
    loaded = load(engine, data, route_short_names=routes)
    log.info("schedule %s %s", version, "loaded" if loaded else "unchanged")
    return 0
