from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from database import database_ready, rows
from oracle_brain import build_oracle_brain_snapshot
from oracle_brain_component import render_oracle_brain_component


st.set_page_config(
    page_title="Oracle Brain - GARIBALDI MARKET ORACLE",
    page_icon="ORCL",
    layout="wide",
)

health = database_ready(connect_timeout=5)
if not health.get("ok"):
    st.error("Oracle Brain cannot safely display evidence because PostgreSQL is unavailable.")
    st.caption(str(health.get("message") or "Database readiness check failed."))
    st.stop()

if st.button("Refresh Brain", type="primary"):
    st.rerun()

snapshot = build_oracle_brain_snapshot(rows)

# Authoritative AEVE research-generation progress. Count only generation-isolated
# outcomes produced by the AEVE evaluator; Council approvals/signals are not trades.
try:
    aeve_rows = rows("""
        SELECT g.generation,g.started_at,g.config_hash,g.status,
               COUNT(o.id)::int AS observed_outcomes,
               COUNT(o.id) FILTER (WHERE o.would_trade)::int AS accepted_trades,
               COUNT(o.id) FILTER (
                   WHERE o.would_trade AND o.provenance_version >= 2
                     AND o.config_hash = g.config_hash
               )::int AS verified_trades,
               COUNT(o.id) FILTER (
                   WHERE o.would_trade AND o.net_pnl > 0
                     AND o.provenance_version >= 2 AND o.config_hash = g.config_hash
               )::int AS winners,
               COUNT(o.id) FILTER (
                   WHERE o.would_trade AND o.net_pnl <= 0
                     AND o.provenance_version >= 2 AND o.config_hash = g.config_hash
               )::int AS non_winners,
               COALESCE(SUM(o.net_pnl) FILTER (
                   WHERE o.would_trade AND o.provenance_version >= 2
                     AND o.config_hash = g.config_hash
               ),0)::double precision AS net_pnl
        FROM paper_aeve_generations g
        LEFT JOIN paper_aeve_generation_outcomes o
          ON o.generation=g.generation AND o.config_hash=g.config_hash
        WHERE g.status='ACTIVE'
        GROUP BY g.generation,g.started_at,g.config_hash,g.status
        ORDER BY g.generation DESC LIMIT 1
    """)
except Exception:
    aeve_rows = []

if aeve_rows:
    aeve = aeve_rows[0]
    completed = int(aeve.get("verified_trades") or 0)
    target = 1000
    st.subheader("AEVE Generation Progress")
    p1, p2, p3, p4 = st.columns(4)
    p1.metric("Generation", int(aeve.get("generation") or 1))
    p2.metric("Verified trades", f"{completed:,} / {target:,}")
    p3.metric("Remaining", f"{max(0, target-completed):,}")
    p4.metric("Net post-cost P&L", f"${float(aeve.get('net_pnl') or 0.0):,.2f}")
    st.progress(min(1.0, completed / target))
    st.caption(
        f"Accepted AEVE outcomes only · provenance v2+ · active config hash matched · "
        f"winners {int(aeve.get('winners') or 0):,} · "
        f"non-winners {int(aeve.get('non_winners') or 0):,}. "
        "Council approvals, scans, quote handoffs, and rejected shadow candidates do not increment this counter."
    )
else:
    st.warning("AEVE generation progress is unavailable; no active generation-isolated outcome row was returned.")

# Research diagnostics: expose the economics behind the counter, not merely volume.
try:
    aeve_diag = rows("""
        SELECT COUNT(*) FILTER (WHERE would_trade)::int AS trades,
               AVG(net_pnl) FILTER (WHERE would_trade) AS expectancy,
               CASE WHEN ABS(SUM(CASE WHEN would_trade AND net_pnl<0 THEN net_pnl ELSE 0 END)) > 0
                    THEN SUM(CASE WHEN would_trade AND net_pnl>0 THEN net_pnl ELSE 0 END)
                         / ABS(SUM(CASE WHEN would_trade AND net_pnl<0 THEN net_pnl ELSE 0 END))
                    ELSE NULL END AS profit_factor,
               AVG(mfe_pct) FILTER (WHERE would_trade AND excursion_sample_count>0) AS avg_mfe_pct,
               AVG(mae_pct) FILTER (WHERE would_trade AND excursion_sample_count>0) AS avg_mae_pct,
               AVG(cost_pct) FILTER (WHERE would_trade) AS avg_cost_pct
        FROM paper_aeve_generation_outcomes o
        JOIN paper_aeve_generations g
          ON g.generation=o.generation AND g.config_hash=o.config_hash
        WHERE g.status='ACTIVE' AND o.provenance_version>=2
    """)
