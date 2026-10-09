"""Static chronology contracts for paper-only forecast provenance."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = (ROOT / "market_worker.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _function(name):
    return next(n for n in TREE.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)


def _calls(fn, name):
    return [n for n in ast.walk(fn) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name) and n.func.id == name]


def test_forecast_precedes_decision_timestamp_and_both_writes():
    for scan, horizon in (("fast_scan_market", '3 if market == "cash" else 1'),
                          ("scan_market", "5")):
        fn = _function(scan)
        expected = ast.parse(horizon, mode="eval").body
        forecasts = [n for n in _calls(fn, "forecast_price")
                     if len(n.args) > 1 and ast.dump(n.args[1]) == ast.dump(expected)]
        assert len(forecasts) == 1, scan
        forecast_line = forecasts[0].lineno
        timestamps = [n for n in ast.walk(fn) if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == "signal_created_at" for t in n.targets)
                      and isinstance(n.value, ast.Call)
                      and isinstance(n.value.func, ast.Name) and n.value.func.id == "utc_now"]
        assert len(timestamps) >= 1, scan
        following = [n for n in timestamps if n.lineno > forecast_line]
        assert len(following) == 1, scan
        decision_line = following[0].lineno
        signal_writes = [n for n in _calls(fn, "save_json_signal") if n.lineno > decision_line]
        forecast_writes = [n for n in _calls(fn, "save_forecast") if n.lineno > decision_line]
        assert len(signal_writes) == 1 and len(forecast_writes) == 1, scan
        assert forecast_line < decision_line < signal_writes[0].lineno < forecast_writes[0].lineno


def test_signal_payload_preserves_signed_edge_and_forecast_identity():
    fn = _function("_signal_payload")
    code = ast.get_source_segment(SOURCE, fn)
    assert 'payload["expected_edge_pct"] = forecast_edge' in code
    assert 'payload["forecast_id"] = getattr(signal, "forecast_id", None)' in code
    assert 'payload["edge_provenance"] = "predecision_forecast_expected_move_pct"' in code
    assert "if forecast_edge is not None:" in code


def test_shadow_cutoff_stays_at_original_signal_time():
    shadow = (ROOT / "paper_shadow_exit_challenger.py").read_text(encoding="utf-8")
    assert "AND NULLIF(created_at,'')::timestamptz <= %s" in shadow
    assert '(market, symbol, item.get("id"), item["created_at"])' in shadow
