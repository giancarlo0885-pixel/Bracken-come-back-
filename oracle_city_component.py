from __future__ import annotations

import json
from typing import Any


def _safe_json(value: Any) -> str:
    return (
        json.dumps(value, ensure_ascii=True, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def render_oracle_city_component(snapshot: dict[str, Any]) -> str:
    encoded = _safe_json(snapshot)
    document = r"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<style>
*{box-sizing:border-box}
html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#02050a;color:#eef8ff;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
#app{position:relative;width:100%;height:100%;min-height:780px;overflow:hidden;border:1px solid #17394d;border-radius:22px;background:#02050a;box-shadow:inset 0 0 80px rgba(8,57,87,.2)}
#stage{position:absolute;inset:0}
#status{position:absolute;z-index:20;left:50%;top:50%;transform:translate(-50%,-50%);padding:10px 13px;border-radius:10px;background:rgba(2,8,12,.9);border:1px solid #28516a;color:#b8d5e4;font-size:11px}
.hud{position:absolute;z-index:10;pointer-events:none}
.topbar{left:14px;right:14px;top:14px;display:flex;gap:10px;align-items:flex-start;justify-content:space-between}
.brand,.toolbar,.inspector,.replay{pointer-events:auto;border:1px solid rgba(81,159,197,.38);background:linear-gradient(180deg,rgba(3,12,19,.9),rgba(2,8,13,.82));backdrop-filter:blur(16px);box-shadow:0 18px 58px rgba(0,0,0,.35)}
.brand{max-width:370px;border-radius:14px;padding:10px 12px}
.brand b{font-size:13px;letter-spacing:.11em;text-transform:uppercase}
.chips{display:flex;flex-wrap:wrap;gap:4px;margin-top:6px}
.chip{display:inline-flex;align-items:center;min-height:24px;padding:3px 8px;border:1px solid #28516a;border-radius:999px;background:rgba(6,25,37,.86);font-size:10px;font-weight:850;color:#dff5ff}
.toolbar{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:6px;border-radius:14px;padding:6px;max-width:530px}
button{appearance:none;border:1px solid #28516a;border-radius:9px;background:#071722;color:#e9f7ff;padding:9px 11px;min-height:38px;font-size:11px;font-weight:850;cursor:pointer;letter-spacing:.025em}
button:hover{border-color:#5cbbe5;background:#0c2432}
button.active{border-color:#4bf49b;color:#4bf49b;box-shadow:0 0 18px rgba(77,244,155,.15)}
.inspector{display:none;left:14px;bottom:14px;width:min(360px,calc(100% - 28px));border-radius:15px;padding:11px 12px}.inspector.open{display:block}
.inspector .eyebrow{font-size:10px;font-weight:950;letter-spacing:.12em;text-transform:uppercase;color:#67d6ff}
.inspector h3{margin:5px 0 3px;font-size:20px}
.inspector .metric{font-size:14px;font-weight:850;margin:5px 0 7px;color:#effaff}
.inspector p{margin:0;color:#bdd0d9;font-size:12px;line-height:1.55}
.replay{display:none;right:14px;bottom:14px;width:min(430px,calc(100% - 28px));border-radius:15px;padding:9px 10px}.replay.open{display:block}
.replay-head{display:flex;gap:8px;align-items:center}
.replay-title{min-width:0;flex:1}
.replay-title b{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:12px}
.replay-title span{display:block;color:#abc3cf;font-size:10px;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
input[type=range]{width:100%;accent-color:#55d4ff}
.legend,.minimap,.label{display:none}
#hovercard{display:none;position:absolute;z-index:18;min-width:150px;max-width:230px;padding:8px 10px;border:1px solid rgba(92,187,229,.55);border-radius:10px;background:rgba(2,10,16,.94);backdrop-filter:blur(12px);box-shadow:0 12px 34px rgba(0,0,0,.42);pointer-events:none;color:#f3fbff}
#hovercard.show{display:block}
#hovercard b{display:block;font-size:11px;line-height:1.25}
#hovercard span{display:block;margin-top:2px;color:#a9c3d0;font-size:9px;line-height:1.3}
@media(max-width:720px){
  #app{min-height:860px;border-radius:14px}
  .topbar{left:8px;right:8px;top:8px;gap:6px}
  .brand{max-width:176px;padding:8px 9px}.brand b{font-size:9px}
  .chips{margin-top:5px;gap:3px}.chip{font-size:8px;min-height:20px;padding:2px 5px}
  .toolbar{display:grid;grid-template-columns:1fr 1fr;gap:4px;padding:4px;max-width:176px}
  button{padding:7px 8px;min-height:44px;font-size:9px}
  .legend,.minimap,.label{display:none}
  #hovercard{max-width:190px}
  .inspector{left:8px;right:8px;width:auto;bottom:66px;padding:10px;max-height:150px;overflow:auto}
  .inspector h3{font-size:14px}.inspector .metric{font-size:11px}.inspector p{font-size:10px}
  .replay{left:8px;right:8px;width:auto;bottom:8px;padding:8px 9px}
}
</style>
<script type="importmap">
{"imports":{
"three":"https://cdn.jsdelivr.net/npm/three@0.160.1/build/three.module.js",
"three/addons/":"https://cdn.jsdelivr.net/npm/three@0.160.1/examples/jsm/"
}}
</script>
</head>
<body>
<div id="app">
  <div id="stage"></div>
  <div id="status">Building cinematic Oracle City…</div>
  <div id="hovercard"></div>
  <div class="hud topbar">
    <div class="brand">
      <b>GARIBALDI MARKET ORACLE · ORACLE CITY</b>
      <div class="chips">
        <span class="chip" id="cityMood">CITY: --</span>
        <span class="chip" id="aeveProgress">AEVE: -- / 1000</span>
        <span class="chip" id="paperState">PAPER</span>
      </div>
    </div>
    <div class="toolbar">
      <button id="reset">CITY</button>
      <button id="street" aria-label="STREET VIEW">STREET</button>
      <button id="topview">TOP</button>
      <button id="workers" class="active">WORKERS</button>
      <button id="traffic" class="active">TRAFFIC</button>
      <button id="autorotate" aria-label="CINEMATIC">CINEMA</button>
      <button id="brain" aria-label="BRAIN MAP" title="Decision provenance network">BRAIN</button>
      <button id="replayToggle">REPLAY</button>
    </div>
  </div>
  <div class="hud inspector" id="inspector"></div>
  <div class="hud replay" id="replayPanel">
    <div class="replay-head">
      <button id="play">PLAY</button>
      <div class="replay-title"><b id="replayTitle">Recent decision replay</b><span id="replayDetail">Review persisted Oracle decisions, intelligence events, and paper trades.</span></div>
    </div>
    <input id="timeline" type="range" min="0" max="0" value="0" step="1">
  </div>
</div>
<script type="application/json" id="oracle-data">__ORACLE_DATA__</script>
<script type="module">
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { CSS2DRenderer, CSS2DObject } from "three/addons/renderers/CSS2DRenderer.js";

const DATA=JSON.parse(document.getElementById("oracle-data").textContent);
const app=document.getElementById("app");
const stage=document.getElementById("stage");
const status=document.getElementById("status");
const inspector=document.getElementById("inspector");
const hovercard=document.getElementById("hovercard");
const replayPanel=document.getElementById("replayPanel");
const isMobileDevice=window.matchMedia("(max-width:720px)").matches;
const prefersReduced=window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const DETAIL=isMobileDevice?0.48:1;
const colors={online:0x4df49b,waiting:0xffd166,offline:0xff6767};
const aeveData=DATA.aeve||{};
document.getElementById("cityMood").textContent="CITY MOOD: "+String(DATA.city_mood||"UNKNOWN");
document.getElementById("aeveProgress").textContent="AEVE: "+(aeveData.accepted==null?"UNAVAILABLE":String(aeveData.accepted))+" / "+String(aeveData.target||1000);
const safety=DATA.safety||{};
document.getElementById("paperState").textContent=(String(safety.execution_mode||DATA.execution_mode||"paper").toUpperCase())+" · LIVE "+(safety.live_trading_armed?"ARMED":"DISARMED");

function esc(value){
  return String(value==null?"":value).replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
}
function money(value){const n=Number(value||0);return "$"+n.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});}
function stateColor(state){return colors[state]||colors.waiting;}
function mat(color,rough=.5,metal=.18,emissive=0,ei=0){
  return new THREE.MeshStandardMaterial({color,roughness:rough,metalness:metal,emissive,emissiveIntensity:ei});
}

const scene=new THREE.Scene();
scene.background=new THREE.Color(0x02060b);
scene.fog=new THREE.FogExp2(0x02060b,isMobileDevice?.018:.013);

const camera=new THREE.PerspectiveCamera(isMobileDevice?52:46,1,.1,280);
camera.position.set(30,22,38);

const renderer=new THREE.WebGLRenderer({antialias:!isMobileDevice,powerPreference:"high-performance"});
renderer.setPixelRatio(Math.min(window.devicePixelRatio||1,isMobileDevice?1.25:1.8));
renderer.outputColorSpace=THREE.SRGBColorSpace;
renderer.shadowMap.enabled=!isMobileDevice;
renderer.shadowMap.type=THREE.PCFSoftShadowMap;
renderer.toneMapping=THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure=1.08;
stage.appendChild(renderer.domElement);

const labelRenderer=new CSS2DRenderer();
labelRenderer.domElement.style.position="absolute";
labelRenderer.domElement.style.inset="0";
labelRenderer.domElement.style.pointerEvents="none";
stage.appendChild(labelRenderer.domElement);

const controls=new OrbitControls(camera,renderer.domElement);
controls.target.set(2.5,2.7,0);
controls.enableDamping=true;
controls.dampingFactor=.06;
controls.minDistance=9;
controls.maxDistance=88;
controls.maxPolarAngle=Math.PI*.49;
controls.minPolarAngle=.12;
controls.enablePan=!isMobileDevice;

scene.add(new THREE.HemisphereLight(0x7bc6ff,0x06100d,1.8));
const moon=new THREE.DirectionalLight(0xb9e7ff,2.2);moon.position.set(-18,26,18);moon.castShadow=!isMobileDevice;scene.add(moon);
const cityGlow=new THREE.PointLight(0x21a9ff,80,70,2);cityGlow.position.set(2,11,-8);scene.add(cityGlow);
const warmGlow=new THREE.PointLight(0xffb257,45,48,2);warmGlow.position.set(10,7,8);scene.add(warmGlow);

const stars=new THREE.BufferGeometry();
const starCount=isMobileDevice?160:480;
const starPositions=new Float32Array(starCount*3);
for(let i=0;i<starCount;i++){starPositions[i*3]=(Math.random()-.5)*150;starPositions[i*3+1]=18+Math.random()*55;starPositions[i*3+2]=(Math.random()-.5)*120;}
stars.setAttribute("position",new THREE.BufferAttribute(starPositions,3));
scene.add(new THREE.Points(stars,new THREE.PointsMaterial({color:0x91cfff,size:.08,transparent:true,opacity:.7})));

const ground=new THREE.Mesh(new THREE.PlaneGeometry(92,66),mat(0x071117,.96,.03));
ground.rotation.x=-Math.PI/2;ground.position.y=-.035;ground.receiveShadow=true;scene.add(ground);

function addPlane(x,z,w,d,color,y=.003){
  const mesh=new THREE.Mesh(new THREE.PlaneGeometry(w,d),mat(color,1,0));
  mesh.rotation.x=-Math.PI/2;mesh.position.set(x,y,z);mesh.receiveShadow=true;scene.add(mesh);return mesh;
}
function addRoad(x,z,w,d){return addPlane(x,z,w,d,0x091116,.012);}
function addSidewalk(x,z,w,d){return addPlane(x,z,w,d,0x16242b,.02);}
addRoad(2.5,0,60,2.0);addRoad(2.5,9.5,60,1.45);addRoad(2.5,-9.5,60,1.45);
addRoad(-12,0,1.6,32);addRoad(-3.5,0,1.35,32);addRoad(7,0,1.45,32);addRoad(17,0,1.45,32);
addSidewalk(2.5,1.55,60,.7);addSidewalk(2.5,-1.55,60,.7);
addSidewalk(2.5,10.55,60,.48);addSidewalk(2.5,8.45,60,.48);
addSidewalk(2.5,-10.55,60,.48);addSidewalk(2.5,-8.45,60,.48);

const laneMat=new THREE.MeshBasicMaterial({color:0xc5a95c,transparent:true,opacity:.45});
for(let x=-27;x<32;x+=3.2){
  const dash=new THREE.Mesh(new THREE.PlaneGeometry(1.45,.055),laneMat);dash.rotation.x=-Math.PI/2;dash.position.set(x,.025,0);scene.add(dash);
}

const nodeObjects=new Map();
const interactables=[];
const workerObjects=[];
const vehicleObjects=[];
const flowObjects=[];
const portfolio_towers=DATA.portfolio_towers||[];

function addLabel(group,title,metric,y){ return; }

function box(parent,w,h,d,x,y,z,material){
  const m=new THREE.Mesh(new THREE.BoxGeometry(w,h,d),material);m.position.set(x,y,z);m.castShadow=!isMobileDevice;m.receiveShadow=true;parent.add(m);return m;
}
function cyl(parent,rt,rb,h,x,y,z,material,segments=18){
  const m=new THREE.Mesh(new THREE.CylinderGeometry(rt,rb,h,segments),material);m.position.set(x,y,z);m.castShadow=!isMobileDevice;parent.add(m);return m;
}
function roofGlow(parent,w,d,y,color){
  const r=box(parent,w,.10,d,0,y,0,new THREE.MeshStandardMaterial({color,emissive:color,emissiveIntensity:1.2,metalness:.4,roughness:.3}));return r;
}
function windowGrid(parent,w,h,d,accent,rows=8,cols=5){
  const maxRows=Math.max(2,Math.floor(rows*DETAIL)),maxCols=Math.max(2,Math.floor(cols*DETAIL));
  const sideX=w/2+.011,sideZ=d/2+.011;
  const windowMat=new THREE.MeshBasicMaterial({color:accent,transparent:true,opacity:.74});
  for(let r=0;r<maxRows;r++){
    const yy=.45+(r/(maxRows-1||1))*Math.max(.4,h-.9);
    for(let c=0;c<maxCols;c++){
      if((r+c)%5===0)continue;
      const xx=-w*.38+(c/(maxCols-1||1))*w*.76;
      const wz=new THREE.Mesh(new THREE.PlaneGeometry(Math.max(.08,w/(maxCols*3.2)),.12),windowMat);wz.position.set(xx,yy,sideZ);parent.add(wz);
      if(!isMobileDevice){
        const wx=new THREE.Mesh(new THREE.PlaneGeometry(Math.max(.08,d/(maxCols*3.2)),.12),windowMat);wx.rotation.y=Math.PI/2;wx.position.set(sideX,yy,xx*(d/w));parent.add(wx);
      }
    }
  }
}
function neonSign(parent,textColor,y,w=1.9){
  const backing=box(parent,w,.45,.05,0,y,1.25,mat(0x031018,.35,.4));
  const glow=new THREE.Mesh(new THREE.PlaneGeometry(w*.78,.08),new THREE.MeshBasicMaterial({color:textColor,transparent:true,opacity:.92}));
  glow.position.set(0,y,1.282);parent.add(glow);return backing;
}
function antenna(parent,y,color){
  cyl(parent,.025,.035,1.7,0,y+.85,0,new THREE.MeshBasicMaterial({color}),8);
  const b=new THREE.Mesh(new THREE.SphereGeometry(.09,10,10),new THREE.MeshBasicMaterial({color}));b.position.set(0,y+1.75,0);parent.add(b);return b;
}

function createStandardTower(node,opts={}){
  const g=new THREE.Group(),state=stateColor(node.state),h=Number(opts.height||node.height||5),w=Number(opts.w||2.5),d=Number(opts.d||2.5);
  const facade=mat(opts.color||0x102a38,.32,.68,state,.12);
  box(g,w,h,d,0,h/2,0,facade);
  if(opts.setback){
    box(g,w*.72,h*.32,d*.72,0,h+h*.16,0,mat(opts.color2||0x0c2431,.28,.75,state,.16));
    roofGlow(g,w*.76,d*.76,h+h*.32+.06,state);
    antenna(g,h+h*.32,state);
  }else{roofGlow(g,w*1.03,d*1.03,h+.06,state);antenna(g,h,state);}
  windowGrid(g,w,h,d,opts.window||0x6dd9ff,opts.rows||10,opts.cols||6);
  neonSign(g,state,Math.min(h-.45,h*.72),Math.min(2.0,w*.78));
  return g;
}
function createCouncil(node){
  const g=new THREE.Group(),state=stateColor(node.state),h=9.3;
  box(g,3.8,1.0,3.8,0,.5,0,mat(0x17242b,.75,.28));
  box(g,3.0,5.7,3.0,0,3.85,0,mat(0x0a2635,.24,.82,state,.12));
  box(g,2.35,2.4,2.35,0,7.9,0,mat(0x0b3143,.22,.84,state,.18));
  box(g,1.55,1.3,1.55,0,9.75,0,mat(0x0d3d50,.2,.87,state,.22));
  roofGlow(g,1.7,1.7,10.44,state);antenna(g,10.43,state);
  windowGrid(g,3.0,5.7,3.0,0x7ce9ff,13,7);windowGrid(g,2.35,2.4,2.35,0x97f7ff,5,5);
  for(let i=-1;i<=1;i++){cyl(g,.09,.09,1.0,i*.72,.5,2.0,mat(0xb9c7cb,.45,.1),10);}
  neonSign(g,state,6.1,2.2);return g;
}
function createRisk(node){
  const g=new THREE.Group(),state=stateColor(node.state);
  box(g,4.0,.55,3.6,0,.275,0,mat(0x242018,.82,.15));
  box(g,3.15,3.35,2.95,0,2.05,0,mat(0x302619,.45,.45,state,.08));
  box(g,2.35,1.0,2.25,0,4.05,0,mat(0x3b2c18,.35,.55,state,.12));
  const ring=new THREE.Mesh(new THREE.TorusGeometry(1.05,.09,8,28),new THREE.MeshStandardMaterial({color:state,emissive:state,emissiveIntensity:.75}));ring.rotation.x=Math.PI/2;ring.position.y=4.62;g.add(ring);
  windowGrid(g,3.15,3.35,2.95,0xffd66d,6,5);return g;
}
function createExchange(node,crypto=false){
  const g=new THREE.Group(),state=stateColor(node.state),accent=crypto?0xa86dff:0x59cfff;
  box(g,4.1,.55,3.2,0,.275,0,mat(0x10191f,.8,.22));
  box(g,3.4,4.35,2.65,0,2.45,0,mat(crypto?0x221535:0x0c2838,.28,.72,accent,.18));
  box(g,3.8,.5,2.95,0,4.86,0,new THREE.MeshStandardMaterial({color:accent,emissive:accent,emissiveIntensity:.55,metalness:.6,roughness:.28}));
  windowGrid(g,3.4,4.35,2.65,crypto?0xc099ff:0x7fe8ff,8,7);
  for(let i=-2;i<=2;i++){const ticker=new THREE.Mesh(new THREE.PlaneGeometry(.42,.08),new THREE.MeshBasicMaterial({color:i%2?0x4df49b:0xffd166}));ticker.position.set(i*.55,3.15,1.34);g.add(ticker);}
  return g;
}
function createDataCenter(node){
  const g=new THREE.Group(),state=stateColor(node.state);
  box(g,4.4,.45,3.6,0,.225,0,mat(0x111a20,.9,.12));
  for(let i=-1;i<=1;i++){box(g,1.05,2.4,2.5,i*1.3,1.65,0,mat(0x0c202b,.35,.6,state,.09));windowGrid(g,1.05,2.4,2.5,0x64ddff,6,3);}
  const dish=new THREE.Mesh(new THREE.SphereGeometry(.52,16,10,0,Math.PI*2,0,Math.PI/2),mat(0x718693,.28,.8));dish.rotation.x=Math.PI;dish.position.set(0,3.18,0);g.add(dish);
  return g;
}
function createArena(node){
  const g=new THREE.Group(),state=stateColor(node.state);
  cyl(g,2.35,2.65,.55,0,.275,0,mat(0x231d12,.75,.25),28);
  const dome=new THREE.Mesh(new THREE.SphereGeometry(2.15,28,16,0,Math.PI*2,0,Math.PI/2),new THREE.MeshStandardMaterial({color:0x1b252b,transparent:true,opacity:.8,metalness:.65,roughness:.18,emissive:state,emissiveIntensity:.09}));dome.position.y=.55;g.add(dome);
  const ring=new THREE.Mesh(new THREE.TorusGeometry(2.18,.07,8,48),new THREE.MeshBasicMaterial({color:state}));ring.rotation.x=Math.PI/2;ring.position.y=.58;g.add(ring);
  return g;
}
function createResidential(node){
  const g=new THREE.Group(),state=stateColor(node.state);
  const towers=[[-1.25,0,3.2], [1.15,.35,4.4],[0,-1.05,2.7]];
  towers.forEach((t,i)=>{const h=t[2];const b=box(g,1.45,h,1.45,t[0],h/2,t[1],mat(i===1?0x263642:0x202f38,.55,.2,state,.05));windowGrid(b,1.45,h,1.45,0xffd98e,Math.floor(h*2),4);});
  return g;
}
function createPark(node){
  const g=new THREE.Group();
  const grass=box(g,4.8,.08,3.8,0,.04,0,mat(0x0f3c2b,.96,.02));
  for(let i=0;i<(isMobileDevice?8:16);i++){
    const a=(i/(isMobileDevice?8:16))*Math.PI*2,r=1.1+(i%3)*.42;
    const x=Math.cos(a)*r,z=Math.sin(a)*r*.72;
    cyl(g,.035,.055,.38,x,.19,z,mat(0x4b321c,.9,.02),6);
    const crown=new THREE.Mesh(new THREE.ConeGeometry(.23,.58,7),mat(0x1d6b45,.9,.02));crown.position.set(x,.69,z);g.add(crown);
  }
  const pond=new THREE.Mesh(new THREE.CircleGeometry(.7,24),new THREE.MeshStandardMaterial({color:0x174b61,metalness:.25,roughness:.2,transparent:true,opacity:.8}));pond.rotation.x=-Math.PI/2;pond.position.y=.09;g.add(pond);
  return g;
}
function createCommunity(node){
  const g=new THREE.Group(),state=stateColor(node.state);
  box(g,4.3,.08,3.7,0,.04,0,mat(0x41321f,.9,.04));
  box(g,2.9,1.9,1.8,-.45,.98,-.35,mat(0x38291e,.5,.18,state,.05));
  box(g,1.1,.14,.65,1.1,.55,1.1,mat(0x8d5a2d,.7,.08));
  for(let i=-1;i<=1;i++){cyl(g,.045,.045,.48,i*.55,.3,1.15,mat(0x6a4526,.8,.05),8);}
  windowGrid(g,2.9,1.9,1.8,0xffd182,4,5);return g;
}
function createWellness(node){
  const g=new THREE.Group(),state=stateColor(node.state);
  box(g,4.0,.12,3.2,0,.06,0,mat(0x163b33,.9,.04));
  box(g,2.9,1.65,2.2,0,.9,0,mat(0x183f38,.45,.18,state,.05));
  const roof=new THREE.Mesh(new THREE.CylinderGeometry(1.55,1.55,.18,24),mat(0x1b5849,.6,.15));roof.position.y=1.82;g.add(roof);
  windowGrid(g,2.9,1.65,2.2,0x88ffce,3,5);return g;
}
function createLab(node,aeve=false){
  const g=new THREE.Group(),state=stateColor(node.state),accent=aeve?0xa86dff:0x59cfff;
  box(g,4.0,.16,3.3,0,.08,0,mat(0x151c24,.9,.05));
  box(g,1.65,3.1,2.5,-.9,1.63,0,mat(aeve?0x251a39:0x102c3b,.3,.6,accent,.12));
  box(g,1.65,2.35,2.5,.9,1.25,0,mat(aeve?0x1c1530:0x0e2532,.32,.58,accent,.09));
  const bridge=box(g,1.0,.42,1.2,0,1.55,0,mat(0x243540,.25,.65,accent,.15));
  windowGrid(g,1.65,3.1,2.5,aeve?0xcaa7ff:0x83ebff,7,4);return g;
}

function buildDistrict(node){
  let g;
  switch(node.id){
    case "council":g=createCouncil(node);break;
    case "risk":g=createRisk(node);break;
    case "stock":g=createExchange(node,false);break;
    case "crypto":g=createExchange(node,true);break;
    case "data":g=createDataCenter(node);break;
    case "arena":g=createArena(node);break;
    case "residential":g=createResidential(node);break;
    case "recreation":g=createPark(node);break;
    case "community":g=createCommunity(node);break;
    case "wellness":g=createWellness(node);break;
    case "patterns":g=createLab(node,false);break;
    case "aeve":g=createLab(node,true);break;
    case "academy":g=createStandardTower(node,{height:4.3,w:3.4,d:2.7,color:0x20324a,window:0x89c9ff});break;
    case "intel":g=createStandardTower(node,{height:6.8,w:2.6,d:2.6,color:0x143246,window:0x76dcff,setback:true});break;
    case "execution":g=createStandardTower(node,{height:6.8,w:2.9,d:2.7,color:0x11334a,window:0x65ddff,setback:true});break;
    case "portfolio":g=createStandardTower(node,{height:7.5,w:3.3,d:3.0,color:0x183c32,window:0x77f0ba,setback:true});break;
    default:g=createStandardTower(node,{height:Number(node.height||4.5),w:2.7,d:2.7,color:0x102a38,window:0x6dd9ff,setback:true});
  }
  g.position.set(Number(node.x||0),0,Number(node.z||0));
  g.userData={type:"node",data:node};
  g.traverse(obj=>{if(obj.isMesh){obj.userData={type:"node",data:node};interactables.push(obj);}});
  addLabel(g,node.title,node.metric,Math.max(3.1,Number(node.height||4)+2.0));
  nodeObjects.set(node.id,g);scene.add(g);return g;
}
(DATA.nodes||[]).forEach(buildDistrict);

function createAmbientBuilding(x,z,w,d,h,accent){
  const g=new THREE.Group();
  box(g,w,h,d,0,h/2,0,mat(0x101c24,.42,.38,accent,.025));
  if(!isMobileDevice)windowGrid(g,w,h,d,accent,Math.max(3,Math.floor(h*1.4)),Math.max(3,Math.floor(w*2.1)));
  if(h>5.5)roofGlow(g,w*1.02,d*1.02,h+.05,accent);
  g.position.set(x,0,z);scene.add(g);return g;
}
const skylineCount=isMobileDevice?26:68;
for(let i=0;i<skylineCount;i++){
  let x,z;
  if(i<skylineCount*.55){x=-29+Math.random()*62;z=(Math.random()<.5?-1:1)*(13+Math.random()*13);}
  else{x=(Math.random()<.5?-1:1)*(23+Math.random()*9);z=-12+Math.random()*24;}
  const h=1.7+Math.pow(Math.random(),.52)*8.7,w=.9+Math.random()*1.8,d=.9+Math.random()*1.8;
  const accent=[0x337da0,0x426f83,0x446d65,0x5f6655][i%4];
  createAmbientBuilding(x,z,w,d,h,accent);
}

const portfolioBase=nodeObjects.get("portfolio");
portfolio_towers.forEach(function(item,index){
  if(!portfolioBase)return;
  const h=Math.max(.55,Number(item.height||1)*.72);
  const angle=(index/Math.max(1,portfolio_towers.length))*Math.PI*2,r=2.4+(index%2)*.55;
  const mesh=new THREE.Mesh(new THREE.BoxGeometry(.42,h,.42),mat(0x173d54,.34,.55,0x2d9fd0,.2));
  mesh.position.set(portfolioBase.position.x+Math.cos(angle)*r,h/2,portfolioBase.position.z+Math.sin(angle)*r);
  mesh.userData={type:"position",data:item};mesh.castShadow=!isMobileDevice;scene.add(mesh);interactables.push(mesh);
});

const arena=nodeObjects.get("arena");
(DATA.strategy_arena||[]).slice(0,isMobileDevice?6:10).forEach(function(item,index){
  if(!arena)return;const n=Math.min(10,(DATA.strategy_arena||[]).length),angle=(index/Math.max(1,n))*Math.PI*2;
  const h=.28+Math.min(1.65,Number(item.samples||0)/110);
  const negative=String(item.evidence_state||"").includes("NEGATIVE"),promising=String(item.evidence_state||"").includes("PROMISING");
  const accent=negative?0xff6767:(promising?0x4df49b:0xffd166);
  const mesh=new THREE.Mesh(new THREE.CylinderGeometry(.14,.18,h,10),mat(0x172631,.3,.5,accent,.55));
  mesh.position.set(arena.position.x+Math.cos(angle)*2.55,h/2,arena.position.z+Math.sin(angle)*2.55);
  mesh.userData={type:"cohort",data:item};scene.add(mesh);interactables.push(mesh);
});

function makeWorker(worker,index){
  const home=nodeObjects.get(worker.home),dest=nodeObjects.get(worker.destination);if(!home||!dest)return;
  const g=new THREE.Group(),state=String(worker.state||"");
  const tint=state==="RISK_REVIEW"?0xffd166:(state==="LEARNING"||state==="TRAINING"?0xa86dff:(state==="RESTING"||state==="RECREATION"?0x59cfff:0xf5f7ff));
  const body=cyl(g,.085,.115,.30,0,.25,0,mat(0x223743,.5,.12,tint,.22),8);
  const head=new THREE.Mesh(new THREE.SphereGeometry(.083,9,9),mat(tint,.5,.04));head.position.y=.48;g.add(head);
  const leg1=cyl(g,.025,.025,.22,-.045,.08,0,mat(0x101b22,.7,.08),6),leg2=cyl(g,.025,.025,.22,.045,.08,0,mat(0x101b22,.7,.08),6);
  const homePos=home.position.clone(),workPos=dest.position.clone(),off=((index%5)-2)*.18;
  homePos.x+=off;homePos.z+=((index%3)-1)*.2;workPos.x+=off;workPos.z+=((index%3)-1)*.2;
  g.position.copy(homePos);
  g.userData={type:"resident",data:worker,home:homePos,work:workPos,phase:index*.63,speed:.13+(index%4)*.02,leg1,leg2};
  [body,head,leg1,leg2].forEach(m=>m.userData=g.userData);
  scene.add(g);workerObjects.push(g);interactables.push(body,head);return g;
}
(DATA.resident_agents||[]).forEach(makeWorker);

function makeVehicle(index){
  const g=new THREE.Group(),accent=index%3===0?0x59cfff:(index%3===1?0xffd166:0xa86dff);
  box(g,.56,.18,.28,0,.16,0,mat(0x172a35,.3,.55,accent,.12));
  box(g,.30,.13,.24,-.02,.31,0,mat(0x24475a,.2,.35));
  const l=new THREE.Mesh(new THREE.SphereGeometry(.035,7,7),new THREE.MeshBasicMaterial({color:accent}));l.position.set(.29,.16,.11);g.add(l);
  const path=index%2===0?"east":"west";
  g.position.set(path==="east"?-31:31,.03,index%4<2?.55:-.55);
  g.userData={path,speed:.035+(index%4)*.008,offset:index*6.4};
  scene.add(g);vehicleObjects.push(g);return g;
}
for(let i=0;i<(isMobileDevice?6:16);i++)makeVehicle(i);

const plaza=nodeObjects.get("community");
if(plaza){
  for(let i=0;i<(isMobileDevice?3:8);i++){
    const a=(i/8)*Math.PI*2,r=2.3;
    const lamp=cyl(scene,.025,.035,.8,plaza.position.x+Math.cos(a)*r,.4,plaza.position.z+Math.sin(a)*r,mat(0x5a6467,.35,.65),7);
    const bulb=new THREE.PointLight(0xffd18a,isMobileDevice?2:5,4,2);bulb.position.set(plaza.position.x+Math.cos(a)*r,.88,plaza.position.z+Math.sin(a)*r);scene.add(bulb);
  }
}


function curveFor(source,target){
  const a=nodeObjects.get(source)?.position.clone(),b=nodeObjects.get(target)?.position.clone();if(!a||!b)return null;
  a.y=.55;b.y=.55;const mid=a.clone().lerp(b,.5);mid.y=2.0+Math.min(3.6,a.distanceTo(b)*.1);return new THREE.QuadraticBezierCurve3(a,mid,b);
}
(DATA.flows||[]).forEach(function(flow,index){
  const curve=curveFor(flow.source,flow.target);if(!curve)return;
  const line=new THREE.Line(new THREE.BufferGeometry().setFromPoints(curve.getPoints(30)),new THREE.LineBasicMaterial({color:0x2f9fcc,transparent:true,opacity:.16}));
  scene.add(line);
  const particle=new THREE.Mesh(new THREE.SphereGeometry(.065,8,8),new THREE.MeshBasicMaterial({color:0x69dcff}));
  particle.userData={phase:index/Math.max(1,(DATA.flows||[]).length),curve};scene.add(particle);flowObjects.push({line,particle,curve});
});

const cityObjects=scene.children.slice();
const brainGroup=new THREE.Group();brainGroup.visible=false;scene.add(brainGroup);
const brainData=DATA.decision_graph||{nodes:[],edges:[],summary:{}},brainObjects=new Map(),brainEdges=[];
function brainColor(node){if(node.state==="offline")return 0xff6767;if(node.state==="waiting")return 0xffd166;return ({feature:0x59cfff,decision:0xa86dff,gate:0x4df49b,outcome:0x55b8ff})[node.kind]||0x9ccfe6;}
(brainData.nodes||[]).forEach(function(node){
  const radius=Math.max(.11,Number(node.size||.28)),color=brainColor(node);
  const mesh=new THREE.Mesh(new THREE.IcosahedronGeometry(radius,1),mat(color,.25,.32,color,node.kind==="decision"?.75:.42));
  mesh.position.set(Number(node.x||0),Number(node.y||0),Number(node.z||0));mesh.userData={type:"brain",data:node,phase:Math.random()*6.28};
  brainGroup.add(mesh);brainObjects.set(node.id,mesh);interactables.push(mesh);
  if(node.label&&!isMobileDevice)addLabel(mesh,node.title,node.metric,radius*2.3);
});
(brainData.edges||[]).forEach(function(edge,index){
  const s=brainObjects.get(edge.source),t=brainObjects.get(edge.target);if(!s||!t)return;
  const a=s.position.clone(),b=t.position.clone(),mid=a.clone().lerp(b,.5);mid.y+=.3+Math.min(1,a.distanceTo(b)*.05);
  const curve=new THREE.QuadraticBezierCurve3(a,mid,b),blocked=edge.kind==="blocked",color=blocked?0xff6767:0x59cfff;
  const line=new THREE.Line(new THREE.BufferGeometry().setFromPoints(curve.getPoints(22)),new THREE.LineBasicMaterial({color,transparent:true,opacity:blocked?.78:.25}));brainGroup.add(line);
  const p=new THREE.Mesh(new THREE.SphereGeometry(.045,7,7),new THREE.MeshBasicMaterial({color}));p.userData={phase:index/Math.max(1,(brainData.edges||[]).length),curve};brainGroup.add(p);brainEdges.push({line,particle:p,curve});
});

let brainMode=false,workersVisible=true,trafficVisible=true;
function showCityObjects(on){cityObjects.forEach(o=>{if(o!==brainGroup&&!o.isLight)o.visible=on;});workerObjects.forEach(o=>o.visible=on&&workersVisible);vehicleObjects.forEach(o=>o.visible=on&&trafficVisible);}
function setBrainMode(on){
  brainMode=on;showCityObjects(!on);brainGroup.visible=on;
  const b=document.getElementById("brain");b.classList.toggle("active",on);b.textContent=on?"CITY":"BRAIN";
  inspector.classList.remove("open");
  hovercard.classList.remove("show");
  if(on){camera.position.set(14,10,19);controls.target.set(0,2,0);}
  else resetView();
  controls.update();
}

function inspect(kind,data){
  let eyebrow="ORACLE CITY",title="",metric="",detail="";
  if(kind==="node"){eyebrow="CITY DISTRICT";title=data.title;metric=data.metric;detail=data.detail;}
  else if(kind==="resident"){eyebrow="ORACLE CITY WORKER · VISUAL ONLY";title=data.title;metric=String(data.state||"IDLE").replaceAll("_"," ");detail=data.detail+" Assigned district: "+String(data.destination||"unknown")+". This worker cannot place or approve trades.";}
  else if(kind==="position"){eyebrow="PORTFOLIO POSITION";title=data.symbol+" · "+String(data.market||"").toUpperCase();metric=money(data.value);detail="Quantity "+String(data.quantity)+". Portfolio mini-tower height reflects known marked exposure only.";}
  else if(kind==="cohort"){eyebrow=data.control?"STRATEGY ARENA · CONTROL":"STRATEGY ARENA · PAPER EVIDENCE";title=String(data.strategy||"unknown").replaceAll("_"," ")+" · "+String(data.regime||"unknown");metric=String(data.evidence_state||"RESEARCH ONLY")+" · "+String(data.samples||0)+" samples";detail=(data.expectancy==null?"Expectancy unavailable":"Expectancy "+Number(data.expectancy).toFixed(6))+" · "+(data.profit_factor==null?"PF unavailable":"PF "+Number(data.profit_factor).toFixed(3))+". Visual evidence does not grant promotion or execution authority.";}
  else if(kind==="brain"){eyebrow="ORACLE BRAIN · "+String(data.kind||"NODE").toUpperCase();title=data.title;metric=data.metric;detail=data.detail;}
  inspector.innerHTML="<div class='eyebrow'>"+esc(eyebrow)+"</div><h3>"+esc(title)+"</h3><div class='metric'>"+esc(metric)+"</div><p>"+esc(detail)+"</p>";
  inspector.classList.add("open");
}

const Raycaster=THREE.Raycaster;
const raycaster=new Raycaster(),pointer=new THREE.Vector2();
function pointerFrom(e){const r=renderer.domElement.getBoundingClientRect();pointer.x=((e.clientX-r.left)/r.width)*2-1;pointer.y=-((e.clientY-r.top)/r.height)*2+1;}
function hit(){const hits=raycaster.intersectObjects(interactables,false);return hits.find(h=>brainMode?h.object.userData.type==="brain":h.object.userData.type!=="brain");}
let hoveredObject=null,hoveredIntensity=0;
function clearHover(){
  if(hoveredObject&&hoveredObject.material&&"emissiveIntensity" in hoveredObject.material){hoveredObject.material.emissiveIntensity=hoveredIntensity;}
  hoveredObject=null;
  hovercard.classList.remove("show");
}
function hoverText(kind,data){
  if(kind==="node")return [data.title,data.metric];
  if(kind==="resident")return [data.title,String(data.state||"IDLE").replaceAll("_"," ")];
  if(kind==="position")return [data.symbol+" · "+String(data.market||"").toUpperCase(),money(data.value)];
  if(kind==="cohort")return [String(data.strategy||"strategy").replaceAll("_"," "),String(data.evidence_state||"RESEARCH ONLY")];
  if(kind==="brain")return [data.title,data.metric];
  return ["Oracle City","Select for details"];
}
renderer.domElement.addEventListener("pointermove",e=>{
  pointerFrom(e);raycaster.setFromCamera(pointer,camera);const h=hit();renderer.domElement.style.cursor=h?"pointer":"grab";
  if(!h){clearHover();return;}
  if(hoveredObject!==h.object){
    clearHover();hoveredObject=h.object;
    if(hoveredObject.material&&"emissiveIntensity" in hoveredObject.material){
      hoveredIntensity=Number(hoveredObject.material.emissiveIntensity||0);
      hoveredObject.material.emissiveIntensity=Math.max(.7,hoveredIntensity+.45);
    }
  }
  const text=hoverText(h.object.userData.type,h.object.userData.data);
  hovercard.innerHTML="<b>"+esc(text[0])+"</b><span>"+esc(text[1])+"</span>";
  const r=app.getBoundingClientRect();
  const x=Math.min(app.clientWidth-245,Math.max(8,e.clientX-r.left+14));
  const y=Math.min(app.clientHeight-80,Math.max(8,e.clientY-r.top+14));
  hovercard.style.left=x+"px";hovercard.style.top=y+"px";hovercard.classList.add("show");
});
renderer.domElement.addEventListener("pointerleave",clearHover);
renderer.domElement.addEventListener("click",e=>{
  pointerFrom(e);raycaster.setFromCamera(pointer,camera);const h=hit();
  if(h)inspect(h.object.userData.type,h.object.userData.data);
  else inspector.classList.remove("open");
});

function mobileView(){return window.matchMedia&&window.matchMedia("(max-width:720px)").matches;}
function resetView(){
  if(mobileView()){camera.position.set(2.5,31,28);controls.target.set(2.5,2.2,0);}
  else{camera.position.set(30,22,38);controls.target.set(2.5,2.7,0);}
  controls.update();
  inspector.classList.remove("open");
  hovercard.classList.remove("show");
}
document.getElementById("reset").onclick=()=>{if(brainMode)setBrainMode(false);else resetView();};
document.getElementById("street").onclick=()=>{if(brainMode)setBrainMode(false);camera.position.set(-2,3.0,18);controls.target.set(3,2.4,0);controls.update();};
document.getElementById("topview").onclick=()=>{if(brainMode)setBrainMode(false);camera.position.set(2.5,48,.01);controls.target.set(2.5,0,0);controls.update();};
document.getElementById("autorotate").onclick=function(){controls.autoRotate=!controls.autoRotate;controls.autoRotateSpeed=.48;this.classList.toggle("active",controls.autoRotate);};
document.getElementById("brain").onclick=()=>setBrainMode(!brainMode);
document.getElementById("workers").onclick=function(){workersVisible=!workersVisible;workerObjects.forEach(w=>w.visible=!brainMode&&workersVisible);this.textContent=workersVisible?"WORKERS":"WORKERS OFF";this.classList.toggle("active",workersVisible);};
document.getElementById("traffic").onclick=function(){trafficVisible=!trafficVisible;vehicleObjects.forEach(v=>v.visible=!brainMode&&trafficVisible);this.textContent=trafficVisible?"TRAFFIC":"TRAFFIC OFF";this.classList.toggle("active",trafficVisible);};
document.getElementById("replayToggle").onclick=function(){replayPanel.classList.toggle("open");this.classList.toggle("active",replayPanel.classList.contains("open"));};

const replay=DATA.replay||[],timeline=document.getElementById("timeline"),replayTitle=document.getElementById("replayTitle"),replayDetail=document.getElementById("replayDetail"),play=document.getElementById("play");
timeline.max=String(Math.max(0,replay.length-1));timeline.value=String(Math.max(0,replay.length-1));let replayIndex=Number(timeline.value||0),replayTimer=null;
function showReplay(index){if(!replay.length){replayTitle.textContent="No replay events available";replayDetail.textContent="Oracle City will populate this timeline from persisted decisions, trades, and intelligence.";return;}replayIndex=Math.max(0,Math.min(replay.length-1,index));timeline.value=String(replayIndex);const item=replay[replayIndex];replayTitle.textContent=String(item.kind||"event").toUpperCase()+" · "+item.title;replayDetail.textContent=(item.time?item.time+" · ":"")+item.detail;}
timeline.oninput=function(){showReplay(Number(this.value));};
play.onclick=function(){if(replayTimer){clearInterval(replayTimer);replayTimer=null;play.textContent="PLAY";play.classList.remove("active");return;}play.textContent="PAUSE";play.classList.add("active");replayTimer=setInterval(()=>{replayIndex=(replayIndex+1)%Math.max(1,replay.length);showReplay(replayIndex);},1600);};showReplay(replayIndex);

let lastMobileView=null;
function resize(){const w=app.clientWidth,h=app.clientHeight;camera.aspect=w/h;camera.updateProjectionMatrix();renderer.setSize(w,h,false);labelRenderer.setSize(w,h);const mobile=mobileView();if(lastMobileView===null||lastMobileView!==mobile){lastMobileView=mobile;if(!brainMode)resetView();}}
new ResizeObserver(resize).observe(app);resize();

let elapsed=0;
function animate(){
  requestAnimationFrame(animate);elapsed+=prefersReduced?.004:.012;controls.update();
  flowObjects.forEach(item=>{const phase=(elapsed*.12+item.particle.userData.phase)%1;item.particle.position.copy(item.curve.getPointAt(phase));});
  brainEdges.forEach(item=>{const phase=(elapsed*.18+item.particle.userData.phase)%1;item.particle.position.copy(item.curve.getPointAt(phase));});
  brainObjects.forEach(o=>{if(brainMode){const p=1+Math.sin(elapsed*2.4+o.userData.phase)*.055;o.scale.setScalar(p);o.rotation.y+=.004;}});
  workerObjects.forEach(w=>{
    const state=String(w.userData.data.state||""),cycle=(Math.sin(elapsed*w.userData.speed+w.userData.phase)+1)/2;
    const t=(state==="RESTING"||state==="HOME")?0.16:(state==="RECREATION"?0.72:cycle);
    w.position.lerpVectors(w.userData.home,w.userData.work,t);w.position.y=.02+Math.abs(Math.sin(elapsed*5+w.userData.phase))*.024;
    const dir=w.userData.work.clone().sub(w.userData.home);if(dir.lengthSq()>.001)w.rotation.y=Math.atan2(dir.x,dir.z);
    const step=Math.sin(elapsed*8+w.userData.phase)*.4;w.userData.leg1.rotation.x=step;w.userData.leg2.rotation.x=-step;
  });
  vehicleObjects.forEach((v,i)=>{if(v.userData.path==="east"){v.position.x=-31+((elapsed*v.userData.speed*45+v.userData.offset)%62);v.rotation.y=Math.PI/2;}else{v.position.x=31-((elapsed*v.userData.speed*45+v.userData.offset)%62);v.rotation.y=-Math.PI/2;}});
  renderer.render(scene,camera);labelRenderer.render(scene,camera);
}
status.style.display="none";resetView();animate();
</script>
</body>
</html>"""
    return document.replace("__ORACLE_DATA__", encoded)


__all__ = ["render_oracle_city_component"]
