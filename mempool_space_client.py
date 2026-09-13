from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


DEFAULT_BASE_URL = "https://mempool.space"
DEFAULT_TIMEOUT_SECONDS = 5.0


class MempoolSpaceError(RuntimeError):
    pass


@dataclass(frozen=True)
class MempoolSpaceSnapshot:
    observed_at: float
    source: str
    block_height: int | None
    current_hashrate: float | None
    current_difficulty: float | None
    hash_rate_change: float | None
    difficulty_change: float | None
    block_interval_seconds: float | None
    mempool_count: int | None
    mempool_vsize: float | None
    mempool_total_fee: float | None
    fastest_fee_sat_vb: float | None
    half_hour_fee_sat_vb: float | None
    hour_fee_sat_vb: float | None
    economy_fee_sat_vb: float | None
    minimum_fee_sat_vb: float | None
    fee_pressure: float | None
    mempool_pressure: float | None

    def knowledge_metrics(self) -> dict[str, Any]:
        return {
            "block_height": self.block_height,
            "hash_rate_change": self.hash_rate_change,
            "difficulty_change": self.difficulty_change,
            "block_interval_seconds": self.block_interval_seconds,
            "fee_pressure": self.fee_pressure,
            "mempool_pressure": self.mempool_pressure,
        }

    def provenance(self) -> dict[str, Any]:
        return {
            "btc_network_source": self.source,
            "btc_network_observed_at": self.observed_at,
            "btc_network_block_height": self.block_height,
            "btc_network_current_hashrate": self.current_hashrate,
            "btc_network_current_difficulty": self.current_difficulty,
            "btc_network_hash_rate_change": self.hash_rate_change,
            "btc_network_difficulty_change": self.difficulty_change,
            "btc_network_block_interval_seconds": self.block_interval_seconds,
            "btc_network_mempool_count": self.mempool_count,
            "btc_network_mempool_vsize": self.mempool_vsize,
            "btc_network_mempool_total_fee": self.mempool_total_fee,
            "btc_network_fastest_fee_sat_vb": self.fastest_fee_sat_vb,
            "btc_network_half_hour_fee_sat_vb": self.half_hour_fee_sat_vb,
            "btc_network_hour_fee_sat_vb": self.hour_fee_sat_vb,
            "btc_network_economy_fee_sat_vb": self.economy_fee_sat_vb,
            "btc_network_minimum_fee_sat_vb": self.minimum_fee_sat_vb,
            "btc_network_fee_pressure": self.fee_pressure,
            "btc_network_mempool_pressure": self.mempool_pressure,
        }


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: Any) -> int | None:
    number = _finite(value)
    return int(number) if number is not None else None


def _clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _window_change(points: Any, key: str, window: int = 7) -> float | None:
    if not isinstance(points, list):
        return None
    values = [_finite(item.get(key)) for item in points if isinstance(item, dict)]
    clean = [value for value in values if value is not None and value > 0]
    if len(clean) < max(4, window * 2):
        if len(clean) >= 2 and clean[0] > 0:
            return clean[-1] / clean[0] - 1.0
        return None
    previous = clean[-window * 2 : -window]
    recent = clean[-window:]
    prev_avg = sum(previous) / len(previous)
    recent_avg = sum(recent) / len(recent)
    return recent_avg / prev_avg - 1.0 if prev_avg > 0 else None


def _difficulty_change(payload: Any) -> float | None:
    if isinstance(payload, dict):
        for key in ("difficultyChange", "difficulty_change", "estimatedDifficultyChange"):
            value = _finite(payload.get(key))
            if value is not None:
                return value / 100.0 if abs(value) > 1.0 else value
        adjustments = payload.get("difficulty")
        if isinstance(adjustments, list) and adjustments:
            value = _finite(adjustments[-1].get("adjustment")) if isinstance(adjustments[-1], dict) else None
            if value is not None:
                # mempool.space mining/hashrate returns an adjustment multiplier near 1.0.
                return value - 1.0 if 0.5 <= value <= 1.5 else (value / 100.0 if abs(value) > 1.0 else value)
    return None


def _block_interval_seconds(payload: Any) -> float | None:
    if not isinstance(payload, dict):
        return None
    for key in ("timeAvg", "averageBlockTime", "average_block_time", "blockTime"):
        value = _finite(payload.get(key))
        if value is None or value <= 0:
            continue
        # Some APIs expose milliseconds; values in a plausible seconds range pass through.
        if value > 100_000:
            value /= 1000.0
        return value
    return None


