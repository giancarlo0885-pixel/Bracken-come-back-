"""Regression protection for Oracle's read-only mobile visualizations."""

from pathlib import Path

from oracle_brain_component import render_oracle_brain_component
from oracle_city_component import render_oracle_city_component


def test_city_does_not_reset_camera_for_spurious_mobile_resizes():
    html = render_oracle_city_component({})
    resize = html.split("function resize(){", 1)[1].split("new ResizeObserver(resize)", 1)[0]
    initial = resize.split("if(lastMobileView===null){", 1)[1]
    initial, responsive = initial.split("}else if(lastMobileView!==mobile){", 1)
    assert "fitBrainView();" in initial and "resetView();" in initial
    assert "fitBrainView();" not in responsive and "resetView();" not in responsive
    assert "camera.position" not in resize and "controls.target" not in resize
    assert "else if(mobile&&!brainMode)" not in html
    assert "controls.enableZoom=true" in html


def test_city_displays_fallback_even_if_module_import_never_runs():
    html = render_oracle_city_component({})
    watchdog = html.index("window.setTimeout(()=>")
    module = html.index('<script type="module">')
    assert watchdog < module
    assert "if(window.__oracleCityReady)return;" in html
    assert 'fallback.classList.add("show")' in html
    assert 'renderer.domElement.addEventListener("webglcontextlost"' in html


def test_city_prioritizes_inspector_and_replay_over_mobile_cockpit():
    html = render_oracle_city_component({})
    assert "#app:has(.inspector.open) .crypto-cockpit" in html
    assert "#app:has(.replay.open) .crypto-cockpit" in html
    assert 'replayPanel.classList.remove("open")' in html
    assert 'inspector.classList.remove("open");replayPanel.classList.toggle("open")' in html


def test_city_and_brain_limit_hidden_and_mobile_animation_work():
    city = render_oracle_city_component({})
    brain = render_oracle_brain_component({})
    assert "if(document.hidden){previousFrameMs=frameMs;return;}" in city
    assert "frameInterval=isMobileDevice?1000/30" in city
    assert "if(document.hidden){previousDrawMs=frameMs;return;}" in brain
    assert "1000/30:1000/60" in brain


def test_brain_uses_uniform_centered_coordinates_and_real_links_only():
    html = render_oracle_brain_component({})
    assert "const W=640,H=560;" in html
    assert "viewScale=Math.max(.001,Math.min(r.width/W,r.height/H))" in html
    assert "offsetX=(r.width-W*viewScale)/2" in html
    assert "offsetY=(r.height-H*viewScale)/2" in html
    assert "(e.clientX-r.left-offsetX)/viewScale" in html
    assert "edges.push({a:i,b:(i*7+5)%nodes.length" not in html
    assert "No inferred/synthetic synapses" in html


def test_brain_viewport_has_enough_height_for_mobile_anatomy():
    page = Path("pages/3_Oracle_Brain.py").read_text(encoding="utf-8")
    html = render_oracle_brain_component({})
    assert "height=700," in page
    assert "#brain{top:168px;bottom:50px;" in html
    assert "height:680px;min-height:680px" in html
    assert "use_container_width=True" not in page


def test_visual_repairs_do_not_add_execution_authority():
    city = render_oracle_city_component({})
    brain = render_oracle_brain_component({})
    for html in (city, brain):
        assert "submit_order(" not in html
        assert "LIVE_TRADING_ARMED=true" not in html
        assert "ENABLE_BROKER_SUBMISSION=true" not in html
