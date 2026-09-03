from __future__ import annotations

import os
import sys


def main() -> None:
    port = os.getenv("PORT", "8501").strip() or "8501"
    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        "app_runtime.py",
        "--server.address=0.0.0.0",
        f"--server.port={port}",
        "--server.headless=true",
        "--browser.gatherUsageStats=false",
    ]
    print(f"Starting GARIBALDI MARKET ORACLE web service on 0.0.0.0:{port}", flush=True)
    print("ORACLE WEB SOURCE SYNC | current-main deployment requested", flush=True)
    print("Command: " + " ".join(command), flush=True)
    os.execv(sys.executable, command)


# Production source-sync marker: current main, 2026-09-03.
if __name__ == "__main__":
    main()