except Exception:
    aeve_diag = []
if aeve_diag:
    d = aeve_diag[0]
    st.caption(
        "Generation economics · "
        f"expectancy {float(d.get('expectancy') or 0):.6f} · "
        f"PF {float(d.get('profit_factor') or 0):.3f} · "
        f"avg MFE {float(d.get('avg_mfe_pct') or 0):.3f}% · "
        f"avg MAE {float(d.get('avg_mae_pct') or 0):.3f}% · "
        f"avg measured cost {float(d.get('avg_cost_pct') or 0):.4f}%"
    )

# Lifecycle reconciliation makes the difference between a decision and a completed trade explicit.
try:
    lifecycle = rows("""
        SELECT COUNT(*)::int AS closed_trades,
               COUNT(*) FILTER (WHERE entry_time IS NOT NULL AND exit_time IS NOT NULL)::int AS complete_lifecycle,
               COUNT(*) FILTER (WHERE entry_signal_id IS NOT NULL AND feature_snapshot IS NOT NULL)::int AS exact_entry_provenance
        FROM paper_regime_trade_metrics
        WHERE strategy='oracle_council_v3'
    """)
except Exception:
    lifecycle = []
if lifecycle:
    life = lifecycle[0]
    st.subheader("Paper Trade Lifecycle")
    q1, q2, q3 = st.columns(3)
    q1.metric("Closed Council trades", int(life.get("closed_trades") or 0))
    q2.metric("Complete entry → exit", int(life.get("complete_lifecycle") or 0))
    q3.metric("Exact entry provenance", int(life.get("exact_entry_provenance") or 0))
    st.caption("Signals and approvals are not counted as completed trades. Closed lifecycle evidence is the source of truth.")

# Unified research cockpit: evidence throughput, knowledge use, source health and control comparison.
try:
    cockpit = rows("""
        WITH active AS (
          SELECT generation,config_hash,started_at FROM paper_aeve_generations
          WHERE status='ACTIVE' ORDER BY generation DESC LIMIT 1
        )
        SELECT
          a.generation,a.config_hash,a.started_at,
          COUNT(o.id) FILTER (WHERE o.would_trade AND o.provenance_version>=2)::int AS aeve_trades,
          COUNT(o.id) FILTER (WHERE o.would_trade AND o.provenance_version>=2)::int AS provenance_v2,
          COUNT(o.id) FILTER (WHERE o.would_trade AND o.provenance_version<2)::int AS legacy_provenance
        FROM active a LEFT JOIN paper_aeve_generation_outcomes o
          ON o.generation=a.generation AND o.config_hash=a.config_hash
        GROUP BY a.generation,a.config_hash,a.started_at
    """)
except Exception:
    cockpit=[]
if cockpit:
    cp=cockpit[0]
    st.subheader("Oracle Research Cockpit")
    completed=int(cp.get("aeve_trades") or 0)
    elapsed=max(0.001,(datetime.now(timezone.utc)-cp["started_at"]).total_seconds()/3600.0) if cp.get("started_at") else 0.0
    tph=completed/elapsed if elapsed else 0.0
    c1,c2,c3,c4=st.columns(4)
    c1.metric("Verified AEVE", f"{completed:,} / 1,000")
    c2.metric("Learning velocity", f"{tph:.2f}/hr")
    c3.metric("Provenance v2", int(cp.get("provenance_v2") or 0))
    c4.metric("Legacy provenance", int(cp.get("legacy_provenance") or 0))
    st.caption("The authoritative generation table stores immutable config/provenance identity. Entry-time knowledge snapshots remain in canonical Council lifecycle evidence; they are not duplicated into AEVE outcomes.")

