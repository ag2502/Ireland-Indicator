import json

import numpy as np
import pandas as pd
import pytest

from ml import evaluate, models, train
from pipeline.features import ARRIVAL_FEATURES, NOSHOW_FEATURES

SMALL = {"n_estimators": 80, "learning_rate": 0.1, "num_leaves": 15, "min_child_samples": 20, "verbose": -1}
DAYS = [f"202611{d:02d}" for d in range(2, 10)]


def _arrival_frame(n_per_day: int = 1500, seed: int = 0) -> pd.DataFrame:
    """Official prediction is too optimistic in proportion to how late the bus already is
    (pos_minus_official_s) and noisier further out: a pattern a model can learn."""
    rng = np.random.default_rng(seed)
    n = n_per_day * len(DAYS)
    df = pd.DataFrame({c: rng.normal(size=n) for c in ARRIVAL_FEATURES})
    df["horizon_s"] = rng.uniform(60, 3600, n)
    df["pos_minus_official_s"] = rng.normal(0, 120, n)
    df["route_id"] = rng.choice(["39A", "46A", "C1"], n)
    df["stop_id"] = rng.choice([f"s{i}" for i in range(20)], n)
    df["hour"] = rng.integers(5, 24, n)
    df["weekday"] = rng.integers(0, 7, n)
    df["service_date"] = np.repeat(DAYS, n_per_day)
    noise = rng.normal(0, 30 + df["horizon_s"] / 20)
    df["y"] = 0.7 * df["pos_minus_official_s"] + 40 * (df["route_id"] == "C1") + noise
    return df


def _noshow_frame(n_per_day: int = 800, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = n_per_day * len(DAYS)
    df = pd.DataFrame({c: rng.normal(size=n) for c in NOSHOW_FEATURES})
    df["route_id"] = rng.choice(["39A", "46A", "C1"], n)
    df["min_to_start"] = rng.uniform(-15, 60, n)
    df["has_vehicle"] = rng.integers(0, 2, n)
    df["route_noshow_7d"] = 0.03
    df["service_date"] = np.repeat(DAYS, n_per_day)
    logit = -3.5 + 2.5 * (1 - df["has_vehicle"]) * (df["min_to_start"] < 10) + 0.8 * (df["route_id"] == "C1")
    df["y"] = (rng.uniform(size=n) < 1 / (1 + np.exp(-logit))).astype(int)
    return df


@pytest.fixture(autouse=True)
def small_params(monkeypatch):
    monkeypatch.setattr(models, "ARRIVAL_PARAMS", SMALL)
    monkeypatch.setattr(models, "NOSHOW_PARAMS", SMALL)


def test_split_days_is_chronological():
    tr, cal, te = train.split_days(list(reversed(DAYS)))
    assert tr + cal + te == DAYS
    assert max(tr) < min(cal) <= max(cal) < min(te)
    assert len(tr) >= 3
    with pytest.raises(ValueError):
        train.split_days(DAYS[:3])


def test_conformal_offset_finite_sample():
    s = np.arange(1, 11, dtype=float)  # n=10, ceil(11*0.8)/10 = 0.9 -> 9th smallest
    assert models.conformal_offset(s, 0.8) == 9.0
    assert models.conformal_offset(np.array([]), 0.8) == 0.0


def test_arrival_model_beats_official_and_is_calibrated():
    ex = _arrival_frame()
    model, card, test = train.train_arrival(ex)
    ov = card["evaluation"]["overall"]
    assert ov["mae_ours_s"] < 0.9 * ov["mae_official_s"]
    assert 0.75 <= ov["coverage"] <= 0.85
    pred = model.predict(test)
    assert (pred["p10"] <= pred["p50"]).all() and (pred["p50"] <= pred["p90"]).all()
    assert card["feature_importance_gain"]
    assert next(iter(card["feature_importance_gain"])) == "pos_minus_official_s"
    ok, why = train.gate_arrival(model, test, None)
    assert ok, why


def test_arrival_json_roundtrip_and_explain(tmp_path):
    ex = _arrival_frame(n_per_day=400)
    model = models.ArrivalModel(version="t").fit(ex).calibrate(ex)
    path = models.save(model, tmp_path / "m.json")
    back = models.load(path)
    sample = ex.head(50)
    pd.testing.assert_frame_equal(model.predict(sample), back.predict(sample))
    # unseen category values must not crash
    pd.testing.assert_index_equal(back.predict(sample.assign(stop_id="new")).index, sample.index)
    top = model.explain(sample.head(3))
    assert len(top) == 3 and all(len(r) == 3 for r in top)


def test_median_correction_backs_off():
    ex = _arrival_frame(n_per_day=300)
    mc = models.MedianCorrection(min_n=20).fit(ex)
    p = mc.predict(ex.assign(route_id="unknown", stop_id="unknown"))
    assert np.isfinite(p).all()


def test_noshow_model_ranks_and_calibrates(tmp_path):
    ex = _noshow_frame()
    model, card, test = train.train_noshow(ex)
    ov = card["evaluation"]["overall"]
    assert ov["pr_auc"] > ov["pr_auc_route_7d"]
    assert ov["brier"] < ov["brier_route_7d"]
    p = model.predict(test)
    assert ((p >= 0) & (p <= 1)).all()
    assert abs(p.mean() - test["y"].mean()) < 0.03
    back = models.load(models.save(model, tmp_path / "n.json"))
    np.testing.assert_allclose(back.predict(test.head(20)), p[:20])


def test_choose_threshold_reaches_precision():
    y = np.array([1, 1, 0, 1, 0, 0, 0, 0])
    p = np.array([0.9, 0.8, 0.7, 0.6, 0.3, 0.2, 0.1, 0.05])
    assert evaluate.choose_threshold(y, p, min_precision=0.75) == 0.6


def test_train_cli_promotes_then_keeps(tmp_path, monkeypatch, engine):
    from common import config

    for kind, frame in [("arrival", _arrival_frame(n_per_day=600)), ("noshow", _noshow_frame(n_per_day=500))]:
        (tmp_path / "pq" / kind).mkdir(parents=True)
        for d, g in frame.groupby("service_date"):
            g.to_parquet(tmp_path / "pq" / kind / f"{d}.parquet")
    s = config.get_settings()
    monkeypatch.setattr(s, "parquet_dir", tmp_path / "pq")
    monkeypatch.setattr(s, "model_dir", str(tmp_path / "models"))
    monkeypatch.setattr(s, "database_url", engine.url.render_as_string(hide_password=False))

    assert train.run(["--report", str(tmp_path / "eval.json")]) == 0
    reg = train.read_registry()
    assert set(reg["current"]) == {"arrival", "noshow"}
    assert isinstance(train.load_current("arrival"), models.ArrivalModel)
    assert "arrival" in json.loads((tmp_path / "eval.json").read_text())

    # same data again: an identical model cannot beat the current one, so nothing changes
    first = reg["current"]["arrival"]["version"]
    monkeypatch.setattr(train, "_version", lambda kind: f"{kind}-second")
    train.run(["--kind", "arrival"])
    reg = train.read_registry()
    assert reg["current"]["arrival"]["version"] == first
    assert reg["history"][-1]["promoted"] is False
