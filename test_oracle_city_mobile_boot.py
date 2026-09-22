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
