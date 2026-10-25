# Data dictionary

The schema lives in [common/db.py](../common/db.py), and that file is the source of truth.
This page covers what each table means, who writes it, and how long its rows stay.

Conventions:
- Columns ending in `_at` are UTC epoch seconds (BIGINT).
- `service_date` is `YYYYMMDD`, as in GTFS. A bus at 25:10 belongs to the previous service date.
- `stop_sequence` is GTFS's order within a trip. It is not always 1, 2, 3.
- Every `gtfs_*` row carries a `schedule_version`, the first 16 hex characters of the zip's sha256.

## Ingestion (written by the poller)

| Table | One row per | Notes |
|---|---|---|
| `poll_log` | call to a feed | `ok`, HTTP status, entity count, bytes, latency and the feed header time. Weather and static loads log here too. Hypertable; compressed after 30 days. |
| `feed_gaps` | outage of a feed | `start_at`/`end_at`. Labels and scores skip trips that overlap a gap. |
| `trip_updates` | (poll, trip, stop) in a TripUpdate | `trip_rel` and `stop_rel` keep CANCELED and SKIPPED. Delays and absolute times are stored as sent. Hypertable; compressed after 7 days. |
| `vehicle_positions` | (poll, vehicle) | `ping_at` is the vehicle's own fix time and `polled_at` is when we received it. Features use `polled_at`; freshness uses `ping_at`. Hypertable; compressed after 7 days. |
| `weather_hourly` | hour | Open-Meteo. `source` is `forecast` or `archive`. |

The poller also writes every raw response, gzipped, to `BT_RAW_ARCHIVE`, so any table can
be rebuilt from source.

## Static schedule (written by `load-schedule`)

`schedule_versions` lists every zip we have loaded, with its feed dates. The other
schedule tables are the GTFS files restricted to Dublin Bus and Go-Ahead Dublin, with two
precomputed fields:

- `gtfs_stop_times.dist_m`: each stop's distance along its trip's shape.
- `gtfs_trips.first_dep_s` / `last_arr_s`: the first departure and last arrival, for
  quick time filters.

`gtfs_shapes.coords` is JSON `[[lon, lat], …]`.

## Labels (written nightly by `build-labels`)

| Table | One row per | Meaning |
|---|---|---|
| `arrivals` | (service_date, trip, stop) | When the bus really reached the stop. `method` is `gps_interp` (GPS pings projected onto the shape and interpolated), `prediction` (the last official time before the stop dropped out of the feed) or `stopwatch`. `uncertainty_s` is the gap between the pings either side. |
| `trip_outcomes` | (service_date, trip) | `noshow` is true when the trip was never seen with GPS in its window, or was cancelled in the feed. This matches the NTA's own definition of a ghost bus. |

## Live (rebuilt every minute by the scorer)

These two tables are replaced in full on each run. If their columns change, `init-db`
drops and recreates them rather than migrating.

**`live_board`**: one row per (stop, bus) that a rider at that stop should know about.

| Column | Meaning |
|---|---|
| `scheduled_at` | Timetable time at this stop. |
| `official_at` | The NTA prediction, unaltered. Null when the trip is only in the timetable. |
| `p10_at`, `p50_at`, `p90_at` | Our range and median. Null until an arrival model is promoted. |
| `noshow_prob` | Chance the trip never comes. Set to 1 when the feed cancels it; set to 0 once it is moving on GPS. |
| `status` | `on_time`, `late` (120 s or more), `early` (60 s or more early), `scheduled`, `cancelled`, `skipped` or `departed`. |
| `delay_s` | How late the bus is now: from its GPS position if the fix is fresh, otherwise from the feed. |
| `stops_away`, `last_stop_id` | Where the bus is relative to this stop. |
| `vehicle_lat`, `vehicle_lon`, `ping_at` | Last GPS fix, if it is fresh (5 minutes or less). |
| `passed_at` | For `departed` rows: when it passed this stop. Null when only the feed says it has gone. |
| `off_route` | The last two fresh pings are both more than 150 m from the route line. |

**`live_trips`**: one row per trip that is on the road or due within the hour.

| Column | Meaning |
|---|---|
| `status` | `not_started`, `running`, `diverted`, `cancelled` or `finished`. |
| `lat`, `lon`, `bearing`, `ping_at` | Last fresh GPS fix. |
| `last_stop_id`, `next_stop_id` | Taken from GPS, or from the feed when the bus has no GPS. |
| `passed` | JSON `{stop_sequence: passed_at or null}` for every stop behind the bus. |
| `skipped` | JSON list of the stop sequences the feed says it will skip. |
| `start_at`, `end_at` | Scheduled first departure and last arrival. |

## Grading

| Table | Written by | Meaning |
|---|---|---|
| `predictions_log` | scorer | Board rows whose official horizon is near 2, 5, 10, 15, 20, 30, 45 or 60 minutes, kept so they can be graded the next morning. Hypertable; compressed after 7 days. |
| `accuracy_daily` | `accuracy` | Per service date and horizon bucket: MAE of the official time and of our median, the 90th percentile error of each, range coverage and mean width. The public accuracy page reads this. |
| `model_registry` | `train` | Every model trained, its card (JSON) and whether it was promoted. |

## Route reliability (written nightly by `scores`)

| Table | One row per | Meaning |
|---|---|---|
| `route_scores_daily` | (date, route, direction) | Frequent routes (headway of 12 minutes or less) are scored on excess wait time: `ewt_s = awt_actual_s - awt_sched_s`. Infrequent routes are scored on `n_on_time / n_departures`, where on time means between 1 minute early and 5 minutes late. Also records bunching and no-shows. |
| `route_heatmap` | (route, weekday, hour) | On-time share and median delay over the last `window_days`. |
| `segment_delays` | (route, direction, stop pair) | Median time lost against the schedule on that stretch. |
| `data_checks` | (run, check) | `ok`, `warn` or `fail`, with the value. See [pipeline/checks.py](../pipeline/checks.py). |

## Files outside the database

| Path (setting) | Holds |
|---|---|
| `BT_RAW_ARCHIVE` | Raw feed responses: `<feed>/YYYY/MM/DD/HHMMSS.pb.gz` (UTC). |
| `BT_STATIC_ARCHIVE` | Every static GTFS zip we have seen, named `<schedule_version>.zip`. |
| `BT_PARQUET_DIR` | Training examples per day: `arrival/<date>.parquet` and `noshow/<date>.parquet`. |
| `BT_MODEL_DIR` | Model bundles (JSON) and `registry.json`, which records the promoted version of each kind. |

The raw, static and model locations can each be a local folder or an `s3://bucket/prefix`.
The Parquet folder must be local; it can be rebuilt from the database with `features`.
