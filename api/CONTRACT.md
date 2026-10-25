# api: contract

A read-only FastAPI service. It reads our own database and never calls the NTA.
Interactive docs are served at `/docs`, and response models are in [schemas.py](schemas.py).

## Endpoints

| Path | Returns | Cache |
|---|---|---|
| `GET /healthz` | Poll and board age, `feed_mode`, and model versions. Answers 503 when the board is more than 3 minutes old or the poll more than 5. | none |
| `GET /v1/stops?q=&limit=` | Stops matching a name or code. With no `q`, every stop, so the phone can find the nearest stops itself. | 1 h / 24 h |
| `GET /v1/stops/{stop}` | One stop and the routes that serve it. | 1 h |
| `GET /v1/stops/{stop}/arrivals?walk_min=` | The board for one stop: upcoming buses, plus buses that left, were cancelled or are skipping the stop in the last 15 minutes. | 20 s |
| `GET /v1/vehicles?route=` | Every bus with a fresh GPS fix, with its status, delay and off-route flag. | 20 s |
| `GET /v1/trips/{trip_id}` | One trip: its status, position, every stop's state (passed, upcoming, skipped or cancelled) and the route shape. 404 if the trip is not live. | 20 s |
| `GET /v1/alerts` | Routes with cancelled, diverted, likely no-show or very late (10 minutes or more) trips. | 30 s |
| `GET /v1/routes` | Every route with a reliability score. | 1 h |
| `GET /v1/routes/{route}/reliability?days=` | Daily scores, the heatmap and the slowest segments. | 10 min |
| `GET /v1/leaderboard?days=` | Routes ranked best and worst. A route needs at least 20 trips to be ranked. | 10 min |
| `GET /v1/accuracy?days=` | Live accuracy by horizon, from `accuracy_daily`. | 10 min |

## Guarantees

- Times are UTC epoch seconds. The client formats them in Europe/Dublin.
- `official_at` is the NTA prediction, passed through unchanged. `official_source` says
  whether it came from the feed or only from the timetable.
- `likely_at` is our median when a model is promoted, and otherwise the official time.
  `early_at` and `late_at` are null without a promoted model, so a client can tell the
  two cases apart.
- `walk_min` only fills `leave_by_at`. The web app always sends 0 and applies the walk on
  the phone, so a rider's location never reaches us.
- Every response that shows NTA data carries the attribution text.
