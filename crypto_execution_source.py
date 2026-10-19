"""No configured crypto execution venue after removal of the broker integration.

Research feeds keep their original authority. This guard cannot authorize a fill.
Historical order/quote parsers and permanent evidence are unaffected.
"""
from asset_routing import infer_asset_class


def install_crypto_candidate_source_guard(worker, eligible, reason):
    """Apply the active pricing source's identity check to capital candidates too."""
    worker._crypto_execution_source_eligible = eligible
    original = worker._v39_signal_opportunity

    def opportunity(market, signal, prices, ranked_by_symbol, scan_type):
        result = original(market, signal, prices, ranked_by_symbol, scan_type)
        if str(market).strip().lower() != 'crypto':
            return result
        symbol = signal.get('symbol') if isinstance(signal, dict) else getattr(signal, 'symbol', '')
        symbol = str(symbol).upper().strip()
        try:
            valid = bool(eligible((prices or {}).get(symbol), symbol))
        except Exception:
            valid = False
        result['execution_source_eligible'] = valid
        if not valid:
            result['qualified_for_capital'] = False
            result['execution_source_rejection'] = reason
        return result

    worker._v39_signal_opportunity = opportunity


def install_unavailable_crypto_execution_source(oracle_module=None, worker=None):
    if oracle_module is None:
        import oracle_bot as oracle_module
    original = oracle_module._verified_quote_for
    if getattr(original, '_no_crypto_execution_source', False) is True:
        return False

    original_guard = oracle_module._execution_quote_guard
    if worker is not None:
        install_crypto_candidate_source_guard(worker, lambda quote, symbol: False,
                                              'CRYPTO_EXECUTION_SOURCE_NOT_CONFIGURED')

    def unavailable(market, symbol):
        return str(market).strip().lower() == 'crypto' or infer_asset_class(str(symbol).upper()) == 'crypto'

    def verified_quote(symbol, quotes, market='cash'):
        if unavailable(market, symbol):
            return None
        # Cached quotes from the removed provider cannot become stock quotes.
        payload = original(symbol, quotes, market)
        if payload and str(payload.get('provider', '')).strip().lower().startswith('robinhood'):
            return None
        return payload

    def execution_guard(market, symbol, price, signal, position=None, quote_metadata=None):
        if unavailable(market, symbol):
            return False, 'CRYPTO_EXECUTION_SOURCE_NOT_CONFIGURED'
        if quote_metadata and str(quote_metadata.get('provider', '')).strip().lower().startswith('robinhood'):
            return False, 'PROVIDER_REMOVED'
        return original_guard(market, symbol, price, signal, position, quote_metadata)

    verified_quote._no_crypto_execution_source = True
    oracle_module._verified_quote_for = verified_quote
    oracle_module._execution_quote_guard = execution_guard
    return True
