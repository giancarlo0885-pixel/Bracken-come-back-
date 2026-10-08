import logging
from unittest.mock import patch

from stock_transient_quarantine_repair import install_stock_transient_quarantine_repair


class Worker:
    def __init__(self):
        self.calls = []
        self._v39_quarantine_symbol = lambda *args: self.calls.append(args)


def test_transient_logs_throttled_without_persisting(caplog):
    worker = Worker()
    with patch("stock_transient_quarantine_repair.time.monotonic", side_effect=[100, 101, 401]):
        install_stock_transient_quarantine_repair(worker)
        with caplog.at_level(logging.INFO, logger="stock-transient-quarantine"):
            for _ in range(2):
                worker._v39_quarantine_symbol("NVDA", "market_data", "empty_fast_history")
            worker._v39_quarantine_symbol("NVDA", "market_data", "empty_fast_history")
    assert worker.calls == []
    assert caplog.text.count("STOCK TRANSIENT DATA COOLDOWN") == 2


def test_durable_invalid_symbol_failures_still_persist():
    worker = Worker()
    install_stock_transient_quarantine_repair(worker)
    worker._v39_quarantine_symbol("BAD", "market_data", "identity_mismatch")
    assert worker.calls == [("BAD", "market_data", "identity_mismatch")]
