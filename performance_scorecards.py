from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass
class StrategyScorecard:
    strategy: str
    symbol: str
    sector: str
    asset_class: str
    market_regime: str
    holding_period: str
    confidence_range: str
    opportunity_score_range: str
    advisor_action: str
    model_version: str
    total_return: float
    benchmark_return: float
    alpha: float
    maximum_drawdown: float
    sharpe: float
    sortino: float
    calmar_ratio: float
    profit_factor: float
    expectancy: float
    win_rate: float
    loss_rate: float
    average_win: float
    average_loss: float
    turnover: float
    average_holding_time: float
    slippage: float
    fees: float
    rejected_trade_count: int
    data_quality_rejection_count: int
    status: str


def build_strategy_scorecard(records: Iterable[dict[str, Any]], *, strategy: str, model_version: str = "") -> StrategyScorecard:
    items = list(records)
    returns = [float(item.get("return_pct") or 0.0) for item in items]
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value <= 0]
    total = sum(returns)
    benchmark = sum(float(item.get("benchmark_return_pct") or 0.0) for item in items)
    max_drawdown = min(0.0, min(returns or [0.0]))
    avg = total / len(returns) if returns else 0.0
    variance = sum((value - avg) ** 2 for value in returns) / len(returns) if returns else 0.0
    sharpe = avg / (variance ** 0.5) if variance > 0 else 0.0
    downside = [value for value in returns if value < 0]
    downside_var = sum(value * value for value in downside) / len(downside) if downside else 0.0
    sortino = avg / (downside_var ** 0.5) if downside_var > 0 else 0.0
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_win / gross_loss if gross_loss > 0 else (gross_win if gross_win > 0 else 0.0)
    win_rate = len(wins) / len(returns) if returns else 0.0
    status = "approved" if len(items) >= 30 and win_rate >= 0.50 and profit_factor >= 1.1 else "shadow"
    return StrategyScorecard(
        strategy=strategy,
        symbol=str(items[0].get("symbol") if items else ""),
        sector=str(items[0].get("sector") if items else ""),
        asset_class=str(items[0].get("asset_class") if items else ""),
        market_regime=str(items[0].get("market_regime") if items else ""),
        holding_period=str(items[0].get("holding_period") if items else ""),
        confidence_range=str(items[0].get("confidence_range") if items else ""),
        opportunity_score_range=str(items[0].get("opportunity_score_range") if items else ""),
        advisor_action=str(items[0].get("advisor_action") if items else ""),
        model_version=model_version,
        total_return=total,
        benchmark_return=benchmark,
        alpha=total - benchmark,
        maximum_drawdown=max_drawdown,
        sharpe=sharpe,
        sortino=sortino,
        calmar_ratio=(total / abs(max_drawdown)) if max_drawdown < 0 else 0.0,
        profit_factor=profit_factor,
        expectancy=avg,
        win_rate=win_rate,
        loss_rate=len(losses) / len(returns) if returns else 0.0,
        average_win=sum(wins) / len(wins) if wins else 0.0,
        average_loss=sum(losses) / len(losses) if losses else 0.0,
        turnover=sum(float(item.get("turnover") or 0.0) for item in items),
        average_holding_time=sum(float(item.get("holding_minutes") or 0.0) for item in items) / len(items) if items else 0.0,
        slippage=sum(float(item.get("slippage") or 0.0) for item in items),
        fees=sum(float(item.get("fees") or 0.0) for item in items),
        rejected_trade_count=sum(1 for item in items if item.get("rejected")),
        data_quality_rejection_count=sum(1 for item in items if item.get("data_quality_rejected")),
        status=status,
    )
