from __future__ import annotations

import pandas as pd
import streamlit as st

from database import database_ready, rows
from oracle_brain import build_oracle_brain_snapshot


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
safety = snapshot["safety"]

st.title("Oracle Brain")
st.caption(
    "Persistent engineering/research memory + live evidence summary. "
    "Read-only dashboard; execution authority: NONE."
)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Active knowledge entries", summary["active_entries"])
c2.metric("Tracked experiments", summary["experiments"])
c3.metric("Mature negative regimes", summary["mature_negative_regimes"])
c4.metric("Mature positive regimes", summary["mature_positive_regimes"])

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
        use_container_width=True,
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
        use_container_width=True,
        hide_index=True,
    )
else:
    st.info("No regime economics rows are available.")

st.subheader("Worker context")
if snapshot["workers"]:
    st.dataframe(pd.DataFrame(snapshot["workers"]), use_container_width=True, hide_index=True)
else:
    st.info("Worker heartbeat data is unavailable.")

with st.expander("Brain safety boundary"):
    st.markdown(
        """
- Oracle Brain is not imported into the worker hot path.
- The dashboard performs SELECT-only evidence reads.
- Brain ledger entries have execution impact NONE enforced by the database.
- The write helper exists only for explicit engineering/research workflows.
- Brain entries cannot arm live trading, submit orders, or bypass Council/risk/capacity gates.
"""
    )
