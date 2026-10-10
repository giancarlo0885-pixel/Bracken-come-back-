"""Static regression for stage-specific fast forecast diagnostics."""
import ast
from pathlib import Path

SOURCE = (Path(__file__).resolve().parent / "market_worker.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def test_fast_scan_provenance_stages_and_no_silent_failure():
    fn = next(node for node in TREE.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
              and node.name == "fast_scan_market")
    code = ast.get_source_segment(SOURCE, fn)
    assert 'stage = "forecast_calculation"' in code
    assert 'stage = "signal_persistence"' in code
    assert 'stage = "forecast_persistence"' in code
    assert "FAST_FORECAST_MISSING" in code
    assert "FAST_PROVENANCE_WRITE_FAILED" in code
    assert "type(exc).__name__" in code
    assert 'log.debug("Fast persistence failed' not in code
    assert code.index('stage = "forecast_calculation"') < code.index("forecast = forecast_price(")
    assert code.index('stage = "signal_persistence"') < code.index("signal_id = save_json_signal(")
    assert code.index('stage = "forecast_persistence"') < code.index("save_forecast(market, symbol, forecast, scan_type=\"fast\"")
