# ingest: contract

Gets data in from outside. It holds the only code that calls the NTA.

## Reads

| Source | By | Limits |
|---|---|---|
| NTA GTFS-Realtime `TripUpdates` and `Vehicles` (`BT_NTA_BASE_URL`) | `poller` | One call per key per 60 s. With one key it alternates feeds. Two keys run at once only when `BT_USE_BOTH_KEYS=true`, after the NTA agrees in writing. |
| NTA static GTFS zip (`BT_STATIC_GTFS_URL`) | `static_loader` | Once a day. |
| Open-Meteo forecast and archive | `weather` | Once an hour. No key needed. |
| `calendar_ie.csv` | `calendar_ie` | A local file, maintained by hand. |

With `BT_FEED_MODE=simulated`, the poller reads `simulator` instead of the NTA and makes no
external calls.

## Writes

| Table / location | Writer | Shape |
|---|---|---|
| `trip_updates`, `vehicle_positions` | `poller` via `feeds` | One row per (poll, trip, stop) and one per (poll, vehicle). Values are as sent, with `polled_at` added. |
| `poll_log`, `feed_gaps` | `poller` | Every call, and every gap longer than twice a feed's normal refresh interval. |
| raw archive | `poller` | `<feed>/YYYY/MM/DD/HHMMSS.pb.gz`, the exact response bytes. |
| `schedule_versions`, `gtfs_*` | `static_loader` | All rows of one version, stamped with `schedule_version`. An unchanged zip is skipped. |
| static archive | `static_loader` | `<schedule_version>.zip` |
| `weather_hourly` | `weather` | Upserted by `hour_at`. |

## Guarantees

- No other package may call the NTA. The API, scorer and web app read our tables only.
- On a 429 or 5xx response the poller backs off exponentially and records a gap. It never retries faster than the policy allows.
- After each successful poll the heartbeat URL is pinged, if one is set.
- The simulator is deterministic in (seed, service date, trip), so repeated polls see one consistent world, and tests can check labels against its hidden truth.
