from __future__ import annotations

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from database import database_ready, rows
from oracle_city_component import render_oracle_city_component
from oracle_city_model import build_oracle_city_snapshot

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st_autorefresh = None


st.set_page_config(
    page_title="Oracle City V3 - GARIBALDI MARKET ORACLE",
    page_icon="ORCL",
    layout="wide",
)

health = database_ready(connect_timeout=5)
if not health.get("ok"):
    st.error("Oracle City cannot safely display financial state because PostgreSQL is unavailable.")
    st.caption(str(health.get("message") or "Database readiness check failed."))
    st.stop()

left, middle, spacer = st.columns([1, 1, 4])
with left:
    if st.button("Refresh now", type="primary", width="stretch"):
        st.rerun()
with middle:
    auto_refresh = st.toggle("Sync data every 60s", value=False)

if auto_refresh and st_autorefresh is not None:
    st_autorefresh(interval=60_000, key="oracle-city-world-sync")

snapshot = build_oracle_city_snapshot(rows)
summary = snapshot["summary"]

st.title("Oracle City — Cinematic Metropolis")
if snapshot["warnings"]:
    st.warning("Partial Oracle City feeds: " + "; ".join(snapshot["warnings"]))

components.html(
    render_oracle_city_component(snapshot),
    height=980,
    scrolling=False,
)

st.subheader("City status")
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Systems online", f"{summary['workers_online']}/{summary['workers_total']}")
c2.metric("Open positions", summary["open_positions"])
c3.metric("Trade ideas", summary["ranked_opportunities"])
c4.metric("Paper fill records", summary.get("paper_fill_records", summary["recent_trades"]))
c5.metric("Money in positions", "$" + f"{summary['known_exposure']:,.2f}")

aeve = snapshot.get("aeve", {})
safety = snapshot.get("safety", {})
aeve_progress = "Unavailable / 1000" if aeve.get("accepted") is None else f"{aeve['accepted']} / {aeve.get('target', 1000)}"
m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("City mood", snapshot.get("city_mood") or "UNKNOWN")
st.caption("City mood reason: " + str(snapshot.get("city_mood_reason") or "not available"))
m2.metric("AEVE progress", aeve_progress)
m3.metric("Execution mode", str(safety.get("execution_mode") or snapshot.get("execution_mode") or "unknown").upper())
m4.metric("Broker submission", "ENABLED" if safety.get("broker_submission_enabled") else "DISABLED")
m5.metric("Live trading", "ARMED" if safety.get("live_trading_armed") else "DISARMED")

brain_summary = snapshot.get("decision_graph", {}).get("summary", {})
b1, b2, b3, b4 = st.columns(4)
b1.metric("Decisions tracked", int(brain_summary.get("traced_decisions") or 0))
b2.metric("Result-linked provenance", int(brain_summary.get("linked_outcomes") or 0))
b3.metric("Safety blocks", int(brain_summary.get("downstream_blocks") or 0))
brain_gaps = int(brain_summary.get("recent_closed_provenance_gaps") or 0)
b4.metric("Closed trades missing provenance", brain_gaps)
if brain_gaps:
    st.error(
        f"{brain_gaps} recent closed trade(s) lack a canonical decision provenance link. "
        "Treat those rows as unsuitable for strategy-learning attribution until repaired."
    )


world_state = snapshot.get("world_state", {})
brain_growth = snapshot.get("brain_growth", {})
x1, x2, x3 = st.columns(3)
x1.metric("Canonical open positions", summary["open_positions"])
x2.metric("Closed fills with realized P&L", int(summary.get("closed_result_records") or 0))
x3.metric("Downstream blocks", int(summary.get("downstream_blocks") or 0))
st.caption("Paper fill records are canonical BUY/SELL rows from the trades table. Result-linked provenance is a separate attribution metric and is not an execution count.")

