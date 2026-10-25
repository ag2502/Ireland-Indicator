# pipeline: contract

Nightly batch work. It turns raw feed rows into labels, features and route scores. Nothing
here runs on a rider's request.

## Commands

| Command | Reads | Writes |
|---|---|---|
| `build-labels [--date D \| --days N]` | `vehicle_positions`, `trip_updates`, schedule, `feed_gaps` | `arrivals`, `trip_outcomes` (upserted per service date) |
| `features [--date D \| --days N]` | labels, realtime tables, schedule, `weather_hourly`, calendar | `BT_PARQUET_DIR/arrival/<date>.parquet`, `BT_PARQUET_DIR/noshow/<date>.parquet` |
| `scores [--days N] [--window 28]` | `arrivals`, `trip_outcomes`, schedule | `route_scores_daily`, `route_heatmap`, `segment_delays` |
| `checks` | `poll_log`, `arrivals`, realtime tables | `data_checks`. Exits 1 on any `fail`. |
| `stopwatch FILE.csv` | a CSV of arrivals timed by hand, plus `arrivals` | a report on stdout |

## Guarantees

- **As-of features.** `features.py` builds every feature from what had been received
  by the poll time T: `polled_at`, not `ping_at`, and never a later row. The live scorer
  calls the same functions, so training and serving cannot drift apart.
- **Official times as riders saw them.** `official.py` rebuilds them by GTFS-Realtime
  delay propagation: a stop without its own update inherits the delay of the nearest
  update before it.
- **Labels say how they were made.** Each arrival records its `method` and
  `uncertainty_s`. Trips that overlap a feed gap get no no-show label.
- **Reruns are safe.** Running any command again for the same date replaces that
  date's rows.

## Thresholds

The checks thresholds are in `checks.CHECKS`. The scoring rules are in the
`scores` module docstring: frequent services (12 minutes or less) are scored on excess
wait time, the rest on the share of departures between 1 minute early and 5 minutes late.
