import numpy as np
import pandas as pd
import pytest

from pipeline import checks, scores


def test_awt_regular_and_bunched():
    # regular 10-minute headways: average wait is half the headway
    assert scores.awt(np.full(6, 600)) == pytest.approx(300)
    # same buses per hour, but in pairs: waits get much longer
    assert scores.awt(np.array([60, 1140] * 3)) > 500


def test_on_time_window():
    d = pd.Series([-61, -60, 0, 300, 301])
    assert scores.on_time(d).tolist() == [False, True, True, True, False]


def test_reliability_pct():
    assert scores.reliability_pct(100, 5, 1000, 800) == pytest.approx(76.0)
    assert np.isnan(scores.reliability_pct(0, 0, 0, 0))


def _frames():
    # one route-direction, 6 trips 10 min apart, 3 stops; trip T3 is a no-show, T1 is bunched behind T0
    base = 1_793_610_000  # Mon 2 Nov 2026 ~09:00 Dublin
    se, arr, out = [], [], []
    for k in range(6):
        for s in range(3):
            sched = base + k * 600 + s * 120
            se.append(
                {
                    "trip_id": f"T{k}",
                    "stop_id": f"S{s}",
                    "stop_sequence": s + 1,
                    "scheduled_at": sched,
                    "route_id": "R",
                    "direction_id": 0,
                    "route_short_name": "39A",
                }
            )
            if k != 3:
                late = 480 if k == 0 else 0  # T0 runs 8 min late, right in front of T1
                arr.append(
                    {
                        "trip_id": f"T{k}",
                        "stop_id": f"S{s}",
                        "stop_sequence": s + 1,
                        "scheduled_at": sched,
                        "actual_at": sched + late,
                        "route_id": "R",
                        "direction_id": 0,
                        "method": "gps_interp",
                        "service_date": "20261102",
                    }
                )
        out.append({"trip_id": f"T{k}", "route_id": "R", "direction_id": 0, "noshow": k == 3})
    return pd.DataFrame(se), pd.DataFrame(arr), pd.DataFrame(out)


def test_day_scores():
    se, arr, out = _frames()
    row = scores.day_scores(se, arr, out, "20261102").iloc[0]
    assert row.n_trips == 6 and row.n_noshow == 1
    assert row.frequent
    assert row.n_bunched == 1
    assert row.ewt_s > 0
    # punctuality excludes the last stop: 5 trips x 2 stops, T0's 2 late
    assert (row.n_departures, row.n_on_time) == (10, 8)


def test_segments_need_five_observations():
    _, arr, _ = _frames()
    seg = scores.segments(arr)
    assert len(seg) == 2 and (seg["median_lost_s"] == 0).all()


def test_check_status_thresholds():
    assert checks.status("poll_success", 0.999) == "ok"
    assert checks.status("poll_success", 0.97) == "warn"
    assert checks.status("poll_success", 0.5) == "fail"
    assert checks.status("max_poll_gap", 31) == "fail"
    assert checks.status("gps_coverage", float("nan")) == "warn"
