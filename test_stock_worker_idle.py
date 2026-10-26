"""Regression tests for the crypto-only stock-worker standby mode."""
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest


ROOT = Path(__file__).resolve().parent
ENTRYPOINT = ROOT / "stock_worker.py"


class StockWorkerIdleTests(unittest.TestCase):
    def test_pause_guard_precedes_heavy_imports(self):
        source = ENTRYPOINT.read_text(encoding="utf-8")
        self.assertLess(
            source.index('if __name__ == "__main__" and not _cash_research_enabled():'),
            source.index("import market_worker"),
        )
        self.assertIn("time.sleep(3600)", source)

    def test_crypto_only_worker_stays_alive_without_stock_scans(self):
        env = dict(os.environ)
        env["ENABLE_CASH_MARKET_RESEARCH"] = "false"
        process = subprocess.Popen(
            [sys.executable, "-u", str(ENTRYPOINT)],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            time.sleep(0.8)
            self.assertIsNone(
                process.poll(),
                "Railway restartPolicyType=ALWAYS must not repeatedly restart the worker",
            )
        finally:
            process.terminate()
            try:
                output, _ = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                output, _ = process.communicate(timeout=5)
        self.assertIn("ORACLE STOCK RESEARCH PAUSED", output)
        self.assertNotIn("STOCK HISTORY PROVIDER DIAGNOSTIC", output)


if __name__ == "__main__":
    unittest.main()
