import paper_accounting_reconciliation_runtime as runtime


def test_reconciliation_is_select_only_and_explains_equity(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")

    def fake_row(sql, params=()):
        if "FROM portfolios" in sql:
            return {"cash": 900.0, "starting_balance": 1000.0, "margin_debt": 0.0, "margin_interest_accrued": 0.0}
        if "FROM positions" in sql:
            return {"positions_value": 95.0, "open_unrealized_pnl": -2.0, "open_positions": 1}
        if "FROM trades" in sql:
            return {"sell_count": 2, "net_realized_pnl": -3.0, "gross_realized_pnl": -2.5, "sell_fees": 0.5}
        if "FROM paper_fills" in sql:
            return {"fill_count": 4, "all_fill_fees": 0.7, "buy_fill_fees": 0.2, "sell_fill_fees": 0.5}
        raise AssertionError(sql)

    monkeypatch.setattr(runtime, "row", fake_row)
    monkeypatch.setattr(runtime, "accounting_health", lambda: {
        "markets": [{"market": "crypto", "ok": True}],
        "paper_order_fill_mismatches": 0,
        "position_lot_mismatches": 0,
        "trade_ledger_mismatches": 0,
        "invalid_executions": 0,
    })

    result = runtime.emit_paper_accounting_reconciliation("crypto")
    assert result["status"] == "PASS"
    assert result["canonical_equity"] == 995.0
    assert result["equity_change"] == -5.0
    assert result["explained_pnl"] == -5.0
    assert result["diagnostic_residual"] == 0.0


def test_reconciliation_disables_when_live_is_armed(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "true")
    result = runtime.emit_paper_accounting_reconciliation("crypto")
    assert result["status"] == "DISABLED"
