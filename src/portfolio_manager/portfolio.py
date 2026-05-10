"""
Portfolio manager — persists orders, positions, equity, and trade journal to SQLite.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from src.database.db import (
    insert_order,
    insert_trade,
    upsert_position,
    delete_position,
    insert_equity_snapshot,
    fetch_positions,
    fetch_equity_history,
)

logger = logging.getLogger(__name__)


def record_entry(order: dict, stop_price: float):
    """Persist a filled entry order as both an order and an open position."""
    insert_order(order)
    upsert_position({
        "symbol": order["symbol"],
        "side": "long",
        "entry_price": order["price"],
        "units": order["units"],
        "entry_time": order["timestamp"],
        "stop_price": stop_price,
        "highest_high_since_entry": order["price"],
        "bars_in_trade": 0,
        "client_order_id": order["client_order_id"],
    })
    logger.info(f"Recorded entry: {order['symbol']} {order['units']} @ {order['price']}")


def record_exit(order: dict, position: dict):
    """Close a position and record the trade."""
    insert_order(order)

    pnl = (order["price"] - position["entry_price"]) * position["units"]
    stop_distance = abs(position["entry_price"] - position["stop_price"])
    risk = stop_distance * position["units"]
    r_multiple = pnl / risk if risk > 0 else 0

    insert_trade({
        "symbol": order["symbol"],
        "side": "long",
        "entry_time": position["entry_time"],
        "exit_time": order["timestamp"],
        "entry_price": position["entry_price"],
        "exit_price": order["price"],
        "units": position["units"],
        "pnl": round(pnl, 2),
        "r_multiple": round(r_multiple, 2),
        "exit_reason": order.get("reason", "unknown"),
        "fees": order.get("fees", 0),
    })

    delete_position(order["symbol"])
    logger.info(f"Recorded exit: {order['symbol']} PnL={pnl:.2f} ({r_multiple:.1f}R)")
    return pnl, r_multiple


def update_position_bar(symbol: str, current_high: float, current_price: float):
    """Update bars_in_trade and highest_high for an open position."""
    positions = fetch_positions()
    for p in positions:
        if p["symbol"] == symbol:
            new_highest = max(p["highest_high_since_entry"], current_high)
            upsert_position({
                **p,
                "highest_high_since_entry": new_highest,
                "bars_in_trade": p["bars_in_trade"] + 1,
            })
            break


def record_equity(equity: float, peak_equity: float):
    """Record an equity snapshot."""
    now = datetime.now(timezone.utc).isoformat()
    dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0
    insert_equity_snapshot(now, round(equity, 2), round(peak_equity, 2), round(dd, 4))


def get_open_positions() -> list[dict]:
    return fetch_positions()
