from __future__ import annotations

from pathlib import Path
from datetime import datetime, timedelta, timezone
from copy import deepcopy
from contextlib import contextmanager

import pytest

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


def test_temporal_excursion_records_earliest_extrema_and_offsets():
    entry = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
    samples = [
        {"observed_at": entry + timedelta(seconds=30), "price": 101.0},
        {"observed_at": entry + timedelta(seconds=60), "price": 103.0},
        {"observed_at": entry + timedelta(seconds=90), "price": 103.0},
        {"observed_at": entry + timedelta(seconds=120), "price": 97.0},
    ]
    mfe, mae, t_mfe, t_mae, dt_mfe, dt_mae = regime._excursion_temporal_metrics(100.0, entry, samples)
    assert round(mfe, 8) == 3.0
    assert round(mae, 8) == -3.0
    assert t_mfe == entry + timedelta(seconds=60)
    assert t_mae == entry + timedelta(seconds=120)
    assert dt_mfe == 60
    assert dt_mae == 120


def test_temporal_excursion_uses_entry_anchor_when_path_is_one_sided():
    entry = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
    below = [{"observed_at": entry + timedelta(seconds=30), "price": 99.0}]
    mfe, mae, t_mfe, t_mae, dt_mfe, dt_mae = regime._excursion_temporal_metrics(100.0, entry, below)
    assert mfe == 0.0 and round(mae, 8) == -1.0
    assert t_mfe == entry and dt_mfe == 0
    assert t_mae == entry + timedelta(seconds=30) and dt_mae == 30


def test_temporal_excursion_does_not_fabricate_missing_observations():
    entry = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
    assert regime._excursion_temporal_metrics(100.0, entry, []) == (None, None, None, None, None, None)


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
    assert "SELECT observed_at,price FROM paper_regime_price_samples" in source
    assert "time_to_mfe_sec" in source
    assert "time_to_mae_sec" in source


def test_stock_worker_installs_cash_regime_learning():
    source = Path("stock_worker.py").read_text(encoding="utf-8")
    assert "from paper_regime_economics_shadow import install_paper_regime_economics_shadow" in source
    assert 'install_paper_regime_economics_shadow("cash")' in source


class _Result:
    def __init__(self, rows):
        self._rows = rows
    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


def _linked_close(*, trade_id="sell", fill_id="sell-fill", quantity=5.0,
                  exit_fee=1.0, buy_quantity=10.0, buy_fee=2.0):
    """A full persisted ID chain; similar prices/timestamps are not the proof."""
    from paper_fill_provenance import PROVENANCE_VERSION
    entry_time = datetime(2026, 9, 24, tzinfo=timezone.utc)
    exit_time = entry_time + timedelta(minutes=20)
    identity = dict(market="crypto", symbol="BTC-USD")
    paper = dict(broker_mode="PAPER", account_environment="PAPER")
    decision = dict(entry_signal_id="sig-1", entry_decision_id="dec-1", entry_forecast_id="forecast-1", entry_quote_id="quote-1")
    opening = dict(identity, **paper, **decision, trade_id="buy", side="BUY", quantity=buy_quantity,
                   entry_time=entry_time, entry_price=100.0, fees=buy_fee,
                   gross_pnl=0.0, net_pnl=-buy_fee)
    trade = dict(identity, **paper, **decision, trade_id=trade_id, side="SELL", quantity=quantity,
                 entry_time=entry_time, exit_time=exit_time, entry_price=100.0, exit_price=102.0,
                 fees=exit_fee, gross_pnl=quantity * 2, net_pnl=quantity * 2 - exit_fee,
                 strategy="council", feature_snapshot={})
    lot = dict(identity, **paper, lot_id="original-lot", opened_at=entry_time, entry_price=100.0,
               quantity_opened=buy_quantity, entry_fees=buy_fee)

    def fill_chain(side, q, price, fee, identity_id, observed, execution_id):
        execution = dict(identity, id=execution_id, side=side, quantity=q, price=price,
                         value=q * price, fees=fee, created_at=observed,
                         gross_realized_pnl=q * (price - 100) if side == "SELL" else 0.0,
                         realized_pnl=q * (price - 100) - fee if side == "SELL" else 0.0)
        order = dict(identity, side=side, order_id=identity_id + "-order", filled_quantity=q,
                     filled_notional=q * price, average_fill_price=price, fee_amount=fee)
        fill = dict(identity, side=side, fill_id=identity_id, order_id=order["order_id"],
                    canonical_trade_id=execution_id, quantity=q, fill_price=price,
                    notional=q * price, fee_amount=fee,
                    created_at=observed + timedelta(seconds=1))
        return execution, order, fill

    execution, order, fill = fill_chain("SELL", quantity, 102.0, exit_fee, fill_id, exit_time, 2)
    entry_execution, entry_order, entry_fill = fill_chain("BUY", buy_quantity, 100.0, buy_fee,
                                                        "buy-fill", entry_time, 1)
    allocation = dict(trade_id=trade_id, lot_id=lot["lot_id"], fill_id=fill_id, side="SELL",
                      allocated_quantity=quantity, allocated_fee=exit_fee, provenance_version=PROVENANCE_VERSION,
                      ledger=deepcopy(trade), lot=lot, execution=execution, paper_order=order, fill=fill,
                      total_quantity=quantity, total_fee=exit_fee, total_gross_pnl=quantity * 2,
                      lot_closed_quantity=quantity,
                      entry_allocation=dict(trade_id="buy", lot_id=lot["lot_id"], fill_id="buy-fill",
                                            side="BUY", quantity=buy_quantity, fee_amount=buy_fee,
                                            provenance_version=PROVENANCE_VERSION),
                      entry_ledger=opening, entry_execution=entry_execution, entry_order=entry_order,
                      entry_fill=entry_fill, entry_total_quantity=buy_quantity, entry_total_fee=buy_fee,
                      entry_total_gross_pnl=0.0)
    return trade, allocation


