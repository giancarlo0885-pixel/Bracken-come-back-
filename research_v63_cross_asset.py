from __future__ import annotations

import json
import math
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import requests
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

URL = "https://data-api.binance.vision/api/v3/klines"
SYMBOLS = [
    "BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","DOGEUSDT","ADAUSDT",
    "AVAXUSDT","LINKUSDT","LTCUSDT","BCHUSDT","AAVEUSDT","UNIUSDT",
]
INTERVAL = "5m"
INTERVAL_MS = 300_000
DAYS = 90
HORIZON_BARS = 3
HOLDOUT_DAYS = 14
PREHOLDOUT_DAYS = 21


def fetch(symbol: str) -> pd.DataFrame:
    end_ms = int(time.time() * 1000)
    cursor = end_ms - DAYS * 86_400_000
    rows = []
    while cursor < end_ms:
        response = requests.get(
            URL,
            params={
                "symbol": symbol,
                "interval": INTERVAL,
                "startTime": cursor,
                "endTime": end_ms,
                "limit": 1000,
            },
            timeout=20,
        )
        response.raise_for_status()
        batch = response.json()
        if not batch:
            break
        rows.extend(batch)
        next_cursor = int(batch[-1][0]) + INTERVAL_MS
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        if len(batch) < 1000:
            break
    frame = pd.DataFrame(
        rows,
        columns=["ot","o","h","l","c","v","ct","qv","n","tb","tbq","x"],
    ).drop_duplicates("ot").sort_values("ot")
    for column in ["o","h","l","c","qv","n","tbq"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame.index = pd.to_datetime(frame["ct"], unit="ms", utc=True) + pd.Timedelta(milliseconds=1)
    return frame


def build_features(own: pd.DataFrame, btc: pd.DataFrame) -> pd.DataFrame:
    idx = own.index.intersection(btc.index)
    own = own.reindex(idx)
    btc = btc.reindex(idx)

    lr = np.log(own["c"])
    btc_lr = np.log(btc["c"])
    ret = lr.diff()
    quote = own["qv"]
    signed_quote = 2.0 * own["tbq"] - quote

    out = pd.DataFrame(index=idx)
    for k in (1, 3, 6, 12, 24):
        out[f"r{k}"] = lr.diff(k)
    for k in (3, 6, 12, 24):
        out[f"rv{k}"] = ret.rolling(k).std()
        out[f"ofi{k}"] = signed_quote.rolling(k).sum() / (quote.rolling(k).sum() + 1e-12)
    for k in (6, 12, 24):
        out[f"vi{k}"] = quote / (quote.rolling(k).mean() + 1e-12)
        out[f"ti{k}"] = own["n"] / (own["n"].rolling(k).mean() + 1e-12)

    out["range"] = (own["h"] - own["l"]) / (own["c"] + 1e-12)
    out["body"] = (own["c"] - own["o"]) / (own["o"] + 1e-12)
    out["cloc"] = (own["c"] - own["l"]) / (own["h"] - own["l"] + 1e-12) - 0.5

    out["btc_r3"] = btc_lr.diff(3)
    out["btc_r6"] = btc_lr.diff(6)
    out["btc_r12"] = btc_lr.diff(12)
    out["resid3"] = out["r3"] - out["btc_r3"]
    out["resid6"] = out["r6"] - out["btc_r6"]
    out["flow_delta"] = out["ofi3"] - out["ofi12"]
    out["return_accel"] = out["r3"] - 0.5 * out["r6"]

    minute = out.index.hour * 60 + out.index.minute
    out["sin"] = np.sin(2 * np.pi * minute / 1440)
    out["cos"] = np.cos(2 * np.pi * minute / 1440)

    out["y"] = (lr.shift(-HORIZON_BARS) > lr).astype(int)
    out["prev"] = (lr.diff(HORIZON_BARS) > 0).astype(int)
    out["mom"] = (lr.diff(12) > 0).astype(int)

    # Non-overlapping 15-minute decisions.
    out = out.iloc[::HORIZON_BARS]
    return out.replace([np.inf, -np.inf], np.nan).dropna()


def ece(probability: np.ndarray, outcome: np.ndarray) -> float:
    if not len(outcome):
        return 1.0
    total = float(len(outcome))
    result = 0.0
    for i in range(10):
        lo, hi = i / 10, (i + 1) / 10
        mask = (probability >= lo) & (probability < hi if i < 9 else probability <= hi)
        if mask.any():
            result += mask.sum() / total * abs(float(probability[mask].mean()) - float(outcome[mask].mean()))
    return float(result)


def metrics(probability: np.ndarray, outcome: np.ndarray, baseline: dict[str, np.ndarray]) -> dict:
    probability = np.asarray(probability, dtype=float)
    outcome = np.asarray(outcome, dtype=float)
    if not len(outcome):
        return {
            "n": 0,
            "accuracy": 0.0,
            "brier": math.inf,
            "climatology_brier": math.inf,
            "brier_skill": -math.inf,
            "ece": 1.0,
            "beats_all_baselines": False,
        }
    brier = float(np.mean((probability - outcome) ** 2))
    base_rate = float(outcome.mean())
    climatology = float(np.mean((base_rate - outcome) ** 2))
    benchmark_scores = {
        name: float(np.mean((np.asarray(values, dtype=float) - outcome) ** 2))
        for name, values in baseline.items()
    }
    return {
        "n": int(len(outcome)),
        "accuracy": float(np.mean((probability >= 0.5) == (outcome >= 0.5))),
        "brier": brier,
        "climatology_brier": climatology,
        "brier_skill": float(1.0 - brier / climatology) if climatology > 1e-12 else -math.inf,
        "ece": ece(probability, outcome),
        "beats_all_baselines": bool(all(brier < score for score in benchmark_scores.values())),
        "baseline_briers": benchmark_scores,
    }


def baselines(frame: pd.DataFrame, base_rate: float, mask: np.ndarray) -> dict[str, np.ndarray]:
    prev = np.where(frame["prev"].to_numpy()[mask] > 0, 0.60, 0.40)
    mom = np.where(frame["mom"].to_numpy()[mask] > 0, 0.65, 0.35)
    n = int(mask.sum())
    return {
        "coin_flip": np.full(n, 0.5),
        "base_rate": np.full(n, base_rate),
        "previous_direction": prev,
        "momentum_5": mom,
    }


def smooth_accuracy(correct: int, total: int) -> float:
    return float(min(0.72, max(0.505, (correct + 18.0) / (total + 36.0))))


RULE_FEATURES = (
    "r3","r6","r12","ofi3","ofi6","ofi12","flow_delta",
    "resid3","resid6","return_accel","cloc",
)
RULE_QUANTILES = (0.50, 0.65, 0.75, 0.85, 0.90)


def rule_prediction(frame: pd.DataFrame, feature: str, threshold: float, polarity: int) -> tuple[np.ndarray, np.ndarray]:
    values = frame[feature].to_numpy(dtype=float)
    mask = np.abs(values) >= threshold
    direction = (values >= 0.0)
    if polarity < 0:
        direction = ~direction
    return mask, direction


def evaluate_direction(
    frame: pd.DataFrame,
    mask: np.ndarray,
    direction: np.ndarray,
    probability_strength: float,
    base_rate: float,
) -> dict:
    if int(mask.sum()) == 0:
        return metrics(np.array([]), np.array([]), {})
    probability = np.where(direction[mask], probability_strength, 1.0 - probability_strength)
    outcome = frame["y"].to_numpy(dtype=float)[mask]
    return metrics(probability, outcome, baselines(frame, base_rate, mask))


def choose_rule(train: pd.DataFrame, v1: pd.DataFrame, v2: pd.DataFrame) -> dict | None:
    candidates = []
    base_rate = float(train["y"].mean())

    for feature in RULE_FEATURES:
        train_values = train[feature].to_numpy(dtype=float)
        if not len(train_values):
            continue
        for q in RULE_QUANTILES:
            threshold = float(np.quantile(np.abs(train_values), q))
            if not math.isfinite(threshold) or threshold <= 0:
                continue
            for polarity in (1, -1):
                tr_mask, tr_dir = rule_prediction(train, feature, threshold, polarity)
                tr_n = int(tr_mask.sum())
                if tr_n < 60:
                    continue
                tr_correct = int(np.sum(tr_dir[tr_mask] == (train["y"].to_numpy()[tr_mask] > 0)))
                qprob = smooth_accuracy(tr_correct, tr_n)

                inner = []
                valid = True
                for frame in (v1, v2):
                    mask, direction = rule_prediction(frame, feature, threshold, polarity)
                    n = int(mask.sum())
                    coverage = n / max(1, len(frame))
                    if n < 18 or not (0.03 <= coverage <= 0.65):
                        valid = False
                        break
                    m = evaluate_direction(frame, mask, direction, qprob, base_rate)
                    inner.append((m, coverage))
                if not valid:
                    continue
                min_skill = min(x[0]["brier_skill"] for x in inner)
                min_accuracy = min(x[0]["accuracy"] for x in inner)
                all_beats = all(x[0]["beats_all_baselines"] for x in inner)
                score = min_skill + 0.30 * sum(x[0]["brier_skill"] for x in inner) + 0.01 * min_accuracy
                candidates.append({
                    "kind": "rule",
                    "feature": feature,
                    "threshold": threshold,
                    "polarity": polarity,
                    "train_probability": qprob,
                    "min_skill": min_skill,
                    "min_accuracy": min_accuracy,
                    "inner_beats_all": all_beats,
                    "score": score,
                })

    feature_cols = [
        c for c in train.columns
        if c not in {"y","prev","mom"}
    ]
    for C in (0.001, 0.003, 0.01, 0.03, 0.1):
        model = make_pipeline(StandardScaler(), LogisticRegression(C=C, max_iter=1000))
        model.fit(train[feature_cols], train["y"])
        train_probability = model.predict_proba(train[feature_cols])[:, 1]
        for confidence in (0.02, 0.04, 0.06, 0.08, 0.10, 0.13):
            train_mask = np.abs(train_probability - 0.5) >= confidence
            if int(train_mask.sum()) < 60:
                continue
            train_dir = train_probability >= 0.5
            tr_correct = int(np.sum(train_dir[train_mask] == (train["y"].to_numpy()[train_mask] > 0)))
            qprob = smooth_accuracy(tr_correct, int(train_mask.sum()))
            inner = []
            valid = True
            for frame in (v1, v2):
                raw = model.predict_proba(frame[feature_cols])[:, 1]
                mask = np.abs(raw - 0.5) >= confidence
                coverage = int(mask.sum()) / max(1, len(frame))
                if int(mask.sum()) < 18 or not (0.03 <= coverage <= 0.65):
                    valid = False
                    break
                direction = raw >= 0.5
                m = evaluate_direction(frame, mask, direction, qprob, base_rate)
                inner.append((m, coverage))
            if not valid:
                continue
            min_skill = min(x[0]["brier_skill"] for x in inner)
            min_accuracy = min(x[0]["accuracy"] for x in inner)
            all_beats = all(x[0]["beats_all_baselines"] for x in inner)
            score = min_skill + 0.30 * sum(x[0]["brier_skill"] for x in inner) + 0.01 * min_accuracy
            candidates.append({
                "kind": "logistic",
                "C": C,
                "confidence": confidence,
                "model": model,
                "feature_cols": feature_cols,
                "train_probability": qprob,
                "min_skill": min_skill,
                "min_accuracy": min_accuracy,
                "inner_beats_all": all_beats,
                "score": score,
            })

    if not candidates:
        return None
    return max(candidates, key=lambda item: (item["score"], item["min_skill"], item["min_accuracy"]))


def apply_candidate(candidate: dict, train: pd.DataFrame, v1: pd.DataFrame, v2: pd.DataFrame, test: pd.DataFrame):
    base_rate = float(train["y"].mean())

    # Calibrate only from the two already-past validation blocks after selection.
    correct = total = 0
    for frame in (v1, v2):
        if candidate["kind"] == "rule":
            mask, direction = rule_prediction(
                frame, candidate["feature"], candidate["threshold"], candidate["polarity"]
            )
        else:
            raw = candidate["model"].predict_proba(frame[candidate["feature_cols"]])[:, 1]
            mask = np.abs(raw - 0.5) >= candidate["confidence"]
            direction = raw >= 0.5
        correct += int(np.sum(direction[mask] == (frame["y"].to_numpy()[mask] > 0)))
        total += int(mask.sum())
    probability_strength = smooth_accuracy(correct, total)

    if candidate["kind"] == "rule":
        mask, direction = rule_prediction(
            test, candidate["feature"], candidate["threshold"], candidate["polarity"]
        )
    else:
        raw = candidate["model"].predict_proba(test[candidate["feature_cols"]])[:, 1]
        mask = np.abs(raw - 0.5) >= candidate["confidence"]
        direction = raw >= 0.5

    if int(mask.sum()) == 0:
        return [], [], {k: [] for k in ("coin_flip","base_rate","previous_direction","momentum_5")}
    probability = np.where(direction[mask], probability_strength, 1.0 - probability_strength)
    outcome = test["y"].to_numpy(dtype=float)[mask]
    base = baselines(test, base_rate, mask)
    return probability.tolist(), outcome.tolist(), {k: v.tolist() for k, v in base.items()}


def preholdout(symbol: str, frame: pd.DataFrame) -> dict:
    latest = frame.index.max().floor("1d")
    end = latest - pd.Timedelta(days=HOLDOUT_DAYS)
    start = end - pd.Timedelta(days=PREHOLDOUT_DAYS)

    probabilities = []
    outcomes = []
    base = {k: [] for k in ("coin_flip","base_rate","previous_direction","momentum_5")}
    selected = []

    for day in range(PREHOLDOUT_DAYS):
        t = start + pd.Timedelta(days=day)
        train = frame[
            (frame.index >= t - pd.Timedelta(days=35))
            & (frame.index < t - pd.Timedelta(days=10, minutes=15))
        ]
        v1 = frame[
            (frame.index >= t - pd.Timedelta(days=10))
            & (frame.index < t - pd.Timedelta(days=5, minutes=15))
        ]
        v2 = frame[
            (frame.index >= t - pd.Timedelta(days=5))
            & (frame.index < t - pd.Timedelta(minutes=15))
        ]
        test = frame[
            (frame.index >= t)
            & (frame.index < t + pd.Timedelta(days=1) - pd.Timedelta(minutes=15))
        ]
        if len(train) < 1000 or len(v1) < 250 or len(v2) < 250 or len(test) < 80:
            continue

        candidate = choose_rule(train, v1, v2)
        if candidate is None:
            continue
        # Fail closed: only use a daily candidate when both past validation blocks
        # show positive skill and non-random directionality.
        if candidate["min_skill"] <= 0.0 or candidate["min_accuracy"] < 0.53:
            continue

        p, y, b = apply_candidate(candidate, train, v1, v2, test)
        probabilities.extend(p)
        outcomes.extend(y)
        for key in base:
            base[key].extend(b[key])
        selected.append({
            "date": str(t.date()),
            "kind": candidate["kind"],
            "feature": candidate.get("feature"),
            "polarity": candidate.get("polarity"),
            "C": candidate.get("C"),
            "confidence": candidate.get("confidence"),
            "min_skill": candidate["min_skill"],
            "min_accuracy": candidate["min_accuracy"],
            "n": len(y),
        })

    m = metrics(np.array(probabilities), np.array(outcomes), {k: np.array(v) for k, v in base.items()})
    passed = bool(
        m["n"] >= 100
        and m["directional_accuracy" if "directional_accuracy" in m else "accuracy"] >= 0.52
        and m["brier_skill"] >= 0.02
        and m["ece"] <= 0.12
        and m["beats_all_baselines"]
    )
    return {
        "symbol": symbol,
        "status": "PASS" if passed else "FAIL",
        "metrics": m,
        "selected_days": len(selected),
        "sample": selected[:5],
    }


def main():
    with ThreadPoolExecutor(max_workers=6) as pool:
        frames = dict(zip(SYMBOLS, pool.map(fetch, SYMBOLS)))
    print("V65_RAW", json.dumps({k: len(v) for k, v in frames.items()}), flush=True)
    btc = frames["BTCUSDT"]
    results = {}
    for symbol in SYMBOLS:
        try:
            prepared = build_features(frames[symbol], btc)
            results[symbol] = preholdout(symbol, prepared)
            print("V65_SYMBOL", json.dumps(results[symbol], default=str), flush=True)
        except Exception as exc:
            results[symbol] = {"symbol": symbol, "status": "ERROR", "reason": exc.__class__.__name__}
            print("V65_SYMBOL", json.dumps(results[symbol]), flush=True)
    passing = [symbol for symbol, item in results.items() if item.get("status") == "PASS"]
    print("V65_PREHOLDOUT", json.dumps({"passing_symbols": passing, "count": len(passing), "results": results}, default=str), flush=True)
    print("V65_CAN_ADVANCE_TO_HOLDOUT", len(passing) >= 3, flush=True)


if __name__ == "__main__":
    main()
