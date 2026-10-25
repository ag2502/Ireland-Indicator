# common: contract

Shared plumbing. Every other Python package imports it, and it imports none of them.

## Provides

| Module | What callers can rely on |
|---|---|
| `config` | `get_settings()` reads `BT_*` variables from the environment or `.env`. The result is cached for the process. Comma-separated list settings are split. |
| `db` | Every table definition (the schema's source of truth) and `get_engine()`. `init_db()` is idempotent: it creates tables, sets up Timescale hypertables and compression on Postgres, and drops and recreates `live_board`/`live_trips` when their columns change. `insert_rows`/`upsert_rows` work on both SQLite and Postgres. |
| `timeutil` | `now_epoch()`, `to_utc()`, `to_local()`, `local_date()`, `parse_gtfs_time()`, `service_day_reference()` and `gtfs_time_to_epoch()`. These are the only place GTFS times past 24:00 and clock-change days are handled. |
| `schedule` | `latest_version()`, `active_service_ids()` and `Schedule`: an in-memory view of one schedule version for a few service dates (trips, stop times, shape lines, stop names and scheduled times). |
| `geo` | `to_itm()` (Irish Transverse Mercator, metres), `shape_line()`, `project_point()`, `project_stops_in_order()`, `point_at()` and `haversine_m()`. |
| `storage` | `put_bytes`, `get_bytes`, `exists` and `list_keys` under a local folder or `s3://bucket/prefix`, with the same behaviour for both. |
| `cli` | `bustrust <command>`. Command modules are imported lazily, so the API never loads LightGBM. |

## Invariants

- Every stored instant is UTC epoch seconds, in a column ending in `_at`.
- A service date is `YYYYMMDD`. A stop time of 25:10:00 belongs to the previous service date.
- `common` makes no network calls except through `storage` to S3.

## Changing it

When a table's columns change, update [docs/data-dictionary.md](../docs/data-dictionary.md)
and the contract of the package that writes that table. Ephemeral tables (`EPHEMERAL`)
can change shape freely. Every other table needs a migration on Postgres.
