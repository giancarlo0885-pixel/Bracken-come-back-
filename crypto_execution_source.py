"""No configured crypto execution venue after removal of the broker integration.

Research feeds keep their original authority. This guard cannot authorize a fill.
Historical order/quote parsers and permanent evidence are unaffected.
"""
from asset_routing import infer_asset_class


def install_unavailable_crypto_execution_source(oracle_module=None):
    if oracle_module is None:
        import oracle_bot as oracle_module
    original = oracle_module._verified_quote_for
    if getattr(original, '_no_crypto_execution_source', False) is True:
        return False

    original_guard = oracle_module._execution_quote_guard

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
