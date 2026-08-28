from __future__ import annotations

import html
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

from ai_oracle import answer_market_question, openai_available, test_openai_connection
from config import (
    ALWAYS_ON_TRADING, APP_NAME, EXECUTION_MODE, LIVE_STATUS_STALE_SECONDS,
    PAPER_BROKER_MODE, UI_AUTO_REFRESH, UI_REFRESH_SECONDS,
)
from dashboard_helpers import (
    as_float,
    balanced_data_status,
    balanced_money_bar,
    balanced_opportunity_rows,
    balanced_portfolio_rows,
    capital_allocation_rows,
    capital_deployment_status,
    compact_money_text,
    format_quantity,
    format_asset_price,
    live_data_status,
    wall_street_market_focus,
    money_text,
    readable_trade_rows,
    simple_opportunity_summary,
    simple_portfolio_builder_plan,
    simple_portfolio_scores,
    trade_summary,
    worker_is_online,
)
from crypto_opportunity_engine import crypto_page_sections
from database import bootstrap_database_with_lock, database_ready, database_storage_report, row, rows
from earnings_calendar import mobile_card_lines, prepare_events, table_rows
from market_data import get_history
from stock_best_movers import holding_view_rows
from global_pit_engine import dashboard_activity_labels
from global_adaptive_engine import build_decision_funnel_from_events, split_capital_engines, v39_dashboard_summary
from execution_policy import execution_policy
from migrations import run_migrations
from portfolio_advisor import analyze_portfolio, simulate_trade
from paper_broker import build_account
from prediction_engine import build_decisions
from profit_attribution import profit_attribution_rows
from provider_diagnostics import provider_diagnostics
from realtime_runtime import status_age_seconds

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:  # deployment installs it; local tests can still import modules
    st_autorefresh = None

st.set_page_config(
    page_title=f"{APP_NAME} — AI Chief Investment Officer",
    page_icon="ORCL",
    layout="wide",
    initial_sidebar_state="expanded",
)

if UI_AUTO_REFRESH and st_autorefresh is not None:
    st_autorefresh(interval=UI_REFRESH_SECONDS * 1000, key="oracle-live-refresh")

st.markdown(
    """
<style>
:root{--bg:#071018;--panel:#0d1823;--panel2:#132333;--line:#334b60;--text:#f7fbff;--muted:#aebdca;--green:#39ff88;--yellow:#ffd84d;--orange:#ff9f43;--red:#ff4d5a;--blue:#55b8ff}
html{font-size:17px}.stApp{background:var(--bg);color:var(--text)}.block-container{max-width:1500px;padding-top:1rem;padding-bottom:4rem}
.hero{border:2px solid var(--line);background:linear-gradient(135deg,var(--panel2),var(--panel));border-radius:22px;padding:24px;margin-bottom:18px}.hero h1{font-size:clamp(2rem,4vw,3.3rem);margin:.3rem 0}.hero p{color:var(--muted);max-width:950px;line-height:1.65}.eyebrow{color:var(--green);font-weight:900;letter-spacing:.12em;text-transform:uppercase;font-size:.8rem}
.card{border:2px solid var(--line);background:var(--panel);border-radius:18px;padding:18px;margin-bottom:14px}.card h3{margin-top:0}.muted{color:var(--muted)}
.decision{border:2px solid var(--line);border-left:8px solid var(--yellow);background:var(--panel);border-radius:16px;padding:17px;margin:12px 0}.decision.buy{border-left-color:var(--green)}.decision.sell{border-left-color:var(--red)}.decision.wait{border-left-color:var(--yellow)}.decision.hold{border-left-color:var(--blue)}
.action{font-size:1.32rem;font-weight:950}.buy-text{color:var(--green)}.sell-text{color:var(--red)}.wait-text{color:var(--yellow)}.hold-text{color:var(--blue)}
.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.tile{border:2px solid var(--line);background:var(--panel);border-radius:15px;padding:14px}.tile small{color:var(--muted)}.tile b{display:block;font-size:1.55rem;margin-top:.25rem}.summary-cell small{display:block}.summary-cell b{display:block;margin-top:.2rem;overflow-wrap:anywhere}.money-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px}.money-tile{border:2px solid var(--line);background:var(--panel);border-radius:15px;padding:18px}.money-tile small{color:var(--muted);font-weight:800;text-transform:uppercase}.money-tile b{display:block;font-size:clamp(1.35rem,2.1vw,1.95rem);margin-top:.3rem;overflow-wrap:anywhere}.simple-status{font-size:1.45rem;font-weight:950}.reason{line-height:1.6;color:#eef6fc}.pill{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:.35rem .65rem;margin:.2rem .25rem .1rem 0;background:#09131d;font-weight:700}.status-bar{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.status-chip{border:1px solid var(--line);background:#09131d;border-radius:12px;padding:10px 12px;font-weight:900}.status-chip span{float:right}.green-text{color:var(--green)}.yellow-text{color:var(--yellow)}.red-text{color:var(--red)}.section-title{font-size:1.25rem;font-weight:950;margin:1.4rem 0 .65rem}.oracle-strip{border:2px solid var(--line);background:var(--panel);border-radius:16px;padding:16px;margin:12px 0 18px}
[data-testid="stSidebar"]{border-right:2px solid var(--line)}[data-testid="stMetric"]{border:2px solid var(--line);border-radius:16px;padding:14px;background:var(--panel)}
.stTabs [data-baseweb="tab-list"]{gap:.3rem;overflow-x:auto}.stTabs [data-baseweb="tab"]{min-height:48px;font-weight:800;white-space:nowrap}.stTabs [aria-selected="true"]{border-bottom:4px solid var(--green)!important}
[data-testid="stDataFrame"]{border:2px solid var(--line);border-radius:15px;overflow:hidden}button,input,textarea,select{min-height:44px}
@media(max-width:900px){.grid,.money-grid,.status-bar{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:650px){html{font-size:16px}.block-container{padding-left:.7rem;padding-right:.7rem}.hero{padding:18px}.grid,.money-grid,.status-bar{grid-template-columns:1fr}}
</style>
""",
    unsafe_allow_html=True,
)


@st.cache_resource
def bootstrap() -> list[str]:
    return bootstrap_database_with_lock(run_migrations)


def safe_rows(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    try:
        return rows(query, params)
    except Exception:
        return []


def safe_row(query: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    try:
        return row(query, params) or {}
    except Exception:
        return {}


def money(value: Any) -> str:
    return f"${as_float(value):,.2f}"


def compact_money(value: Any) -> str:
    return compact_money_text(value)


LEGACY_PAPER_MODE_LABELS = (
    "LIVE PAPER TRADING",
    "Institutional Paper Broker",
    "Always-On Institutional Paper Broker",
)


def pct(value: Any) -> str:
    return f"{as_float(value):+.1f}%"


def parse_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            obj = json.loads(value)
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}
    return {}


def get_portfolio(market: str) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, float]]:
    portfolio = safe_row("SELECT * FROM portfolios WHERE market=%s", (market,))
    positions = safe_rows("SELECT * FROM positions WHERE market=%s ORDER BY symbol", (market,))
    account = build_account(market, portfolio, positions)
    start = account.starting_capital
    return portfolio, positions, {
        "cash": account.cash,
        "invested": account.positions_value,
        "positions_value": account.positions_value,
        "equity": account.equity,
        "starting_balance": account.starting_capital,
        "return_pct": ((account.equity / start) - 1) * 100 if start else 0.0,
        "margin_debt": account.margin_debt,
        "buying_power": account.buying_power,
        "gross_exposure": account.gross_exposure,
        "leverage_limit": account.leverage_limit,
        "leverage_used": account.leverage_used,
        "margin_utilization_pct": account.margin_utilization_pct,
        "maintenance_requirement": account.maintenance_requirement,
        "excess_liquidity": account.excess_liquidity,
        "margin_call": 1.0 if account.margin_call else 0.0,
        "margin_interest_accrued": account.margin_interest_accrued,
    }


def latest_opportunities(limit: int = 100) -> list[dict[str, Any]]:
    records = safe_rows(
        """SELECT DISTINCT ON (market,symbol) market,symbol,rank,opportunity_score,payload,created_at
           FROM opportunity_rankings ORDER BY market,symbol,created_at DESC"""
    )
    return sorted(records, key=lambda x: as_float(x.get("opportunity_score")), reverse=True)[:limit]


def action_class(action: str) -> tuple[str, str]:
    action = action.upper()
    if action == "BUY": return "buy", "buy-text"
    if action == "SELL": return "sell", "sell-text"
    if action == "HOLD": return "hold", "hold-text"
    return "wait", "wait-text"


def plain_reason(raw: Any) -> tuple[list[str], list[str]]:
    """Translate dense engine output into simple supporting points and cautions."""
    text = str(raw or "").replace("\n", " ").strip()
    lower = text.lower()
    supports: list[str] = []
    cautions: list[str] = []
    if "momentum breakout" in lower:
        supports.append("Momentum breakout detected")
    if "trend continuation" in lower:
        supports.append("The price trend is still moving in the favorable direction")
    if "sector leadership" in lower:
        supports.append("The asset is showing leadership inside its sector")
    if "execution 9" in lower or "execution 8" in lower:
        supports.append("Market liquidity and execution conditions are acceptable")
    if "net ev" in lower:
        supports.append("The estimated reward remains greater than the modeled trading cost")
    if "historically cautionary" in lower or "win rate 0%" in lower:
        cautions.append("A small number of similar completed trades performed poorly")
    if "neutral global backdrop" in lower:
        cautions.append("Global conditions are neutral rather than strongly supportive")
    if "no completed historical analogs" in lower:
        cautions.append("There is not enough completed historical evidence yet")
    if not supports:
        supports.append("The combined market models currently support this decision")
    if not cautions:
        cautions.append("The recommendation can change if price, risk, or global conditions weaken")
    return supports[:3], cautions[:2]


