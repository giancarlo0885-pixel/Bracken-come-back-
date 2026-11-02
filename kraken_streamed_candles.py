"""Bounded same-venue closed candles on the elected Kraken socket; no orders.

A real REST bootstrap supplies history. A candle can close only after a later
interval is observed. Disconnects, gaps and revisions require fresh bootstrap.
"""
from datetime import timedelta
from decimal import Decimal
import os
import threading
import uuid

import pandas as pd

from kraken_reference_book import exact_decimal, timestamp

MAX_ROWS = 721  # 720 historical bars plus one uncommitted interval, per <=4 pairs.
COLUMNS = ['Open', 'High', 'Low', 'Close', 'Volume']


def enabled():
    return (os.getenv('KRAKEN_OHLC_WS_ENABLED', '').strip().lower() == 'true'
            and os.getenv('KRAKEN_REFERENCE_WS_ENABLED', '').strip().lower() == 'true'
            and os.getenv('EXECUTION_MODE', '').strip().lower() == 'paper'
            and os.getenv('ENABLE_BROKER_SUBMISSION', '').strip().lower() == 'false'
            and os.getenv('LIVE_TRADING_ARMED', '').strip().lower() == 'false')


class CandleStream:
    def __init__(self):
        self.lock = threading.RLock()
        self.connected = False
        self.reason = 'AWAITING_CONNECTION'
        self.session = None
        self.symbols = {}
        self.rows = {}
        self.trade_counts = {}
        self.latest = {}
        self.acked = set()
        self.snapshots = set()
        self.seeded = set()
        self.last_wire = None
        self.bootstrap_receipts = {}
        self.updates = 0

    def opened(self, symbols, now):
        if not 1 <= len(symbols) <= 4 or any(p != s.replace('-', '/') for p, s in symbols.items()):
            raise ValueError('Unsupported candle coverage')
        with self.lock:
            self.symbols = dict(symbols)
            self.rows = {s:{} for s in symbols.values()}
            self.trade_counts = {s:{} for s in symbols.values()}
            self.latest = {}
            self.acked = set(); self.snapshots = set(); self.seeded = set()
            self.bootstrap_receipts = {}
            self.connected = True
            self.reason = None
            self.session = uuid.uuid4().hex
            self.last_wire = now

    def invalidate(self, reason='DISCONNECTED'):
        with self.lock:
            self.connected = False
            self.reason = reason
            self.rows = {s:{} for s in self.symbols.values()}
            self.trade_counts = {s:{} for s in self.symbols.values()}
            self.seeded.clear()

    def wire(self, now):
        with self.lock:
            if self.connected:self.last_wire = now

    def _ready(self, symbol, now):
        return (self.connected and not self.reason and symbol in self.acked
                and symbol in self.snapshots and symbol in self.latest
                and self.last_wire is not None and 0 <= (now-self.last_wire).total_seconds() <= 15)

    def consume(self, message, now):
        """Called only for the ohlc channel/ack; failures never reclassify books."""
        with self.lock:
            if not self.connected:return
            try:
                if message.get('method') in {'subscribe', 'unsubscribe'}:
                    result = message.get('result') or {}
                    if (message['method'] != 'subscribe' or message.get('success') is not True
                            or result.get('channel') != 'ohlc' or result.get('interval') != 5
                            or result.get('snapshot') is not True or result.get('symbol') not in self.symbols):
                        raise ValueError('OHLC_SUBSCRIPTION_REJECTED')
                    self.acked.add(self.symbols[result['symbol']])
                    return
                data = message.get('data')
                kind = message.get('type')
                if message.get('channel') != 'ohlc' or kind not in {'snapshot','update'} or not isinstance(data,list) or not 1 <= len(data) <= MAX_ROWS*4:
                    raise ValueError('INVALID_OHLC_MESSAGE')
                parsed = []
                for row in data:
                    pair = row['symbol']; symbol = self.symbols.get(pair)
                    if symbol not in self.acked or (kind == 'update' and symbol not in self.snapshots):
                        raise ValueError('OHLC_SNAPSHOT_REQUIRED')
                    begin = timestamp(row['interval_begin'])
                    if type(row.get('interval')) is not int or row['interval'] != 5 or begin.timestamp()%300 != 0 or begin > now:
                        raise ValueError('INVALID_OHLC_INTERVAL')
                    values = tuple(exact_decimal(row[k]) for k in ('open','high','low','close','volume'))
                    o,h,l,c,v = values
                    if min(o,h,l,c) <= 0 or v < 0 or h < max(o,l,c) or l > min(o,h,c) or type(row.get('trades')) is not int or row['trades'] < 0:
                        raise ValueError('INVALID_OHLC_VALUES')
                    parsed.append((symbol,begin,values,row['trades']))
                for symbol,begin,values,trades in sorted(parsed,key=lambda r:(r[0],r[1])):
                    previous = self.rows[symbol].get(begin)
                    latest = self.latest.get(symbol)
                    if (latest is not None and begin < latest
                            and (previous != values
                                 or self.trade_counts[symbol].get(begin, trades) != trades)):
                        raise ValueError('CLOSED_CANDLE_REVISION')
                    if previous is not None and begin == latest:
                        if (values[0] != previous[0] or values[1] < previous[1]
                                or values[2] > previous[2] or values[4] < previous[4]
                                or trades < self.trade_counts[symbol].get(begin, 0)):
                            raise ValueError('OHLC_TIMESTAMP_OR_VOLUME_REGRESSION')
                    if latest is not None and begin > latest+timedelta(minutes=5):
                        raise ValueError('OHLC_GAP_REQUIRES_RESNAPSHOT')
                    self.rows[symbol][begin] = values
                    # Kraken OHLC trade count is cumulative within each interval.
                    # A decreasing count invalidates this optional research stream.
                    self.trade_counts[symbol][begin] = trades
                    self.latest[symbol] = max(begin,latest) if latest else begin
                    if kind == 'snapshot':self.snapshots.add(symbol)
                    while len(self.rows[symbol]) > MAX_ROWS:
                        oldest = min(self.rows[symbol])
                        del self.rows[symbol][oldest]
                        self.trade_counts[symbol].pop(oldest, None)
                    self.updates += 1
                self.last_wire = now
            except (ValueError,KeyError,TypeError,OverflowError,ArithmeticError):
                self.invalidate('OHLC_INVALID_REQUIRES_RECONNECT')

    def generation(self):
        with self.lock:return self.session

    def seed(self, symbol, frame, now, received_at, *, expected_session):
        """Never carry a REST/bootstrap result across a socket generation."""
        from kraken_research_data import validate_closed_history
        with self.lock:
            if expected_session != self.session or not self._ready(symbol,now) or symbol in self.seeded:return False
            try:receipt = timestamp(received_at.isoformat())
            except (AttributeError,ValueError):return False
            if receipt > now:return False
            route = frame.attrs.get('provider_route') or {}
            if (frame.attrs.get('provider') != 'Kraken Public OHLC'
                    or route.get('requested_symbol') != symbol or route.get('provider_symbol') != symbol
                    or route.get('interval') != '5m'):
                return False
            validate_closed_history(frame,now)
            if frame.index[-1].to_pydatetime()+timedelta(minutes=5) > receipt:return False
            if frame.index[-1].to_pydatetime() >= self.latest[symbol]:return False
            combined = {t.to_pydatetime():tuple(Decimal(str(v)) for v in row)
                        for t,row in frame[COLUMNS].iterrows()}
            if len(combined) > 720:return False
            for begin,values in self.rows[symbol].items():
                # Decimal string/float conversion in the historical frame is
                # validated at its existing numeric precision, not wire CRC.
                if begin in combined and any(float(a)!=float(b) for a,b in zip(values,combined[begin])):
                    return False
                combined[begin] = values
            self.rows[symbol] = dict(sorted(combined.items())[-MAX_ROWS:])
            self.seeded.add(symbol)
            self.bootstrap_receipts[symbol] = receipt.isoformat()
            try:self._frame(symbol,now)
            except ValueError:
                self.seeded.discard(symbol)
                return False
            return True

    def _frame(self, symbol, now):
        from kraken_research_data import validate_closed_history
        rows = [(t,v) for t,v in sorted(self.rows[symbol].items())
                if t < self.latest[symbol] and t+timedelta(minutes=5) <= now]
        frame = pd.DataFrame([tuple(float(x) for x in v) for _,v in rows],
                             columns=COLUMNS,index=pd.DatetimeIndex([t for t,_ in rows]))
        validate_closed_history(frame,now)
        frame.attrs.update(provider='Kraken Public OHLC',requested_symbol=symbol,provider_symbol=symbol,
            interval='5m',quote_verified=False,execution_quote_eligible=False,
            provider_route={'provider':'Kraken Public OHLC','requested_symbol':symbol,'provider_symbol':symbol,
                'interval':'5m','quote_verified':False,'quote_timestamp':frame.index[-1].isoformat(),
                'transport':'REST_BOOTSTRAP_AND_WEBSOCKET_V2_OHLC','ohlc_session_id':self.session,
                'bootstrap_received_at':self.bootstrap_receipts.get(symbol),
                'ohlc_crc_status':'NOT_PROVIDED_NOT_ORDER_BOOK_VERIFICATION'})
        return frame

    def history(self, symbol, now):
        with self.lock:
            if not self._ready(symbol,now) or symbol not in self.seeded:return None
            try:return self._frame(symbol,now)
            except ValueError:return None

    def health(self):
        with self.lock:
            return {'connected':self.connected,'reason':self.reason,'updates':self.updates,
                'session_id':self.session,'seeded_symbols':sorted(self.seeded),
                'bars':{s:len(r) for s,r in self.rows.items()},'execution_authority':'NONE'}
