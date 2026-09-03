from __future__ import annotations

import os
import runpy
from typing import Any, Callable

# Whole-app Streamlit autorefresh causes every widget interaction/result to be
# recreated on a timer. Default it off for the production entry point; operators
# can still explicitly opt in with UI_AUTO_REFRESH=true.
os.environ.setdefault("UI_AUTO_REFRESH", "false")

from database import database_ready
from migration_runtime_safety import install_migration_runtime_safety


# Install before app.py is executed so every later ``from migrations import
# run_migrations`` in the Streamlit process receives the restart-safe runner.
install_migration_runtime_safety()


def database_preflight(
    checker: Callable[..., dict[str, Any]] = database_ready,
) -> dict[str, Any]:
    """Return an explicit web-readiness state before rendering financial data.

    app.py contains convenience readers that intentionally tolerate failures for
    non-critical dashboard sections. This outer guard prevents a real database
    outage from being converted into an apparently empty/default portfolio.
    """
    try:
        result = checker(connect_timeout=5)
    except TypeError:
        result = checker()
    except Exception as exc:
        return {
            "ok": False,
            "configuration_error": False,
            "transient": True,
            "message": f"database readiness check failed: {exc.__class__.__name__}",
        }
    if not isinstance(result, dict):
        return {
            "ok": False,
            "configuration_error": False,
            "transient": True,
            "message": "database readiness check returned an invalid response",
        }
    return result


def render_database_block(result: dict[str, Any]) -> None:
    import streamlit as st

    st.set_page_config(
        page_title="GARIBALDI MARKET ORACLE — Data Safety Hold",
        page_icon="ORCL",
        layout="wide",
    )
    st.error("ACCOUNT DATA UNAVAILABLE — PAPER TRADING VIEW PAUSED")
    st.warning(
        "PostgreSQL did not pass the financial-data preflight. Portfolio balances, "
        "positions, P/L, and trade history are intentionally hidden instead of "
        "being shown as zero or empty."
    )
    message = str(result.get("message") or "Database connection unavailable")
    st.caption(message.replace("postgresql://", "[database-url-redacted]"))
    if st.button("Retry database connection", type="primary"):
        st.rerun()
    st.stop()


def main() -> None:
    health = database_preflight()
    if not health.get("ok"):
        render_database_block(health)
        return
    # Execute the ordinary Streamlit app only after canonical storage is known
    # to be reachable. app.py remains the single UI implementation.
    runpy.run_path("app.py", run_name="__main__")


if __name__ == "__main__":
    main()
