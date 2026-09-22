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
                "id": 1,
                "category": "MACRO",
                "provider": "Federal Reserve",
                "title": "Macro pulse",
                "details": {"fact": "Rates stable", "impact_score": 70},
                "verification_status": "verified",
                "confidence": 0.95,
                "event_time": "2026-09-18T23:56:00+00:00",
                "created_at": "2026-09-18T23:56:00+00:00",
            },
            {
                "id": 2,
                "category": "ENERGY",
                "provider": "EIA",
                "title": "Crude oil inventory update",
                "details": {"fact": "Oil inventories changed", "impact_score": 65},
                "verification_status": "verified",
                "confidence": 0.92,
                "event_time": "2026-09-18T23:55:00+00:00",
                "created_at": "2026-09-18T23:55:00+00:00",
            },
            {
                "id": 3,
                "category": "SHIPPING",
                "provider": "Port data",
                "title": "Freight and port throughput update",
                "details": {"fact": "Shipping conditions observed", "impact_score": 45},
                "verification_status": "reported",
                "confidence": 0.70,
                "event_time": "2026-09-18T23:54:00+00:00",
                "created_at": "2026-09-18T23:54:00+00:00",
            },
        ]
    if "SELECT COUNT(*)::int FROM oracle_brain_entries" in sql:
        return [{
            "durable_lessons": 10,
            "observations": 20,
            "exact_outcomes": 30,
            "relationships": 40,
            "active_contradictions": 2,
            "last_learning_sync": "2026-09-18T23:59:30+00:00",
        }]
    if "FROM oracle_brain_learning_state" in sql:
        return [
            {
                "pipeline_key": "intelligence",
                "market": "global",
                "last_sync_at": "2026-09-18T23:59:30+00:00",
                "last_result": {"new": 2, "updated": 1},
            },
            {
                "pipeline_key": "episodes",
                "market": "crypto",
                "last_sync_at": "2026-09-18T23:59:20+00:00",
                "last_result": {"new_exact_episodes": 1},
            },
            {
                "pipeline_key": "brain_v2",
                "market": "crypto",
                "last_sync_at": "2026-09-18T23:59:10+00:00",
                "last_result": {"lessons_updated": 1},
            },
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
    assert {"brain", "macro", "energy", "logistics", "consumer", "technology"} <= node_ids
    assert snapshot["world_state"]["current_events"] == 3
    assert snapshot["world_state"]["domains"]["macro"]["events"] == 1
    assert snapshot["world_state"]["domains"]["energy"]["events"] == 1
    assert snapshot["world_state"]["domains"]["logistics"]["events"] == 1
    assert snapshot["world_state"]["execution_impact"] == "NONE"
    assert snapshot["brain_growth"]["knowledge_units"] == 100
    assert snapshot["brain_growth"]["learning_status"] == "LEARNING"
    assert snapshot["brain_growth"]["learned_this_cycle"] == 5
    assert snapshot["brain_growth"]["new_sources"] == 2
    assert snapshot["brain_growth"]["revised_sources"] == 1
    assert snapshot["brain_growth"]["new_exact_episodes"] == 1
    assert snapshot["brain_growth"]["lessons_updated"] == 1
    assert snapshot["brain_growth"]["execution_authority"] == "NONE"
    assert any(item["destination"] == "energy" for item in snapshot["resident_agents"])
    assert any(item["destination"] == "brain" for item in snapshot["resident_agents"])
    assert any(item["id"] == "brain-memory:core" for item in snapshot["decision_graph"]["nodes"])
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
    assert 'id="workers" class="active">WORKERS<' in rendered
    assert "resident_agents" in rendered
    assert "workerObjects" in rendered
    assert "Strategy Arena" in rendered
    assert "createAmbientBuilding" in rendered
    assert "createCouncil" in rendered
    assert "createExchange" in rendered
    assert "createResidential" in rendered
    assert "createPark" in rendered
    assert "makeVehicle" in rendered
    assert "function daylightForHour" in rendered
    assert "function twilightForHour" in rendered
    assert "function applyTimeOfDay()" in rendered
    assert "sunVisual" in rendered
    assert "moonVisual" in rendered
    assert "setInterval(applyTimeOfDay,60000)" in rendered
    assert "PAPER ONLY · LIVE " in rendered
    assert 'id="brainState"' in rendered
    assert 'id="worldState"' in rendered
    assert 'BRAIN: LEARNING +' in rendered
    assert 'brainLearningStatus==="LEARNING"' in rendered
    assert 'brainLearningStatus==="STALE"' in rendered
    assert "createEnergy" in rendered
    assert "createLogistics" in rendered
    assert "createMacro" in rendered
    assert "createConsumer" in rendered
    assert "createBrainResearch" in rendered
    assert "createTechnology" in rendered
    assert "CITY_VIEW_STORAGE_KEY" in rendered
    assert "restoreCityViewState" in rendered
    assert "setInterval(saveCityViewState,1000)" in rendered
    assert "STREET VIEW" in rendered
    assert "CINEMATIC" in rendered
    assert 'id="hovercard"' in rendered
    assert ".inspector{display:none" in rendered
    assert ".replay{display:none" in rendered
    assert 'id="replayToggle">REPLAY<' in rendered
    assert "function clearHover()" in rendered
    assert 'inspector.classList.add("open")' in rendered
    assert "function addLabel(group,title,metric,y){ return; }" in rendered


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
    assert ".legend,.minimap,.label{display:none}" in rendered
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
    assert 'auto_refresh = st.toggle("Sync data every 60s", value=False)' in source
    assert 'st_autorefresh(interval=60_000, key="oracle-city-world-sync")' in source


def test_oracle_city_defaults_to_city_first_uncluttered_view():
    rendered = render_oracle_city_component({"nodes": [], "flows": [], "replay": []})
    assert "City Districts" not in rendered
    assert "healthy / active" not in rendered
    assert "A living Wall Street + crypto research metropolis" not in rendered
    assert ".inspector{display:none" in rendered
    assert ".replay{display:none" in rendered
    assert ".legend,.minimap,.label{display:none}" in rendered
    assert 'id="hovercard"' in rendered
    assert 'renderer.domElement.addEventListener("pointermove"' in rendered
    assert 'renderer.domElement.addEventListener("pointerleave",clearHover)' in rendered
    assert 'else inspector.classList.remove("open")' in rendered


def test_oracle_city_day_night_cycle_uses_viewer_local_clock():
    rendered = render_oracle_city_component({"nodes": [], "flows": [], "replay": []})
    assert "new Date()" in rendered
    assert "now.getHours()" in rendered
    assert "daylightForHour" in rendered
    assert "scene.background.copy(sky)" in rendered
    assert "scene.fog.color.copy(fogColor)" in rendered
    assert "renderer.toneMappingExposure=1.02+daylight*.50+twilight*.10" in rendered
    assert "sunLight.intensity=daylight" in rendered
    assert "starMaterial.opacity" in rendered


def test_oracle_city_world_state_preserves_provenance_and_does_not_execute():
    snapshot = build_oracle_city_snapshot(
        _fake_rows,
        now=datetime(2026, 9, 19, 0, 0, tzinfo=timezone.utc),
    )
    events = snapshot["world_state"]["top_events"]
    assert events
    assert all(item["execution_impact"] == "NONE" for item in events)
    assert any(item["provider"] == "EIA" and "energy" in item["domains"] for item in events)
    assert any(item["provider"] == "Federal Reserve" and "macro" in item["domains"] for item in events)
    source = Path("oracle_city_model.py").read_text(encoding="utf-8")
    assert "submit_order(" not in source
    assert "process_signals(" not in source


def test_oracle_city_brain_visual_growth_uses_persisted_counts():
    snapshot = build_oracle_city_snapshot(
        _fake_rows,
        now=datetime(2026, 9, 19, 0, 0, tzinfo=timezone.utc),
    )
    growth = snapshot["brain_growth"]
    assert growth["knowledge_units"] == 100
    assert growth["durable_lessons"] == 10
    assert growth["observations"] == 20
    assert growth["exact_outcomes"] == 30
    assert growth["relationships"] == 40
    assert growth["active_contradictions"] == 2
    assert growth["last_learning_sync"] == "2026-09-18T23:59:30+00:00"
    assert growth["learning_status"] == "LEARNING"
    assert growth["learned_this_cycle"] == 5
    assert growth["sync_age_seconds"] == 30.0
    assert growth["execution_authority"] == "NONE"
    memory_nodes = [
        item for item in snapshot["decision_graph"]["nodes"]
        if str(item.get("id", "")).startswith("brain-memory:")
    ]
    assert len(memory_nodes) == 5
    assert any(item["metric"] == "100 evidence units" for item in memory_nodes)


def test_oracle_city_brain_monitor_distinguishes_synced_without_new_evidence():
    def rows_no_delta(sql, params=()):
        if "SELECT COUNT(*)::int FROM oracle_brain_entries" in sql:
            return [{
                "durable_lessons": 1,
                "observations": 2,
                "exact_outcomes": 3,
                "relationships": 4,
                "active_contradictions": 0,
                "last_learning_sync": "2026-09-18T23:59:30+00:00",
            }]
        if "FROM oracle_brain_learning_state" in sql:
            return [{
                "pipeline_key": "brain_v2",
                "market": "cash",
                "last_sync_at": "2026-09-18T23:59:30+00:00",
                "last_result": {"lessons_updated": 0},
            }]
        return []

    snapshot = build_oracle_city_snapshot(
        rows_no_delta,
        now=datetime(2026, 9, 19, 0, 0, tzinfo=timezone.utc),
    )
    assert snapshot["brain_growth"]["learning_status"] == "SYNCED — NO NEW EVIDENCE"
    assert snapshot["brain_growth"]["learned_this_cycle"] == 0
    rendered = render_oracle_city_component(snapshot)
    assert 'BRAIN: SYNCED' in rendered
