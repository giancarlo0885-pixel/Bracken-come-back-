"""Real adapter-shaped books, frozen choices and costed paired research outcomes."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import pytest
import prospective_paper_research as pilot
from costed_crypto_evaluation import matched_panel
from test_prospective_paper_research import result, exit_book
from test_kraken_paper_pricing import provider
from test_kraken_reference_book import NOW


def selected_trial(monkeypatch, move='4'):
    pricing, _ = provider(monkeypatch)
    q = pricing.quote('BTC-USD')
    r = result()
    r.update(status='SHADOW_FORECAST', reason=None, forecast={
        'symbol':'BTC-USD','forecast_id':'original-forward-f','validation_status':'shadow',
        'model_version':'original-v1','generated_at':NOW.isoformat(),
        'source_quote_timestamp':NOW.isoformat(),'expected_move_pct':float(move),
        'target_price':float(Decimal(str(q['mid_price']))*(1+Decimal(move)/100))})
    return pilot.capture_trial(r, pricing, NOW)


def outcome(t, seconds, bid, identity=10001):
    at = NOW + timedelta(seconds=seconds)
    q = exit_book(at, bid=bid, ask=str(Decimal(bid)+Decimal('.1')))
    return {**pilot.evaluate_trial(t, q, seconds, at), 'source_evidence_id':identity}


def test_real_adapter_shape_selects_one_costed_bounded_hypothesis(monkeypatch):
    t = selected_trial(monkeypatch)
    p = t['challenger']['decision']
    assert p['selection_status'] == 'SELECTED'
    assert Decimal(p['entry_notional']) <= 25
    assert Decimal(p['risk_usd']) <= Decimal('.50')
    assert p['execution_authority'] == 'NONE' and not p['execution_eligible']
    assert pilot.validate_trial(t) == NOW


def test_low_edge_is_not_rescued_by_sizing_or_later_profit(monkeypatch):
    t = selected_trial(monkeypatch, '.8')
    assert t['challenger']['decision']['selection_status'] == 'ABSTAIN'
    first, last = outcome(t,300,'110'), outcome(t,900,'115')
    panel = matched_panel([(first,last)])
    assert Decimal(panel['policies']['passive_900']['modeled_net_pnl']) > 0
    assert panel['policies']['combined_checkpoints']['modeled_net_pnl'] == '0'
    assert panel['policies']['no_trade']['profit_factor'] is None


def test_frozen_checkpoint_exit_uses_observed_book_not_hindsight_or_stop_price(monkeypatch):
    t = selected_trial(monkeypatch)
    original = deepcopy(t)
    first, last = outcome(t,300,'106'), outcome(t,900,'95')
    panel = matched_panel([(first,last)])['policies']
    assert Decimal(panel['passive_900']['modeled_net_pnl']) < 0
    assert Decimal(panel['entry_only_900']['modeled_net_pnl']) < 0
    assert Decimal(panel['exit_only_checkpoints']['modeled_net_pnl']) > 0
    assert Decimal(panel['combined_checkpoints']['modeled_net_pnl']) > 0
    assert t == original
    assert panel['combined_checkpoints']['independent_episodes'] is None
    assert panel['combined_checkpoints']['mfe'] is None


def test_original_trial_cannot_change_selection_limits_or_quantity_after_outcome(monkeypatch):
    t = selected_trial(monkeypatch)
    t['challenger']['decision']['selected_notional'] = '25000'
    t['trial_hash'] = pilot.digest({k:v for k,v in t.items() if k!='trial_hash'})
    with pytest.raises(pilot.ResearchUnavailable, match='FROZEN_CHALLENGER'):
        pilot.validate_trial(t)


def test_unavailable_duplicate_or_conflicting_pairs_never_improve_sample_count(monkeypatch):
    t = selected_trial(monkeypatch)
    first, last = outcome(t,300,'106'), outcome(t,900,'95')
    late = pilot.evaluate_trial(t,None,900,NOW+timedelta(seconds=961))
    panel = matched_panel([(first,last),(first,last),(first,late)])
    assert panel['matched_original_entries'] == 1 and panel['pair_exclusions'] == 2
    assert panel['policies']['combined_checkpoints']['matched_observations'] == 1

