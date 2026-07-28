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
        "app.py",
        "--server.address=0.0.0.0",
        f"--server.port={port}",
        "--server.headless=true",
        "--browser.gatherUsageStats=false",
    ]
    print(f"Starting GARIBALDI MARKET ORACLE web service on 0.0.0.0:{port}", flush=True)
    print("Command: " + " ".join(command), flush=True)
    os.execv(sys.executable, command)


if __name__ == "__main__":
    main()
