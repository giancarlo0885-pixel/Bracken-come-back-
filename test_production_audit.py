from pathlib import Path

from autonomous_intelligence import build_autonomous_brief
from dashboard_helpers import worker_is_online


def test_procfile_contains_only_real_services():
    lines = [line.strip() for line in Path("Procfile").read_text().splitlines() if line.strip()]
    assert lines == [
        "web: python start_web.py",
        "stock-worker: python stock_worker.py",
        "crypto-worker: python crypto_worker.py",
    ]


def test_stopped_worker_is_not_online():
    assert worker_is_online("running")
    assert worker_is_online("idle")
    assert not worker_is_online("stopped")
    assert not worker_is_online("error")
    assert not worker_is_online("unknown")


def test_autonomous_brief_treats_stopped_worker_as_offline():
    context = {
        "opportunities": [],
        "portfolios": [{"equity": 2000, "cash": 2000}],
        "workers": [
            {"market": "cash", "status": "stopped"},
            {"market": "crypto", "status": "idle"},
        ],
        "diagnostics": [{"configured": True, "status": "healthy"}],
        "risk_reasons": [],
    }
    brief = build_autonomous_brief(context)
    assert "1/2 workers online" in brief.operations_summary
    assert brief.posture == "SYSTEM DEFENSE"


def test_market_worker_uses_canonical_cash_status_key():
    source = Path("market_worker.py").read_text()
    assert 'status_market = market' in source
    assert '"stock" if market == "cash"' not in source
