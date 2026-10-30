"""Kraken depth-10 pricing for paper simulation, never an exchange order gateway.

The public reference packet remains unchanged. A separate, locally registered
receipt binds its original depth/checksum, timestamps, session and product rules.
No independent venue is claimed; fees/slippage remain explicit assumptions.
"""
from copy import deepcopy
from decimal import Decimal, localcontext
import hashlib
import json
import os

from coinbase_paper_pricing import PaperPricing, FEE, paper_context_enabled
from kraken_research_data import ResearchUnavailable
from kraken_reference_book import (SOURCE as REFERENCE_SOURCE, DEPTH, book_checksum,
                                   exact_decimal, timestamp, fresh)

SOURCE = 'Kraken WebSocket v2 paper pricing'
MODEL = 'kraken-depth10-paper-v1'
FEE_MODEL = 'conservative_simulation_60bps_per_side_not_account_verified'


def enabled():
    return (paper_context_enabled()
            and os.getenv('CRYPTO_PAPER_PRICING_PROVIDER', 'coinbase').strip().lower() == 'kraken')


def validate_rules(symbol, metadata):
    pair = symbol.replace('-', '/')
    row = metadata.get(pair) if isinstance(metadata, dict) else None
    if (not isinstance(row, dict) or row.get('base') != symbol[:-4] or row.get('quote') != 'USD'
            or row.get('aclass_base') != 'currency' or row.get('aclass_quote') != 'currency'
            or row.get('status') != 'online' or type(row.get('lot_multiplier')) is not int or row.get('lot_multiplier') != 1):
        raise ResearchUnavailable('KRAKEN_PRODUCT_UNAVAILABLE')
    decimals = row.get('lot_decimals')
    if type(decimals) is not int or not 0 <= decimals <= 18:
        raise ResearchUnavailable('KRAKEN_PRODUCT_RULES_UNKNOWN')
    quantity, cost, tick = (exact_decimal(row.get(k)) for k in ('ordermin', 'costmin', 'tick_size'))
    if min(quantity, cost, tick) <= 0:
        raise ResearchUnavailable('KRAKEN_PRODUCT_RULES_UNKNOWN')
    keys = ('base', 'quote', 'aclass_base', 'aclass_quote', 'status', 'lot_multiplier',
            'lot_decimals', 'ordermin', 'costmin', 'tick_size')
    return {k: row[k] for k in keys}


