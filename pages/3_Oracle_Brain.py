from __future__ import annotations

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
