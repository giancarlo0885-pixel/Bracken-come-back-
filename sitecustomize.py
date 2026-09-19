"""Startup marker for GARIBALDI MARKET ORACLE.

Paper-autonomous-learning policy is installed explicitly by the stock and
crypto worker entry points via paper_autonomous_learning.install_paper_autonomous_learning().
Keep sitecustomize side-effect free so confidence/liquidity multipliers have one
runtime owner and cannot depend on Python startup/import order.

This module intentionally grants no execution permission and changes no
live-money controls.
"""

from __future__ import annotations


PAPER_LEARNING_POLICY_OWNER = "paper_autonomous_learning"
