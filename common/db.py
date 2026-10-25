"""Database schema and helpers. Works on SQLite (dev, tests) and Postgres + TimescaleDB.

Every instant is a BIGINT of UTC epoch seconds (column names end in `_at`). Service dates
are 'YYYYMMDD' strings, as in GTFS.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Engine,
    Float,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    event,
    text,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from common.config import get_settings

log = logging.getLogger(__name__)
metadata = MetaData()

# ---------------------------------------------------------------- ingestion
poll_log = Table(
    "poll_log", metadata,
    Column("polled_at", BigInteger, nullable=False),
    Column("feed", String(32), nullable=False),  # trip_updates | vehicles | static | weather
    Column("key_index", Integer),
    Column("status_code", Integer),
    Column("ok", Boolean, nullable=False),
    Column("n_entities", Integer),
    Column("n_bytes", Integer),
    Column("latency_ms", Integer),
    Column("header_at", BigInteger),  # FeedHeader.timestamp
    Column("error", Text),
    Index("ix_poll_log_at", "polled_at"),
)

feed_gaps = Table(
    "feed_gaps", metadata,
    Column("feed", String(32), nullable=False),
    Column("start_at", BigInteger, nullable=False),
    Column("end_at", BigInteger, nullable=False),
    Column("reason", Text),
    Index("ix_feed_gaps_start", "start_at"),
)

vehicle_positions = Table(
    "vehicle_positions", metadata,
    Column("polled_at", BigInteger, nullable=False),
    Column("ping_at", BigInteger),  # VehiclePosition.timestamp
    Column("vehicle_id", String(64)),
    Column("trip_id", String(64)),
    Column("route_id", String(64)),
    Column("start_date", String(8)),
    Column("start_time", String(8)),
    Column("direction_id", Integer),
    Column("lat", Float),
    Column("lon", Float),
    Column("bearing", Float),
    Column("speed", Float),
    Index("ix_vp_trip", "start_date", "trip_id", "ping_at"),
    Index("ix_vp_polled", "polled_at"),
)

trip_updates = Table(
    "trip_updates", metadata,
    Column("polled_at", BigInteger, nullable=False),
    Column("trip_at", BigInteger),  # TripUpdate.timestamp
    Column("trip_id", String(64), nullable=False),
    Column("route_id", String(64)),
    Column("start_date", String(8)),
    Column("start_time", String(8)),
    Column("direction_id", Integer),
    Column("vehicle_id", String(64)),
    Column("trip_rel", String(16)),  # SCHEDULED | ADDED | CANCELED | ...
    Column("stop_id", String(64)),
    Column("stop_sequence", Integer),
    Column("stop_rel", String(16)),  # SCHEDULED | SKIPPED | NO_DATA
    Column("arrival_delay", Integer),
    Column("arrival_at", BigInteger),
    Column("departure_delay", Integer),
    Column("departure_at", BigInteger),
    Index("ix_tu_trip", "start_date", "trip_id", "polled_at"),
    Index("ix_tu_polled", "polled_at"),
)

weather_hourly = Table(
    "weather_hourly", metadata,
    Column("hour_at", BigInteger, primary_key=True),
    Column("temp_c", Float),
    Column("precip_mm", Float),
    Column("wind_kmh", Float),
    Column("source", String(16)),  # forecast | archive
)

# ---------------------------------------------------------------- static schedule
schedule_versions = Table(
    "schedule_versions", metadata,
    Column("schedule_version", String(64), primary_key=True),  # sha256[:16] of the zip
    Column("feed_version", String(64)),
    Column("feed_start", String(8)),
    Column("feed_end", String(8)),
    Column("fetched_at", BigInteger, nullable=False),
    Column("loaded_at", BigInteger),
    Column("n_trips", Integer),
)

gtfs_routes = Table(
    "gtfs_routes", metadata,
    Column("schedule_version", String(64), nullable=False),
    Column("route_id", String(64), nullable=False),
    Column("agency_id", String(16)),
    Column("route_short_name", String(32)),
    Column("route_long_name", Text),
    Column("route_type", Integer),
    Index("ix_routes", "schedule_version", "route_id", unique=True),
)

gtfs_stops = Table(
    "gtfs_stops", metadata,
    Column("schedule_version", String(64), nullable=False),
    Column("stop_id", String(64), nullable=False),
    Column("stop_code", String(32)),
    Column("stop_name", Text),
    Column("stop_lat", Float),
    Column("stop_lon", Float),
    Index("ix_stops", "schedule_version", "stop_id", unique=True),
)

gtfs_trips = Table(
    "gtfs_trips", metadata,
    Column("schedule_version", String(64), nullable=False),
    Column("trip_id", String(64), nullable=False),
    Column("route_id", String(64)),
    Column("service_id", String(64)),
    Column("direction_id", Integer),
    Column("shape_id", String(64)),
    Column("block_id", String(64)),
    Column("trip_headsign", Text),
    Column("first_dep_s", Integer),  # seconds of first departure, for quick filtering
    Column("last_arr_s", Integer),
    Index("ix_trips", "schedule_version", "trip_id", unique=True),
    Index("ix_trips_route", "schedule_version", "route_id"),
)

gtfs_stop_times = Table(
    "gtfs_stop_times", metadata,
    Column("schedule_version", String(64), nullable=False),
    Column("trip_id", String(64), nullable=False),
    Column("stop_sequence", Integer, nullable=False),
    Column("stop_id", String(64), nullable=False),
    Column("arrival_s", Integer),  # seconds after service-day reference
    Column("departure_s", Integer),
    Column("dist_m", Float),  # distance along the trip's shape, from shape projection
    Index("ix_st_trip", "schedule_version", "trip_id", "stop_sequence", unique=True),
    Index("ix_st_stop", "schedule_version", "stop_id"),
)

gtfs_shapes = Table(
    "gtfs_shapes", metadata,
    Column("schedule_version", String(64), nullable=False),
    Column("shape_id", String(64), nullable=False),
    Column("coords", Text, nullable=False),  # JSON [[lon, lat], ...]
    Column("length_m", Float),
    Index("ix_shapes", "schedule_version", "shape_id", unique=True),
)

gtfs_calendar = Table(
    "gtfs_calendar", metadata,
    Column("schedule_version", String(64), nullable=False),
    Column("service_id", String(64), nullable=False),
    Column("days", String(7)),  # '1111100' Monday..Sunday
    Column("start_date", String(8)),
    Column("end_date", String(8)),
)

gtfs_calendar_dates = Table(
    "gtfs_calendar_dates", metadata,
    Column("schedule_version", String(64), nullable=False),
    Column("service_id", String(64), nullable=False),
    Column("date", String(8)),
    Column("exception_type", Integer),
)

# ---------------------------------------------------------------- derived
arrivals = Table(
    "arrivals", metadata,
    Column("service_date", String(8), nullable=False),
    Column("trip_id", String(64), nullable=False),
    Column("stop_sequence", Integer, nullable=False),
    Column("stop_id", String(64)),
    Column("route_id", String(64)),
    Column("direction_id", Integer),
    Column("schedule_version", String(64)),
    Column("scheduled_at", BigInteger),
    Column("actual_at", BigInteger),
    Column("method", String(16)),  # gps_interp | prediction | stopwatch
    Column("uncertainty_s", Integer),  # gap between bracketing pings, a label-noise proxy
    Index("ix_arr_key", "service_date", "trip_id", "stop_sequence", unique=True),
    Index("ix_arr_route", "route_id", "scheduled_at"),
)

trip_outcomes = Table(
    "trip_outcomes", metadata,
    Column("service_date", String(8), nullable=False),
    Column("trip_id", String(64), nullable=False),
    Column("route_id", String(64)),
    Column("direction_id", Integer),
    Column("scheduled_start_at", BigInteger),
    Column("ran", Boolean),  # seen with GPS in its window
    Column("cancelled_in_feed", Boolean),
    Column("noshow", Boolean),  # never seen with GPS, or cancelled
    Column("first_seen_at", BigInteger),  # first poll mentioning the trip at all
    Index("ix_to_key", "service_date", "trip_id", unique=True),
)

predictions_log = Table(
    "predictions_log", metadata,
    Column("made_at", BigInteger, nullable=False),
    Column("service_date", String(8)),
    Column("trip_id", String(64)),
    Column("stop_id", String(64)),
    Column("stop_sequence", Integer),
    Column("route_id", String(64)),
    Column("scheduled_at", BigInteger),
    Column("official_at", BigInteger),
    Column("p10_at", BigInteger),
    Column("p50_at", BigInteger),
    Column("p90_at", BigInteger),
    Column("noshow_prob", Float),
    Column("model_version", String(64)),
    Index("ix_pl_made", "made_at"),
    Index("ix_pl_trip", "service_date", "trip_id", "stop_sequence"),
)

# Replaced wholesale after every scoring run; the API's hot path reads only this.
live_board = Table(
    "live_board", metadata,
    Column("made_at", BigInteger, nullable=False),
    Column("stop_id", String(64), nullable=False),
    Column("service_date", String(8)),
    Column("trip_id", String(64)),
    Column("stop_sequence", Integer),
    Column("route_id", String(64)),
    Column("route_short_name", String(32)),
    Column("headsign", Text),
    Column("vehicle_id", String(64)),
    Column("scheduled_at", BigInteger),
    Column("official_at", BigInteger),
    Column("p10_at", BigInteger),
    Column("p50_at", BigInteger),
    Column("p90_at", BigInteger),
    Column("noshow_prob", Float),
    Column("has_gps", Boolean),
    Column("model_version", String(64)),
    Index("ix_lb_stop", "stop_id", "official_at"),
)

route_scores_daily = Table(
    "route_scores_daily", metadata,
    Column("service_date", String(8), nullable=False),
    Column("route_id", String(64), nullable=False),
    Column("direction_id", Integer, nullable=False),
    Column("route_short_name", String(32)),
    Column("frequent", Boolean),
    Column("n_trips", Integer),
    Column("n_noshow", Integer),
    Column("n_departures", Integer),
    Column("n_on_time", Integer),
    Column("n_bunched", Integer),
    Column("n_headways", Integer),
    Column("awt_actual_s", Float),
    Column("awt_sched_s", Float),
    Column("ewt_s", Float),
    Column("mean_delay_s", Float),
    Index("ix_rsd", "service_date", "route_id", "direction_id", unique=True),
)

route_heatmap = Table(
    "route_heatmap", metadata,
    Column("route_id", String(64), nullable=False),
    Column("weekday", Integer, nullable=False),  # 0 = Monday
    Column("hour", Integer, nullable=False),
    Column("n", Integer),
    Column("on_time_share", Float),
    Column("median_delay_s", Float),
    Column("window_days", Integer),
    Column("computed_at", BigInteger),
)

segment_delays = Table(
    "segment_delays", metadata,
    Column("route_id", String(64), nullable=False),
    Column("direction_id", Integer),
    Column("from_stop_id", String(64)),
    Column("to_stop_id", String(64)),
    Column("from_seq", Integer),
    Column("n", Integer),
    Column("median_lost_s", Float),  # actual run time minus scheduled run time
    Column("computed_at", BigInteger),
)

data_checks = Table(
    "data_checks", metadata,
    Column("run_at", BigInteger, nullable=False),
    Column("check", String(64), nullable=False),
    Column("status", String(8), nullable=False),  # ok | warn | fail
    Column("value", Float),
    Column("detail", Text),
)

model_registry = Table(
    "model_registry", metadata,
    Column("model_version", String(64), primary_key=True),
    Column("kind", String(16)),  # arrival | noshow
    Column("created_at", BigInteger),
    Column("promoted", Boolean),
    Column("path", Text),
    Column("card", Text),  # JSON model card
)

accuracy_daily = Table(
    "accuracy_daily", metadata,
    Column("service_date", String(8), nullable=False),
    Column("horizon", String(8), nullable=False),  # 0-5, 5-10, ...
    Column("n", Integer),
    Column("mae_official_s", Float),
    Column("mae_ours_s", Float),
    Column("p90_abs_official_s", Float),
    Column("p90_abs_ours_s", Float),
    Column("coverage", Float),
    Column("mean_width_s", Float),
    Column("model_version", String(64)),
    Index("ix_acc", "service_date", "horizon", unique=True),
)

# Tables that are time series in Postgres: (table, time column, compress after days)
HYPERTABLES = [
    ("vehicle_positions", "polled_at", 7),
    ("trip_updates", "polled_at", 7),
    ("predictions_log", "made_at", 7),
    ("poll_log", "polled_at", 30),
]


@lru_cache
def get_engine(url: str | None = None) -> Engine:
    url = url or get_settings().database_url
    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, connect_args={"timeout": 30})

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn: Any, _: Any) -> None:
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")  # poller writes while the API reads
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()

        return engine
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)


def init_db(engine: Engine | None = None) -> Engine:
    engine = engine or get_engine()
    metadata.create_all(engine)
    if engine.dialect.name == "postgresql":
        _setup_timescale(engine)
    return engine


def _setup_timescale(engine: Engine) -> None:
    with engine.begin() as conn:
        has = conn.execute(
            text("SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'")
        ).scalar()
        if not has:
            log.warning("TimescaleDB not available; using plain Postgres tables")
            return
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
        conn.execute(text(
            "CREATE OR REPLACE FUNCTION unix_now() RETURNS BIGINT LANGUAGE SQL STABLE "
            "AS $$ SELECT extract(epoch FROM now())::BIGINT $$"
        ))
        for table, col, compress_days in HYPERTABLES:
            conn.execute(text(
                f"SELECT create_hypertable('{table}', '{col}', chunk_time_interval => 86400, "
                "if_not_exists => TRUE, migrate_data => TRUE)"
            ))
            conn.execute(text(f"SELECT set_integer_now_func('{table}', 'unix_now', TRUE)"))
            conn.execute(text(f"ALTER TABLE {table} SET (timescaledb.compress)"))
            conn.execute(text(
                f"SELECT add_compression_policy('{table}', BIGINT '{compress_days * 86400}', "
                "if_not_exists => TRUE)"
            ))


def insert_rows(
    engine: Engine,
    table: Table,
    rows: Sequence[Mapping[str, Any]] | Iterable[Mapping[str, Any]],
    chunk: int = 5000,
    ignore_conflicts: bool = False,
) -> int:
    """Bulk insert in chunks. With ignore_conflicts, rows clashing on a unique index are
    skipped (re-running a job is then idempotent)."""
    rows = list(rows)
    if not rows:
        return 0
    if ignore_conflicts:
        ins = pg_insert if engine.dialect.name == "postgresql" else sqlite_insert
        stmt: Any = ins(table).on_conflict_do_nothing()
    else:
        stmt = table.insert()
    with engine.begin() as conn:
        for i in range(0, len(rows), chunk):
            conn.execute(stmt, rows[i : i + chunk])
    return len(rows)


def upsert_rows(
    engine: Engine, table: Table, rows: Sequence[Mapping[str, Any]], keys: list[str]
) -> int:
    """Insert or replace on the given unique key columns."""
    rows = list(rows)
    if not rows:
        return 0
    ins = pg_insert if engine.dialect.name == "postgresql" else sqlite_insert
    stmt: Any = ins(table)
    update_cols = {c.name: stmt.excluded[c.name] for c in table.columns if c.name not in keys}
    stmt = stmt.on_conflict_do_update(index_elements=keys, set_=update_cols)
    with engine.begin() as conn:
        for i in range(0, len(rows), 2000):
            conn.execute(stmt, rows[i : i + 2000])
    return len(rows)


def replace_table(engine: Engine, table: Table, rows: Sequence[Mapping[str, Any]]) -> int:
    """Swap a table's contents in one transaction (readers see old or new, never empty)."""
    with engine.begin() as conn:
        conn.execute(table.delete())
        if rows:
            conn.execute(table.insert(), list(rows))
    return len(rows)


def bulk_load_df(engine: Engine, table: Table, df: Any, chunk: int = 200_000) -> int:
    """Fast append of a pandas DataFrame: COPY on Postgres, executemany on SQLite.
    Columns not in the frame are left NULL."""
    if df is None or len(df) == 0:
        return 0
    cols = [c.name for c in table.columns if c.name in df.columns]
    frame = df[cols].astype(object).where(df[cols].notna(), None)
    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        if engine.dialect.name == "postgresql":
            with cur.copy(f"COPY {table.name} ({', '.join(cols)}) FROM STDIN") as copy:  # type: ignore[attr-defined]
                for rec in frame.itertuples(index=False, name=None):
                    copy.write_row(rec)
        else:
            sql = f"INSERT INTO {table.name} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
            values = frame.itertuples(index=False, name=None)
            batch: list[tuple] = []
            for rec in values:
                batch.append(rec)
                if len(batch) >= chunk:
                    cur.executemany(sql, batch)
                    batch = []
            if batch:
                cur.executemany(sql, batch)
        raw.commit()
    finally:
        raw.close()
    return len(frame)


def run_init(argv: list[str] | None = None) -> int:
    init_db()
    log.info("database ready at %s", get_engine().url.render_as_string(hide_password=True))
    return 0
