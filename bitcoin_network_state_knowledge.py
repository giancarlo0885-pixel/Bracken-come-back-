from __future__ import annotations

from dataclasses import dataclass, asdict
import math
from typing import Any


@dataclass(frozen=True)
class BitcoinNetworkState:
    available: bool
    confirmations: int | None = None
    attacker_share: float | None = None
    attacker_catchup_probability: float | None = None
    hash_rate_change: float | None = None
    difficulty_change: float | None = None
    block_interval_ratio: float | None = None
    fee_pressure: float | None = None
    mempool_pressure: float | None = None
    subsidy_btc: float | None = None
    blocks_to_halving: int | None = None
    halving_progress: float | None = None
    miner_revenue_stress: float | None = None
    network_security_score: float = 0.0
    network_activity_score: float = 0.0
    miner_stress_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clip(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def attacker_catchup_probability(*, q: float, z: int) -> float | None:
    """Satoshi whitepaper section-11 Poisson approximation.

    Measurement only: this is a chain-security context metric, never a price forecast.
    """
    q = float(q)
    z = int(z)
    if z < 0 or q < 0.0 or q > 1.0:
        return None
    p = 1.0 - q
    if q >= p:
        return 1.0
    if z == 0:
        return 1.0
    lam = z * q / p
    poisson = math.exp(-lam)
    total = 1.0
    cumulative = poisson
    for k in range(0, z + 1):
        if k > 0:
            poisson *= lam / k
            cumulative += poisson
        total -= poisson * (1.0 - (q / p) ** (z - k))
    return _clip(total, 0.0, 1.0)


def block_subsidy_btc(height: int) -> float:
    halvings = max(0, int(height)) // 210_000
    if halvings >= 64:
        return 0.0
    satoshis = 5_000_000_000 >> halvings
    return satoshis / 100_000_000.0


def halving_context(height: int) -> tuple[int, float]:
    height = max(0, int(height))
    remainder = height % 210_000
    blocks_to = 210_000 - remainder if remainder else 210_000
    progress = remainder / 210_000.0
    return blocks_to, progress


def assess_bitcoin_network_state(metrics: dict[str, Any] | None) -> BitcoinNetworkState:
    """Convert already-observed Bitcoin network metrics into attributable features.

    No metric is inferred when the provider did not supply it. The output is designed
    for paper Market Memory / forward-return research, not direct trade authorization.
    """
    m = dict(metrics or {})
    if not m:
        return BitcoinNetworkState(available=False)

    height_raw = m.get("block_height")
    height = int(height_raw) if _finite(height_raw) is not None else None
    confirmations = int(m["confirmations"]) if _finite(m.get("confirmations")) is not None else None
    q = _finite(m.get("attacker_hash_share"))
    catchup = attacker_catchup_probability(q=q, z=confirmations) if q is not None and confirmations is not None else None
    hash_change = _finite(m.get("hash_rate_change"))
    difficulty_change = _finite(m.get("difficulty_change"))
    interval = _finite(m.get("block_interval_seconds"))
    interval_ratio = interval / 600.0 if interval is not None and interval > 0 else None
    fee_pressure = _finite(m.get("fee_pressure"))
    mempool_pressure = _finite(m.get("mempool_pressure"))
    revenue_change = _finite(m.get("miner_revenue_change"))

    subsidy = block_subsidy_btc(height) if height is not None else None
    blocks_to, progress = halving_context(height) if height is not None else (None, None)

    security_parts = []
    if hash_change is not None:
        security_parts.append(_clip(hash_change / 0.20))
    if difficulty_change is not None:
        security_parts.append(_clip(difficulty_change / 0.20))
    if interval_ratio is not None:
        security_parts.append(_clip((1.0 - abs(interval_ratio - 1.0)) / 0.25))
    if catchup is not None:
        security_parts.append(_clip(1.0 - 2.0 * catchup))
    security = sum(security_parts) / len(security_parts) if security_parts else 0.0

    activity_parts = []
    if fee_pressure is not None:
        activity_parts.append(_clip(fee_pressure))
    if mempool_pressure is not None:
        activity_parts.append(_clip(mempool_pressure))
    activity = sum(activity_parts) / len(activity_parts) if activity_parts else 0.0

    stress = _clip(-revenue_change / 0.25) if revenue_change is not None else 0.0

    return BitcoinNetworkState(
        available=True,
        confirmations=confirmations,
        attacker_share=q,
        attacker_catchup_probability=catchup,
        hash_rate_change=hash_change,
        difficulty_change=difficulty_change,
        block_interval_ratio=interval_ratio,
        fee_pressure=fee_pressure,
        mempool_pressure=mempool_pressure,
        subsidy_btc=subsidy,
        blocks_to_halving=blocks_to,
        halving_progress=progress,
        miner_revenue_stress=revenue_change,
        network_security_score=security,
        network_activity_score=activity,
        miner_stress_score=stress,
    )
