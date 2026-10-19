"""Fail-closed Coinbase public-depth *paper simulation*; not wired to trading.

Unauthenticated REST book has no exchange timestamp: pair it with a fresh
public ticker, require bounded fetch time, and keep Kraken research separate.
"""
from __future__ import annotations
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
import re

PROVIDER = "Coinbase Exchange public depth"
VERSION = "coinbase-public-paper-v1"
TAKER_FEE_ASSUMPTION = Decimal("0.006")
SLIPPAGE_BPS_ASSUMPTION = Decimal("5")


class BookRejected(ValueError):
    pass


def number(value, *, positive=False):
    if type(value) not in (str, int, Decimal) or len(str(value)) > 60:
        raise BookRejected("INVALID_NUMBER")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise BookRejected("INVALID_NUMBER") from exc
    if not result.is_finite() or result.adjusted() > 20 or result < 0 or (positive and result == 0):
        raise BookRejected("INVALID_NUMBER")
    return result


def utc(value):
    if isinstance(value, str) and len(value) <= 64:
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise BookRejected("INVALID_TIME") from exc
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise BookRejected("INVALID_TIME")
    return value.astimezone(timezone.utc)


def levels(raw, *, bids):
    if not isinstance(raw, list) or not 1 <= len(raw) <= 50:
        raise BookRejected("INVALID_DEPTH")
    output, previous = [], None
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            raise BookRejected("INVALID_LEVEL")
        price, qty = number(item[0], positive=True), number(item[1], positive=True)
        if previous is not None and (price >= previous if bids else price <= previous):
            raise BookRejected("UNSORTED_BOOK")
        output.append([str(price), str(qty)])
        previous = price
    return output


