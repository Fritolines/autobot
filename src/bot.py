"""
Main bot loop. Runs as a background asyncio task inside the FastAPI server.
Polls for 4h bar closes, computes indicators, generates signals, executes trades.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from src.database.db import init_db, fetch_circuit_breaker_state, fetch_positions, fetch_trades
from src.data_handler.fetcher import get_exchange, fetch_ohlcv, fetch_daily_ohlcv
from src.data_handler.cache import load_cached, save_cache
from src.indicator_engine.indicators import ema
from src.signal_generator.signals import compute_indicators, check_entry, check_exit
from src.risk_manager.position_sizer import compute_position_size
from src.risk_manager.circuit_breaker import CircuitBreakerStateMachine
from src.order_executor.paper_executor import PaperExecutor
from src.order_executor.executor import LiveExecutor, reconcile_positions
from src.notifier.telegram import TelegramNotifier
from src.portfolio_manager.portfolio import (
    record_entry, record_exit, update_position_bar,
    record_equity, get_open_positions,
)

logger = logging.getLogger(__name__)

_ROOT_DIR = Path(__file__).parent.parent
CONFIG_DIR = _ROOT_DIR / "config"

load_dotenv(_ROOT_DIR / ".env")


def _resolve_env_vars(text: str) -> str:
    """Replace ${VAR_NAME} placeholders with environment variable values."""
    return re.sub(r"\$\{([^}]+)\}", lambda m: os.environ.get(m.group(1), ""), text)


def _load_config() -> dict:
    mode = os.environ.get("BOT_MODE", "dryrun")
    config_file = CONFIG_DIR / f"config.{mode}.json"
    if not config_file.exists():
        config_file = CONFIG_DIR / "config.dryrun.json"
    return json.loads(_resolve_env_vars(config_file.read_text()))


class TradingBot:
    def __init__(self, config: dict | None = None):
        if config is None:
            config = _load_config()
        self.config = config
        self.running = False
        self.mode = config.get("mode", "dryrun")
        self.pairs = config.get("pairs", ["BTC/EUR", "ETH/EUR"])
        self.timeframe = config.get("timeframe", "4h")
        self.exchange = None
        self.paper_executor = PaperExecutor(
            initial_balance=config.get("initial_capital", 500),
            maker_fee=config.get("fees", {}).get("maker_pct", 0.0025),
            taker_fee=config.get("fees", {}).get("taker_pct", 0.004),
        )
        self.circuit_breaker: CircuitBreakerStateMachine | None = None
        self.last_bar_time: datetime | None = None
        self.ws_broadcast = None  # set by app.py
        self._exchange_equity: float = float(config.get("initial_capital", 500))
        self._mark_prices: dict[str, float] = {}
        self._signals: list[dict] = []

        tg_cfg = config.get("telegram", {})
        self.notifier = TelegramNotifier(
            bot_token=tg_cfg.get("bot_token", ""),
            chat_id=tg_cfg.get("chat_id", ""),
        )

    def start(self):
        init_db()
        self.exchange = get_exchange(
            self.config.get("exchange", {}).get("name", "kraken"),
            self.config.get("exchange", {}),
        )
        self.circuit_breaker = CircuitBreakerStateMachine()

        if self.mode == "live":
            self.live_executor = LiveExecutor(
                self.exchange,
                taker_fee=self.config.get("fees", {}).get("taker_pct", 0.004),
            )
            self._reconcile()
            self._sync_equity()
            if self.circuit_breaker and not fetch_trades(limit=1):
                self.circuit_breaker.peak_equity = self._exchange_equity
                self.circuit_breaker.drawdown_pct = 0.0
                self.circuit_breaker.soft_pause = False
                self.circuit_breaker.hard_kill = False
                self.circuit_breaker._save()
                logger.info(f"Fresh start: peak_equity set to {self._exchange_equity:.2f}")
        else:
            self.live_executor = None

        self.running = True
        logger.info(f"Bot started in {self.mode} mode, pairs: {self.pairs}")

    def _reconcile(self):
        """Startup reconciliation: compare DB positions to exchange state."""
        db_positions = fetch_positions()
        warnings = reconcile_positions(self.exchange, db_positions, self.pairs)
        for w in warnings:
            logger.warning(f"RECONCILIATION: {w}")
        if warnings:
            asyncio.get_event_loop().create_task(
                self.notifier.notify_reconciliation_warning(warnings)
            )

    def stop(self):
        self.running = False
        logger.info("Bot stopped")

    def get_equity(self) -> float:
        if self.mode == "dryrun":
            positions = get_open_positions()
            unrealized = sum(
                (p.get("highest_high_since_entry", p["entry_price"]) - p["entry_price"]) * p["units"]
                for p in positions
            )
            return self.paper_executor.balance + unrealized
        return self._exchange_equity

    def _sync_equity(self):
        """Refresh equity from exchange: EUR cash + mark-to-market position value."""
        if not self.live_executor:
            return
        try:
            balance = self.live_executor.fetch_balance()
            eur = float(balance.get("EUR", {}).get("total", 0) or 0)
            position_value = 0.0
            for symbol in self.pairs:
                base = symbol.split("/")[0]
                amount = float(balance.get(base, {}).get("total", 0) or 0)
                if amount > 1e-6 and symbol in self._mark_prices:
                    position_value += amount * self._mark_prices[symbol]
            self._exchange_equity = eur + position_value
            logger.info(
                f"Exchange equity: {self._exchange_equity:.2f} EUR "
                f"(cash={eur:.2f}, positions={position_value:.2f})"
            )
        except Exception as e:
            logger.error(f"Failed to sync equity: {e}")

    def _add_signal(self, symbol: str, signal_type: str, price: float, reason: str):
        """Cache a signal decision for the dashboard."""
        self._signals.append({
            "symbol": symbol,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "signal_type": signal_type,
            "price": round(price, 2),
            "reason": reason,
        })
        if len(self._signals) > 200:
            self._signals = self._signals[-200:]

    async def run_loop(self):
        """Main loop: wait for bar close, then process."""
        self.start()

        while self.running:
            try:
                now = datetime.now(timezone.utc)
                next_bar = self._next_bar_close(now)
                wait_seconds = (next_bar - now).total_seconds()

                if wait_seconds > 10:
                    logger.info(f"Waiting {wait_seconds:.0f}s for next bar close at {next_bar}")
                    # In dev mode, run every 60s instead of waiting for real bar
                    actual_wait = min(wait_seconds, 60)
                    await asyncio.sleep(actual_wait)

                    if actual_wait < wait_seconds:
                        # Dev mode tick — still process but don't wait for real bar
                        await self._process_tick()
                        continue

                await self._process_tick()
                self.last_bar_time = next_bar
                # Small buffer after bar close
                await asyncio.sleep(5)

            except asyncio.CancelledError:
                logger.info("Bot loop cancelled")
                break
            except Exception as e:
                logger.error(f"Bot loop error: {e}", exc_info=True)
                await asyncio.sleep(30)

    async def _process_tick(self):
        """Fetch data, compute indicators, check signals, execute orders."""
        logger.info("Processing tick...")

        for symbol in self.pairs:
            try:
                await self._process_pair(symbol)
            except Exception as e:
                logger.error(f"Error processing {symbol}: {e}", exc_info=True)

        if self.mode == "live":
            self._sync_equity()
        equity = self.get_equity()
        peak = max(equity, self.circuit_breaker.peak_equity if self.circuit_breaker else equity)
        record_equity(equity, peak)

        # Update circuit breaker
        if self.circuit_breaker:
            # Simple daily P&L approximation
            self.circuit_breaker.update(
                equity=equity,
                daily_pnl_pct=0,  # calculated properly with full equity history
                config=self.config.get("circuit_breakers", {}),
            )

        # Broadcast to WebSocket clients
        if self.ws_broadcast:
            await self.ws_broadcast({
                "type": "equity_tick",
                "equity": round(equity, 2),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

        logger.info(f"Tick complete. Equity: {equity:.2f}")

    async def _process_pair(self, symbol: str):
        """Process a single trading pair."""
        # Fetch 4h OHLCV
        df_4h = await asyncio.to_thread(
            fetch_ohlcv, self.exchange, symbol, self.timeframe, limit=250
        )
        if len(df_4h) < 50:
            logger.warning(f"Insufficient data for {symbol}: {len(df_4h)} bars")
            return

        # Cache it
        await asyncio.to_thread(save_cache, df_4h, symbol, self.timeframe)

        # Fetch daily for EMA(200)
        daily_df = None
        try:
            daily_df = await asyncio.to_thread(
                fetch_daily_ohlcv, self.exchange, symbol, limit=250
            )
        except Exception as e:
            logger.warning(f"Failed to fetch daily data for {symbol}: {e}")

        # Compute indicators
        df_4h = compute_indicators(df_4h, self.config)

        if len(df_4h) > 0:
            self._mark_prices[symbol] = float(df_4h.iloc[-1]["close"])

        # Get current state
        positions = get_open_positions()
        cb_state = fetch_circuit_breaker_state()
        equity = self.get_equity()

        # Check for existing position on this pair
        current_position = next((p for p in positions if p["symbol"] == symbol), None)

        if current_position:
            # Update position tracking
            last_bar = df_4h.iloc[-1]
            update_position_bar(symbol, last_bar["high"], last_bar["close"])

            # Reload position after update
            positions = get_open_positions()
            current_position = next((p for p in positions if p["symbol"] == symbol), None)

            # Check exit
            exit_signal = check_exit(df_4h, current_position, self.config)
            if exit_signal:
                executor = self.live_executor if self.mode == "live" else self.paper_executor
                order = executor.execute_exit(
                    symbol, current_position["units"], exit_signal.price, exit_signal.reason
                )
                if order:
                    pnl, r_mult = record_exit(order, current_position)
                    won = pnl > 0
                    if self.circuit_breaker:
                        self.circuit_breaker.update(
                            equity=self.get_equity(),
                            daily_pnl_pct=0,
                            last_trade_won=won,
                            config=self.config.get("circuit_breakers", {}),
                        )
                    logger.info(f"EXIT {symbol}: {exit_signal.reason}, PnL={pnl:.2f} ({r_mult:.1f}R)")
                    self._add_signal(symbol, "exit", exit_signal.price,
                        f"{exit_signal.reason}, PnL={pnl:.2f} ({r_mult:.1f}R)")
                    await self.notifier.notify_exit(
                        symbol, exit_signal.price, pnl, r_mult, exit_signal.reason
                    )
        else:
            # Check entry
            entry_signal = check_entry(
                df_4h, daily_df, symbol, equity, positions, cb_state, self.config
            )
            if entry_signal:
                units = compute_position_size(
                    equity=equity,
                    atr_value=entry_signal.atr_value,
                    risk_pct=self.config.get("risk_per_trade_pct", 0.01),
                    price=entry_signal.price,
                    stop_atr_mult=self.config.get("strategy", {}).get("protective_stop_atr_mult", 2.0),
                )
                if units > 0:
                    executor = self.live_executor if self.mode == "live" else self.paper_executor
                    order = executor.execute_entry(symbol, units, entry_signal.price)
                    if order:
                        record_entry(order, entry_signal.stop_price)
                        if self.live_executor:
                            self.live_executor.place_stop_market(
                                symbol, units, entry_signal.stop_price
                            )
                        logger.info(
                            f"ENTRY {symbol}: {units} @ {entry_signal.price:.2f}, "
                            f"stop={entry_signal.stop_price:.2f}"
                        )
                        self._add_signal(symbol, "entry", entry_signal.price,
                            "; ".join(entry_signal.reasons))
                        await self.notifier.notify_entry(
                            symbol, units, entry_signal.price, entry_signal.stop_price, equity
                        )

    def _next_bar_close(self, now: datetime) -> datetime:
        """Calculate next bar close based on configured timeframe."""
        tf_hours = {"1h": 1, "4h": 4, "1d": 24}.get(self.timeframe, 4)
        current_hour = now.hour
        next_bar_hour = ((current_hour // tf_hours) + 1) * tf_hours
        if next_bar_hour >= 24:
            next_day = now.date() + timedelta(days=1)
            return datetime(next_day.year, next_day.month, next_day.day, 0, 0, 0, tzinfo=timezone.utc)
        return now.replace(hour=next_bar_hour, minute=0, second=0, microsecond=0)
