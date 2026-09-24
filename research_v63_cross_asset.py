from __future__ import annotations

import json
import math
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import requests

URL = "https://data-api.binance.vision/api/v3/klines"
SYMBOLS = [
    "BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","DOGEUSDT","ADAUSDT",
    "AVAXUSDT","LINKUSDT","LTCUSDT","BCHUSDT","AAVEUSDT","UNIUSDT",
]
INTERVAL_MS = 300_000
DAYS = 100
H = 3
HOLDOUT_DAYS = 14

FEATURES = ("r3","r6","r12","r24","ofi3","ofi6","ofi12","ofi24","flow_delta","cloc","resid3","resid6","resid12")
QUANTILES = (0.45,0.55,0.65,0.75,0.82,0.88,0.92)

def fetch(symbol):
    end=int(time.time()*1000); cur=end-DAYS*86400000; rows=[]
    while cur<end:
        r=requests.get(URL,params={"symbol":symbol,"interval":"5m","startTime":cur,"endTime":end,"limit":1000},timeout=20)
        r.raise_for_status(); batch=r.json()
        if not batch: break
        rows += batch
        nxt=int(batch[-1][0])+INTERVAL_MS
        if nxt<=cur: break
        cur=nxt
        if len(batch)<1000: break
    d=pd.DataFrame(rows,columns=["ot","o","h","l","c","v","ct","qv","n","tb","tbq","x"]).drop_duplicates("ot").sort_values("ot")
    for z in ["o","h","l","c","qv","n","tbq"]: d[z]=pd.to_numeric(d[z],errors="coerce")
    d.index=pd.to_datetime(d.ct,unit="ms",utc=True)+pd.Timedelta(milliseconds=1)
    return d

def frame(own,btc):
    idx=own.index.intersection(btc.index); own=own.reindex(idx); btc=btc.reindex(idx)
    lr=np.log(own.c); br=np.log(btc.c); signed=2*own.tbq-own.qv
    X=pd.DataFrame(index=idx)
    for k in (3,6,12,24): X[f"r{k}"]=lr.diff(k)
    for k in (3,6,12,24): X[f"ofi{k}"]=signed.rolling(k).sum()/(own.qv.rolling(k).sum()+1e-12)
    X["flow_delta"]=X.ofi3-X.ofi12
    X["cloc"]=(own.c-own.l)/(own.h-own.l+1e-12)-.5
    X["resid3"]=X.r3-br.diff(3); X["resid6"]=X.r6-br.diff(6); X["resid12"]=X.r12-br.diff(12)
    X["y"]=(lr.shift(-H)>lr).astype(int)
    X["prev"]=(lr.diff(H)>0).astype(int); X["mom"]=(lr.diff(12)>0).astype(int)
    # independent 15-minute decisions
    return X.iloc[::H].replace([np.inf,-np.inf],np.nan).dropna()

def ece(p,y):
    if len(y)==0:return 1.
    out=0.
    for i in range(10):
        lo=i/10; hi=(i+1)/10; m=(p>=lo)&(p<(hi if i<9 else 1.0001))
        if m.any(): out+=m.mean()*abs(float(p[m].mean())-float(y[m].mean()))
    return float(out)

def metrics(p,y,prev,mom,base):
    p=np.asarray(p,float); y=np.asarray(y,float)
    if len(y)==0:return {"n":0,"accuracy":0.,"brier_skill":-math.inf,"ece":1.,"beats_all_baselines":False}
    b=float(np.mean((p-y)**2)); clim=float(np.mean((y.mean()-y)**2))
    bsets=[
        float(np.mean((.5-y)**2)),
        float(np.mean((np.full(len(y),base)-y)**2)),
        float(np.mean((np.where(prev>0,.60,.40)-y)**2)),
        float(np.mean((np.where(mom>0,.65,.35)-y)**2)),
    ]
    return {"n":int(len(y)),"accuracy":float(np.mean((p>=.5)==y)),"brier":b,"climatology_brier":clim,
            "brier_skill":float(1-b/clim) if clim>1e-12 else -math.inf,"ece":ece(p,y),
            "beats_all_baselines":bool(all(b<x for x in bsets)),"baseline_briers":bsets}

def apply_rule(d,feature,thr,polarity,q,base):
    vals=d[feature].to_numpy(float); mask=np.abs(vals)>=thr
    direction=vals>=0
    if polarity<0: direction=~direction
    p=np.where(direction[mask],q,1-q); y=d.y.to_numpy(float)[mask]
    return metrics(p,y,d.prev.to_numpy()[mask],d.mom.to_numpy()[mask],base), int(mask.sum())