def validate_kraken_book(symbol, reference, rules, metadata_received_at, now):
    """Recompute CRC32 and validate original source time; never re-date a book."""
    if (not isinstance(symbol, str) or not symbol.endswith('-USD') or not symbol[:-4].isalnum()
            or not isinstance(reference, dict)):
        raise ResearchUnavailable('KRAKEN_SYMBOL_OR_BOOK_INVALID')
    expected = {'provider': REFERENCE_SOURCE, 'source': REFERENCE_SOURCE, 'venue': 'Kraken',
        'symbol': symbol, 'exchange_symbol': symbol.replace('-', '/'), 'checksum_status': 'VALID',
        'reference_only': True, 'verified': False, 'quote_verified': False,
        'provider_quote_verified': False, 'paper_reference_verified': False,
        'execution_quote_eligible': False, 'execution_authority': 'NONE', 'stale': False}
    if any(type(reference.get(k)) is not type(v) or reference.get(k) != v for k, v in expected.items()):
        raise ResearchUnavailable('KRAKEN_REFERENCE_IDENTITY_OR_AUTHORITY_INVALID')
    if not isinstance(reference.get('session_id'), str) or not 1 <= len(reference['session_id']) <= 64:
        raise ResearchUnavailable('KRAKEN_SNAPSHOT_SESSION_REQUIRED')
    event, received = timestamp(reference.get('quote_timestamp')), timestamp(reference.get('received_at'))
    if (not fresh(event, received, now) or not 0 <= (now-event).total_seconds() <= 5
            or not 0 <= (now-received).total_seconds() <= 5):
        raise ResearchUnavailable('STALE_KRAKEN_PAPER_BOOK')
    metadata_time = timestamp(metadata_received_at)
    if not 0 <= (now-metadata_time).total_seconds() <= 300:
        raise ResearchUnavailable('STALE_KRAKEN_PRODUCT_RULES')
    rules = validate_rules(symbol, {symbol.replace('-', '/'): rules})
    book = reference.get('book')
    if not isinstance(book, dict):
        raise ResearchUnavailable('KRAKEN_DEPTH_SNAPSHOT_REQUIRED')
    levels, sides = {}, {}
    for side in ('bids', 'asks'):
        rows = book.get(side)
        if not isinstance(rows, list) or not 1 <= len(rows) <= DEPTH:
            raise ResearchUnavailable('INVALID_KRAKEN_DEPTH')
        parsed = []
        for row in rows:
            if not isinstance(row, list) or len(row) != 2:
                raise ResearchUnavailable('INVALID_KRAKEN_LEVEL')
            price, quantity = map(exact_decimal, row)
            if price <= 0 or quantity <= 0:
                raise ResearchUnavailable('INVALID_KRAKEN_LEVEL')
            if parsed and ((side == 'bids' and price >= parsed[-1][0])
                           or (side == 'asks' and price <= parsed[-1][0])):
                raise ResearchUnavailable('UNSORTED_OR_DUPLICATE_KRAKEN_DEPTH')
            parsed.append((price, quantity))
        sides[side] = dict(parsed)
        levels[side] = [[str(p), str(q)] for p, q in parsed]
    checksum = reference.get('checksum')
    if type(checksum) is not int or book_checksum(sides['bids'], sides['asks']) != checksum:
        raise ResearchUnavailable('KRAKEN_CHECKSUM_FAILURE')
    bid, ask = max(sides['bids']), min(sides['asks'])
    if bid >= ask:
        raise ResearchUnavailable('CROSSED_OR_LOCKED_KRAKEN_PAPER_BOOK')
    with localcontext() as context:
        context.prec = 80
        mid = (bid+ask)/2
        spread = (ask-bid)/mid
    if spread > Decimal('.015'):
        raise ResearchUnavailable('PAPER_SPREAD_LIMIT')
    for key, actual in (('bid', bid), ('ask', ask), ('price', mid),
                        ('bid_quantity', sides['bids'][bid]), ('ask_quantity', sides['asks'][ask])):
        if exact_decimal(reference.get(key)) != actual:
            raise ResearchUnavailable('KRAKEN_TOP_OF_BOOK_MISMATCH')
    minimum = max(exact_decimal(rules['costmin']), exact_decimal(rules['ordermin'])*mid)
    quote = {'symbol': symbol, 'requested_symbol': symbol, 'provider_symbol': symbol,
        'provider_native_symbol': reference['exchange_symbol'], 'provider': SOURCE, 'source': SOURCE,
        'price': float(mid), 'mid_price': str(mid), 'bid': float(bid), 'ask': float(ask),
        'bid_quantity': str(sides['bids'][bid]), 'ask_quantity': str(sides['asks'][ask]),
        'spread_pct': float(spread), 'book': levels, 'checksum': checksum, 'checksum_status': 'VALID',
        'session_id': reference['session_id'], 'quote_timestamp': event.isoformat(),
        'timestamp': event.isoformat(), 'received_at': received.isoformat(), 'fetched_at': received.isoformat(),
        'timestamp_basis': 'kraken_exchange_book_time', 'interval': '1m', 'source_interval': '1m',
        'stale': False, 'quote_verified': True, 'verified': False, 'provider_quote_verified': False,
        'paper_reference_verified': True, 'execution_quote_eligible': True, 'reference_only': False,
        'execution_authority': 'PAPER_SIMULATION_ONLY', 'broker_submission': 'NONE',
        'source_capability': 'public_checksum_depth10_paper_pricing',
        'verification_basis': 'paper:kraken_crc32_single_venue',
        'independent_validation': 'NOT_REQUIRED_SINGLE_VENUE_PAPER_SIMULATION',
        'paper_fee_pct': FEE, 'estimated_fees_pct': FEE, 'fee_model': FEE_MODEL,
        'fee_account_verified': False, 'fee_source': 'EXPLICIT_SIMULATION_ASSUMPTION',
        'paper_price_model': MODEL, 'min_order_notional': str(minimum),
        'min_order_quantity': rules['ordermin'], 'base_increment': str(Decimal(1).scaleb(-rules['lot_decimals'])),
        'quote_increment': rules['tick_size'], 'product_id': reference['exchange_symbol'],
        'product_status': 'online', 'product_rules': rules, 'product_metadata_received_at': metadata_received_at,
        'original_reference': deepcopy(reference)}
    identity = hashlib.sha256(json.dumps(quote, sort_keys=True, allow_nan=False).encode()).hexdigest()
    quote.update(quote_id='kraken-paper:'+identity, source_identity='kraken-paper:'+identity,
                 cache_identity='kraken-paper:'+symbol+':'+reference['session_id']+':'+str(checksum)+':'+event.isoformat())
    return quote


