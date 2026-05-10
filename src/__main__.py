"""
Entry point: python -m src
Starts the FastAPI server with structured logging and graceful shutdown.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from src.logging_config import setup_logging

CONFIG_DIR = Path(__file__).parent.parent / "config"


def _resolve_env_vars(config_text: str) -> str:
    """Replace ${VAR_NAME} placeholders with environment variable values."""
    def replacer(match):
        var_name = match.group(1)
        return os.environ.get(var_name, "")
    return re.sub(r"\$\{([^}]+)\}", replacer, config_text)


def main():
    load_dotenv(Path(__file__).parent.parent / ".env")

    mode = os.environ.get("BOT_MODE", "dryrun")
    config_file = CONFIG_DIR / f"config.{mode}.json"

    if not config_file.exists():
        print(f"Config not found: {config_file}, falling back to dryrun")
        config_file = CONFIG_DIR / "config.dryrun.json"
        mode = "dryrun"

    setup_logging(mode=mode)

    config_text = _resolve_env_vars(config_file.read_text())
    config = json.loads(config_text)
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
