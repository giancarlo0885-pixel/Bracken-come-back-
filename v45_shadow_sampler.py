from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import requests

from database import connect
from v45_flow_shadow import MODEL_NAME, MODEL_VERSION, predict_v45_flow_shadow
from v45_shadow_governance import evaluate_shadow_predictions


LOG = logging.getLogger("v45-shadow-sampler")
BINANCE_KLINES_URL = "https://data-api.binance.vision/api/v3/klines"
SYMBOLS = {"BTC-USD": "BTCUSDT", "ETH-USD": "ETHUSDT"}
INTERVAL = "5m"
INTERVAL_MS = 300_000
HISTORY_BARS = 2200
POLL_SECONDS = max(60, int(os.getenv("V45_SHADOW_POLL_SECONDS", "300")))


def ensure_schema() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS v45_shadow_predictions (
                id BIGSERIAL PRIMARY KEY,
                symbol TEXT NOT NULL,
                provider_symbol TEXT NOT NULL,
                model TEXT NOT NULL,
                model_version TEXT NOT NULL,
                observed_at TIMESTAMPTZ NOT NULL,
                resolve_at TIMESTAMPTZ NOT NULL,
                spot_price DOUBLE PRECISION NOT NULL,
                status TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                probability_up DOUBLE PRECISION,
                realized_price DOUBLE PRECISION,
                realized_up BOOLEAN,
                validation_samples INTEGER,
                validation_accuracy DOUBLE PRECISION,
                validation_brier_skill DOUBLE PRECISION,
                payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                resolved_at TIMESTAMPTZ,
                UNIQUE(symbol, model_version, observed_at)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_v45_shadow_resolution
            ON v45_shadow_predictions(model_version, status, resolve_at)
            """
        )


def _fetch_history(provider_symbol: str) -> pd.DataFrame:
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - HISTORY_BARS * INTERVAL_MS
    rows: list[list[Any]] = []
    cursor = start_ms
    while cursor < end_ms and len(rows) < HISTORY_BARS:
        response = requests.get(
            BINANCE_KLINES_URL,
            params={
                "symbol": provider_symbol,
                "interval": INTERVAL,
                "startTime": cursor,
                "endTime": end_ms,
                "limit": 1000,
            },
            timeout=15,
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
    rows = rows[-HISTORY_BARS:]
    frame = pd.DataFrame(
        rows,
        columns=[
            "open_time", "open", "high", "low", "Close", "volume",
            "close_time", "QuoteVolume", "trades", "taker_base",
            "TakerBuyQuoteVolume", "ignore",
        ],
    )
    if frame.empty:
        return frame
    frame.index = pd.to_datetime(frame["open_time"], unit="ms", utc=True)
    for column in ("Close", "QuoteVolume", "TakerBuyQuoteVolume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame[~frame.index.duplicated(keep="last")].sort_index()


def _persist_observation(symbol: str, provider_symbol: str, history: pd.DataFrame, prediction: dict[str, Any]) -> None:
    if history.empty:
        return
    observed_at = pd.Timestamp(history.index[-1]).to_pydatetime().astimezone(timezone.utc)
    resolve_at = observed_at + timedelta(minutes=15)
    spot = float(history["Close"].iloc[-1])
    predict_status = str(prediction.get("status") or "ABSTAIN")
    status = "PENDING" if predict_status == "PREDICT" else "ABSTAIN"
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO v45_shadow_predictions
            (symbol, provider_symbol, model, model_version, observed_at, resolve_at,
             spot_price, status, reason, probability_up, validation_samples,
             validation_accuracy, validation_brier_skill, payload)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
            ON CONFLICT (symbol, model_version, observed_at) DO NOTHING
            """,
            (
                symbol,
                provider_symbol,
                MODEL_NAME,
                MODEL_VERSION,
                observed_at,
                resolve_at,
                spot,
                status,
                str(prediction.get("reason") or ""),
                prediction.get("probability_up"),
                prediction.get("validation_samples"),
                prediction.get("validation_accuracy"),
                prediction.get("validation_brier_skill"),
                json.dumps(prediction, sort_keys=True, default=str),
            ),
        )


