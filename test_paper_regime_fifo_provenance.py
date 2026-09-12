from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from types import SimpleNamespace
import uuid

import pytest

import paper_fee_policy as fee_policy
from profit_attribution import PositionLot


ENTRY_TIME = datetime(2026, 9, 12, 12, tzinfo=timezone.utc)
ENTRY_FEATURES = {
    "trend_strength": 0.08,
    "momentum_20d": 0.14,
    "volatility_20d": 0.31,
}


def _provenance(signal_id="42"):
    return {
        "entry_decision_id": f"decision:{signal_id}",
        "entry_signal_id": signal_id,
        "entry_forecast_id": f"forecast:{signal_id}",
        "entry_quote_id": f"quote:{signal_id}",
        "decision_correlation_id": f"correlation:{signal_id}",
        "model": "entry-model",
        "model_version": "v1",
        "provider": "entry-provider",
        "provider_symbol": "BTCUSD",
        "quote_timestamp": ENTRY_TIME.isoformat(),
        "decision_timestamp": ENTRY_TIME.isoformat(),
        "feature_snapshot": dict(ENTRY_FEATURES),
        "risk_snapshot": {"approved": True, "risk_budget": 0.01},
        "portfolio_snapshot": {"cash": 2000.0},
    }


def _lot(signal_id="42", *, opened_at=ENTRY_TIME, provenance=None):
    return PositionLot(
        lot_id=f"lot:{signal_id}", symbol="BTC-USD", market="crypto",
        bucket="Tactical", strategy="oracle_council_v3", opened_at=opened_at,
        quantity_opened=10.0, quantity_remaining=10.0,
        entry_price=100.0, entry_fees=2.0, decision_id=f"legacy:{signal_id}",
        **(_provenance(signal_id) if provenance is None else provenance),
    )


@pytest.mark.parametrize("quantity", [4.0, 10.0])
def test_fee_aware_fifo_preserves_all_entry_provenance_and_fee_math(quantity):
    lot = _lot()
    [row] = fee_policy.fee_aware_fifo_close_lots(
        [lot], quantity=quantity, exit_price=110.0,
        exit_time=ENTRY_TIME + timedelta(hours=1), fees=3.0,
        decision_id="exit-decision", quote_provider="exit-provider",
        order_id="exit-order",
    )

    assert {key: getattr(row, key) for key in _provenance()} == _provenance()
    assert row.decision_id == "exit-decision"
    assert row.quote_provider == "exit-provider"
    assert row.order_id == "exit-order"
    assert row.status == ("CLOSED" if quantity == 10 else "PARTIAL")
    assert lot.quantity_remaining == 10.0 - quantity
    assert row.gross_pnl == 10.0 * quantity
    assert row.fees == 3.0
    assert row.net_pnl == 10.0 * quantity - 3.0
    entry_fee = 2.0 * quantity / 10.0
    assert row.return_pct == pytest.approx(
        (10.0 * quantity - entry_fee - 3.0) / (100.0 * quantity + entry_fee) * 100
    )


def test_fee_aware_fifo_keeps_each_entry_identity_across_multiple_lots():
    first = _lot("41")
    second = _lot("42", opened_at=ENTRY_TIME + timedelta(minutes=1))
    rows = fee_policy.fee_aware_fifo_close_lots(
        [second, first], quantity=15.0, exit_price=110.0,
        exit_time=ENTRY_TIME + timedelta(hours=1), fees=3.0,
    )

    assert [row.quantity for row in rows] == [10.0, 5.0]
    assert [row.fees for row in rows] == [2.0, 1.0]
    for row, signal_id in zip(rows, ("41", "42")):
        assert {key: getattr(row, key) for key in _provenance()} == _provenance(signal_id)


def test_fee_aware_fifo_does_not_invent_missing_legacy_provenance():
    [row] = fee_policy.fee_aware_fifo_close_lots(
        [_lot(provenance={})], quantity=10, exit_price=110,
        exit_time=ENTRY_TIME + timedelta(hours=1), decision_id="exit-decision",
    )
    assert row.entry_decision_id == "legacy:42"
    assert row.decision_id == "exit-decision"
    for key in _provenance():
        if key != "entry_decision_id":
            assert getattr(row, key) is None


@pytest.fixture
def paper_provenance_db(monkeypatch):
    """Use the initialized CI test database; roll back all fixture writes."""
    if not os.getenv("DATABASE_URL"):
        pytest.skip("PostgreSQL integration test runs in CI service container")

    import database
    import market_worker
    import oracle_bot
    import paper_regime_economics_shadow as shadow
    import paper_regime_entry_provenance as provenance
    import profit_attribution

    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("PAPER_AUTONOMOUS_LEARNING", "true")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")
    # Install the real wrappers while ensuring pytest restores every module hook.
    for module, name in (
        (oracle_bot, "fifo_close_lots"),
        (profit_attribution, "fifo_close_lots"),
        (oracle_bot, "_entry_provenance"),
        (market_worker, "_signal_payload"),
        (shadow, "finalize_closed_trades"),
    ):
        monkeypatch.setattr(module, name, getattr(module, name))
    fee_policy.install_fee_aware_fifo_policy()
    assert oracle_bot.fifo_close_lots is fee_policy.fee_aware_fifo_close_lots
    assert profit_attribution.fifo_close_lots is fee_policy.fee_aware_fifo_close_lots
    assert provenance.install_paper_regime_entry_provenance(oracle_bot, market_worker)

    with database.connect() as conn:
        assert "test" in conn.info.dbname.lower(), "Refusing a non-test database"
        with conn.transaction(force_rollback=True):
            @contextmanager
            def connect():
                # Production helpers open their own contexts; nested savepoints
                # keep the real SQL path inside this rollback-only test boundary.
                with conn.transaction():
                    yield conn

            monkeypatch.setattr(database, "connect", connect)
            shadow.ensure_schema()
            yield conn