try:
    source_health=rows("""
        SELECT provider,
               COUNT(*)::int AS observations,
               COUNT(*) FILTER (WHERE status='active')::int AS active,
               COUNT(*) FILTER (WHERE status='stale')::int AS stale,
               AVG(freshness_score) AS freshness,
               AVG(confidence) AS confidence
        FROM oracle_brain_sources
        GROUP BY provider ORDER BY observations DESC LIMIT 12
    """)
except Exception:
    source_health=[]
if source_health:
    st.subheader("Intelligence Data Lineage & Health")
    st.dataframe(source_health, width="stretch", hide_index=True)
    st.caption("Provider → freshness → confidence is visible. Stale evidence remains research evidence and is not silently treated as current.")

# Learning + validation cockpit (research-only).
try:
    lv = rows("""SELECT
      (SELECT COUNT(*)::int FROM oracle_counterfactual_outcomes) counterfactuals,
      (SELECT COUNT(*)::int FROM oracle_counterfactual_outcomes WHERE outcome_class='avoided_loss') avoided_losses,
      (SELECT COUNT(*)::int FROM oracle_counterfactual_outcomes WHERE outcome_class='missed_winner') missed_winners,
      (SELECT COUNT(*)::int FROM oracle_decision_replays) decision_replays,
      (SELECT COUNT(*)::int FROM oracle_setup_validation) setup_cohorts,
      (SELECT COUNT(*)::int FROM oracle_challenger_validation WHERE state='shadow') shadow_challengers,
      (SELECT COUNT(*)::int FROM oracle_validation_weaknesses WHERE state='negative') negative_cohorts,
      (SELECT SUM(samples*calibration_error)/NULLIF(SUM(samples),0) FROM oracle_calibration_buckets) calibration_error
    """)
    failure_rows=rows("""SELECT failure_class,COUNT(*)::int samples FROM oracle_failure_taxonomy GROUP BY failure_class ORDER BY samples DESC""")
    challenger_rows=rows("""SELECT candidate_key,market,samples,expectancy,profit_factor,max_drawdown,state,updated_at FROM oracle_challenger_validation ORDER BY state DESC,samples DESC LIMIT 30""")
    setup_rows=rows("""SELECT market,strategy,regime,samples,wins,losses,expectancy,profit_factor,avg_mfe_pct,avg_mae_pct FROM oracle_setup_validation ORDER BY samples DESC LIMIT 30""")
except Exception:
    lv,failure_rows,challenger_rows,setup_rows=[],[],[],[]

if lv:
    v=lv[0]
    st.subheader("Learning & validation")
    c1,c2,c3,c4=st.columns(4)
    c1.metric("Decision replays",int(v.get("decision_replays") or 0))
    c2.metric("Avoided losses",int(v.get("avoided_losses") or 0))
    c3.metric("Missed winners",int(v.get("missed_winners") or 0))
    c4.metric("Shadow challengers",int(v.get("shadow_challengers") or 0))
    c5,c6,c7,c8=st.columns(4)
    c5.metric("Counterfactuals",int(v.get("counterfactuals") or 0))
    c6.metric("Setup cohorts",int(v.get("setup_cohorts") or 0))
    c7.metric("Negative cohorts",int(v.get("negative_cohorts") or 0))
    err=v.get("calibration_error"); c8.metric("Calibration error","n/a" if err is None else f"{float(err):.3f}")
    st.caption("Research-only evidence: replay → attribution → calibration → counterfactuals → walk-forward challenger validation. Execution authority: NONE.")
    with st.expander("Setup-specific learning"):
        st.dataframe(pd.DataFrame(setup_rows),width="stretch",hide_index=True)
    with st.expander("Failure taxonomy"):
        st.dataframe(pd.DataFrame(failure_rows),width="stretch",hide_index=True)
    with st.expander("Walk-forward challengers"):
        st.dataframe(pd.DataFrame(challenger_rows),width="stretch",hide_index=True)

summary = snapshot["summary"]
growth = snapshot["growth"]
safety = snapshot["safety"]