def choose(train,val1,val2):
    base=float(train.y.mean()); cands=[]
    for feature in FEATURES:
        tv=np.abs(train[feature].to_numpy(float))
        for quantile in QUANTILES:
            thr=float(np.quantile(tv,quantile))
            if not math.isfinite(thr) or thr<=0:continue
            for polarity in (1,-1):
                vals=train[feature].to_numpy(float); mask=np.abs(vals)>=thr; direction=vals>=0
                if polarity<0:direction=~direction
                n=int(mask.sum())
                if n<120:continue
                correct=int(np.sum(direction[mask]==(train.y.to_numpy()[mask]>0)))
                q=min(.70,max(.505,(correct+24)/(n+48)))
                a,n1=apply_rule(val1,feature,thr,polarity,q,base); b,n2=apply_rule(val2,feature,thr,polarity,q,base)
                cov1=n1/max(1,len(val1)); cov2=n2/max(1,len(val2))
                if n1<30 or n2<30 or not(.04<=cov1<=.65 and .04<=cov2<=.65):continue
                if not(a["beats_all_baselines"] and b["beats_all_baselines"]):continue
                minskill=min(a["brier_skill"],b["brier_skill"]); minacc=min(a["accuracy"],b["accuracy"])
                score=minskill+.2*(a["brier_skill"]+b["brier_skill"])+.01*minacc
                cands.append((score,minskill,minacc,feature,thr,polarity,q,base,a,b))
    if not cands:return None
    return max(cands,key=lambda z:(z[0],z[1],z[2]))

def evaluate_symbol(symbol,X):
    latest=X.index.max().floor("1D")
    hold_start=latest-pd.Timedelta(days=HOLDOUT_DAYS)
    pre_start=hold_start-pd.Timedelta(days=21)
    v2_start=pre_start-pd.Timedelta(days=10)
    v1_start=v2_start-pd.Timedelta(days=10)
    train_start=v1_start-pd.Timedelta(days=40)
    train=X[(X.index>=train_start)&(X.index<v1_start-pd.Timedelta(minutes=15))]
    v1=X[(X.index>=v1_start)&(X.index<v2_start-pd.Timedelta(minutes=15))]
    v2=X[(X.index>=v2_start)&(X.index<pre_start-pd.Timedelta(minutes=15))]
    pre=X[(X.index>=pre_start)&(X.index<hold_start-pd.Timedelta(minutes=15))]
    if min(len(train),len(v1),len(v2),len(pre))<100:return {"symbol":symbol,"status":"INSUFFICIENT"}
    chosen=choose(train,v1,v2)
    if chosen is None:return {"symbol":symbol,"status":"NO_RULE"}
    _,minskill,minacc,feature,thr,polarity,q,base,a,b=chosen
    m,n=apply_rule(pre,feature,thr,polarity,q,base)
    passed=bool(n>=100 and m["accuracy"]>=.52 and m["brier_skill"]>=.02 and m["ece"]<=.12 and m["beats_all_baselines"])
    return {"symbol":symbol,"status":"PASS" if passed else "FAIL","rule":{"feature":feature,"threshold":thr,"polarity":polarity,"probability":q},
            "inner_min_skill":minskill,"inner_min_accuracy":minacc,"v1":a,"v2":b,"preholdout":m}

def main():
    with ThreadPoolExecutor(max_workers=6) as ex: data=dict(zip(SYMBOLS,ex.map(fetch,SYMBOLS)))
    print("V67_RAW",json.dumps({k:len(v) for k,v in data.items()}),flush=True)
    btc=data["BTCUSDT"]; results={}
    for s in SYMBOLS:
        try: results[s]=evaluate_symbol(s,frame(data[s],btc))
        except Exception as exc: results[s]={"symbol":s,"status":"ERROR","reason":exc.__class__.__name__}
        print("V67_SYMBOL",json.dumps(results[s],default=str),flush=True)
    passing=[s for s,x in results.items() if x.get("status")=="PASS"]
    print("V67_PREHOLDOUT",json.dumps({"passing_symbols":passing,"count":len(passing),"results":results},default=str),flush=True)
    print("V67_CAN_ADVANCE_TO_HOLDOUT",len(passing)>=3,flush=True)

if __name__=="__main__":main()
