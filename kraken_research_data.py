"""Bounded public research requests. No broker credentials or execution claims."""
from datetime import datetime, timezone
import time
import requests
import pandas as pd
from global_adaptive_engine import (provider_cooldown_active_live,
    reserve_provider_budget_live, mark_provider_cooldown_live)


class ResearchUnavailable(ValueError):
    pass


class PublicResearchData:
    def __init__(self, get=requests.get):
        self.get = get
        self.cooldowns = {}
        self.cache = {}
        self.last_receipt = None

    def request(self, provider, path, params=None):
        key = (provider, path, tuple(sorted((params or {}).items())))
        now = time.monotonic()
        if key in self.cache and now-self.cache[key][0] < 300:
            self.last_receipt = self.cache[key][2]
            return self.cache[key][1]
        if self.cooldowns.get(provider, 0) > now or provider_cooldown_active_live(provider).get('active'):
            raise ResearchUnavailable('PROVIDER_COOLDOWN')
        if not reserve_provider_budget_live(provider, '__provider__', daily_budget=1200 if provider == 'Kraken Public Research' else 2400,
                entitlement='public', data_mode='shadow_research').get('reserved'):
            raise ResearchUnavailable('PROVIDER_BUDGET_BLOCKED')
        root = 'https://api.kraken.com/0/public/' if provider == 'Kraken Public Research' else 'https://api.exchange.coinbase.com/'
        response = self.get(root+path, params=params, timeout=6,
                            headers={'User-Agent':'GARIBALDI-MARKET-ORACLE/1.0'})
        if response.status_code == 429:
            self.cooldowns[provider] = now+300
            mark_provider_cooldown_live(provider, seconds=300, reason='HTTP 429')
            raise ResearchUnavailable('HTTP_429')
        if response.status_code != 200:
            raise ResearchUnavailable('HTTP_'+str(response.status_code))
        payload = response.json()
        if provider == 'Kraken Public Research':
            errors = payload.get('error')
            if errors:
                if any('rate limit' in str(e).lower() or 'throttled' in str(e).lower() for e in errors):
                    self.cooldowns[provider] = now+300
                    mark_provider_cooldown_live(provider, seconds=300, reason='Kraken API rate limit')
                raise ResearchUnavailable('KRAKEN_API_ERROR')
            payload = payload['result']
        # This client is scoped to <=4 instruments; cache cannot grow with ticks.
        if len(self.cache) >= 12:
            self.cache.pop(next(iter(self.cache)))
        self.last_receipt = datetime.now(timezone.utc)
        self.cache[key] = (now, payload, self.last_receipt)
        return payload

    def instruments(self):
        return self.request('Kraken Public Research', 'AssetPairs', {'assetVersion':1})

    def history(self, symbol, now):
        result = self.request('Kraken Public Research', 'OHLC',
                              {'pair':symbol.replace('-','/'), 'assetVersion':1, 'interval':5})
        pair = symbol.replace('-','/')
        if set(result)-{'last'} != {pair}:
            raise ResearchUnavailable('HISTORY_SYMBOL_MISMATCH')
        rows = result[pair]
        if not isinstance(rows, list) or not 2 <= len(rows) <= 721:
            raise ResearchUnavailable('INVALID_HISTORY')
        # Kraken always includes an uncommitted final candle. Never train on it.
        frame = pd.DataFrame(rows[:-1], columns=['time','Open','High','Low','Close','vwap','Volume','count'])
        frame.index = pd.to_datetime(frame.pop('time'), unit='s', utc=True)
        frame = frame[['Open','High','Low','Close','Volume']].apply(pd.to_numeric, errors='coerce')
        validate_closed_history(frame, now)
        frame.attrs.update(provider='Kraken Public OHLC', requested_symbol=symbol, provider_symbol=symbol,
                           interval='5m', quote_verified=False, execution_quote_eligible=False,
                           provider_route={'provider':'Kraken Public OHLC','requested_symbol':symbol,
                              'provider_symbol':symbol,'interval':'5m','quote_verified':False,
                              'quote_timestamp':frame.index[-1].isoformat()})
        return frame


