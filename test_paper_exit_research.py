from datetime import datetime, timedelta, timezone
import os
import pytest

from paper_exit_research import (reconcile_fifo, observed_path, chronological_oos_report,
                                ensure_reconciliation_schema, source_query_diagnostics)

START = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)


def buy(identity, qty, price, fees, minute=0):
    return dict(id=minute + 1, trade_id=identity, market="crypto", symbol="TEST-USD", side="BUY",
                quantity=qty, entry_price=price, entry_time=START + timedelta(minutes=minute),
                fees=fees, strategy="council", gross_pnl=0, net_pnl=-fees)


def sell(identity, qty, entry, exit_price, fees, gross, minute=20, entry_minute=0):
    return dict(id=minute + 1, trade_id=identity, market="crypto", symbol="TEST-USD", side="SELL",
                quantity=qty, entry_price=entry, exit_price=exit_price,
                entry_time=START + timedelta(minutes=entry_minute), exit_time=START + timedelta(minutes=minute),
                fees=fees, gross_pnl=gross, net_pnl=gross-fees, strategy="council", order_id="sell_signal")


def test_partial_fifo_cost_allocation_and_deduplication():
    first = buy("buy-1", 10, 100, 2)
    second = buy("buy-2", 5, 110, 1, minute=1)
    closes = [sell("sell-1", 4, 100, 105, 0.4, 20),
              sell("sell-2", 6, 100, 106, 0.6, 36, minute=30),
              sell("sell-3", 5, 110, 100, 0.5, -50, minute=40, entry_minute=1)]
    report = reconcile_fifo([first, second, *closes, first], as_of=START + timedelta(days=1))
    assert report["qualified_fifo_closes"] == 3
    assert report["net_pnl"] == pytest.approx(1.5)
    assert [r["entry_fees"] for r in report["closes"]] == pytest.approx([0.8, 1.2, 1])
    assert report["sell_signal_losses"] == 1
    assert report["diagnostics"]["duplicate_fill"] == 1
    assert all(r["exit_net_difference"] == 0 for r in report["closes"])
    assert all(r["post_exit"]["60"]["status"] == "unavailable" for r in report["closes"])
    assert report["premature_exit_verdict"] == "insufficient_data"


def test_missing_buy_and_conflicting_fill_identity_never_qualify():
    close = sell("sell", 2, 100, 101, 0.1, 2)
    report = reconcile_fifo([close], as_of=START + timedelta(days=1))
    assert report["qualified_fifo_closes"] == 0
    assert report["closes"][0]["net_pnl"] is None
    opening = buy("buy", 2, 100, 0.1)
    assert reconcile_fifo([opening, {**opening, "entry_price": 99}, close])["qualified_fifo_closes"] == 0


def test_reconciliation_detects_gross_and_round_trip_metric_disagreement():
    fills = [buy("buy", 2, 100, 0.2), sell("sell", 2, 100, 101, 0.1, 2)]
    report = reconcile_fifo(fills, {"sell": {"round_trip_net_pnl": 2, "regime": "range"}})
    assert report["closes"][0]["round_trip_metric_difference"] == pytest.approx(-0.3)
    assert report["metric_discrepancies"] == 1
    bad = {**fills[-1], "gross_pnl": 3}
    assert reconcile_fifo([fills[0], bad])["qualified_fifo_closes"] == 0


def test_post_exit_paths_exclude_future_and_out_of_horizon_prices():
    end = START + timedelta(hours=1)
    samples = [dict(observed_at=START - timedelta(minutes=1), price=200),
               dict(observed_at=START + timedelta(minutes=10), price=110),
               dict(observed_at=START + timedelta(minutes=30), price=90),
               dict(observed_at=end, price=105), dict(observed_at=end + timedelta(minutes=1), price=1000)]
    result = observed_path(samples, START, end, 100, end)
    assert result["samples"] == 3
    assert result["mfe_pct"] == pytest.approx(10)
    assert result["mae_pct"] == pytest.approx(-10)
    earlier = observed_path(samples, START, end, 100, START + timedelta(minutes=15))
    assert earlier["samples"] == 1
    assert earlier["horizon_elapsed"] is False


def test_oos_purges_boundary_and_future_trigger():
    cutoff = START + timedelta(hours=1)
    base = dict(episode_id="episode", entry_time=START, exit_time=START + timedelta(minutes=30),
                actual_realized_r_net=-0.2, challenger_counterfactual_r_net=-0.1,
                entry_pattern="dip", regime="range", challenger_exit_type="FORWARD_EVIDENCE_EXIT",
                trigger_snapshot={"challenger_trigger_at": START + timedelta(minutes=20)})
    future = {**base, "entry_time": cutoff + timedelta(minutes=10), "exit_time": cutoff + timedelta(minutes=30),
              "trigger_snapshot": {"challenger_trigger_at": cutoff + timedelta(minutes=40)}}
    report = chronological_oos_report([base, future], cutoff)
    assert report["exclusions"]["pre_cutoff_or_overlapping_episode"] == 1
    assert report["exclusions"]["invalid_temporal_evidence"] == 1
    assert report["promotion_evidence_ready"] is False


def test_postgres_source_query_and_bounded_report_schema():
    import psycopg
    from psycopg.rows import dict_row
    url = os.getenv("DATABASE_URL", "")
    if not url:
        pytest.skip("PostgreSQL integration only")
    with psycopg.connect(url, row_factory=dict_row) as conn:
        conn.execute("CREATE TEMP TABLE trade_ledger(trade_id text,market text,side text,broker_mode text,account_environment text)")
        conn.execute("CREATE TEMP TABLE paper_regime_trade_metrics(trade_id text,entry_time timestamptz,exit_time timestamptz,cost_provenance text,round_trip_net_pnl float8)")
        conn.execute("CREATE TEMP TABLE garibaldi_shadow_exit_epochs(model_version text,started_at timestamptz)")
        conn.execute("INSERT INTO trade_ledger VALUES ('missing','crypto','SELL','PAPER','PAPER'),('good','crypto','SELL','PAPER','PAPER'),('legacy','crypto','SELL','PAPER','PAPER')")
        conn.execute("INSERT INTO garibaldi_shadow_exit_epochs VALUES ('test','2026-10-08T11:00:00Z')")
        conn.execute("INSERT INTO paper_regime_trade_metrics VALUES ('good','2026-10-08T12:00:00Z','2026-10-08T13:00:00Z','exact_lot',1),('legacy','2026-10-08T12:00:00Z','2026-10-08T13:00:00Z','unknown',NULL)")
        counts = source_query_diagnostics(conn, "crypto", "test")
        assert counts == dict(missing_regime_metric=1, eligible_source_close=1, unqualified_cost_provenance=1)
        with conn.transaction(force_rollback=True):
            ensure_reconciliation_schema(conn)
            conn.execute("INSERT INTO paper_exit_research_reports(market,version,report) VALUES ('test-market','test-version','{}') ON CONFLICT(market,version) DO UPDATE SET report=EXCLUDED.report")


def test_tiny_unmatched_fill_cannot_pass_dollar_tolerance():
    report = reconcile_fifo([sell("tiny", 0.00000001, 100, 101, 0, 0.00000001)])
    assert report["qualified_fifo_closes"] == 0
    assert report["closes"][0]["net_pnl"] is None
