from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from oracle_city_component import render_oracle_city_component
from oracle_city_model import _read_only, build_oracle_city_snapshot


def _fake_rows(sql, params=()):
    if "market_worker_status" in sql:
        return [
            {
                "market": "cash",
                "status": "running",
                "heartbeat": "2026-09-18T23:59:55+00:00",
                "message": "stock scan healthy",
            },
            {
                "market": "crypto",
                "status": "running",
                "heartbeat": "2026-09-18T23:59:50+00:00",
                "message": "crypto scan healthy",
            },
        ]
    if "FROM portfolios" in sql:
        return [{"market": "cash", "cash": 1200, "starting_balance": 2000}]
    if "FROM positions" in sql:
        return [
            {
                "market": "cash",
                "symbol": "AAPL",
                "quantity": 2,
                "current_price": 200,
            },
            {
                "market": "crypto",
                "symbol": "BTC-USD",
                "quantity": 0.01,
                "current_price": 100000,
            },
        ]
    if "FROM opportunity_rankings" in sql:
        return [
            {
                "market": "cash",
                "symbol": "AAPL",
                "opportunity_score": 81.5,
                "payload": {
                    "action": "BUY",
                    "confidence": 0.82,
                    "strategy": "dip_rebound",
                    "regime": "trend_up",
                },
                "created_at": "2026-09-18T23:59:45+00:00",
            }
        ]
    if "FROM trades" in sql:
        return [
            {
                "id": 7,
                "market": "cash",
                "symbol": "AAPL",
                "side": "SELL",
                "quantity": 1,
                "price": 210,
                "value": 210,
                "reason": "take_profit",
                "created_at": "2026-09-18T23:58:00+00:00",
            }
        ]
    if "oracle_decision_audit" in sql:
        return [
            {
                "market": "cash",
                "symbol": "AAPL",
                "action": "BUY",
                "reason": "Council V3 evidence accepted",
                "created_at": "2026-09-18T23:57:00+00:00",
            }
        ]
    if "intelligence_events" in sql:
        return [
            {
                "title": "Macro pulse",
                "details": "Rates stable",
                "created_at": "2026-09-18T23:56:00+00:00",
            }
        ]
    if "paper_aeve_generations" in sql:
        return [
            {
                "generation": 1,
                "started_at": "2026-09-18T00:00:00+00:00",
                "config_json": {
                    "generation": 1,
                    "min_edge_pct": 0.05,
                    "min_profit_factor": 1.0,
                    "min_mfe_mae_ratio": 1.0,
                    "min_mfe_cost_multiple": 2.0,
                    "max_loss_streak": 8,
                    "score_gate": 0.25,
                    "rebound_gate": 0.5,
                    "require_positive_regime": True,
                },
                "config_hash": "abc123",
                "diagnosis": "initial_aeve_v1",
                "status": "ACTIVE",
            }
        ]
    if "paper_aeve_generation_outcomes" in sql:
        return [
            {
                "provenance_version": 2,
                "observed": 180,
                "accepted": 137,
                "expectancy": 0.14,
                "gross_win": 19.0,
                "gross_loss": 11.0,
                "avg_mfe_pct": 0.7,
                "avg_mae_pct": -0.3,
                "avg_cost_pct": 0.08,
            }
        ]
    if "paper_regime_trade_metrics" in sql:
        return [
            {
                "strategy": "oracle_council_v3",
                "regime": "trend_up",
                "samples": 42,
                "expectancy": 0.12,
                "gross_win": 12.0,
                "gross_loss": 6.0,
                "avg_mfe_pct": 0.8,
                "avg_mae_pct": -0.35,
            },
            {
                "strategy": "mean_reversion",
                "regime": "range_high_vol",
                "samples": 36,
                "expectancy": -0.08,
                "gross_win": 4.0,
                "gross_loss": 9.0,
                "avg_mfe_pct": 0.4,
                "avg_mae_pct": -0.7,
            },
        ]
    return []


def test_oracle_city_page_uses_v2_read_only_architecture():
    source = Path("pages/2_Oracle_City.py").read_text(encoding="utf-8")
    assert "build_oracle_city_snapshot" in source
    assert "render_oracle_city_component" in source
    assert "components.html" in source
    assert "SELECT queries only" in source
    assert "submit_order(" not in source
    assert "ENABLE_BROKER_SUBMISSION=true" not in source
    assert "LIVE_TRADING_ARMED=true" not in source


def test_oracle_city_model_rejects_mutating_sql():
    with pytest.raises(ValueError, match="read-only"):
        _read_only(lambda sql, params=(): [], "UPDATE portfolios SET cash=0")


