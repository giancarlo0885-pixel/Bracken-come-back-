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
SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
INTERVAL_MS = 300_000
DAYS = 90
HORIZON_BARS = 3


def fetch(symbol: str) -> pd.DataFrame:
    end_ms = int(time.time() * 1000)
    cursor = end_ms - DAYS * 86_400_000
    rows: list[list[object]] = []
    while cursor < end_ms:
        batch = requests.get(
            URL,
            params={
                "symbol": symbol,
                "interval": "5m",
                "startTime": cursor,
                "endTime": end_ms,
                "limit": 1000,
            },
            timeout=20,
        ).json()
        if not batch:
            break
        rows += batch
        next_cursor = int(batch[-1][0]) + INTERVAL_MS
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        if len(batch) < 1000:
            break

    frame = (
        pd.DataFrame(
            rows,
            columns=[
                "ot", "o", "h", "l", "c", "v", "ct", "qv",
                "n", "tb", "tbq", "x",
            ],
        )
        .drop_duplicates("ot")
        .sort_values("ot")
    )
    for col in ["o", "h", "l", "c", "qv", "n", "tbq"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame.index = pd.to_datetime(frame.ct, unit="ms", utc=True) + pd.Timedelta(milliseconds=1)
    print("V63_RAW", symbol, len(frame), flush=True)
    return frame


def features(frame: pd.DataFrame, prefix: str) -> pd.DataFrame:
    log_close = np.log(frame.c)
    returns = log_close.diff()
    signed_quote_flow = 2 * frame.tbq - frame.qv
    out = pd.DataFrame(index=frame.index)

    for k in [1, 2, 3, 6, 12]:
        out[prefix + f"r{k}"] = log_close.diff(k)
    for k in [3, 6, 12, 24]:
        out[prefix + f"rv{k}"] = returns.rolling(k).std()
    for k in [1, 3, 6, 12]:
        out[prefix + f"ofi{k}"] = (
            signed_quote_flow.rolling(k).sum() / (frame.qv.rolling(k).sum() + 1e-12)
        )
    for k in [6, 12, 24]:
        out[prefix + f"vpin{k}"] = (
            signed_quote_flow.abs().rolling(k).sum() / (frame.qv.rolling(k).sum() + 1e-12)
        )
        out[prefix + f"vi{k}"] = frame.qv / (frame.qv.rolling(k).mean() + 1e-12)
        out[prefix + f"ti{k}"] = frame.n / (frame.n.rolling(k).mean() + 1e-12)

    out[prefix + "range"] = (frame.h - frame.l) / (frame.c + 1e-12)
    out[prefix + "body"] = (frame.c - frame.o) / (frame.o + 1e-12)
    out[prefix + "cloc"] = (frame.c - frame.l) / (frame.h - frame.l + 1e-12) - 0.5
    return out


def ece(probability: np.ndarray, outcome: np.ndarray) -> float:
    total = 0.0
    for lower in np.arange(0, 1, 0.1):
        upper = lower + 0.1 if lower < 0.9 else 1.0001
        mask = (probability >= lower) & (probability < upper)
        if mask.any():
            total += mask.mean() * abs(
                float(probability[mask].mean()) - float(outcome[mask].mean())
            )
    return float(total)


def metrics(
    probability: np.ndarray,
    outcome: np.ndarray,
    baselines: dict[str, np.ndarray],
) -> dict[str, object]:
    probability = np.asarray(probability, dtype=float)
    outcome = np.asarray(outcome, dtype=float)
    brier = float(np.mean((probability - outcome) ** 2))
    baseline_briers = {
        name: float(np.mean((np.asarray(values, dtype=float) - outcome) ** 2))
        for name, values in baselines.items()
    }
    best_baseline = min(baseline_briers.values())
    return {
        "n": int(len(outcome)),
        "accuracy": float(np.mean((probability >= 0.5) == outcome)),
        "brier": brier,
        "brier_skill_vs_best": float(1 - brier / best_baseline)
        if best_baseline > 1e-12
        else -9.0,
        "ece": ece(probability, outcome),
        "beats_all_baselines": bool(all(brier < value for value in baseline_briers.values())),
        "best_baseline_brier": best_baseline,
        "baseline_briers": baseline_briers,
    }


def wilson_lower(successes: int, total: int, z: float = 1.96) -> float:
    if total <= 0:
        return 0.0
    p = successes / total
    denom = 1 + z * z / total
    return (
        p
        + z * z / (2 * total)
        - z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    ) / denom


def main() -> None:
    with ThreadPoolExecutor(max_workers=3) as pool:
        raw = dict(zip(SYMS, pool.map(fetch, SYMS)))

    panel = pd.concat(
        [
            features(raw["BTCUSDT"], "b_"),
            features(raw["ETHUSDT"], "e_"),
            features(raw["SOLUSDT"], "s_"),
        ],
        axis=1,
        join="inner",
    )
    panel["mktr1"] = (panel.b_r1 + panel.e_r1 + panel.s_r1) / 3
    panel["mktr3"] = (panel.b_r3 + panel.e_r3 + panel.s_r3) / 3
    panel["be_r1"] = panel.b_r1 - panel.e_r1
    panel["bs_r1"] = panel.b_r1 - panel.s_r1
    panel["es_r1"] = panel.e_r1 - panel.s_r1
    minute = panel.index.hour * 60 + panel.index.minute
    panel["sin"] = np.sin(2 * np.pi * minute / 1440)
    panel["cos"] = np.cos(2 * np.pi * minute / 1440)

    for symbol, prefix in [("BTCUSDT", "b_"), ("ETHUSDT", "e_"), ("SOLUSDT", "s_")]:
        log_close = np.log(raw[symbol].c).reindex(panel.index)
        panel["y_" + symbol] = (log_close.shift(-HORIZON_BARS) > log_close).astype(int)
        panel["prev_" + symbol] = (log_close.diff() > 0).astype(int)
        panel["mom_" + symbol] = (log_close.diff(5) > 0).astype(int)

    panel = panel.replace([np.inf, -np.inf], np.nan).dropna()
    feature_cols = [
        col
        for col in panel.columns
        if not col.startswith(("y_", "prev_", "mom_"))
    ]

    output: dict[str, object] = {}
    for target in SYMS:
        ycol = "y_" + target
        prevcol = "prev_" + target
        momcol = "mom_" + target

        end = panel.index.max().floor("6h") - pd.Timedelta(days=10)
        start = end - 60 * pd.Timedelta(hours=6)

        probabilities: list[float] = []
        outcomes: list[float] = []
        baseline_sets = {"coin": [], "base": [], "prev": [], "mom": []}
        eligible = passing = 0
        consecutive = max_consecutive = 0

        for index in range(60):
            t = start + index * pd.Timedelta(hours=6)
            train = panel[
                (panel.index >= t - pd.Timedelta(days=35))
                & (panel.index < t - pd.Timedelta(days=4, minutes=15))
            ]
            validation_a = panel[
                (panel.index >= t - pd.Timedelta(days=4))
                & (panel.index < t - pd.Timedelta(days=2, minutes=15))
            ]
            validation_b = panel[
                (panel.index >= t - pd.Timedelta(days=2))
                & (panel.index < t - pd.Timedelta(minutes=15))
            ]
            test = panel[
                (panel.index >= t)
                & (panel.index < t + pd.Timedelta(hours=6) - pd.Timedelta(minutes=15))
            ]
            if min(map(len, [train, validation_a, validation_b, test])) < 300:
                continue

            base_rate = float(train[ycol].mean())
            best = None

            for c_value in [0.0003, 0.001, 0.003, 0.01, 0.03]:
                model = make_pipeline(
                    StandardScaler(),
                    LogisticRegression(C=c_value, max_iter=1000),
                ).fit(train[feature_cols], train[ycol])
                p_a = model.predict_proba(validation_a[feature_cols])[:, 1]
                p_b = model.predict_proba(validation_b[feature_cols])[:, 1]

                for threshold in [0.01, 0.02, 0.03, 0.04, 0.06, 0.08]:
                    mask_a = np.abs(p_a - 0.5) >= threshold
                    mask_b = np.abs(p_b - 0.5) >= threshold
                    if mask_a.sum() < 60 or mask_b.sum() < 60:
                        continue
                    coverage_a = float(mask_a.mean())
                    coverage_b = float(mask_b.mean())
                    if not (
                        0.08 <= coverage_a <= 0.90
                        and 0.08 <= coverage_b <= 0.90
                    ):
                        continue

                    correct = int(
                        np.sum(
                            (p_a[mask_a] >= 0.5)
                            == validation_a[ycol].to_numpy()[mask_a]
                        )
                        + np.sum(
                            (p_b[mask_b] >= 0.5)
                            == validation_b[ycol].to_numpy()[mask_b]
                        )
                    )
                    accepted = int(mask_a.sum() + mask_b.sum())
                    calibrated_q = min(
                        0.70,
                        max(0.51, (correct + 30) / (accepted + 60)),
                    )

                    def score_block(
                        probability: np.ndarray,
                        data: pd.DataFrame,
                        mask: np.ndarray,
                    ) -> dict[str, object]:
                        direction = probability[mask] >= 0.5
                        calibrated = np.where(direction, calibrated_q, 1 - calibrated_q)
                        actual = data[ycol].to_numpy()[mask]
                        previous = np.where(data[prevcol].to_numpy()[mask] > 0, 0.60, 0.40)
                        momentum = np.where(data[momcol].to_numpy()[mask] > 0, 0.65, 0.35)
                        return metrics(
                            calibrated,
                            actual,
                            {
                                "coin": np.full(len(actual), 0.5),
                                "base": np.full(len(actual), base_rate),
                                "prev": previous,
                                "mom": momentum,
                            },
                        )

                    a_metrics = score_block(p_a, validation_a, mask_a)
                    b_metrics = score_block(p_b, validation_b, mask_b)
                    score = (
                        min(
                            float(a_metrics["brier_skill_vs_best"]),
                            float(b_metrics["brier_skill_vs_best"]),
                        )
                        + 0.5
                        * (
                            float(a_metrics["brier_skill_vs_best"])
                            + float(b_metrics["brier_skill_vs_best"])
                        )
                    )
                    candidate = (
                        score,
                        c_value,
                        threshold,
                        calibrated_q,
                        model,
                        base_rate,
                    )
                    if best is None or candidate[0] > best[0]:
                        best = candidate

            if best is None:
                continue

            _, c_value, threshold, calibrated_q, model, base_rate = best
            eligible += 1
            test_probability = model.predict_proba(test[feature_cols])[:, 1]
            mask = np.abs(test_probability - 0.5) >= threshold
            if mask.sum() < 8:
                continue

            direction = test_probability[mask] >= 0.5
            calibrated = np.where(direction, calibrated_q, 1 - calibrated_q)
            actual = test[ycol].to_numpy()[mask]
            previous = np.where(test[prevcol].to_numpy()[mask] > 0, 0.60, 0.40)
            momentum = np.where(test[momcol].to_numpy()[mask] > 0, 0.65, 0.35)
            baseline = {
                "coin": np.full(len(actual), 0.5),
                "base": np.full(len(actual), base_rate),
                "prev": previous,
                "mom": momentum,
            }
            result = metrics(calibrated, actual, baseline)
            fold_pass = bool(
                result["n"] >= 8
                and result["accuracy"] >= 0.52
                and result["brier_skill_vs_best"] >= 0.02
                and result["ece"] <= 0.12
                and result["beats_all_baselines"]
            )
            passing += int(fold_pass)
            consecutive = 0 if fold_pass else consecutive + 1
            max_consecutive = max(max_consecutive, consecutive)

            probabilities += calibrated.tolist()
            outcomes += actual.tolist()
            for key, values in baseline.items():
                baseline_sets[key] += np.asarray(values).tolist()

        aggregate = (
            metrics(
                np.array(probabilities),
                np.array(outcomes),
                {key: np.array(values) for key, values in baseline_sets.items()},
            )
            if outcomes
            else {
                "n": 0,
                "accuracy": 0,
                "brier_skill_vs_best": -9,
                "ece": 1,
                "beats_all_baselines": False,
            }
        )
        wilson = (
            wilson_lower(
                int(round(float(aggregate["accuracy"]) * int(aggregate["n"]))),
                int(aggregate["n"]),
            )
            if aggregate["n"]
            else 0
        )
        pass_rate = passing / eligible if eligible else 0
        high_level_pass = bool(
            aggregate["n"] >= 1000
            and aggregate["accuracy"] >= 0.52
            and aggregate["brier_skill_vs_best"] >= 0.02
            and aggregate["ece"] <= 0.12
            and aggregate["beats_all_baselines"]
            and wilson >= 0.50
            and eligible >= 30
            and pass_rate >= 0.45
            and max_consecutive <= 7
        )
        output[target] = {
            "eligible_folds": eligible,
            "passing_folds": passing,
            "eligible_pass_rate": pass_rate,
            "max_consecutive_failures": max_consecutive,
            "wilson95_lower": wilson,
            "aggregate": aggregate,
            "HIGH_LEVEL_PASS": high_level_pass,
        }

    print("V63_CROSS_ASSET_15M", json.dumps(output), flush=True)
    print(
        "V63_ALL_PASS",
        all(bool(item["HIGH_LEVEL_PASS"]) for item in output.values()),
        flush=True,
    )


if __name__ == "__main__":
    main()
