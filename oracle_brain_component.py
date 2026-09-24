from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"Object of type {value.__class__.__name__} is not JSON serializable")


def _safe_json(value: Any) -> str:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
            default=_json_default,
        )
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def render_oracle_brain_component(snapshot: dict[str, Any]) -> str:
    growth = snapshot.get("growth") or {}
    activity = snapshot.get("learning_activity") or {}
    payload = {
        "generated_at": snapshot.get("generated_at"),
        "growth": growth,
        "activity": activity,
        "entries": [
            {
                "key": item.get("brain_key"),
                "category": item.get("category"),
                "title": item.get("title"),
                "confidence": item.get("confidence"),
            }
            for item in (snapshot.get("entries") or [])[:48]
        ],
        "sources": [
            {
                "key": item.get("source_key"),
                "title": item.get("title"),
                "provider": item.get("provider"),
                "category": item.get("category"),
                "confidence": item.get("confidence"),
                "freshness": item.get("freshness_score"),
                "symbol": item.get("symbol"),
            }
            for item in (snapshot.get("sources") or [])[:80]
        ],
        "episodes": [
            {
                "key": item.get("episode_key"),
                "symbol": item.get("symbol"),
                "strategy": item.get("strategy"),
                "regime": item.get("regime"),
                "pnl": item.get("net_pnl"),
                "confidence": item.get("confidence"),
            }
            for item in (snapshot.get("episodes") or [])[:80]
        ],
        "regimes": [
            {
                "key": f"{item.get('market')}::{item.get('strategy')}::{item.get('regime')}",
                "market": item.get("market"),
                "strategy": item.get("strategy"),
                "regime": item.get("regime"),
                "samples": item.get("samples"),
                "expectancy": item.get("expectancy"),
                "state": item.get("evidence_state"),
            }
            for item in (snapshot.get("regime_economics") or [])[:40]
        ],
        "links": [
            {
                "source": item.get("source_key"),
                "target": item.get("target_key"),
                "relation": item.get("relation"),
                "confidence": item.get("confidence"),
                "evidence_count": item.get("evidence_count"),
            }
            for item in (snapshot.get("concept_links") or [])[:100]
        ],
    }
    encoded = _safe_json(payload)
    return r"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<style>