def portfolio_status_label(score: Any) -> str:
    value = as_float(score)
    if value >= 75:
        return "Healthy"
    if value >= 50:
        return "Needs Attention"
    return "Needs Improvement"


def worker_live(record: dict[str, Any]) -> bool:
    if not worker_is_online(record.get("status")):
        return False
    age = status_age_seconds(record.get("heartbeat"))
    return age is not None and age <= LIVE_STATUS_STALE_SECONDS


def clean_trade_reason(value: Any) -> str:
    text = str(value or "").strip()
    mapping = {
        "trailing_stop": "Trailing risk exit",
        "take_profit": "Profit target reached",
        "stop_loss": "Protective stop reached",
        "paper_margin_reduction": "Automatic paper-margin reduction",
    }
    if text in mapping:
        return mapping[text]
    if text.startswith("Quant-approved buy") or text.startswith("Institutional paper buy"):
        return "Oracle-approved institutional paper entry"
    return text[:90] or "Oracle decision"


def clean_trade_frame(trades: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(trades)
    rename = {
        "created_at": "Date",
        "market": "Portfolio",
        "side": "Action",
        "symbol": "Symbol",
        "quantity": "Quantity",
        "price": "Price",
        "value": "Trade Value",
        "realized_pnl": "Profit / Loss",
        "reason": "Reason",
    }
    desired = [column for column in rename if column in frame.columns]
    view = frame[desired].rename(columns=rename)
    if "Date" in view.columns:
        parsed = pd.to_datetime(view["Date"], errors="coerce", utc=True)
        view["Date"] = parsed.dt.strftime("%b %d %H:%M UTC").fillna(view["Date"].astype(str))
    if "Portfolio" in view.columns:
        view["Portfolio"] = view["Portfolio"].map({"cash": "Stock", "crypto": "Crypto"}).fillna(view["Portfolio"])
    if "Action" in view.columns:
        view["Action"] = view["Action"].astype(str).str.upper()
    if "Reason" in view.columns:
        view["Reason"] = view["Reason"].map(clean_trade_reason)
    return view


def decision_card(item: dict[str, Any], compact: bool = False, simple: bool = False) -> None:
    action = str(item.get("action", "WAIT")).upper()
    expected = as_float(item.get("expected_return"))
    target = as_float(item.get("target"))
    price = as_float(item.get("price"))
    low = as_float(item.get("low"))
    confidence = as_float(item.get("confidence"))
    score = as_float(item.get("score"))
    risk = str(item.get("risk", "Unknown"))
    symbol = str(item.get("symbol", "")).upper()
    market_key = str(item.get("market", "")).lower()
    market = "Stock" if market_key == "cash" else "Crypto"
    supports, cautions = plain_reason(item.get("reason"))
    freshness = live_data_status(item)
    ready = bool(item.get("trade_eligible")) and not freshness["blocks_execution"]
    data_status = str(item.get("data_status") or freshness["detail"] or "Data status unavailable")
    if action in {"BUY", "STRONG_BUY", "ACCUMULATE", "LONG"} and not ready:
        action = "WATCH"
    color = {"BUY": "green", "STRONG_BUY": "green", "ACCUMULATE": "green", "LONG": "green", "SELL": "red", "HOLD": "blue", "WAIT": "orange", "WATCH": "orange"}.get(action, "orange")

    with st.container(border=True):
        if simple:
            summary = simple_opportunity_summary(item)
            data = summary["data"]
            st.markdown(f"### {html.escape(summary['symbol'])}")
            st.markdown(
                f"<div class='simple-status'>ORACLE SAYS: {html.escape(summary['action'])}</div>",
                unsafe_allow_html=True,
            )
            m1, m2 = st.columns(2)
            m1.metric("Price now", summary["price_now"])
            m2.metric("Oracle target", summary["target"])
            st.markdown("**Why?**")
            st.write(summary["why"])
            g1, g2 = st.columns(2)
            g1.metric("Possible gain", summary["possible_gain"])
            g2.metric("Risk", summary["risk"])
            if data["label"] == "LIVE DATA":
                st.success(f"{data['label']} - {data['detail']}")
            elif data["label"] == "DELAYED DATA":
                st.warning(f"{data['label']} - {data['detail']}")
            else:
                st.error(f"{data['label']} - {data['detail']}")
            with st.expander("Why does Oracle like this?"):
                left, right = st.columns(2)
                with left:
                    st.metric("Confidence", f"{confidence:.0f}%")
                    st.metric("Expected move", f"{expected:+.1f}%")
                with right:
                    st.metric("Trade quality", f"{score:.0f}/100")
                    st.metric("Market", market)
                st.markdown("**Supporting details**")
                for point in supports:
                    st.markdown(f"- {point}")
                st.markdown("**Cautions**")
                for point in cautions:
                    st.markdown(f"- {point}")
                st.caption(f"Technical data status: {data_status}")
            return
        st.markdown(f"### :{color}[{action} · {symbol}]")
        current_text = format_asset_price(price, symbol, market_key)
        target_text = format_asset_price(target, symbol, market_key)
        st.caption(f"{market} portfolio · Current {current_text} · Target {target_text}")
        if ready:
            st.success(f"Trade-ready data - {freshness['label']}: {freshness['detail']}")
        else:
            st.warning(f"Not trade-ready - {freshness['label']}: {freshness['detail']}")
        if not compact:
            left, right = st.columns(2)
            with left:
                st.metric("Confidence", f"{confidence:.0f}%")
                st.metric("Potential move", f"{expected:+.1f}%")
            with right:
                st.metric("Trade quality", f"{score:.0f}/100")
                st.metric("Risk", risk)
            st.markdown("**Why this decision**")
            for point in supports:
                st.markdown(f"✅ {point}")
            st.markdown("**What to watch**")
            for point in cautions:
                st.markdown(f"Warning: {point}")
            reference = format_asset_price(price, symbol, market_key)
            stop_text = format_asset_price(low, symbol, market_key)
            st.caption(f"Entry reference: {reference} · Target: {target_text} · Protective level: {stop_text}")


def money_tiles(summary: dict[str, str]) -> None:
    st.markdown(
        "<div class='money-grid'>"
        + "".join(
            f"<div class='money-tile'><small>{html.escape(label)}</small><b>{html.escape(value)}</b></div>"
            for label, value in summary.items()
            if label != "sentence"
        )
        + "</div>",
        unsafe_allow_html=True,
    )
    st.markdown(f"### {html.escape(summary['sentence'])}")


def render_money_bar(items: list[dict[str, str]]) -> None:
    st.markdown(
        "<div class='money-grid'>"
        + "".join(
            f"<div class='money-tile'><small>{html.escape(item['label'])}</small><b>{html.escape(item['value'])}</b></div>"
            for item in items
        )
        + "</div>",
        unsafe_allow_html=True,
    )


def render_trade_history_section(trades: list[dict[str, Any]], key_prefix: str) -> None:
    f1, f2, f3 = st.columns(3)
    with f1:
        trade_market_filter = st.selectbox("Portfolio", ["All", "Stock", "Crypto"], key=f"{key_prefix}-market")
    with f2:
        trade_side_filter = st.selectbox("Bought / Sold", ["All", "Buy", "Sell"], key=f"{key_prefix}-side")
    with f3:
        trade_symbol_filter = st.text_input("Symbol", key=f"{key_prefix}-symbol").upper().strip()
    if trade_market_filter != "All":
        wanted_market = "cash" if trade_market_filter == "Stock" else "crypto"
        trades = [trade for trade in trades if str(trade.get("market") or "").lower() == wanted_market]
    if trade_side_filter != "All":
        trades = [trade for trade in trades if str(trade.get("side") or "").upper() == trade_side_filter.upper()]
    if trade_symbol_filter:
        trades = [trade for trade in trades if trade_symbol_filter in str(trade.get("symbol") or "").upper()]
    trades = trades[:50]
    if not trades:
        st.info("No trade history has been recorded.")
        return
    summary = trade_summary(trades)
    s1, s2, s3, s4, s5 = st.columns(5)
    s1.metric("Total Trades", summary["Total Trades"])
    s2.metric("Buys", summary["Buys"])
    s3.metric("Sells", summary["Sells"])
    s4.metric("Realized P/L", money(summary["Realized P/L"]))
    s5.metric("Trade Volume", money(summary["Trade Volume"]))
    readable = pd.DataFrame(readable_trade_rows(trades))
    simple_columns = ["Date", "Bought / Sold", "Asset", "Price", "Money Used", "Profit / Loss"]
    st.dataframe(readable[simple_columns], width="stretch", hide_index=True)
    with st.expander("Show Trade Details"):
        detail_columns = ["Date", "Bought / Sold", "Asset", "Quantity", "Price", "Money Used", "Profit / Loss", "Reason", "Trade Value Arithmetic"]
        st.dataframe(readable[detail_columns], width="stretch", hide_index=True)


def render_global_pit_section() -> None:
    st.markdown("<div class='section-title'>MARKET FOCUS</div>", unsafe_allow_html=True)
    stock_queue = [row for row in global_pit_queue if str(row.get("market") or "cash").lower() in {"cash", "stock", ""}]
    ledger_rows = safe_rows("SELECT * FROM trade_ledger WHERE market='cash' ORDER BY id DESC LIMIT 100")
    focus = wall_street_market_focus(stock_queue, stock_positions, ledger_rows)
    stock_worker = next((record for record in workers if record.get("market") == "cash"), {})
    today_stock_pnl = sum(as_float(trade.get("realized_pnl")) for trade in recent_trades if str(trade.get("market") or "").lower() == "cash")
    market_regime = "NEUTRAL"
    for row_data in stock_queue:
        candidate_regime = str(row_data.get("market_regime") or "").upper()
        if candidate_regime in {"RISK ON", "RISK OFF", "HIGH VOLATILITY", "NEUTRAL"}:
            market_regime = candidate_regime
            break
    c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
    c1.metric("U.S. Market Regime", market_regime)
    c2.metric("Stock Session", stock_worker.get("session_label") or stock_worker.get("status") or "Waiting")
    c3.metric("Today's Stock P/L", money(today_stock_pnl))
    c4.metric("Stock Capital Invested", money(stock_metrics["invested"]))
    c5.metric("Stock Cash Available", money(stock_metrics["cash"]))
    c6.metric("Open Stock Positions", len(stock_positions))
    c7.metric("Qualified Trades Now", len(focus["best_trades"]))
    st.caption("Market Focus answers one question: what are the best verified Wall Street stock and ETF trades right now?")

    stock_entry_policy = execution_policy(market="cash", intent="entry")
    stock_exit_policy = execution_policy(market="cash", intent="exit")
    stock_rotation_policy = execution_policy(market="cash", intent="rotation")
    broker_policy = execution_policy(market="cash", intent="broker")
    activity = dashboard_activity_labels({
        **global_pit_activity,
        "qualified_allocations": len(focus["best_trades"]),
        "execution_enabled": stock_entry_policy.allowed,
    })

    st.markdown("**BEST WALL STREET TRADES NOW**")
    if focus["best_trades"]:
        st.dataframe(pd.DataFrame(focus["best_trades"]), width="stretch", hide_index=True)
    else:
        st.info("No U.S. stock or ETF has passed verified quote, liquidity, risk, and portfolio checks right now.")

    st.markdown("**STRONGEST VERIFIED MOVERS**")
    st.dataframe(pd.DataFrame(focus["movers"]), width="stretch", hide_index=True) if focus["movers"] else st.info("No liquid verified stock movers are available yet.")

    st.markdown("**ORACLE ACTION QUEUE**")
    st.dataframe(pd.DataFrame(focus["action_queue"]), width="stretch", hide_index=True) if focus["action_queue"] else st.info("No stock action is queued.")

    st.markdown("**WHAT THE ORACLE OWNS**")
    st.dataframe(pd.DataFrame(focus["owned"]), width="stretch", hide_index=True) if focus["owned"] else st.info("No open stock positions are currently recorded.")

    st.markdown("**ROTATION OPPORTUNITIES**")
    rotation_rows = [
        {
            "Current Holding": item.get("current_holding") or "",
            "Holding Score": item.get("holding_score") or "",
            "Incoming Candidate": item.get("incoming_candidate") or item.get("symbol") or "",
            "Candidate Score": item.get("candidate_score") or item.get("opportunity_score") or "",
            "Score Improvement": item.get("score_improvement") or "",
            "Recommended Action": item.get("recommended_action") or "WATCH",
        }
        for item in focus["rotations"]
    ]
    st.dataframe(pd.DataFrame(rotation_rows), width="stretch", hide_index=True) if rotation_rows else st.info("No verified stock rotation currently clears the improvement threshold and execution checks.")

    st.markdown("**HOW STOCK PROFITS WERE CREATED**")
    st.dataframe(pd.DataFrame(focus["profit_sources"]), width="stretch", hide_index=True) if focus["profit_sources"] else st.info("Stock profit attribution will appear after ledgered paper fills close or verified open marks are available.")

    st.markdown("**LEADING WALL STREET SECTORS**")
    st.dataframe(pd.DataFrame(focus["sectors"]), width="stretch", hide_index=True) if focus["sectors"] else st.info("Sector leadership will appear when qualified Wall Street candidates are available.")

    with st.expander("REJECTED / WAITING"):
        st.dataframe(pd.DataFrame(focus["rejected"]), width="stretch", hide_index=True) if focus["rejected"] else st.success("No rejected stock candidates are currently in the focus view.")

    with st.expander("Provider Diagnostics"):
        st.caption("Provider diagnostics are secondary and shown here only to explain trade-impacting data limits. They do not override quote verification.")
        policy_rows = [
            {"Switch": "Stock Entries", "Enabled": stock_entry_policy.allowed, "Reason": stock_entry_policy.reason},
            {"Switch": "Stock Exits", "Enabled": stock_exit_policy.allowed, "Reason": stock_exit_policy.reason},
            {"Switch": "Stock Rotation", "Enabled": stock_rotation_policy.allowed, "Reason": stock_rotation_policy.reason},
            {"Switch": "Broker Submission", "Enabled": broker_policy.allowed, "Reason": broker_policy.reason},
        ]
        st.dataframe(pd.DataFrame(policy_rows), width="stretch", hide_index=True)
        st.dataframe(pd.DataFrame([{"Activity": key, "Status": value} for key, value in activity.items()]), width="stretch", hide_index=True)
        diagnostics = provider_diagnostics()
        st.dataframe(pd.DataFrame(diagnostics), width="stretch", hide_index=True) if diagnostics else st.info("No provider limitations are currently reported.")


def simple_portfolio_card(
    name: str,
    metrics: dict[str, Any],
    positions: list[dict[str, Any]],
    health: Any,
    scores: dict[str, Any],
) -> None:
    with st.container(border=True):
        st.markdown(f"### {name.upper()} PORTFOLIO")
        st.markdown("**How am I doing?**")
        st.markdown(f"<div class='simple-status'>{html.escape(str(scores['status']))}</div>", unsafe_allow_html=True)
        a, b = st.columns(2)
        a.metric("Money invested", money_text(scores["money_invested"], whole=True))
        b.metric("Cash waiting", money_text(scores["cash_waiting"], whole=True))
        c, d = st.columns(2)
        c.metric("How many investments", int(scores["position_count"]))
        pnl = as_float(scores["profit_loss"])
        d.metric("Profit/Loss", f"{'+' if pnl >= 0 else '-'}{money_text(abs(pnl), whole=True)}")
        st.write(scores["explanation"])
        s1, s2 = st.columns(2)
        s1.metric("Safety", scores["safety"])
        s2.metric("Diversification", scores["diversification"])
        s3, s4 = st.columns(2)
        s3.metric("Money Use", scores["capital_use"])
        s4.metric("Opportunity", scores["opportunity"])
        with st.expander("Show Advanced Details"):
            p1, p2 = st.columns(2)
            p1.metric("Health score", f"{health.health_score}/100")
            p2.metric("Original risk label", health.risk_label)
            p3, p4 = st.columns(2)
            p3.metric("Leverage", f"{health.leverage_used:.2f}x")
            p4.metric("Holdings", health.position_count)
            st.write(health.plain_summary)
            st.write(
                f"Margin debt: {money(metrics['margin_debt'])} · Gross exposure: {money(metrics['gross_exposure'])} · "
                f"Maintenance requirement: {money(metrics['maintenance_requirement'])}."
            )
            st.write(
                f"Safety Score {scores['safety_score']}/100 · Diversification Score {scores['diversification_score']}/100 · "
                f"Opportunity Score {scores['opportunity_score']}/100 · Data Quality Score {scores['data_quality_score']}/100 · "
                f"Overall Portfolio Score {scores['overall_score']}/100."
            )
            st.caption(scores["overall_explanation"])


def portfolio_table(positions: list[dict[str, Any]], market: str = "") -> pd.DataFrame:
    if market == "crypto":
        equity = sum(as_float(p.get("quantity")) * as_float(p.get("current_price") or p.get("price")) for p in positions)
        return pd.DataFrame(
            [
                {
                    "Symbol": str(p.get("symbol") or "").upper(),
                    "Bucket": p.get("bucket") or ("Core" if str(p.get("symbol") or "").upper() in {"BTC-USD", "ETH-USD", "XRP-USD", "SOL-USD", "BNB-USD", "DOGE-USD", "ADA-USD", "AVAX-USD", "LINK-USD"} else "Tactical"),
                    "Quantity": format_quantity(p.get("quantity")),
                    "Avg Cost": as_float(p.get("average_price") or p.get("entry_price")),
                    "Current Price": as_float(p.get("current_price") or p.get("price")),
                    "Market Value": as_float(p.get("quantity")) * as_float(p.get("current_price") or p.get("price")),
                    "P/L $": (as_float(p.get("current_price") or p.get("price")) - as_float(p.get("average_price") or p.get("entry_price"))) * as_float(p.get("quantity")),
                    "P/L %": (((as_float(p.get("current_price") or p.get("price")) / as_float(p.get("average_price") or p.get("entry_price"))) - 1) * 100 if as_float(p.get("average_price") or p.get("entry_price")) > 0 else 0.0),
                    "Weight": ((as_float(p.get("quantity")) * as_float(p.get("current_price") or p.get("price"))) / equity * 100 if equity else 0.0),
                    "Tier": p.get("tier") or "",
                    "Strategy": p.get("strategy") or "",
                    "Provider": p.get("quote_provider") or p.get("provider") or "Unknown",
                    "Data Status": "VERIFIED" if p.get("quote_verified") is True else "NEEDS PRICE",
                    "Entry Reason": p.get("entry_reason") or p.get("reason") or "",
                    "Hold Reason": p.get("hold_reason") or "Open position",
                }
                for p in positions
                if str(p.get("market") or "crypto").lower() == "crypto" or str(p.get("symbol") or "").upper().endswith("-USD")
            ]
        )
    equity = sum(as_float(p.get("quantity")) * as_float(p.get("current_price") or p.get("price")) for p in positions)
    stock_rows = holding_view_rows(positions, equity=equity)
    if stock_rows:
        return pd.DataFrame(
            [
                {
                    "Symbol": item["symbol"],
                    "Company": item["name"],
                    "Bucket": item["bucket"],
                    "Shares": item["shares"],
                    "Avg Cost": item["avg_cost"],
                    "Current Price": item["current_price"],
                    "Market Value": item["market_value"],
                    "P/L $": item["unrealized_pnl"],
                    "P/L %": item["unrealized_pnl_pct"],
                    "Weight": item["portfolio_weight_pct"],
                    "Sector": item["sector"] or "Unknown",
                    "Tier": item["trade_tier"] or "",
                    "Strategy": item["strategy"] or "",
                    "Provider": item["quote_provider"] or "Unknown",
                    "Data Status": "VERIFIED" if item["quote_verified"] else "NEEDS PRICE",
                    "Entry Reason": next((p.get("entry_reason") or p.get("reason") or "" for p in positions if str(p.get("symbol") or "").upper() == item["symbol"]), ""),
                    "Hold Reason": next((p.get("hold_reason") or "Open position" for p in positions if str(p.get("symbol") or "").upper() == item["symbol"]), "Open position"),
                }
                for item in stock_rows
            ]
        )
    data = []
    for p in positions:
        qty = as_float(p.get("quantity"))
        avg = as_float(p.get("average_price") or p.get("entry_price"))
        current = as_float(p.get("current_price"))
        value = qty * current
        pnl = (current - avg) * qty
        pnl_pct = ((current / avg) - 1) * 100 if avg else 0.0
        data.append({
            "Symbol": p.get("symbol"), "Quantity": round(qty, 6), "Average Cost": avg,
            "Current Price": current, "Current Value": value, "Gain/Loss": pnl,
            "Return %": pnl_pct, "Opened": p.get("opened_at"),
        })
    return pd.DataFrame(data)


bootstrap_failed = False
try:
    bootstrap()
except RuntimeError as exc:
    bootstrap_failed = True
    st.error("Database temporarily unavailable")
    st.info("GARIBALDI MARKET ORACLE is waiting for PostgreSQL to recover. Trading execution remains disabled.")
    st.caption(str(exc).replace("postgresql://", "[database-url-redacted]"))
    if st.button("Retry database connection"):
        bootstrap.clear()
        st.rerun()
    st.stop()
except Exception as exc:
    health = database_ready()
    if not health.get("ok"):
        bootstrap_failed = True
        st.error("Database temporarily unavailable")
        st.info("GARIBALDI MARKET ORACLE is waiting for PostgreSQL to recover. Trading execution remains disabled.")
        st.caption(str(health.get("message") or "Database connection failed"))
        if st.button("Retry database connection"):
            bootstrap.clear()
            st.rerun()
        st.stop()
    raise
stock_portfolio, stock_positions, stock_metrics = get_portfolio("cash")
crypto_portfolio, crypto_positions, crypto_metrics = get_portfolio("crypto")
all_positions = stock_positions + crypto_positions
signals = safe_rows("SELECT * FROM signals ORDER BY id DESC LIMIT 400")
forecasts = safe_rows("SELECT * FROM forecasts ORDER BY id DESC LIMIT 400")
opportunities = latest_opportunities(100)
decisions = build_decisions(opportunities, signals, forecasts, 100)
alerts = safe_rows("SELECT * FROM alerts WHERE acknowledged=0 ORDER BY id DESC LIMIT 40")
events = safe_rows("SELECT * FROM intelligence_events ORDER BY id DESC LIMIT 100")
workers = safe_rows("SELECT * FROM market_worker_status ORDER BY market")
recent_trades = safe_rows("SELECT * FROM trades ORDER BY id DESC LIMIT 50")
global_pit_universe = safe_rows("SELECT * FROM global_financial_universe ORDER BY updated_at DESC LIMIT 500")
global_pit_queue = safe_rows("SELECT * FROM global_opportunity_queue ORDER BY opportunity_score DESC, updated_at DESC LIMIT 50")
global_pit_activity = safe_row("SELECT * FROM global_pit_activity WHERE id=1")
global_pit_map = safe_rows("SELECT * FROM global_market_map ORDER BY strength_score DESC LIMIT 25")
global_learning = safe_rows("SELECT * FROM global_learning_observations ORDER BY id DESC LIMIT 10")
global_decisions_v39 = safe_rows("SELECT * FROM global_decision_ledger ORDER BY created_at DESC LIMIT 250")
global_decision_events_v39 = safe_rows("SELECT * FROM global_decision_events ORDER BY created_at DESC LIMIT 500")
provider_budgets_v39 = safe_rows("SELECT * FROM provider_budget_ledger ORDER BY updated_at DESC LIMIT 50")

stock_health = analyze_portfolio(
    stock_metrics["cash"], stock_positions, stock_metrics["margin_debt"],
    stock_metrics["leverage_limit"], stock_metrics["buying_power"],
)
crypto_health = analyze_portfolio(
    crypto_metrics["cash"], crypto_positions, crypto_metrics["margin_debt"],
    crypto_metrics["leverage_limit"], crypto_metrics["buying_power"],
)
combined_equity = stock_metrics["equity"] + crypto_metrics["equity"]
combined_start = stock_metrics["starting_balance"] + crypto_metrics["starting_balance"]
combined_return = ((combined_equity / combined_start) - 1) * 100 if combined_start else 0.0
combined_buying_power = stock_metrics["buying_power"] + crypto_metrics["buying_power"]
combined_margin_debt = stock_metrics["margin_debt"] + crypto_metrics["margin_debt"]
ready_decisions = [d for d in decisions if bool(d.get("trade_eligible")) and not live_data_status(d)["blocks_execution"]]
buy_decisions = [d for d in ready_decisions if d["action"] == "BUY"]
sell_decisions = [d for d in ready_decisions if d["action"] == "SELL"]
waiting_for_data = [d for d in decisions if live_data_status(d)["blocks_execution"] or not bool(d.get("trade_eligible"))]

with st.sidebar:
    st.markdown("## GARIBALDI ORACLE")
    st.caption("The AI Chief Investment Officer")
    page = st.radio(
        "Main navigation",
        ["Dashboard", "Market Focus", "Crypto", "Markets", "Portfolios", "Oracle", "Intelligence", "Professional"],
        label_visibility="collapsed",
    )
    st.divider()
    st.caption("Live worker status")
    for market in ("cash", "crypto"):
        record = next((r for r in workers if r.get("market") == market), {})
        online = worker_live(record)
        label = "Stock" if market == "cash" else "Crypto"
        age = status_age_seconds(record.get("last_pulse"))
        fast_age = status_age_seconds(record.get("last_fast_scan"))
        pulse_text = f"{int(age)}s ago" if age is not None else "waiting"
        fast_text = f"{int(fast_age)}s ago" if fast_age is not None else "starting"
        st.write(
            f"{'🟢' if online else '🔴'} **{label}** — "
            f"{record.get('session_label') or record.get('status','waiting')} · "
            f"heartbeat {pulse_text} · fast scan {fast_text}"
        )
    st.caption(f"Auto-refresh: {'ON' if UI_AUTO_REFRESH else 'OFF'} · every {UI_REFRESH_SECONDS}s")
    if st.button("Refresh market data", width="stretch"):
        st.cache_data.clear()
        st.rerun()

hero_eyebrow = "Balanced AI Investing Dashboard"
hero_text = "Professional paper-investing dashboard with plain-language decisions, compact portfolio views, and deeper analytics available inside each section."

st.markdown(
    f"""
<div class='hero'>
  <div class='eyebrow'>{html.escape(hero_eyebrow)}</div>
  <h1>{html.escape(APP_NAME)}</h1>
  <p>{html.escape(hero_text)}</p>
</div>
""",
    unsafe_allow_html=True,
)

live_records = [record for record in workers if worker_live(record)]
mode_label = "LIVE PAPER SIMULATION ENGINE" if EXECUTION_MODE == "paper" and PAPER_BROKER_MODE else ("PAPER EXECUTION ENGINE" if EXECUTION_MODE == "paper" else f"LIVE {EXECUTION_MODE.upper()} EXECUTION")
show_advanced_chrome = page != "Dashboard"
if len(live_records) == 2 and show_advanced_chrome:
    always_text = "ALWAYS-ON" if ALWAYS_ON_TRADING else "CONTINUOUS MONITORING"
    st.success(
        f"🟢 {mode_label} · {always_text} · rolling fast scans, deep research, "
        "position monitoring, qualified entries, rotations, and risk exits are active."
    )
elif live_records and show_advanced_chrome:
    st.warning(f"🟠 {mode_label} · only {len(live_records)} of 2 workers currently has a fresh heartbeat.")
elif show_advanced_chrome:
    st.error("🔴 Live workers are not reporting fresh heartbeats. Check both Railway worker services.")

latest_trade = recent_trades[0] if recent_trades else {}
error_total = sum(int(as_float(record.get("cycle_errors"))) for record in workers)
if show_advanced_chrome:
    status_cols = st.columns(4)
    for index, market in enumerate(("cash", "crypto")):
        record = next((r for r in workers if r.get("market") == market), {})
        label = "Stock engine" if market == "cash" else "Crypto engine"
        age = status_age_seconds(record.get("last_fast_scan"))
        value = f"{int(age)}s ago" if age is not None else "Starting"
        status_cols[index].metric(
            label,
            value,
            f"Fast {record.get('fast_scan_seconds') or '—'}s · Deep {record.get('deep_scan_seconds') or '—'}s",
        )
    trade_label = f"{str(latest_trade.get('side','')).upper()} {latest_trade.get('symbol','')}".strip() or "No trade yet"
    status_cols[2].metric(
        "Latest execution",
        trade_label,
        clean_trade_reason(latest_trade.get("reason")) if latest_trade else "Scanning for a qualified trade",
    )
    status_cols[3].metric("Auto recovery", "Ready" if error_total == 0 else "Recovering", f"Cycle errors: {error_total}")

if page == "Market Focus":
    render_global_pit_section()

elif page == "Crypto":
    st.markdown("<div class='section-title'>CRYPTO SUMMARY</div>", unsafe_allow_html=True)
    crypto_queue = [row for row in global_pit_queue if str(row.get("market") or "").lower() == "crypto" or str(row.get("asset_class") or "").lower() == "crypto" or str(row.get("symbol") or "").upper().endswith("-USD")]
    crypto_ledger = safe_rows("SELECT * FROM trade_ledger WHERE market='crypto' ORDER BY id DESC LIMIT 100")
    crypto_focus = crypto_page_sections(crypto_queue, crypto_positions, crypto_ledger, crypto_metrics)
    summary_cols = st.columns(6)
    for index, (label, value) in enumerate(list(crypto_focus["summary"].items())[:6]):
        summary_cols[index].metric(label, value)
    st.caption("Crypto stays in paper mode and uses verified 24/7 quotes, liquidity, tier sizing, reserve protection, and core/tactical separation.")

    st.markdown("**WHAT I OWN NOW**")
    st.dataframe(pd.DataFrame(crypto_focus["owned"]), width="stretch", hide_index=True) if crypto_focus["owned"] else st.info("No crypto positions are currently recorded.")

    st.markdown("**BEST CRYPTO TRADES NOW**")
    st.dataframe(pd.DataFrame(crypto_focus["best_trades"]), width="stretch", hide_index=True) if crypto_focus["best_trades"] else st.info("No crypto trade has passed quote, liquidity, signal, tier, and portfolio checks right now.")

    st.markdown("**STRONGEST CRYPTO MOVERS**")
    st.dataframe(pd.DataFrame(crypto_focus["movers"]), width="stretch", hide_index=True) if crypto_focus["movers"] else st.info("No verified crypto movers are available yet.")

    st.markdown("**ROTATION OPPORTUNITIES**")
    st.dataframe(pd.DataFrame(crypto_focus["rotations"]), width="stretch", hide_index=True) if crypto_focus["rotations"] else st.info("No tactical crypto rotation clears the required score improvement.")

    st.markdown("**HOW CRYPTO PROFITS WERE CREATED**")
    st.dataframe(pd.DataFrame(crypto_focus["profit_sources"]), width="stretch", hide_index=True) if crypto_focus["profit_sources"] else st.info("Crypto profit attribution will appear after ledgered paper fills close or verified open marks are available.")

    st.markdown("**CORE ALLOCATION**")
    st.dataframe(pd.DataFrame(crypto_focus["core_allocation"]), width="stretch", hide_index=True)

    st.markdown("**CORE DEPLOYMENT PLAN**")
    st.dataframe(pd.DataFrame(crypto_focus["core_deployment"]), width="stretch", hide_index=True) if crypto_focus["core_deployment"] else st.info("No verified underweight crypto core allocation is available above the protected reserve right now.")

    with st.expander("WATCHLIST / WAITING"):
        st.dataframe(pd.DataFrame(crypto_focus["waiting"]), width="stretch", hide_index=True) if crypto_focus["waiting"] else st.success("No crypto candidates are waiting on data or liquidity right now.")
    with st.expander("PROVIDER DIAGNOSTICS"):
        st.caption("Provider diagnostics do not override verified quote requirements.")
        diagnostics = provider_diagnostics()
        st.dataframe(pd.DataFrame(diagnostics), width="stretch", hide_index=True) if diagnostics else st.info("No provider limitations are currently reported.")

elif page == "Dashboard":
    top = buy_decisions[0] if buy_decisions else (decisions[0] if decisions else None)
    stock_scores = simple_portfolio_scores(stock_metrics, stock_positions, len([d for d in buy_decisions if d.get("market") == "cash"]))
    crypto_scores = simple_portfolio_scores(crypto_metrics, crypto_positions, len([d for d in buy_decisions if d.get("market") == "crypto"]))
    combined_metrics = {
        "starting_balance": combined_start,
        "equity": combined_equity,
        "cash": stock_metrics["cash"] + crypto_metrics["cash"],
        "invested": stock_metrics["invested"] + crypto_metrics["invested"],
    }

    render_money_bar(balanced_money_bar(combined_metrics, recent_trades))

    st.markdown("<div class='section-title'>ORACLE RIGHT NOW</div>", unsafe_allow_html=True)
    if top:
        summary = simple_opportunity_summary(top)
        freshness = live_data_status(top)
        blocked_buy_setups = [d for d in decisions if d.get("action") == "BUY" and live_data_status(d)["blocks_execution"]]
        oracle_state = "BUYING OPPORTUNITIES READY" if buy_decisions else "WATCHING THE BEST SETUP"
        status_class = "green-text" if buy_decisions else "yellow-text"
        if blocked_buy_setups and not buy_decisions:
            oracle_state = "SETUPS FOUND - WAITING FOR FRESH QUOTES"
        if sell_decisions and not buy_decisions:
            oracle_state = "RISK ACTIONS FOUND"
            status_class = "red-text"
        st.markdown(
            "<div class='oracle-strip'>"
            f"<div class='simple-status {status_class}'>{html.escape(oracle_state)}</div>"
            "<div class='grid'>"
            f"<div class='summary-cell'><small class='muted'>Best opportunity</small><b>{html.escape(summary['symbol'])}</b></div>"
            f"<div class='summary-cell'><small class='muted'>Confidence</small><b>{as_float(top.get('confidence')):.0f}%</b></div>"
            f"<div class='summary-cell'><small class='muted'>Possible move</small><b>{as_float(top.get('expected_return')):+.1f}%</b></div>"
            f"<div class='summary-cell'><small class='muted'>Risk</small><b>{html.escape(summary['risk'])}</b></div>"
            "</div>"
            f"<p class='muted'>Data: {html.escape(freshness['label'])} - {html.escape(freshness['detail'])}</p>"
            f"<p class='reason'>{html.escape(summary['why'])}</p>"
            "</div>",
            unsafe_allow_html=True,
        )
    else:
        st.info("Oracle right now: WAITING FOR BETTER SETUPS. No investment has passed every safety check yet.")

    st.markdown("<div class='section-title'>TOP OPPORTUNITIES</div>", unsafe_allow_html=True)
    table_decisions = buy_decisions[:10] if buy_decisions else decisions[:10]
    if table_decisions:
        opportunity_rows = pd.DataFrame(balanced_opportunity_rows(table_decisions, limit=10))

        def _style_action(row: pd.Series) -> list[str]:
            action = str(row.get("Action") or "")
            if action.startswith("GREEN"):
                color = "color: #39ff88; font-weight: 900"
            elif action.startswith("RED"):
                color = "color: #ff4d5a; font-weight: 900"
            else:
                color = "color: #ffd84d; font-weight: 900"
            return [color if column == "Action" else "" for column in row.index]

        st.dataframe(opportunity_rows.style.apply(_style_action, axis=1), width="stretch", hide_index=True)
        labels = [f"{index + 1}. {item.get('symbol')} - {item.get('action')}" for index, item in enumerate(table_decisions)]
        selected_label = st.selectbox("Opportunity details", labels, key="balanced-opportunity-details")
        selected = table_decisions[labels.index(selected_label)]
        with st.expander("Details"):
            supports, cautions = plain_reason(selected.get("reason"))
            d1, d2, d3, d4 = st.columns(4)
            d1.metric("Forecast", f"{as_float(selected.get('expected_return')):+.1f}%")
            d2.metric("Technical Score", f"{as_float(selected.get('score')):.0f}/100")
            d3.metric("Provider", str(selected.get("provider") or selected.get("quote_provider") or "Mixed"))
            d4.metric("Model", str(selected.get("model") or selected.get("model_version") or "Oracle"))
            st.markdown("**Reasons to consider it**")
            for point in supports:
                st.write(f"- {point}")
            st.markdown("**Risk notes**")
            for point in cautions:
                st.write(f"- {point}")
            st.caption(f"Data status: {selected.get('data_status') or live_data_status(selected)['detail']}")
    else:
        st.info("No current opportunities are available yet.")

    st.markdown("<div class='section-title'>WHAT I OWN</div>", unsafe_allow_html=True)
    all_positions = stock_positions + crypto_positions
    holdings_frame = pd.DataFrame(balanced_portfolio_rows(all_positions, combined_equity))
    if holdings_frame.empty:
        st.info("No open positions are currently recorded.")
    else:
        left, right = st.columns([1.4, 1])
        with left:
            st.dataframe(holdings_frame, width="stretch", hide_index=True)
        with right:
            allocation_data = []
            for position in all_positions:
                value = as_float(position.get("quantity")) * as_float(position.get("current_price") or position.get("price"))
                if value > 0:
                    allocation_data.append({"Symbol": str(position.get("symbol") or "").upper(), "Value": value})
            if allocation_data:
                fig = px.pie(pd.DataFrame(allocation_data), names="Symbol", values="Value", hole=0.45)
                fig.update_layout(margin=dict(l=0, r=0, t=0, b=0), height=320, showlegend=True)
                st.plotly_chart(fig, width="stretch")

    st.markdown("<div class='section-title'>PORTFOLIO HEALTH</div>", unsafe_allow_html=True)
    h1, h2, h3, h4 = st.columns(4)
    h1.metric("Safety", stock_scores["safety"] if stock_scores["safety_score"] <= crypto_scores["safety_score"] else crypto_scores["safety"])
    h2.metric("Diversification", stock_scores["diversification"] if stock_scores["diversification_score"] <= crypto_scores["diversification_score"] else crypto_scores["diversification"])
    cash_pct = (combined_metrics["cash"] / combined_equity * 100.0) if combined_equity else 0.0
    h3.metric("Cash Use", "HIGH CASH" if cash_pct >= 60 else "LOW CASH" if cash_pct <= 8 else "BALANCED")
    h4.metric("Data Quality", "GOOD" if not waiting_for_data else "NEEDS CHECK")
    with st.expander("Details"):
        detail_scores = pd.DataFrame([{ "Portfolio": "Stock", **stock_scores }, { "Portfolio": "Crypto", **crypto_scores }])
        st.dataframe(detail_scores, width="stretch", hide_index=True)
        st.caption("Scores are supporting diagnostics. The main dashboard uses plain status labels.")

    st.markdown("<div class='section-title'>HOW ORACLE WOULD USE AVAILABLE CASH</div>", unsafe_allow_html=True)
    stock_plan = simple_portfolio_builder_plan(stock_metrics["cash"], stock_metrics["equity"], [d for d in buy_decisions if d.get("market") == "cash"], stock_positions)
    crypto_plan = simple_portfolio_builder_plan(crypto_metrics["cash"], crypto_metrics["equity"], [d for d in buy_decisions if d.get("market") == "crypto"], crypto_positions)
    plan_rows = []
    for market_name, plan in (("Stock", stock_plan), ("Crypto", crypto_plan)):
        for item in plan:
            plan_rows.append({"Portfolio": market_name, "Symbol": item["symbol"], "Amount": money_text(item["amount"], whole=True), "Why": item["reason"]})
    st.dataframe(pd.DataFrame(plan_rows), width="stretch", hide_index=True)
    with st.expander("Details"):
        st.caption("This is a planning view only. It does not place trades. Stale or unverified opportunities are excluded before allocation.")
        st.write("The planner considers opportunity score, confidence, expected return, risk, current exposure, cash reserve, duplicate exposure, concentration, and data freshness. The paper execution path still rechecks every hard safeguard before any simulated order.")

    st.markdown("<div class='section-title'>CAPITAL ALLOCATION</div>", unsafe_allow_html=True)
    allocation_rows = capital_allocation_rows(
        buy_decisions[:8],
        combined_metrics,
        stock_positions + crypto_positions,
        market="crypto" if buy_decisions and str(buy_decisions[0].get("market") or "").lower() == "crypto" else "cash",
    )
    st.dataframe(pd.DataFrame(allocation_rows), width="stretch", hide_index=True)

    st.markdown("<div class='section-title'>TRADE HISTORY</div>", unsafe_allow_html=True)
    dashboard_trades = safe_rows("SELECT * FROM trades ORDER BY id DESC LIMIT 500")
    render_trade_history_section(dashboard_trades, "dashboard-trade-history")

    st.markdown("<div class='section-title'>DATA STATUS</div>", unsafe_allow_html=True)
    try:
        diagnostics = provider_diagnostics()
    except Exception:
        diagnostics = []
    status_rows = balanced_data_status(
        any(worker_live(record) for record in workers if record.get("market") == "cash"),
        any(worker_live(record) for record in workers if record.get("market") == "crypto"),
        diagnostics,
    )
    st.markdown(
        "<div class='status-bar'>"
        + "".join(
            f"<div class='status-chip'>{html.escape(row['Area'])}<span class='{row['Status'].lower()}-text'>{html.escape(row['Status'])}</span></div>"
            for row in status_rows
        )
        + "</div>",
        unsafe_allow_html=True,
    )
    with st.expander("Details"):
        st.caption("Provider limitations are diagnostics only and do not override quote freshness or execution safeguards.")
        alpha_rows = [row for row in diagnostics if str(row.get("provider") or "").lower() in {"alpha vantage", "alpha_vantage_api_key"}]
        if alpha_rows:
            alpha = alpha_rows[0]
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Alpha Vantage", str(alpha.get("status") or "Unknown").upper())
            c2.metric("Requests Today", f"{int(as_float(alpha.get('requests')))} / {int(as_float(alpha.get('daily_budget')))}")
            c3.metric("Daily Remaining", int(as_float(alpha.get("daily_remaining"))))
            c4.metric("Mode", str(alpha.get("mode") or "Historical / EOD / Delayed"))
            st.caption(f"Last Success: {alpha.get('last_success') or 'Waiting'}")
        st.dataframe(pd.DataFrame(diagnostics), width="stretch", hide_index=True) if diagnostics else st.info("No provider limitations are currently reported.")

    if alerts:
        st.markdown("<div class='section-title'>WHAT NEEDS ATTENTION</div>", unsafe_allow_html=True)
        for alert in alerts[:5]:
            st.warning(f"{alert.get('title','Alert')}: {alert.get('message','')}")

    advisor_context = st.expander("Advisor And System Details")
    with advisor_context:
        st.caption("Paper simulation diagnostics remain available here. Execution switches stay disabled unless deliberately configured.")
        a1, a2, a3, a4 = st.columns(4)
        a1.metric("Broker Equity", compact_money(combined_equity), pct(combined_return))
        a2.metric("Available Buying Power", compact_money(combined_buying_power))
        a3.metric("Trade-ready Buys", len(buy_decisions), f"{len(waiting_for_data)} waiting on data")
        a4.metric("Margin Debt", compact_money(combined_margin_debt))
        advisor_tabs = st.tabs([
            "Advisor Brief",
            "Opportunities Now",
            "Proposed Trades",
            "Watchlist",
            "Strategy Scorecards",
            "Forecast Accuracy",
            "Provider Health",
            "Risk Center",
            "Historical Audit",
            "Worker Status",
            "Settings",
        ])
        with advisor_tabs[0]:
            st.info("Advisor mode is active for recommendations and diagnostics. Live brokerage submission remains disabled.")
        with advisor_tabs[1]:
            st.caption("Opportunities are filtered by verified price, forecast quality, data quality, liquidity, and risk controls.")
        with advisor_tabs[2]:
            proposals = safe_rows("SELECT * FROM order_proposals ORDER BY id DESC LIMIT 25")
            if proposals:
                st.dataframe(pd.DataFrame(proposals), width="stretch", hide_index=True)
                st.warning("Authenticated trade approval is not enabled yet.")
            else:
                st.info("No trade proposals are waiting for manual review.")
        with advisor_tabs[3]:
            st.caption("Watchlist candidates continue to update from fixed watchlists and dynamic discovery.")
        with advisor_tabs[4]:
            scorecards = safe_rows("SELECT * FROM strategy_performance ORDER BY id DESC LIMIT 25")
            st.dataframe(pd.DataFrame(scorecards), width="stretch", hide_index=True) if scorecards else st.info("Strategy scorecards will appear after enough paper or shadow observations.")
        with advisor_tabs[5]:
            validation = safe_rows("SELECT * FROM forecast_validation ORDER BY id DESC LIMIT 25")
            st.dataframe(pd.DataFrame(validation), width="stretch", hide_index=True) if validation else st.info("Forecast accuracy records will appear as walk-forward outcomes mature.")
        with advisor_tabs[6]:
            diagnostics = provider_diagnostics()
            st.dataframe(pd.DataFrame(diagnostics), width="stretch", hide_index=True)
        with advisor_tabs[7]:
            risk_events = safe_rows("SELECT * FROM risk_events ORDER BY id DESC LIMIT 25")
            st.dataframe(pd.DataFrame(risk_events), width="stretch", hide_index=True) if risk_events else st.success("No advisor risk events are currently recorded.")
        with advisor_tabs[8]:
            audits = safe_rows("SELECT * FROM trade_audits ORDER BY id DESC LIMIT 25")
            st.dataframe(pd.DataFrame(audits), width="stretch", hide_index=True) if audits else st.info("Historical audit records are non-destructive and appear here after review.")
        with advisor_tabs[9]:
            st.dataframe(pd.DataFrame(workers), width="stretch", hide_index=True) if workers else st.info("Workers have not reported status yet.")
        with advisor_tabs[10]:
            st.caption("Execution switches default to disabled. Railway variables must be intentionally changed before any paper automation can run.")
            st.write(f"OpenAI enabled: {'Yes' if openai_available() else 'No'}")

            try:
                storage = database_storage_report()
                st.markdown("### Database storage status")
                st.write(f"Database size: {storage.get('database_size')} ? status: {storage.get('status')}")
                if storage.get("used_pct") is not None:
                    st.progress(min(1.0, float(storage["used_pct"]) / 100.0), text=f"{storage['used_pct']:.1f}% of configured database volume")
                else:
                    st.caption("Database volume capacity is not configured; set DATABASE_VOLUME_CAPACITY_GB later to enable percentage warnings.")
                table_rows_view = [
                    {
                        "Table": item.get("table"),
                        "Total": item.get("total_size"),
                        "Data": item.get("table_size"),
                        "Indexes": item.get("index_size"),
                        "Live rows": item.get("live_rows"),
                        "Dead rows": item.get("dead_rows"),
                        "Last autovacuum": item.get("last_autovacuum"),
                        "Last autoanalyze": item.get("last_autoanalyze"),
                    }
                    for item in storage.get("largest_tables", [])
                ]
                st.dataframe(pd.DataFrame(table_rows_view), width="stretch", hide_index=True) if table_rows_view else st.info("No table size records are available yet.")
                st.caption("Retention is conservative and never auto-deletes canonical financial, governance, migration, or execution history.")
            except Exception as exc:
                st.warning(f"Database storage diagnostics unavailable: {exc}")
            if st.button("Test OpenAI Connection"):
                result = test_openai_connection()
                st.write(f"API key configured: {'Yes' if result.get('api_key_configured') else 'No'}")
                st.write(f"Configured model: {result.get('model', '')}")
                st.write(f"Connection status: {result.get('status', 'unavailable')}")
                message = str(result.get("message") or "")
                if result.get("available") and message.strip() == "GARIBALDI AI ONLINE":
                    st.success("GARIBALDI AI ONLINE")
                else:
                    st.warning(message or "OpenAI diagnostic did not complete.")

elif page == "Markets":
    st.subheader("Global Market Opportunity Center")
    tab1, tab2, tab3 = st.tabs(["Top Ranked", "Stocks", "Crypto"])
    for tab, market_filter in ((tab1, None), (tab2, "cash"), (tab3, "crypto")):
        with tab:
            filtered = decisions if market_filter is None else [d for d in decisions if d["market"] == market_filter]
            if not filtered:
                st.info("No ranked opportunities are currently available for this market.")
            else:
                view_rows = []
                for item in filtered:
                    freshness = live_data_status(item)
                    display_action = str(item.get("action") or "WAIT").upper()
                    trade_ready = bool(item.get("trade_eligible")) and not freshness["blocks_execution"]
                    if display_action == "BUY" and not trade_ready:
                        display_action = "WATCH"
                    view_rows.append({
                        "Symbol": item.get("symbol"),
                        "Market": item.get("market"),
                        "Decision": display_action,
                        "Quality": item.get("score"),
                        "Confidence %": item.get("confidence"),
                        "Expected %": item.get("expected_return"),
                        "Risk": item.get("risk"),
                        "Price": item.get("price"),
                        "Target": item.get("target"),
                        "Data Status": f"{freshness['label']}: {freshness['detail']}",
                        "Trade Ready": trade_ready,
                    })
                st.dataframe(pd.DataFrame(view_rows), width="stretch", hide_index=True)

    st.markdown("### Chart and price history")
    symbols = sorted({d["symbol"] for d in decisions if d.get("symbol")})
    selected = st.selectbox("Choose an asset", symbols or ["AAPL"])
    period = st.selectbox("History", ["1mo", "3mo", "6mo", "1y", "2y"], index=2)
    try:
        history = get_history(selected, period=period)
        if history is not None and not history.empty:
            chart = history.reset_index()
            date_col = chart.columns[0]
            fig = px.line(chart, x=date_col, y="Close", title=f"{selected} closing price")
            st.plotly_chart(fig, width="stretch")
            st.caption("The Oracle uses price history as evidence. A chart pattern alone does not qualify a trade without volume, regime, risk, and portfolio confirmation.")
        else:
            st.info("Price history is not available from the configured providers.")
    except Exception as exc:
        st.warning(f"Price history could not be loaded: {exc}")

elif page == "Portfolios":
    st.subheader("Portfolio Center")
    portfolio_tabs = st.tabs(["Stock Portfolio", "Crypto Portfolio", "Trade History", "Hypothetical Analyzer"])
    for tab, name, market, positions, metrics, health in (
        (portfolio_tabs[0], "Stock", "cash", stock_positions, stock_metrics, stock_health),
        (portfolio_tabs[1], "Crypto", "crypto", crypto_positions, crypto_metrics, crypto_health),
    ):
        with tab:
            a, b, c, d = st.columns(4)
            a.metric("Broker Equity", compact_money(metrics["equity"]), pct(metrics["return_pct"]))
            b.metric("Buying Power", compact_money(metrics["buying_power"]), f"{metrics['leverage_limit']:.1f}x limit")
            c.metric("Gross Exposure", compact_money(metrics["gross_exposure"]), f"{metrics['leverage_used']:.2f}x equity")
            d.metric("Portfolio Status", portfolio_status_label(health.health_score), f"{health.health_score}/100")
            e, f, g, h = st.columns(4)
            e.metric("Cash Reserve", compact_money(metrics["cash"]), f"{health.cash_pct:.1f}%")
            f.metric("Margin Used", compact_money(metrics["margin_debt"]), f"{metrics['margin_utilization_pct']:.1f}% capacity")
            g.metric("Open Holdings", health.position_count)
            h.metric("Excess Liquidity", compact_money(metrics["excess_liquidity"]))
            if metrics["margin_call"]:
                st.error("Margin call condition: the paper broker must reduce exposure immediately.")
            elif metrics["margin_utilization_pct"] >= 70:
                st.warning("Paper leverage is elevated. New positions will be reduced or blocked as utilization approaches the hard limit.")
            else:
                st.info(health.plain_summary)
            holdings_tab, profit_tab, activity_tab, advice_tab = st.tabs([
                "What I Own Now",
                f"How {name} Profits Were Made",
                f"{name} Trade History",
                "Rotation / Next Move",
            ])
            with holdings_tab:
                frame = portfolio_table(positions, market)
                if frame.empty:
                    st.info(
                        f"The {name.lower()} portfolio has no open positions right now. "
                        "The Oracle is waiting for verified prices, qualified signals, and portfolio-safe sizing before adding paper exposure."
                    )
                else:
                    formatters = {
                        key: value
                        for key, value in {
                            "Average Cost": "${:,.2f}",
                            "Avg Cost": "${:,.2f}",
                            "Current Price": "${:,.2f}",
                            "Current Value": "${:,.2f}",
                            "Market Value": "${:,.2f}",
                            "Gain/Loss": "${:+,.2f}",
                            "P/L $": "${:+,.2f}",
                            "Return %": "{:+.1f}%",
                            "P/L %": "{:+.1f}%",
                            "Weight": "{:.1f}%",
                        }.items()
                        if key in frame.columns
                    }
                    st.dataframe(
                        frame.style.format(formatters),
                        width="stretch", hide_index=True,
                    )
            with profit_tab:
                ledger = safe_rows("SELECT * FROM trade_ledger WHERE market=%s ORDER BY id DESC LIMIT 500", (market,))
                attribution = profit_attribution_rows(
                    positions=positions,
                    ledger_rows=ledger,
                    market=market,
                    equity=metrics["equity"],
                )
                if not attribution:
                    st.info(f"{name} profit attribution will appear after ledgered paper fills or verified open-position marks are available.")
                else:
                    attribution_frame = pd.DataFrame(
                        [
                            {
                                "Symbol": row["symbol"],
                                "Bucket": row.get("bucket") or "",
                                "Strategy": row.get("strategy") or "",
                                "Quantity": format_quantity(row.get("quantity")),
                                "Entry Price": row.get("entry_price"),
                                "Exit / Current": row.get("exit_or_current_price"),
                                "Realized P/L": row.get("realized_pnl"),
                                "Unrealized P/L": row.get("unrealized_pnl"),
                                "Total P/L": row.get("total_pnl"),
                                "Return %": row.get("return_pct"),
                                "Portfolio Contribution %": row.get("contribution_to_portfolio_profit_pct"),
                                "Tier": row.get("tier") or "",
                                "Provider": row.get("quote_provider") or "",
                                "Data Status": row.get("status") or "",
                                "First Entry": row.get("first_entry_time") or "",
                                "Latest Fill": row.get("latest_fill_time") or "",
                                "Entry Reason": row.get("entry_reason") or "",
                                "Exit / Hold Reason": row.get("exit_or_hold_reason") or "",
                            }
                            for row in attribution
                        ]
                    )
                    st.dataframe(
                        attribution_frame.style.format(
                            {
                                "Entry Price": "${:,.4f}",
                                "Exit / Current": "${:,.4f}",
                                "Realized P/L": "${:+,.2f}",
                                "Unrealized P/L": "${:+,.2f}",
                                "Total P/L": "${:+,.2f}",
                                "Return %": "{:+.1f}%",
                                "Portfolio Contribution %": "{:+.3f}%",
                            },
                            na_rep="Waiting",
                        ),
                        width="stretch",
                        hide_index=True,
                    )
            with activity_tab:
                trades = safe_rows("SELECT * FROM trades WHERE market=%s ORDER BY id DESC LIMIT 300", (market,))
                if not trades:
                    st.info("No completed trades have been recorded yet.")
                else:
                    view = clean_trade_frame(trades)
                    st.dataframe(
                        view.style.format({"Price": "${:,.4f}", "Trade Value": "${:,.2f}", "Profit / Loss": "${:+,.2f}"}, na_rep="—"),
                        width="stretch",
                        hide_index=True,
                    )
            with advice_tab:
                st.markdown(f"### {name} Portfolio Doctor")
                st.write(f"**Status:** {portfolio_status_label(health.health_score)} · **Health:** {health.health_score}/100 · **Risk:** {health.risk_label}")
                st.write(health.plain_summary)
                worker_record = next((r for r in workers if r.get("market") == market), {})
                deployment = capital_deployment_status(
                    metrics,
                    decisions,
                    market=market,
                    session_label=str(worker_record.get("session_label") or ""),
                )
                if deployment["status"].startswith("blocked") or deployment["status"] == "paused_market_closed":
                    st.warning(deployment["message"])
                elif deployment["status"] == "deployable_cash_available":
                    st.info(deployment["message"])
                else:
                    st.caption(deployment["message"])
                st.write(
                    f"**Broker capacity:** {compact_money(metrics['buying_power'])} buying power · "
                    f"{metrics['leverage_used']:.2f}x used of {metrics['leverage_limit']:.1f}x · "
                    f"{compact_money(metrics['margin_debt'])} paper margin debt."
                )
                if health.cash_pct < 8:
                    st.warning("Cash is low. Consider reducing a weak position before adding another holding.")
                if health.largest_position_pct > 20:
                    st.warning("The largest holding is creating concentration risk.")
                if health.position_count < 4 and health.invested > 0:
                    st.warning("The portfolio may benefit from broader diversification.")
                related = [d for d in ready_decisions if d["market"] == market][:3]
                for item in related:
                    decision_card(item, compact=True)

    with portfolio_tabs[2]:
        st.markdown("### Bought and sold")
        trades = safe_rows("SELECT * FROM trades ORDER BY id DESC LIMIT 500")
        render_trade_history_section(trades, "trade-history")

    with portfolio_tabs[3]:
        st.markdown("### Test a trade before risking capital")
        portfolio_choice = st.radio("Portfolio", ["Stock", "Crypto"], horizontal=True)
        positions = stock_positions if portfolio_choice == "Stock" else crypto_positions
        selected_metrics = stock_metrics if portfolio_choice == "Stock" else crypto_metrics
        cash = selected_metrics["cash"]
        c1, c2 = st.columns(2)
        with c1:
            action = st.selectbox("Hypothetical action", ["Buy", "Sell"])
            symbol = st.text_input("Symbol", value="AAPL" if portfolio_choice == "Stock" else "BTC-USD").upper().strip()
        with c2:
            default_amount = min(100000.0, max(500.0, selected_metrics["equity"] * 0.01))
            amount = st.number_input("Dollar amount", min_value=0.0, value=float(default_amount), step=1000.0)
            assumed_price = st.number_input("Assumed price", min_value=0.000001, value=100.0, step=1.0)
        if st.button("Analyze hypothetical trade", type="primary", width="stretch"):
            result = simulate_trade(
                cash, positions, action, symbol, amount, assumed_price,
                selected_metrics["margin_debt"], selected_metrics["leverage_limit"],
                selected_metrics["buying_power"],
            )
            verdict = result["verdict"]
            st.markdown(f"## {verdict} · Portfolio score {result['score_change']:+.1f}")
            st.write(result["note"])
            before, after = result["before"], result["after"]
            comparison = pd.DataFrame([
                {"Measure": "Portfolio health", "Current": before["health_score"], "Proposed": after["health_score"]},
                {"Measure": "Cash %", "Current": before["cash_pct"], "Proposed": after["cash_pct"]},
                {"Measure": "Largest holding %", "Current": before["largest_position_pct"], "Proposed": after["largest_position_pct"]},
                {"Measure": "Number of holdings", "Current": before["position_count"], "Proposed": after["position_count"]},
            ])
            st.dataframe(comparison, width="stretch", hide_index=True)
            st.markdown("**What changes:** " + "; ".join(result["reasons"]) + ".")
            st.caption("This is a portfolio-structure simulation, not a guarantee of future return. Live market evidence should be checked before acting.")

elif page == "Oracle":
    st.subheader("Oracle Decisions")
    st.caption("Only four plain decisions: BUY, HOLD, WAIT, or SELL.")
    filter_action = st.segmented_control("Show", ["ALL", "BUY", "HOLD", "WAIT", "SELL"], default="ALL")
    filtered = decisions if filter_action == "ALL" else [d for d in decisions if d["action"] == filter_action]
    for item in filtered[:20]:
        decision_card(item)

    st.markdown("### Ask Oracle")
    question = st.text_area("Ask about the market, an asset, or a portfolio decision", placeholder="Example: Why is the Oracle waiting on this stock?")
    if st.button("Get Oracle explanation", disabled=not question.strip()):
        if openai_available():
            try:
                answer = answer_market_question(question, {
                    "decisions": decisions[:15], "stock_health": stock_health.to_dict(),
                    "crypto_health": crypto_health.to_dict(), "alerts": alerts[:10],
                })
                st.write(answer)
            except Exception as exc:
                st.warning(f"Oracle explanation is temporarily unavailable: {exc}")
        else:
            st.info("Add an OpenAI API key to enable conversational explanations. The deterministic decision cards remain available without it.")

elif page == "Intelligence":
    st.subheader("Financial Intelligence")
    st.caption("Only market-moving information: macroeconomics, policy, earnings, capital flow, insiders, options, and global events.")
    earnings_events = [e for e in events if str(e.get("category") or "") == "Earnings Calendar"]
    if earnings_events:
        st.markdown("### Earnings Calendar")
        position_symbols = {str(p.get("symbol") or "").upper() for p in stock_positions if p.get("symbol")}
        opportunity_symbols = {str(item.get("symbol") or "").upper() for item in opportunities if item.get("symbol")}
        mover_symbols = {str(item.get("symbol") or "").upper() for item in decisions[:20] if item.get("symbol")}
        cap_choices = ["All"] + sorted({
            str(parse_payload(e.get("details")).get("market_cap_category") or parse_payload(e.get("details")).get("marketCapCategory") or "Unknown")
            for e in earnings_events
        })
        f1, f2, f3 = st.columns([1.35, 1, 1])
        with f1:
            selected_range = st.date_input(
                "Date range",
                value=(date.today(), date.today() + timedelta(days=14)),
                key="earnings-date-range",
            )
        with f2:
            ticker_filter = st.text_input("Ticker", key="earnings-ticker-filter")
        with f3:
            cap_filter = st.selectbox("Market cap", cap_choices, key="earnings-market-cap")
        c1, c2, c3 = st.columns(3)
        with c1:
            reporting_today = st.checkbox("Reporting today", key="earnings-reporting-today")
        with c2:
            held_only = st.checkbox("Held positions", key="earnings-held-only")
        with c3:
            radar_only = st.checkbox("Opportunity radar", key="earnings-radar-only")

        start_filter = None
        end_filter = None
        if isinstance(selected_range, tuple) and len(selected_range) == 2:
            start_filter, end_filter = selected_range
        elif selected_range:
            start_filter = end_filter = selected_range

        prepared_earnings = prepare_events(
            earnings_events,
            held_symbols=position_symbols,
            opportunity_symbols=opportunity_symbols,
            major_movers=mover_symbols,
            start_date=start_filter,
            end_date=end_filter,
            ticker_filter=ticker_filter,
            market_cap_category=cap_filter,
            reporting_today=reporting_today,
            held_only=held_only,
            opportunity_only=radar_only,
            limit=20,
        )
        main_rows = table_rows(prepared_earnings["main"])
        if main_rows:
            st.dataframe(pd.DataFrame(main_rows), width="stretch", hide_index=True)
            with st.expander("Show more earnings"):
                more_rows = table_rows(prepared_earnings["more"])
                if more_rows:
                    st.dataframe(pd.DataFrame(more_rows), width="stretch", hide_index=True)
                else:
                    st.caption("No additional matching events.")
            with st.expander("Mobile card view"):
                for event in prepared_earnings["main"]:
                    st.markdown(
                        "<div class='card'>" + "<br>".join(html.escape(line) for line in mobile_card_lines(event)) + "</div>",
                        unsafe_allow_html=True,
                    )
        else:
            st.info("No complete earnings events match the selected filters.")

        if prepared_earnings["incomplete"]:
            with st.expander("Incomplete Provider Data"):
                st.dataframe(pd.DataFrame(table_rows(prepared_earnings["incomplete"])), width="stretch", hide_index=True)
        with st.expander("Developer diagnostics: raw provider payload"):
            st.json([event.get("raw_payload") for event in prepared_earnings["main"] + prepared_earnings["more"] + prepared_earnings["incomplete"]])

    non_earnings_events = [e for e in events if str(e.get("category") or "") != "Earnings Calendar"]
    categories = sorted({str(e.get("category") or "Other") for e in non_earnings_events})
    selected_categories = st.multiselect("Filter intelligence", categories, default=categories)
    filtered_events = [e for e in non_earnings_events if str(e.get("category") or "Other") in selected_categories]
    if not filtered_events:
        st.info("No intelligence events match the selected filters.")
    else:
        for event in filtered_events[:50]:
            st.markdown(
                f"""<div class='card'><span class='pill'>{html.escape(str(event.get('category','Market')))}</span>
                <span class='pill'>{html.escape(str(event.get('provider','Unknown')))}</span>
                <h3>{html.escape(str(event.get('title','Untitled event')))}</h3>
                <p class='reason'>{html.escape(str(event.get('details') or 'No additional details.'))}</p>
                <small class='muted'>{html.escape(str(event.get('event_time') or event.get('created_at') or ''))}</small></div>""",
                unsafe_allow_html=True,
            )

elif page == "Professional":
    st.subheader("Professional Research & System Evidence")
    st.caption("Advanced tools are kept here so everyday investors are not overwhelmed.")
    tabs = st.tabs(["Evidence Ledger", "Backtesting", "Provider Health", "Raw Signals"])
    with tabs[0]:
        st.markdown("### Evidence behind current decisions")
        if decisions:
            selected_symbol = st.selectbox("Decision", [f"{d['symbol']} · {d['action']}" for d in decisions])
            selected_index = [f"{d['symbol']} · {d['action']}" for d in decisions].index(selected_symbol)
            d = decisions[selected_index]
            supports, cautions = plain_reason(d.get("reason"))
            a, b, c, e = st.columns(4)
            a.metric("Decision", d.get("action", "WAIT"))
            b.metric("Confidence", f"{as_float(d.get('confidence')):.0f}%")
            c.metric("Trade quality", f"{as_float(d.get('score')):.0f}/100")
            e.metric("Expected move", f"{as_float(d.get('expected_return')):+.1f}%")
            freshness = live_data_status(d)
            if d.get("trade_eligible") and not freshness["blocks_execution"]:
                st.success(f"Trade-ready data - {freshness['label']}: {freshness['detail']}")
            else:
                st.warning(f"Not trade-ready - {freshness['label']}: {freshness['detail']}")
            st.markdown("### Supporting evidence")
            for point in supports:
                st.success(point)
            st.markdown("### Main cautions")
            for point in cautions:
                st.warning(point)
            with st.expander("Developer data"):
                st.json(d)
        else:
            st.info("No current decisions are available.")
    with tabs[1]:
        runs = safe_rows("SELECT * FROM backtest_runs ORDER BY id DESC LIMIT 100")
        if runs:
            st.dataframe(pd.DataFrame(runs), width="stretch", hide_index=True)
        else:
            st.info("No stored backtest runs are available yet.")
        st.caption("Production strategies should pass out-of-sample, walk-forward, fee, slippage, and drawdown testing before influencing live decisions.")
    with tabs[2]:
        try:
            diagnostics = provider_diagnostics()
            st.dataframe(pd.DataFrame(diagnostics), width="stretch", hide_index=True)
        except Exception as exc:
            st.warning(f"Provider diagnostics unavailable: {exc}")
    with tabs[3]:
        if signals:
            st.dataframe(pd.DataFrame(signals), width="stretch", hide_index=True)
        else:
            st.info("No raw signals are available.")

st.divider()
st.caption("GARIBALDI MARKET ORACLE™ provides evidence-based decision support and simulated execution. Markets remain uncertain; every trade requires risk limits and an exit plan.")
