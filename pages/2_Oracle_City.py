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
    page_title="Oracle City V2 - GARIBALDI MARKET ORACLE",
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
    if st.button("Refresh now", type="primary", use_container_width=True):
        st.rerun()
with middle:
    auto_refresh = st.toggle("Auto refresh", value=True)

if auto_refresh and st_autorefresh is not None:
    st_autorefresh(interval=15_000, key="oracle-city-v2-refresh")

snapshot = build_oracle_city_snapshot(rows)
summary = snapshot["summary"]

st.title("Oracle City V2")
st.caption(
    "Interactive 3D digital twin of GARIBALDI MARKET ORACLE. "
    "This interface is read-only and has no order-submission authority."
)

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Workers online", f"{summary['workers_online']}/{summary['workers_total']}")
c2.metric("Open positions", summary["open_positions"])
c3.metric("Ranked opportunities", summary["ranked_opportunities"])
c4.metric("Recent trades", summary["recent_trades"])
c5.metric("Known exposure", "$" + f"{summary['known_exposure']:,.2f}")

if snapshot["warnings"]:
    st.warning("Partial Oracle City feeds: " + "; ".join(snapshot["warnings"]))

components.html(
    render_oracle_city_component(snapshot),
    height=820,
    scrolling=False,
)

st.subheader("Top ranked opportunities")
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
        use_container_width=True,
        hide_index=True,
    )
else:
    st.info("No ranked opportunity records are currently available.")

st.subheader("Decision / execution replay ledger")
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
        use_container_width=True,
        hide_index=True,
    )
else:
    st.info("Replay data is not available yet.")

with st.expander("Oracle City V2 architecture and safety boundary"):
    st.markdown(
        """
**Interactive layer**

- WebGL/Three.js scene with orbit, zoom, top view, reset view, clickable districts,
  strategy agents, capital-weighted position towers, and animated data-flow paths.
- Historical replay uses persisted Oracle decisions, intelligence events, and paper trades
  to illuminate the path that evidence took through the system.
- Position towers around Portfolio Vault scale only from available persisted marked values.

**Hard boundary**

- Oracle City performs SELECT queries only.
- It cannot submit, approve, cancel, size, or modify orders.
- It cannot enable live-money switches, change trading thresholds, or mutate balances.
- Stock and crypto workers, Council, risk checks, and execution modules remain authoritative.
- Missing data remains missing; the visualization does not fabricate financial state.
"""
    )
