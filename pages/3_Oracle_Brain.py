from __future__ import annotations

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

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
}
import json
_payload = json.dumps(neural_payload).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
components.html(f"""<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
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
function make(){{nodes=[];const src=[...D.entries.map((v,i)=>({{...v,kind:"knowledge",value:v.confidence||.5,label:v.title}})),...D.regimes.map((v,i)=>({{...v,kind:"regime",value:Math.min(1,(v.samples||0)/200),label:v.strategy+" · "+v.regime}}))];const count=Math.max(18,src.length);for(let i=0;i<count;i++){{const a=(i/count)*Math.PI*2*3.7,r=(.08+.38*Math.sqrt((i+1)/count))*Math.min(W,H),v=src[i%Math.max(1,src.length)]||{{kind:"idle",value:.2,label:"awaiting evidence"}};nodes.push({{x:W*.5+Math.cos(a)*r*.72,y:H*.52+Math.sin(a)*r*.46,v,phase:i*.71}});}}}}
function color(v,a){{if(v.state==="negative")return "rgba(255,82,112,"+a+")";if(v.state==="positive")return "rgba(75,245,164,"+a+")";if(v.kind==="knowledge")return "rgba(177,102,255,"+a+")";return "rgba(80,199,255,"+a+")";}}
function frame(){{t+=.018;x.clearRect(0,0,W,H);x.save();x.translate(Math.sin(t*.2)*2,Math.cos(t*.17)*2);for(let i=0;i<nodes.length;i++){{let a=nodes[i],b=nodes[(i*7+5)%nodes.length],d=Math.hypot(a.x-b.x,a.y-b.y);if(d<Math.min(W,H)*.38){{x.strokeStyle=color(a.v,.08+.08*Math.sin(t+a.phase));x.lineWidth=.7;x.beginPath();x.moveTo(a.x,a.y);x.quadraticCurveTo(W*.5,H*.5,b.x,b.y);x.stroke();}}}}for(const n of nodes){{let pulse=1+.35*Math.sin(t*2.4+n.phase),r=2.4+5*(n.v.value||.2)*pulse;x.shadowBlur=16;x.shadowColor=color(n.v,.8);x.fillStyle=color(n.v,.9);x.beginPath();x.arc(n.x,n.y,r,0,Math.PI*2);x.fill();}}x.restore();requestAnimationFrame(frame);}}
new ResizeObserver(resize).observe(c);resize();frame();
</script></body></html>""", height=440, scrolling=False)
st.caption("Living evidence map. Open details only when you want the numbers.")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Knowledge", summary["active_entries"])
c2.metric("Experiments", summary["experiments"])
c3.metric("Avoid", summary["mature_negative_regimes"])
c4.metric("Promising", summary["mature_positive_regimes"])

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

with st.expander("How the Brain learns"):
    for item in snapshot["doctrine"]:
        st.markdown(f"**{item['title']}** — {item['body']}")

if snapshot["derived_lessons"]:
    st.subheader("Evidence-derived lessons")
    for lesson in snapshot["derived_lessons"]:
        if lesson["level"] == "warning":
            st.warning(f"**{lesson['title']}** — {lesson['body']}")
        else:
            st.info(f"**{lesson['title']}** — {lesson['body']}")

entries = snapshot["entries"]
with st.expander("Knowledge memory"):
    if entries:
        st.dataframe(
            pd.DataFrame([
                {
                    "Category": item["category"],
                    "Title": item["title"],
                    "Confidence": item["confidence"],
                    "Evidence": item["evidence_ref"],
                }
                for item in entries
            ]),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No active knowledge yet.")

with st.expander("Detailed market evidence"):
    st.caption("Positive = promising. Negative = avoid/research. Insufficient = still learning.")
    regimes = snapshot["regime_economics"]
    if regimes:
        st.dataframe(
            pd.DataFrame([
                {
                    "Strategy": item["strategy"],
                    "Regime": item["regime"],
                    "Samples": item["samples"],
                    "Expectancy": item["expectancy"],
                    "State": item["evidence_state"],
                }
                for item in regimes
            ]),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No regime evidence yet.")

with st.expander("System status"):
    st.caption("Read-only status. This does not control trading.")
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