st.title("Oracle Brain")
activity = snapshot["learning_activity"]
st.caption(
    f"Brain growth: {growth['knowledge_units']:,} retained evidence units · "
    f"{growth['relationships']:,} learned relationships · "
    f"learning state {activity['status']} · last sync {activity['last_sync_at'] or 'not recorded'}"
)

a1, a2, a3, a4, a5 = st.columns(5)
a1.metric("Brain state", activity["status"])
a2.metric("New sources", activity["new_sources"])
a3.metric("Revised sources", activity["revised_sources"])
a4.metric("New exact outcomes", activity["new_exact_episodes"])
a5.metric("Lessons updated", activity["lessons_updated"])

if activity["status"] == "LEARNING":
    st.success(
        f"Oracle Brain learned from {activity['learned_this_cycle']} new or revised evidence item(s) "
        "in its latest completed learning cycle."
    )
elif activity["status"] == "SYNCED — NO NEW EVIDENCE":
    st.info("Oracle Brain completed its latest sync successfully, but no new/revised evidence was available to learn.")
elif activity["status"] == "STALE":
    st.warning("Oracle Brain learning sync is stale. The visualization will stay dim until a fresh learning cycle completes.")
else:
    st.warning("Oracle Brain has not recorded a completed learning sync yet.")

# Evidence-driven anatomical neural field. Raw generated HTML runs in Streamlit's supported iframe.
st.iframe(
    render_oracle_brain_component(snapshot),
    height=610,
)
st.caption(
    "The brain shape is a visualization of persisted research memory. "
    "Learning status comes from completed Oracle Brain sync records; execution authority: NONE."
)

retention = snapshot["retention_health"]
st.subheader("Brain retention health")
rh1, rh2, rh3, rh4 = st.columns(4)
rh1.metric("Persistent entries", retention["entries"]["count"])
rh2.metric("Retained sources", retention["sources"]["count"])
rh3.metric("Exact episodes retained", retention["episodes"]["count"])
rh4.metric("Learning pipelines", retention["learning_pipelines"])
st.caption(
    f"Store: {retention['persistent_store']} · Last learning sync: "
    f"{retention['last_sync_at'] or 'not recorded'} · "
    f"Oldest retained knowledge: {retention['entries']['oldest'] or 'not recorded'} · "
    "Read-only; execution authority: NONE."
)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Active knowledge entries", summary["active_entries"])
c2.metric("Exact trade episodes", summary["exact_episodes"])
c3.metric("Knowledge sources", summary["knowledge_sources"])
c4.metric("Concept links", summary["concept_links"])

l1, l2, l3, l4 = st.columns(4)
l1.metric("Research topics", summary["research_topics"])
l2.metric("Open contradictions", summary["active_contradictions"])
l3.metric("High-confidence links", summary["high_confidence_links"])
l4.metric("Stale sources", summary["stale_sources"])

if safety["safe_research_boundary"]:
    st.success(
        f"Research boundary intact: execution_mode={safety['execution_mode']}, "
        "broker submission disabled, live trading disarmed."
    )
else:
    st.error(
        "Research boundary is not fully isolated. "
        f"execution_mode={safety['execution_mode']}, "
        f"broker_submission={safety['broker_submission_enabled']}, "
        f"live_armed={safety['live_trading_armed']}."
    )

st.subheader("Permanent doctrine")
for item in snapshot["doctrine"]:
    with st.expander(item["title"]):
        st.write(item["body"])

if snapshot["derived_lessons"]:
    st.subheader("Evidence-derived lessons")
    for lesson in snapshot["derived_lessons"]:
        if lesson["level"] == "warning":
            st.warning(f"**{lesson['title']}** — {lesson['body']}")
        else:
            st.info(f"**{lesson['title']}** — {lesson['body']}")

st.subheader("Learning system")
st.caption(
    "Orange nodes are acquired source facts, green/red nodes are exact-provenance outcome episodes, "
    "purple nodes are durable lessons, and blue nodes are regime evidence. None has execution authority."
)

if snapshot["research_queue"]:
    st.markdown("**Research queue**")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Topic": item["topic"],
                    "Market": item["market"],
                    "Priority": item["priority"],
                    "Reason": item["reason"],
                    "Updated": item["updated_at"],
                }
                for item in snapshot["research_queue"]
            ]
        ),
        width="stretch",
        hide_index=True,
    )