def select_instruments(metadata, requested):
    selected = {'BTC/USD':'BTC-USD', 'ETH/USD':'ETH-USD'}
    # Fixed ceiling and exact display symbols; never guess legacy aliases.
    for symbol in sorted(set(requested)):
        pair = symbol.replace('-','/')
        row = metadata.get(pair, {})
        if (len(selected) < 4 and symbol.endswith('-USD') and symbol[:-4].isalnum()
                and row.get('status') == 'online' and row.get('base') == symbol[:-4]
                and row.get('quote') == 'USD' and row.get('aclass_base') == 'currency'):
            selected[pair] = symbol
    return selected


def coinbase_quote(client, symbol, received=None):
    """Independent L1 diagnostic; cached source time is never refreshed."""
    from kraken_reference_book import timestamp, fresh, exact_decimal
    if not symbol.endswith('-USD') or not symbol[:-4].isalnum():
        raise ResearchUnavailable('UNSUPPORTED_SYMBOL')
    data = client.request('Coinbase Public Research', 'products/'+symbol+'/book', {'level':1})
    received = received or client.last_receipt
    if data.get('auction_mode') is True:
        raise ResearchUnavailable('COINBASE_AUCTION')
    event = timestamp(data.get('time'))
    if not fresh(event, received, received):
        raise ResearchUnavailable('COINBASE_STALE')
    bids, asks = data['bids'], data['asks']
    if len(bids) != 1 or len(asks) != 1:
        raise ResearchUnavailable('COINBASE_INVALID_BOOK')
    bid, ask = exact_decimal(bids[0][0]), exact_decimal(asks[0][0])
    bq, aq = exact_decimal(bids[0][1]), exact_decimal(asks[0][1])
    if not 0 < bid <= ask or bq <= 0 or aq <= 0:
        raise ResearchUnavailable('COINBASE_INVALID_BOOK')
    return {'symbol':symbol,'provider':'Coinbase Exchange public reference','bid':str(bid),'ask':str(ask),
            'price':str((bid+ask)/2),'bid_quantity':str(bq),'ask_quantity':str(aq),'quote_timestamp':event.isoformat(),
            'received_at':received.isoformat(),'timestamp_basis':'provider_payload',
            'reference_only':True,'quote_verified':False,'execution_quote_eligible':False,
            'execution_authority':'NONE','checksum_status':'NOT_PROVIDED'}


def coinbase_history(client, symbol, now):
    """Exact-venue research backup. Never mix Kraken returns with Coinbase marks."""
    data = client.request('Coinbase Public Research', 'products/'+symbol+'/candles', {'granularity':300})
    if not isinstance(data, list) or not 40 <= len(data) <= 300:
        raise ResearchUnavailable('COINBASE_INVALID_HISTORY')
    frame = pd.DataFrame(data, columns=['time','Low','High','Open','Close','Volume'])
    frame.index = pd.to_datetime(frame.pop('time'), unit='s', utc=True)
    frame = frame[frame.index <= pd.Timestamp(now)-pd.Timedelta(minutes=5)].sort_index()
    frame = frame[['Open','High','Low','Close','Volume']].apply(pd.to_numeric,errors='coerce')
    validate_closed_history(frame,now)
    frame.attrs.update(provider='Coinbase Exchange',interval='5m',quote_verified=False,
        provider_route={'provider':'Coinbase Exchange','requested_symbol':symbol,'provider_symbol':symbol,
                        'interval':'5m','quote_verified':False,'quote_timestamp':frame.index[-1].isoformat()})
    return frame


def validate_closed_history(frame, now):
    import numpy as np
    if (frame.empty or not np.isfinite(frame.to_numpy(dtype=float)).all()
            or (frame[['Open','High','Low','Close']] <= 0).any().any() or (frame.Volume < 0).any()
            or (frame.High < frame[['Open','Close','Low']].max(axis=1)).any()
            or (frame.Low > frame[['Open','Close','High']].min(axis=1)).any()
            or not frame.index.is_unique or not frame.index.is_monotonic_increasing
            or not (frame.index.to_series().diff().dropna() == pd.Timedelta(minutes=5)).all()
            or (now-frame.index[-1].to_pydatetime()).total_seconds() < 300
            or (now-frame.index[-1].to_pydatetime()).total_seconds() > 610):
        raise ResearchUnavailable('INVALID_OR_STALE_CLOSED_HISTORY')
