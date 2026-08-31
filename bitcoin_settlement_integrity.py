"""Bitcoin on-chain settlement integrity primitives.

This module deliberately does not create wallets, hold keys, sign transactions,
or influence exchange-trading signals. It validates externally supplied Bitcoin
settlement observations before a future custody or transfer workflow can credit
funds as final.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any, Iterable


_TXID_RE = re.compile(r"^[0-9a-f]{64}$")


class SettlementState(StrEnum):
    INVALID = "INVALID"
    UNSEEN = "UNSEEN"
    MEMPOOL = "MEMPOOL"
    REPLACEABLE = "REPLACEABLE"
    CONFLICTED = "CONFLICTED"
    REORG_RISK = "REORG_RISK"
    CONFIRMING = "CONFIRMING"
    FINAL = "FINAL"


@dataclass(frozen=True, slots=True)
class BitcoinSettlementAssessment:
    state: SettlementState
    confirmations: int
    required_confirmations: int
    final: bool
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "confirmations": self.confirmations,
            "required_confirmations": self.required_confirmations,
            "final": self.final,
            "reason": self.reason,
        }


def normalize_txid(value: Any) -> str:
    """Return a canonical transaction id or raise ValueError.

    A transaction id is display-order hex. This function validates identity
    only; it does not prove inclusion in the best blockchain.
    """
    txid = str(value or "").strip().lower()
    if not _TXID_RE.fullmatch(txid):
        raise ValueError("Bitcoin transaction id must be exactly 64 hexadecimal characters")
    return txid


def confirmation_depth(*, tip_height: int, block_height: int | None) -> int:
    """Calculate best-chain confirmation depth from validated block heights."""
    if block_height is None:
        return 0
    if isinstance(tip_height, bool) or isinstance(block_height, bool):
        raise ValueError("Block heights must be integers")
    if not isinstance(tip_height, int) or not isinstance(block_height, int):
        raise ValueError("Block heights must be integers")
    if tip_height < 0 or block_height < 0:
        raise ValueError("Block heights cannot be negative")
    if block_height > tip_height:
        raise ValueError("Block height cannot exceed chain tip")
    return tip_height - block_height + 1


def assess_settlement(
    *,
    txid: Any,
    tip_height: int,
    block_height: int | None,
    in_best_chain: bool,
    seen_in_mempool: bool = False,
    replaceable: bool = False,
    conflicting_txids: Iterable[Any] = (),
    required_confirmations: int = 6,
) -> BitcoinSettlementAssessment:
    """Classify a Bitcoin transfer without treating zero-conf as final.

    The caller must obtain chain observations from an independently verified
    Bitcoin node or an explicitly trusted provider. A broker fill is not an
    on-chain settlement and must never be passed to this function as one.
    """
    try:
        normalize_txid(txid)
    except ValueError as exc:
        return BitcoinSettlementAssessment(SettlementState.INVALID, 0, max(1, required_confirmations), False, str(exc))

    if isinstance(required_confirmations, bool) or not isinstance(required_confirmations, int):
        return BitcoinSettlementAssessment(SettlementState.INVALID, 0, 1, False, "Required confirmations must be an integer")
    required = max(1, required_confirmations)

    conflicts: list[str] = []
    for candidate in conflicting_txids:
        try:
            conflicts.append(normalize_txid(candidate))
        except ValueError:
            return BitcoinSettlementAssessment(SettlementState.INVALID, 0, required, False, "Conflicting transaction id is invalid")
    if conflicts:
        return BitcoinSettlementAssessment(SettlementState.CONFLICTED, 0, required, False, "Conflicting spend observed")

    try:
        confirmations = confirmation_depth(tip_height=tip_height, block_height=block_height)
    except ValueError as exc:
        return BitcoinSettlementAssessment(SettlementState.INVALID, 0, required, False, str(exc))

    if block_height is not None and not in_best_chain:
        return BitcoinSettlementAssessment(SettlementState.REORG_RISK, 0, required, False, "Transaction block is not in the best chain")
    if block_height is None:
        if not seen_in_mempool:
            return BitcoinSettlementAssessment(SettlementState.UNSEEN, 0, required, False, "Transaction is not observed")
        if replaceable:
            return BitcoinSettlementAssessment(SettlementState.REPLACEABLE, 0, required, False, "Unconfirmed transaction signals replaceability")
        return BitcoinSettlementAssessment(SettlementState.MEMPOOL, 0, required, False, "Transaction is unconfirmed")

    if confirmations < required:
        return BitcoinSettlementAssessment(
            SettlementState.CONFIRMING,
            confirmations,
            required,
            False,
            f"Waiting for {required - confirmations} additional confirmation(s)",
        )
    return BitcoinSettlementAssessment(SettlementState.FINAL, confirmations, required, True, "Required confirmation depth reached")


def credit_allowed(assessment: BitcoinSettlementAssessment) -> bool:
    """Fail-closed credit gate for a future deposit or custody adapter."""
    return assessment.state is SettlementState.FINAL and assessment.final