def _fee_pressure(fees: dict[str, Any]) -> float | None:
    fastest = _finite(fees.get("fastestFee"))
    minimum = _finite(fees.get("minimumFee"))
    if fastest is None:
        return None
    baseline = max(1.0, minimum or 1.0)
    # Log normalization avoids letting temporary fee spikes dominate the feature.
    ratio = max(1.0, fastest / baseline)
    return _clip(math.log1p(ratio - 1.0) / math.log1p(100.0))


def _mempool_pressure(mempool: dict[str, Any]) -> float | None:
    vsize = _finite(mempool.get("vsize"))
    if vsize is None:
        return None
    # This is a bounded backlog feature, not a claim about node RAM capacity.
    return _clip(vsize / 100_000_000.0)


class MempoolSpaceClient:
    """Small public REST client for measurement-only Bitcoin network evidence."""

    def __init__(self, base_url: str | None = None, timeout: float | None = None, opener: Any | None = None):
        self.base_url = str(base_url or os.getenv("MEMPOOL_SPACE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = float(timeout or os.getenv("MEMPOOL_SPACE_TIMEOUT_SECONDS") or DEFAULT_TIMEOUT_SECONDS)
        self._opener = opener

    def _get_text(self, path: str) -> str:
        url = f"{self.base_url}{path}"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json,text/plain;q=0.9",
                "User-Agent": "Garibaldi-Market-Oracle/bitcoin-network-research",
            },
            method="GET",
        )
        try:
            if self._opener is not None:
                response = self._opener(request, timeout=self.timeout)
            else:
                response = urllib.request.urlopen(request, timeout=self.timeout)
            with response:
                status = int(getattr(response, "status", 200) or 200)
                body = response.read().decode("utf-8", errors="strict")
            if status != 200:
                raise MempoolSpaceError(f"HTTP_{status}")
            return body
        except urllib.error.HTTPError as exc:
            raise MempoolSpaceError(f"HTTP_{exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError, UnicodeError) as exc:
            raise MempoolSpaceError(exc.__class__.__name__) from exc

    def _get_json(self, path: str) -> Any:
        try:
            return json.loads(self._get_text(path))
        except json.JSONDecodeError as exc:
            raise MempoolSpaceError("INVALID_JSON") from exc

    def snapshot(self) -> MempoolSpaceSnapshot:
        height = _integer(self._get_text("/api/blocks/tip/height").strip())
        mining = self._get_json("/api/v1/mining/hashrate/1m")
        difficulty = self._get_json("/api/v1/difficulty-adjustment")
        mempool = self._get_json("/api/mempool")
        fees = self._get_json("/api/v1/fees/recommended")

        mining = mining if isinstance(mining, dict) else {}
        difficulty = difficulty if isinstance(difficulty, dict) else {}
        mempool = mempool if isinstance(mempool, dict) else {}
        fees = fees if isinstance(fees, dict) else {}

        current_hashrate = _finite(mining.get("currentHashrate"))
        current_difficulty = _finite(mining.get("currentDifficulty"))
        hash_change = _window_change(mining.get("hashrates"), "avgHashrate", window=7)
        difficulty_change = _difficulty_change(difficulty)
        if difficulty_change is None:
            difficulty_change = _difficulty_change(mining)

        return MempoolSpaceSnapshot(
            observed_at=time.time(),
            source="mempool.space",
            block_height=height,
            current_hashrate=current_hashrate,
            current_difficulty=current_difficulty,
            hash_rate_change=hash_change,
            difficulty_change=difficulty_change,
            block_interval_seconds=_block_interval_seconds(difficulty),
            mempool_count=_integer(mempool.get("count")),
            mempool_vsize=_finite(mempool.get("vsize")),
            mempool_total_fee=_finite(mempool.get("total_fee")),
            fastest_fee_sat_vb=_finite(fees.get("fastestFee")),
            half_hour_fee_sat_vb=_finite(fees.get("halfHourFee")),
            hour_fee_sat_vb=_finite(fees.get("hourFee")),
            economy_fee_sat_vb=_finite(fees.get("economyFee")),
            minimum_fee_sat_vb=_finite(fees.get("minimumFee")),
            fee_pressure=_fee_pressure(fees),
            mempool_pressure=_mempool_pressure(mempool),
        )