def _digest(book):
    encoded = json.dumps(book, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def validate_snapshot(symbol, book, ticker, *, requested_at, received_at, now):
    pair = str(symbol).upper().strip()
    if not re.fullmatch(r"[A-Z0-9]{2,16}-USD", pair):
        raise BookRejected("INVALID_SYMBOL")
    if not isinstance(book, dict) or not isinstance(ticker, dict):
        raise BookRejected("INVALID_PAYLOAD")
    if any(obj.get("auction_mode") is True for obj in (book, ticker)):
        raise BookRejected("AUCTION_MODE")
    if book.get("product_id", pair) != pair or ticker.get("product_id", pair) != pair:
        raise BookRejected("SYMBOL_MISMATCH")
    sequence = book.get("sequence")
    if type(sequence) is not int or sequence < 0:
        raise BookRejected("INVALID_SEQUENCE")
    start, received, current = utc(requested_at), utc(received_at), utc(now)
    exchange = utc(ticker.get("time"))
    if not 0 <= (received - start).total_seconds() <= 3:
        raise BookRejected("FETCH_LATENCY")
    if not 0 <= (current - received).total_seconds() <= 10:
        raise BookRejected("STALE_RECEIPT")
    if not -1 <= (current - exchange).total_seconds() <= 10:
        raise BookRejected("STALE_TICKER")
    if (exchange - received).total_seconds() > 1:
        raise BookRejected("TICKER_AFTER_BOOK")
    bid_levels = levels(book.get("bids"), bids=True)
    ask_levels = levels(book.get("asks"), bids=False)
    bid, ask = number(bid_levels[0][0]), number(ask_levels[0][0])
    if bid >= ask:
        raise BookRejected("CROSSED_OR_LOCKED")
    ticker_bid, ticker_ask = number(ticker.get("bid"), positive=True), number(ticker.get("ask"), positive=True)
    if ticker_bid >= ticker_ask:
        raise BookRejected("TICKER_CROSSED")
    with localcontext() as ctx:
        ctx.prec = 50
        midpoint = (bid + ask) / 2
        if (ask - bid) / midpoint * 10000 > Decimal("150"):
            raise BookRejected("WIDE_SPREAD")
        if any(abs(a-b)/midpoint * 10000 > Decimal("10")
               for a,b in ((bid,ticker_bid),(ask,ticker_ask))):
            raise BookRejected("MISALIGNED_TICKER")
    evidence = {"symbol":pair,"provider":PROVIDER,"sequence":sequence,
                "ticker_time":exchange.isoformat(),"requested_at":start.isoformat(),
                "received_at":received.isoformat(),"bids":bid_levels,"asks":ask_levels}
    return {"book":evidence,"sha256":_digest(evidence),"execution_authority":"NONE",
            "broker_submission":"NONE","reference_only":True}


def price_paper_order(snapshot, *, side, quantity, fee_rate=TAKER_FEE_ASSUMPTION,
                      slippage_bps=SLIPPAGE_BPS_ASSUMPTION):
    if side not in ("BUY", "SELL"):
        raise BookRejected("INVALID_SIDE")
    qty, rate, slip = number(quantity, positive=True), number(fee_rate), number(slippage_bps)
    if rate > Decimal("0.05") or slip > Decimal("500"):
        raise BookRejected("COST_ASSUMPTION_OUT_OF_BOUNDS")
    book = snapshot.get("book")
    if (not isinstance(book, dict) or snapshot.get("sha256") != _digest(book)
            or book.get("provider") != PROVIDER or snapshot.get("execution_authority") != "NONE"):
        raise BookRejected("UNTRUSTED_RECEIPT")
    remaining, gross = qty, Decimal(0)
    with localcontext() as ctx:
        ctx.prec = 50
        for p, q in book["asks" if side == "BUY" else "bids"]:
            take = min(number(q, positive=True), remaining)
            gross += take * number(p, positive=True)
            remaining -= take
            if remaining == 0:
                break
        if remaining:
            raise BookRejected("INSUFFICIENT_DEPTH")
        vwap = gross / qty
        fill = vwap * (1 + (slip / 10000) * (1 if side == "BUY" else -1))
        if fill <= 0:
            raise BookRejected("INVALID_FILL")
        notional = qty * fill
        fee = notional * rate
    return {"version":VERSION,"provider":PROVIDER,"symbol":book["symbol"],"side":side,
            "quantity":str(qty),"fill_price":str(fill),"vwap":str(vwap),
            "fee_amount":str(fee),"notional":str(notional),
            "fee_rate_assumption":str(rate),"slippage_bps_assumption":str(slip),
            "book":book,"sha256":snapshot["sha256"],"execution_authority":"NONE",
            "execution_impact":"NONE","broker_submission":"NONE"}


def verify_receipt(receipt, *, symbol, side, quantity, fill_price, fee_amount):
    if not isinstance(receipt, dict) or receipt.get("version") != VERSION:
        raise BookRejected("INVALID_RECEIPT")
    expected = price_paper_order(
        {"book":receipt.get("book"),"sha256":receipt.get("sha256"),
         "execution_authority":"NONE"},
        side=side,quantity=quantity,fee_rate=receipt.get("fee_rate_assumption"),
        slippage_bps=receipt.get("slippage_bps_assumption"))
    if any(receipt.get(k) != expected[k] for k in expected):
        raise BookRejected("RECEIPT_TAMPERED")
    if receipt.get("symbol") != str(symbol).upper().strip():
        raise BookRejected("SYMBOL_MISMATCH")
    if abs(number(fill_price, positive=True)-number(expected["fill_price"])) > Decimal("0.00000001"):
        raise BookRejected("FILL_PRICE_MISMATCH")
    if abs(number(fee_amount)-number(expected["fee_amount"])) > Decimal("0.00000001"):
        raise BookRejected("FEE_MISMATCH")


def fetch_public_snapshot(symbol, *, get=None, clock=None):
    """Two public GET requests; no credentials, broker calls, or execution writes."""
    if get is None:
        import requests
        get = requests.get
    if clock is None:
        clock = lambda: datetime.now(timezone.utc)
    pair = str(symbol).upper().strip()
    if not re.fullmatch(r"[A-Z0-9]{2,16}-USD", pair):
        raise BookRejected("INVALID_SYMBOL")
    start = clock()
    def read(endpoint):
        try:
            response = get("https://api.exchange.coinbase.com/products/"+pair+"/"+endpoint,
                           timeout=3,headers={"User-Agent":"GARIBALDI-MARKET-ORACLE/1.0",
                                              "Cache-Control":"no-cache"})
            response.raise_for_status()
            payload = response.json(parse_float=Decimal)
            if not isinstance(payload, dict):
                raise BookRejected("INVALID_PAYLOAD")
            return payload
        except BookRejected:
            raise
        except Exception as exc:
            raise BookRejected("COINBASE_UNAVAILABLE") from exc
    book = read("book?level=2")
    ticker = read("ticker")
    return validate_snapshot(pair,book,ticker,requested_at=start,received_at=clock(),now=clock())
