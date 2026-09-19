from __future__ import annotations

import ast
from pathlib import Path


def test_sitecustomize_does_not_mutate_allocator_policy():
    source = Path("sitecustomize.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert "capital_allocator" not in source
    assert "confidence_multiplier" not in source
    assert "liquidity_multiplier" not in source
    assert "PAPER_LEARNING_POLICY_OWNER" in source
    assert not any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) for node in ast.walk(tree))


def test_worker_entrypoints_install_single_paper_learning_owner():
    for path in ("crypto_worker.py", "stock_worker.py"):
        source = Path(path).read_text(encoding="utf-8")
        assert source.count("install_paper_autonomous_learning()") == 1
        assert "from paper_autonomous_learning import install_paper_autonomous_learning" in source


def test_paper_learning_defaults_remain_conservative_and_live_disarmed():
    source = Path("paper_autonomous_learning.py").read_text(encoding="utf-8")
    assert 'PAPER_LEARNING_CONFIDENCE_MULTIPLIER_FLOOR", "0.45"' in source
    assert 'PAPER_LEARNING_LIQUIDITY_MULTIPLIER_FLOOR", "0.40"' in source
    assert 'not _truthy("ENABLE_BROKER_SUBMISSION")' in source
    assert 'not _truthy("LIVE_TRADING_ARMED")' in source
