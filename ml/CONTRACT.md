# ml: contract

The two models and the live scorer that writes the board riders see.

## Commands

| Command | Reads | Writes |
|---|---|---|
| `train [--kind arrival\|noshow\|both] [--force] [--report PATH]` | Parquet examples | model bundles and `registry.json` in `BT_MODEL_DIR`, plus `model_registry` rows. `--report` writes the evaluation JSON. |
| `score` (a loop, every minute) | latest poll, schedule, promoted models | `live_board` and `live_trips`, replaced in full; `predictions_log`, appended |
| `accuracy [--date D] [--days N]` | `predictions_log`, `arrivals` | `accuracy_daily` |

## live_board (one row per stop and bus)

The API's hot path reads only this table. The columns are listed in
[docs/data-dictionary.md](../docs/data-dictionary.md#live-rebuilt-every-minute-by-the-scorer).
The scorer guarantees the following:

- `official_at` is the NTA prediction, unaltered. It is null only for trips that appear
  in the timetable alone (`status = scheduled`).
- `p10_at <= p50_at <= p90_at` and `p50_at >= made_at`. All three are null when no
  arrival model is promoted.
- `status` is one of `on_time`, `late`, `early`, `scheduled`, `cancelled`, `skipped` or
  `departed`:
  - `departed` rows stay listed for 15 minutes. `passed_at` is null when only the feed
    shows the bus has gone.
  - `cancelled` rows stay listed from 15 minutes before now until the end of the horizon.
- A stop our GPS track shows as passed is never listed as still to come, whatever the feed says.

## live_trips (one row per trip on the road or due within the hour)

- `status` is one of `not_started`, `running`, `diverted`, `cancelled` or `finished`,
  with that order of precedence: cancelled, then finished, then diverted, then running.
- `passed` is JSON `{stop_sequence: passed_at or null}`. Its keys are always a prefix of the
  trip's stop sequences.
- `lat`, `lon` and `ping_at` are set only when the fix is 5 minutes old or less.

## Promotion rules (train)

- The days are split in time order: train, then calibrate, then test. They are never
  split at random.
- A new arrival model is promoted only if both of these hold on the test days:
  - its 80% range covers between 78% and 82% of arrivals;
  - its median's MAE beats the promoted model's. The first model must beat the
    official prediction instead.
- A new no-show model is promoted only if its PR-AUC beats the promoted model's. The
  first model must beat each route's 7-day no-show rate instead.
- `--force` promotes regardless of the gate. Use it only by hand, and say why in the commit.

Bundles are JSON files holding booster text, features, category levels and calibration.
They contain no pickles.
