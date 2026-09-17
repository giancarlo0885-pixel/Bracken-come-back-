from paper_aeve_generation_controller import AEVEGenerationConfig, BatchDiagnostics, BATCH_SIZE, next_generation


def d(**overrides):
    base = dict(samples=BATCH_SIZE, expectancy=-0.01, profit_factor=0.8, win_rate=0.4,
                avg_mfe_pct=0.30, avg_mae_pct=-0.60, avg_cost_pct=0.10)
    base.update(overrides)
    return BatchDiagnostics(**base)


def test_insufficient_sample_does_not_advance():
    cfg = AEVEGenerationConfig()
    nxt, reason = next_generation(cfg, d(samples=BATCH_SIZE-1))
    assert nxt == cfg
    assert reason == 'insufficient_samples'


def test_changes_only_excursion_dimension_when_mae_dominates():
    cfg = AEVEGenerationConfig()
    nxt, reason = next_generation(cfg, d())
    assert reason == 'adverse_excursion_dominates'
    assert nxt.generation == 2
    assert nxt.min_mfe_mae_ratio > cfg.min_mfe_mae_ratio
    assert nxt.min_edge_pct == cfg.min_edge_pct
    assert nxt.min_profit_factor == cfg.min_profit_factor


def test_cost_problem_tightens_cost_coverage_only():
    cfg = AEVEGenerationConfig()
    nxt, reason = next_generation(cfg, d(avg_mfe_pct=0.25, avg_mae_pct=-0.10, avg_cost_pct=0.10))
    assert reason == 'costs_consume_favorable_excursion'
    assert nxt.min_mfe_cost_multiple > cfg.min_mfe_cost_multiple
    assert nxt.min_mfe_mae_ratio == cfg.min_mfe_mae_ratio


def test_positive_batch_holds_formula_for_oos_confirmation():
    cfg = AEVEGenerationConfig()
    nxt, reason = next_generation(cfg, d(expectancy=0.02, profit_factor=1.2,
                                         avg_mfe_pct=0.60, avg_mae_pct=-0.25,
                                         avg_cost_pct=0.08))
    assert reason == 'hold_positive_formula_for_oos_confirmation'
    assert nxt.generation == 2
    assert nxt.min_edge_pct == cfg.min_edge_pct
    assert nxt.min_profit_factor == cfg.min_profit_factor
    assert nxt.min_mfe_mae_ratio == cfg.min_mfe_mae_ratio


def test_active_requires_paper_and_disarmed(monkeypatch):
    import paper_aeve_generation_controller as c
    monkeypatch.setenv('EXECUTION_MODE','paper')
    monkeypatch.setenv('PAPER_AUTONOMOUS_LEARNING','true')
    monkeypatch.setenv('ENABLE_BROKER_SUBMISSION','false')
    monkeypatch.setenv('LIVE_TRADING_ARMED','false')
    assert c.active() is True
    monkeypatch.setenv('LIVE_TRADING_ARMED','true')
    assert c.active() is False
