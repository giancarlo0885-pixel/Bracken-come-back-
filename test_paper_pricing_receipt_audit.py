"""Synthetic receipt boundaries; no production evidence is created or repaired."""
from copy import deepcopy
from datetime import timedelta

import pytest

from coinbase_paper_pricing import MODEL, SOURCE
from paper_pricing_receipt_audit import pricing_receipt_evidence
from test_coinbase_paper_pricing import NOW, valid


def receipt_graph():
    quote = valid()
    quote.update(paper_price_model=MODEL)
    event = NOW + timedelta(seconds=1)
    execution = {'side': 'BUY', 'quantity': 1, 'price': 100.1, 'reference_price': quote['price'],
                 'fee_amount': .6006, 'fee_pct': .006, 'depth_vwap': 100.00000001,
                 'depth_order_value': 100, 'slippage_pct': .001, 'latency_pct': 0, 'market_impact_pct': 0}
    fill = {'fill_id': 'fill-1', 'market': 'crypto', 'symbol': 'BTC-USD',
            'quote_provider': SOURCE, 'quote_timestamp': NOW.isoformat(),
            'created_at': event + timedelta(milliseconds=10), 'fill_price': 100.1,
            'notional': 100.1, **{k: v for k, v in execution.items() if k != 'price'}}
    receipt = {'fill_id': 'fill-1', 'quote_id': quote['quote_id'], 'model_version': MODEL,
               'created_at': event, 'execution_authority': 'PAPER_SIMULATION_ONLY',
               'evidence': {'source_quote': quote, 'execution': execution,
                            'execution_authority': 'PAPER_SIMULATION_ONLY'}}
    entry = {'entry_quote_id': quote['quote_id'], 'decision_timestamp': NOW + timedelta(milliseconds=500)}
    return fill, receipt, event, entry


def check(graph):
    fill, receipt, event, entry = graph
    return pricing_receipt_evidence(fill, [receipt], execution_time=event, entry=entry)


def test_original_receipt_checks_original_times_without_promoting_authority():
    graph = receipt_graph()
    before = deepcopy(graph)
    result = check(graph)
    assert result['status'] == 'CONSISTENT_ORIGINAL_PAPER_RECEIPT'
    assert result['reasons'] == []
    assert result['fee_provenance'] == 'MODELED_NOT_ACCOUNT_VERIFIED'
    assert result['decision_time_commit_proof'] == 'NOT_ESTABLISHED'
    assert graph == before
    assert graph[1]['evidence']['source_quote']['verified'] is False


def test_missing_and_conflicting_receipts_remain_unavailable():
    fill, receipt, event, entry = receipt_graph()
    for receipts, reason in (([], 'missing_pricing_receipt'),
                              ([receipt, deepcopy(receipt)], 'conflicting_pricing_receipts')):
        result = pricing_receipt_evidence(fill, receipts, execution_time=event, entry=entry)
        assert result['status'] == 'UNAVAILABLE' and result['reasons'] == [reason]
    receipt['fill_id'] = 'another-fill'
    assert check((fill, receipt, event, entry))['reasons'] == ['missing_pricing_receipt']


@pytest.mark.parametrize('field,value,reason', [
    ('quote_id', 'another-quote', 'pricing_receipt_identity_mismatch'),
    ('model_version', 'unknown', 'pricing_receipt_identity_mismatch'),
    ('execution_authority', 'LIVE', 'pricing_receipt_authority_mismatch'),
    ('created_at', NOW + timedelta(minutes=1), 'late_or_invalid_pricing_receipt'),
    ('created_at', 'invalid', 'late_or_invalid_pricing_receipt'),
])
def test_conflicting_or_late_original_receipt(field, value, reason):
    graph = receipt_graph(); graph[1][field] = value
    result = check(graph)
    assert result['status'] == 'UNAVAILABLE' and reason in result['reasons']


@pytest.mark.parametrize('field,value', [('quantity', 2), ('fee_amount', 0), ('fee_pct', 0),
    ('slippage_pct', False), ('latency_pct', float('nan')), ('market_impact_pct', .02),
    ('reference_price', 99), ('price', 101), ('side', 'SELL')])
def test_execution_cost_and_identity_conflicts(field, value):
    graph = receipt_graph(); graph[1]['evidence']['execution'][field] = value
    assert check(graph)['status'] == 'UNAVAILABLE'


@pytest.mark.parametrize('field,value', [('symbol', 'ETH-USD'), ('provider', 'Yahoo Finance'),
    ('provider_quote_verified', True), ('verified', True), ('fee_account_verified', True),
    ('quote_timestamp', (NOW + timedelta(seconds=2)).isoformat()), ('received_at', None),
    ('bid', 200), ('sequence', None)])
