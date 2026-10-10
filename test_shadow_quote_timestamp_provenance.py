"""Fail-closed quote provenance regression for the paper-only exit sampler."""
import ast
from pathlib import Path

SOURCE = (Path(__file__).resolve().parent / "paper_shadow_exit_challenger.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def test_shadow_exit_never_uses_signal_creation_as_quote_timestamp():
    fn = next(node for node in TREE.body if isinstance(node, ast.FunctionDef)
              and node.name == "_trigger_telemetry")
    quote_assignments = [
        node for node in ast.walk(fn)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "quote_time" for target in node.targets)
    ]
    assert len(quote_assignments) == 1
    expr = quote_assignments[0].value
    assert isinstance(expr, ast.BoolOp) and isinstance(expr.op, ast.Or)
    assert len(expr.values) == 2
    assert all(isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)
               and isinstance(value.func.value, ast.Name)
               and value.func.value.id == "quote" and value.func.attr == "get"
               for value in expr.values)
    assert [value.args[0].value for value in expr.values] == [
        "quote_timestamp", "source_quote_timestamp"
    ]
    # Missing or invalid source timestamps must be rejected, not substituted.
    code = ast.get_source_segment(SOURCE, fn)
    assert 'if not quote_verified or observed_at is None:' in code
    assert 'return reject("unverified_quote_or_timestamp")' in code
    assert 'return reject("future_quote")' in code
    assert 'return reject("stale_quote")' in code