def _resolve_pending(symbol: str, history: pd.DataFrame) -> int:
    if history.empty:
        return 0
    latest_at = pd.Timestamp(history.index[-1]).to_pydatetime().astimezone(timezone.utc)
    with connect() as conn:
        pending = conn.execute(
            """
            SELECT id, resolve_at, spot_price
            FROM v45_shadow_predictions
            WHERE symbol=%s AND model_version=%s AND status='PENDING' AND resolve_at <= %s
            ORDER BY resolve_at ASC
            LIMIT 500
            """,
            (symbol, MODEL_VERSION, latest_at),
        ).fetchall()
        resolved = 0
        for item in pending:
            resolve_at = pd.Timestamp(item["resolve_at"])
            if resolve_at.tzinfo is None:
                resolve_at = resolve_at.tz_localize("UTC")
            candidates = history.loc[history.index >= resolve_at]
            if candidates.empty:
                continue
            realized_price = float(candidates["Close"].iloc[0])
            realized_up = realized_price > float(item["spot_price"])
            conn.execute(
                """
                UPDATE v45_shadow_predictions
                SET status='RESOLVED', realized_price=%s, realized_up=%s,
                    resolved_at=NOW()
                WHERE id=%s AND status='PENDING'
                """,
                (realized_price, realized_up, int(item["id"])),
            )
            resolved += 1
        return resolved


def governance_summary() -> dict[str, Any]:
    with connect() as conn:
        total_row = conn.execute(
            "SELECT COUNT(*) AS n FROM v45_shadow_predictions WHERE model_version=%s",
            (MODEL_VERSION,),
        ).fetchone() or {"n": 0}
        rows = conn.execute(
            """
            SELECT probability_up, realized_up
            FROM v45_shadow_predictions
            WHERE model_version=%s AND status='RESOLVED' AND probability_up IS NOT NULL
            ORDER BY id ASC
            """,
            (MODEL_VERSION,),
        ).fetchall()
    records = [
        {"status": "RESOLVED", "probability_up": row["probability_up"], "realized_up": row["realized_up"]}
        for row in rows
    ]
    # Baseline and leakage checks remain fail-closed until the independent
    # validation service supplies affirmative evidence. The sampler cannot
    # promote itself merely by accumulating predictions.
    return evaluate_shadow_predictions(
        records,
        total_opportunities=int(total_row.get("n") or 0),
        temporal_leakage_ok=False,
        beats_baselines=False,
    )


def sample_once() -> dict[str, Any]:
    ensure_schema()
    output: dict[str, Any] = {"model_version": MODEL_VERSION, "symbols": {}}
    for symbol, provider_symbol in SYMBOLS.items():
        try:
            history = _fetch_history(provider_symbol)
            resolved = _resolve_pending(symbol, history)
            prediction = predict_v45_flow_shadow(history, symbol)
            _persist_observation(symbol, provider_symbol, history, prediction)
            output["symbols"][symbol] = {
                "status": prediction.get("status"),
                "reason": prediction.get("reason"),
                "resolved": resolved,
            }
        except Exception as exc:
            output["symbols"][symbol] = {"status": "ERROR", "reason": exc.__class__.__name__}
            LOG.exception("V45 SHADOW symbol failure | symbol=%s", symbol)
    output["governance"] = governance_summary()
    LOG.info("V45 SHADOW | %s", json.dumps(output, sort_keys=True, default=str))
    return output


def run_forever() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    LOG.info("V45 SHADOW START | execution_allowed=False | broker_submission=False | symbols=BTC-USD,ETH-USD")
    while True:
        started = time.monotonic()
        try:
            sample_once()
        except Exception:
            LOG.exception("V45 SHADOW cycle failure")
        elapsed = time.monotonic() - started
        time.sleep(max(1.0, POLL_SECONDS - elapsed))


if __name__ == "__main__":
    run_forever()
