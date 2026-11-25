"""Bounded paper-price diagnostics; no quote acquisition or execution authority."""
from collections import deque
from copy import deepcopy
import json
import threading
import time

from kraken_reference_book import timestamp


class HealthPublication:
    """Publish an owned diagnostic snapshot with the original commit semantics."""

    def __init__(self, source, clock, publish, logger, label):
        self.source = source
        self.clock = clock
        self.publish = publish
        self.logger = logger
        self.label = label
        self.last_health = {}
        self._health_publication_signature = None
        self._health_publication_monotonic = float('-inf')
        self._health_attempt_signature = None
        self._health_attempt_monotonic = float('-inf')

    def _expire_stale_health(self):
        now = self.clock()
        for record in self.last_health.values():
            if record.get('status') != 'VALID_PAPER_PRICE':
                continue
            try:
                age = (now - timestamp(record['quote_timestamp'])).total_seconds()
                expired = age > 5 or age < -2
                reason = 'QUOTE_TTL_EXPIRED'
            except (KeyError, TypeError, ValueError, OverflowError):
                expired = True
                reason = 'INVALID_QUOTE_TIMESTAMP'
            if expired:
                record.update(status='STALE_PAPER_PRICE', reason=reason,
                              execution_authority='NONE')

    def _health_signature(self):
        return tuple(sorted(
            (symbol, record.get('status'), record.get('reason'))
            for symbol, record in self.last_health.items()
        ))

    def _health_publication_due(self):
        signature = self._health_signature()
        has_valid_quote = any(record.get('status') == 'VALID_PAPER_PRICE'
                              for record in self.last_health.values())
        current = time.monotonic()
        if not has_valid_quote:
            if (signature == self._health_publication_signature
                    and current - self._health_publication_monotonic < 60):
                return False
            if (signature == self._health_attempt_signature
                    and current - self._health_attempt_monotonic < 5):
                return False
        return True

    def _publish_health(self):
        self._expire_stale_health()
        if not self._health_publication_due():
            return None
        signature = self._health_signature()
        publication_status = 'LOG_ONLY'
        try:
            if self.publish:
                from database import connect
                with connect() as conn:
                    conn.execute('''INSERT INTO coinbase_paper_price_state VALUES(1,NOW(),%s::jsonb)
                        ON CONFLICT(id) DO UPDATE SET updated_at=EXCLUDED.updated_at,payload=EXCLUDED.payload''',
                        (json.dumps({'provider': self.source, 'symbols': self.last_health,
                                     'broker_submission': 'NONE'}),))
                publication_status = 'COMMITTED'
            self._health_publication_signature = signature
            self._health_publication_monotonic = time.monotonic()
        except Exception:
            publication_status = 'UNAVAILABLE'
        finally:
            self._health_attempt_signature = signature
            self._health_attempt_monotonic = time.monotonic()
        # A slow commit cannot make an expired receipt look current in logs.
        # Readers independently check persisted original timestamps as before.
        self._expire_stale_health()
        self.logger.info('%s | %s', self.label, json.dumps({
            'symbols': self.last_health, 'broker_submission': 'NONE',
            'health_publication_status': publication_status}))
        return publication_status


class LatestHealthPublisher:
    """One daemon, one pending snapshot; database I/O never holds the queue lock."""

    def __init__(self, sink, stop_event):
        self.sink = sink
        self.stop_event = stop_event
        self.thread = None
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._pending = None

    def start(self):
        with self._lock:
            if self.stop_event.is_set() or (self.thread and self.thread.is_alive()):
                return False
            self.thread = threading.Thread(target=self._run, name='paper-price-health', daemon=True)
            self.thread.start()
        return True

    def submit(self, snapshot):
        if not isinstance(snapshot, dict) or self.stop_event.is_set():
            return False
        # Diagnostics are bounded independently of the caller's source universe.
        snapshot = deepcopy(dict(deque(snapshot.items(), maxlen=8)))
        with self._lock:
            if self.stop_event.is_set():
                return False
            self._pending = snapshot
            self._event.set()
        return True

    def stop(self, timeout=1):
        self.stop_event.set()
        self._event.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout)
        return not self.thread or not self.thread.is_alive()

    def _run(self):
        retry_at = None
        while not self.stop_event.is_set():
            with self._lock:
                pending, self._pending = self._pending, None
                self._event.clear()
            if pending is not None:
                self.sink.last_health = pending
            if pending is not None or (retry_at is not None and time.monotonic() >= retry_at):
                unexpected_failure = False
                try:
                    status = self.sink._publish_health()
                except Exception as exc:
                    self.sink.logger.warning('PAPER PRICE HEALTH | status=UNAVAILABLE | reason=%s',
                                             type(exc).__name__)
                    status = 'UNAVAILABLE'
                    unexpected_failure = True
                if status == 'UNAVAILABLE':
                    retry_at = (time.monotonic() if unexpected_failure
                                else self.sink._health_attempt_monotonic) + 5
                elif status is not None:
                    retry_at = None
                elif self.sink._health_signature() == self.sink._health_publication_signature:
                    retry_at = None  # The latest state already has a durable acknowledgement.
                # A throttled new snapshot must not cancel a pending failed-write
                # retry. No successful publication is acknowledged before commit.
            if not self.stop_event.is_set():
                wait = .25 if retry_at is None else min(.25, max(.05, retry_at - time.monotonic()))
                self._event.wait(wait)
