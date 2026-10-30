"""Read-only consistency checks of original, explicitly linked paper receipts.

These checks do not replay an exchange feed or prove decision-time consumption.
Fees remain simulation assumptions, never account-verified exchange charges.
"""
from paper_fill_provenance import _same_json, _time, close, number


def pricing_receipt_evidence(fill, receipts, *, execution_time, entry=None):
    from coinbase_paper_pricing import MODEL, SOURCE, depth_price, validate_book

    result = {'status': 'UNAVAILABLE', 'reasons': [],
              'scope': 'original_stored_receipt_consistency_only',
              'decision_time_commit_proof': 'NOT_ESTABLISHED',
              'fee_provenance': 'MODELED_NOT_ACCOUNT_VERIFIED'}
    reasons = result['reasons']
    from kraken_paper_pricing import MODEL as KRAKEN_MODEL, SOURCE as KRAKEN_SOURCE
    kraken = fill.get('quote_provider') == KRAKEN_SOURCE
    if kraken:
        MODEL, SOURCE = KRAKEN_MODEL, KRAKEN_SOURCE
    if fill.get('quote_provider') != SOURCE:

        reasons.append('no_supported_original_pricing_receipt_contract')
        return result
    matches = [r for r in receipts if r.get('fill_id') == fill.get('fill_id')]
    if len(matches) != 1:
        reasons.append('missing_pricing_receipt' if not matches else 'conflicting_pricing_receipts')
        return result
    receipt = matches[0]
    evidence = receipt.get('evidence')
    evidence = evidence if isinstance(evidence, dict) else {}
    quote, execution = evidence.get('source_quote'), evidence.get('execution')
    if not isinstance(quote, dict) or not isinstance(execution, dict):
        reasons.append('malformed_pricing_receipt')
        return result
    event, recorded, written = (_time(execution_time), _time(fill.get('created_at')),
                                _time(receipt.get('created_at')))
    if (event is None or recorded is None or written is None or event > recorded or written > recorded):
        reasons.append('late_or_invalid_pricing_receipt')
    if (not receipt.get('quote_id') or receipt.get('quote_id') != quote.get('quote_id')
            or quote.get('source_identity') != receipt.get('quote_id')
            or receipt.get('model_version') != MODEL or quote.get('paper_price_model') != MODEL):
        reasons.append('pricing_receipt_identity_mismatch')
    if (fill.get('market') != 'crypto' or quote.get('symbol') != fill.get('symbol')
            or quote.get('provider') != SOURCE):
        reasons.append('pricing_receipt_scope_mismatch')
    if entry is not None and entry.get('entry_quote_id') != receipt.get('quote_id'):
        reasons.append('entry_pricing_quote_id_mismatch')
    if any(obj.get('execution_authority') != 'PAPER_SIMULATION_ONLY'
           for obj in (receipt, evidence, quote)):
        reasons.append('pricing_receipt_authority_mismatch')
    for key in ('quantity', 'fee_amount', 'fee_pct', 'reference_price',
                'slippage_pct', 'latency_pct', 'market_impact_pct'):
        value = number(execution.get(key))
        if (value is None or value < 0 or (key in {'quantity','reference_price'} and value == 0)
                or not close(execution.get(key), fill.get(key), quantity=key == 'quantity')):
            reasons.append('pricing_execution_' + key + '_mismatch')
    if execution.get('side') != fill.get('side') or not close(execution.get('price'), fill.get('fill_price')):
        reasons.append('pricing_execution_identity_or_price_mismatch')
    if (not close(execution.get('fee_pct'), .006)
            or not close(fill.get('fee_amount'), (number(fill.get('notional')) or 0) * number(.006))
            or quote.get('fee_account_verified') is not False):
        reasons.append('pricing_modeled_fee_mismatch')
    observed, received = _time(quote.get('quote_timestamp')), _time(quote.get('received_at'))
    decision = _time(entry.get('decision_timestamp')) if entry is not None else event
    if (event is None or observed is None or received is None or decision is None
            or observed > decision or received > decision or decision > event):
        reasons.append('quote_not_available_at_original_decision')
    if _time(fill.get('quote_timestamp')) != observed:
        reasons.append('fill_quote_timestamp_mismatch')
    if kraken:
        return _kraken_pricing_receipt(result, quote, execution, fill, received, event, decision)
    comparison = quote.get('independent_comparison')
    comparison = comparison if isinstance(comparison, dict) else {}
    reference = comparison.get('reference_quote')
    reference = reference if isinstance(reference, dict) else {}
    from kraken_reference_book import SOURCE as KRAKEN_SOURCE
    reference_identity = {'provider': KRAKEN_SOURCE, 'source': KRAKEN_SOURCE, 'venue': 'Kraken',
        'reference_only': True, 'verified': False, 'quote_verified': False,
        'provider_quote_verified': False, 'paper_reference_verified': False,
        'quote_eligible': False, 'execution_quote_eligible': False, 'execution_authority': 'NONE'}
    if any(not _same_json(reference.get(k), v) for k, v in reference_identity.items()):
        reasons.append('independent_reference_identity_or_authority_mismatch')
    reference_observed = _time(reference.get('quote_timestamp'))
    reference_received = _time(reference.get('received_at'))
    if (decision is None or reference_observed is None or reference_received is None
            or reference_observed > decision or reference_received > decision):
        reasons.append('independent_reference_not_available_at_original_decision')
    try:
        # Revalidate the original book at its execution time, never at audit time.
        # Do not fetch a new quote or substitute a later independent reference.
        book = quote['book']
        checked = validate_book(fill['symbol'], {
            'time': quote['quote_timestamp'], 'sequence': quote['sequence'],
            'auction_mode': False, 'bids': book['bids'], 'asks': book['asks'],
        }, received, comparison.get('reference_quote'), event)
        excluded = {'quote_id', 'source_identity', 'independent_comparison'}
        if any(not _same_json(quote.get(k), v) for k, v in checked.items() if k not in excluded):
            reasons.append('original_pricing_book_or_provenance_mismatch')
        fields = ('status', 'symbol', 'primary_timestamp', 'reference_timestamp',
                  'primary_provider', 'reference_provider', 'alignment_basis',
                  'primary_received_at', 'primary_timestamp_basis',
                  'timestamp_delta_seconds', 'mid_difference_bps', 'execution_impact')
        if any(not _same_json(comparison.get(k), checked['independent_comparison'].get(k)) for k in fields):
            reasons.append('original_independent_comparison_mismatch')
        if not close(execution.get('reference_price'), quote.get('price')):
            reasons.append('original_reference_price_mismatch')
        if not close(depth_price(quote, fill['side'], execution.get('depth_order_value')), execution.get('depth_vwap')):
            reasons.append('original_depth_vwap_mismatch')
    except (ValueError, KeyError, TypeError, ArithmeticError, AttributeError):
        reasons.append('invalid_original_pricing_book_or_reference')
    if not reasons:
        result['status'] = 'CONSISTENT_ORIGINAL_PAPER_RECEIPT'
    return result