def test_round_trip_accounting_allocates_entry_and_exit_fees_once():
    trade, allocation = _linked_close()
    net, fees, provenance = regime._round_trip_accounting(
        None, trade, allocations=[allocation],
    )
    assert provenance == "exact_lot"
    assert fees == 2.0
    assert net == 8.0


def test_partial_closes_allocate_original_buy_fee_without_residual_lot_bias():
    first, first_allocation = _linked_close(quantity=4, exit_fee=.4)
    second, second_allocation = _linked_close(trade_id="sell-2", fill_id="sell-fill-2",
                                             quantity=6, exit_fee=.6)
    first_allocation["lot_closed_quantity"] = second_allocation["lot_closed_quantity"] = 10
    results = [regime._round_trip_accounting(None, trade, allocations=[allocation])
               for trade, allocation in ((first, first_allocation), (second, second_allocation))]
    assert [r[2] for r in results] == ["exact_lot", "exact_lot"]
    assert sum(r[1] for r in results) == pytest.approx(3.0)
    assert sum(r[0] for r in results) == pytest.approx(17.0)


def test_explicit_zero_fees_are_valid_without_imputing_missing_fees():
    trade, allocation = _linked_close(exit_fee=0, buy_fee=0)
    assert regime._round_trip_accounting(None, trade, allocations=[allocation]) == (10, 0, "exact_lot")


@pytest.mark.parametrize("field", ["gross_pnl", "fees", "quantity"])
@pytest.mark.parametrize("invalid", [None, float("nan"), float("inf"), True])
def test_round_trip_accounting_rejects_missing_or_nonfinite_cost_inputs(field, invalid):
    trade, allocation = _linked_close()
    trade[field] = invalid
    assert regime._round_trip_accounting(None, trade, allocations=[allocation]) == (
        None, None, "legacy_unverified_fill_lot")


@pytest.mark.parametrize("object_name,field", [("entry_fill", "fee_amount"),
                                              ("entry_allocation", "fee_amount"),
                                              ("lot", "entry_fees"), ("fill", "fee_amount")])
@pytest.mark.parametrize("invalid", [None, -1, 99, float("nan"), float("inf")])
def test_linked_missing_or_negative_fees_never_become_zero(object_name, field, invalid):
    trade, allocation = _linked_close()
    allocation[object_name][field] = invalid
    assert regime._round_trip_accounting(None, trade, allocations=[allocation]) == (
        None, None, "legacy_unverified_fill_lot")


@pytest.mark.parametrize("field,invalid", [("gross_pnl", 11), ("net_pnl", 10)])
def test_matching_ledger_snapshot_does_not_certify_incorrect_pnl_arithmetic(field, invalid):
    trade, allocation = _linked_close()
    trade[field] = allocation["ledger"][field] = invalid
    assert regime._round_trip_accounting(None, trade, allocations=[allocation]) == (
        None, None, "legacy_unverified_fill_lot")


def test_matching_original_lot_and_fill_cannot_hide_buy_net_missing_entry_fee():
    trade, allocation = _linked_close()
    allocation["entry_ledger"]["net_pnl"] = 0
    assert regime._round_trip_accounting(None, trade, allocations=[allocation]) == (
        None, None, "legacy_unverified_fill_lot")


@pytest.mark.parametrize("field", ["trade_id", "lot_id", "fill_id"])
def test_round_trip_requires_persisted_original_fill_and_lot_identity(field):
    trade, allocation = _linked_close()
    allocation[field] = "different-id"
    assert regime._round_trip_accounting(None, trade, allocations=[allocation]) == (
        None, None, "legacy_unverified_fill_lot")


def test_same_symbol_timestamp_and_price_cannot_replace_absent_fill_link():
    trade, _ = _linked_close()
    assert regime._round_trip_accounting(None, trade, allocations=[]) == (
        None, None, "legacy_unverified_fill_lot")


def test_individually_valid_sell_cannot_certify_an_overclosed_original_lot():
    trade, allocation = _linked_close(quantity=5, buy_quantity=10)
    # Other committed SELL links can exhaust this lot even when this single
    # allocation, its fill totals and its copied ledger are internally valid.
    allocation["lot_closed_quantity"] = 11
    assert regime._round_trip_accounting(None, trade, allocations=[allocation]) == (
        None, None, "legacy_unverified_fill_lot")


