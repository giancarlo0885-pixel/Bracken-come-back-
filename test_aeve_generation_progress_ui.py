import ast
import math
import os
from pathlib import Path

import pytest

from paper_aeve_generation_controller import PROVENANCE_VERSION

def test_aeve_progress_query_contract():
    source = open("pages/3_Oracle_Brain.py", encoding="utf-8").read()
    assert "AEVE Generation Progress" in source
    assert "o.provenance_version = %s" in source
    assert "from paper_aeve_generation_controller import PROVENANCE_VERSION" in source
    assert "entry_evidence_complete" in source
    assert "o.config_hash = g.config_hash" in source
    assert "o.entry_evidence_complete AND o.would_trade" in source
    assert "Verified outcomes" in source
    assert "Incomplete, legacy, or incompatible outcomes" in source


def _page_tree():
    return ast.parse(Path("pages/3_Oracle_Brain.py").read_text(encoding="utf-8"))


def _formatter():
    function = next(node for node in _page_tree().body
                    if isinstance(node, ast.FunctionDef) and node.name == "_format_research_number")
    namespace = {"math": math}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "brain_formatter", "exec"), namespace)
    return namespace[function.name]


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), True])
def test_missing_research_metric_is_unavailable_instead_of_zero_profit(value):
    assert _formatter()(value, 2, prefix="$") == "Unavailable"


def test_actual_measured_zero_is_displayed_as_zero():
    assert _formatter()(0, 2, prefix="$") == "$0.00"
    assert _formatter()(0, 4, suffix="%") == "0.0000%"


def test_postgres_progress_displays_current_provenance_and_unknown_profit_only():
    url = os.getenv("DATABASE_URL", "")
    if not url.startswith(("postgresql://", "postgres://")):
        pytest.skip("PostgreSQL integration service required")
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(url, row_factory=dict_row) as conn:
        conn.execute("""CREATE TEMP TABLE paper_aeve_generations(
            generation INT,started_at TIMESTAMPTZ,config_hash TEXT,status TEXT);
            CREATE TEMP TABLE paper_aeve_provenance_epochs(
            provenance_version INT,started_at TIMESTAMPTZ);
            CREATE TEMP TABLE paper_aeve_generation_outcomes(
            id BIGSERIAL,generation INT,config_hash TEXT,provenance_version INT,
            entry_evidence_complete BOOLEAN,would_trade BOOLEAN,net_pnl DOUBLE PRECISION,
            mfe_pct DOUBLE PRECISION,mae_pct DOUBLE PRECISION,excursion_sample_count INT,cost_pct DOUBLE PRECISION)
        """)
        conn.execute("INSERT INTO paper_aeve_generations VALUES (1,'2026-10-01T00:00:00Z','hash','ACTIVE')")
        epoch = conn.execute("""INSERT INTO paper_aeve_provenance_epochs
            VALUES (%s,'2026-10-15T00:00:00Z') RETURNING started_at""", (PROVENANCE_VERSION,)).fetchone()["started_at"]
        conn.execute("""INSERT INTO paper_aeve_generation_outcomes(
            generation,config_hash,provenance_version,entry_evidence_complete,would_trade,net_pnl)
            VALUES (1,'hash',7,TRUE,TRUE,999),(1,'hash',%s,TRUE,FALSE,-1)
        """, (PROVENANCE_VERSION,))
        queries = []
        for node in ast.walk(_page_tree()):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "_brain_rows" and len(node.args) > 1
                    and isinstance(node.args[0], ast.Constant)
                    and "paper_aeve_generation_outcomes" in str(node.args[0].value)):
                params = eval(compile(ast.Expression(node.args[1]), "brain_params", "eval"),
                              {"PROVENANCE_VERSION": PROVENANCE_VERSION})
                queries.append((node.args[0].value, params))
        assert len(queries) == 3
        for query, params in queries:
            result = conn.execute(query, params).fetchone()
            if "observed_outcomes" in result:
                assert result["verified_trades"] == 1
                assert result["accepted_trades"] == 0
                assert result["net_pnl"] is None
                assert _formatter()(result["net_pnl"], 2, prefix="$") == "Unavailable"
            elif "aeve_trades" in result:
                assert result["aeve_trades"] == 1
                assert result["accepted_outcomes"] == 0
                assert result["legacy_provenance"] == 1
                # A generation can predate the corrected provenance epoch.
                # Current-version throughput starts when that evidence begins.
                assert result["started_at"] == epoch
            else:
                assert result["trades"] == 0
                assert result["expectancy"] is None
                assert result["profit_factor"] is None