if snapshot["contradictions"]:
    st.markdown("**Active contradictions**")
    for item in snapshot["contradictions"]:
        st.warning(
            f"{item['subject_key']}: {item.get('prior_polarity') or 'unknown'} → "
            f"{item.get('current_polarity') or 'unknown'} — {item['reason']}"
        )

with st.expander("Recent acquired knowledge sources"):
    if snapshot["sources"]:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Provider": item["provider"],
                        "Category": item["category"],
                        "Symbol": item["symbol"],
                        "Title": item["title"],
                        "Quality": item["source_quality"],
                        "Freshness": item["freshness_score"],
                        "Confidence": item["confidence"],
                        "Verification": item.get("verification_status", "reported"),
                        "Ranking eligible": item.get("ranking_eligible", False),
                        "Source": item.get("source_ref"),
                        "Status": item["status"],
                    }
                    for item in snapshot["sources"]
                ]
            ),
            width="stretch",
            hide_index=True,
        )
    else:
        st.info("No acquired source facts are available yet.")

with st.expander("Recent exact-provenance episodes"):
    if snapshot["episodes"]:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Market": item["market"],
                        "Symbol": item["symbol"],
                        "Strategy": item["strategy"],
                        "Regime": item["regime"],
                        "Net P&L": item["net_pnl"],
                        "MFE %": item["mfe_pct"],
                        "MAE %": item["mae_pct"],
                        "Confidence": item["confidence"],
                        "Exit": item["exit_time"],
                    }
                    for item in snapshot["episodes"]
                ]
            ),
            width="stretch",
            hide_index=True,
        )
    else:
        st.info("No exact-provenance outcome episodes are available yet.")

with st.expander("Strongest concept relationships"):
    if snapshot["concept_links"]:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "From": item["source_key"],
                        "Relation": item["relation"],
                        "To": item["target_key"],
                        "Evidence": item["evidence_count"],
                        "Confidence": item["confidence"],
                        "Weight": item["weight"],
                    }
                    for item in snapshot["concept_links"][:50]
                ]
            ),
            width="stretch",
            hide_index=True,
        )
    else:
        st.info("Concept links will appear as the learner observes repeated relationships.")

st.subheader("Persistent knowledge ledger")
entries = snapshot["entries"]
if entries:
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Category": item["category"],
                    "Key": item["brain_key"],
                    "Title": item["title"],
                    "Confidence": item["confidence"],
                    "Evidence": item["evidence_ref"],
                    "Execution impact": item["execution_impact"],
                    "Created": item["created_at"],
                }
                for item in entries
            ]
        ),
        width="stretch",
        hide_index=True,
    )
else:
    st.info("No active Oracle Brain entries are available yet.")

st.subheader("Regime economics evidence")
regimes = snapshot["regime_economics"]
if regimes:
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Strategy": item["strategy"],
                    "Regime": item["regime"],
                    "Samples": item["samples"],
                    "Net P&L": item["net_pnl"],
                    "Fees": item["fees"],
                    "Expectancy": item["expectancy"],
                    "Avg MFE %": item["avg_mfe_pct"],
                    "Avg MAE %": item["avg_mae_pct"],
                    "State": item["evidence_state"],
                }
                for item in regimes
            ]
        ),
        width="stretch",
        hide_index=True,
    )
else:
    st.info("No regime economics rows are available.")

st.subheader("Worker context")
if snapshot["workers"]:
    st.dataframe(pd.DataFrame(snapshot["workers"]), width="stretch", hide_index=True)
else:
    st.info("Worker heartbeat data is unavailable.")

with st.expander("Brain safety boundary"):
    st.markdown(
        """
- The dashboard performs SELECT-only evidence reads.
- The learning worker runs asynchronously after scans and does not participate in order approval.
- Durable sources, episodes, links, contradictions, and Brain ledger entries enforce execution impact NONE.
- Exact trade provenance is required before an outcome becomes a durable episode.
- Brain knowledge cannot arm live trading, submit orders, size positions, or bypass Council/risk/capacity gates.
"""
    )
