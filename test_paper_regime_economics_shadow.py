from __future__ import annotations

from pathlib import Path

import paper_regime_economics_shadow as regime


def _paper(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("PAPER_AUTONOMOUS_LEARNING", "true")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")


def test_shadow_is_fail_closed_for_live(monkeypatch):
    _paper(monkeypatch)
    assert regime.active() is True
    monkeypatch.setenv("LIVE_TRADING_ARMED", "true")
    assert regime.active() is False


def test_existing_market_memory_regime_wins():
    assert regime.classify_regime(
        feature_snapshot={"trend_strength": -0.2, "volatility_20d": 0.9},
        memory_regime="RISK OFF",
    ) == "risk_off"


def test_regime_uses_only_entry_features():
    assert regime.classify_regime(
        feature_snapshot={"trend_strength": 0.10, "momentum_20d": 0.08, "volatility_20d": 0.75}
    ) == "trend_up__high_vol"
    assert regime.classify_regime(
        feature_snapshot={"trend_strength": -0.12, "momentum_20d": -0.06, "volatility_20d": 0.20}
    ) == "trend_down__low_vol"
    assert regime.classify_regime(
        feature_snapshot={"trend_strength": 0.01, "momentum_20d": -0.02}
    ) == "range__vol_unknown"


def test_unknown_features_do_not_invent_volatility():
    assert regime.classify_regime(feature_snapshot={}) == "range__vol_unknown"


def test_mfe_is_anchored_at_zero_when_all_forward_prices_are_below_entry():
    mfe, mae = regime._excursion_percentages(100.0, [99.5, 98.0, 99.0])
    assert mfe == 0.0
    assert round(mae, 8) == -2.0


def test_mae_is_anchored_at_zero_when_all_forward_prices_are_above_entry():
    mfe, mae = regime._excursion_percentages(100.0, [100.5, 102.0, 101.0])
    assert round(mfe, 8) == 2.0
    assert mae == 0.0


def test_excursions_preserve_observed_two_sided_path():
    mfe, mae = regime._excursion_percentages(100.0, [97.5, 103.25, 101.0])
    assert round(mfe, 8) == 3.25
    assert round(mae, 8) == -2.5


def test_excursions_remain_unavailable_without_forward_samples():
    assert regime._excursion_percentages(100.0, []) == (None, None)
    assert regime._excursion_percentages(0.0, [100.0]) == (None, None)


def test_market_normalization_supports_cash_and_stock_alias():
    assert regime._normalize_market("crypto") == "crypto"
    assert regime._normalize_market("cash") == "cash"
    assert regime._normalize_market("stock") == "cash"


def test_regime_shadow_queries_are_market_scoped_not_crypto_hardcoded():
    source = Path("paper_regime_economics_shadow.py").read_text(encoding="utf-8")
    assert "WHERE market=%s AND COALESCE(quantity,0) > 0" in source
    assert "WHERE market=%s AND side='SELL'" in source
    assert "WHERE market=%s AND symbol=%s" in source
    assert "VALUES (%s,%s,%s,%s,'canonical_position',%s)" in source
    assert "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)" in source


def test_stock_worker_installs_cash_regime_learning():
    source = Path("stock_worker.py").read_text(encoding="utf-8")
    assert "from paper_regime_economics_shadow import install_paper_regime_economics_shadow" in source
    assert 'install_paper_regime_economics_shadow("cash")' in source


class _Result:
    def __init__(self, rows):
        self._rows = rows
    def fetchall(self):
        return self._rows


class _LotConn:
    def execute(self, sql, params=None):
        if "FROM position_lots" in sql:
            return _Result([{"entry_fees": 2.0, "quantity_opened": 10.0}])
        return _Result([])


def test_round_trip_accounting_allocates_entry_and_exit_fees_once():
    net, fees, provenance = regime._round_trip_accounting(
        _LotConn(),
        {
            "market": "crypto",
            "symbol": "BTC-USD",
            "entry_time": "2026-09-24T00:00:00+00:00",
            "entry_price": 100.0,
            "quantity": 5.0,
            "entry_signal_id": "sig-1",
            "entry_decision_id": "dec-1",
            "fees": 1.0,
            "gross_pnl": 10.0,
        },
    )
    assert provenance == "exact_lot"
    assert fees == 2.0
    assert net == 8.0


def test_round_trip_cost_columns_are_additive_research_fields():
    source = Path("paper_regime_economics_shadow.py").read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS round_trip_net_pnl DOUBLE PRECISION" in source
    assert "ADD COLUMN IF NOT EXISTS round_trip_fees DOUBLE PRECISION" in source
    assert "ADD COLUMN IF NOT EXISTS cost_provenance TEXT" in source
    assert "COALESCE(round_trip_net_pnl,net_pnl)" in source


def test_summary_market_filter_casts_nullable_parameter_for_postgres():
    source = Path("paper_regime_economics_shadow.py").read_text(encoding="utf-8")
    assert "WHERE (%s::text IS NULL OR market=%s::text)" in source
