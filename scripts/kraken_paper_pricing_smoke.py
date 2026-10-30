"""Bounded public connectivity/pricing validation; no DB, credentials or orders."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import requests
import websocket
from kraken_reference_book import ReferenceBooks, SYMBOLS
from kraken_reference_runtime import URL
from kraken_paper_pricing import validate_rules, validate_kraken_book
from coinbase_paper_pricing import depth_price


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds',type=int,default=20,choices=range(5,61))
    args=parser.parse_args();state=ReferenceBooks();ws=None
    try:
        response=requests.get('https://api.kraken.com/0/public/AssetPairs',
            params={'pair':'BTC/USD,ETH/USD','assetVersion':1},timeout=8)
        response.raise_for_status();payload=response.json()
        if payload.get('error'):raise ValueError('Kraken product metadata error')
        metadata=payload['result'];metadata_received=datetime.now(timezone.utc).isoformat()
        ws=websocket.create_connection(URL,timeout=8)
        state.opened(datetime.now(timezone.utc))
        ws.send(json.dumps({'method':'subscribe','params':{'channel':'book','symbol':list(SYMBOLS),'depth':10,'snapshot':True},'req_id':1}))
        ws.settimeout(1);deadline=time.monotonic()+args.seconds;quotes={};validated={s:0 for s in SYMBOLS.values()}
        while time.monotonic()<deadline:
            try:raw=ws.recv()
            except websocket.WebSocketTimeoutException:continue
            now=datetime.now(timezone.utc);state.ingest(raw,now)
            for pair,symbol in SYMBOLS.items():
                reference=state.quote(pair,now,include_depth=True)
                if not reference:continue
                quote=validate_kraken_book(symbol,reference,validate_rules(symbol,metadata),metadata_received,now)
                # Calculation only, not a fill/order or accounting record.
                notional=max(25,float(quote['min_order_notional'])*2)
                quotes[symbol]={'provider':quote['provider'],'quote_timestamp':quote['quote_timestamp'],
                    'checksum_status':quote['checksum_status'],'bid':quote['bid'],'ask':quote['ask'],
                    'buy_depth_vwap':str(depth_price(quote,'BUY',notional)),
                    'sell_depth_vwap':str(depth_price(quote,'SELL',notional)),
                    'execution_authority':quote['execution_authority'],'fee_account_verified':False}
                validated[symbol]+=1
        health=state.health(datetime.now(timezone.utc))
        passed=len(health['quotes'])==2 and all(validated.values()) and state.counts['updates']>0
        print(json.dumps({'actual_public_connectivity':True,'passed':passed,'validated':validated,
            'counts':health['counts'],'quotes':quotes,'orders_submitted':0,'database_writes':0},sort_keys=True))
        return 0 if passed else 1
    except Exception as exc:
        print(json.dumps({'actual_public_connectivity':True,'passed':False,'error_type':type(exc).__name__,
            'counts':dict(state.counts),'orders_submitted':0,'database_writes':0}))
        return 1
    finally:
        state.invalidate()
        if ws:ws.close()


if __name__=='__main__':raise SystemExit(main())