def test_oracle_city_snapshot_builds_workers_exposure_agents_and_replay():
    snapshot = build_oracle_city_snapshot(
        _fake_rows,
        now=datetime(2026, 9, 19, 0, 0, tzinfo=timezone.utc),
    )
    assert snapshot["read_only"] is True
    assert snapshot["summary"]["workers_online"] == 2
    assert snapshot["summary"]["open_positions"] == 2
    assert snapshot["summary"]["known_exposure"] == 1400.0
    assert snapshot["opportunities"][0]["strategy"] == "dip_rebound"
    assert snapshot["strategy_agents"][0]["strategy"] == "dip_rebound"
    assert len(snapshot["portfolio_towers"]) == 2
    assert {item["kind"] for item in snapshot["replay"]} == {"decision", "trade", "intel"}
    assert any(flow["source"] == "council" and flow["target"] == "risk" for flow in snapshot["flows"])
    assert snapshot["aeve"]["generation"] == 1
    assert snapshot["aeve"]["accepted"] == 137
    assert snapshot["aeve"]["target"] == 1000
    assert snapshot["aeve"]["provenance_version"] == 2
    assert snapshot["aeve"]["execution_impact"] == "NONE"
    assert snapshot["city_mood"] == "RESEARCHING"
    assert len(snapshot["resident_agents"]) >= 10
    assert {item["state"] for item in snapshot["resident_agents"]} >= {"MONITORING", "LEARNING", "TRAINING"}
    node_ids = {item["id"] for item in snapshot["nodes"]}
    assert {"academy", "aeve", "arena", "residential", "wellness", "community", "recreation"} <= node_ids
    arena = {(item["strategy"], item["regime"]): item for item in snapshot["strategy_arena"]}
    assert arena[("oracle_council_v3", "trend_up")]["evidence_state"] == "PROMISING — PAPER ONLY"
    assert arena[("oracle_council_v3", "trend_up")]["control"] is True
    assert arena[("mean_reversion", "range_high_vol")]["evidence_state"] == "NEGATIVE EVIDENCE"


def test_oracle_city_component_contains_interactive_webgl_controls():
    snapshot = build_oracle_city_snapshot(
        _fake_rows,
        now=datetime(2026, 9, 19, 0, 0, tzinfo=timezone.utc),
    )
    rendered = render_oracle_city_component(snapshot)
    assert "three@0.160.1" in rendered
    assert "OrbitControls" in rendered
    assert "Raycaster" in rendered
    assert "portfolio_towers" in rendered
    assert "Recent decision replay" in rendered
    assert "READ-ONLY" not in rendered or "read-only" in rendered.lower()
    assert "WORKERS ON" in rendered
    assert "resident_agents" in rendered
    assert "workerObjects" in rendered
    assert "Strategy Arena" in rendered
    assert "Work with discipline. Learn from results. Progress earns rewards." in rendered
    assert "Living Financial Metropolis" in rendered
    assert "createAmbientBuilding" in rendered
    assert "createCouncil" in rendered
    assert "createExchange" in rendered
    assert "createResidential" in rendered
    assert "createPark" in rendered
    assert "makeVehicle" in rendered
    assert "STREET VIEW" in rendered
    assert "CINEMATIC" in rendered


def test_oracle_city_component_escapes_script_breakout_payloads():
    snapshot = build_oracle_city_snapshot(
        _fake_rows,
        now=datetime(2026, 9, 19, 0, 0, tzinfo=timezone.utc),
    )
    snapshot["nodes"][0]["detail"] = "</script><script>alert(1)</script>"
    rendered = render_oracle_city_component(snapshot)
    assert "</script><script>alert(1)</script>" not in rendered
    assert "\\u003c/script\\u003e" in rendered


def test_oracle_city_mobile_layout_suppresses_label_collisions_and_resets_camera():
    rendered = render_oracle_city_component({"nodes": [], "flows": [], "replay": []})
    assert "@media(max-width:720px)" in rendered
    assert ".label{display:none}" in rendered
    assert 'window.matchMedia("(max-width:720px)")' in rendered
    assert "camera.position.set(2.5,31,28)" in rendered
    assert "lastMobileView" in rendered
    assert "min-height:44px" in rendered
    assert "isMobileDevice?26:68" in rendered
    assert "isMobileDevice?6:16" in rendered


def test_oracle_city_missing_aeve_progress_stays_unavailable():
    def empty_rows(sql, params=()):
        return []

    snapshot = build_oracle_city_snapshot(
        empty_rows,
        now=datetime(2026, 9, 19, 0, 0, tzinfo=timezone.utc),
    )
    assert snapshot["aeve"]["available"] is False
    assert snapshot["aeve"]["accepted"] is None
    assert snapshot["aeve"]["observed"] is None
    assert snapshot["aeve"]["profit_factor"] is None
    rendered = render_oracle_city_component(snapshot)
    assert "UNAVAILABLE" in rendered


def test_oracle_city_worker_and_strategy_layers_are_visual_only():
    source = Path("oracle_city_component.py").read_text(encoding="utf-8")
    model = Path("oracle_city_model.py").read_text(encoding="utf-8")
    assert "visualization-only" in source.lower() or "visual only" in source.lower()
    assert "cannot place or approve trades" in source
    assert "submit_order(" not in source
    assert "INSERT INTO" not in model
    assert "UPDATE " not in model
    assert "DELETE FROM" not in model
    assert "paper_aeve_generations" in model
    assert "paper_aeve_generation_outcomes" in model
    assert "paper_regime_trade_metrics" in model


def test_oracle_city_cinematic_renderer_remains_data_driven_and_read_only():
    source = Path("oracle_city_component.py").read_text(encoding="utf-8")
    assert "DATA.nodes" in source
    assert "DATA.resident_agents" in source
    assert "DATA.strategy_arena" in source
    assert "DATA.portfolio_towers" in source
    assert "submit_order(" not in source
    assert "fetch(" not in source
    assert "XMLHttpRequest" not in source
    assert "WebSocket(" not in source
    assert "ENABLE_BROKER_SUBMISSION=true" not in source
    assert "LIVE_TRADING_ARMED=true" not in source


def test_oracle_city_page_leads_with_cinematic_city():
    source = Path("pages/2_Oracle_City.py").read_text(encoding="utf-8")
    city = source.index("components.html")
    status = source.index('st.subheader("City status")')
    assert city < status
    assert "Cinematic Metropolis" in source
    assert "height=980" in source
