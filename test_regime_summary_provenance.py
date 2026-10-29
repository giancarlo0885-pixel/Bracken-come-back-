"""Descriptive cohort P/L must expose incomplete accounting and qualification scope."""
from contextlib import contextmanager
from unittest.mock import Mock
import logging
import pytest
import paper_regime_economics_shadow as regime
from test_kraken_reference_pg import pg


def configure(monkeypatch,connect):
    import database
    monkeypatch.setattr(regime,'active',lambda:True)
    monkeypatch.setattr(database,'connect',connect)


def test_positive_summary_is_not_qualified_forward_evidence(monkeypatch,caplog):
    @contextmanager
    def connect():
        yield Mock(execute=Mock(return_value=Mock(fetchall=lambda:[dict(market='crypto',strategy='fast',regime='up',entry_pattern='resistance',samples=13,net_pnl=.2083,fees=.0523,expectancy=.016,exact_accounting_samples=0,incomplete_accounting_samples=13,exact_accounting_net_pnl=None)])))
    configure(monkeypatch,connect)
    with caplog.at_level(logging.INFO,logger=regime.log.name):regime.emit_summary('crypto')
    assert 'exact_accounting_samples=0' in caplog.text
    assert 'incomplete_accounting_samples=13' in caplog.text
    assert 'exact_accounting_net_pnl=NA' in caplog.text
    assert 'strategy_qualification=NOT_ESTABLISHED' in caplog.text
    assert 'evaluation_scope=RETROSPECTIVE_CLOSED_LEDGER_ROWS' in caplog.text
    assert 'independent_episodes=UNAVAILABLE' in caplog.text


@pytest.mark.parametrize('accounting', ['mixed','exact','legacy','nonfinite'])
def test_pg_summary_separates_exact_allocations_without_modifying_records(pg,monkeypatch,caplog,accounting):
    with pg() as conn:
        conn.execute('''CREATE TABLE paper_regime_trade_metrics(
          market text,strategy text,regime text,entry_pattern text,net_pnl double precision,fees double precision,
          round_trip_net_pnl double precision,round_trip_fees double precision,cost_provenance text,
          mfe_pct double precision,mae_pct double precision,excursion_sample_count integer)''')
        def insert(pnl,provenance,exact_net=None,exact_fee=None):
            conn.execute("INSERT INTO paper_regime_trade_metrics VALUES('crypto','fast','up','resistance',%s,.01,%s,%s,%s,NULL,NULL,0)",(pnl,exact_net,exact_fee,provenance))
        if accounting=='mixed':
            insert(2,'legacy_unknown');insert(-1,'exact_lot',-1,.02)
            expected=['samples=2','net_pnl=1.0000','exact_accounting_samples=1','incomplete_accounting_samples=1','exact_accounting_net_pnl=-1.0000']
        elif accounting=='exact':
            insert(9,'exact_lot',.2083,.0523)
            expected=['samples=1','net_pnl=0.2083','exact_accounting_samples=1','incomplete_accounting_samples=0','exact_accounting_net_pnl=0.2083']
        elif accounting=='nonfinite':
            insert(1,'exact_lot',float('nan'),.02);insert(1,'exact_lot',1,float('inf'));insert(1,'exact_lot',1,-.01)
            expected=['samples=3','exact_accounting_samples=0','incomplete_accounting_samples=3','exact_accounting_net_pnl=NA']
        else:
            insert(.2083,'legacy_unknown')
            expected=['samples=1','net_pnl=0.2083','exact_accounting_samples=0','incomplete_accounting_samples=1','exact_accounting_net_pnl=NA']
        before=conn.execute('SELECT md5(to_jsonb(t)::text) AS hash FROM paper_regime_trade_metrics t ORDER BY hash').fetchall()
        configure(monkeypatch,pg)
        with caplog.at_level(logging.INFO,logger=regime.log.name):regime.emit_summary('crypto')
        for fragment in expected:assert fragment in caplog.text
        assert 'strategy_qualification=NOT_ESTABLISHED' in caplog.text
        assert 'execution_impact=NONE' in caplog.text
        assert conn.execute('SELECT md5(to_jsonb(t)::text) AS hash FROM paper_regime_trade_metrics t ORDER BY hash').fetchall()==before
