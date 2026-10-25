# Architecture

BusTrust is five small processes around one database. Only one of them talks to the
National Transport Authority (NTA); everything a rider sees is read from our own tables.

```
                 NTA GTFS-Realtime (TripUpdates, Vehicles)      NTA static GTFS zip   Open-Meteo
                              │  1 call / key / 60 s                   │ daily           │ hourly
                              ▼                                        ▼                 ▼
                          ┌────────┐                           ┌──────────────┐   ┌─────────┐
  raw archive  ◀───────── │ poller │                           │ load-schedule│   │ weather │
  (gz protobuf,           └───┬────┘                           └──────┬───────┘   └────┬────┘
   local or S3)               │ trip_updates, vehicle_positions,      │ gtfs_* rows,     │
                              │ poll_log, feed_gaps                   │ schedule_version │
                              ▼                                       ▼                  ▼
                 ┌────────────────────────────────────────────────────────────────────────────┐
                 │                    Postgres 16 + TimescaleDB (SQLite in dev)               │
                 └────────────────────────────────────────────────────────────────────────────┘
                     ▲  every minute          ▲ nightly                      ▲ weekly
                     │  live_board,           │ arrivals, trip_outcomes,     │ model bundles,
                     │  live_trips,           │ features (Parquet),          │ registry.json,
                     │  predictions_log       │ route scores, accuracy,      │ model_registry
                 ┌───┴────┐                   │ data_checks                  │
                 │ scorer │            ┌──────┴───────┐               ┌──────┴──────┐
                 └────────┘            │ nightly jobs │               │    train    │
                                       └──────────────┘               └─────────────┘
                     │ reads
                 ┌───┴────┐   /v1/*, /healthz    ┌───────┐    HTTPS     ┌──────────────┐
                 │  API   │ ◀─────────────────── │ Caddy │ ◀─────────── │ phone (PWA)  │
                 └────────┘                      └───┬───┘              └──────────────┘
                                                     │ everything else
                                                 ┌───┴────┐
                                                 │  web   │ Next.js
                                                 └────────┘
```

## Processes

| Process | Command | Runs | Writes | Reads |
|---|---|---|---|---|
| poller | `bustrust poll` | always, a 60 s loop | `trip_updates`, `vehicle_positions`, `poll_log`, `feed_gaps`, raw archive | NTA realtime (or the simulator) |
| scorer | `bustrust score` | always, every minute after a poll | `live_board`, `live_trips`, `predictions_log` | realtime tables, schedule, promoted models |
| API | `uvicorn api.main:app` | always | nothing | `live_*`, scores, schedule, `accuracy_daily` |
| web | `node server.js` | always | nothing | the API, through the same origin |
| jobs | `supercronic infra/crontab` | cron | everything derived | everything |

The nightly chain (04:30 Dublin) is `build-labels`, then `features`, then `scores`, then
`accuracy`, with `checks` last. The weekly `train` runs on Monday at 06:30. The whole
schedule is in [infra/crontab](../infra/crontab).

## Rules the design protects

- **Only the poller calls the NTA.** That keeps us inside the fair usage policy: one
  realtime call per key per 60 s. With one key it alternates TripUpdates and Vehicles.
  Both keys run at once only after the NTA agrees, behind `BT_USE_BOTH_KEYS`.
- **The official prediction is shown unaltered.** The API returns it in `official_at`.
  Our range sits beside it and never replaces it.
- **Timestamps are UTC epoch seconds** in every table. Europe/Dublin is used only to
  display times and to build calendar features.
- **The schedule is versioned.** Each distinct static zip gets a `schedule_version`.
  Realtime rows join only to the version active on their service date, because the NTA
  reuses and changes IDs between versions.
- **There is no leakage.** Features are built as of the poll time T: a ping is usable
  from the poll that received it, and a stop passage is usable from the ping after it.
  Models are split by day, in time order.

## Live status

The scorer does more than predict. For every bus it works out what a rider at the stop
needs to know ([ml/status.py](../ml/status.py)):

- **Per stop:** on time, late, early, timetable only, cancelled, not stopping here
  (diversion), or already left (shown for 15 minutes).
- **Per trip:** not started, on the road, diverted, cancelled or finished. It also
  records:
  - the last GPS fix;
  - the last and next stop;
  - every stop passed and when;
  - the stops it will skip;
  - whether the GPS has left the route line.

Our GPS track beats the feed. When the track shows the bus has passed a stop, the stop is
marked as departed, even if the official feed, a minute or two behind, is still counting
down to it.

## Deployment

The production stack runs on one small VM. [infra/docker-compose.yml](../infra/docker-compose.yml)
defines these services:

- TimescaleDB;
- a one-shot `migrate`, which creates tables and loads the schedule;
- the poller, scorer, jobs and API, all from one Python image;
- the web app, as a standalone Next.js server;
- Caddy, which serves TLS and routes `/v1` to the API and everything else to the web app.

GitHub Actions runs CI on every push. After CI passes on `main` it deploys over SSH.
Every Monday it copies the retrain report into [evaluation.json](evaluation.json). The
[runbook](runbook.md) covers setup and the work that follows it.

## Why these choices

- **One database, no queue.** At one poll a minute the write rate is tiny. Timescale
  compresses the realtime tables after 7 days, which keeps a year of history cheap.
- **A minute-old board instead of on-demand scoring.** The API's hot path is a single
  indexed read of `live_board`. Every rider at a stop sees the same numbers, and the
  numbers they saw are the ones logged and graded.
- **JSON model bundles instead of pickles.** A model trained on one LightGBM version
  loads on another, and a model file can't run code when it loads.
- **The simulator** ([ingest/simulator.py](../ingest/simulator.py)) gives the whole
  system realistic feeds before an NTA key arrives. It also gives the tests ground truth.
  With `BT_FEED_MODE=simulated`, every page of the app warns that the data is not real.
