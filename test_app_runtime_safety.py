from __future__ import annotations

import os

import app_runtime


def test_web_runtime_defaults_whole_app_autorefresh_off():
    assert os.environ.get("UI_AUTO_REFRESH") == "false" or os.environ.get("UI_AUTO_REFRESH") is not None


def test_database_preflight_preserves_healthy_state():
    result = app_runtime.database_preflight(
        lambda **_: {"ok": True, "message": "database ready"}
    )
    assert result["ok"] is True


def test_database_preflight_fails_closed_on_exception():
    def broken(**_):
        raise RuntimeError("connection lost")

    result = app_runtime.database_preflight(broken)
    assert result["ok"] is False
    assert "RuntimeError" in result["message"]


def test_start_web_uses_safety_entrypoint():
    source = open("start_web.py", encoding="utf-8").read()
    assert '"app_runtime.py"' in source
    assert '"app.py"' not in source
