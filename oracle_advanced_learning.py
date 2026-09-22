from __future__ import annotations

"""Advanced research-only learning diagnostics for Oracle.

Adds point-in-time decision replay, setup attribution, loss taxonomy, adaptive
evidence weighting, exit diagnostics, portfolio concentration, data-quality
confidence, lineage, and walk-forward champion/challenger evidence. It cannot
approve, size, modify, or submit trades.
"""

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any


def _f(v: Any, default: float = 0.0) -> float:
    try: x=float(v)
    except (TypeError,ValueError): return default
    return x if math.isfinite(x) else default


def _obj(v: Any) -> dict[str,Any]:
    if isinstance(v,dict): return v
    if isinstance(v,str):
        try:
            x=json.loads(v); return x if isinstance(x,dict) else {}
        except Exception: return {}
    return {}


def _deep(p: dict[str,Any], keys: tuple[str,...]) -> float|None:
    stack=[p]
    while stack:
        x=stack.pop()
        if not isinstance(x,dict): continue
        for k in keys:
            if x.get(k) is not None:
                y=_f(x.get(k),float("nan"))
                if math.isfinite(y): return y
        stack.extend(v for v in x.values() if isinstance(v,dict))
    return None


def build_decision_replays(conn: Any, *, limit: int=200) -> dict[str,int]:
    decisions=list(conn.execute(
        """SELECT id,market,symbol,approved,payload,created_at FROM oracle_decision_audit d
           WHERE NOT EXISTS(SELECT 1 FROM oracle_decision_replays r WHERE r.decision_id=d.id)
           ORDER BY created_at ASC LIMIT %s""",(max(1,int(limit)),)
    ).fetchall())
    written=0
    for d in decisions:
        observations=list(conn.execute(
            """SELECT event_key,source_table,observation_type,event_time,payload
               FROM oracle_brain_observations
               WHERE event_time::timestamptz <= %s::timestamptz
                 AND market IN (%s,'global') AND (symbol=%s OR symbol IS NULL)
               ORDER BY event_time::timestamptz DESC,id DESC LIMIT 100""",
            (d.get("created_at"),d.get("market"),d.get("symbol"))
        ).fetchall())
        snapshot={"decision_payload":_obj(d.get("payload")),"approved":bool(d.get("approved")),
                  "observations":[dict(x) for x in observations]}
        raw=json.dumps(snapshot,default=str,sort_keys=True,separators=(",",":"))
        digest=hashlib.sha256(raw.encode()).hexdigest()
        conn.execute(
            """INSERT INTO oracle_decision_replays(
                 decision_id,market,symbol,decision_time,observation_count,replay_snapshot,lineage_hash,execution_impact
               ) VALUES(%s,%s,%s,%s,%s,%s::jsonb,%s,'NONE') ON CONFLICT(decision_id) DO NOTHING""",
            (d.get("id"),d.get("market"),d.get("symbol"),d.get("created_at"),len(observations),raw,digest))
        written+=1
    return {"replays":written}


def refresh_setup_attribution(conn: Any) -> dict[str,int]:
    rows=list(conn.execute(
        """SELECT market,strategy,regime,COUNT(*)::int samples,
          SUM(CASE WHEN net_pnl>0 THEN 1 ELSE 0 END)::int wins,
          SUM(CASE WHEN net_pnl<0 THEN 1 ELSE 0 END)::int losses,
          AVG(net_pnl) expectancy,
          SUM(CASE WHEN net_pnl>0 THEN net_pnl ELSE 0 END) gross_win,
          ABS(SUM(CASE WHEN net_pnl<0 THEN net_pnl ELSE 0 END)) gross_loss,
          AVG(mfe_pct) FILTER(WHERE mfe_pct IS NOT NULL) avg_mfe,
          AVG(mae_pct) FILTER(WHERE mae_pct IS NOT NULL) avg_mae
          FROM oracle_brain_episodes WHERE provenance_status='exact'
          GROUP BY market,strategy,regime"""
    ).fetchall())
    for x in rows:
        loss=_f(x.get("gross_loss")); pf=_f(x.get("gross_win"))/loss if loss>0 else 0.0
        key=f"{x.get('market')}:{x.get('strategy')}:{x.get('regime')}"
        conn.execute(
            """INSERT INTO oracle_setup_validation(
              cohort_key,market,strategy,regime,samples,wins,losses,expectancy,profit_factor,avg_mfe_pct,avg_mae_pct,execution_impact
            ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'NONE')
            ON CONFLICT(cohort_key) DO UPDATE SET samples=EXCLUDED.samples,wins=EXCLUDED.wins,
            losses=EXCLUDED.losses,expectancy=EXCLUDED.expectancy,profit_factor=EXCLUDED.profit_factor,
            avg_mfe_pct=EXCLUDED.avg_mfe_pct,avg_mae_pct=EXCLUDED.avg_mae_pct,updated_at=NOW()""",
            (key,x.get("market"),x.get("strategy"),x.get("regime"),x.get("samples"),x.get("wins"),x.get("losses"),
             x.get("expectancy"),pf,x.get("avg_mfe"),x.get("avg_mae")))
    return {"setup_cohorts":len(rows)}