def test_malformed_late_or_relabelled_quote_never_passes(field, value):
    graph = receipt_graph(); graph[1]['evidence']['source_quote'][field] = value
    assert check(graph)['status'] == 'UNAVAILABLE'


def test_stale_and_unaligned_original_references_are_not_replaced_with_current_quotes():
    for seconds in (3, 20):
        graph = receipt_graph()
        quote = graph[1]['evidence']['source_quote']
        quote['independent_comparison']['reference_quote']['quote_timestamp'] = (NOW-timedelta(seconds=seconds)).isoformat()
        assert 'invalid_original_pricing_book_or_reference' in check(graph)['reasons']


def test_entry_and_exit_quote_id_scopes_are_distinct():
    fill, receipt, event, entry = receipt_graph()
    entry['entry_quote_id'] = 'different-original-entry'
    assert 'entry_pricing_quote_id_mismatch' in check((fill, receipt, event, entry))['reasons']
    # An exit's receipt is compared to its own fill, never to the entry quote ID.
    fill['side'] = receipt['evidence']['execution']['side'] = 'SELL'
    receipt['evidence']['execution']['depth_vwap'] = 100
    assert pricing_receipt_evidence(fill, [receipt], execution_time=event)['status'] == 'CONSISTENT_ORIGINAL_PAPER_RECEIPT'


def test_other_venues_and_missing_quotes_are_not_invented():
    fill, receipt, event, entry = receipt_graph()
    fill['quote_provider'] = 'Yahoo Finance'
    result = pricing_receipt_evidence(fill, [receipt], execution_time=event, entry=entry)
    assert result['status'] == 'UNAVAILABLE'
    assert result['reasons'] == ['no_supported_original_pricing_receipt_contract']


def test_ingestion_or_replay_fields_cannot_prove_decision_time_consumption():
    graph = receipt_graph()
    graph[1]['evidence'].update(brain_ingested=True, decision_replay={'consumed_episode_keys': ['anything']})
    result = check(graph)
    assert result['status'] == 'CONSISTENT_ORIGINAL_PAPER_RECEIPT'
    assert result['scope'] == 'original_stored_receipt_consistency_only'
    assert 'decision_use' not in result


@pytest.mark.parametrize('field,value',[('depth_vwap',99),('depth_vwap',None),
    ('depth_order_value',1000000),('depth_order_value',None)])
def test_missing_or_conflicting_depth_evidence_is_unavailable(field,value):
    graph=receipt_graph();graph[1]['evidence']['execution'][field]=value
    assert check(graph)['status']=='UNAVAILABLE'


def test_matching_negative_costs_do_not_prove_valid_costs():
    graph=receipt_graph()
    graph[0]['slippage_pct']=graph[1]['evidence']['execution']['slippage_pct']=-.01
    assert 'pricing_execution_slippage_pct_mismatch' in check(graph)['reasons']


@pytest.mark.parametrize('late_field',['quote_timestamp','received_at'])
def test_independent_reference_received_after_decision_cannot_validate_that_decision(late_field):
    from kraken_reference_book import compare_reference
    graph=receipt_graph()
    quote=graph[1]['evidence']['source_quote']
    reference=quote['independent_comparison']['reference_quote']
    # Both observations are fresh and aligned at execution, but the independent
    # reference arrived after the original entry decision (0.5 seconds).
    reference[late_field]=(NOW+timedelta(milliseconds=750)).isoformat()
    quote['independent_comparison']=compare_reference(reference,quote,graph[2])
    assert quote['independent_comparison']['status']=='ALIGNED_VENUE_COMPARISON'
    result=check(graph)
    assert result['status']=='UNAVAILABLE'
    assert 'independent_reference_not_available_at_original_decision' in result['reasons']


def test_reference_at_decision_time_is_not_rejected_as_late():
    graph=receipt_graph()
    graph[3]['decision_timestamp']=NOW
    assert check(graph)['status']=='CONSISTENT_ORIGINAL_PAPER_RECEIPT'


@pytest.mark.parametrize('field,value',[('provider','Yahoo Finance'),('source','unknown'),
    ('venue','Coinbase'),('reference_only',False),('execution_quote_eligible',True),
    ('execution_authority','PAPER_SIMULATION_ONLY'),('provider_quote_verified',True)])
def test_reference_identity_and_authority_cannot_be_relabelled(field,value):
    graph=receipt_graph()
    graph[1]['evidence']['source_quote']['independent_comparison']['reference_quote'][field]=value
    assert 'independent_reference_identity_or_authority_mismatch' in check(graph)['reasons']
