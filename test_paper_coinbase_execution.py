"""Offline Coinbase depth tests: never hit a trading endpoint or create fills."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from paper_coinbase_execution import (
    BookRejected, fetch_public_snapshot, price_paper_order,
    validate_snapshot, verify_receipt,
)

NOW = datetime(2026, 10, 19, 13, tzinfo=timezone.utc)


def fixture():
    return (
        {"product_id":"BTC-USD","sequence":123,
         "bids":[["99.9","2",1],["99.8","3",2]],
         "asks":[["100.1","2",1],["100.2","3",2]]},
        {"product_id":"BTC-USD","bid":"99.9","ask":"100.1",
         "time":(NOW-timedelta(milliseconds=200)).isoformat()},
    )


def snapshot():
    book,ticker=fixture()
    return validate_snapshot("BTC-USD",book,ticker,
        requested_at=NOW-timedelta(seconds=1),received_at=NOW,now=NOW)


def test_buy_depth_vwap_fee_receipt_and_no_authority():
    receipt=price_paper_order(snapshot(),side="BUY",quantity="3",
                              slippage_bps="10",fee_rate="0.006")
    assert Decimal("100.1") < Decimal(receipt["vwap"]) < Decimal("100.2")
    assert Decimal(receipt["fill_price"]) > Decimal(receipt["vwap"])
    assert abs(Decimal(receipt["fee_amount"])-Decimal(receipt["notional"])*Decimal("0.006")) < Decimal("1e-30")
    assert receipt["execution_authority"] == "NONE"
    assert receipt["broker_submission"] == "NONE"
    assert receipt["execution_impact"] == "NONE"
    verify_receipt(receipt,symbol="BTC-USD",side="BUY",quantity="3",
                   fill_price=receipt["fill_price"],fee_amount=receipt["fee_amount"])


def test_sell_uses_bid_depth_and_adverse_slippage():
    receipt=price_paper_order(snapshot(),side="SELL",quantity="3")
    assert Decimal("99.8") < Decimal(receipt["vwap"]) < Decimal("99.9")
    assert Decimal(receipt["fill_price"]) < Decimal(receipt["vwap"])


@pytest.mark.parametrize("action,reason",[
    (lambda b,t:b.update(auction_mode=True),"AUCTION_MODE"),
    (lambda b,t:b.update(sequence=None),"INVALID_SEQUENCE"),
    (lambda b,t:b["bids"].__setitem__(0,["102","2",1]),"CROSSED_OR_LOCKED"),
    (lambda b,t:b["asks"].__setitem__(0,["99.9","2",1]),"CROSSED_OR_LOCKED"),
    (lambda b,t:b["asks"].__setitem__(0,["110","2",1]),"WIDE_SPREAD"),
    (lambda b,t:b["asks"].__setitem__(1,["100.05","3",1]),"UNSORTED_BOOK"),
    (lambda b,t:t.update(bid="95"),"MISALIGNED_TICKER"),
    (lambda b,t:t.update(time=(NOW-timedelta(minutes=1)).isoformat()),"STALE_TICKER"),
    (lambda b,t:b.update(product_id="ETH-USD"),"SYMBOL_MISMATCH"),
])
def test_bad_book_rejected(action,reason):
    book,ticker=fixture()
    action(book,ticker)
    with pytest.raises(BookRejected,match=reason):
        validate_snapshot("BTC-USD",book,ticker,
            requested_at=NOW-timedelta(seconds=1),received_at=NOW,now=NOW)


def test_missing_depth_untrusted_book_and_receipt_tampering():
    snap=snapshot()
    with pytest.raises(BookRejected,match="INSUFFICIENT_DEPTH"):
        price_paper_order(snap,side="BUY",quantity="6")
    tampered=deepcopy(snap)
    tampered["book"]["asks"][0][1]="100"
    with pytest.raises(BookRejected,match="UNTRUSTED_RECEIPT"):
        price_paper_order(tampered,side="BUY",quantity="3")
    receipt=price_paper_order(snap,side="BUY",quantity="1")
    bad=deepcopy(receipt)
    bad["fee_amount"]="0"
    with pytest.raises(BookRejected,match="RECEIPT_TAMPERED"):
        verify_receipt(bad,symbol="BTC-USD",side="BUY",quantity="1",
                       fill_price=receipt["fill_price"],fee_amount="0")
    with pytest.raises(BookRejected,match="FILL_PRICE_MISMATCH"):
        verify_receipt(receipt,symbol="BTC-USD",side="BUY",quantity="1",
                       fill_price="90",fee_amount=receipt["fee_amount"])


def test_stale_missing_time_and_slow_fetch_rejected():
    book,ticker=fixture()
    ticker["time"]=None
    with pytest.raises(BookRejected,match="INVALID_TIME"):
        validate_snapshot("BTC-USD",book,ticker,
            requested_at=NOW-timedelta(seconds=1),received_at=NOW,now=NOW)
    ticker["time"]=NOW.isoformat()
    with pytest.raises(BookRejected,match="FETCH_LATENCY"):
        validate_snapshot("BTC-USD",book,ticker,
            requested_at=NOW-timedelta(seconds=4),received_at=NOW,now=NOW)


def test_public_gets_are_read_only_and_have_no_credentials():
    book,ticker=fixture()
    seen=[]
    class Response:
        def __init__(self,payload):
            self.payload=payload
        def raise_for_status(self):
            pass
        def json(self,**kwargs):
            return self.payload
    def get(url,**kwargs):
        seen.append((url,kwargs))
        return Response(book if "/book?" in url else ticker)
    result=fetch_public_snapshot("BTC-USD",get=get,clock=lambda:NOW)
    assert result["execution_authority"]=="NONE"
    assert len(seen)==2
    assert seen[0][0].endswith("/book?level=2")
    assert seen[1][0].endswith("/ticker")
    assert all("Authorization" not in kw["headers"] for _,kw in seen)
