"""
Pure indicator functions. No I/O, no state, no side effects.
All operate on pandas Series/DataFrames and return new Series.
All use shift(1) where needed to prevent look-ahead bias.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def donchian(high: pd.Series, low: pd.Series, period: int = 20) -> tuple[pd.Series, pd.Series]:
    """
    Donchian Channel: highest high / lowest low over prior `period` bars.
    Uses shift(1) so the channel is based on completed bars only (no look-ahead).
    """
    upper = high.shift(1).rolling(window=period, min_periods=period).max()
    lower = low.shift(1).rolling(window=period, min_periods=period).min()
    return upper, lower


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """
    Average True Range with Wilder smoothing (EMA with alpha = 1/period).
    """
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    # Wilder smoothing: EMA with alpha = 1/period
    result = tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    return result


def adx(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    Average Directional Index with Wilder smoothing.
    Returns (ADX, +DI, -DI).
    """
    prev_high = high.shift(1)
    prev_low = low.shift(1)

    plus_dm = high - prev_high
    minus_dm = prev_low - low

    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    atr_vals = atr(high, low, close, period)

    alpha = 1.0 / period
    smooth_plus_dm = plus_dm.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    smooth_minus_dm = minus_dm.ewm(alpha=alpha, min_periods=period, adjust=False).mean()

    plus_di = 100.0 * smooth_plus_dm / atr_vals
    minus_di = 100.0 * smooth_minus_dm / atr_vals

    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    dx = dx.replace([np.inf, -np.inf], 0.0).fillna(0.0)

    adx_vals = dx.ewm(alpha=alpha, min_periods=period, adjust=False).mean()

    return adx_vals, plus_di, minus_di


def ema(close: pd.Series, period: int = 200) -> pd.Series:
    """Exponential Moving Average."""
    return close.ewm(span=period, min_periods=period, adjust=False).mean()


def volume_sma(volume: pd.Series, period: int = 20) -> pd.Series:
    """Simple Moving Average of volume."""
    return volume.rolling(window=period, min_periods=period).mean()
