"""Hourly Dublin weather from Open-Meteo (free, no key, CC BY 4.0).

Runs hourly: fetches the last 2 days plus today's forecast and upserts. `--archive` back-
fills from the historical archive API. In simulated mode it writes the simulator's own
weather, so the rain feature matches the simulated world.
"""

from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta

import httpx
from sqlalchemy import Engine

from common import db
from common.config import get_settings
from common.timeutil import now_epoch

log = logging.getLogger(__name__)

FORECAST = "https://api.open-meteo.com/v1/forecast"
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
HOURLY = "temperature_2m,precipitation,wind_speed_10m"


def _rows(payload: dict, source: str) -> list[dict]:
    h = payload["hourly"]
    return [
        {"hour_at": int(t), "temp_c": temp, "precip_mm": rain, "wind_kmh": wind, "source": source}
        for t, temp, rain, wind in zip(
            h["time"], h["temperature_2m"], h["precipitation"], h["wind_speed_10m"], strict=True
        )
        if temp is not None
    ]


def fetch_recent(client: httpx.Client) -> list[dict]:
    s = get_settings()
    params = {
        "latitude": s.latitude,
        "longitude": s.longitude,
        "hourly": HOURLY,
        "past_days": 2,
        "forecast_days": 1,
        "timezone": "UTC",
        "timeformat": "unixtime",
    }
    resp = client.get(FORECAST, params=params)
    resp.raise_for_status()
    return _rows(resp.json(), "forecast")


def fetch_archive(client: httpx.Client, start: date, end: date) -> list[dict]:
    s = get_settings()
    params = {
        "latitude": s.latitude,
        "longitude": s.longitude,
        "hourly": HOURLY,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "timezone": "UTC",
        "timeformat": "unixtime",
    }
    resp = client.get(ARCHIVE, params=params)
    resp.raise_for_status()
    return _rows(resp.json(), "archive")


def simulated_rows(engine: Engine, start: date, end: date) -> list[dict]:
    """Hourly weather consistent with the simulator's rainy days."""
    import numpy as np

    from common.timeutil import service_day_reference
    from ingest.simulator import _seed, make_simulator

    sim = make_simulator(engine)
    rows = []
    d = start
    while d <= end:
        ds = d.strftime("%Y%m%d")
        try:
            rainy = sim.day(ds).rainy
        except (RuntimeError, KeyError, ValueError):
            rainy = False
        rng = np.random.default_rng(_seed(sim.seed, "weather", ds))
        ref = service_day_reference(ds)
        for h in range(24):
            rain = float(rng.gamma(1.2, 1.0)) if rainy and rng.random() < 0.6 else 0.0
            rows.append(
                {
                    "hour_at": ref + h * 3600,
                    "temp_c": round(9 + 3 * np.sin((h - 9) / 24 * 2 * np.pi), 1),
                    "precip_mm": round(rain, 1),
                    "wind_kmh": round(float(rng.uniform(8, 30)), 1),
                    "source": "simulated",
                }
            )
        d += timedelta(days=1)
    return rows


def run(argv: list[str] | None = None) -> int:
    """CLI: bustrust weather [--archive START END]"""
    p = argparse.ArgumentParser(prog="bustrust weather")
    p.add_argument("--archive", nargs=2, metavar=("START", "END"), help="YYYY-MM-DD YYYY-MM-DD")
    args = p.parse_args(argv)
    engine = db.init_db()
    s = get_settings()
    if s.feed_mode == "simulated":
        end = date.today()
        start = date.fromisoformat(args.archive[0]) if args.archive else end - timedelta(days=2)
        rows = simulated_rows(engine, start, end)
    else:
        with httpx.Client(timeout=30) as client:
            if args.archive:
                rows = fetch_archive(client, date.fromisoformat(args.archive[0]), date.fromisoformat(args.archive[1]))
            else:
                rows = fetch_recent(client)
    db.upsert_rows(engine, db.weather_hourly, rows, keys=["hour_at"])
    db.insert_rows(
        engine,
        db.poll_log,
        [{"polled_at": now_epoch(), "feed": "weather", "ok": True, "n_entities": len(rows)}],
    )
    log.info("weather: %d hourly rows upserted", len(rows))
    return 0
