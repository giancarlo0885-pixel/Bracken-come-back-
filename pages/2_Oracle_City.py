from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import streamlit as st

from config import EXECUTION_MODE, LIVE_STATUS_STALE_SECONDS
from database import database_ready, rows

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st_autorefresh = None


st.set_page_config(
    page_title="Oracle City - GARIBALDI MARKET ORACLE",
    page_icon="ORCL",
    layout="wide",
)


def safe_rows(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    if not query.lstrip().upper().startswith("SELECT"):
        raise ValueError("Oracle City is read-only")
    try:
        return rows(query, params)
    except Exception:
        return []


def parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def worker_state(record: dict[str, Any]) -> str:
    status = str(record.get("status") or "").lower()
    if status in {"error", "failed", "offline", "halted", "stopped"}:
        return "offline"
    heartbeat = record.get("heartbeat") or record.get("last_pulse") or record.get("last_run")
    parsed = parse_time(heartbeat)
    if parsed is None:
        return "waiting"
    age = max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds())
    if age > LIVE_STATUS_STALE_SECONDS:
        return "offline"
    if status in {"running", "online", "ok", "healthy", "live", "scanning"}:
        return "online"
    return "waiting"


def payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            item = json.loads(value)
            return item if isinstance(item, dict) else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
    return {}


def number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def money(value: Any) -> str:
    numeric = number(value)
    return "Unavailable" if numeric is None else "$" + f"{numeric:,.2f}"


def district(node: dict[str, str]) -> str:
    state = node["state"] if node["state"] in {"online", "waiting", "offline"} else "waiting"
    return (
        "<div class='district " + state + "'>"
        "<div class='tower-wrap'><div class='tower'><div class='beacon'></div></div></div>"
        "<div class='district-title'>" + html.escape(node["title"]) + "</div>"
        "<div class='district-metric'>" + html.escape(node["metric"]) + "</div>"
        "<div class='district-detail'>" + html.escape(node["detail"]) + "</div>"
        "</div>"
    )


health = database_ready(connect_timeout=5)
if not health.get("ok"):
    st.error("Oracle City cannot safely display financial state because PostgreSQL is unavailable.")
    st.caption(str(health.get("message") or "Database readiness check failed."))
    st.stop()

workers = safe_rows("SELECT * FROM market_worker_status ORDER BY market")
portfolios = safe_rows("SELECT * FROM portfolios ORDER BY market")
positions = safe_rows("SELECT * FROM positions ORDER BY market,symbol")
trades = safe_rows("SELECT * FROM trades ORDER BY id DESC LIMIT 60")
opportunities = safe_rows(
    """SELECT DISTINCT ON (market,symbol)
              market,symbol,rank,opportunity_score,payload,created_at
       FROM opportunity_rankings
       ORDER BY market,symbol,created_at DESC"""
)
decisions = safe_rows("SELECT * FROM oracle_decision_audit ORDER BY id DESC LIMIT 50")
events = safe_rows("SELECT * FROM intelligence_events ORDER BY id DESC LIMIT 40")

worker_map = {str(item.get("market") or ""): item for item in workers}
worker_states = {market: worker_state(item) for market, item in worker_map.items()}
online_workers = sum(1 for item in worker_states.values() if item == "online")
open_positions = [item for item in positions if abs(number(item.get("quantity")) or 0.0) > 0]
top_opportunities = sorted(
    opportunities,
    key=lambda item: number(item.get("opportunity_score")) or 0.0,
    reverse=True,
)
top = top_opportunities[0] if top_opportunities else {}
top_payload = payload(top.get("payload"))
top_symbol = str(top.get("symbol") or "Waiting")
top_action = str(top_payload.get("action") or top_payload.get("decision") or "WATCH").upper()

left, middle, spacer = st.columns([1, 1, 4])
with left:
    if st.button("Refresh now", type="primary", use_container_width=True):
        st.rerun()
with middle:
    auto_refresh = st.toggle("Auto refresh", value=True)
if auto_refresh and st_autorefresh is not None:
    st_autorefresh(interval=15_000, key="oracle-city-refresh")

