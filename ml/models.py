"""The two models and their baselines, saved as portable JSON bundles.

Arrival range: three LightGBM quantile regressions (p10, p50, p90) of the correction to
the official prediction (actual - official, seconds), then split-conformal calibration
(conformalized quantile regression, the method MAPIE implements) per horizon bucket: the
p10-p90 band is widened or narrowed by the calibration-set quantile of
max(p10 - y, y - p90), so that it really holds 80% of the time.

No-show risk: a LightGBM classifier whose scores are mapped to probabilities by isotonic
regression on the calibration days.

A bundle holds booster text, feature list, category levels and calibration, so it loads
on any Python/LightGBM version without pickles.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

from pipeline.features import ARRIVAL_FEATURES, CATEGORICAL, NOSHOW_FEATURES
from pipeline.official import horizon_bucket

QUANTILES = {"p10": 0.1, "p50": 0.5, "p90": 0.9}

ARRIVAL_PARAMS = {
    "n_estimators": 600,
    "learning_rate": 0.04,
    "num_leaves": 63,
    "min_child_samples": 40,
    "subsample": 0.8,
    "subsample_freq": 1,
    "colsample_bytree": 0.8,
    "cat_smooth": 20,
    "min_data_per_group": 50,
    "verbose": -1,
}
NOSHOW_PARAMS = {
    "n_estimators": 400,
    "learning_rate": 0.03,
    "num_leaves": 31,
    "min_child_samples": 50,
    "subsample": 0.8,
    "subsample_freq": 1,
    "colsample_bytree": 0.8,
    "verbose": -1,
}


def _frame(df: pd.DataFrame, features: list[str], levels: dict[str, list[str]]) -> pd.DataFrame:
    x = df.reindex(columns=features).copy()
    for c in features:
        if c in levels:
            x[c] = pd.Categorical(x[c].astype("string"), categories=levels[c])
        else:
            x[c] = pd.to_numeric(x[c], errors="coerce").astype("float64")
    return x


def _levels(df: pd.DataFrame, features: list[str]) -> dict[str, list[str]]:
    return {c: sorted(df[c].dropna().astype(str).unique().tolist()) for c in CATEGORICAL if c in features}


def conformal_offset(scores: np.ndarray, coverage: float) -> float:
    """Finite-sample split-conformal quantile: the k-th smallest score, k = ceil((n+1)*coverage)."""
    n = len(scores)
    if n == 0:
        return 0.0
    k = min(n, math.ceil((n + 1) * coverage))
    return float(np.sort(scores)[k - 1])


@dataclass
class ArrivalModel:
    version: str
    boosters: dict[str, lgb.Booster] = field(default_factory=dict)
    levels: dict[str, list[str]] = field(default_factory=dict)
    offsets: dict[str, float] = field(default_factory=dict)  # horizon bucket -> conformal Q
    coverage: float = 0.8
    features: list[str] = field(default_factory=lambda: list(ARRIVAL_FEATURES))
    card: dict[str, Any] = field(default_factory=dict)

    kind = "arrival"

    def fit(self, train: pd.DataFrame, params: dict | None = None) -> ArrivalModel:
        self.levels = _levels(train, self.features)
        x = _frame(train, self.features, self.levels)
        for name, alpha in QUANTILES.items():
            m = lgb.LGBMRegressor(objective="quantile", alpha=alpha, **(params or ARRIVAL_PARAMS))
            m.fit(x, train["y"].to_numpy(), categorical_feature=[c for c in self.levels])
            self.boosters[name] = m.booster_
        return self

    def raw(self, df: pd.DataFrame) -> pd.DataFrame:
        x = _frame(df, self.features, self.levels)
        out = pd.DataFrame({k: b.predict(x) for k, b in self.boosters.items()}, index=df.index)
        # quantile crossing happens with separate models; sort each row
        out[["p10", "p50", "p90"]] = np.sort(out[["p10", "p50", "p90"]].to_numpy(), axis=1)
        return out

    def calibrate(self, cal: pd.DataFrame) -> ArrivalModel:
        raw = self.raw(cal)
        y = cal["y"].to_numpy()
        scores = np.maximum(raw["p10"].to_numpy() - y, y - raw["p90"].to_numpy())
        buckets = horizon_bucket(cal["horizon_s"].to_numpy() / 60).to_numpy()
        self.offsets = {"_all": conformal_offset(scores, self.coverage)}
        for b in np.unique(buckets):
            s = scores[buckets == b]
            if len(s) >= 100:  # too few points makes a noisy offset; fall back to global
                self.offsets[str(b)] = conformal_offset(s, self.coverage)
        return self

    def predict(self, df: pd.DataFrame) -> pd.DataFrame:
        """Corrections in seconds: p10 <= p50 <= p90 after conformal adjustment."""
        out = self.raw(df)
        buckets = horizon_bucket(df["horizon_s"].to_numpy() / 60)
        q = buckets.map(lambda b: self.offsets.get(str(b), self.offsets.get("_all", 0.0))).to_numpy()
        out["p10"] = np.minimum(out["p10"] - q, out["p50"])
        out["p90"] = np.maximum(out["p90"] + q, out["p50"])
        return out

    def explain(self, df: pd.DataFrame, top: int = 3) -> list[list[tuple[str, float]]]:
        """Top SHAP contributions (TreeSHAP built into LightGBM) to the median correction."""
        x = _frame(df, self.features, self.levels)
        contrib = np.asarray(self.boosters["p50"].predict(x, pred_contrib=True))[:, :-1]
        order = np.argsort(-np.abs(contrib), axis=1)[:, :top]
        return [[(self.features[j], float(contrib[i, j])) for j in row] for i, row in enumerate(order)]

    # ---------------------------------------------------------------- persistence
    def to_json(self) -> dict:
        return {
            "kind": self.kind,
            "version": self.version,
            "features": self.features,
            "levels": self.levels,
            "offsets": self.offsets,
            "coverage": self.coverage,
            "boosters": {k: b.model_to_string() for k, b in self.boosters.items()},
            "card": self.card,
        }

    @classmethod
    def from_json(cls, d: dict) -> ArrivalModel:
        return cls(
            version=d["version"],
            boosters={k: lgb.Booster(model_str=s) for k, s in d["boosters"].items()},
            levels=d["levels"],
            offsets=d["offsets"],
            coverage=d["coverage"],
            features=d["features"],
            card=d.get("card", {}),
        )


@dataclass
class NoShowModel:
    version: str
    booster: lgb.Booster | None = None
    levels: dict[str, list[str]] = field(default_factory=dict)
    iso_x: list[float] = field(default_factory=list)
    iso_y: list[float] = field(default_factory=list)
    features: list[str] = field(default_factory=lambda: list(NOSHOW_FEATURES))
    card: dict[str, Any] = field(default_factory=dict)

    kind = "noshow"

    def fit(self, train: pd.DataFrame, params: dict | None = None) -> NoShowModel:
        self.levels = _levels(train, self.features)
        x = _frame(train, self.features, self.levels)
        y = train["y"].to_numpy()
        pos = max(y.sum(), 1)
        m = lgb.LGBMClassifier(
            objective="binary", scale_pos_weight=min((len(y) - pos) / pos, 20), **(params or NOSHOW_PARAMS)
        )
        m.fit(x, y, categorical_feature=[c for c in self.levels])
        self.booster = m.booster_
        return self

    def raw(self, df: pd.DataFrame) -> np.ndarray:
        assert self.booster is not None
        return np.asarray(self.booster.predict(_frame(df, self.features, self.levels)))

    def calibrate(self, cal: pd.DataFrame) -> NoShowModel:
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        iso.fit(self.raw(cal), cal["y"].to_numpy())
        self.iso_x, self.iso_y = iso.X_thresholds_.tolist(), iso.y_thresholds_.tolist()
        return self

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        s = self.raw(df)
        if not self.iso_x:
            return s
        return np.interp(s, self.iso_x, self.iso_y)

    def explain(self, df: pd.DataFrame, top: int = 3) -> list[list[tuple[str, float]]]:
        assert self.booster is not None
        contrib = np.asarray(self.booster.predict(_frame(df, self.features, self.levels), pred_contrib=True))[:, :-1]
        order = np.argsort(-contrib, axis=1)[:, :top]  # what pushes risk *up*
        return [[(self.features[j], float(contrib[i, j])) for j in row] for i, row in enumerate(order)]

    def to_json(self) -> dict:
        assert self.booster is not None
        return {
            "kind": self.kind,
            "version": self.version,
            "features": self.features,
            "levels": self.levels,
            "iso_x": self.iso_x,
            "iso_y": self.iso_y,
            "booster": self.booster.model_to_string(),
            "card": self.card,
        }

    @classmethod
    def from_json(cls, d: dict) -> NoShowModel:
        return cls(
            version=d["version"],
            booster=lgb.Booster(model_str=d["booster"]),
            levels=d["levels"],
            iso_x=d["iso_x"],
            iso_y=d["iso_y"],
            features=d["features"],
            card=d.get("card", {}),
        )


# ---------------------------------------------------------------- baselines
class MedianCorrection:
    """Baseline 2: the historical median correction by route, stop, hour and horizon bucket,
    backing off to coarser groups when a cell has fewer than `min_n` examples."""

    LEVELS = [["route_id", "stop_id", "hour", "hb"], ["route_id", "hour", "hb"], ["route_id", "hb"], ["hb"]]

    def __init__(self, min_n: int = 20):
        self.min_n = min_n
        self.tables: list[pd.Series] = []

    @staticmethod
    def _hb(df: pd.DataFrame) -> pd.DataFrame:
        return df.assign(hb=horizon_bucket(df["horizon_s"].to_numpy() / 60).to_numpy())

    def fit(self, train: pd.DataFrame) -> MedianCorrection:
        t = self._hb(train)
        self.tables = []
        for keys in self.LEVELS:
            g = t.groupby(keys)["y"].agg(["median", "size"])
            self.tables.append(g.loc[g["size"] >= self.min_n, "median"])
        return self

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        t = self._hb(df)
        out = pd.Series(np.nan, index=t.index)
        for keys, table in zip(self.LEVELS, self.tables, strict=True):
            idx = pd.MultiIndex.from_frame(t[keys]) if len(keys) > 1 else t[keys[0]]
            out = out.fillna(pd.Series(table.reindex(idx).to_numpy(), index=t.index))
        return out.fillna(0).to_numpy()


# ---------------------------------------------------------------- IO
def save(model: ArrivalModel | NoShowModel, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(model.to_json()))
    return path


def load(path: Path) -> ArrivalModel | NoShowModel:
    d = json.loads(Path(path).read_text())
    return ArrivalModel.from_json(d) if d["kind"] == "arrival" else NoShowModel.from_json(d)
