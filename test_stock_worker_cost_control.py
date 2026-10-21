"""Regression checks for crypto-only cash research cost control."""
import ast
from pathlib import Path


def test_cash_worker_opt_in_guard_precedes_database_bootstrap():
    source = Path(__file__).with_name("stock_worker.py").read_text()
    tree = ast.parse(source)
    assert 'ENABLE_CASH_MARKET_RESEARCH' in source
    assert '"false"' in source
    assert 'market_worker.run_worker("cash")' in source
    main = next(node for node in tree.body if isinstance(node, ast.If) and isinstance(node.test, ast.Compare) and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__")
    guard = next(node for node in main.body if isinstance(node, ast.If))
    bootstrap = next(node for node in main.body if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id == "prepare_capital_readiness_runtime")
    assert guard.lineno < bootstrap.lineno
    assert any(isinstance(node, ast.While) for node in guard.body)
