from __future__ import annotations

from oracle_city_component import render_oracle_city_component
from oracle_city_model import _build_decision_graph


def test_brain_map_traces_features_gates_and_exact_outcome():
    decisions = [
        {
            "decision_id": "decision-1",
            "market": "crypto",
            "symbol": "BTC-USD",
            "decision": "BUY",
            "features": {
                "rsi_14": 31.2,
                "momentum_pct": 2.4,
                "spread_pct": 0.08,
                "regime": "trend_up",
            },
            "portfolio_context": {
                "cash": 1200.0,
                "gross_exposure": 800.0,
            },
            "created_at": "2026-09-19T17:00:00+00:00",
        }
    ]
    events = [
        {
            "decision_id": "decision-1",
            "stage": "council",
            "rejection_reason": None,
            "created_at": "2026-09-19T17:00:01+00:00",
        },
        {
            "decision_id": "decision-1",
            "stage": "execution_capacity",
            "rejection_reason": "insufficient executable room",
            "created_at": "2026-09-19T17:00:02+00:00",
        },
    ]
    ledger = [
        {
            "trade_id": "trade-1",
            "entry_decision_id": "decision-1",
            "decision_id": "decision-1",
            "symbol": "BTC-USD",
            "side": "BUY",
            "status": "CLOSED",
            "net_pnl": 14.25,
        }
    ]

    graph = _build_decision_graph(decisions, events, ledger, [])

    assert graph["read_only"] is True
    assert graph["summary"]["traced_decisions"] == 1
    assert graph["summary"]["linked_outcomes"] == 1
    assert graph["summary"]["downstream_blocks"] == 1
    assert graph["summary"]["recent_closed_provenance_gaps"] == 0

    node_kinds = {node["kind"] for node in graph["nodes"]}
    assert {"feature", "decision", "gate", "outcome"} <= node_kinds
    assert any(
        node["kind"] == "gate"
        and node["state"] == "offline"
        and "insufficient executable room" in node["detail"]
        for node in graph["nodes"]
    )
    assert any(
        node["kind"] == "outcome"
        and "P/L $+14.25" in node["metric"]
        and "Exact immutable decision provenance" in node["detail"]
        for node in graph["nodes"]
    )
    assert any(edge["kind"] == "blocked" for edge in graph["edges"])


def test_brain_map_counts_closed_trade_without_decision_provenance():
    graph = _build_decision_graph(
        [],
        [],
        [
            {
                "trade_id": "legacy-closed",
                "status": "CLOSED",
                "entry_decision_id": None,
                "decision_id": None,
            }
        ],
        [],
    )
    assert graph["summary"]["recent_closed_provenance_gaps"] == 1


def test_brain_map_component_is_read_only_visualization():
    html = render_oracle_city_component(
        {
            "nodes": [],
            "flows": [],
            "portfolio_towers": [],
            "strategy_agents": [],
            "replay": [],
            "decision_graph": {
                "nodes": [
                    {
                        "id": "decision:x",
                        "title": "AAPL · BUY",
                        "kind": "decision",
                        "state": "online",
                        "metric": "CASH",
                        "detail": "persisted decision",
                        "x": 0,
                        "y": 1,
                        "z": 0,
                        "size": 0.4,
                        "label": True,
                    }
                ],
                "edges": [],
                "summary": {
                    "traced_decisions": 1,
                    "linked_outcomes": 0,
                    "downstream_blocks": 0,
                    "recent_closed_provenance_gaps": 0,
                },
                "read_only": True,
            },
        }
    )
    assert "BRAIN MAP" in html
    assert "Decision provenance network" in html
    assert "submit_order(" not in html
    assert "LIVE_TRADING_ARMED=true" not in html
