"""Decision-time edge persistence contract for fast and deep scans.

These tests intentionally avoid database writes and broker execution.
"""
import ast
from pathlib import Path

SOURCE = Path(__file__).with_name("market_worker.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def test_forecasts_precede_signal_persistence_in_both_scans():
    # Each scan must compute the forecast before assigning its decision timestamp
    # and before persisting the signal; never repair missing edges with hindsight.
    for horizon in ('3 if market == "cash" else 1', '5'):
        calls = [
            node for node in ast.walk(TREE)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "forecast_price"
            and len(node.args) > 1 and ast.dump(node.args[1]) == ast.dump(ast.parse(horizon, mode="eval").body)
        ]
        assert len(calls) == 1
        call = calls[0]
        subsequent = SOURCE.splitlines()[call.lineno - 1:call.lineno + 48]
        text = "\n".join(subsequent)
        assert text.index("signal_created_at = utc_now()") < text.index("save_json_signal(")
        assert "save_forecast(" in text


def test_signal_payload_persists_signed_forecast_edge_and_identity():
    fn = next(node for node in TREE.body if isinstance(node, ast.FunctionDef) and node.name == "_signal_payload")
    code = ast.get_source_segment(SOURCE, fn)
    assert 'payload["expected_edge_pct"] = forecast_edge' in code
    assert 'payload["forecast_id"] = getattr(signal, "forecast_id", None)' in code
    assert 'payload["edge_provenance"] = "predecision_forecast_expected_move_pct"' in code
    assert "if forecast_edge is not None:" in code


def test_shadow_forecast_cutoff_stays_at_original_signal_time():
    shadow = Path(__file__).with_name("paper_shadow_exit_challenger.py").read_text(encoding="utf-8")
    assert "AND NULLIF(created_at,'')::timestamptz <= %s" in shadow
    assert '(market, symbol, item.get("id"), item["created_at"])' in shadow
