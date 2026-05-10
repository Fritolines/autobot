"""
OHLCV data fetcher using ccxt (Kraken for live, Binance for backfill).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import ccxt
import pandas as pd

logger = logging.getLogger(__name__)

_exchanges: dict[str, ccxt.Exchange] = {}


def get_exchange(name: str = "kraken", config: dict | None = None) -> ccxt.Exchange:
    if name in _exchanges:
        return _exchanges[name]

    cfg = config or {}
    exchange_cls = getattr(ccxt, name)
    exchange = exchange_cls({
        "apiKey": cfg.get("key", ""),
        "secret": cfg.get("secret", ""),
        "enableRateLimit": True,
    })
    _exchanges[name] = exchange
    return exchange


def fetch_ohlcv(
    exchange: ccxt.Exchange,
    symbol: str,
    timeframe: str = "4h",
    since: int | None = None,
    limit: int = 500,
) -> pd.DataFrame:
    """
    Fetch OHLCV data from exchange.
    Returns DataFrame with columns: timestamp, open, high, low, close, volume.
    """
    try:
        raw = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
    except ccxt.RateLimitExceeded:
        logger.warning("Rate limited, waiting 5s")
        time.sleep(5)
        raw = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)

    if not raw:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    df = pd.DataFrame(raw, columns=["timestamp_ms", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp_ms"], unit="ms", utc=True)
    df = df.drop(columns=["timestamp_ms"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


def fetch_daily_ohlcv(
    exchange: ccxt.Exchange,
    symbol: str,
    limit: int = 250,
) -> pd.DataFrame:
    """Fetch daily OHLCV for EMA(200) calculation."""
    return fetch_ohlcv(exchange, symbol, timeframe="1d", limit=limit)


def fetch_ticker(exchange: ccxt.Exchange, symbol: str) -> dict:
    """Fetch current ticker for a symbol."""
    try:
        return exchange.fetch_ticker(symbol)
    except Exception as e:
        logger.error(f"Failed to fetch ticker for {symbol}: {e}")
        return {}
