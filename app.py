from __future__ import annotations

import html
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

from ai_oracle import answer_market_question, openai_available
from config import (
    ALWAYS_ON_TRADING, APP_NAME, EXECUTION_MODE, LIVE_STATUS_STALE_SECONDS,
    PAPER_BROKER_MODE, UI_AUTO_REFRESH, UI_REFRESH_SECONDS,
)
from dashboard_helpers import as_float, format_asset_price, worker_is_online
from database import initialize_database, row, rows
from earnings_calendar import mobile_card_lines, prepare_events, table_rows
from market_data import get_history
from migrations import run_migrations
from portfolio_advisor import analyze_portfolio, simulate_trade
from paper_broker import build_account
from prediction_engine import build_decisions
from provider_diagnostics import provider_diagnostics
from realtime_runtime import status_age_seconds

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:  # deployment installs it; local tests can still import modules
    st_autorefresh = None

st.set_page_config(
    page_title=f"{APP_NAME} — AI Chief Investment Officer",
    page_icon="🔮",
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
.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.tile{border:2px solid var(--line);background:var(--panel);border-radius:15px;padding:14px}.tile small{color:var(--muted)}.tile b{display:block;font-size:1.55rem;margin-top:.25rem}.reason{line-height:1.6;color:#eef6fc}.pill{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:.35rem .65rem;margin:.2rem .25rem .1rem 0;background:#09131d;font-weight:700}
[data-testid="stSidebar"]{border-right:2px solid var(--line)}[data-testid="stMetric"]{border:2px solid var(--line);border-radius:16px;padding:14px;background:var(--panel)}
.stTabs [data-baseweb="tab-list"]{gap:.3rem;overflow-x:auto}.stTabs [data-baseweb="tab"]{min-height:48px;font-weight:800;white-space:nowrap}.stTabs [aria-selected="true"]{border-bottom:4px solid var(--green)!important}
[data-testid="stDataFrame"]{border:2px solid var(--line);border-radius:15px;overflow:hidden}button,input,textarea,select{min-height:44px}
@media(max-width:900px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:650px){html{font-size:16px}.block-container{padding-left:.7rem;padding-right:.7rem}.hero{padding:18px}.grid{grid-template-columns:1fr}}
</style>
""",
    unsafe_allow_html=True,
)


@st.cache_resource
def bootstrap() -> list[str]:
    initialize_database()
    return run_migrations()


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


def decision_card(item: dict[str, Any], compact: bool = False) -> None:
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
    color = {"BUY": "green", "SELL": "red", "HOLD": "blue", "WAIT": "orange"}.get(action, "orange")
    supports, cautions = plain_reason(item.get("reason"))
    ready = bool(item.get("trade_eligible"))
    data_status = str(item.get("data_status") or "Data status unavailable")

    with st.container(border=True):
        st.markdown(f"### :{color}[{action} · {symbol}]")
        current_text = format_asset_price(price, symbol, market_key)
        target_text = format_asset_price(target, symbol, market_key)
        st.caption(f"{market} portfolio · Current {current_text} · Target {target_text}")
        if ready:
            st.success(f"Trade-ready data · {data_status}")
        else:
            st.warning(data_status)
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
                st.markdown(f"⚠️ {point}")
            reference = format_asset_price(price, symbol, market_key)
            stop_text = format_asset_price(low, symbol, market_key)
            st.caption(f"Entry reference: {reference} · Target: {target_text} · Protective level: {stop_text}")

def portfolio_table(positions: list[dict[str, Any]]) -> pd.DataFrame:
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


bootstrap()
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
ready_decisions = [d for d in decisions if bool(d.get("trade_eligible"))]
buy_decisions = [d for d in ready_decisions if d["action"] == "BUY"]
sell_decisions = [d for d in ready_decisions if d["action"] == "SELL"]
waiting_for_data = [d for d in decisions if not bool(d.get("trade_eligible"))]

with st.sidebar:
    st.markdown("## 🔮 GARIBALDI ORACLE")
    st.caption("The AI Chief Investment Officer")
    page = st.radio(
        "Main navigation",
        ["🏠 Dashboard", "📈 Markets", "💼 Portfolios", "🤖 Oracle", "🌍 Intelligence", "⚙ Professional"],
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
    if st.button("Refresh market data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

st.markdown(
    f"""
<div class='hero'>
  <div class='eyebrow'>Always-On Institutional Paper Broker · AI Chief Investment Officer</div>
  <h1>{html.escape(APP_NAME)}</h1>
  <p>Large simulated capital, controlled paper leverage, rolling fast scans, deeper global research, continuous position monitoring, automatic rotation, live risk exits, and clear decisions. The broker account is paper-only and does not submit real-money orders.</p>
</div>
""",
    unsafe_allow_html=True,
)

live_records = [record for record in workers if worker_live(record)]
mode_label = "LIVE INSTITUTIONAL PAPER BROKER" if EXECUTION_MODE == "paper" and PAPER_BROKER_MODE else ("LIVE PAPER TRADING" if EXECUTION_MODE == "paper" else f"LIVE {EXECUTION_MODE.upper()} EXECUTION")
if len(live_records) == 2:
    always_text = "ALWAYS-ON" if ALWAYS_ON_TRADING else "CONTINUOUS MONITORING"
    st.success(
        f"🟢 {mode_label} · {always_text} · rolling fast scans, deep research, "
        "position monitoring, qualified entries, rotations, and risk exits are active."
    )
elif live_records:
    st.warning(f"🟠 {mode_label} · only {len(live_records)} of 2 workers currently has a fresh heartbeat.")
else:
    st.error("🔴 Live workers are not reporting fresh heartbeats. Check both Railway worker services.")

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
latest_trade = recent_trades[0] if recent_trades else {}
trade_label = f"{str(latest_trade.get('side','')).upper()} {latest_trade.get('symbol','')}".strip() or "No trade yet"
status_cols[2].metric(
    "Latest execution",
    trade_label,
    clean_trade_reason(latest_trade.get("reason")) if latest_trade else "Scanning for a qualified trade",
)
error_total = sum(int(as_float(record.get("cycle_errors"))) for record in workers)
status_cols[3].metric("Auto recovery", "Ready" if error_total == 0 else "Recovering", f"Cycle errors: {error_total}")

if page == "🏠 Dashboard":
    st.subheader("What should I do today?")
    top = buy_decisions[0] if buy_decisions else (decisions[0] if decisions else None)
    market_state = "Constructive" if buy_decisions else "Cautious"
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Broker Equity", money(combined_equity), pct(combined_return))
    c2.metric("Available Buying Power", money(combined_buying_power))
    c3.metric("Trade-ready Buys", len(buy_decisions), f"{len(waiting_for_data)} waiting on data")
    c4.metric("Active Risks", len(alerts))
    b1, b2, b3, b4 = st.columns(4)
    b1.metric("Stock Leverage", f"{stock_metrics['leverage_used']:.2f}x", f"Limit {stock_metrics['leverage_limit']:.1f}x")
    b2.metric("Crypto Leverage", f"{crypto_metrics['leverage_used']:.2f}x", f"Limit {crypto_metrics['leverage_limit']:.1f}x")
    b3.metric("Margin Debt", money(combined_margin_debt))
    b4.metric("Market Condition", market_state)

    if top:
        st.markdown("### Today's strongest decision")
        decision_card(top)
    else:
        st.info("The Oracle has not produced a qualified opportunity yet. The disciplined decision is to wait for stronger evidence.")

    left, right = st.columns([1.2, 1])
    with left:
        st.markdown("### Today's action plan")
        plan = []
        if buy_decisions:
            plan.append(f"Review **{buy_decisions[0]['symbol']}** as the highest-ranked current opportunity.")
        if sell_decisions:
            plan.append(f"Review **{sell_decisions[0]['symbol']}** for reduction or exit.")
        if stock_health.cash_pct < 8:
            plan.append("Increase stock-portfolio cash before adding several new positions.")
        if crypto_health.cash_pct < 8:
            plan.append("Protect crypto liquidity; cash is below the preferred operating range.")
        if not plan:
            plan.append("Hold current positions and wait for stronger confirmation.")
        for item in plan:
            st.markdown(f"- {item}")

        st.markdown("### Top live opportunities")
        if buy_decisions:
            for item in buy_decisions[:5]:
                decision_card(item, compact=True)
        else:
            st.info("No BUY has passed the live-price, forecast, freshness, and minimum-edge gates yet.")

    with right:
        st.markdown("### Portfolio health")
        for name, health in (("Stock", stock_health), ("Crypto", crypto_health)):
            with st.container(border=True):
                st.markdown(f"### {name} Portfolio · {portfolio_status_label(health.health_score)}")
                p1, p2 = st.columns(2)
                p1.metric("Health", f"{health.health_score}/100")
                p2.metric("Risk", health.risk_label)
                p3, p4 = st.columns(2)
                p3.metric("Leverage", f"{health.leverage_used:.2f}x")
                p4.metric("Holdings", health.position_count)
                st.caption(health.plain_summary)
        st.markdown("### Important alerts")
        if alerts:
            for a in alerts[:5]:
                st.warning(f"**{a.get('title','Alert')}** — {a.get('message','')}")
        else:
            st.success("No unresolved high-priority alerts.")

elif page == "📈 Markets":
    st.subheader("Global Market Opportunity Center")
    tab1, tab2, tab3 = st.tabs(["Top Ranked", "Stocks", "Crypto"])
    for tab, market_filter in ((tab1, None), (tab2, "cash"), (tab3, "crypto")):
        with tab:
            filtered = decisions if market_filter is None else [d for d in decisions if d["market"] == market_filter]
            if not filtered:
                st.info("No ranked opportunities are currently available for this market.")
            else:
                frame = pd.DataFrame(filtered)[["symbol", "market", "action", "score", "confidence", "expected_return", "risk", "price", "target", "data_status", "trade_eligible"]]
                frame.columns = ["Symbol", "Market", "Decision", "Quality", "Confidence %", "Expected %", "Risk", "Price", "Target", "Data Status", "Trade Ready"]
                st.dataframe(frame, use_container_width=True, hide_index=True)

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
            st.plotly_chart(fig, use_container_width=True)
            st.caption("The Oracle uses price history as evidence. A chart pattern alone does not qualify a trade without volume, regime, risk, and portfolio confirmation.")
        else:
            st.info("Price history is not available from the configured providers.")
    except Exception as exc:
        st.warning(f"Price history could not be loaded: {exc}")

elif page == "💼 Portfolios":
    st.subheader("Portfolio Center")
    portfolio_tabs = st.tabs(["Stock Portfolio", "Crypto Portfolio", "Trade History", "Hypothetical Analyzer"])
    for tab, name, market, positions, metrics, health in (
        (portfolio_tabs[0], "Stock", "cash", stock_positions, stock_metrics, stock_health),
        (portfolio_tabs[1], "Crypto", "crypto", crypto_positions, crypto_metrics, crypto_health),
    ):
        with tab:
            a, b, c, d = st.columns(4)
            a.metric("Broker Equity", money(metrics["equity"]), pct(metrics["return_pct"]))
            b.metric("Buying Power", money(metrics["buying_power"]), f"{metrics['leverage_limit']:.1f}x limit")
            c.metric("Gross Exposure", money(metrics["gross_exposure"]), f"{metrics['leverage_used']:.2f}x equity")
            d.metric("Portfolio Status", portfolio_status_label(health.health_score), f"{health.health_score}/100")
            e, f, g, h = st.columns(4)
            e.metric("Cash Reserve", money(metrics["cash"]), f"{health.cash_pct:.1f}%")
            f.metric("Margin Used", money(metrics["margin_debt"]), f"{metrics['margin_utilization_pct']:.1f}% capacity")
            g.metric("Open Holdings", health.position_count)
            h.metric("Excess Liquidity", money(metrics["excess_liquidity"]))
            if metrics["margin_call"]:
                st.error("Margin call condition: the paper broker must reduce exposure immediately.")
            elif metrics["margin_utilization_pct"] >= 70:
                st.warning("Paper leverage is elevated. New positions will be reduced or blocked as utilization approaches the hard limit.")
            else:
                st.info(health.plain_summary)
            holdings_tab, activity_tab, advice_tab = st.tabs(["What It Owns", "What It Bought & Sold", "Oracle Advice"])
            with holdings_tab:
                frame = portfolio_table(positions)
                if frame.empty:
                    st.info(f"The {name.lower()} portfolio has no open positions.")
                else:
                    st.dataframe(
                        frame.style.format({"Average Cost": "${:,.2f}", "Current Price": "${:,.2f}", "Current Value": "${:,.2f}", "Gain/Loss": "${:+,.2f}", "Return %": "{:+.1f}%"}),
                        use_container_width=True, hide_index=True,
                    )
            with activity_tab:
                trades = safe_rows("SELECT * FROM trades WHERE market=%s ORDER BY id DESC LIMIT 300", (market,))
                if not trades:
                    st.info("No completed trades have been recorded yet.")
                else:
                    view = clean_trade_frame(trades)
                    st.dataframe(
                        view.style.format({"Price": "${:,.4f}", "Trade Value": "${:,.2f}", "Profit / Loss": "${:+,.2f}"}, na_rep="—"),
                        use_container_width=True,
                        hide_index=True,
                    )
            with advice_tab:
                st.markdown(f"### {name} Portfolio Doctor")
                st.write(f"**Status:** {portfolio_status_label(health.health_score)} · **Health:** {health.health_score}/100 · **Risk:** {health.risk_label}")
                st.write(health.plain_summary)
                st.write(
                    f"**Broker capacity:** {money(metrics['buying_power'])} buying power · "
                    f"{metrics['leverage_used']:.2f}x used of {metrics['leverage_limit']:.1f}x · "
                    f"{money(metrics['margin_debt'])} paper margin debt."
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
        if trades:
            view = clean_trade_frame(trades)
            st.dataframe(
                view.style.format({"Price": "${:,.4f}", "Trade Value": "${:,.2f}", "Profit / Loss": "${:+,.2f}"}, na_rep="—"),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No trade history has been recorded.")

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
        if st.button("Analyze hypothetical trade", type="primary", use_container_width=True):
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
            st.dataframe(comparison, use_container_width=True, hide_index=True)
            st.markdown("**What changes:** " + "; ".join(result["reasons"]) + ".")
            st.caption("This is a portfolio-structure simulation, not a guarantee of future return. Live market evidence should be checked before acting.")

elif page == "🤖 Oracle":
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

elif page == "🌍 Intelligence":
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
            st.dataframe(pd.DataFrame(main_rows), use_container_width=True, hide_index=True)
            with st.expander("Show more earnings"):
                more_rows = table_rows(prepared_earnings["more"])
                if more_rows:
                    st.dataframe(pd.DataFrame(more_rows), use_container_width=True, hide_index=True)
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
                st.dataframe(pd.DataFrame(table_rows(prepared_earnings["incomplete"])), use_container_width=True, hide_index=True)
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

elif page == "⚙ Professional":
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
            if d.get("trade_eligible"):
                st.success(str(d.get("data_status") or "Trade-ready data"))
            else:
                st.warning(str(d.get("data_status") or "Not trade-ready"))
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
            st.dataframe(pd.DataFrame(runs), use_container_width=True, hide_index=True)
        else:
            st.info("No stored backtest runs are available yet.")
        st.caption("Production strategies should pass out-of-sample, walk-forward, fee, slippage, and drawdown testing before influencing live decisions.")
    with tabs[2]:
        try:
            diagnostics = provider_diagnostics()
            st.dataframe(pd.DataFrame(diagnostics), use_container_width=True, hide_index=True)
        except Exception as exc:
            st.warning(f"Provider diagnostics unavailable: {exc}")
    with tabs[3]:
        if signals:
            st.dataframe(pd.DataFrame(signals), use_container_width=True, hide_index=True)
        else:
            st.info("No raw signals are available.")

st.divider()
st.caption("GARIBALDI MARKET ORACLE™ provides evidence-based decision support and simulated execution. Markets remain uncertain; every trade requires risk limits and an exit plan.")