st.markdown(
    """
<style>
.oracle-city-hero{border:1px solid #21445a;border-radius:22px;padding:18px 20px;background:linear-gradient(135deg,#071621,#0b2431 55%,#061018);margin:.4rem 0 1rem;box-shadow:0 20px 70px rgba(0,0,0,.25)}
.oracle-city-hero h1{margin:0;font-size:clamp(1.8rem,4vw,3.1rem);letter-spacing:.04em}
.oracle-city-hero p{margin:.4rem 0 0;color:#9bb2c1;max-width:900px}
.city-status{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0 0}.city-chip{border:1px solid #2b5870;background:#07131d;border-radius:999px;padding:6px 10px;font-size:.76rem;font-weight:850}
.city-shell{position:relative;overflow:hidden;border:1px solid #17384d;border-radius:24px;padding:30px 20px 34px;background:radial-gradient(circle at 50% 28%,#103244 0,#07141e 38%,#03080d 78%);box-shadow:inset 0 0 80px rgba(31,150,190,.08),0 24px 80px rgba(0,0,0,.28)}
.city-shell:before{content:"";position:absolute;inset:0;background-image:linear-gradient(rgba(72,148,184,.09) 1px,transparent 1px),linear-gradient(90deg,rgba(72,148,184,.09) 1px,transparent 1px);background-size:42px 42px;transform:perspective(550px) rotateX(62deg) scale(1.5);transform-origin:center 30%;pointer-events:none}
.city-grid{position:relative;z-index:2;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px;max-width:1180px;margin:0 auto}
.district{position:relative;min-height:230px;border:1px solid rgba(76,142,174,.23);border-radius:20px;padding:14px;background:linear-gradient(180deg,rgba(9,27,39,.78),rgba(4,13,20,.83));backdrop-filter:blur(6px);transition:transform .25s ease,border-color .25s ease,box-shadow .25s ease}
.district:hover{transform:translateY(-6px);border-color:#4da6cb;box-shadow:0 18px 40px rgba(0,0,0,.33)}
.tower-wrap{height:120px;display:flex;align-items:center;justify-content:center;perspective:600px}
.tower{position:relative;width:76px;height:96px;transform:rotateX(-12deg) rotateY(24deg);background:linear-gradient(90deg,#112d3e,#1d5571 60%,#0b2635);border:1px solid #367b99;box-shadow:18px 18px 0 rgba(1,7,11,.38),0 0 34px rgba(66,180,220,.14)}
.tower:before{content:"";position:absolute;left:9px;right:9px;top:12px;height:4px;background:currentColor;box-shadow:0 15px currentColor,0 30px currentColor,0 45px currentColor,0 60px currentColor;opacity:.45}
.tower:after{content:"";position:absolute;left:50%;top:-20px;width:2px;height:20px;background:currentColor;transform:translateX(-50%);box-shadow:0 0 12px currentColor}
.beacon{position:absolute;left:50%;top:-25px;width:7px;height:7px;border-radius:50%;transform:translateX(-50%);background:currentColor;box-shadow:0 0 16px currentColor;animation:pulse 1.7s ease-in-out infinite}
.online{color:#52f59b}.waiting{color:#ffd166}.offline{color:#ff6767}
@keyframes pulse{0%,100%{opacity:.35;transform:translateX(-50%) scale(.8)}50%{opacity:1;transform:translateX(-50%) scale(1.25)}}
.district-title{color:#f4fbff;font-weight:900;font-size:1.02rem}.district-metric{color:currentColor;font-size:1.22rem;font-weight:950;margin-top:4px}.district-detail{color:#91abba;font-size:.78rem;line-height:1.4;margin-top:6px}
.oracle-note{margin:14px 0 0;border:1px solid #21445a;border-radius:14px;padding:11px 13px;background:#06121b;color:#9eb4c1;font-size:.8rem}
@media(max-width:900px){.city-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:620px){.city-grid{grid-template-columns:1fr}.district{min-height:205px}}
</style>
""",
    unsafe_allow_html=True,
)

mode_label = str(EXECUTION_MODE).upper()
st.markdown(
    "<div class='oracle-city-hero'><h1>GARIBALDI MARKET ORACLE - CITY</h1>"
    "<p>Live command center for Oracle's workers, Council evidence, risk state, portfolio, "
    "execution history, intelligence, and pattern memory. The city reads canonical PostgreSQL "
    "state and does not own the trading engine.</p>"
    "<div class='city-status'>"
    "<span class='city-chip'>MODE " + html.escape(mode_label) + "</span>"
    "<span class='city-chip'>WORKERS " + str(online_workers) + "/" + str(len(workers)) + "</span>"
    "<span class='city-chip'>POSITIONS " + str(len(open_positions)) + "</span>"
    "<span class='city-chip'>TOP " + html.escape(top_symbol) + " " + html.escape(top_action) + "</span>"
    "</div></div>",
    unsafe_allow_html=True,
)

def worker_node(market: str, title: str) -> dict[str, str]:
    record = worker_map.get(market)
    if not record:
        return {"title": title, "state": "waiting", "metric": "No heartbeat", "detail": "Worker status has not been recorded."}
    state = worker_states.get(market, "waiting")
    heartbeat = record.get("heartbeat") or record.get("last_pulse") or record.get("last_run")
    parsed = parse_time(heartbeat)
    age = "unknown age" if parsed is None else str(int(max(0, (datetime.now(timezone.utc) - parsed).total_seconds()))) + "s old"
    return {
        "title": title,
        "state": state,
        "metric": str(record.get("status") or "unknown").upper(),
        "detail": "Heartbeat " + age + ". " + str(record.get("message") or ""),
    }

