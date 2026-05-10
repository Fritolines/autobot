"""
Paper trading executor — simulates order fills for dry-run mode.
"""
from __future__ import annotations

import logging
import random
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class PaperExecutor:
    def __init__(self, initial_balance: float = 500.0, maker_fee: float = 0.0025, taker_fee: float = 0.004):
        self.balance = initial_balance
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee

    def execute_entry(self, symbol: str, units: float, price: float) -> dict:
        """Simulate a market buy with slippage and fees."""
        slippage_bps = random.uniform(0, 10)
        fill_price = price * (1 + slippage_bps / 10000)
        fee = fill_price * units * self.taker_fee
        cost = fill_price * units + fee

        if cost > self.balance:
            logger.warning(f"Insufficient balance: need {cost:.2f}, have {self.balance:.2f}")
            return {}

        self.balance -= cost
        client_order_id = f"paper-{symbol.replace('/', '')}-{uuid.uuid4().hex[:8]}"

        order = {
            "client_order_id": client_order_id,
            "symbol": symbol,
            "side": "buy",
            "order_type": "market",
            "price": round(fill_price, 2),
            "units": units,
            "fees": round(fee, 4),
            "status": "FILLED",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        logger.info(f"PAPER BUY {symbol}: {units} @ {fill_price:.2f} (fee: {fee:.4f})")
        return order

    def execute_exit(self, symbol: str, units: float, price: float, reason: str) -> dict:
        """Simulate a market sell."""
        slippage_bps = random.uniform(0, 10)
        fill_price = price * (1 - slippage_bps / 10000)
        fee = fill_price * units * self.taker_fee
        proceeds = fill_price * units - fee

        self.balance += proceeds
        client_order_id = f"paper-{symbol.replace('/', '')}-{uuid.uuid4().hex[:8]}"

        order = {
            "client_order_id": client_order_id,
            "symbol": symbol,
            "side": "sell",
            "order_type": "market",
            "price": round(fill_price, 2),
            "units": units,
            "fees": round(fee, 4),
            "status": "FILLED",
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        logger.info(f"PAPER SELL {symbol}: {units} @ {fill_price:.2f} ({reason}, fee: {fee:.4f})")
        return order