def _kraken_pricing_receipt(result, quote, execution, fill, received, event, decision):
    from kraken_paper_pricing import validate_kraken_book
    from coinbase_paper_pricing import depth_price
    reasons = result['reasons']
    try:
        metadata_time = _time(quote.get('product_metadata_received_at'))
        if decision is None or metadata_time is None or metadata_time > decision:
            reasons.append('product_rules_not_available_at_original_decision')
        checked = validate_kraken_book(fill['symbol'], quote['original_reference'],
            quote['product_rules'], quote['product_metadata_received_at'], event)
        if any(not _same_json(quote.get(k), v) for k, v in checked.items()):
            reasons.append('original_pricing_book_or_provenance_mismatch')
        if not close(execution.get('reference_price'), quote.get('price')):
            reasons.append('original_reference_price_mismatch')
        if not close(depth_price(quote, fill['side'], execution.get('depth_order_value')), execution.get('depth_vwap')):
            reasons.append('original_depth_vwap_mismatch')
    except (ValueError, KeyError, TypeError, ArithmeticError, AttributeError):
        reasons.append('invalid_original_kraken_pricing_book')
    if not reasons:
        result['status'] = 'CONSISTENT_ORIGINAL_PAPER_RECEIPT'
    return result


def audit_sample_pricing_receipts(conn, allocations):
    """At most two fills per sampled allocation; receipt lookup uses its PK."""
    fills = {}
    for allocation in allocations:
        for prefix in ('', 'entry_'):
            fill = allocation.get(prefix + 'fill')
            execution = allocation.get(prefix + 'execution')
            ledger = allocation.get(prefix + 'ledger')
            if not isinstance(fill, dict) or not isinstance(execution, dict):
                continue
            fills.setdefault(fill['fill_id'], (fill, execution, ledger))
    available = bool(conn.execute("SELECT to_regclass('paper_fill_price_evidence') AS relation").fetchone()['relation'])
    receipts = list(conn.execute('''SELECT fill_id,model_version,quote_id,evidence,created_at,execution_authority
        FROM paper_fill_price_evidence WHERE fill_id=ANY(%s)''', (list(fills),)).fetchall()) if fills and available else []
    records = []
    for fill_id, (fill, execution, ledger) in sorted(fills.items()):
        records.append({'fill_id': fill_id, 'side': fill.get('side'), 'symbol': fill.get('symbol'),
                        **pricing_receipt_evidence(fill, receipts, execution_time=execution.get('created_at'),
                            entry=ledger if fill.get('side') == 'BUY' else None)})
    return {'receipt_table_available': available, 'sample_fills': len(records),
            'consistent_original_receipts': sum(r['status'] == 'CONSISTENT_ORIGINAL_PAPER_RECEIPT' for r in records),
            'records': records, 'execution_impact': 'NONE'}
