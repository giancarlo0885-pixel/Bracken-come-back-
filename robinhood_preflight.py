from __future__ import annotations

import argparse
import json

import robinhood_agentic_mcp
import robinhood_crypto_api


def run_preflight(mode: str = "direct") -> dict[str, object]:
    if mode == "agentic":
        return robinhood_agentic_mcp.agentic_preflight(None)
    return robinhood_crypto_api.preflight()


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Robinhood crypto preflight. Never places orders.")
    parser.add_argument("--mode", choices=["direct", "agentic"], default="direct")
    args = parser.parse_args()
    print(json.dumps(run_preflight(args.mode), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

