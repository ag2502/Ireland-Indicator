# BusTrust: working notes for agents

Dublin bus reliability predictor. Each package has its own `CONTRACT.md` describing what it
reads and writes; keep it current when a table or file shape changes.

## Rules that must not break

- **Only the poller calls the NTA.** One realtime call per key per 60 s (NTA fair usage
  policy). The API, scoring job and web app read our own database only.
- **Timestamps are UTC** everywhere in storage (`datetime` with tzinfo=UTC, or epoch
  seconds). Convert to Europe/Dublin only for display and for calendar features.
- **GTFS times past 24:00** (e.g. `25:10:00`) belong to the previous service day. Use
  `common.timeutil.gtfs_time_to_utc`, never hand-roll it.
- **Static schedule is versioned.** Every `gtfs_*` row carries `schedule_version`;
  realtime data joins only to the version active on its service date.
- **No leakage.** Features are built with as-of joins on poll time. Splits are by time,
  never random.
- **Official prediction is shown unaltered** beside ours (fair usage policy).

## Commands

```bash
.venv/bin/python -m pytest              # tests
.venv/bin/ruff check . && .venv/bin/mypy common ingest pipeline ml api
.venv/bin/bustrust --help               # CLI for every job
```

The `.venv/bin/activate` script breaks on the space in the folder path; call
`.venv/bin/python` directly.
