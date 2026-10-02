from oracle_city_component import render_oracle_city_component

def test_city_document_has_no_literal_newline_tokens_that_break_module_boot():
    html = render_oracle_city_component({})
    assert "\\nconst DATA" not in html
    assert "</div>\\n  <div id=\"fallbackCity\"" not in html

def test_city_renderer_uses_bounded_high_dpi_scaling():
    html = render_oracle_city_component({})
    assert "const renderScale=" in html
    assert "2.5" in html
    assert "1.75" in html


def test_city_cinematic_architecture_is_present():
    html = render_oracle_city_component({})
    assert "addArchitecturalDetail" in html
    assert "cinematic boulevard lighting" in html
    assert "curbGlowMat" in html
    assert "opts.rows||14" in html


def test_city_declares_brain_mode_before_initial_time_of_day_boot():
    html = render_oracle_city_component({})
    declaration = html.index("let brainMode=false,workersVisible=true,trafficVisible=true;")
    first_time_of_day = html.index("applyTimeOfDay();")
    assert declaration < first_time_of_day
    assert html.count("let brainMode=false,workersVisible=true,trafficVisible=true;") == 1


def test_city_activates_visible_fallback_on_renderer_runtime_error():
    html = render_oracle_city_component({})
    assert "function activateCityFallback(reason)" in html
    assert 'fallbackCity.classList.add("show")' in html
    assert 'window.addEventListener("error"' in html
    assert 'window.addEventListener("unhandledrejection"' in html
    assert 'window.__oracleCityReady=true' in html
