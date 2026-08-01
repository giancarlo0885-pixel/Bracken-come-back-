from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol


class BrokerMode(str, Enum):
    ADVISOR_ONLY = "advisor-only"
    READ_ONLY = "read-only"
    SHADOW = "shadow"
    MANUAL_APPROVAL_PAPER = "manual-approval paper"
    AUTOMATED_PAPER = "automated paper"
    LIVE_DISABLED = "live-disabled"


@dataclass
class OrderPreview:
    accepted: bool
    mode: BrokerMode
    estimated_fees: float
    estimated_slippage: float
    reason: str


class BrokerAdapter(Protocol):
    mode: BrokerMode

    def account_information(self) -> dict[str, Any]: ...
    def holdings(self) -> list[dict[str, Any]]: ...
    def cash(self) -> float: ...
    def buying_power(self) -> float: ...
    def open_orders(self) -> list[dict[str, Any]]: ...
    def completed_orders(self) -> list[dict[str, Any]]: ...
    def quotes(self, symbols: list[str]) -> dict[str, Any]: ...
    def order_preview(self, proposal: dict[str, Any]) -> OrderPreview: ...
    def order_submission(self, proposal: dict[str, Any]) -> dict[str, Any]: ...
    def order_cancellation(self, order_id: str) -> dict[str, Any]: ...
    def order_status(self, order_id: str) -> dict[str, Any]: ...


class DisabledBrokerAdapter:
    mode = BrokerMode.LIVE_DISABLED

    def account_information(self) -> dict[str, Any]:
        return {"mode": self.mode.value, "brokerage_submission": "disabled"}

    def holdings(self) -> list[dict[str, Any]]:
        return []

    def cash(self) -> float:
        return 0.0

    def buying_power(self) -> float:
        return 0.0

    def open_orders(self) -> list[dict[str, Any]]:
        return []

    def completed_orders(self) -> list[dict[str, Any]]:
        return []

    def quotes(self, symbols: list[str]) -> dict[str, Any]:
        return {str(symbol).upper(): {"status": "broker quotes disabled"} for symbol in symbols}

    def order_preview(self, proposal: dict[str, Any]) -> OrderPreview:
        return OrderPreview(False, self.mode, 0.0, 0.0, "real broker submission is disabled")

    def order_submission(self, proposal: dict[str, Any]) -> dict[str, Any]:
        return {"submitted": False, "status": "live-disabled", "reason": "real broker submission is disabled"}

    def order_cancellation(self, order_id: str) -> dict[str, Any]:
        return {"cancelled": False, "order_id": order_id, "reason": "no live broker order exists"}

    def order_status(self, order_id: str) -> dict[str, Any]:
        return {"order_id": order_id, "status": "live-disabled"}
