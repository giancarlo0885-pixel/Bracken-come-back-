from autonomous_intelligence import build_autonomous_brief


def test_strong_ready_system_deploys_selectively_or_more():
    ctx={
        "opportunities":[{"symbol":"NVDA","opportunity_score":92,"payload":{"recommendation":"BUY","probability_of_profit":78,"quant":{"net_expected_value_pct":0.018}}}],
        "portfolios":[{"equity":2000,"cash":400},{"equity":2000,"cash":400}],
        "workers":[{"status":"running"},{"status":"running"}],
        "diagnostics":[{"available":True},{"available":True},{"available":True}],
        "risk_reasons":[],
    }
    brief=build_autonomous_brief(ctx)
    assert brief.deployment_score >= 60
    assert brief.posture in {"SELECTIVE OFFENSE","DEPLOY CAPITAL"}


def test_offline_workers_force_system_defense():
    ctx={"opportunities":[],"portfolios":[{"equity":2000,"cash":2000}],"workers":[],"diagnostics":[],"risk_reasons":[]}
    brief=build_autonomous_brief(ctx)
    assert brief.posture == "SYSTEM DEFENSE"
    assert brief.system_readiness_score < 55


def test_configured_provider_status_counts_as_ready():
    ctx={"opportunities":[],"portfolios":[{"equity":2000,"cash":2000}],"workers":[{"status":"running"},{"status":"running"}],"diagnostics":[{"status":"configured"},{"status":"available"}],"risk_reasons":[]}
    brief=build_autonomous_brief(ctx)
    assert brief.system_readiness_score >= 70