nodes = [
    {
        "title": "Council HQ",
        "state": "online" if top_opportunities else "waiting",
        "metric": str(len(top_opportunities)) + " ranked candidates",
        "detail": "Top evidence: " + top_symbol + " " + top_action + ".",
    },
    worker_node("cash", "Stock Exchange"),
    worker_node("crypto", "Crypto Exchange"),
    {
        "title": "Intelligence Tower",
        "state": "online" if events else "waiting",
        "metric": str(len(events)) + " recent events",
        "detail": str((events[0] if events else {}).get("title") or (events[0] if events else {}).get("event_type") or "Waiting for intelligence."),
    },
    {
        "title": "Pattern Lab",
        "state": "online" if top_opportunities else "waiting",
        "metric": str(len({str(payload(item.get("payload")).get("strategy") or payload(item.get("payload")).get("setup") or "unattributed") for item in top_opportunities})) + " setup classes",
        "detail": "Observed strategy and regime evidence from ranked opportunities.",
    },
    {
        "title": "Risk Center",
        "state": "online",
        "metric": mode_label + " isolated",
        "detail": "Oracle City cannot change risk gates, sizing, thresholds, or broker controls.",
    },
    {
        "title": "Portfolio Vault",
        "state": "online" if portfolios else "waiting",
        "metric": str(len(open_positions)) + " open positions",
        "detail": "Portfolio state is read from canonical storage.",
    },
    {
        "title": "Execution Center",
        "state": "online" if trades else "waiting",
        "metric": str(len(trades)) + " recent records",
        "detail": "Displays persisted paper-trade history; this page submits no orders.",
    },
    {
        "title": "Data Center",
        "state": "online",
        "metric": "PostgreSQL linked",
        "detail": "Read-only queries power every Oracle City district.",
    },
]

st.markdown(
    "<div class='city-shell'><div class='city-grid'>"
    + "".join(district(node) for node in nodes)
    + "</div><div class='oracle-note'>DATA FLOW: market data -> pattern evidence -> Council -> risk gates -> execution engine -> portfolio outcome -> learning memory. Oracle City observes the flow; it does not bypass it.</div></div>",
    unsafe_allow_html=True,
)

st.subheader("Top ranked opportunities")
opportunity_rows = []
for item in top_opportunities[:15]:
    detail = payload(item.get("payload"))
    confidence = number(detail.get("confidence"))
    if confidence is not None and confidence <= 1:
        confidence *= 100
    opportunity_rows.append(
        {
            "Market": item.get("market"),
            "Symbol": item.get("symbol"),
            "Action": str(detail.get("action") or detail.get("decision") or "WATCH").upper(),
            "Score": number(item.get("opportunity_score")),
            "Confidence %": confidence,
            "Strategy": detail.get("strategy") or detail.get("setup") or "unattributed",
            "Regime": detail.get("regime") or "unknown",
            "Observed": item.get("created_at"),
        }
    )
if opportunity_rows:
    st.dataframe(pd.DataFrame(opportunity_rows), use_container_width=True, hide_index=True)
else:
    st.info("No ranked opportunity records are currently available.")

st.subheader("Decision and execution replay")
replay_rows = []
for item in decisions[:20]:
    detail = payload(item.get("payload"))
    replay_rows.append(
        {
            "Time": item.get("created_at") or item.get("decision_time"),
            "Type": "COUNCIL",
            "Market": item.get("market") or detail.get("market"),
            "Symbol": item.get("symbol") or detail.get("symbol"),
            "Action": item.get("action") or detail.get("action") or detail.get("decision"),
            "Detail": item.get("reason") or detail.get("reason") or detail.get("explanation"),
        }
    )
for item in trades[:20]:
    replay_rows.append(
        {
            "Time": item.get("created_at"),
            "Type": "TRADE",
            "Market": item.get("market"),
            "Symbol": item.get("symbol"),
            "Action": item.get("side"),
            "Detail": str(item.get("reason") or "Paper trade") + " | value " + money(item.get("value") or item.get("notional")),
        }
    )
replay_rows.sort(key=lambda item: parse_time(item.get("Time")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
if replay_rows:
    st.dataframe(pd.DataFrame(replay_rows[:30]), use_container_width=True, hide_index=True)
else:
    st.info("Replay data is not available yet.")

with st.expander("Oracle City safety boundary"):
    st.markdown(
        """
- The page performs SELECT queries only.
- It cannot submit, approve, size, cancel, or modify an order.
- It cannot enable live-money switches or change trading thresholds.
- Stock and crypto workers, Council, risk checks, and execution modules remain authoritative.
- Missing data is shown as missing or waiting; Oracle City does not fabricate status.
"""
    )