*{box-sizing:border-box}
html,body{margin:0;background:#03060b;color:#eef8ff;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;overflow:hidden}
#wrap{position:relative;height:590px;min-height:520px;border:1px solid #203a54;border-radius:24px;overflow:hidden;background:radial-gradient(circle at 50% 48%,#10213a 0,#07101c 43%,#02050a 82%);box-shadow:inset 0 0 90px rgba(30,132,190,.12)}
#brain{position:absolute;inset:0;width:100%;height:100%;display:block}
.panel{position:absolute;z-index:4;border:1px solid rgba(100,180,225,.33);background:rgba(3,10,18,.80);backdrop-filter:blur(14px);box-shadow:0 16px 42px rgba(0,0,0,.32)}
.status{left:16px;top:16px;max-width:340px;border-radius:14px;padding:11px 13px}
.status .eyebrow{font-size:10px;font-weight:900;letter-spacing:.14em;color:#72ddff;text-transform:uppercase}
.status .state{font-size:20px;font-weight:900;margin-top:4px}
.status .sub{font-size:11px;color:#a9c2d2;line-height:1.45;margin-top:4px}
.metrics{right:16px;top:16px;display:grid;grid-template-columns:repeat(2,minmax(122px,1fr));gap:6px;border-radius:14px;padding:7px;max-width:340px}
.metric{padding:8px 9px;border-radius:10px;background:rgba(8,24,38,.73);border:1px solid rgba(83,151,190,.18)}
.metric b{display:block;font-size:15px}.metric span{display:block;font-size:9px;color:#9ab4c5;margin-top:2px;text-transform:uppercase;letter-spacing:.08em}
.legend{left:16px;bottom:14px;display:flex;gap:8px;flex-wrap:wrap;border-radius:12px;padding:8px 10px;font-size:10px;color:#abc2cf}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:4px;vertical-align:-1px;box-shadow:0 0 8px currentColor}
.tip{right:16px;bottom:14px;max-width:310px;border-radius:12px;padding:9px 11px;font-size:10px;color:#aac1cf;line-height:1.4}
#hover{display:none;position:absolute;z-index:6;pointer-events:none;min-width:150px;max-width:260px;padding:8px 10px;border:1px solid rgba(105,211,255,.5);border-radius:10px;background:rgba(2,9,15,.94);box-shadow:0 10px 28px rgba(0,0,0,.4)}
#hover.show{display:block}#hover b{display:block;font-size:11px}#hover span{display:block;font-size:9px;color:#a7bdca;margin-top:3px;line-height:1.35}
@media(max-width:720px){
 #wrap{height:610px;border-radius:16px}
 .status{left:8px;top:8px;max-width:205px;padding:8px 9px}.status .state{font-size:14px}.status .sub{font-size:9px}
 .metrics{right:8px;top:8px;grid-template-columns:1fr;max-width:145px}.metric{padding:6px 7px}.metric b{font-size:11px}.metric span{font-size:7px}
 .legend{left:8px;right:8px;bottom:8px;font-size:8px}.tip{display:none}
}
</style>
</head>
<body>
<div id="wrap">
<canvas id="brain"></canvas>
<div class="panel status">
  <div class="eyebrow">Oracle Brain · evidence monitor</div>
  <div class="state" id="learningState">--</div>
  <div class="sub" id="learningDetail">Reading persisted learning state…</div>
</div>
<div class="panel metrics">
  <div class="metric"><b id="knowledgeUnits">0</b><span>retained evidence units</span></div>
  <div class="metric"><b id="newCycle">0</b><span>changed this cycle</span></div>
  <div class="metric"><b id="relationships">0</b><span>learned relationships</span></div>
  <div class="metric"><b id="exactOutcomes">0</b><span>exact outcomes</span></div>
</div>
<div class="panel legend">
  <span style="color:#ffc25e"><i class="dot"></i>sources</span>
  <span style="color:#55f0ae"><i class="dot"></i>positive outcomes</span>
  <span style="color:#ff6685"><i class="dot"></i>negative outcomes</span>
  <span style="color:#b277ff"><i class="dot"></i>durable lessons</span>
  <span style="color:#59cfff"><i class="dot"></i>regime evidence / synapses</span>
</div>
<div class="panel tip">Brain shape and glow show retained evidence. Pulses intensify only when the latest learning cycle records new or revised evidence. Hover a neuron for its real source.</div>
<div id="hover"></div>
<script id="data" type="application/json">__DATA__</script>
<script>
const D=JSON.parse(document.getElementById("data").textContent);
const wrap=document.getElementById("wrap"),canvas=document.getElementById("brain"),ctx=canvas.getContext("2d"),hover=document.getElementById("hover");
let W=1,H=1,dpr=1,t=0,nodes=[],edges=[],pointer={x:-999,y:-999};
const G=D.growth||{},A=D.activity||{};
document.getElementById("knowledgeUnits").textContent=Number(G.knowledge_units||0).toLocaleString();
document.getElementById("newCycle").textContent=Number(A.learned_this_cycle||0).toLocaleString();
document.getElementById("relationships").textContent=Number(G.relationships||0).toLocaleString();
document.getElementById("exactOutcomes").textContent=Number(G.exact_outcomes||0).toLocaleString();

function syncAge(){
  if(!A.last_sync_at)return null;
  const ms=Date.now()-Date.parse(A.last_sync_at);
  return Number.isFinite(ms)?Math.max(0,ms/1000):null;
}
function fmtAge(s){
  if(s==null)return "never";
  if(s<60)return Math.round(s)+"s ago";
  if(s<3600)return Math.round(s/60)+"m ago";
  return (s/3600).toFixed(1)+"h ago";
}
function statusNow(){
  const age=syncAge();
  let state=String(A.status||"NOT SYNCED");
  if(age!=null&&age>2700)state="STALE";
  const changed=Number(A.learned_this_cycle||0);
  const detail=state==="LEARNING"
    ? "+"+changed+" evidence change"+(changed===1?"":"s")+" in the latest cycle · sync "+fmtAge(age)
    : state==="SYNCED — NO NEW EVIDENCE"
    ? "Learner ran successfully; no new/revised evidence was recorded · sync "+fmtAge(age)
    : state==="STALE"
    ? "Learning sync is older than 45 minutes · last sync "+fmtAge(age)
    : "No completed Brain learning sync is recorded yet.";
  const el=document.getElementById("learningState");el.textContent=state;
  el.style.color=state==="LEARNING"?"#55f0ae":state==="STALE"||state==="NOT SYNCED"?"#ff7a8f":"#72ddff";
  document.getElementById("learningDetail").textContent=detail;
}
statusNow();setInterval(statusNow,1000);

function hash(str){
  let h=2166136261>>>0;
  for(let i=0;i<str.length;i++){h^=str.charCodeAt(i);h=Math.imul(h,16777619);}
  return h>>>0;
}
function rand(seed){let x=seed||1;return()=>{x^=x<<13;x^=x>>>17;x^=x<<5;return((x>>>0)%100000)/100000;};}
function brainMask(nx,ny){
  const l=((nx-.37)/.27)**2+((ny-.49)/.34)**2<1;
  const r=((nx-.63)/.27)**2+((ny-.49)/.34)**2<1;
  const low=((nx-.50)/.31)**2+((ny-.67)/.22)**2<1;
  return (l||r||low)&&ny>.15&&ny<.88;
}
function pointFor(key,index){
  const R=rand(hash(String(key)+"|"+index));
  for(let i=0;i<120;i++){
    const nx=.18+R()*.64,ny=.17+R()*.68;
    if(brainMask(nx,ny))return {x:nx*W,y:ny*H};
  }
  return {x:W*.5,y:H*.5};
}
function colorFor(n,a=1){
  if(n.kind==="lesson")return "rgba(178,119,255,"+a+")";
  if(n.kind==="source")return "rgba(255,194,94,"+a+")";
  if(n.kind==="episode")return Number(n.pnl||0)<0?"rgba(255,102,133,"+a+")":"rgba(85,240,174,"+a+")";
  if(n.kind==="regime")return n.state==="negative"?"rgba(255,102,133,"+a+")":"rgba(89,207,255,"+a+")";
  return "rgba(89,207,255,"+a+")";
}
function buildNodes(){
  const raw=[];
  (D.entries||[]).forEach(v=>raw.push({kind:"lesson",key:"brain:"+String(v.key||v.title),label:v.title||v.key,sub:v.category||"durable lesson",value:Number(v.confidence||.5)}));
  (D.sources||[]).forEach(v=>raw.push({kind:"source",key:"source:"+String(v.key||v.title),label:v.title||v.key,sub:(v.provider||"source")+" · "+(v.category||"uncategorized"),value:Number(v.confidence||.4)*Number(v.freshness==null?1:v.freshness)}));
  (D.episodes||[]).forEach(v=>raw.push({kind:"episode",key:"episode:"+String(v.key||v.symbol),label:(v.symbol||"outcome")+" · "+(v.strategy||"strategy"),sub:(v.regime||"unknown")+" · P/L "+Number(v.pnl||0).toFixed(4),value:Number(v.confidence||.5),pnl:Number(v.pnl||0)}));
  (D.regimes||[]).forEach(v=>raw.push({kind:"regime",key:"regime:"+String(v.key),label:(v.market?String(v.market).toUpperCase()+" · ":"")+(v.strategy||"strategy")+" · "+(v.regime||"regime"),sub:String(v.samples||0)+" samples · expectancy "+Number(v.expectancy||0).toFixed(5),value:Math.min(1,Number(v.samples||0)/200),state:v.state}));
  nodes=raw.slice(0,210).map((n,i)=>({...n,...pointFor(n.key,i),phase:(hash(n.key)%628)/100,r:2.3+Math.min(5.5,5.5*Math.max(.08,n.value||.1))}));
  const map=new Map(nodes.map((n,i)=>[n.key,i]));
  edges=[];
  (D.links||[]).forEach((e,i)=>{
    const candidates=[
      "source:"+String(e.source||""),
      "brain:"+String(e.source||""),
      "regime:"+String(e.source||"")
    ];
    const targets=[
      "source:"+String(e.target||""),
      "brain:"+String(e.target||""),
      "regime:"+String(e.target||"")
    ];
    let a=null,b=null;
    for(const k of candidates)if(map.has(k)){a=map.get(k);break;}
    for(const k of targets)if(map.has(k)){b=map.get(k);break;}
    if(a!=null&&b!=null&&a!==b)edges.push({a,b,confidence:Number(e.confidence||.3),count:Number(e.evidence_count||1),relation:e.relation||"linked"});
  });
  if(edges.length<Math.min(18,Math.floor(nodes.length/2))){
    for(let i=0;i<nodes.length-3&&edges.length<36;i+=3)edges.push({a:i,b:(i*7+5)%nodes.length,confidence:.18,count:1,aggregate:true});
  }
}
function resize(){
  const r=wrap.getBoundingClientRect();dpr=Math.min(2,window.devicePixelRatio||1);
  canvas.width=Math.max(1,Math.floor(r.width*dpr));canvas.height=Math.max(1,Math.floor(r.height*dpr));
  W=r.width;H=r.height;ctx.setTransform(dpr,0,0,dpr,0,0);buildNodes();
}
new ResizeObserver(resize).observe(wrap);resize();

function brainPath(side){
  const cx=W*.5,top=H*.155,bottom=H*.865;
  const p=new Path2D();
  if(side==="left"){
    p.moveTo(cx-3,top+H*.04);
    p.bezierCurveTo(W*.40,H*.13,W*.28,H*.17,W*.21,H*.29);
    p.bezierCurveTo(W*.12,H*.34,W*.11,H*.49,W*.15,H*.59);
    p.bezierCurveTo(W*.11,H*.70,W*.19,H*.83,W*.31,H*.84);
    p.bezierCurveTo(W*.37,H*.91,W*.46,H*.87,cx-4,bottom);
    p.bezierCurveTo(W*.48,H*.73,W*.48,H*.56,cx-3,top+H*.04);
  }else{
    p.moveTo(cx+3,top+H*.04);
    p.bezierCurveTo(W*.60,H*.13,W*.72,H*.17,W*.79,H*.29);
    p.bezierCurveTo(W*.88,H*.34,W*.89,H*.49,W*.85,H*.59);
    p.bezierCurveTo(W*.89,H*.70,W*.81,H*.83,W*.69,H*.84);
    p.bezierCurveTo(W*.63,H*.91,W*.54,H*.87,cx+4,bottom);
    p.bezierCurveTo(W*.52,H*.73,W*.52,H*.56,cx+3,top+H*.04);
  }
  p.closePath();return p;
}
function drawBrainBase(){
  const learning=String(A.status||"").startsWith("LEARNING");
  const changed=Number(A.learned_this_cycle||0);
  const pulse=learning?(.14+.08*Math.sin(t*3.1)):0.05;
  const units=Math.max(0,Number(G.knowledge_units||0));
  const glow=Math.min(.34,.08+Math.log1p(units)*.025)+pulse;
  ctx.save();
  ctx.shadowBlur=learning?34:22;ctx.shadowColor="rgba(89,207,255,"+Math.min(.85,glow*1.8)+")";
  for(const side of ["left","right"]){
    const p=brainPath(side);
    const grad=ctx.createRadialGradient(W*.5,H*.48,20,W*.5,H*.48,Math.min(W,H)*.43);
    grad.addColorStop(0,"rgba(34,72,105,"+(0.60+glow)+")");
    grad.addColorStop(.65,"rgba(16,39,65,.76)");
    grad.addColorStop(1,"rgba(6,17,30,.92)");
    ctx.fillStyle=grad;ctx.fill(p);
    ctx.strokeStyle="rgba(108,218,255,"+(0.42+glow)+")";ctx.lineWidth=1.5;ctx.stroke(p);
  }
  ctx.shadowBlur=0;
  ctx.strokeStyle="rgba(119,192,226,.28)";ctx.lineWidth=1;
  const gyri=[
    [.28,.29,.34,.22,.43,.28,.46,.36],[.20,.42,.29,.35,.41,.39,.47,.47],
    [.18,.57,.28,.51,.39,.55,.47,.62],[.22,.70,.31,.65,.39,.70,.47,.77],
    [.72,.29,.66,.22,.57,.28,.54,.36],[.80,.42,.71,.35,.59,.39,.53,.47],
    [.82,.57,.72,.51,.61,.55,.53,.62],[.78,.70,.69,.65,.61,.70,.53,.77]
  ];
  for(const g of gyri){ctx.beginPath();ctx.moveTo(g[0]*W,g[1]*H);ctx.bezierCurveTo(g[2]*W,g[3]*H,g[4]*W,g[5]*H,g[6]*W,g[7]*H);ctx.stroke();}
  ctx.strokeStyle="rgba(145,215,245,.42)";ctx.lineWidth=1.4;ctx.beginPath();ctx.moveTo(W*.5,H*.20);ctx.bezierCurveTo(W*.485,H*.38,W*.515,H*.61,W*.5,H*.84);ctx.stroke();
  const stemGrad=ctx.createLinearGradient(0,H*.75,0,H*.96);stemGrad.addColorStop(0,"rgba(45,79,102,.75)");stemGrad.addColorStop(1,"rgba(14,29,41,.92)");
  ctx.fillStyle=stemGrad;ctx.beginPath();ctx.moveTo(W*.46,H*.79);ctx.bezierCurveTo(W*.47,H*.87,W*.47,H*.92,W*.49,H*.96);ctx.lineTo(W*.51,H*.96);ctx.bezierCurveTo(W*.53,H*.92,W*.53,H*.87,W*.54,H*.79);ctx.closePath();ctx.fill();
  ctx.restore();
  return changed;
}
function draw(){
  t+=.016;ctx.clearRect(0,0,W,H);
  drawBrainBase();
  for(const e of edges){
    const a=nodes[e.a],b=nodes[e.b];if(!a||!b)continue;
    const alpha=e.aggregate?.055:Math.min(.36,.08+e.confidence*.25);
    ctx.strokeStyle="rgba(89,207,255,"+alpha+")";ctx.lineWidth=e.aggregate?.6:Math.min(2,.6+Math.log1p(e.count)*.35);
    ctx.beginPath();ctx.moveTo(a.x,a.y);const mx=(a.x+b.x)/2,my=(a.y+b.y)/2-Math.min(30,Math.abs(a.x-b.x)*.12);ctx.quadraticCurveTo(mx,my,b.x,b.y);ctx.stroke();
    if(!e.aggregate&&String(A.status||"")==="LEARNING"){
      const q=(t*.20+e.a*.031)%1;
      const x1=(1-q)*(1-q)*a.x+2*(1-q)*q*mx+q*q*b.x;
      const y1=(1-q)*(1-q)*a.y+2*(1-q)*q*my+q*q*b.y;
      ctx.fillStyle="rgba(122,232,255,.9)";ctx.beginPath();ctx.arc(x1,y1,1.5,0,Math.PI*2);ctx.fill();
    }
  }
  for(const n of nodes){
    const learning=String(A.status||"")==="LEARNING";
    const pulse=1+(learning?.18:.07)*Math.sin(t*2.6+n.phase);
    const r=n.r*pulse;
    ctx.shadowBlur=learning?16:10;ctx.shadowColor=colorFor(n,.78);ctx.fillStyle=colorFor(n,.93);ctx.beginPath();ctx.arc(n.x,n.y,r,0,Math.PI*2);ctx.fill();
  }
  ctx.shadowBlur=0;
  requestAnimationFrame(draw);
}
draw();

function nearest(px,py){
  let best=null,bd=18;
  for(const n of nodes){const d=Math.hypot(px-n.x,py-n.y);if(d<bd){bd=d;best=n;}}
  return best;
}
canvas.addEventListener("pointermove",e=>{
  const r=canvas.getBoundingClientRect(),px=e.clientX-r.left,py=e.clientY-r.top,n=nearest(px,py);
  if(!n){hover.classList.remove("show");canvas.style.cursor="default";return;}
  canvas.style.cursor="pointer";hover.innerHTML="<b>"+String(n.label||"Evidence").replace(/[<>]/g,"")+"</b><span>"+String(n.kind).toUpperCase()+" · "+String(n.sub||"").replace(/[<>]/g,"")+"</span>";
  hover.style.left=Math.min(W-275,Math.max(8,px+12))+"px";hover.style.top=Math.min(H-68,Math.max(8,py+12))+"px";hover.classList.add("show");
});
canvas.addEventListener("pointerleave",()=>hover.classList.remove("show"));
</script>
</div>
</body>
</html>""".replace("__DATA__", encoded)


__all__ = ["render_oracle_brain_component"]