def classify_failures(conn: Any, *, limit:int=500) -> dict[str,int]:
    rows=list(conn.execute(
        """SELECT episode_key,market,strategy,regime,net_pnl,fees,return_pct,mfe_pct,mae_pct,feature_snapshot
           FROM oracle_brain_episodes e WHERE provenance_status='exact' AND net_pnl<0
           AND NOT EXISTS(SELECT 1 FROM oracle_failure_taxonomy f WHERE f.episode_key=e.episode_key)
           ORDER BY exit_time ASC LIMIT %s""",(max(1,int(limit)),)
    ).fetchall())
    counts={}
    for x in rows:
        feat=_obj(x.get("feature_snapshot")); fees=_f(x.get("fees")); pnl=_f(x.get("net_pnl"))
        mfe=_f(x.get("mfe_pct")); mae=abs(_f(x.get("mae_pct")))
        spread=_deep(feat,("spread_pct","spread_bps")); stale=_deep(feat,("staleness_seconds","age_seconds"))
        if stale is not None and stale>60: cls="stale_data"
        elif fees>0 and abs(pnl)<=fees*1.25: cls="cost_drag"
        elif spread is not None and spread>0.5: cls="spread_expansion"
        elif mfe>0.5 and pnl<0: cls="exit_giveback"
        elif mae>1.0 and mfe<=0.1: cls="wrong_direction_or_entry"
        else: cls="unresolved_loss"
        evidence={"net_pnl":pnl,"fees":fees,"mfe_pct":mfe,"mae_pct":mae,"spread":spread,"staleness":stale}
        conn.execute(
            """INSERT INTO oracle_failure_taxonomy(
              episode_key,market,strategy,regime,failure_class,evidence,execution_impact
            ) VALUES(%s,%s,%s,%s,%s,%s::jsonb,'NONE') ON CONFLICT(episode_key) DO NOTHING""",
            (x.get("episode_key"),x.get("market"),x.get("strategy"),x.get("regime"),cls,json.dumps(evidence)))
        counts[cls]=counts.get(cls,0)+1
    return counts


def adaptive_evidence_weight(samples:int, age_days:float, drift_score:float) -> float:
    depth=min(1.0,max(0.0,samples/100.0))
    recency=math.exp(-max(0.0,age_days)/30.0)
    drift=max(0.1,1.0-min(1.0,max(0.0,drift_score)))
    return depth*recency*drift


def exit_diagnostics(conn: Any) -> dict[str,Any]:
    row=conn.execute(
        """SELECT COUNT(*)::int samples,
          AVG(CASE WHEN net_pnl<0 AND mfe_pct>0 THEN mfe_pct ELSE NULL END) avg_giveback_mfe,
          COUNT(*) FILTER(WHERE net_pnl<0 AND mfe_pct>=0.5)::int giveback_losses,
          AVG(mae_pct) FILTER(WHERE net_pnl<0) avg_loser_mae
          FROM oracle_brain_episodes WHERE provenance_status='exact'"""
    ).fetchone() or {}
    return dict(row)


def portfolio_concentration(conn: Any) -> dict[str,Any]:
    rows=list(conn.execute(
        """SELECT symbol,SUM(ABS(COALESCE(net_pnl,0))) exposure_proxy
           FROM oracle_brain_episodes WHERE provenance_status='exact'
           GROUP BY symbol ORDER BY exposure_proxy DESC"""
    ).fetchall())
    total=sum(_f(x.get("exposure_proxy")) for x in rows)
    top=sum(_f(x.get("exposure_proxy")) for x in rows[:3])
    return {"symbols":len(rows),"top3_concentration":top/total if total>0 else 0.0}


