"""Bounded public book+OHLC smoke test. One REST bootstrap; no DB or orders."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import requests
import websocket
from kraken_reference_runtime import KrakenReferenceService,URL
from kraken_research_data import PublicResearchData


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds',type=int,default=15,choices=range(5,61))
    args=parser.parse_args()
    # These settings are local to this diagnostic process, not Railway config.
    for k,v in {'KRAKEN_OHLC_WS_ENABLED':'true','KRAKEN_REFERENCE_WS_ENABLED':'true',
        'EXECUTION_MODE':'paper','ENABLE_BROKER_SUBMISSION':'false','LIVE_TRADING_ARMED':'false'}.items():os.environ[k]=v
    service=KrakenReferenceService(connect_factory=lambda:None)
    service.symbols={'BTC/USD':'BTC-USD'};service.state.symbols=service.symbols
    ws=None;requests_used=0
    try:
        response=requests.get('https://api.kraken.com/0/public/OHLC',params={
            'pair':'BTC/USD','assetVersion':1,'interval':5},timeout=8)
        requests_used+=1;response.raise_for_status();payload=response.json()
        if payload.get('error'):raise ValueError('public bootstrap error')
        received=datetime.now(timezone.utc)
        # Exercise the production parser, with an already fetched response.
        client=PublicResearchData();client.request=lambda *_:payload['result']
        bootstrap=client.history('BTC-USD',received)
        ws=websocket.create_connection(URL,timeout=8)
        now=datetime.now(timezone.utc);service.state.opened(now);service.candles.opened(service.symbols,now)
        for channel in ('book','ohlc'):
            params={'channel':channel,'symbol':['BTC/USD'],'snapshot':True}
            params.update({'depth':10} if channel=='book' else {'interval':5})
            ws.send(json.dumps({'method':'subscribe','params':params,'req_id':1 if channel=='book' else 3}))
        ws.settimeout(1);deadline=time.monotonic()+args.seconds
        while time.monotonic()<deadline:
            try:raw=ws.recv()
            except websocket.WebSocketTimeoutException:continue
            if not raw:raise ValueError('disconnected')
            now=datetime.now(timezone.utc);service._ingest_wire(raw,now)
            if not service.candles.seeded:
                service.candles.seed('BTC-USD',bootstrap,now,received,
                    expected_session=service.candles.generation())
        frame=service.candles.history('BTC-USD',datetime.now(timezone.utc))
        passed=frame is not None and service.state.counts['updates']>0
        result={'actual_public_connectivity':True,'passed':passed,'scope':'ISOLATED_DIAGNOSTIC',
            'rest_bootstrap_requests':requests_used,'candle_health':service.candles.health(),
            'book_updates':service.state.counts['updates'],'checksum_failures':service.state.counts['checksum_failures'],
            'history_bars':len(frame) if frame is not None else None,
            'last_closed_interval':frame.index[-1].isoformat() if frame is not None else None,
            'history_quote_verified':False,'orders_submitted':0,'database_writes':0,
            'production_configuration_changed':False}
        print(json.dumps(result,sort_keys=True));return 0 if passed else 1
    except Exception as exc:
        print(json.dumps({'actual_public_connectivity':True,'passed':False,'reason_class':type(exc).__name__,
            'rest_bootstrap_requests':requests_used,'candle_health':service.candles.health(),
            'orders_submitted':0,'database_writes':0}));return 1
    finally:
        service.state.invalidate();service.candles.invalidate()
        if ws:ws.close()


if __name__=='__main__':raise SystemExit(main())
