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
growth = snapshot["growth"]
safety = snapshot["safety"]

st.title("Oracle Brain")
st.caption(f"Brain growth: {growth['knowledge_units']:,} retained evidence units · {growth['relationships']:,} learned relationships · last sync {growth['last_learning_sync'] or 'not recorded'}")

# Evidence-driven neural field: visualizes what Oracle has actually learned.
# It is deliberately read-only and has no execution authority.
neural_payload = {
    "entries": [
        {"category": item["category"], "title": item["title"], "confidence": item["confidence"]}
        for item in snapshot["entries"][:24]
    ],
    "regimes": [
        {"strategy": item["strategy"], "regime": item["regime"], "samples": item["samples"],
         "expectancy": item["expectancy"], "state": item["evidence_state"]}
        for item in snapshot["regime_economics"][:30]
    ],
    "sources": [
        {"title": item["title"], "provider": item.get("provider"), "category": item.get("category"),
         "confidence": item.get("confidence"), "freshness": item.get("freshness_score"),
         "symbol": item.get("symbol")}
        for item in snapshot["sources"][:24]
    ],
    "episodes": [
        {"symbol": item["symbol"], "strategy": item["strategy"], "regime": item["regime"],
         "pnl": item["net_pnl"], "confidence": item["confidence"]}
        for item in snapshot["episodes"][:24]
    ],
    "links": [
        {"source": item["source_key"], "target": item["target_key"], "relation": item["relation"],
         "confidence": item["confidence"], "evidence_count": item["evidence_count"]}
        for item in snapshot["concept_links"][:40]
    ],
}
import json
_payload = json.dumps(neural_payload).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
st.html(f"""<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
html,body{{margin:0;background:#05080e;color:#eaf6ff;font-family:system-ui;overflow:hidden}}
#brain{{width:100%;height:430px;display:block;background:radial-gradient(circle at 50% 48%,#11243b 0,#07101d 42%,#03060b 78%);border:1px solid #263d58;border-radius:22px}}
.hud{{position:absolute;left:18px;top:16px;padding:9px 11px;border:1px solid #294865;border-radius:12px;background:#07101dcc;backdrop-filter:blur(8px);font-size:11px}}
.hud b{{display:block;letter-spacing:.12em;color:#78d9ff}} .hud span{{color:#93a9bb}}
@media(max-width:720px){{#brain{{height:390px}}.hud{{left:10px;top:10px;font-size:10px}}}}
</style></head><body><canvas id="brain"></canvas><div class="hud"><b>ORACLE NEURAL FIELD</b><span id="state">Evidence pulses are learned observations — visualization only</span></div>
<script id="data" type="application/json">{_payload}</script><script>
const D=JSON.parse(document.getElementById("data").textContent),c=document.getElementById("brain"),x=c.getContext("2d");let W,H,t=0,nodes=[];
function resize(){{const r=c.getBoundingClientRect();c.width=Math.max(1,r.width*devicePixelRatio);c.height=Math.max(1,r.height*devicePixelRatio);W=r.width;H=r.height;x.setTransform(devicePixelRatio,0,0,devicePixelRatio,0,0);make();}}
function make(){{nodes=[];const src=[
...D.entries.map(v=>({{...v,kind:"knowledge",value:v.confidence||.5,label:v.title}})),
...D.regimes.map(v=>({{...v,kind:"regime",value:Math.min(1,(v.samples||0)/200),label:v.strategy+" · "+v.regime}})),
...D.sources.map(v=>({{...v,kind:"source",value:(v.confidence||.3)*(v.freshness||.5),label:v.title}})),
...D.episodes.map(v=>({{...v,kind:"episode",value:v.confidence||.5,state:(v.pnl||0)>0?"positive":((v.pnl||0)<0?"negative":"mixed"),label:v.symbol+" · "+v.strategy}}))
];const evidenceCount=D.entries.length+D.regimes.length+D.sources.length+D.episodes.length;const count=Math.max(18,Math.min(120,evidenceCount));for(let i=0;i<count;i++){{const a=(i/count)*Math.PI*2*3.7,r=(.08+.38*Math.sqrt((i+1)/count))*Math.min(W,H),v=src[i%Math.max(1,src.length)]||{{kind:"idle",value:.2,label:"awaiting evidence"}};nodes.push({{x:W*.5+Math.cos(a)*r*.72,y:H*.52+Math.sin(a)*r*.46,v,phase:i*.71}});}}}}
function color(v,a){{if(v.state==="negative")return "rgba(255,82,112,"+a+")";if(v.state==="positive")return "rgba(75,245,164,"+a+")";if(v.kind==="knowledge")return "rgba(177,102,255,"+a+")";if(v.kind==="source")return "rgba(255,194,94,"+a+")";if(v.kind==="episode")return "rgba(95,234,205,"+a+")";return "rgba(80,199,255,"+a+")";}}
function frame(){{t+=.018;x.clearRect(0,0,W,H);for(const n of nodes){{n.x=Math.max(14,Math.min(W-14,n.x));n.y=Math.max(14,Math.min(H-14,n.y));}}x.save();x.translate(Math.sin(t*.2)*2,Math.cos(t*.17)*2);for(let i=0;i<nodes.length;i++){{let a=nodes[i],b=nodes[(i*7+5)%nodes.length],d=Math.hypot(a.x-b.x,a.y-b.y);if(d<Math.min(W,H)*.38){{x.strokeStyle=color(a.v,.08+.08*Math.sin(t+a.phase));x.lineWidth=.7;x.beginPath();x.moveTo(a.x,a.y);x.quadraticCurveTo(W*.5,H*.5,b.x,b.y);x.stroke();}}}}for(const n of nodes){{let pulse=1+.35*Math.sin(t*2.4+n.phase),r=2.4+5*(n.v.value||.2)*pulse;x.shadowBlur=16;x.shadowColor=color(n.v,.8);x.fillStyle=color(n.v,.9);x.beginPath();x.arc(n.x,n.y,r,0,Math.PI*2);x.fill();}}x.restore();requestAnimationFrame(frame);}}
new ResizeObserver(resize).observe(c);resize();frame();
</script></body></html>""")
st.caption(
    "Persistent engineering/research memory + live evidence summary. "
    "Read-only dashboard; execution authority: NONE."
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
