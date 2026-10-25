"""Train, evaluate and (maybe) promote both models.

Split by time, never at random: the earliest days train, the next days calibrate, the
latest days test. A new model replaces the promoted one only if it beats it on the same
test days (lower MAE / higher PR-AUC) and its range coverage stays within 78-82%.

The registry is `registry.json` beside the model files (local folder or S3); the scorer
reads it to find the promoted models. The weekly retrain runs from the jobs container's
cron (infra/crontab), and GitHub Actions copies its report into docs/evaluation.json.
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from common import db, storage
from common.config import get_settings
from common.timeutil import now_epoch
from ml.evaluate import arrival_report, choose_threshold, noshow_report
from ml.models import ArrivalModel, MedianCorrection, NoShowModel

log = logging.getLogger(__name__)

COVERAGE_BAND = (0.78, 0.82)
REGISTRY = "registry.json"
MAX_ROWS_PER_DAY = {"arrival": 250_000, "noshow": None}


# ---------------------------------------------------------------- data
def load_examples(
    kind: str, parquet_dir: str | None = None, since: str | None = None, max_rows_per_day: int | None = None
) -> pd.DataFrame:
    """All example days of one kind. `max_rows_per_day` takes a random sample of each day:
    consecutive polls of one bus are near-duplicates, so this costs little accuracy and
    keeps memory flat as history grows."""
    base = Path(parquet_dir or get_settings().parquet_dir) / kind
    files = sorted(base.glob("*.parquet"))
    if since:
        files = [f for f in files if f.stem >= since]
    if not files:
        return pd.DataFrame()
    frames = []
    for f in files:
        df = pd.read_parquet(f)
        if max_rows_per_day and len(df) > max_rows_per_day:
            df = df.sample(max_rows_per_day, random_state=0)
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def split_days(days: list[str], test_days: int = 7, cal_days: int = 2, min_train: int = 3) -> tuple[list, list, list]:
    """Earliest -> train, next -> calibrate, latest -> test. Shrinks the test window when
    history is short, keeping at least `min_train` training days."""
    days = sorted(days)
    n = len(days)
    if n < min_train + 2:
        raise ValueError(f"need at least {min_train + 2} days of examples, have {n}")
    test_days = min(test_days, max(1, (n - min_train - 1) // 2 + (n - min_train - 1) % 2))
    cal_days = min(cal_days, n - min_train - test_days)
    train = days[: n - test_days - cal_days]
    return train, days[len(train) : len(train) + cal_days], days[n - test_days :]


# ---------------------------------------------------------------- registry
def read_registry(model_dir: str | None = None) -> dict:
    base = model_dir or get_settings().model_dir
    if not storage.exists(base, REGISTRY):
        return {"current": {}, "history": []}
    return json.loads(storage.get_bytes(base, REGISTRY))


def write_registry(reg: dict, model_dir: str | None = None) -> None:
    storage.put_bytes(model_dir or get_settings().model_dir, REGISTRY, json.dumps(reg, indent=2).encode())


def load_current(kind: str, model_dir: str | None = None) -> ArrivalModel | NoShowModel | None:
    base = model_dir or get_settings().model_dir
    entry = read_registry(base)["current"].get(kind)
    if not entry:
        return None
    d = json.loads(storage.get_bytes(base, entry["file"]))
    return ArrivalModel.from_json(d) if kind == "arrival" else NoShowModel.from_json(d)


def _store(model: ArrivalModel | NoShowModel, model_dir: str) -> str:
    key = f"{model.kind}/{model.version}.json"
    storage.put_bytes(model_dir, key, json.dumps(model.to_json()).encode())
    return key


# ---------------------------------------------------------------- training
def _version(kind: str) -> str:
    return f"{kind}-{datetime.now(UTC):%Y%m%d-%H%M%S}"


def train_arrival(ex: pd.DataFrame) -> tuple[ArrivalModel, dict, pd.DataFrame]:
    days = sorted(ex["service_date"].unique())
    tr_d, cal_d, te_d = split_days(days)
    train, cal, test = (ex[ex["service_date"].isin(d)] for d in (tr_d, cal_d, te_d))
    log.info(
        "arrival: train %d rows (%s..%s), calibrate %d, test %d", len(train), tr_d[0], tr_d[-1], len(cal), len(test)
    )
    baseline = MedianCorrection().fit(train)
    model = ArrivalModel(version=_version("arrival")).fit(train).calibrate(cal)
    pred = model.predict(test)
    report = arrival_report(test, pred, baseline.predict(test))
    card = {
        "model": "LightGBM quantile regression (p10/p50/p90) + split-conformal calibration per horizon bucket",
        "label": "actual arrival minus official prediction, seconds (GPS-interpolated labels)",
        "features": model.features,
        "train_days": tr_d,
        "calibration_days": cal_d,
        "test_days": te_d,
        "rows": {"train": len(train), "calibration": len(cal), "test": len(test)},
        "conformal_offsets_s": model.offsets,
        "evaluation": report,
        "feature_importance_gain": _importance(model.boosters["p50"]),
        "created_at": now_epoch(),
    }
    model.card = card
    return model, card, test


def train_noshow(ex: pd.DataFrame) -> tuple[NoShowModel, dict, pd.DataFrame]:
    days = sorted(ex["service_date"].unique())
    tr_d, cal_d, te_d = split_days(days)
    train, cal, test = (ex[ex["service_date"].isin(d)] for d in (tr_d, cal_d, te_d))
    log.info(
        "noshow: train %d rows (base rate %.3f), calibrate %d, test %d",
        len(train),
        train["y"].mean(),
        len(cal),
        len(test),
    )
    model = NoShowModel(version=_version("noshow")).fit(train).calibrate(cal)
    threshold = choose_threshold(cal["y"].to_numpy(), model.predict(cal))
    report = noshow_report(test, model.predict(test), threshold)
    card = {
        "model": "LightGBM classifier + isotonic calibration",
        "label": "trip never seen with GPS in its window, or cancelled in the feed",
        "features": model.features,
        "train_days": tr_d,
        "calibration_days": cal_d,
        "test_days": te_d,
        "alert_threshold": threshold,
        "evaluation": report,
        "feature_importance_gain": _importance(model.booster),
        "created_at": now_epoch(),
    }
    model.card = card
    return model, card, test


def _importance(booster) -> dict[str, float]:
    gain = booster.feature_importance(importance_type="gain")
    total = gain.sum() or 1
    pairs = sorted(zip(booster.feature_name(), gain / total, strict=True), key=lambda p: -p[1])
    return {k: round(float(v), 4) for k, v in pairs[:15]}


def gate_arrival(new: ArrivalModel, test: pd.DataFrame, current: ArrivalModel | None) -> tuple[bool, str]:
    y = test["y"].to_numpy()
    pn = new.predict(test)
    cov = float(((y >= pn["p10"]) & (y <= pn["p90"])).mean())
    mae_new = float(np.abs(y - pn["p50"]).mean())
    if not COVERAGE_BAND[0] <= cov <= COVERAGE_BAND[1]:
        return False, f"coverage {cov:.3f} outside {COVERAGE_BAND}"
    ref = float(np.abs(y).mean()) if current is None else float(np.abs(y - current.predict(test)["p50"]).mean())
    name = "official prediction" if current is None else f"current model {current.version}"
    if mae_new >= ref:
        return False, f"MAE {mae_new:.1f}s not below {name} ({ref:.1f}s)"
    return True, f"MAE {mae_new:.1f}s < {name} {ref:.1f}s, coverage {cov:.3f}"


def gate_noshow(new: NoShowModel, test: pd.DataFrame, current: NoShowModel | None) -> tuple[bool, str]:
    from sklearn.metrics import average_precision_score

    y = test["y"].to_numpy()
    if not 0 < y.sum() < len(y):
        return False, "test set has no positives"
    ap_new = average_precision_score(y, new.predict(test))
    if current is None:
        base = test["route_noshow_7d"].fillna(y.mean()).to_numpy()
        ref, name = average_precision_score(y, base), "route 7-day rate"
    else:
        ref, name = average_precision_score(y, current.predict(test)), f"current model {current.version}"
    if ap_new <= ref:
        return False, f"PR-AUC {ap_new:.3f} not above {name} ({ref:.3f})"
    return True, f"PR-AUC {ap_new:.3f} > {name} {ref:.3f}"


def run(argv: list[str] | None = None) -> int:
    """CLI: bustrust train [--kind arrival|noshow|both] [--force] [--report docs/evaluation.json]"""
    p = argparse.ArgumentParser(prog="bustrust train")
    p.add_argument("--kind", choices=["arrival", "noshow", "both"], default="both")
    p.add_argument("--force", action="store_true", help="promote even if the gate says no")
    p.add_argument("--report", help="also write the evaluation JSON here")
    args = p.parse_args(argv)
    s = get_settings()
    reg = read_registry()
    reports = {}
    model: ArrivalModel | NoShowModel
    for kind in ["arrival", "noshow"] if args.kind == "both" else [args.kind]:
        ex = load_examples(kind, max_rows_per_day=MAX_ROWS_PER_DAY[kind])
        if ex.empty:
            log.error("%s: no examples; run `bustrust features --days N` first", kind)
            continue
        if kind == "arrival":
            model, card, test = train_arrival(ex)
            ok, why = gate_arrival(model, test, load_current("arrival"))  # type: ignore[arg-type]
        else:
            model, card, test = train_noshow(ex)
            ok, why = gate_noshow(model, test, load_current("noshow"))  # type: ignore[arg-type]
        card["promotion"] = {"promoted": ok or args.force, "reason": why}
        key = _store(model, s.model_dir)
        entry = {
            "version": model.version,
            "file": key,
            "created_at": now_epoch(),
            "promoted": ok or args.force,
            "reason": why,
        }
        reg["history"].append(entry)
        if ok or args.force:
            reg["current"][kind] = entry
        db.upsert_rows(
            db.init_db(),
            db.model_registry,
            [
                {
                    "model_version": model.version,
                    "kind": kind,
                    "created_at": entry["created_at"],
                    "promoted": entry["promoted"],
                    "path": key,
                    "card": json.dumps(card),
                }
            ],  # fmt: skip
            keys=["model_version"],
        )
        log.info("%s %s: %s (%s)", kind, model.version, "PROMOTED" if entry["promoted"] else "kept old", why)
        reports[kind] = card
    write_registry(reg)
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(reports, indent=2, default=str))
    return 0
