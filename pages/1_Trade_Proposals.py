from __future__ import annotations

import pandas as pd
import streamlit as st

from migrations import run_migrations
from trade_proposals import list_trade_proposals


st.set_page_config(page_title="Oracle Crypto Executions", layout="wide")
st.title("Crypto Paper Executions")
st.caption(
    "These trades were already executed automatically by Oracle in the paper portfolio. "
    "Broker prices are shown for comparison only; this page does not submit real-money orders."
)

try:
    run_migrations()
except Exception as exc:
    st.error(f"Database preparation unavailable: {exc.__class__.__name__}")
    st.stop()

if st.button("Refresh executions", use_container_width=False):
    st.rerun()

try:
    proposals = list_trade_proposals(limit=200, pending_only=False)
except Exception as exc:
    st.error(f"Trade execution history unavailable: {exc.__class__.__name__}")
    st.stop()

c1, c2, c3 = st.columns(3)
c1.metric("Recent autonomous paper trades", len(proposals))
if proposals:
    newest = proposals[0]
    c2.metric("Newest symbol", str(newest.get("symbol") or "—"))
    notional = newest.get("notional")
    c3.metric("Newest size", "—" if notional is None else f"${float(notional):,.2f}")
else:
    c2.metric("Newest symbol", "—")
    c3.metric("Newest size", "—")

if not proposals:
    st.info("No broker-verified paper executions yet.")
    st.stop()

summary_rows = []
for item in proposals:
    confidence = item.get("confidence")
    if confidence is not None:
        try:
            confidence = float(confidence)
            if confidence <= 1:
                confidence *= 100
            confidence = f"{confidence:.1f}%"
        except (TypeError, ValueError):
            pass
    rr = item.get("risk_reward_ratio")
    if rr is not None:
        try:
            rr = f"{float(rr):.2f}x"
        except (TypeError, ValueError):
            pass
    summary_rows.append(
        {
            "Created": item.get("created_at"),
            "Symbol": item.get("symbol"),
            "Side": item.get("side"),
            "Notional": item.get("notional"),
            "Paper Fill": item.get("paper_fill_price"),
            "Broker Price": item.get("broker_executable_price"),
            "Spread %": item.get("broker_spread_pct"),
            "Strategy": item.get("strategy") or "Unavailable",
            "Confidence": confidence if confidence is not None else "Unavailable",
            "Risk/Reward": rr if rr is not None else "Unavailable",
            "Status": "EXECUTED AUTOMATICALLY" if item.get("paper_fill_id") else "PAPER FILL UNAVAILABLE",
        }
    )

frame = pd.DataFrame(summary_rows)
st.dataframe(
    frame,
    use_container_width=True,
    hide_index=True,
    column_config={
        "Notional": st.column_config.NumberColumn(format="$%.2f"),
        "Paper Fill": st.column_config.NumberColumn(format="$%.8f"),
        "Broker Price": st.column_config.NumberColumn(format="$%.8f"),
        "Spread %": st.column_config.NumberColumn(format="%.4f%%"),
    },
)

st.subheader("Execution details")
for item in proposals[:25]:
    label = f"{item.get('side')} {item.get('symbol')} — ${float(item.get('notional') or 0):,.2f}"
    with st.expander(label):
        left, right = st.columns(2)
        with left:
            st.write("Paper execution", item.get("paper_execution_status"))
            st.write("Paper fill ID", item.get("paper_fill_id") or "Unavailable")
            st.write("Strategy", item.get("strategy") or "Unavailable")
            st.write("Reason", item.get("reason") or "Unavailable")
            st.write("Score", item.get("score") if item.get("score") is not None else "Unavailable")
            st.write("Confidence", item.get("confidence") if item.get("confidence") is not None else "Unavailable")
            st.write("Risk / reward", item.get("risk_reward_ratio") if item.get("risk_reward_ratio") is not None else "Unavailable")
            st.write("Target", item.get("target_price") if item.get("target_price") is not None else "Unavailable")
            st.write("Stop", item.get("stop_loss") if item.get("stop_loss") is not None else "Unavailable")
        with right:
            st.write("Quantity", item.get("quantity"))
            st.write("Notional", item.get("notional"))
            st.write("Oracle reference", item.get("oracle_reference_price"))
            st.write("Paper fill price", item.get("paper_fill_price"))
            st.write("Broker comparison price", item.get("broker_executable_price"))
            st.write("Broker bid", item.get("broker_bid"))
            st.write("Broker ask", item.get("broker_ask"))
            st.write("Broker spread %", item.get("broker_spread_pct"))
            st.write("Estimated-price response", item.get("broker_estimated_price") or "Unavailable")

        st.caption(
            "Paper execution is automatic. Real-money broker submission is a separate path and is not performed by this page."
        )