class KrakenPaperPricing(PaperPricing):
    source = SOURCE

    def available(self):
        return enabled()

    def history(self, symbol):
        return self.data.history(symbol, self.clock())

    def quote(self, symbol):
        symbol = str(symbol).upper().strip()
        if not self.available():
            return None
        with self.lock:
            try:
                state = self.reference()
                if not state.get('connected') or symbol not in state.get('subscribed_symbols', []):
                    raise ResearchUnavailable('KRAKEN_CONNECTED_SNAPSHOT_REQUIRED')
                reference = state.get('quotes', {}).get(symbol)
                if not reference:
                    raise ResearchUnavailable('NO_FRESH_KRAKEN_BOOK')
                # Existing bounded HTTP cache and shared allowance apply only to
                # instrument metadata. Tick pricing uses the one elected feed.
                with self.data.lock:
                    metadata = self.data.instruments()
                    rules = validate_rules(symbol, metadata)
                    metadata_received = self.data.last_receipt.isoformat()
                current = self.reference()
                reference = current.get('quotes', {}).get(symbol)
                if not current.get('connected') or not reference:
                    raise ResearchUnavailable('KRAKEN_CONNECTED_SNAPSHOT_REQUIRED')
                quote = validate_kraken_book(symbol, reference, rules, metadata_received, self.clock())
                self.receipts = {k: v for k, v in self.receipts.items()
                                 if (self.clock()-timestamp(v['received_at'])).total_seconds() <= 5}
                self.receipts[quote['quote_id']] = deepcopy(quote)
                try:
                    from paper_regime_economics_shadow import observe_paper_quote
                    observe_paper_quote(self, quote)
                except Exception:
                    pass  # Measurement cannot grant execution authority.
                self.last_health[symbol] = {'status': 'VALID_PAPER_PRICE',
                    'quote_timestamp': quote['quote_timestamp'], 'quote_id': quote['quote_id'],
                    'execution_authority': 'PAPER_SIMULATION_ONLY', 'checksum_status': 'VALID',
                    'independent_validation': quote['independent_validation']}
                return deepcopy(quote)
            except Exception as exc:
                if len(self.last_health) >= 8 and symbol not in self.last_health:
                    self.last_health.pop(next(iter(self.last_health)))
                self.last_health[symbol] = {'status': 'UNAVAILABLE',
                    'reason': str(exc) if isinstance(exc, ResearchUnavailable) else type(exc).__name__}
                return None
            finally:
                self._publish_health()

    def authentic(self, quote, symbol):
        if not super().authentic(quote, symbol):
            return False
        # A disconnected, invalidated or replaced session cannot authorize a
        # previously registered receipt even within the five-second quote TTL.
        try:
            state = self.reference()
            current = state.get('quotes', {}).get(symbol, {})
            return (state.get('connected') is True and current.get('checksum_status') == 'VALID'
                    and current.get('session_id') == quote.get('session_id')
                    and fresh(timestamp(current['quote_timestamp']), timestamp(current['received_at']), self.clock()))
        except (ValueError, KeyError, TypeError, AttributeError):
            return False
