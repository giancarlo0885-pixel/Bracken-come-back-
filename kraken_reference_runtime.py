"""One elected crypto-worker ingests Kraken; dashboards only read bounded state."""
from __future__ import annotations

from datetime import datetime, timezone
from collections import deque
import json
import logging
import os
import random
import threading
import time

import websocket

from kraken_reference_book import (BookIntegrityError, ReferenceBooks, SYMBOLS, SOURCE,
                                   MAX_AGE, MAX_ALIGNMENT, compare_reference, fresh, timestamp)

log = logging.getLogger("kraken-reference")
URL = "wss://ws.kraken.com/v2"
OWNER_LOCK = 73420261603
STATE_DDL = """CREATE TABLE IF NOT EXISTS kraken_reference_state (
    id SMALLINT PRIMARY KEY CHECK (id=1), updated_at TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL)"""


def utc_now():
    return datetime.now(timezone.utc)


def reconnect_delay(attempt, uniform=random.uniform):
    # >=5 seconds even after transient errors: <=120 attempts/10 minutes/IP
    # from this process, below Kraken's documented approximate 150 limit.
    return min(60.0, max(5.0, min(60.0, 5 * 2 ** min(attempt, 4)) * uniform(0.8, 1.2)))


def claim_owner(conn):
    return bool(conn.execute("SELECT pg_try_advisory_lock(%s) AS owned", (OWNER_LOCK,)).fetchone()["owned"])


def write_state(conn, payload, now):
    conn.execute("""INSERT INTO kraken_reference_state(id,updated_at,payload)
        VALUES (1,%s,%s::jsonb) ON CONFLICT (id) DO UPDATE
        SET updated_at=EXCLUDED.updated_at,payload=EXCLUDED.payload""", (now, json.dumps(payload)))


def comparison_current(symbol, comparison, quote, now):
    try:
        if not 0 <= (now - timestamp(comparison["checked_at"])).total_seconds() <= MAX_AGE:
            return False
        if comparison.get("status") != "ALIGNED_VENUE_COMPARISON":
            return True  # Unavailability/invalid-book diagnostics contain no divergence.
        primary_time = timestamp(comparison["primary_timestamp"])
        reference_time = timestamp(comparison["reference_timestamp"])
        paired = comparison.get("reference_quote", quote)
        return (comparison.get("symbol") == symbol
                and comparison.get("reference_provider") == SOURCE
                and paired.get("symbol") == symbol and paired.get("provider") == SOURCE
                and paired.get("checksum_status") == "VALID" and paired.get("reference_only") is True
                and paired.get("execution_quote_eligible") is False
                and reference_time == timestamp(paired["quote_timestamp"])
                and fresh(reference_time, timestamp(paired["received_at"]), now)
                and (comparison.get("primary_received_at") is None
                     or fresh(primary_time, timestamp(comparison["primary_received_at"]), now))
                and -2 <= (now - primary_time).total_seconds() <= MAX_AGE
                and -2 <= (now - reference_time).total_seconds() <= MAX_AGE
                and abs((reference_time - primary_time).total_seconds()) <= MAX_ALIGNMENT)
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def display_state(payload, published_at, now):
    """Revalidate at read time. A dead publisher or old book is never live."""
    result = json.loads(json.dumps(payload))
    result["execution_authority"] = "NONE"
    result["broker_submission"] = "NONE"
    try:
        published = timestamp(published_at) if isinstance(published_at, str) else published_at
        result["delivery"] = "SHARED_DIAGNOSTIC_SNAPSHOT"
        result["published_at"] = published.isoformat()
        result["publisher_age_seconds"] = (now - published).total_seconds()
        if not 0 <= (now - published).total_seconds() <= MAX_AGE:
            raise ValueError("publisher stale")
        quotes = {}
        for symbol, quote in result.get("quotes", {}).items():
            if (symbol in result.get("subscribed_symbols", SYMBOLS.values()) and quote.get("checksum_status") == "VALID"
                    and fresh(timestamp(quote["quote_timestamp"]), timestamp(quote["received_at"]), now)):
                quote.update(reference_only=True, verified=False, quote_verified=False,
                             provider_quote_verified=False, paper_reference_verified=False,
                             execution_quote_eligible=False, quote_eligible=False,
                             provider=SOURCE, source=SOURCE, execution_authority="NONE",
                             age_seconds=(now - timestamp(quote["quote_timestamp"])).total_seconds())
                quotes[symbol] = quote
        result["quotes"] = quotes if result.get("connected") else {}
        if result.get("connected") and len(quotes) != len(result.get("subscribed_symbols", SYMBOLS)):
            result["status"] = "STALE_OR_AWAITING_SNAPSHOT"
        result["comparisons"] = {
            s: v for s, v in result.get("comparisons", {}).items()
            if s in result["quotes"] and comparison_current(s, v, result["quotes"][s], now)
        }
        for comparison in result["comparisons"].values():
            if comparison.get("reference_quote"):
                comparison["reference_quote"]["age_seconds"] = (now-timestamp(comparison["reference_timestamp"])).total_seconds()
    except (ValueError, TypeError, KeyError):
        result.update(status="PUBLISHER_STALE", connected=False, quotes={}, comparisons={})
    return result


