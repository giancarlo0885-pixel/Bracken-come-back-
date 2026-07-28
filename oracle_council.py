from __future__ import annotations
from dataclasses import asdict, dataclass
from typing import Any

@dataclass
class CouncilVote:
    specialist: str; score: float; weight: float; reason: str

def _v(s:Any,n:str,d=0.0): return s.get(n,d) if isinstance(s,dict) else getattr(s,n,d)
def _f(v,d=0.0):
    try: return float(v)
    except (TypeError,ValueError): return d
def _clip(v,a=0,b=1): return max(a,min(b,v))
def _norm(v):
    x=_f(v,.5); return _clip(x/100 if x>1 else x)

def deliberate(signal: Any, news_headlines: list[str] | None=None) -> dict[str,Any]:
    news_headlines=news_headlines or []
    base=_norm(_v(signal,"score",.5)); action=str(_v(signal,"action","HOLD")).upper()
    rsi=_f(_v(signal,"rsi_14",50),50); m5=_f(_v(signal,"momentum_5d",0)); m20=_f(_v(signal,"momentum_20d",0))
    vol=max(0,_f(_v(signal,"volatility_20d",0))); trend=_f(_v(signal,"trend_strength",0)); news=_f(_v(signal,"news_sentiment",0)); volume=max(0,_f(_v(signal,"volume_ratio",1),1)); regime=str(_v(signal,"regime","neutral")).lower()
    votes=[
      CouncilVote("Chief Market Strategist",_clip(base+(.06 if action=="BUY" else -.06 if action=="SELL" else 0)),1.35,"Integrates the full engine signal."),
      CouncilVote("Trend & Momentum Specialist",_clip(.5+trend*2.5+m20*1.2+m5*.8),1.10,"Evaluates multi-horizon price persistence."),
      CouncilVote("Dip & Mean-Reversion Specialist",_clip(.5+(35-rsi)/100+max(0,-m5)*1.2),.85,"Looks for oversold dislocations."),
      CouncilVote("Liquidity & Volume Specialist",_clip(.5+(volume-1)*.12),.75,"Measures participation and unusual volume."),
      CouncilVote("Macro Regime Specialist",_clip(.58-(.10 if regime=="risk-off" else 0)-max(0,vol-.5)*.18),.90,"Adjusts for regime and volatility."),
      CouncilVote("News Catalyst Specialist",_clip(.5+news*.30),.70 if news_headlines else .35,f"Assessed {len(news_headlines)} headlines."),
      CouncilVote("Risk & Drawdown Specialist",_clip(.82-vol*.30-(.08 if regime=="risk-off" else 0)),1.20,"Protects capital during unstable conditions."),
      CouncilVote("Opportunity Ranker",_clip(.48+base*.25+max(0,m20)*1.6+max(0,1-volume)*-.04),.95,"Compares upside quality against alternatives."),
      CouncilVote("Portfolio Rotation Specialist",_clip(.5+m20*1.3+m5*.5-vol*.12),.90,"Tests whether capital deserves to remain allocated."),
      CouncilVote("Crypto Structure Specialist",_clip(.52+m20*.9-vol*.16),.55,"Handles higher-volatility digital assets."),
      CouncilVote("Equity Quality Specialist",_clip(.54+trend*1.8-vol*.08),.55,"Favors durable equity trends."),
      CouncilVote("Devil's Advocate",_clip(.72-sum([regime=="risk-off",m5<-.04,m20<-.08,news<-.3,vol>.8])*.09),.95,"Challenges consensus and crowded assumptions."),
    ]
    weighted=sum(v.score*v.weight for v in votes)/sum(v.weight for v in votes)
    dispersion=sum(abs(v.score-weighted) for v in votes)/len(votes)
    confidence=_clip(.55+(1-dispersion)*.34+abs(weighted-.5)*.2)
    risk=next(v.score for v in votes if v.specialist=="Risk & Drawdown Specialist")
    if weighted>=.59 and risk>=.43: decision="BUY"
    elif weighted<=.41: decision="SELL"
    else: decision="HOLD"
    top=sorted(votes,key=lambda v:v.score*v.weight,reverse=True)[:3]
    return {"version":"V3","action":decision,"score":round(weighted,4),"score_100":round(weighted*100,2),"confidence":round(confidence,4),"agreement":round(1-dispersion,4),"risk_approval":round(risk,4),"votes":[asdict(v) for v in votes],"explanation":"Oracle Council V3: "+"; ".join(f"{v.specialist} {v.score:.0%}" for v in top)}
