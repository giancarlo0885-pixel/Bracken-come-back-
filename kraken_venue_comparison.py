"""Bounded, non-execution observations of existing Robinhood quote responses."""
from kraken_reference_book import SYMBOLS, timestamp, exact_decimal
from robinhood_quote_core_fix import _first_present, _normalized_book


def canonical_pair(value):
    pair = str(value or '').upper().strip()
    return SYMBOLS.get(pair, pair if pair in SYMBOLS.values() else None)


def robinhood_observations(requested, records, received):
    symbols = {s for value in requested if (s := canonical_pair(value))}
    result = {}
    for symbol in symbols:
        rows = [r for r in records if isinstance(r, dict) and canonical_pair(r.get('symbol')) == symbol] if isinstance(records, list) else []
        observed = {'symbol':symbol, 'provider':'Robinhood Crypto',
                    'received_at':received.isoformat(), 'timestamp_basis':'provider_payload',
                    'source_identity':'Robinhood Crypto:best_bid_ask'}
        if len(rows) != 1:
            observed['comparison_rejection'] = ('PRIMARY_REQUEST_FAILED' if records is None else
                'PRIMARY_SYMBOL_NOT_RETURNED' if not rows else 'PRIMARY_AMBIGUOUS_SYMBOL')
        else:
            row = rows[0]
            try:
                observed['quote_timestamp'] = timestamp(row.get('timestamp')).isoformat()
            except ValueError:
                observed['comparison_rejection'] = ('PRIMARY_TIMESTAMP_MISSING' if row.get('timestamp') is None else 'PRIMARY_TIMESTAMP_INVALID')
            # Bound diagnostic numeric storage before the canonical book parser.
            bid = ask = None
            try:
                bid = exact_decimal(str(_first_present(row,'bid_price','bid','bid_inclusive_of_sell_spread')))
                ask = exact_decimal(str(_first_present(row,'ask_price','ask','ask_inclusive_of_buy_spread')))
            except ValueError:
                pass
            book = _normalized_book(row) if bid is not None and ask is not None else None
            if book is None:
                reason = 'PRIMARY_INVALID_BOOK'
                if bid is not None and ask is not None and bid > ask > 0:
                    reason = 'PRIMARY_CROSSED_BOOK'
                observed['comparison_rejection'] = reason
            else:
                observed.update(bid=str(book['bid']), ask=str(book['ask']))
        result[symbol] = observed
    return result
