from paper_aeve_generation_controller import (
    AEVEGenerationConfig,
    BatchDiagnostics,
    BATCH_SIZE,
    PROVENANCE_VERSION,
    _decode_generation_row,
    _missing_research_relations,
    generation_config_hash,
    next_generation,
)
from paper_aeve_v1_formula import score_entry


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


class _RelationResult:
    def __init__(self, value):
        self.value = value

    def fetchone(self):
        return {'relation': self.value}


class _RelationConn:
    def __init__(self, present):
        self.present = set(present)

    def execute(self, sql, params):
        assert 'to_regclass' in sql
        relation = params[0]
        return _RelationResult(relation if relation in self.present else None)


def test_missing_research_relations_names_exact_dependency():
    conn = _RelationConn(set())
    assert _missing_research_relations(conn) == ['paper_aeve_generation_outcomes']


def test_research_schema_guard_passes_when_both_relations_exist():
    conn = _RelationConn({'paper_aeve_generation_outcomes'})
    assert _missing_research_relations(conn) == []


def test_generation_controller_uses_only_generation_isolated_outcomes():
    import inspect
    import paper_aeve_generation_controller as controller
    source = inspect.getsource(controller.maybe_advance_generation)
    assert "paper_aeve_generation_outcomes" in source
    assert "WHERE generation=%s" in source
    assert "oracle_council_v3" not in source
    assert "paper_regime_trade_metrics" not in source


def test_aeve_outcome_producer_consumes_frozen_generation_config():
    import inspect
    import paper_aeve_generation_controller as controller
    source = inspect.getsource(controller.record_generation_outcomes)
    assert "config=config_snapshot" in source
    assert "exit_time < %s" in source
    assert "ON CONFLICT (generation,trade_id) DO NOTHING" in source
    assert "WHERE started_at <= m.entry_time" in source
    assert "o.config_hash=g.config_hash" in source
    assert "AEVE SHADOW RESULT" in source
    assert "entry_evidence_complete" in source
    assert "feature_snapshot" in source
    assert "post-entry excursion" in source


def test_generation_batch_counts_only_accepted_aeve_outcomes():
    import inspect
    import paper_aeve_generation_controller as controller
    source = inspect.getsource(controller.maybe_advance_generation)
    assert "would_trade=TRUE" in source
    assert "WHERE generation=%s" in source
    assert "config_hash=%s" in source
    assert "provenance_version=%s" in source
    assert PROVENANCE_VERSION == 2


def test_legacy_outcomes_are_not_retroactively_certified_for_advancement():
    import inspect
    import paper_aeve_generation_controller as controller
    schema_source = inspect.getsource(controller.ensure_schema)
    producer_source = inspect.getsource(controller.record_generation_outcomes)
    assert "provenance_version SMALLINT NOT NULL DEFAULT 1" in schema_source
    assert "config_hash=%s AND provenance_version=%s" in inspect.getsource(controller.maybe_advance_generation)
    assert "json.dumps(config_snapshot),config_hash,PROVENANCE_VERSION" in producer_source


def test_aeve_entry_features_fail_closed_when_provenance_missing():
    import inspect
    import paper_aeve_generation_controller as controller
    source = inspect.getsource(controller.record_generation_outcomes)
    assert "decision.would_trade if entry_evidence_complete else False" in source
    assert "realized P&L" in source


def _score_with_persisted_row(row):
    cfg, identity = _decode_generation_row(row)
    decision = score_entry(
        expected_net_edge_pct=0.20,
        mfe_pct=1.0,
        mae_pct=-0.10,
        round_trip_cost_pct=0.05,
        loss_streak=0,
        price_above_recent_low_pct=0.10,
        rebound_from_low_pct=1.0,
        rsi=43.5,
        trend_confirmed=True,
        regime_expectancy_positive=True,
        profit_factor=1.5,
        min_samples=100,
        config=cfg.__dict__,
    )
    return identity, decision


def test_persisted_generation_config_changes_evaluator_input_and_identity():
    permissive = AEVEGenerationConfig(generation=7, min_edge_pct=0.05)
    restrictive = AEVEGenerationConfig(generation=8, min_edge_pct=0.30)
    permissive_hash, permissive_decision = _score_with_persisted_row({
        "generation": 7,
        "config_json": permissive.__dict__,
        "config_hash": generation_config_hash(permissive),
    })
    restrictive_hash, restrictive_decision = _score_with_persisted_row({
        "generation": 8,
        "config_json": restrictive.__dict__,
        "config_hash": generation_config_hash(restrictive),
    })
    assert permissive_hash != restrictive_hash
    assert permissive_decision.would_trade is True
    assert restrictive_decision.would_trade is False


def test_generation_config_hash_rejects_mutated_persisted_config():
    original = AEVEGenerationConfig(generation=3, min_edge_pct=0.05)
    mutated = {**original.__dict__, "min_edge_pct": 0.25}
    try:
        _decode_generation_row({
            "generation": 3,
            "config_json": mutated,
            "config_hash": generation_config_hash(original),
        })
    except ValueError as exc:
        assert "config hash mismatch" in str(exc)
    else:
        raise AssertionError("mutated generation configuration must fail closed")


def test_generation_identity_rejects_config_from_another_generation():
    cfg = AEVEGenerationConfig(generation=4)
    try:
        _decode_generation_row({
            "generation": 5,
            "config_json": cfg.__dict__,
            "config_hash": generation_config_hash(cfg),
        })
    except ValueError as exc:
        assert "generation identity mismatch" in str(exc)
    else:
        raise AssertionError("cross-generation configuration must fail closed")