@pytest.mark.parametrize("capture_entry_features", [True, False])
def test_postgres_fee_aware_regime_provenance_lifecycle(paper_provenance_db, capture_entry_features):
    """Saved signal -> BUY lot -> installed fee-aware SELL -> regime metric."""
    import database
    import market_worker
    import oracle_bot
    import paper_regime_economics_shadow as shadow
    import paper_regime_entry_signal_fallback as fallback

    conn = paper_provenance_db
    symbol = f"REGIME-{uuid.uuid4().hex[:12].upper()}-USD"
    entry = ENTRY_TIME.isoformat()
    exit_time = (ENTRY_TIME + timedelta(hours=1)).isoformat()
    base_signal = {
        "symbol": symbol, "price": 100.0, "score": 90.0, "confidence": 0.9,
        "action": "BUY", "strategy": "oracle_council_v3",
        "feature_snapshot": {"custom": "immutable-entry"},
    }
    # Deep signals can expose observed fields omitted by their to_dict method.
    observed = SimpleNamespace(**base_signal, **ENTRY_FEATURES)
    observed.to_dict = lambda: dict(base_signal)
    quote = {
        "provider": "entry-provider", "provider_symbol": symbol,
        "quote_id": "entry-quote", "quote_timestamp": entry,
        "correlation_id": "entry-correlation", "quote_verified": True,
    }
    payload = market_worker._signal_payload(observed, quote, "deep")
    signal_id = database.save_json_signal(
        "crypto", symbol, 100.0, 90.0, "BUY", 0.9, payload, created_at=entry,
    )
    saved = conn.execute("SELECT details FROM signals WHERE id=%s", (signal_id,)).fetchone()
    assert {key: json.loads(saved["details"])[key] for key in ENTRY_FEATURES} == ENTRY_FEATURES

    signal = dict(base_signal, signal_id=signal_id, forecast_id="entry-forecast")
    if capture_entry_features:
        signal.update(ENTRY_FEATURES)
    oracle_bot._record_buy_attribution(
        conn, market="crypto", symbol=symbol, quantity=10.0, price=100.0,
        fees=2.0, signal=signal, quote_metadata=quote, now=entry,
    )
    lot = conn.execute("SELECT * FROM position_lots WHERE symbol=%s", (symbol,)).fetchone()
    assert lot["entry_signal_id"] == str(signal_id)
    assert lot["feature_snapshot"]["custom"] == "immutable-entry"

    # A newer, contradictory signal must never substitute for the exact entry ID.
    database.save_json_signal(
        "crypto", symbol, 110.0, 10.0, "SELL", 0.1,
        {"trend_strength": -0.9, "momentum_20d": -0.8, "volatility_20d": 0.9},
        created_at=exit_time,
    )
    [closed] = oracle_bot._record_sell_attribution(
        conn, market="crypto", position={"symbol": symbol}, price=110.0,
        quantity=10.0, fees=3.0, reason="test-close",
        quote_metadata={"provider": "exit-provider"}, now=exit_time,
    )
    ledger = conn.execute(
        "SELECT * FROM trade_ledger WHERE trade_id=%s", (closed["trade_id"],),
    ).fetchone()
    for key in _provenance():
        assert ledger[key] == lot[key]
    assert ledger["gross_pnl"] == 100.0
    assert ledger["net_pnl"] == 97.0
    assert ledger["fees"] == 3.0
    assert conn.execute(
        "SELECT SUM(net_pnl) AS pnl FROM trade_ledger WHERE symbol=%s", (symbol,),
    ).fetchone()["pnl"] == 95.0

    unwrapped = shadow.finalize_closed_trades._entry_signal_regime_fallback_original
    unwrapped()
    before = conn.execute(
        "SELECT * FROM paper_regime_trade_metrics WHERE trade_id=%s", (closed["trade_id"],),
    ).fetchone()
    assert before["regime"] == ("trend_up__low_vol" if capture_entry_features else "range__vol_unknown")
    shadow.finalize_closed_trades()
    after = conn.execute(
        "SELECT * FROM paper_regime_trade_metrics WHERE trade_id=%s", (closed["trade_id"],),
    ).fetchone()
    assert after["regime"] == "trend_up__low_vol"
    if not capture_entry_features:
        assert after["schema_version"] == fallback._SCHEMA_VERSION
    for key in before.keys() - {"regime", "schema_version"}:
        assert after[key] == before[key]
    assert conn.execute(
        "SELECT * FROM trade_ledger WHERE trade_id=%s", (closed["trade_id"],),
    ).fetchone() == ledger
    assert conn.execute(
        "SELECT quantity_remaining FROM position_lots WHERE lot_id=%s", (lot["lot_id"],),
    ).fetchone()["quantity_remaining"] == 0.0
