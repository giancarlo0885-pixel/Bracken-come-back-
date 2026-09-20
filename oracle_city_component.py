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
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{box-sizing:border-box}
html,body{margin:0;height:100%;overflow:hidden;background:#02070c;color:#eef8ff;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
#app{position:relative;height:100%;min-height:720px;background:radial-gradient(circle at 50% 25%,#0b2635 0,#051019 42%,#02070c 78%);border:1px solid #17394d;border-radius:22px;overflow:hidden}
#stage{position:absolute;inset:0}
#labels{position:absolute;inset:0;pointer-events:none}
.label{padding:5px 7px;border:1px solid rgba(91,176,214,.42);border-radius:8px;background:rgba(3,11,17,.82);backdrop-filter:blur(8px);font-size:14px;font-weight:800;color:#f5fbff;white-space:nowrap;box-shadow:0 7px 20px rgba(0,0,0,.25)}
.label small{display:block;margin-top:1px;color:#b6cfdb;font-size:11px;font-weight:650}
.topbar{position:absolute;z-index:5;left:14px;right:14px;top:14px;display:flex;gap:10px;align-items:flex-start;justify-content:space-between;pointer-events:none}
.brand,.toolbar,.inspector,.replay{pointer-events:auto;border:1px solid rgba(74,143,176,.38);background:rgba(2,10,16,.82);backdrop-filter:blur(14px);box-shadow:0 15px 48px rgba(0,0,0,.28)}
.brand{border-radius:15px;padding:14px 16px;max-width:520px}
.brand b{font-size:16px;letter-spacing:.12em;text-transform:uppercase}
.brand span{display:block;margin-top:3px;color:#c0d3dd;font-size:12px;line-height:1.45}.chips{display:flex;flex-wrap:wrap;gap:5px;margin-top:8px}.chip{display:inline-flex;align-items:center;min-height:24px;padding:3px 7px;border:1px solid #28516a;border-radius:999px;background:rgba(7,23,34,.82);font-size:10px;font-weight:800;color:#dff5ff}
.toolbar{display:flex;gap:6px;border-radius:13px;padding:6px}
button{appearance:none;border:1px solid #28516a;border-radius:9px;background:#071722;color:#e9f7ff;padding:9px 11px;min-height:38px;font-size:12px;font-weight:800;cursor:pointer}
button:hover{border-color:#5cbbe5;background:#0c2432}
button.active{border-color:#4bf49b;color:#4bf49b}
.inspector{position:absolute;z-index:5;left:14px;bottom:14px;width:min(360px,calc(100% - 28px));border-radius:16px;padding:13px}
.inspector .eyebrow{font-size:11px;font-weight:900;letter-spacing:.1em;text-transform:uppercase;color:#66d1ff}
.inspector h3{margin:4px 0 3px;font-size:20px}
.inspector .metric{font-size:15px;font-weight:850;margin:5px 0 7px}
.inspector p{margin:0;color:#c3d4dc;font-size:13px;line-height:1.55}
.replay{position:absolute;z-index:5;right:14px;bottom:14px;width:min(440px,calc(100% - 28px));border-radius:16px;padding:10px 12px}
.replay-head{display:flex;gap:8px;align-items:center}
.replay-title{min-width:0;flex:1}
.replay-title b{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:13px}
.replay-title span{display:block;color:#b4cad5;font-size:11px;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
input[type=range]{width:100%;accent-color:#55d4ff}
.legend{position:absolute;z-index:5;right:14px;top:66px;border:1px solid rgba(74,143,176,.28);border-radius:12px;padding:8px 10px;background:rgba(2,10,16,.72);font-size:11px;color:#b9ced8;pointer-events:none}
.legend div+div{margin-top:4px}.dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px}
#fallback{position:absolute;z-index:2;inset:88px 18px 150px;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;align-content:start;pointer-events:none}
.fallback-card{border:1px solid #17394d;border-radius:13px;padding:10px;background:rgba(5,18,27,.72)}
.fallback-card b{font-size:11px}.fallback-card span{display:block;color:#8ba8b8;font-size:9px;margin-top:3px}
#status{position:absolute;z-index:6;left:50%;top:50%;transform:translate(-50%,-50%);padding:8px 10px;border-radius:9px;background:rgba(2,8,12,.88);border:1px solid #244d65;color:#9ec2d4;font-size:10px}
@media(max-width:720px){
  #app{min-height:820px}
  .topbar{left:8px;right:8px;top:8px;gap:6px}
  .brand{max-width:190px;padding:8px 9px}.brand b{font-size:10px}.brand span{display:none}.chips{margin-top:5px;gap:3px}.chip{font-size:8px;min-height:20px;padding:2px 5px}
  .toolbar{display:grid;grid-template-columns:1fr 1fr;gap:4px;padding:4px}
  button{padding:7px 8px;min-height:44px;font-size:10px}
  .legend{display:none}
  .label{display:none}
  .replay{left:8px;right:8px;width:auto;bottom:8px;padding:8px 9px}
  .inspector{left:8px;right:8px;width:auto;bottom:96px;padding:10px;max-height:150px;overflow:auto}
  .inspector h3{font-size:14px}.inspector .metric{font-size:11px}.inspector p{font-size:10px}
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
  <div id="labels"></div>
  <div id="fallback"></div>
  <div id="status">Loading Oracle City WebGL...</div>
  <div class="topbar">
    <div class="brand"><b>GARIBALDI MARKET ORACLE · LIVING CITY</b><span>Workers move through research, Council, risk, paper execution, learning, homes, and recovery spaces. Motion is visualization-only.</span><div class="chips"><span class="chip" id="cityMood">CITY MOOD: --</span><span class="chip" id="aeveProgress">AEVE: -- / 1000</span><span class="chip">PAPER ONLY</span></div></div>
    <div class="toolbar">
      <button id="reset">RESET VIEW</button>
      <button id="flows" class="active">FLOWS ON</button>
      <button id="autorotate">AUTO ROTATE</button>
      <button id="topview">TOP VIEW</button>
      <button id="brain">BRAIN MAP</button>
      <button id="workers" class="active">WORKERS ON</button>
    </div>
  </div>
  <div class="legend">
    <div><i class="dot" style="background:#4df49b"></i>healthy / live state</div>
    <div><i class="dot" style="background:#ffd166"></i>waiting / partial state</div>
    <div><i class="dot" style="background:#ff6767"></i>offline / error state</div>
    <div><i class="dot" style="background:#59cfff"></i>data flow / evidence</div>
    <div><i class="dot" style="background:#a86dff"></i>decision node in Brain Map</div>
    <div><i class="dot" style="background:#f5f7ff"></i>living-city worker / research agent</div>
  </div>
  <div class="inspector" id="inspector">
    <div class="eyebrow">ORACLE CITY</div>
    <h3>Living Oracle City</h3>
    <div class="metric">Work with discipline. Learn from results. Progress earns rewards.</div>
    <p>Workers travel to districts based on persisted Oracle state. Research hard. Protect capital. Let evidence earn conviction. The city cannot place trades.</p>
  </div>
  <div class="replay">
    <div class="replay-head">
      <button id="play">PLAY</button>
      <div class="replay-title"><b id="replayTitle">Recent decision replay</b><span id="replayDetail">Move the slider to review Oracle decisions and paper-trade events.</span></div>
    </div>
    <input id="timeline" type="range" min="0" max="0" value="0" step="1">
  </div>
</div>
<script type="application/json" id="oracle-data">__ORACLE_DATA__</script>
<script type="module">
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { CSS2DRenderer, CSS2DObject } from "three/addons/renderers/CSS2DRenderer.js";

const DATA = JSON.parse(document.getElementById("oracle-data").textContent);
const app = document.getElementById("app");
const stage = document.getElementById("stage");
const fallback = document.getElementById("fallback");
const status = document.getElementById("status");
const inspector = document.getElementById("inspector");
const colors = {online:0x4df49b,waiting:0xffd166,offline:0xff6767};
const aeveData=DATA.aeve || {};
document.getElementById("cityMood").textContent="CITY MOOD: "+String(DATA.city_mood || "UNKNOWN");
document.getElementById("aeveProgress").textContent="AEVE: "+(aeveData.accepted==null?"UNAVAILABLE":String(aeveData.accepted))+" / "+String(aeveData.target || 1000);

(DATA.nodes || []).forEach(function(node){
  const card=document.createElement("div");
  card.className="fallback-card";
  card.innerHTML="<b>"+esc(node.title)+"</b><span>"+esc(node.metric)+"</span>";
  fallback.appendChild(card);
});

function esc(value){
  return String(value == null ? "" : value).replace(/[&<>"']/g,function(ch){
    return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch];
  });
}
function money(value){
  const n=Number(value || 0);
  return "$"+n.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});
}
function nodeColor(state){ return colors[state] || colors.waiting; }
function districtColor(id){
  const palette={
    data:0x12344a,intel:0x15334b,patterns:0x2a2048,council:0x183b35,risk:0x432d19,
    execution:0x17364a,portfolio:0x193d31,stock:0x173b4d,crypto:0x30214a,
    academy:0x24354d,aeve:0x33235a,arena:0x49351b,residential:0x263845,
    wellness:0x1f463c,community:0x48351f,recreation:0x1d4531
  };
  return palette[id] || 0x0b2635;
}

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x02070c);
scene.fog = new THREE.FogExp2(0x02070c,0.022);

const camera = new THREE.PerspectiveCamera(48,1,0.1,220);
camera.position.set(17,18,27);

const renderer = new THREE.WebGLRenderer({antialias:true,powerPreference:"high-performance"});
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1,2));
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
stage.appendChild(renderer.domElement);

const labelRenderer = new CSS2DRenderer();
labelRenderer.domElement.style.position="absolute";
labelRenderer.domElement.style.inset="0";
labelRenderer.domElement.style.pointerEvents="none";
stage.appendChild(labelRenderer.domElement);

const controls = new OrbitControls(camera,renderer.domElement);
controls.target.set(2.5,1.8,0);
controls.enableDamping=true;
controls.dampingFactor=.065;
controls.minDistance=10;
controls.maxDistance=62;
controls.maxPolarAngle=Math.PI*.49;

scene.add(new THREE.HemisphereLight(0x7edbff,0x03101a,2.2));
const keyLight=new THREE.DirectionalLight(0xd9f5ff,2.4);
keyLight.position.set(-8,18,10);keyLight.castShadow=true;scene.add(keyLight);
const rim=new THREE.PointLight(0x2f9cff,60,50,2);
rim.position.set(4,10,-8);scene.add(rim);

const ground=new THREE.Mesh(
  new THREE.PlaneGeometry(65,44),
  new THREE.MeshStandardMaterial({color:0x041019,roughness:.95,metalness:.1})
);
ground.rotation.x=-Math.PI/2;
ground.position.y=-.04;
ground.receiveShadow=true;
scene.add(ground);

const grid=new THREE.GridHelper(60,30,0x1d5c79,0x103142);
grid.position.y=.01;
grid.material.opacity=.38;
grid.material.transparent=true;
scene.add(grid);

function addRoad(x,z,w,d){
  const road=new THREE.Mesh(
    new THREE.PlaneGeometry(w,d),
    new THREE.MeshStandardMaterial({color:0x07131b,roughness:1,metalness:0})
  );
  road.rotation.x=-Math.PI/2;
  road.position.set(x,.018,z);
  road.receiveShadow=true;
  scene.add(road);
}
addRoad(2.5,0,38,1.25);
addRoad(4,0,1.25,21);
addRoad(10,0,1.0,19);
addRoad(-5,2,1.0,17);

const nodeObjects=new Map();
const interactables=[];
const flowObjects=[];

function addLabel(object,title,metric){
  const div=document.createElement("div");
  div.className="label";
  div.innerHTML=esc(title)+"<small>"+esc(metric)+"</small>";
  const label=new CSS2DObject(div);
  label.position.set(0,object.userData.labelY || 4.8,0);
  object.add(label);
}

function addBuilding(node){
  const group=new THREE.Group();
  group.position.set(Number(node.x),0,Number(node.z));
  group.userData={type:"node",data:node,labelY:Number(node.height)*Number(node.scale)+1.2};

  const stateColor=nodeColor(node.state);
  const scale=Number(node.scale || 1);
  const height=Number(node.height || 4)*scale;
  const geometry=new THREE.BoxGeometry(2.35*scale,height,2.35*scale);
  const material=new THREE.MeshStandardMaterial({
    color:districtColor(node.id),metalness:.62,roughness:.32,
    emissive:stateColor,emissiveIntensity:.16
  });
  const tower=new THREE.Mesh(geometry,material);
  tower.position.y=height/2;
  tower.castShadow=true;tower.receiveShadow=true;
  tower.userData=group.userData;
  group.add(tower);interactables.push(tower);

  const crown=new THREE.Mesh(
    new THREE.BoxGeometry(2.5*scale,.16,2.5*scale),
    new THREE.MeshStandardMaterial({color:stateColor,emissive:stateColor,emissiveIntensity:.95})
  );
  crown.position.y=height+.08;group.add(crown);

  const antenna=new THREE.Mesh(
    new THREE.CylinderGeometry(.025,.025,1.15,8),
    new THREE.MeshBasicMaterial({color:stateColor})
  );
  antenna.position.y=height+.72;group.add(antenna);

  const beacon=new THREE.Mesh(
    new THREE.SphereGeometry(.11,12,12),
    new THREE.MeshBasicMaterial({color:stateColor})
  );
  beacon.position.y=height+1.3;
  beacon.userData.pulse=Math.random()*Math.PI*2;
  group.add(beacon);
  group.userData.beacon=beacon;
  addLabel(group,node.title,node.metric);
  nodeObjects.set(node.id,group);scene.add(group);
}

(DATA.nodes || []).forEach(addBuilding);

(DATA.portfolio_towers || []).forEach(function(item){
  const height=Math.max(.7,Number(item.height || 1));
  const mesh=new THREE.Mesh(
    new THREE.BoxGeometry(.48,height,.48),
    new THREE.MeshStandardMaterial({color:0x173d54,metalness:.5,roughness:.4,emissive:0x2d9fd0,emissiveIntensity:.18})
  );
  mesh.position.set(Number(item.x),height/2,Number(item.z));
  mesh.castShadow=true;
  mesh.userData={type:"position",data:item};
  scene.add(mesh);interactables.push(mesh);
});

(DATA.strategy_agents || []).forEach(function(agent){
  const mesh=new THREE.Mesh(
    new THREE.IcosahedronGeometry(.24,1),
    new THREE.MeshStandardMaterial({color:0xa86dff,emissive:0x6b35c9,emissiveIntensity:.65,metalness:.35,roughness:.28})
  );
  mesh.position.set(Number(agent.x),.55,Number(agent.z));
  mesh.userData={type:"agent",data:agent,baseY:.55,phase:Math.random()*6.28};
  scene.add(mesh);interactables.push(mesh);
});

const cohortObjects=[];
const arena=nodeObjects.get("arena");
(DATA.strategy_arena || []).slice(0,10).forEach(function(item,index){
  if(!arena)return;
  const angle=(index/Math.max(1,Math.min(10,(DATA.strategy_arena || []).length)))*Math.PI*2;
  const samples=Math.max(0,Number(item.samples || 0));
  const height=.35+Math.min(2.4,samples/80);
  const negative=String(item.evidence_state || "").includes("NEGATIVE");
  const promising=String(item.evidence_state || "").includes("PROMISING");
  const color=negative?0xff6767:(promising?0x4df49b:0xffd166);
  const mesh=new THREE.Mesh(
    new THREE.CylinderGeometry(.16,.21,height,10),
    new THREE.MeshStandardMaterial({color:0x172631,emissive:color,emissiveIntensity:.42,metalness:.3,roughness:.42})
  );
  mesh.position.set(arena.position.x+Math.cos(angle)*2.15,height/2,arena.position.z+Math.sin(angle)*2.15);
  mesh.userData={type:"cohort",data:item};
  scene.add(mesh);interactables.push(mesh);cohortObjects.push(mesh);
});

const workerObjects=[];
function workerTint(state){
  const value=String(state || "");
  if(value==="RISK_REVIEW") return 0xffd166;
  if(value==="LEARNING" || value==="TRAINING") return 0xa86dff;
  if(value==="RESTING" || value==="RECREATION") return 0x59cfff;
  return 0xf5f7ff;
}
(DATA.resident_agents || []).forEach(function(worker,index){
  const home=nodeObjects.get(worker.home);
  const destination=nodeObjects.get(worker.destination);
  if(!home || !destination)return;
  const group=new THREE.Group();
  const body=new THREE.Mesh(
    new THREE.CylinderGeometry(.09,.13,.34,8),
    new THREE.MeshStandardMaterial({color:0x233947,emissive:workerTint(worker.state),emissiveIntensity:.24,roughness:.5})
  );
  body.position.y=.24;
  const head=new THREE.Mesh(
    new THREE.SphereGeometry(.095,10,10),
    new THREE.MeshStandardMaterial({color:workerTint(worker.state),roughness:.45})
  );
  head.position.y=.49;
  group.add(body);group.add(head);
  const homePos=home.position.clone();
  const workPos=destination.position.clone();
  const offset=((index%4)-1.5)*.24;
  homePos.x+=offset;homePos.z+=((index%3)-1)*.18;
  workPos.x+=offset;workPos.z+=((index%3)-1)*.18;
  group.position.copy(homePos);
  group.userData={type:"resident",data:worker,home:homePos,work:workPos,phase:index*.57,speed:.18+(index%4)*.025};
  body.userData=group.userData;head.userData=group.userData;
  scene.add(group);interactables.push(body);interactables.push(head);workerObjects.push(group);
});

const recreation=nodeObjects.get("recreation");
if(recreation){
  for(let i=0;i<9;i++){
    const a=(i/9)*Math.PI*2;
    const trunk=new THREE.Mesh(new THREE.CylinderGeometry(.035,.05,.36,6),new THREE.MeshStandardMaterial({color:0x392918}));
    const crown=new THREE.Mesh(new THREE.ConeGeometry(.22,.55,7),new THREE.MeshStandardMaterial({color:0x1d6b45,roughness:.9}));
    trunk.position.set(recreation.position.x+Math.cos(a)*2.2,.18,recreation.position.z+Math.sin(a)*1.7);
    crown.position.set(trunk.position.x,.65,trunk.position.z);
    scene.add(trunk);scene.add(crown);
  }
}

function curveFor(source,target){
  const a=nodeObjects.get(source).position.clone();
  const b=nodeObjects.get(target).position.clone();
  a.y=.38;b.y=.38;
  const mid=a.clone().lerp(b,.5);
  mid.y=2.4+Math.min(3,a.distanceTo(b)*.11);
  return new THREE.QuadraticBezierCurve3(a,mid,b);
}
(DATA.flows || []).forEach(function(flow,index){
  const curve=curveFor(flow.source,flow.target);
  const geometry=new THREE.BufferGeometry().setFromPoints(curve.getPoints(42));
  const material=new THREE.LineBasicMaterial({color:0x2f9fcc,transparent:true,opacity:.28});
  const line=new THREE.Line(geometry,material);
  line.userData={source:flow.source,target:flow.target,label:flow.label};
  scene.add(line);

  const particle=new THREE.Mesh(
    new THREE.SphereGeometry(.085,10,10),
    new THREE.MeshBasicMaterial({color:0x69dcff})
  );
  particle.userData={phase:index/(DATA.flows.length || 1),curve:curve};
  scene.add(particle);
  flowObjects.push({line:line,particle:particle,curve:curve});
});


const cityVisuals=scene.children.filter(function(object){return !object.isLight;});
const brainGroup=new THREE.Group();
brainGroup.visible=false;
scene.add(brainGroup);
const brainObjects=new Map();
const brainEdgeObjects=[];
const brainData=DATA.decision_graph || {nodes:[],edges:[],summary:{}};
let brainMode=false;

function brainColor(node){
  if(node.state==="offline") return 0xff6767;
  if(node.state==="waiting") return 0xffd166;
  const byKind={feature:0x59cfff,decision:0xa86dff,gate:0x4df49b,outcome:0x55b8ff};
  return byKind[node.kind] || 0x9ccfe6;
}
function addBrainLabel(mesh,node){
  if(!node.label)return;
  const div=document.createElement("div");
  div.className="label";
  div.innerHTML=esc(node.title)+"<small>"+esc(node.metric)+"</small>";
  const label=new CSS2DObject(div);
  label.position.set(0,Number(node.size || .28)*2.2,0);
  mesh.add(label);
}
(brainData.nodes || []).forEach(function(node){
  const radius=Math.max(.12,Number(node.size || .28));
  const color=brainColor(node);
  const mesh=new THREE.Mesh(
    new THREE.IcosahedronGeometry(radius,1),
    new THREE.MeshStandardMaterial({
      color:color,
      emissive:color,
      emissiveIntensity:node.kind==="decision"?.72:.42,
      metalness:.28,
      roughness:.3
    })
  );
  mesh.position.set(Number(node.x || 0),Number(node.y || 0),Number(node.z || 0));
  mesh.userData={type:"brain",data:node,phase:Math.random()*6.28,baseScale:1};
  addBrainLabel(mesh,node);
  brainGroup.add(mesh);
  brainObjects.set(node.id,mesh);
  interactables.push(mesh);
});
(brainData.edges || []).forEach(function(edge,index){
  const source=brainObjects.get(edge.source);
  const target=brainObjects.get(edge.target);
  if(!source || !target)return;
  const a=source.position.clone();
  const b=target.position.clone();
  const mid=a.clone().lerp(b,.5);
  mid.y+=.35+Math.min(1.2,a.distanceTo(b)*.05);
  const curve=new THREE.QuadraticBezierCurve3(a,mid,b);
  const geometry=new THREE.BufferGeometry().setFromPoints(curve.getPoints(24));
  const blocked=edge.kind==="blocked";
  const color=blocked?0xff6767:0x59cfff;
  const line=new THREE.Line(
    geometry,
    new THREE.LineBasicMaterial({
      color:color,
      transparent:true,
      opacity:blocked?.8:.28
    })
  );
  brainGroup.add(line);
  const particle=new THREE.Mesh(
    new THREE.SphereGeometry(blocked?.055:.045,8,8),
    new THREE.MeshBasicMaterial({color:blocked?0xff6767:0x7de4ff})
  );
  particle.userData={curve:curve,phase:index/Math.max(1,(brainData.edges || []).length)};
  brainGroup.add(particle);
  brainEdgeObjects.push({line:line,particle:particle,curve:curve});
});
function setBrainMode(enabled){
  brainMode=enabled;
  cityVisuals.forEach(function(object){object.visible=!brainMode;});
  brainGroup.visible=brainMode;
  const button=document.getElementById("brain");
  button.classList.toggle("active",brainMode);
  button.textContent=brainMode?"CITY MAP":"BRAIN MAP";
  if(brainMode){
    camera.position.set(14,10,19);
    controls.target.set(0,2,0);
    inspector.innerHTML="<div class='eyebrow'>ORACLE BRAIN MAP</div><h3>Decision provenance network</h3><div class='metric'>"+
      String((brainData.summary || {}).traced_decisions || 0)+" decisions · "+
      String((brainData.summary || {}).linked_outcomes || 0)+" linked outcomes</div><p>"+
      "Blue = evidence, purple = Oracle decision, green = passed safety gate, red = blocked, and outcome nodes = recorded paper-trade result. This view cannot place trades."+
      "</p>";
  }else{
    resetView();
    inspector.innerHTML="<div class='eyebrow'>ORACLE CITY</div><h3>Living Oracle City</h3><div class='metric'>"+esc(String(DATA.city_mood || "UNKNOWN"))+" · "+String((DATA.resident_agents || []).length)+" workers</div><p>Workers move between homes, research, Council, risk, paper execution, training, and recovery districts. Motion is illustrative; persisted Oracle state drives their assigned work.</p>";
  }
  controls.update();
}
document.getElementById("brain").onclick=function(){setBrainMode(!brainMode);};

function inspect(kind,data){
  let eyebrow="ORACLE NODE",title="",metric="",detail="";
  if(kind==="node"){
    title=data.title;metric=data.metric;detail=data.detail;
  } else if(kind==="position"){
    eyebrow="PORTFOLIO POSITION";
    title=data.symbol+" · "+String(data.market || "").toUpperCase();
    metric=money(data.value);
    detail="Quantity "+String(data.quantity)+" · tower height scales with known marked exposure.";
  } else if(kind==="agent"){
    eyebrow="PATTERN AGENT";
    title=data.title;
    metric=data.symbol+" · "+data.action+" · score "+Number(data.score || 0).toFixed(1);
    detail="Observed strategy identity: "+data.strategy+". This agent is visualization-only.";
  } else if(kind==="brain"){
    eyebrow="ORACLE BRAIN · "+String(data.kind || "node").toUpperCase();
    title=data.title;
    metric=data.metric;
    detail=data.detail;
  } else if(kind==="resident"){
    eyebrow="ORACLE CITY WORKER · VISUAL ONLY";
    title=data.title;
    metric=String(data.state || "IDLE").replaceAll("_"," ");
    detail=data.detail+" Assigned district: "+String(data.destination || "unknown")+". This worker cannot place or approve trades.";
  } else if(kind==="cohort"){
    eyebrow=data.control?"STRATEGY ARENA · CONTROL":"STRATEGY ARENA · PAPER EVIDENCE";
    title=String(data.strategy || "unknown").replaceAll("_"," ")+" · "+String(data.regime || "unknown");
    metric=String(data.evidence_state || "RESEARCH ONLY")+" · "+String(data.samples || 0)+" samples";
    const ex=data.expectancy==null?"expectancy unavailable":"expectancy "+Number(data.expectancy).toFixed(6);
    const pf=data.profit_factor==null?"PF unavailable":"PF "+Number(data.profit_factor).toFixed(3);
    detail=ex+" · "+pf+". No visual ranking grants execution or promotion authority.";
  }
  inspector.innerHTML="<div class='eyebrow'>"+esc(eyebrow)+"</div><h3>"+esc(title)+"</h3><div class='metric'>"+esc(metric)+"</div><p>"+esc(detail)+"</p>";
}

const raycaster=new THREE.Raycaster();
const pointer=new THREE.Vector2();
function updatePointer(event){
  const rect=renderer.domElement.getBoundingClientRect();
  pointer.x=((event.clientX-rect.left)/rect.width)*2-1;
  pointer.y=-((event.clientY-rect.top)/rect.height)*2+1;
}
function visibleHit(){
  const hits=raycaster.intersectObjects(interactables,false);
  return hits.find(function(hit){
    const isBrain=hit.object.userData.type==="brain";
    return brainMode?isBrain:!isBrain;
  });
}
renderer.domElement.addEventListener("pointermove",function(event){
  updatePointer(event);raycaster.setFromCamera(pointer,camera);
  const hit=visibleHit();
  renderer.domElement.style.cursor=hit?"pointer":"grab";
});
renderer.domElement.addEventListener("click",function(event){
  updatePointer(event);raycaster.setFromCamera(pointer,camera);
  const hit=visibleHit();
  if(hit){inspect(hit.object.userData.type,hit.object.userData.data);}
});

let flowsVisible=true;
document.getElementById("flows").onclick=function(){
  flowsVisible=!flowsVisible;
  flowObjects.forEach(function(item){item.line.visible=flowsVisible;item.particle.visible=flowsVisible;});
  this.textContent=flowsVisible?"FLOWS ON":"FLOWS OFF";
  this.classList.toggle("active",flowsVisible);
};
document.getElementById("autorotate").onclick=function(){
  controls.autoRotate=!controls.autoRotate;controls.autoRotateSpeed=.65;
  this.classList.toggle("active",controls.autoRotate);
};
let workersVisible=true;
document.getElementById("workers").onclick=function(){
  workersVisible=!workersVisible;
  workerObjects.forEach(function(worker){worker.visible=workersVisible;});
  this.textContent=workersVisible?"WORKERS ON":"WORKERS OFF";
  this.classList.toggle("active",workersVisible);
};
function mobileView(){
  return window.matchMedia && window.matchMedia("(max-width:720px)").matches;
}
function resetView(){
  if(mobileView()){
    camera.position.set(2.5,31,28);
    controls.target.set(2.5,1.4,0);
  }else{
    camera.position.set(17,18,27);
    controls.target.set(2.5,1.8,0);
  }
  controls.update();
}
document.getElementById("reset").onclick=resetView;
document.getElementById("topview").onclick=function(){
  camera.position.set(2.5,38,.01);controls.target.set(2.5,0,0);controls.update();
};

const replay=DATA.replay || [];
const timeline=document.getElementById("timeline");
const replayTitle=document.getElementById("replayTitle");
const replayDetail=document.getElementById("replayDetail");
const play=document.getElementById("play");
timeline.max=String(Math.max(0,replay.length-1));
timeline.value=String(Math.max(0,replay.length-1));
let replayIndex=Number(timeline.value || 0);
let replayTimer=null;

function highlightPath(path){
  const active=new Set();
  for(let i=0;i<path.length-1;i++){active.add(path[i]+"|"+path[i+1]);}
  flowObjects.forEach(function(item){
    const key=item.line.userData.source+"|"+item.line.userData.target;
    const on=active.has(key);
    item.line.material.color.setHex(on?0x6affb4:0x2f9fcc);
    item.line.material.opacity=on?.95:.2;
    item.particle.material.color.setHex(on?0x6affb4:0x69dcff);
    item.particle.scale.setScalar(on?1.7:1);
  });
  (DATA.nodes || []).forEach(function(node){
    const object=nodeObjects.get(node.id);
    if(!object)return;
    const beacon=object.userData.beacon;
    if(beacon)beacon.scale.setScalar(path.includes(node.id)?1.8:1);
  });
}
function showReplay(index){
  if(!replay.length){
    replayTitle.textContent="No replay events available";
    replayDetail.textContent="Oracle City will populate this timeline from persisted decisions, trades, and intelligence.";
    return;
  }
  replayIndex=Math.max(0,Math.min(replay.length-1,index));
  timeline.value=String(replayIndex);
  const item=replay[replayIndex];
  replayTitle.textContent=String(item.kind || "event").toUpperCase()+" · "+item.title;
  replayDetail.textContent=(item.time?item.time+" · ":"")+item.detail;
  highlightPath(item.path || []);
}
timeline.oninput=function(){showReplay(Number(this.value));};
play.onclick=function(){
  if(replayTimer){
    clearInterval(replayTimer);replayTimer=null;play.textContent="PLAY";play.classList.remove("active");return;
  }
  play.textContent="PAUSE";play.classList.add("active");
  replayTimer=setInterval(function(){
    replayIndex=(replayIndex+1)%Math.max(1,replay.length);
    showReplay(replayIndex);
  },1500);
};
showReplay(replayIndex);

let lastMobileView=null;
function resize(){
  const width=app.clientWidth,height=app.clientHeight;
  camera.aspect=width/height;camera.updateProjectionMatrix();
  renderer.setSize(width,height,false);
  labelRenderer.setSize(width,height);
  const isMobile=mobileView();
  if(lastMobileView===null || lastMobileView!==isMobile){
    lastMobileView=isMobile;
    if(!brainMode)resetView();
  }
}
new ResizeObserver(resize).observe(app);
resize();

let elapsed=0;
function animate(){
  requestAnimationFrame(animate);
  elapsed+=.012;
  controls.update();
  flowObjects.forEach(function(item,index){
    const phase=(elapsed*.13+item.particle.userData.phase)%1;
    item.particle.position.copy(item.curve.getPointAt(phase));
  });
  brainEdgeObjects.forEach(function(item){
    const phase=(elapsed*.18+item.particle.userData.phase)%1;
    item.particle.position.copy(item.curve.getPointAt(phase));
  });
  if(brainMode){
    brainObjects.forEach(function(object){
      const pulse=1+Math.sin(elapsed*2.4+object.userData.phase)*.06;
      object.scale.setScalar(pulse);
      object.rotation.y+=.004;
    });
  }
  interactables.forEach(function(object){
    if(object.userData.type==="agent"){
      object.position.y=object.userData.baseY+Math.sin(elapsed*2.2+object.userData.phase)*.09;
      object.rotation.y+=.012;
    }
  });
  workerObjects.forEach(function(worker){
    const cycle=(Math.sin(elapsed*worker.userData.speed+worker.userData.phase)+1)/2;
    const state=String(worker.userData.data.state || "");
    const restBias=(state==="RESTING" || state==="HOME")?.18:(state==="RECREATION"?.72:cycle);
    worker.position.lerpVectors(worker.userData.home,worker.userData.work,restBias);
    worker.position.y=.02+Math.abs(Math.sin(elapsed*5+worker.userData.phase))*.035;
    const direction=worker.userData.work.clone().sub(worker.userData.home);
    if(direction.lengthSq()>0.001)worker.rotation.y=Math.atan2(direction.x,direction.z);
  });
  (DATA.nodes || []).forEach(function(node){
    const object=nodeObjects.get(node.id);
    if(object && object.userData.beacon){
      const pulse=1+Math.sin(elapsed*3+object.userData.beacon.userData.pulse)*.18;
      object.userData.beacon.scale.setScalar(pulse);
    }
  });
  renderer.render(scene,camera);
  labelRenderer.render(scene,camera);
}
fallback.style.display="none";
status.style.display="none";
animate();
</script>
</body>
</html>"""
    return document.replace("__ORACLE_DATA__", encoded)


__all__ = ["render_oracle_city_component"]
