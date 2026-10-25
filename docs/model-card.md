# Model card

BusTrust has two models. Both are retrained every Monday, and a new model replaces the
current one only when it passes the gate described below. The full numbers for the
latest run are in [evaluation.json](evaluation.json).

> **The numbers on this page come from simulated data.** The NTA key had not arrived, so
> both models were trained and tested on the feed simulator
> ([ingest/simulator.py](../ingest/simulator.py)). The simulator draws realistic delays,
> ghost buses and diversions from the real Dublin schedule. These numbers show the method
> works end to end. They are not a claim about real Dublin buses, and the weekly report
> will replace them once the real feed has run for a few weeks.

## 1. Arrival range

**Question.** When will this bus reach this stop, and what range will hold 80% of the time?

**Method.**
- Three LightGBM quantile regressions (p10, p50 and p90) predict the correction to the
  official prediction: actual arrival minus official, in seconds.
- Split-conformal calibration, fitted per horizon bucket on two calibration days, then
  widens or narrows the p10 to p90 band so it covers 80% of arrivals.
- The official time is never replaced. Our median and range are shown beside it.

**Label.** The arrival time, reconstructed by projecting GPS pings onto the route shape
and interpolating ([pipeline/arrivals.py](../pipeline/arrivals.py)). Where pings are
missing on a trip that ran, the last official prediction is used instead, and the label
records which method produced it.

**Features** (27). The main groups:
- The official delay, and how far ahead the prediction is (the horizon).
- Where the bus is on GPS, compared with what the official feed assumes.
- Recent speed.
- Time lost by buses ahead on the same road in the last few 5-minute bins.
- Slack in the bus's schedule before its next trip.
- The gap to the bus ahead.
- Route, stop, hour, weekday, holidays, rain and wind.

Every feature is built as of the poll time, from rows already received by then.

**Split.** 5 training days (15–19 Oct 2026), 2 calibration days, and 4 test days
(22–25 Oct 2026), in that order. The split is never random.

**Results on the test days** (910k predictions):

| Horizon | Official MAE | Our MAE | Range coverage | Mean width |
|---|---|---|---|---|
| 0–5 min | 34 s | 23 s | 81.7% | 75 s |
| 5–10 min | 56 s | 35 s | 82.3% | 117 s |
| 10–20 min | 88 s | 52 s | 83.1% | 173 s |
| 20–40 min | 129 s | 73 s | 84.6% | 249 s |
| 40–60 min | 197 s | 106 s | 85.5% | 362 s |
| **All** | **110 s** | **62 s** | **83.6%** | **210 s** |

- **Error.** At 10–30 minutes out, our median's error is 42% lower than the official
  time's (day-level bootstrap 95% interval: 36–47%). A median-only baseline that knows
  the stop and hour reaches 76 s overall, so most of the gain comes from live position
  and conditions ahead.
- **Bad days.** The 90th percentile error falls from 278 s to 143 s, and riders
  remember the bad days.
- **Leave-by test.** A rider who arrives at our early edge catches the bus 96% of the
  time. A rider who trusts the official countdown catches it 74% of the time. The cost
  is about 22 s more waiting on average.

**Status: not promoted.** Coverage is 83.6%, outside the 78–82% band the gate requires.
Coverage also drifts from day to day: 72% on 22 Oct and 87% on 23 Oct. Two calibration
days are too few to pin it down. Until a model passes, the app shows official times
only and says so on the stop page.

**Next step.** Calibrate on more days, and condition the conformal offset on peak or
off-peak as well as the horizon. Peak coverage is 77% and off-peak is higher.

## 2. No-show risk

**Question.** What is the chance this trip never comes? This is the "ghost bus": a trip
that counts down on the board but never runs, whether or not the feed cancels it.

**Method.** A LightGBM classifier. Isotonic regression on the calibration days maps its
scores to probabilities. Trips the feed has cancelled get a probability of 1 by rule.
Trips already moving on GPS get 0.

**Label.** `noshow`: the trip was never seen with GPS in its window, or the feed cancelled
it. Trips that overlap a feed outage are left out.

**Features** (16). The main groups:
- Minutes to the scheduled start.
- Whether the trip is in the feed, and whether a vehicle and GPS are assigned.
- Whether the bus's previous trip ran, and the slack between the two trips.
- The share of the route's buses unseen right now.
- The route's 7-day no-show rate.
- Route, hour, weekday, holidays and rain.

**Results on the test days** (46.7k trip snapshots, base rate 6.0%):

| Lead time | PR-AUC (ours) | PR-AUC (route 7-day rate) | Precision / recall at alert |
|---|---|---|---|
| 30–60 min before | 0.59 | 0.06 | 75% / 53% |
| 10–30 min before | 0.07 | 0.06 | 7% / 6% |
| 0–10 min before | 0.08 | 0.05 | 10% / 13% |
| **All** | **0.50** | **0.06** | **46% / 44%** |

- **Brier score:** 0.035, against 0.058 for the route rate.

**Status: promoted.** It beats the route's 7-day rate, which is the gate for a first model.

**Limits.** The model is useful 30–60 minutes before departure. There, the strongest
signal is whether the bus's previous trip on its block ran. Closer to departure it is
barely better than the base rate. That is why the app shows a risk badge only for
"some" or "high" risk, and words it as a chance, not a verdict. Precision and recall in
the last 30 minutes are the first numbers to check on real data.

## Promotion gate

These rules are applied in [ml/train.py](../ml/train.py):
- **Arrival model.**
  - The range must cover between 78% and 82% of the test days' arrivals.
  - Its median's MAE must beat the current model's on the same days. The first model
    must beat the official prediction instead.
- **No-show model.**
  - Its PR-AUC must beat the current model's.
  - The first model must beat the route's 7-day rate instead.
- **Every run** is recorded in `model_registry` with its card, whether it was promoted
  or not.

## Live grading

Each scored prediction near a target horizon (2, 5, 10, 15, 20, 30, 45 or 60 minutes) is
logged when it is made. The next morning, `accuracy` grades it against the reconstructed
arrival. The How it works page publishes those numbers: live predictions only, never a
replay.

## Ethical and practical notes

- **No personal data.** The models use vehicle and schedule data only. Rider location
  never leaves the phone.
- **Feedback loop.** If enough riders followed our leave-by times to change how buses
  run, the labels would shift. At hobby scale this is not a concern, but the weekly
  retrain would absorb it.
- **Label noise.** GPS pings come every 20–60 s, so the label for each stop carries
  that uncertainty (`uncertainty_s`). The stopwatch check
  ([pipeline/stopwatch.py](../pipeline/stopwatch.py)) measures it against arrivals timed
  by hand.
