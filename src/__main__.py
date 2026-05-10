"""
Entry point: python -m src
Starts the FastAPI server with structured logging and graceful shutdown.
"""
from __future__ import annotations

import json
import os
import signal
import sys
from pathlib import Path

import uvicorn

from src.logging_config import setup_logging

CONFIG_DIR = Path(__file__).parent.parent / "config"


def main():
    mode = os.environ.get("BOT_MODE", "dryrun")
    config_file = CONFIG_DIR / f"config.{mode}.json"

    if not config_file.exists():
        print(f"Config not found: {config_file}, falling back to dryrun")
        config_file = CONFIG_DIR / "config.dryrun.json"
        mode = "dryrun"

    setup_logging(mode=mode)

    config = json.loads(config_file.read_text())
    host = config.get("dashboard", {}).get("host", "127.0.0.1")
    port = config.get("dashboard", {}).get("port", 8000)

    print(f"Starting Autobot in {mode} mode on {host}:{port}")

    uvicorn.run(
        "src.app:app",
        host=host,
        port=port,
        reload=(mode == "dryrun"),
        log_level="info",
    )


if __name__ == "__main__":
    main()