def refresh_data_quality(conn: Any) -> dict[str,Any]:
    rows=list(conn.execute(
        """SELECT market,symbol,MAX(event_time::timestamptz) newest,COUNT(*)::int samples
           FROM oracle_brain_observations GROUP BY market,symbol"""
    ).fetchall())
    now=datetime.now(timezone.utc); written=0
    for x in rows:
        newest=x.get("newest")
        age=(now-newest).total_seconds() if getattr(newest,"tzinfo",None) else 999999.0
        freshness=max(0.0,min(1.0,1.0-age/3600.0))
        quality=freshness
        key=f"{x.get('market')}:{x.get('symbol') or '*'}:{str(newest)}"
        conn.execute(
            """INSERT INTO oracle_data_quality_evidence(
              evidence_key,market,symbol,observed_at,freshness_score,agreement_score,provider_health_score,
              quality_score,evidence,execution_impact
            ) VALUES(%s,%s,%s,%s,%s,NULL,NULL,%s,%s::jsonb,'NONE') ON CONFLICT(evidence_key) DO NOTHING""",
            (key,x.get("market"),x.get("symbol"),newest,freshness,quality,json.dumps({"samples":x.get("samples")})))
        written+=1
    return {"quality_rows":written}


def walk_forward_challengers(conn: Any, market:str) -> dict[str,Any]:
    """Evaluate setup/regime cohorts on the newest 30% only; never changes authority."""
    cohorts=list(conn.execute(
        """SELECT DISTINCT strategy,regime FROM oracle_brain_episodes
           WHERE provenance_status='exact' AND market=%s""",(market,)
    ).fetchall())
    qualified=0
    for cohort in cohorts:
        rows=list(conn.execute(
            """SELECT exit_time,net_pnl FROM oracle_brain_episodes
               WHERE provenance_status='exact' AND market=%s AND strategy=%s AND regime=%s
               ORDER BY exit_time ASC""",(market,cohort.get("strategy"),cohort.get("regime"))
        ).fetchall())
        if len(rows)<30: continue
        cut=max(1,int(len(rows)*0.70)); test=rows[cut:]
        wins=sum(max(0.0,_f(x.get("net_pnl"))) for x in test)
        losses=abs(sum(min(0.0,_f(x.get("net_pnl"))) for x in test))
        exp=sum(_f(x.get("net_pnl")) for x in test)/len(test)
        pf=wins/losses if losses>0 else (999.0 if wins>0 else 0.0)
        equity=peak=dd=0.0
        for x in test:
            equity+=_f(x.get("net_pnl")); peak=max(peak,equity); dd=max(dd,peak-equity)
        state="shadow" if len(test)>=20 and exp>0 and pf>1.05 else "research"
        if state=="shadow": qualified+=1
        key=f"{cohort.get('strategy')}:{cohort.get('regime')}"
        evidence={"train_samples":cut,"test_samples":len(test),"split":"70/30_time_ordered"}
        conn.execute(
            """INSERT INTO oracle_challenger_validation(
              candidate_key,market,validation_window,train_end,test_start,samples,expectancy,profit_factor,
              max_drawdown,state,evidence,execution_impact
            ) VALUES(%s,%s,'latest_30pct',%s,%s,%s,%s,%s,%s,%s,%s::jsonb,'NONE')
            ON CONFLICT(candidate_key,market,validation_window) DO UPDATE SET
              train_end=EXCLUDED.train_end,test_start=EXCLUDED.test_start,samples=EXCLUDED.samples,
              expectancy=EXCLUDED.expectancy,profit_factor=EXCLUDED.profit_factor,
              max_drawdown=EXCLUDED.max_drawdown,state=EXCLUDED.state,evidence=EXCLUDED.evidence,updated_at=NOW()""",
            (key,market,rows[cut-1].get("exit_time"),test[0].get("exit_time"),len(test),exp,pf,dd,state,json.dumps(evidence)))
    return {"challengers":len(cohorts),"shadow_qualified":qualified,"execution_impact":"NONE"}


def sync_advanced_learning(conn:Any,market:str)->dict[str,Any]:
    return {"replay":build_decision_replays(conn),"setups":refresh_setup_attribution(conn),
            "failures":classify_failures(conn),"exit":exit_diagnostics(conn),
            "portfolio":portfolio_concentration(conn),"data_quality":refresh_data_quality(conn),
            "walk_forward":walk_forward_challengers(conn,market),"execution_impact":"NONE"}
