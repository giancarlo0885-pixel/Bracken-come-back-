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
    assert "Decision / execution replay" in rendered
    assert "READ-ONLY" not in rendered or "read-only" in rendered.lower()


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
