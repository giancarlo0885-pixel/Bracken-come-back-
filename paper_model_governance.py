from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
import json
import math
import os
from typing import Any, Iterable

from capital_model_governance import model_governance_assessment
from database import rows
from forecast_calibration import evaluate_probability_calibration


RESEARCH = "RESEARCH"
PAPER_EXPLORATORY = "PAPER_EXPLORATORY"
PAPER_QUALIFIED = "PAPER_QUALIFIED"
CAPITAL_QUALIFIED = "CAPITAL_QUALIFIED"


@dataclass(frozen=True)
class PaperModelGovernanceAssessment:
    model: str
    model_version: str
    tier: str
    exploratory_eligible: bool
    paper_qualified: bool
    capital_qualified: bool
    sample_count: int
    directional_accuracy: float | None
    expected_calibration_error: float | None
    brier_skill_score: float | None
    temporal_leakage_ok: bool
    recent_walk_forward_runs: int
    distinct_symbols: int
    closed_paper_trades: int
    paper_expectancy: float | None
    paper_profit_factor: float | None
    reasons: list[str]
    thresholds: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _int_env(name: str, default: int, minimum: int, maximum: int | None = None) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _float_env(name: str, default: float, minimum: float | None = None, maximum: float | None = None) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    if not math.isfinite(value):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _parse_ts(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {}
        except Exception:
            return {}
    return {}


def _thresholds() -> dict[str, Any]:
    exploratory = _float_env("PAPER_EXPLORATORY_MIN_BRIER_SKILL", -0.01, -1.0, 1.0)
    qualified = _float_env("PAPER_QUALIFIED_MIN_BRIER_SKILL", 0.00, -1.0, 1.0)
    # Paper qualification must never be easier than exploratory qualification.
    qualified = max(exploratory, qualified)
    return {
        "exploratory_min_brier_skill": exploratory,
        "paper_qualified_min_brier_skill": qualified,
        "minimum_validation_samples": _int_env("PAPER_MODEL_MIN_VALIDATION_SAMPLES", 1000, 30, 100000),
        "minimum_directional_accuracy": _float_env("PAPER_MODEL_MIN_DIRECTIONAL_ACCURACY", 0.52, 0.0, 1.0),
        "maximum_ece": _float_env("PAPER_MODEL_MAX_ECE", 0.05, 0.0, 1.0),
        "minimum_distinct_symbols": _int_env("PAPER_MODEL_MIN_SYMBOLS", 2, 1, 20),
        "maximum_evidence_age_days": _int_env("PAPER_MODEL_EVIDENCE_MAX_AGE_DAYS", 30, 1, 365),
        "minimum_closed_paper_trades": _int_env("PAPER_QUALIFIED_MIN_CLOSED_TRADES", 30, 1, 100000),
        "minimum_paper_expectancy": _float_env("PAPER_QUALIFIED_MIN_EXPECTANCY", 0.0, -1000000.0, 1000000.0),
        "minimum_paper_profit_factor": _float_env("PAPER_QUALIFIED_MIN_PROFIT_FACTOR", 1.0, 0.0, 1000.0),
        # Observability only. Real-capital governance reads this independently and
        # is intentionally never derived from any PAPER_* setting.
        "capital_min_brier_skill": _float_env("CAPITAL_MIN_BRIER_SKILL", 0.02, -1.0, 1.0),
    }


def classify_paper_model_metrics(
    model: str,
    model_version: str,
    metrics: dict[str, Any],
    *,
    temporal_leakage_ok: bool,
    recent_walk_forward_runs: int,
    distinct_symbols: int,
) -> PaperModelGovernanceAssessment:
    """Classify model evidence for simulated-paper experimentation only.

    This classifier cannot approve real-capital use. Capital approval remains
    exclusively owned by capital_model_governance.py and its CAPITAL_* thresholds.
    """
    thresholds = _thresholds()
    samples = int(metrics.get("sample_count") or 0)
    accuracy = _finite(metrics.get("directional_accuracy"))
    ece = _finite(metrics.get("expected_calibration_error"))
    brier_skill = _finite(metrics.get("brier_skill_score"))
    closed_paper_trades = int(metrics.get("closed_paper_trades") or 0)
    paper_expectancy = _finite(metrics.get("paper_expectancy"))
    paper_profit_factor = _finite(metrics.get("paper_profit_factor"))

    reasons: list[str] = []
    core_ok = True
    if samples < int(thresholds["minimum_validation_samples"]):
        core_ok = False
        reasons.append(
            f"only {samples} validation samples; needs {thresholds['minimum_validation_samples']}"
        )
    if accuracy is None or accuracy < float(thresholds["minimum_directional_accuracy"]):
        core_ok = False
        reasons.append(
            f"directional accuracy {accuracy} below {thresholds['minimum_directional_accuracy']}"
        )
    if ece is None or ece > float(thresholds["maximum_ece"]):
        core_ok = False
        reasons.append(f"ECE {ece} above {thresholds['maximum_ece']}")
    if not temporal_leakage_ok:
        core_ok = False
        reasons.append("temporal leakage evidence is not clean")
    if recent_walk_forward_runs <= 0:
        core_ok = False
        reasons.append("no recent walk-forward evidence")
    if distinct_symbols < int(thresholds["minimum_distinct_symbols"]):
        core_ok = False
        reasons.append(
            f"only {distinct_symbols} distinct walk-forward symbols; needs {thresholds['minimum_distinct_symbols']}"
        )

    paper_economics_ok = True
    if closed_paper_trades < int(thresholds["minimum_closed_paper_trades"]):
        paper_economics_ok = False
        reasons.append(
            f"only {closed_paper_trades} closed paper trades; needs "
            f"{thresholds['minimum_closed_paper_trades']}"
        )
    if paper_expectancy is None or paper_expectancy < float(thresholds["minimum_paper_expectancy"]):
        paper_economics_ok = False
        reasons.append(
            f"paper expectancy {paper_expectancy} below "
            f"{thresholds['minimum_paper_expectancy']}"
        )
    if paper_profit_factor is None or paper_profit_factor < float(thresholds["minimum_paper_profit_factor"]):
        paper_economics_ok = False
        reasons.append(
            f"paper profit factor {paper_profit_factor} below "
            f"{thresholds['minimum_paper_profit_factor']}"
        )

    exploratory = bool(
        core_ok
        and brier_skill is not None
        and brier_skill >= float(thresholds["exploratory_min_brier_skill"])
    )
    qualified = bool(
        core_ok
        and paper_economics_ok
        and brier_skill is not None
        and brier_skill >= float(thresholds["paper_qualified_min_brier_skill"])
    )

    if qualified:
        tier = PAPER_QUALIFIED
        reasons.append("paper evidence meets calibration and realized post-cost economics qualification")
    elif exploratory:
        tier = PAPER_EXPLORATORY
        reasons.append("paper evidence is within bounded exploratory Brier tolerance")
    else:
        tier = RESEARCH
        if brier_skill is None:
            reasons.append("Brier skill unavailable")
        elif core_ok:
            reasons.append(
                f"Brier skill {brier_skill} below exploratory minimum "
                f"{thresholds['exploratory_min_brier_skill']}"
            )

    return PaperModelGovernanceAssessment(
        model=str(model),
        model_version=str(model_version),
        tier=tier,
        exploratory_eligible=exploratory,
        paper_qualified=qualified,
        capital_qualified=False,
        sample_count=samples,
        directional_accuracy=accuracy,
        expected_calibration_error=ece,
        brier_skill_score=brier_skill,
        temporal_leakage_ok=bool(temporal_leakage_ok),
        recent_walk_forward_runs=int(recent_walk_forward_runs),
        distinct_symbols=int(distinct_symbols),
        closed_paper_trades=closed_paper_trades,
        paper_expectancy=paper_expectancy,
        paper_profit_factor=paper_profit_factor,
        reasons=reasons,
        thresholds=thresholds,
    )


def assess_paper_model_evidence(
    model: str,
    model_version: str,
    calibration_records: Iterable[dict[str, Any]],
    walk_forward_records: Iterable[dict[str, Any]],
    *,
    paper_trade_metrics: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> PaperModelGovernanceAssessment:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    records = [dict(item) for item in calibration_records]
    metrics = evaluate_probability_calibration(records, bins=10).to_dict()
    if paper_trade_metrics:
        metrics.update(dict(paper_trade_metrics))

    max_age_days = int(_thresholds()["maximum_evidence_age_days"])
    cutoff = now.astimezone(timezone.utc) - timedelta(days=max_age_days)
    recent: list[dict[str, Any]] = []
    for item in walk_forward_records:
        item = dict(item)
        if str(item.get("model") or "") != str(model):
            continue
        if str(item.get("model_version") or "") != str(model_version):
            continue
        created = _parse_ts(item.get("created_at"))
        if created is None or created < cutoff:
            continue
        recent.append(item)

    leakage_rows: list[dict[str, Any]] = []
    for item in recent:
        leakage = _json(item.get("leakage_checks"))
        probe = _json(leakage.get("future_mutation_probe"))
        if leakage.get("strict_ordering") is True and probe.get("ok") is True:
            leakage_rows.append(item)

    leakage_ok = bool(recent) and len(leakage_rows) == len(recent)
    distinct_symbols = len(
        {
            str(item.get("symbol") or "").upper()
            for item in leakage_rows
            if str(item.get("symbol") or "").strip()
        }
    )
    return classify_paper_model_metrics(
        model,
        model_version,
        metrics,
        temporal_leakage_ok=leakage_ok,
        recent_walk_forward_runs=len(recent),
        distinct_symbols=distinct_symbols,
    )



def _paper_trade_economics(model: str, model_version: str) -> dict[str, Any]:
    """Return realized after-cost paper economics from canonical closed trade ledger rows.

    Missing or unreadable evidence fails closed: paper qualification receives zero
    closed trades and unavailable expectancy/profit factor.
    """
    try:
        realized = rows(
            """
            SELECT net_pnl
            FROM trade_ledger
            WHERE market='crypto' AND side='SELL'
              AND model=%s AND COALESCE(model_version,'')=COALESCE(%s,'')
              AND net_pnl IS NOT NULL
            ORDER BY id DESC
            LIMIT 1000
            """,
            (model, model_version),
        )
    except Exception:
        return {
            "closed_paper_trades": 0,
            "paper_expectancy": None,
            "paper_profit_factor": None,
        }

    pnls = [
        value
        for item in (realized or [])
        if (value := _finite(dict(item).get("net_pnl"))) is not None
    ]
    if not pnls:
        return {
            "closed_paper_trades": 0,
            "paper_expectancy": None,
            "paper_profit_factor": None,
        }

    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_win / gross_loss if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)
    return {
        "closed_paper_trades": len(pnls),
        "paper_expectancy": sum(pnls) / len(pnls),
        "paper_profit_factor": profit_factor,
    }


def paper_model_governance_assessment(model: str, model_version: str) -> PaperModelGovernanceAssessment:
    """Read current evidence and return a non-authorizing paper model tier."""
    try:
        calibration = rows(
            """
            SELECT probability_up, realized_move_pct, created_at
            FROM forecast_validation
            WHERE model=%s AND COALESCE(model_version,'')=COALESCE(%s,'')
            ORDER BY id DESC
            LIMIT 1000
            """,
            (model, model_version),
        )
        walk_forward = rows(
            """
            SELECT run_id, model, model_version, symbol, status, metrics, leakage_checks, created_at
            FROM walk_forward_validation_runs
            WHERE model=%s AND COALESCE(model_version,'')=COALESCE(%s,'')
            ORDER BY created_at DESC
            LIMIT 20
            """,
            (model, model_version),
        )
        assessment = assess_paper_model_evidence(
            model,
            model_version,
            calibration,
            walk_forward,
            paper_trade_metrics=_paper_trade_economics(model, model_version),
        )
    except Exception as exc:
        return PaperModelGovernanceAssessment(
            model=str(model),
            model_version=str(model_version),
            tier=RESEARCH,
            exploratory_eligible=False,
            paper_qualified=False,
            capital_qualified=False,
            sample_count=0,
            directional_accuracy=None,
            expected_calibration_error=None,
            brier_skill_score=None,
            temporal_leakage_ok=False,
            recent_walk_forward_runs=0,
            distinct_symbols=0,
            closed_paper_trades=0,
            paper_expectancy=None,
            paper_profit_factor=None,
            reasons=[f"paper model evidence unavailable: {exc.__class__.__name__}"],
            thresholds=_thresholds(),
        )

    # Capital status can only strengthen the label after capital governance itself
    # approves the model. PAPER_* settings can never manufacture this state.
    try:
        capital = model_governance_assessment(model, model_version)
    except Exception:
        capital = None
    if capital is not None and bool(capital.eligible_for_approval):
        return replace(
            assessment,
            tier=CAPITAL_QUALIFIED,
            exploratory_eligible=True,
            paper_qualified=True,
            capital_qualified=True,
            reasons=list(assessment.reasons)
            + ["capital governance independently satisfies the stricter approval policy"],
        )
    return assessment


def paper_model_tier(model: str, model_version: str) -> str:
    return paper_model_governance_assessment(model, model_version).tier
