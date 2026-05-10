"""
Event-driven backtester. Uses the same indicator/signal/risk logic as the live bot.
Iterates bar-by-bar, tracks equity curve, computes performance metrics.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from src.indicator_engine.indicators import donchian, atr, adx, ema, volume_sma
from src.signal_generator.signals import compute_indicators, check_entry, check_exit
from src.risk_manager.position_sizer import compute_position_size

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "config.dryrun.json"


@dataclass
class BacktestPosition:
    symbol: str
    entry_price: float
    entry_time: datetime
    units: float
    stop_price: float
    highest_high_since_entry: float
    bars_in_trade: int = 0

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "entry_price": self.entry_price,
            "entry_time": self.entry_time.isoformat() if isinstance(self.entry_time, datetime) else str(self.entry_time),
            "units": self.units,
            "stop_price": self.stop_price,
            "highest_high_since_entry": self.highest_high_since_entry,
            "bars_in_trade": self.bars_in_trade,
        }


@dataclass
class BacktestTrade:
    symbol: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    units: float
    pnl: float
    r_multiple: float
    exit_reason: str
    fees: float


@dataclass
class BacktestResult:
    trades: list[BacktestTrade] = field(default_factory=list)
    equity_curve: list[dict] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


def run_backtest(
    df_4h: pd.DataFrame,
    df_daily: pd.DataFrame | None = None,
    config: dict | None = None,
    initial_capital: float = 500.0,
) -> BacktestResult:
    """
    Run an event-driven backtest on historical 4h data.
    """
    if config is None:
        config = json.loads(CONFIG_PATH.read_text())

    strategy = config.get("strategy", {})
    fees_cfg = config.get("fees", {})
    taker_fee = fees_cfg.get("taker_pct", 0.004)

    warmup = max(
        strategy.get("donchian_period", 20) + 1,
        strategy.get("atr_period", 14),
        strategy.get("adx_period", 14),
        strategy.get("volume_sma_period", 20),
    ) + 5

    df = compute_indicators(df_4h.copy(), config)

    if df_daily is not None and len(df_daily) > 0:
        df_daily = df_daily.copy()
        if "timestamp" in df_daily.columns:
            df_daily["timestamp"] = pd.to_datetime(df_daily["timestamp"], utc=True)

    equity = initial_capital
    peak_equity = initial_capital
    positions: dict[str, BacktestPosition] = {}
    result = BacktestResult()

    pairs = config.get("pairs", ["BTC/EUR"])
    # Determine unique symbols in the data
    if "symbol" in df.columns:
        available_symbols = df["symbol"].unique().tolist()
    else:
        available_symbols = pairs[:1]

    for i in range(warmup, len(df)):
        bar = df.iloc[i]
        history = df.iloc[max(0, i - 250) : i + 1]
        timestamp = bar.get("timestamp", i)

        # Slice daily data to prevent look-ahead bias
        daily_slice = None
        if df_daily is not None and len(df_daily) > 0 and "timestamp" in df_daily.columns:
            current_ts = pd.Timestamp(timestamp, tz="UTC") if not hasattr(timestamp, 'tz') else timestamp
            daily_slice = df_daily[df_daily["timestamp"] <= current_ts]
            if len(daily_slice) == 0:
                daily_slice = None
        elif df_daily is not None and len(df_daily) > 0:
            daily_slice = df_daily

        for symbol in available_symbols:
            pos = positions.get(symbol)

            if pos:
                # Update tracking
                pos.highest_high_since_entry = max(pos.highest_high_since_entry, bar["high"])
                pos.bars_in_trade += 1

                # Check exit
                exit_signal = check_exit(history, pos.to_dict(), config)
                if exit_signal:
                    exit_price = exit_signal.price
                    fee = exit_price * pos.units * taker_fee + pos.entry_price * pos.units * taker_fee
                    pnl = (exit_price - pos.entry_price) * pos.units - fee
                    stop_dist = abs(pos.entry_price - pos.stop_price)
                    risk = stop_dist * pos.units
                    r_mult = pnl / risk if risk > 0 else 0

                    result.trades.append(BacktestTrade(
                        symbol=symbol,
                        entry_time=str(pos.entry_time),
                        exit_time=str(timestamp),
                        entry_price=round(pos.entry_price, 2),
                        exit_price=round(exit_price, 2),
                        units=pos.units,
                        pnl=round(pnl, 2),
                        r_multiple=round(r_mult, 2),
                        exit_reason=exit_signal.reason,
                        fees=round(fee, 4),
                    ))

                    equity += pnl
                    del positions[symbol]

            else:
                # Check entry
                open_pos_list = [p.to_dict() for p in positions.values()]
                entry_signal = check_entry(
                    history, daily_slice, symbol, equity, open_pos_list, None, config,
                )
                if entry_signal:
                    units = compute_position_size(
                        equity=equity,
                        atr_value=entry_signal.atr_value,
                        risk_pct=config.get("risk_per_trade_pct", 0.01),
                        price=entry_signal.price,
                        stop_atr_mult=strategy.get("protective_stop_atr_mult", 2.0),
                    )
                    if units > 0:
                        cost = entry_signal.price * units * taker_fee
                        equity -= cost
                        positions[symbol] = BacktestPosition(
                            symbol=symbol,
                            entry_price=entry_signal.price,
                            entry_time=timestamp,
                            units=units,
                            stop_price=entry_signal.stop_price,
                            highest_high_since_entry=bar["high"],
                        )

        # Record equity snapshot
        unrealized = sum(
            (bar["close"] - p.entry_price) * p.units
            for p in positions.values()
        )
        current_equity = equity + unrealized
        peak_equity = max(peak_equity, current_equity)
        dd = (peak_equity - current_equity) / peak_equity if peak_equity > 0 else 0

        result.equity_curve.append({
            "timestamp": str(timestamp),
            "equity": round(current_equity, 2),
            "peak_equity": round(peak_equity, 2),
            "drawdown_pct": round(dd, 4),
        })

    # Compute metrics
    result.metrics = _compute_metrics(result, initial_capital)
    return result


def _compute_metrics(result: BacktestResult, initial_capital: float) -> dict:
    """Compute all performance metrics from backtest results."""
    trades = result.trades
    equity_curve = result.equity_curve

    if not trades:
        return {"total_trades": 0, "note": "No trades generated"}

    pnls = [t.pnl for t in trades]
    r_multiples = [t.r_multiple for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    final_equity = equity_curve[-1]["equity"] if equity_curve else initial_capital
    total_return = (final_equity - initial_capital) / initial_capital

    # Drawdown
    max_dd = max((e["drawdown_pct"] for e in equity_curve), default=0)

    # Sharpe (annualized for 4h bars: 6 bars/day, 365 days)
    if len(equity_curve) > 1:
        eq_series = pd.Series([e["equity"] for e in equity_curve])
        returns = eq_series.pct_change().dropna()
        bars_per_year = 6 * 365
        if returns.std() > 0:
            sharpe = (returns.mean() / returns.std()) * math.sqrt(bars_per_year)
        else:
            sharpe = 0
        # Sortino
        downside = returns[returns < 0]
        if len(downside) > 0 and downside.std() > 0:
            sortino = (returns.mean() / downside.std()) * math.sqrt(bars_per_year)
        else:
            sortino = 0
    else:
        sharpe = sortino = 0

    # Calmar
    calmar = total_return / max_dd if max_dd > 0 else 0

    # Profit factor
    gross_profit = sum(wins) if wins else 0
    gross_loss = abs(sum(losses)) if losses else 0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Max consecutive losses
    max_consec_losses = 0
    current_streak = 0
    for p in pnls:
        if p <= 0:
            current_streak += 1
            max_consec_losses = max(max_consec_losses, current_streak)
        else:
            current_streak = 0

    # Expectancy in R
    avg_r = np.mean(r_multiples) if r_multiples else 0

    # Max drawdown duration
    dd_durations = []
    current_dd_start = None
    for e in equity_curve:
        if e["drawdown_pct"] > 0.001:
            if current_dd_start is None:
                current_dd_start = e["timestamp"]
        else:
            if current_dd_start is not None:
                dd_durations.append((current_dd_start, e["timestamp"]))
                current_dd_start = None
    max_dd_bars = 0
    for start_t, end_t in dd_durations:
        bars = len([e for e in equity_curve if start_t <= e["timestamp"] <= end_t])
        max_dd_bars = max(max_dd_bars, bars)

    return {
        "total_trades": len(trades),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate": float(round(len(wins) / len(trades) * 100, 1)) if trades else 0,
        "total_return_pct": float(round(total_return * 100, 2)),
        "final_equity": float(round(final_equity, 2)),
        "total_pnl": float(round(sum(pnls), 2)),
        "avg_pnl": float(round(np.mean(pnls), 2)),
        "avg_winner": float(round(np.mean(wins), 2)) if wins else 0,
        "avg_loser": float(round(np.mean(losses), 2)) if losses else 0,
        "best_trade": float(round(max(pnls), 2)),
        "worst_trade": float(round(min(pnls), 2)),
        "profit_factor": float(round(profit_factor, 2)),
        "sharpe_ratio": float(round(sharpe, 2)),
        "sortino_ratio": float(round(sortino, 2)),
        "calmar_ratio": float(round(calmar, 2)),
        "max_drawdown_pct": float(round(max_dd * 100, 2)),
        "max_consecutive_losses": max_consec_losses,
        "max_drawdown_bars": max_dd_bars,
        "expectancy_r": float(round(avg_r, 3)),
        "avg_r_multiple": float(round(avg_r, 2)),
        "total_fees": float(round(sum(t.fees for t in trades), 2)),
    }
