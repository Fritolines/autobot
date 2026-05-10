"""
Entry and exit signal logic.
7 entry conditions (all must be true), 4 exit rules (any triggers).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from src.indicator_engine.indicators import donchian, atr, adx, ema, volume_sma


@dataclass
class EntrySignal:
    symbol: str
    price: float
    stop_price: float
    atr_value: float
    timestamp: datetime
    reasons: list[str]


@dataclass
class ExitSignal:
    symbol: str
    price: float
    reason: str
    timestamp: datetime


def compute_indicators(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Add all indicator columns to a 4h OHLCV DataFrame."""
    s = config.get("strategy", {})
    df = df.copy()
    df["dc_upper"], df["dc_lower"] = donchian(df["high"], df["low"], s.get("donchian_period", 20))
    df["atr"] = atr(df["high"], df["low"], df["close"], s.get("atr_period", 14))
    df["adx"], df["plus_di"], df["minus_di"] = adx(df["high"], df["low"], df["close"], s.get("adx_period", 14))
    df["vol_sma"] = volume_sma(df["volume"], s.get("volume_sma_period", 20))

    dc_lower_exit_period = 10
    df["dc_lower_10"] = df["low"].shift(1).rolling(window=dc_lower_exit_period, min_periods=dc_lower_exit_period).min()
    return df


def check_entry(
    df: pd.DataFrame,
    daily_df: pd.DataFrame | None,
    symbol: str,
    equity: float,
    open_positions: list[dict],
    circuit_breaker: dict | None,
    config: dict,
) -> EntrySignal | None:
    """
    Check all 7 entry conditions on the latest completed bar.
    Returns EntrySignal if all pass, None otherwise.
    """
    if len(df) < 2:
        return None

    s = config.get("strategy", {})
    cb_config = config.get("circuit_breakers", {})

    last = df.iloc[-1]
    reasons = []

    # 1. Macro trend filter: daily close > EMA(200) on daily
    if daily_df is not None and len(daily_df) > 0:
        daily_ema = ema(daily_df["close"], s.get("ema_period", 200))
        if daily_ema.iloc[-1] is not None and not pd.isna(daily_ema.iloc[-1]):
            if daily_df["close"].iloc[-1] <= daily_ema.iloc[-1]:
                return None
            reasons.append(f"EMA(200) daily={daily_ema.iloc[-1]:.0f}, close above")

    # 2. Regime filter: ADX(14) > 25
    adx_threshold = s.get("adx_threshold", 25)
    if pd.isna(last.get("adx")) or last["adx"] <= adx_threshold:
        return None
    reasons.append(f"ADX={last['adx']:.1f} > {adx_threshold}")

    # 3. Breakout trigger: close > prior 20-bar Donchian upper
    if pd.isna(last.get("dc_upper")) or last["close"] <= last["dc_upper"]:
        return None
    reasons.append(f"Breakout: close {last['close']:.0f} > DC upper {last['dc_upper']:.0f}")

    # 4. Volume confirmation
    vol_mult = s.get("volume_multiplier", 1.5)
    if pd.isna(last.get("vol_sma")) or last["volume"] <= vol_mult * last["vol_sma"]:
        return None
    reasons.append(f"Volume {last['volume']:.0f} > {vol_mult}x SMA {last['vol_sma']:.0f}")

    # 5. Portfolio heat check
    heat_max = config.get("portfolio_heat_max_pct", 0.05)
    atr_val = last["atr"]
    stop_mult = s.get("protective_stop_atr_mult", 2.0)
    stop_distance = stop_mult * atr_val
    risk_pct = config.get("risk_per_trade_pct", 0.01)
    proposed_units = (equity * risk_pct) / stop_distance if stop_distance > 0 else 0
    proposed_risk = proposed_units * stop_distance

    current_risk = sum(
        abs(p.get("current_price", p["entry_price"]) - p["stop_price"]) * p["units"]
        for p in open_positions
    )
    total_risk = current_risk + proposed_risk
    if equity > 0 and total_risk / equity > heat_max:
        return None
    reasons.append(f"Portfolio heat: {total_risk / equity * 100:.1f}% < {heat_max * 100:.0f}%")

    # 6. Circuit breaker check
    if circuit_breaker:
        if circuit_breaker.get("soft_pause") or circuit_breaker.get("hard_kill"):
            return None
    reasons.append("Circuit breakers: clear")

    # 7. No existing position on this pair
    for p in open_positions:
        if p.get("symbol") == symbol:
            return None
    reasons.append(f"No existing {symbol} position")

    stop_price = last["close"] - stop_distance

    return EntrySignal(
        symbol=symbol,
        price=last["close"],
        stop_price=stop_price,
        atr_value=atr_val,
        timestamp=last.get("timestamp", datetime.now()),
        reasons=reasons,
    )


def check_exit(
    df: pd.DataFrame,
    position: dict,
    config: dict,
) -> ExitSignal | None:
    """
    Check 4 exit rules on the latest bar. First match wins.
    """
    if len(df) < 2:
        return None

    s = config.get("strategy", {})
    last = df.iloc[-1]
    current_price = last["close"]
    entry_price = position["entry_price"]
    atr_val = last.get("atr", 0)
    timestamp = last.get("timestamp", datetime.now())

    # 1. Protective stop: close <= entry - 2*ATR
    stop_mult = s.get("protective_stop_atr_mult", 2.0)
    protective_stop = entry_price - stop_mult * atr_val
    if current_price <= protective_stop:
        return ExitSignal(symbol=position["symbol"], price=current_price,
                          reason="protective_stop", timestamp=timestamp)

    # 2. Trailing stop (chandelier): close <= highest_high - 3*ATR
    trail_mult = s.get("trailing_stop_atr_mult", 3.0)
    highest = position.get("highest_high_since_entry", entry_price)
    trailing_stop = highest - trail_mult * atr_val
    if current_price <= trailing_stop:
        return ExitSignal(symbol=position["symbol"], price=current_price,
                          reason="trailing_stop", timestamp=timestamp)

    # 3. Opposite Donchian breakdown: close < 10-bar Donchian lower
    dc_lower_10 = last.get("dc_lower_10")
    if dc_lower_10 is not None and not pd.isna(dc_lower_10) and current_price < dc_lower_10:
        return ExitSignal(symbol=position["symbol"], price=current_price,
                          reason="donchian_breakdown", timestamp=timestamp)

    # 4. Time stop: bars > 30 AND unrealized_R < 0.5 AND ADX < 20
    bars = position.get("bars_in_trade", 0)
    time_stop_bars = s.get("time_stop_bars", 30)
    time_stop_min_r = s.get("time_stop_min_r", 0.5)
    time_stop_adx = s.get("time_stop_adx_threshold", 20)

    if bars > time_stop_bars:
        risk_per_unit = stop_mult * atr_val
        unrealized_r = (current_price - entry_price) / risk_per_unit if risk_per_unit > 0 else 0
        current_adx = last.get("adx", 100)
        if unrealized_r < time_stop_min_r and current_adx < time_stop_adx:
            return ExitSignal(symbol=position["symbol"], price=current_price,
                              reason="time_stop", timestamp=timestamp)

    return None
