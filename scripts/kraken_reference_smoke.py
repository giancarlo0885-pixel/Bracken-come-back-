"""Explicit public connectivity check; no database, credentials, or orders.

Run from repository root: python scripts/kraken_reference_smoke.py --seconds 20
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import websocket
from kraken_reference_book import ReferenceBooks, SYMBOLS
from kraken_reference_runtime import URL


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=20, choices=range(5, 61))
    args = parser.parse_args()
    state = ReferenceBooks()
    ws = None
    try:
        ws = websocket.create_connection(URL, timeout=8)
        state.opened(datetime.now(timezone.utc))
        ws.send(json.dumps({"method": "subscribe", "params": {
            "channel": "book", "symbol": list(SYMBOLS), "depth": 10, "snapshot": True}, "req_id": 1}))
        ws.settimeout(1)
        deadline = time.monotonic()+args.seconds
        seen = set()
        while time.monotonic() < deadline:
            try: raw = ws.recv()
            except websocket.WebSocketTimeoutException: continue
            state.ingest(raw, datetime.now(timezone.utc))
            seen.update(state.books)
        health = state.health(datetime.now(timezone.utc))
        passed = len(health["quotes"]) == 2 and seen == set(SYMBOLS) and state.counts["updates"] > 0
        print(json.dumps({"actual_public_connectivity": True, "passed": passed, "health": health}, sort_keys=True))
        return 0 if passed else 1
    except Exception as exc:
        print(json.dumps({"actual_public_connectivity": True, "passed": False,
                          "error_type": type(exc).__name__, "health": state.health(datetime.now(timezone.utc))}))
        return 1
    finally:
        state.invalidate()
        if ws: ws.close()


if __name__ == "__main__":
    raise SystemExit(main())