def read_kraken_reference_state():
    """No connection to Kraken, DDL, or background thread from a reader."""
    try:
        from database import connect
        with connect() as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            conn.execute("SET LOCAL statement_timeout='2s'")
            row = conn.execute("SELECT updated_at,payload FROM kraken_reference_state WHERE id=1").fetchone()
        if row:
            return display_state(row["payload"], row["updated_at"], utc_now())
    except Exception:
        pass
    return {"provider": SOURCE, "status": "UNAVAILABLE_OR_DISABLED", "quotes": {},
            "execution_authority": "NONE", "broker_submission": "NONE"}


class KrakenReferenceService:
    def __init__(self, *, connect_factory=None, socket_factory=None, research_symbols=None):
        if connect_factory is None:
            from database import connect
            connect_factory = connect
        self.connect_factory = connect_factory
        self.socket_factory = socket_factory or websocket.create_connection
        self.symbols = dict(SYMBOLS)
        self.research_symbols = research_symbols
        self.state = ReferenceBooks(self.symbols)
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.primary = {}
        self.reference_history = {symbol:deque(maxlen=128) for symbol in self.symbols.values()}
        self.thread = None
        self.socket = None
        self.last_log = 0.0

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return False
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._run, name="kraken-reference", daemon=True)
            self.thread.start()
            return True

    def stop(self):
        self.stop_event.set()
        if self.socket:
            self.socket.close()

    def observe_response(self, requested, records, received):
        from kraken_venue_comparison import robinhood_observations
        observations = robinhood_observations(requested, records, received, self.symbols)
        with self.lock:
            for symbol, observation in observations.items():
                previous = self.primary.get(symbol, {})
                if previous.get('received_at') and timestamp(previous['received_at']) > received:
                    continue
                if (previous.get('quote_timestamp') and observation.get('quote_timestamp')
                        and timestamp(observation['quote_timestamp']) < timestamp(previous['quote_timestamp'])):
                    observation['comparison_rejection'] = 'PRIMARY_TIMESTAMP_REGRESSION'
                self.primary[symbol] = observation

    def remember_references(self, now):
        # At most ten samples/second per pair, max 128 per pair and ten-second age.
        with self.lock:
            for exchange, symbol in self.symbols.items():
                history = self.reference_history[symbol]
                while history and (now-timestamp(history[0]['received_at'])).total_seconds() > MAX_AGE:
                    history.popleft()
                if history and (now-timestamp(history[-1]['received_at'])).total_seconds() < .1:
                    continue
                quote = self.state.quote(exchange, now)
                if quote and (not history or history[-1]['quote_timestamp'] != quote['quote_timestamp']):
                    history.append(quote)

    def clear_references(self):
        with self.lock:
            for history in self.reference_history.values():
                history.clear()

    def publish(self, conn):
        now = utc_now()
        payload = self.state.health(now)
        payload["subscribed_symbols"] = list(self.symbols.values())
        with self.lock:
            primary = dict(self.primary)
            histories = {s:list(h) for s,h in self.reference_history.items()}
        comparisons = {}
        for symbol in self.symbols.values():
            current = payload["quotes"].get(symbol)
            candidates = histories[symbol] + ([current] if current else [])
            candidates = [q for q in candidates if fresh(timestamp(q['quote_timestamp']), timestamp(q['received_at']), now)]
            observed = primary.get(symbol)
            if current and observed and observed.get('quote_timestamp') and candidates:
                target = timestamp(observed['quote_timestamp'])
                reference = min(candidates, key=lambda q:(
                    abs((timestamp(q['quote_timestamp'])-target).total_seconds()),
                    -timestamp(q['received_at']).timestamp()))
            else:
                reference = current
            comparison = compare_reference(reference, observed, now)
            comparisons[symbol] = {**comparison, "checked_at": now.isoformat()}
        payload["comparisons"] = comparisons
        # This write also detects loss of the dedicated advisory-lock session.
        # A failed write escapes to _run, which closes the socket and drops ownership.
        write_state(conn, payload, now)
        if time.monotonic() - self.last_log >= 30:
            self.last_log = time.monotonic()
            log.info("KRAKEN REFERENCE HEALTH | %s", json.dumps(payload, sort_keys=True))

    def _session(self, conn):
        ws = self.socket_factory(URL, timeout=8, enable_multithread=True)
        self.socket = ws
        try:
            self.state.opened(utc_now())
            self.clear_references()
            self.publish(conn)  # Replace any previous owner's live-looking row immediately.
            ws.send(json.dumps({"method": "subscribe", "params": {
                "channel": "book", "symbol": list(self.symbols), "depth": 10, "snapshot": True}, "req_id": 1}))
            ws.settimeout(1)
            opened = last_wire = last_ping = last_publish = time.monotonic()
            while not self.stop_event.is_set():
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    raw = None
                now_mono = time.monotonic()
                if raw == "" or raw == b"":
                    raise BookIntegrityError("disconnected")
                if raw is not None:
                    self.state.ingest(raw, utc_now())
                    self.remember_references(utc_now())
                    last_wire = now_mono
                if now_mono - last_wire > 15:
                    raise BookIntegrityError("liveness_timeout")
                if now_mono - opened > 15 and set(self.state.books) != set(self.symbols):
                    raise BookIntegrityError("snapshot_timeout")
                # Heartbeats/pongs never refresh a book's exchange/receipt timestamps.
                if any(self.state.quote(symbol, utc_now()) is None for symbol in self.state.books):
                    raise BookIntegrityError("stale_book")
                if now_mono - last_ping >= 5:
                    ws.send(json.dumps({"method": "ping", "req_id": 2}))
                    last_ping = now_mono
                if now_mono - last_publish >= 5:
                    self.publish(conn)
                    last_publish = now_mono
        finally:
            self.state.invalidate()
            self.clear_references()
            ws.close()
            self.socket = None

    def restrict_base_coverage(self):
        """A rejected expansion must not indefinitely disable BTC/ETH."""
        counts = self.state.counts.copy()
        self.symbols = dict(SYMBOLS)
        self.state = ReferenceBooks(self.symbols)
        self.state.counts.update(counts)
        self.reference_history = {s:deque(maxlen=128) for s in self.symbols.values()}

    def _run(self):
        attempt = 0
        while not self.stop_event.is_set():
            try:
                with self.connect_factory() as conn:
                    conn.autocommit = True
                    if not claim_owner(conn):
                        self.stop_event.wait(10)
                        continue
                    conn.execute("SET statement_timeout='3s'")
                    conn.execute(STATE_DDL)
                    if self.research_symbols is not None:
                        from kraken_research_data import PublicResearchData, select_instruments
                        try:
                            metadata = PublicResearchData().instruments()
                            self.symbols = select_instruments(metadata, self.research_symbols)
                        except Exception as exc:
                            log.warning('KRAKEN RESEARCH COVERAGE | status=DISCOVERY_UNAVAILABLE | reason=%s', type(exc).__name__)
                        self.state = ReferenceBooks(self.symbols)
                        self.reference_history = {s:deque(maxlen=128) for s in self.symbols.values()}
                    while not self.stop_event.is_set():
                        started = time.monotonic()
                        initial_updates = self.state.counts["updates"]
                        try:
                            self._session(conn)
                        except Exception as exc:
                            # No raw network payloads/URLs/credentials in error logs.
                            reason = str(exc) if isinstance(exc, BookIntegrityError) else type(exc).__name__
                            self.state.invalidate(reason)
                            self.state.counts["connection_failures"] += 1
                            if reason == 'subscription_error' and len(self.symbols) > 2:
                                self.restrict_base_coverage()
                                log.warning('KRAKEN RESEARCH COVERAGE | status=EXPANSION_REJECTED | retained=BTC/USD,ETH/USD')
                            log.warning("KRAKEN REFERENCE | status=%s | execution_authority=NONE", reason)
                        self.publish(conn)
                        if self.stop_event.is_set():
                            break
                        # Reset only after sustained valid books, not merely a TCP open.
                        if time.monotonic() - started >= 60 and self.state.counts["updates"] > initial_updates:
                            attempt = 0
                        delay = reconnect_delay(attempt)
                        attempt += 1
                        self.state.counts["reconnects"] += 1
                        self.stop_event.wait(delay)
                    conn.execute("SELECT pg_advisory_unlock(%s)", (OWNER_LOCK,))
            except Exception as exc:
                self.state.invalidate("STORAGE_UNAVAILABLE")
                log.warning("KRAKEN REFERENCE | status=STORAGE_UNAVAILABLE | reason=%s", type(exc).__name__)
                self.stop_event.wait(reconnect_delay(attempt))
                attempt += 1