st.subheader("World state & Brain learning")
w1, w2, w3, w4, w5 = st.columns(5)
w1.metric("Current world events", int(world_state.get("current_events") or 0))
w2.metric("Verified/corroborated", int(world_state.get("verified_events") or 0))
w3.metric("Brain evidence units", int(brain_growth.get("knowledge_units") or 0))
w4.metric("Learned relationships", int(brain_growth.get("relationships") or 0))
w5.metric("Exact outcomes", int(brain_growth.get("exact_outcomes") or 0))

domains = world_state.get("domains", {})
domain_rows = []
for key in ("macro", "energy", "logistics", "crypto", "finance", "consumer", "technology"):
    item = domains.get(key, {})
    domain_rows.append({
        "District": key.replace("_", " ").title(),
        "Current events": int(item.get("events") or 0),
        "Verified": int(item.get("verified") or 0),
        "Avg confidence": round(float(item.get("confidence") or 0.0), 2),
    })
st.dataframe(pd.DataFrame(domain_rows), width="stretch", hide_index=True)

top_events = world_state.get("top_events", [])
if top_events:
    st.dataframe(
        pd.DataFrame([
            {
                "Observed": item.get("observed_at"),
                "Event": item.get("title"),
                "World area": ", ".join(item.get("domains") or []),
                "Source": item.get("provider"),
                "Verification": item.get("verification"),
                "Confidence": item.get("confidence"),
            }
            for item in top_events[:10]
        ]),
        width="stretch",
        hide_index=True,
    )
else:
    st.info("No current provenance-aware world events are available in Oracle's intelligence memory.")

st.subheader("Current trade ideas")
opportunities = snapshot["opportunities"]
if opportunities:
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Market": item["market"],
                    "Symbol": item["symbol"],
                    "Action": item["action"],
                    "Score": item["score"],
                    "Confidence %": item["confidence"],
                    "Strategy": item["strategy"],
                    "Regime": item["regime"],
                    "Observed": item["created_at"],
                }
                for item in opportunities[:18]
            ]
        ),
        width="stretch",
        hide_index=True,
    )
else:
    st.info("No ranked opportunity records are currently available.")

st.subheader("Recent Oracle decisions and paper trades")
replay = list(reversed(snapshot["replay"]))
if replay:
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Time": item["time"],
                    "Type": str(item["kind"]).upper(),
                    "Event": item["title"],
                    "Detail": item["detail"],
                    "Path": " -> ".join(item["path"]),
                }
                for item in replay[:40]
            ]
        ),
        width="stretch",
        hide_index=True,
    )
else:
    st.info("Replay data is not available yet.")

with st.expander("Oracle City V3 architecture and safety boundary"):
    st.markdown(
        """
**Interactive layer**

- WebGL/Three.js scene with orbit, zoom, top view, reset view, clickable operational and community districts,
  moving visualization workers, strategy evidence cohorts, capital-weighted position towers, and animated data-flow paths.
- Worker assignments are derived from persisted Oracle state. Their walking, resting, and recreation movement is illustrative only.
- Strategy Arena and AEVE Research Center expose read-only evidence; they cannot promote a strategy or change execution behavior.
- Current world districts are derived from Oracle's existing provenance-aware intelligence memory; the City does not create or invent news.
- Brain-growth metrics are derived from persisted Oracle Brain sources, exact outcomes, durable lessons, and learned relationships.
- Historical replay uses persisted Oracle decisions, intelligence events, and paper trades
  to illuminate the path that evidence took through the system.
- **Brain Map** traces persisted entry-time features into immutable decision IDs, downstream
  decision-event gates, and execution/outcome records when a canonical link exists.
- Brain Map exposes downstream blocks separately from upstream decisions so an approved
  candidate cannot visually masquerade as an executable or completed trade.
- Closed trades without decision provenance are counted explicitly and are not presented as
  trustworthy strategy-learning evidence.
- Position towers around Portfolio Vault scale only from available persisted marked values.

**Hard boundary**

- Oracle City performs SELECT queries only.
- It cannot submit, approve, cancel, size, or modify orders.
- It cannot enable live-money switches, change trading thresholds, or mutate balances.
- Stock and crypto workers, Council, risk checks, and execution modules remain authoritative.
- Missing data remains missing; the visualization does not fabricate financial state.
"""
    )
