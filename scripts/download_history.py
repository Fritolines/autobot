"""
Download historical OHLCV data from Binance Vision (free, no API key).
Saves as parquet files in data/ohlcv/.

Usage:
    python scripts/download_history.py
    python scripts/download_history.py --symbol BTC/EUR --start 2020-01-01
"""
from __future__ import annotations

import argparse
import io
import logging
import sys
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.data_handler.cache import save_cache, CACHE_DIR

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BINANCE_VISION_BASE = "https://data.binance.vision/data/spot/monthly/klines"

SYMBOL_MAP = {
    "BTC/EUR": "BTCEUR",
    "ETH/EUR": "ETHEUR",
    "BTC/USDT": "BTCUSDT",
    "ETH/USDT": "ETHUSDT",
}

TIMEFRAME_MAP = {
    "4h": "4h",
    "1h": "1h",
    "1d": "1d",
}


def download_month(binance_symbol: str, timeframe: str, year: int, month: int) -> pd.DataFrame | None:
    """Download one month of kline data from Binance Vision."""
    month_str = f"{year}-{month:02d}"
    url = f"{BINANCE_VISION_BASE}/{binance_symbol}/{timeframe}/{binance_symbol}-{timeframe}-{month_str}.zip"

    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code == 404:
            logger.debug(f"No data for {month_str}")
            return None
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning(f"Failed to download {month_str}: {e}")
        return None

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        csv_name = zf.namelist()[0]
        with zf.open(csv_name) as f:
            df = pd.read_csv(f, header=None)

    # Binance kline CSV columns:
    # 0=open_time, 1=open, 2=high, 3=low, 4=close, 5=volume,
    # 6=close_time, 7=quote_volume, 8=trades, 9=taker_buy_base, 10=taker_buy_quote, 11=ignore
    df = df.iloc[:, :6]
    df.columns = ["timestamp_raw", "open", "high", "low", "close", "volume"]
    # Binance switched from milliseconds (13 digits) to microseconds (16 digits) in 2025
    sample = int(df["timestamp_raw"].iloc[0])
    if len(str(sample)) > 13:
        df["timestamp"] = pd.to_datetime(df["timestamp_raw"], unit="us", utc=True)
    else:
        df["timestamp"] = pd.to_datetime(df["timestamp_raw"], unit="ms", utc=True)
    df = df.drop(columns=["timestamp_raw"])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)

    logger.info(f"Downloaded {len(df)} bars for {month_str}")
    return df


def download_history(
    symbol: str = "BTC/EUR",
    timeframe: str = "4h",
    start: str = "2020-01-01",
    end: str | None = None,
) -> pd.DataFrame:
    """Download full history and save to parquet cache."""
    binance_symbol = SYMBOL_MAP.get(symbol)
    if not binance_symbol:
        logger.error(f"Unknown symbol: {symbol}. Available: {list(SYMBOL_MAP.keys())}")
        return pd.DataFrame()

    tf = TIMEFRAME_MAP.get(timeframe, timeframe)
    start_date = datetime.strptime(start, "%Y-%m-%d")
    end_date = datetime.strptime(end, "%Y-%m-%d") if end else datetime.now()

    all_frames = []
    current = start_date.replace(day=1)

    while current <= end_date:
        df = download_month(binance_symbol, tf, current.year, current.month)
        if df is not None and len(df) > 0:
            all_frames.append(df)

        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)

    if not all_frames:
        logger.warning(f"No data downloaded for {symbol}")
        return pd.DataFrame()

    combined = pd.concat(all_frames, ignore_index=True)
    combined = combined.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    # Filter to requested date range
    combined = combined[combined["timestamp"] >= pd.Timestamp(start, tz="UTC")]
    if end:
        combined = combined[combined["timestamp"] <= pd.Timestamp(end, tz="UTC")]

    save_cache(combined, symbol, timeframe)
    logger.info(f"Total: {len(combined)} bars for {symbol} {timeframe}, saved to cache")
    return combined


def main():
    parser = argparse.ArgumentParser(description="Download historical OHLCV data")
    parser.add_argument("--symbol", default="BTC/EUR", help="Trading pair (default: BTC/EUR)")
    parser.add_argument("--timeframe", default="4h", help="Timeframe (default: 4h)")
    parser.add_argument("--start", default="2020-01-01", help="Start date YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="End date YYYY-MM-DD (default: now)")
    parser.add_argument("--all-pairs", action="store_true", help="Download BTC/EUR and ETH/EUR")
    args = parser.parse_args()

    if args.all_pairs:
        for sym in ["BTC/EUR", "ETH/EUR"]:
            download_history(sym, args.timeframe, args.start, args.end)
            # Also download daily for EMA(200)
            download_history(sym, "1d", args.start, args.end)
    else:
        download_history(args.symbol, args.timeframe, args.start, args.end)


if __name__ == "__main__":
    main()