def test_missing_cumulative_lot_quantity_is_unknown_accounting_evidence():
    trade, allocation = _linked_close()
    allocation.pop("lot_closed_quantity")
    assert regime._round_trip_accounting(None, trade, allocations=[allocation]) == (
        None, None, "legacy_unverified_fill_lot")


class _FinalizeConn:
    def __init__(self, trades, existing=None):
        self.trades = trades
        self.existing = existing or {}
        self.writes = []

    def execute(self, sql, params=None):
        if "FROM trade_ledger" in sql:
            # Every compared identity must be selected, including forecast ID.
            assert "entry_forecast_id" in sql
            assert "entry_quote_id" in sql
            return _Result(self.trades)
        if "FROM paper_regime_trade_metrics" in sql:
            return _Result([self.existing[params[0]]]) if params[0] in self.existing else _Result([])
        if "SELECT regime" in sql or "SELECT observed_at,price" in sql:
            return _Result([])
        if sql.startswith("UPDATE") or "INSERT INTO paper_regime_trade_metrics" in sql:
            self.writes.append((sql, params))
            return _Result([])
        raise AssertionError("Unexpected query: " + sql)


def _finalize_connection(monkeypatch, conn, allocations):
    import database
    import paper_fill_provenance
    calls = []

    @contextmanager
    def connect():
        yield conn

    def load(actual_conn, *, trade_ids, market):
        assert actual_conn is conn
        calls.append((trade_ids, market))
        return allocations

    _paper(monkeypatch)
    monkeypatch.setattr(database, "connect", connect)
    monkeypatch.setattr(paper_fill_provenance, "load_fill_lot_allocations", load)
    return calls


def test_finalize_loads_explicit_links_once_for_batch_and_keeps_unlinked_accounting_unknown(monkeypatch):
    exact, allocation = _linked_close()
    unlinked, _ = _linked_close(trade_id="legacy", fill_id="old-fill")
    conn = _FinalizeConn([exact, unlinked])
    calls = _finalize_connection(monkeypatch, conn, [allocation])
    assert regime.finalize_closed_trades() == 2
    assert calls == [(["sell", "legacy"], "crypto")]
    assert conn.writes[0][1][16:19] == (8, 2, "exact_lot")
    assert conn.writes[1][1][16:19] == (None, None, "legacy_unverified_fill_lot")


def test_transient_missing_links_never_downgrade_existing_accounting(monkeypatch):
    trade, _ = _linked_close()
    original = dict(trade_id="sell", cost_provenance="exact_lot", time_to_mfe_sec=1,
                    time_to_mae_sec=2, mfe_observed_at=trade["entry_time"],
                    mae_observed_at=trade["exit_time"])
    conn = _FinalizeConn([trade], existing={"sell": original})
    _finalize_connection(monkeypatch, conn, [])
    assert regime.finalize_closed_trades() == 0
    assert conn.writes == []
    assert conn.existing["sell"] == original


def test_late_durable_fill_identity_upgrades_unverified_metrics_without_replacing_history(monkeypatch):
    trade, allocation = _linked_close()
    original = dict(trade_id="sell", cost_provenance="legacy_unverified_fill_lot",
                    mfe_observed_at=trade["entry_time"], mae_observed_at=trade["exit_time"])
    conn = _FinalizeConn([trade], existing={"sell": original})
    _finalize_connection(monkeypatch, conn, [allocation])
    assert regime.finalize_closed_trades() == 1
    sql, params = conn.writes[0]
    assert "round_trip_net_pnl=%s,round_trip_fees=%s,cost_provenance=%s" in sql
    assert params[:3] == (8, 2, "exact_lot")
    assert "DELETE" not in sql and "INSERT" not in sql


def test_round_trip_cost_columns_are_additive_research_fields():
    source = Path("paper_regime_economics_shadow.py").read_text(encoding="utf-8")
    assert "round_trip_net_pnl DOUBLE PRECISION" in source
    assert "round_trip_fees DOUBLE PRECISION" in source
    assert "cost_provenance TEXT NOT NULL DEFAULT 'legacy_unknown'" in source
    assert 'if "round_trip_net_pnl" not in columns' in source
    assert 'if "round_trip_fees" not in columns' in source
    assert 'if "cost_provenance" not in columns' in source
    assert "COALESCE(round_trip_net_pnl,net_pnl)" in source


def test_summary_market_filter_casts_nullable_parameter_for_postgres():
    source = Path("paper_regime_economics_shadow.py").read_text(encoding="utf-8")
    assert "WHERE (%s::text IS NULL OR market=%s::text)" in source


def test_regime_schema_checks_catalog_before_repair_alter():
    source = Path("paper_regime_economics_shadow.py").read_text(encoding="utf-8")
    assert "pg_advisory_xact_lock(hashtext(%s))" in source
    assert "paper_regime_economics_schema_v2" in source
    assert "FROM pg_attribute" in source
    assert "ADD COLUMN IF NOT EXISTS round_trip_net_pnl" not in source
    assert "ADD COLUMN IF NOT EXISTS round_trip_fees" not in source
