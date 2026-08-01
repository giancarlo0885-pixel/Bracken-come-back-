from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ModelStatus(str, Enum):
    EXPERIMENTAL = "experimental"
    SHADOW = "shadow"
    APPROVED = "approved"
    DEGRADED = "degraded"
    DISABLED = "disabled"


@dataclass
class ModelRecord:
    model: str
    model_version: str
    status: ModelStatus
    reason: str = ""


_REGISTRY: dict[tuple[str, str], ModelRecord] = {}


def register_model(model: str, model_version: str, status: str = "experimental", reason: str = "") -> ModelRecord:
    record = ModelRecord(model, model_version, ModelStatus(status), reason)
    _REGISTRY[(model, model_version)] = record
    return record


def model_status(model: str, model_version: str) -> ModelStatus:
    record = _REGISTRY.get((model, model_version))
    return record.status if record else ModelStatus.SHADOW


def model_can_approve_execution(model: str, model_version: str) -> bool:
    return model_status(model, model_version) == ModelStatus.APPROVED


register_model("regime-aware ensemble", "v36-advisor-foundation", "shadow", "requires walk-forward validation")
register_model("log-return diffusion", "v36-timeframe-aware", "shadow", "requires walk-forward validation")
