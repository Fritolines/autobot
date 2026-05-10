from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent / "data" / "autobot.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def _ensure_dir():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def init_db():
    _ensure_dir()
    with get_connection() as conn:
        conn.executescript(SCHEMA_PATH.read_text())
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")


@contextmanager
def get_connection():
    _ensure_dir()
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# --- Query helpers ---

def fetch_equity_history(limit: int = 500) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM equity_snapshots ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def fetch_trades(limit: int = 50) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM trades ORDER BY exit_time DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def fetch_positions() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM positions").fetchall()
    return [dict(r) for r in rows]


def fetch_circuit_breaker_state() -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM circuit_breaker_state WHERE id = 1"
        ).fetchone()
    return dict(row) if row else None


def insert_equity_snapshot(timestamp: str, equity: float, peak_equity: float, drawdown_pct: float):
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO equity_snapshots (timestamp, equity, peak_equity, drawdown_pct) "
            "VALUES (?, ?, ?, ?)",
            (timestamp, equity, peak_equity, drawdown_pct),
        )


def insert_trade(trade: dict):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO trades (symbol, side, entry_time, exit_time, entry_price, exit_price, "
            "units, pnl, r_multiple, exit_reason, fees) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                trade["symbol"], trade.get("side", "long"), trade["entry_time"],
                trade["exit_time"], trade["entry_price"], trade["exit_price"],
                trade["units"], trade["pnl"], trade.get("r_multiple"),
                trade.get("exit_reason"), trade.get("fees", 0),
            ),
        )


def upsert_position(pos: dict):
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO positions "
            "(symbol, side, entry_price, units, entry_time, stop_price, "
            "highest_high_since_entry, bars_in_trade, client_order_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                pos["symbol"], pos.get("side", "long"), pos["entry_price"],
                pos["units"], pos["entry_time"], pos["stop_price"],
                pos["highest_high_since_entry"], pos.get("bars_in_trade", 0),
                pos.get("client_order_id"),
            ),
        )


def delete_position(symbol: str):
    with get_connection() as conn:
        conn.execute("DELETE FROM positions WHERE symbol = ?", (symbol,))


def update_circuit_breaker(state: dict):
    with get_connection() as conn:
        conn.execute(
            "UPDATE circuit_breaker_state SET daily_pnl_pct=?, consecutive_losses=?, "
            "drawdown_pct=?, peak_equity=?, soft_pause=?, hard_kill=?, paused_until=?, "
            "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id=1",
            (
                state["daily_pnl_pct"], state["consecutive_losses"],
                state["drawdown_pct"], state["peak_equity"],
                int(state.get("soft_pause", False)), int(state.get("hard_kill", False)),
                state.get("paused_until"),
            ),
        )


def insert_order(order: dict):
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO orders "
            "(client_order_id, symbol, side, order_type, price, units, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                order["client_order_id"], order["symbol"], order["side"],
                order["order_type"], order.get("price"), order["units"],
                order.get("status", "PENDING"),
            ),
        )