def install_kraken_reference(worker):
    """Explicit opt-in, paper-only startup. Observe payloads without altering them."""
    if (os.getenv("KRAKEN_REFERENCE_WS_ENABLED", "false").strip().lower() != "true"
            or os.getenv("EXECUTION_MODE", "paper").strip().lower() != "paper"
            or any(os.getenv(k, "false").strip().lower() == "true"
                   for k in ("ENABLE_BROKER_SUBMISSION", "LIVE_TRADING_ARMED"))
            or not os.getenv("DATABASE_URL", "").strip()
            or getattr(worker, "_kraken_reference_service", None) is not None):
        return False
    from kraken_primary_research import enabled
    research_symbols = list(getattr(worker, 'WATCHLISTS', {}).get('crypto', {})) if enabled() else None
    service = KrakenReferenceService()
    service.research_symbols = research_symbols
    provider = getattr(worker, '_robinhood_current_marketdata_provider', None)
    client = getattr(provider, 'client', None)
    original = getattr(client, 'best_bid_ask_quotes', None)
    if not callable(original):
        log.warning('KRAKEN REFERENCE | status=PRIMARY_OBSERVER_UNAVAILABLE | execution_authority=NONE')
        return False

    def observed(*symbols, **kwargs):
        try:
            records = original(*symbols, **kwargs)
        except Exception:
            try:
                service.observe_response(symbols, None, utc_now())
            except Exception:
                pass
            raise
        try:
            service.observe_response(symbols, records, utc_now())
        except Exception:
            pass  # Diagnostic failures cannot alter quote or execution behavior.
        return records

    service.start()
    client.best_bid_ask_quotes = observed
    worker._kraken_reference_service = service
    return True
