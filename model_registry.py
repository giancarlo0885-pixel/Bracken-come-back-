from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


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


def _coerce_status(value: Any) -> ModelStatus:
    try:
        return ModelStatus(str(value))
    except Exception:
        return ModelStatus.SHADOW


def register_model(model: str, model_version: str, status: str = "experimental", reason: str = "") -> ModelRecord:
    record = ModelRecord(model, model_version, _coerce_status(status), reason)
    _REGISTRY[(model, model_version)] = record
    try:
        from database import connect, utc_now

        with connect() as conn:
            conn.execute(
                """
                INSERT INTO model_registry (model, model_version, status, reason, created_at, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (model, model_version) DO NOTHING
                """,
                (model, model_version, record.status.value, reason, utc_now(), utc_now()),
            )
    except Exception:
        pass
    return record


def model_status(model: str, model_version: str) -> ModelStatus:
    try:
        from database import row

        record = row(
            """
            SELECT status
            FROM model_registry
            WHERE model=%s AND model_version=%s
            ORDER BY id DESC
            LIMIT 1
            """,
            (model, model_version),
        )
        if record and record.get("status"):
            return _coerce_status(record.get("status"))
    except Exception:
        pass
    record = _REGISTRY.get((model, model_version))
    return record.status if record else ModelStatus.SHADOW


def model_can_approve_execution(model: str, model_version: str) -> bool:
    return model_status(model, model_version) == ModelStatus.APPROVED


def update_model_status(
    model: str,
    model_version: str,
    new_status: str,
    *,
    actor: str,
    reason: str = "",
) -> ModelRecord:
    actor = str(actor or "").strip()
    if not actor:
        raise ValueError("model status governance requires a non-empty actor")
    status = _coerce_status(new_status)
    if status == ModelStatus.SHADOW and str(new_status) not in {ModelStatus.SHADOW.value, "shadow"}:
        raise ValueError("invalid model status")
    from database import connect, utc_now

    now = utc_now()
    with connect() as conn:
        existing = conn.execute(
            """
            SELECT status
            FROM model_registry
            WHERE model=%s AND model_version=%s
            FOR UPDATE
            """,
            (model, model_version),
        ).fetchone()
        old_status = _coerce_status(existing.get("status")) if existing else ModelStatus.SHADOW
        conn.execute(
            """
            INSERT INTO model_registry (model, model_version, status, reason, created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON CONFLICT (model, model_version) DO UPDATE SET
                status=EXCLUDED.status,
                reason=EXCLUDED.reason,
                updated_at=EXCLUDED.updated_at
            """,
            (model, model_version, status.value, reason, now, now),
        )
        conn.execute(
            """
            INSERT INTO model_registry_events
            (model, model_version, old_status, new_status, actor, reason, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            """,
            (model, model_version, old_status.value, status.value, actor, reason, now),
        )
    _REGISTRY[(model, model_version)] = ModelRecord(model, model_version, status, reason)
    return _REGISTRY[(model, model_version)]


register_model("regime-aware ensemble", "v36-advisor-foundation", "shadow", "requires walk-forward validation")
register_model("log-return diffusion", "v36-timeframe-aware", "shadow", "requires walk-forward validation")
register_model("crypto causal adaptive", "v40-causal-adaptive", "shadow", "requires causal short-horizon walk-forward validation")
register_model("crypto nested adaptive selector", "v41-nested-selector", "shadow", "requires nested past-only short-horizon walk-forward validation")
register_model("crypto sign transition selector", "v42-sign-transition", "shadow", "requires causal sign-transition walk-forward validation")
register_model("crypto selective sign transition", "v43-selective-transition", "shadow", "requires coverage-constrained selective walk-forward validation")
