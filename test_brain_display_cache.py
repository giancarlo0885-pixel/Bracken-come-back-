from datetime import datetime, timezone
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


def test_brain_cached_display_keeps_read_only_cte_cockpit_and_scopes_lifecycle():
    captured = []

    def fetch(query, params=()):
        captured.append(query)
        if query.lstrip().startswith("WITH active AS"):
            return [{"generation": 1, "started_at": datetime.now(timezone.utc),
                     "aeve_trades": 10, "accepted_outcomes": 0}]
        return []

    with patch("database.database_ready", return_value={"ok": True}), patch("database.rows", side_effect=fetch):
        app = AppTest.from_file("pages/3_Oracle_Brain.py").run(timeout=15)
        assert not app.exception
        assert "Oracle Research Cockpit" in [h.value for h in app.subheader]
        lifecycle = next(q for q in captured if "AS closed_trades" in q)
        assert "m.market IN ('crypto','US_CRYPTO','us_crypto')" in lifecycle
        app.radio[0].set_value("All markets").run(timeout=15)
        assert not app.exception
        assert any("AS closed_trades" in q and "m.market IN" not in q for q in captured)
