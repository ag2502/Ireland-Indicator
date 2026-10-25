"""Evaluation in the terms riders and reviewers care about.

Arrival: MAE of the median by horizon (official vs historical median vs ours), the 90th
percentile absolute error (riders remember bad days), 80% range coverage and width, peak
vs off-peak, day-level bootstrap intervals on the error cut, and a leave-by simulation:
how often a rider who reaches the stop at our early edge catches the bus, against one who
trusts the official countdown, and how long each waits.

No-show: PR-AUC and Brier score against the base rate and the route's 7-day rate, plus
precision and recall at the alert threshold.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, brier_score_loss

from pipeline.official import horizon_bucket

BUCKETS = ["0-5", "5-10", "10-20", "20-40", "40-60"]


def _peak(df: pd.DataFrame) -> pd.Series:
    return (df["weekday"] < 5) & (df["hour"].between(7, 9) | df["hour"].between(16, 18))


def _arrival_metrics(y: np.ndarray, p10: np.ndarray, p50: np.ndarray, p90: np.ndarray, base: np.ndarray) -> dict:
    ae_off, ae_base, ae_ours = np.abs(y), np.abs(y - base), np.abs(y - p50)
    return {
        "n": int(len(y)),
        "mae_official_s": float(ae_off.mean()),
        "mae_median_baseline_s": float(ae_base.mean()),
        "mae_ours_s": float(ae_ours.mean()),
        "improvement_vs_official_pct": float(100 * (1 - ae_ours.mean() / ae_off.mean())) if ae_off.mean() else 0.0,
        "p90_abs_official_s": float(np.quantile(ae_off, 0.9)),
        "p90_abs_ours_s": float(np.quantile(ae_ours, 0.9)),
        "coverage": float(((y >= p10) & (y <= p90)).mean()),
        "mean_width_s": float((p90 - p10).mean()),
        # leave-by: be at the stop at official + p10 (ours) or at the official time
        "catch_rate_ours": float((y >= p10).mean()),
        "catch_rate_official": float((y >= 0).mean()),
        "mean_wait_ours_s": float(np.maximum(y - p10, 0).mean()),
        "mean_wait_official_s": float(np.maximum(y, 0).mean()),
    }


def arrival_report(test: pd.DataFrame, pred: pd.DataFrame, base: np.ndarray, n_boot: int = 300, seed: int = 0) -> dict:
    y = test["y"].to_numpy()
    p10, p50, p90 = pred["p10"].to_numpy(), pred["p50"].to_numpy(), pred["p90"].to_numpy()
    hb = horizon_bucket(test["horizon_s"].to_numpy() / 60).to_numpy()
    rep: dict = {"overall": _arrival_metrics(y, p10, p50, p90, base), "by_horizon": {}, "by_period": {}}
    for b in BUCKETS:
        m = hb == b
        if m.sum():
            rep["by_horizon"][b] = _arrival_metrics(y[m], p10[m], p50[m], p90[m], base[m])
    peak = _peak(test).to_numpy()
    for name, m in [("peak", peak), ("off_peak", ~peak)]:
        if m.sum():
            rep["by_period"][name] = _arrival_metrics(y[m], p10[m], p50[m], p90[m], base[m])
    target = (test["horizon_s"] >= 600) & (test["horizon_s"] < 1800)
    t = target.to_numpy()
    if t.sum():
        rep["headline_10_30min"] = _arrival_metrics(y[t], p10[t], p50[t], p90[t], base[t])
        rep["headline_10_30min"]["improvement_ci95_pct"] = _bootstrap_improvement(
            test.loc[target, "service_date"].to_numpy(), y[t], p50[t], n_boot, seed
        )
    if "service_date" in test:
        rep["by_day"] = {
            d: _arrival_metrics(y[m], p10[m], p50[m], p90[m], base[m])
            for d in sorted(test["service_date"].unique())
            if (m := (test["service_date"] == d).to_numpy()).sum()
        }
    return rep


def _bootstrap_improvement(days: np.ndarray, y: np.ndarray, p50: np.ndarray, n_boot: int, seed: int) -> list[float]:
    """Resample whole days (neighbouring polls are near-copies, so rows are not independent)."""
    rng = np.random.default_rng(seed)
    uniq = np.unique(days)
    if len(uniq) < 2:
        return [float("nan"), float("nan")]
    by_day = {d: (np.abs(y[days == d]).sum(), np.abs(y[days == d] - p50[days == d]).sum()) for d in uniq}
    stats = []
    for _ in range(n_boot):
        pick = rng.choice(uniq, size=len(uniq), replace=True)
        off = sum(by_day[d][0] for d in pick)
        ours = sum(by_day[d][1] for d in pick)
        stats.append(100 * (1 - ours / off) if off else 0.0)
    return [float(np.quantile(stats, 0.025)), float(np.quantile(stats, 0.975))]


def choose_threshold(y: np.ndarray, p: np.ndarray, min_precision: float = 0.5) -> float:
    """Lowest threshold whose precision reaches `min_precision` (max recall at that precision);
    falls back to the F1-best threshold."""
    order = np.argsort(-p)
    ys, ps = y[order], p[order]
    tp = np.cumsum(ys)
    prec = tp / np.arange(1, len(ys) + 1)
    ok = np.flatnonzero(prec >= min_precision)
    if len(ok) and ys.sum():
        return float(ps[ok.max()])
    rec = tp / max(ys.sum(), 1)
    f1 = 2 * prec * rec / np.maximum(prec + rec, 1e-9)
    return float(ps[np.argmax(f1)]) if len(f1) else 0.5


def _noshow_metrics(y: np.ndarray, p: np.ndarray, base: np.ndarray, threshold: float) -> dict:
    alert = p >= threshold
    tp = int((alert & (y == 1)).sum())
    return {
        "n": int(len(y)),
        "base_rate": float(y.mean()) if len(y) else 0.0,
        "pr_auc": float(average_precision_score(y, p)) if 0 < y.sum() < len(y) else float("nan"),
        "pr_auc_route_7d": float(average_precision_score(y, base)) if 0 < y.sum() < len(y) else float("nan"),
        "brier": float(brier_score_loss(y, p)) if len(y) else float("nan"),
        "brier_route_7d": float(brier_score_loss(y, base)) if len(y) else float("nan"),
        "threshold": threshold,
        "precision": tp / max(int(alert.sum()), 1),
        "recall": tp / max(int(y.sum()), 1),
        "alerts": int(alert.sum()),
    }


def noshow_report(test: pd.DataFrame, p: np.ndarray, threshold: float) -> dict:
    y = test["y"].to_numpy()
    base = test["route_noshow_7d"].fillna(test["y"].mean()).to_numpy()
    rep = {"overall": _noshow_metrics(y, p, base, threshold), "by_lead": {}}
    for name, lo, hi in [("30-60 min before", 30, 61), ("10-30 min before", 10, 30), ("0-10 min before", 0, 10)]:
        m = test["min_to_start"].between(lo, hi, inclusive="left").to_numpy()
        if m.sum():
            rep["by_lead"][name] = _noshow_metrics(y[m], p[m], base[m], threshold)
    return rep
