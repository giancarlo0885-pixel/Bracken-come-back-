from __future__ import annotations

from types import SimpleNamespace

import paper_lifecycle_observability as observability
import paper_lifecycle_readiness as readiness


def test_paper_lifecycle_observer_reloads_after_executed_buy(monkeypatch, caplog):
    events = []
    position_reads = []

    def original(market, signals, prices, ranked, scan_type):
        return [
            {
                "action": "BUY",
                "symbol": "BTC-USD",
                "trade_id": "trade-1",
                "signal_id": "signal-1",
                "forecast_id": "forecast-1",
                "optimizer_approved_amount": 160.0,
            }
        ]

    def position_rows(market):
        position_reads.append(market)
        return (
            {"cash": 1840.0, "equity": 2000.0},
            [{"symbol": "BTC-USD", "quantity": 0.002, "market_value": 160.0}],
        )

    worker = SimpleNamespace(
        _v39_execute_iterative=original,
        _v39_position_rows=position_rows,
        _v39_record_event=lambda market, symbol, stage, payload, rejection_reason=None: events.append(
            (market, symbol, stage, payload)
        ),
    )
    monkeypatch.setenv("EXECUTION_MODE", "paper")

    observability.install_paper_lifecycle_observability(worker)
    actions = worker._v39_execute_iterative("crypto", [], {}, [], "deep")

    assert len(actions) == 1
    assert position_reads == ["crypto"]
    assert events[0][2] == "paper_portfolio_reloaded"
    assert events[0][3]["portfolio_reloaded"] is True
    assert events[0][3]["held_position_count"] == 1
    assert events[0][3]["optimizer_approved_amount"] == 160.0
    assert events[0][3]["execution_mode"] == "paper"


def test_paper_lifecycle_observer_does_not_install_outside_paper(monkeypatch):
    worker = SimpleNamespace(_v39_execute_iterative=lambda *args, **kwargs: [])
    original = worker._v39_execute_iterative
    monkeypatch.setenv("EXECUTION_MODE", "live")

    observability.install_paper_lifecycle_observability(worker)

    assert worker._v39_execute_iterative is original
    assert getattr(worker, "_paper_lifecycle_observability_installed", False) is False


def test_paper_lifecycle_health_requires_entry_reload_and_exit(monkeypatch):
    counts = {
        "buy_orders": 1,
        "sell_orders": 1,
        "buy_fills": 1,
        "sell_fills": 1,
        "reloads": 1,
    }

    def fake_row(query, params=()):
        compact = " ".join(query.split()).lower()
        if "from paper_orders" in compact and "side='buy'" in compact and "count" in compact:
            return {"count": counts["buy_orders"]}
        if "from paper_orders" in compact and "side='sell'" in compact and "count" in compact:
            return {"count": counts["sell_orders"]}
        if "from paper_fills" in compact and "side='buy'" in compact:
            return {"count": counts["buy_fills"]}
        if "from paper_fills" in compact and "side='sell'" in compact:
            return {"count": counts["sell_fills"]}
        if "from global_decision_events" in compact:
            return {"count": counts["reloads"]}
        if "from paper_orders" in compact and "side='buy'" in compact:
            return {"order_id": "buy-1", "symbol": "BTC-USD", "status": "FILLED"}
        if "from paper_orders" in compact and "side='sell'" in compact:
            return {"order_id": "sell-1", "symbol": "BTC-USD", "status": "FILLED"}
        return None

    monkeypatch.setattr(readiness, "row", fake_row)
    monkeypatch.setattr(
        readiness,
        "rows",
        lambda query, params=(): [{"symbol": "BTC-USD", "quantity": 0.002, "market_value": 160.0}],
    )

    report = readiness.paper_lifecycle_health("crypto")

    assert report["ok"] is True
    assert report["status"] == "PASS"
    assert report["entry_proven"] is True
    assert report["exit_proven"] is True
    assert report["round_trip_proven"] is True
    assert report["portfolio_reload_events"] == 1
    assert report["held_position_count"] == 1


def test_paper_lifecycle_health_blocks_live_candidate_until_exit(monkeypatch):
    def fake_row(query, params=()):
        compact = " ".join(query.split()).lower()
        if "from paper_orders" in compact and "side='buy'" in compact and "count" in compact:
            return {"count": 1}
        if "from paper_orders" in compact and "side='sell'" in compact and "count" in compact:
            return {"count": 0}
        if "from paper_fills" in compact and "side='buy'" in compact:
            return {"count": 1}
        if "from paper_fills" in compact and "side='sell'" in compact:
            return {"count": 0}
        if "from global_decision_events" in compact:
            return {"count": 1}
        if "from paper_orders" in compact and "side='buy'" in compact:
            return {"order_id": "buy-1", "symbol": "BTC-USD", "status": "FILLED"}
        return None

    monkeypatch.setattr(readiness, "row", fake_row)
    monkeypatch.setattr(readiness, "rows", lambda query, params=(): [])

    report = readiness.paper_lifecycle_health("crypto")

    assert report["ok"] is False
    assert report["status"] == "ENTRY_PROVEN_EXIT_PENDING"
    assert report["entry_proven"] is True
    assert report["exit_proven"] is False
