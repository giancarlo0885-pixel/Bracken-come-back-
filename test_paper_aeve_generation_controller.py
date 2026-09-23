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


def test_generation_window_counts_all_valid_outcomes_and_tracks_acceptance_separately():
    import inspect
    import paper_aeve_generation_controller as controller
    source = inspect.getsource(controller.maybe_advance_generation)
    assert "COUNT(*) AS window_samples" in source
    assert "COUNT(*) FILTER (WHERE would_trade) AS samples" in source
    assert "AND would_trade=TRUE" not in source
    assert "if window_samples < BATCH_SIZE" in source
    assert "accepted_samples=%s" in source
    assert "WHERE generation=%s" in source
    assert "config_hash=%s" in source
    assert "provenance_version=%s" in source
    assert PROVENANCE_VERSION == 3


def test_completed_window_with_insufficient_acceptance_advances_identity_without_tuning():
    import inspect
    import paper_aeve_generation_controller as controller
    source = inspect.getsource(controller.maybe_advance_generation)
    assert "if nxt.generation == cfg.generation" in source
    assert "replace(cfg, generation=cfg.generation + 1)" in source
    assert "insufficient_samples_hold_formula" not in source  # diagnosis is derived, not hard-coded
    assert "diagnosis = f\"{diagnosis}_hold_formula\"" in source


def test_generation_report_exposes_window_and_accepted_sample_depth():
    import inspect
    import paper_aeve_generation_controller as controller
    source = inspect.getsource(controller.generation_research_report)
    assert "COUNT(*)::int AS window_trades" in source
    assert "COUNT(*) FILTER (WHERE would_trade)::int AS accepted_trades" in source


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
        dip_depth_pct=0.10,
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


def test_aeve_loss_streak_is_preentry_and_not_hardcoded():
    import inspect
    import paper_aeve_generation_controller as controller
    source = inspect.getsource(controller.record_generation_outcomes)
    assert "loss_streak=0" not in source
    assert "loss_streak=loss_streak" in source
    assert "exit_time < %s" in source
    assert "ORDER BY exit_time DESC" in source
    assert "int(cfg.max_loss_streak) + 1" in source


def test_aeve_loss_streak_query_excludes_candidate_and_future_outcomes():
    import inspect
    import paper_aeve_generation_controller as controller
    source = inspect.getsource(controller.record_generation_outcomes)
    query_start = source.index("SELECT net_pnl")
    query_end = source.index("fetchall()", query_start)
    streak_query = source[query_start:query_end]
    assert "exit_time < %s" in streak_query
    assert "exit_time <= %s" not in streak_query
    assert "net_pnl<0" not in streak_query


def test_evaluator_passes_independent_entry_inputs_and_persists_version(monkeypatch, caplog):
    from contextlib import nullcontext
    import logging
    import pytest
    import database
    import paper_aeve_generation_controller as controller
    import paper_aeve_v1_formula as formula

    cfg = AEVEGenerationConfig()
    identity = generation_config_hash(cfg)
    features = {'edge_pct': 0.24, 'dip_depth_pct': 0.0025, 'rebound_pct': 0.0005}
    captured, inserted = [], []
    real_score = formula.score_entry

    class Result:
        def __init__(self, rows):
            self.rows = rows
        def fetchall(self):
            return self.rows
        def fetchone(self):
            return self.rows[0] if self.rows else None

    class Conn:
        def execute(self, sql, params=None):
            if 'JOIN trade_ledger' in sql:
                return Result([dict(trade_id='entry-1', regime='range', entry_time='2026-09-23',
                    exit_time='2026-09-24', net_pnl=1, quantity=1, entry_price=100, fees=0.15,
                    feature_snapshot=dict(features), aeve_generation=cfg.generation,
                    aeve_config_json=cfg.__dict__, aeve_config_hash=identity)])
            if 'COUNT(*) AS samples' in sql:
                return Result([dict(samples=80, expectancy=0.1, gross_win=2, gross_loss=1,
                                    mfe=1.1, mae=-0.3)])
            if 'SELECT net_pnl' in sql:
                return Result([])
            if 'INSERT INTO paper_aeve_generation_outcomes' in sql:
                inserted.append(params)
                return Result([])
            raise AssertionError(sql)

    def capture(**kwargs):
        decision = real_score(**kwargs)
        captured.append((kwargs, decision))
        return decision

    monkeypatch.setattr(controller, 'active', lambda: True)
    monkeypatch.setattr(controller, '_load_active', lambda conn: (cfg, {'config_hash': identity}))
    monkeypatch.setattr(database, 'connect', lambda: nullcontext(Conn()))
    monkeypatch.setattr(formula, 'score_entry', capture)
    caplog.set_level(logging.INFO, logger=controller.log.name)
    assert controller.record_generation_outcomes() == 1
    features['dip_depth_pct'] = 0.0125
    assert controller.record_generation_outcomes() == 1
    features['rebound_pct'] = 0.002
    assert controller.record_generation_outcomes() == 1
    first, dip_changed, rebound_changed = captured
    assert first[0]['dip_depth_pct'] == pytest.approx(0.25)
    assert first[0]['rebound_from_low_pct'] == pytest.approx(0.05)
    assert dip_changed[0]['dip_depth_pct'] == pytest.approx(1.25)
    assert dip_changed[1].dip_quality != first[1].dip_quality
    assert dip_changed[1].rebound_quality == first[1].rebound_quality
    assert rebound_changed[0]['rebound_from_low_pct'] == pytest.approx(0.20)
    assert rebound_changed[1].dip_quality == dip_changed[1].dip_quality
    assert rebound_changed[1].rebound_quality != dip_changed[1].rebound_quality
    assert all(row[-1] == 3 for row in inserted)
    assert all(item[0]['config'] == cfg.__dict__ for item in captured)
    assert 'input_schema=dip_depth_rebound_v1' in caplog.text
    assert 'dip_depth_pct=1.25 | rebound_from_low_pct=0.2' in caplog.text
    features.pop('dip_depth_pct')
    assert controller.record_generation_outcomes() == 1
    assert inserted[-1][8] is False
