"""
Local parquet cache for OHLCV data.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).parent.parent.parent / "data" / "ohlcv"


def _cache_path(symbol: str, timeframe: str) -> Path:
    safe_symbol = symbol.replace("/", "_")
    return CACHE_DIR / f"{safe_symbol}_{timeframe}.parquet"


def load_cached(symbol: str, timeframe: str) -> pd.DataFrame | None:
    path = _cache_path(symbol, timeframe)
    if not path.exists():
        return None
    try:
        return pd.read_parquet(path)
    except Exception as e:
        logger.warning(f"Failed to load cache {path}: {e}")
        return None


def save_cache(df: pd.DataFrame, symbol: str, timeframe: str):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(symbol, timeframe)

    existing = load_cached(symbol, timeframe)
    if existing is not None and len(existing) > 0:
        combined = pd.concat([existing, df]).drop_duplicates(subset=["timestamp"]).sort_values("timestamp")
    else:
        combined = df.sort_values("timestamp")

    combined.reset_index(drop=True).to_parquet(path, index=False)
    logger.info(f"Cached {len(combined)} bars for {symbol} {timeframe}")


def detect_gaps(df: pd.DataFrame, timeframe: str = "4h") -> list[tuple]:
    """Find missing bars in the data."""
    if len(df) < 2:
        return []

    freq_map = {"4h": pd.Timedelta(hours=4), "1h": pd.Timedelta(hours=1), "1d": pd.Timedelta(days=1)}
    expected_delta = freq_map.get(timeframe, pd.Timedelta(hours=4))

    gaps = []
    timestamps = pd.to_datetime(df["timestamp"])
    diffs = timestamps.diff()

    for i in range(1, len(diffs)):
        if diffs.iloc[i] > expected_delta * 1.5:
            gaps.append((timestamps.iloc[i - 1], timestamps.iloc[i]))

    return gaps
