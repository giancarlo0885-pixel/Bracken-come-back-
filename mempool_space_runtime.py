from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

from bitcoin_network_state_knowledge import assess_bitcoin_network_state
from mempool_space_client import MempoolSpaceClient, MempoolSpaceError, MempoolSpaceSnapshot


log = logging.getLogger("mempool-space-runtime")
_LOCK = threading.Lock()
_CACHE: MempoolSpaceSnapshot | None = None
_CACHE_FETCHED_AT = 0.0
_LAST_ERROR_LOG_AT = 0.0


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


def _btc_symbol(symbol: Any) -> bool:
    text = str(symbol or "").strip().upper().replace("/", "-")
    return text in {"BTC-USD", "BTCUSD"}


def _snapshot(client: MempoolSpaceClient | None = None) -> MempoolSpaceSnapshot | None:
    global _CACHE, _CACHE_FETCHED_AT, _LAST_ERROR_LOG_AT
    now = time.time()
    ttl = max(60.0, _float_env("MEMPOOL_SPACE_CACHE_SECONDS", 300.0))
    max_stale = max(ttl, _float_env("MEMPOOL_SPACE_MAX_STALE_SECONDS", 1800.0))

    with _LOCK:
        if _CACHE is not None and now - _CACHE_FETCHED_AT < ttl:
            return _CACHE
        try:
            fresh = (client or MempoolSpaceClient()).snapshot()
        except MempoolSpaceError as exc:
            # Provider failure is missing evidence only; never fabricate values and
            # never block or authorize a paper trade because of this research feed.
            if now - _LAST_ERROR_LOG_AT >= 300.0:
                log.warning(
                    "MEMPOOL.SPACE | status=UNAVAILABLE | execution_impact=NONE | reason=%s",
                    str(exc)[:120],
                )
                _LAST_ERROR_LOG_AT = now
            if _CACHE is not None and now - _CACHE_FETCHED_AT <= max_stale:
                return _CACHE
            return None

        _CACHE = fresh
        _CACHE_FETCHED_AT = now
        log.info(
            "MEMPOOL.SPACE | status=PASS | height=%s | hashrate=%s | difficulty=%s | "
            "mempool_count=%s | fastest_fee=%s | cache=%ss | execution_impact=NONE",
            fresh.block_height,
            f"{fresh.current_hashrate:.3e}" if fresh.current_hashrate is not None else "NA",
            f"{fresh.current_difficulty:.3e}" if fresh.current_difficulty is not None else "NA",
            fresh.mempool_count if fresh.mempool_count is not None else "NA",
            fresh.fastest_fee_sat_vb if fresh.fastest_fee_sat_vb is not None else "NA",
            int(ttl),
        )
        return fresh


def _attach(signal: Any, snapshot: MempoolSpaceSnapshot) -> Any:
    if signal is None:
        return signal
    state = assess_bitcoin_network_state(snapshot.knowledge_metrics())
    values = dict(snapshot.provenance())
    values.update({
        "btc_network_available": state.available,
        "btc_network_subsidy_btc": state.subsidy_btc,
        "btc_network_blocks_to_halving": state.blocks_to_halving,
        "btc_network_halving_progress": state.halving_progress,
        "btc_network_security_score": state.network_security_score,
        "btc_network_activity_score": state.network_activity_score,
        "btc_network_miner_stress_score": state.miner_stress_score,
        "btc_network_execution_impact": "NONE",
    })
    if isinstance(signal, dict):
        signal.update(values)
    else:
        for key, value in values.items():
            try:
                setattr(signal, key, value)
            except Exception:
                continue
    return signal


def install_mempool_space_network_context(worker_module: Any, client: MempoolSpaceClient | None = None) -> bool:
    """Attach cached, observed mempool.space metrics to BTC signals for learning only."""
    original = getattr(worker_module, "analyze_market", None)
    if original is None:
        return False
    if getattr(original, "_mempool_space_network_context_v1", False):
        return True

    def wrapped(symbol: Any, history: Any, news_sentiment: float = 0.0):
        signal = original(symbol, history, news_sentiment)
        if signal is None or not _btc_symbol(symbol):
            return signal
        snapshot = _snapshot(client)
        return _attach(signal, snapshot) if snapshot is not None else signal

    wrapped._mempool_space_network_context_v1 = True  # type: ignore[attr-defined]
    wrapped._mempool_space_network_context_original = original  # type: ignore[attr-defined]
    worker_module.analyze_market = wrapped
    log.info(
        "Installed mempool.space Bitcoin network context | cache=ACTIVE | provenance=OBSERVED_ONLY | execution_impact=NONE"
    )

    # The cohort challenger is strictly shadow-only. It starts only under the
    # same paper/autonomous/live-disarmed conditions enforced by its own active().
    try:
        from paper_btc_network_challenger_shadow import install_btc_network_shadow_challenger
        install_btc_network_shadow_challenger()
    except Exception as exc:
        log.warning(
            "BTC NETWORK SHADOW | install=DEGRADED | execution_impact=NONE | reason=%s",
            exc.__class__.__name__,
        )
    return True
