from __future__ import annotations

import ast
from pathlib import Path


STREAMLIT_UI_CALLS = {
    "dataframe",
    "info",
    "warning",
    "error",
    "success",
    "metric",
    "write",
    "markdown",
    "plotly_chart",
    "line_chart",
}


def _is_streamlit_ui_call(node: ast.AST) -> bool:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return (
            isinstance(node.func.value, ast.Name)
            and node.func.value.id == "st"
            and node.func.attr in STREAMLIT_UI_CALLS
        )
    return any(_is_streamlit_ui_call(child) for child in ast.iter_child_nodes(node))


def test_app_has_no_bare_streamlit_ternary_calls():
    tree = ast.parse(Path("app.py").read_text(encoding="utf-8"))
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.IfExp)
        and _is_streamlit_ui_call(node.value)
    ]

    assert offenders == []


def test_crypto_ownership_section_cannot_render_deltagenerator_expression():
    source = Path("app.py").read_text(encoding="utf-8")
    start = source.index('st.markdown("**WHAT I OWN NOW**")')
    end = source.index('st.markdown("**BEST CRYPTO TRADES NOW**")', start)
    crypto_ownership = source[start:end]

    assert ") if crypto_focus[" not in crypto_ownership
    assert "DeltaGenerator" not in crypto_ownership
    assert "st.dataframe" in crypto_ownership
    assert "st.info" in crypto_ownership


DIAGNOSTIC_EXPANDER_LABELS = {
    "REJECTED / WAITING",
    "WATCHLIST / WAITING",
    "PROVIDER DIAGNOSTICS",
}


def test_ui_rejected_waiting_sections_collapsed_by_default():
    tree = ast.parse(Path("app.py").read_text(encoding="utf-8"))

    expander_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "expander"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "st"
    ]

    diagnostic_calls = []
    for call in expander_calls:
        if not call.args:
            continue
        label_arg = call.args[0]
        if isinstance(label_arg, ast.Constant) and isinstance(label_arg.value, str):
            if label_arg.value in DIAGNOSTIC_EXPANDER_LABELS:
                diagnostic_calls.append(call)

    assert diagnostic_calls, "Expected to find diagnostic expander calls in app.py"

    for call in diagnostic_calls:
        expanded_kwarg = next((kw for kw in call.keywords if kw.arg == "expanded"), None)
        assert expanded_kwarg is not None, (
            f"st.expander at line {call.lineno} is missing an explicit expanded= argument"
        )
        assert isinstance(expanded_kwarg.value, ast.Constant) and expanded_kwarg.value.value is False, (
            f"st.expander at line {call.lineno} must use expanded=False"
        )
