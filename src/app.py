from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse

from src.models import (
    BotMode,
    BotStatus,
    ChartBar,
    CircuitBreakerStatus,
    EquitySnapshot,
    HealthStatus,
    Position,
    Signal,
    Trade,
)
from src.database.db import (
    init_db, fetch_equity_history, fetch_trades,
    fetch_positions, fetch_circuit_breaker_state,
)
from src.bot import TradingBot
from src.backtester import run_backtest
from src.data_handler.cache import load_cached
from src.signal_generator.signals import compute_indicators
from src.logging_config import setup_logging

setup_logging()

app = FastAPI(title="Autobot", version="0.1.0")

DASHBOARD_DIR = Path(__file__).parent.parent / "dashboard"

bot = TradingBot()
_bot_task: asyncio.Task | None = None


async def _ws_broadcast(msg: dict):
    for ws in list(_ws_clients):
        try:
            await ws.send_json(msg)
        except Exception:
            _ws_clients.discard(ws)


@app.on_event("startup")
async def startup():
    global _bot_task
    init_db()
    bot.ws_broadcast = _ws_broadcast
    _bot_task = asyncio.create_task(bot.run_loop())


@app.on_event("shutdown")
async def shutdown():
    bot.stop()
    if _bot_task:
        _bot_task.cancel()

_start_time = datetime.now(timezone.utc)
_ws_clients: set[WebSocket] = set()


# ---------------------------------------------------------------------------
# REST API endpoints
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    index = DASHBOARD_DIR / "index.html"
    return FileResponse(index, media_type="text/html")


@app.get("/api/status")
async def get_status() -> BotStatus:
    now = datetime.now(timezone.utc)
    uptime = int((now - _start_time).total_seconds())

    equity = bot.get_equity()
    prev_eq = equity
    db_equity = fetch_equity_history(2)
    if len(db_equity) >= 2:
        equity = db_equity[-1]["equity"]
        prev_eq = db_equity[-2]["equity"]
    elif len(db_equity) == 1:
        equity = db_equity[-1]["equity"]

    daily_pnl = round(equity - prev_eq, 2)
    daily_pct = round(daily_pnl / prev_eq * 100, 2) if prev_eq else 0

    db_positions = fetch_positions()
    pos_risk = sum(
        abs(p.get("entry_price", 0) - p.get("stop_price", 0)) * p.get("units", 0)
        for p in db_positions
    )
    risk_pct = round(pos_risk / equity * 100, 2) if equity else 0

    last_bar = now.replace(minute=0, second=0, microsecond=0)
    last_bar = last_bar.replace(hour=(last_bar.hour // 4) * 4)
    next_bar = last_bar + timedelta(hours=4)

    cb = fetch_circuit_breaker_state()
    health = HealthStatus.OK
    if cb and cb.get("hard_kill"):
        health = HealthStatus.KILLED
    elif cb and cb.get("soft_pause"):
        health = HealthStatus.PAUSED
    elif not bot.running:
        health = HealthStatus.STOPPED

    mode = BotMode.DRYRUN if bot.mode == "dryrun" else BotMode.LIVE if bot.mode == "live" else BotMode.STOPPED

    return BotStatus(
        mode=mode, uptime_seconds=uptime,
        last_bar_time=last_bar, next_bar_time=next_bar,
        pairs=bot.pairs,
        health=health, equity=round(equity, 2),
        daily_pnl=daily_pnl, daily_pnl_pct=daily_pct,
        open_risk_pct=risk_pct,
    )


@app.post("/api/resume")
async def resume_circuit_breaker():
    if bot.circuit_breaker:
        bot.circuit_breaker.resume()
    return {"status": "resumed"}


@app.post("/api/stop")
async def stop_bot():
    bot.stop()
    return {"status": "stopped"}


@app.post("/api/start")
async def start_bot():
    global _bot_task
    if not bot.running:
        _bot_task = asyncio.create_task(bot.run_loop())
    return {"status": "started"}


@app.post("/api/backtest")
async def run_backtest_endpoint(
    symbol: str = "BTC/EUR",
    timeframe: str = "4h",
    start: str = "2020-01-01",
    end: str = "",
):
    df_4h = load_cached(symbol, timeframe)
    if df_4h is None or len(df_4h) == 0:
        return {"error": f"No cached data for {symbol} {timeframe}. Run scripts/download_history.py first."}

    if start:
        df_4h = df_4h[df_4h["timestamp"] >= pd.Timestamp(start, tz="UTC")]
    if end:
        df_4h = df_4h[df_4h["timestamp"] <= pd.Timestamp(end, tz="UTC")]

    df_daily = load_cached(symbol, "1d")

    config = json.loads((Path(__file__).parent.parent / "config" / "config.dryrun.json").read_text())
    config["pairs"] = [symbol]

    result = await asyncio.to_thread(run_backtest, df_4h, df_daily, config)

    return {
        "metrics": result.metrics,
        "trades": [
            {
                "symbol": t.symbol, "entry_time": t.entry_time, "exit_time": t.exit_time,
                "entry_price": t.entry_price, "exit_price": t.exit_price,
                "units": t.units, "pnl": t.pnl, "r_multiple": t.r_multiple,
                "exit_reason": t.exit_reason, "fees": t.fees,
            }
            for t in result.trades
        ],
        "equity_curve": result.equity_curve,
    }


@app.get("/api/equity")
async def get_equity() -> list[EquitySnapshot]:
    db_rows = fetch_equity_history()
    if db_rows:
        return [EquitySnapshot(**r) for r in db_rows]
    return []


@app.get("/api/positions")
async def get_positions() -> list[Position]:
    db_rows = fetch_positions()
    positions = []
    for p in db_rows:
        mark = bot._mark_prices.get(p["symbol"], p["entry_price"])
        unrealized = (mark - p["entry_price"]) * p["units"]
        risk = abs(p["entry_price"] - p["stop_price"]) * p["units"]
        r_mult = unrealized / risk if risk > 0 else 0.0
        positions.append(Position(
            symbol=p["symbol"],
            side=p.get("side", "long"),
            entry_price=p["entry_price"],
            current_price=round(mark, 2),
            units=p["units"],
            unrealized_pnl=round(unrealized, 2),
            stop_price=p["stop_price"],
            r_multiple=round(r_mult, 2),
            entry_time=p["entry_time"],
            bars_in_trade=p.get("bars_in_trade", 0),
        ))
    return positions


@app.get("/api/trades")
async def get_trades() -> list[Trade]:
    db_rows = fetch_trades()
    if db_rows:
        return [Trade(**r) for r in db_rows]
    return []


@app.get("/api/signals")
async def get_signals() -> list[Signal]:
    return [Signal(**s) for s in bot._signals]


@app.get("/api/circuit-breakers")
async def get_circuit_breakers() -> CircuitBreakerStatus:
    db_state = fetch_circuit_breaker_state()
    if db_state:
        return CircuitBreakerStatus(
            daily_pnl_pct=db_state["daily_pnl_pct"],
            consecutive_losses=db_state["consecutive_losses"],
            drawdown_pct=db_state["drawdown_pct"],
            soft_pause=bool(db_state["soft_pause"]),
            hard_kill=bool(db_state["hard_kill"]),
            paused_until=db_state.get("paused_until"),
        )
    return CircuitBreakerStatus()


@app.get("/api/chart/{symbol_base}")
async def get_chart(symbol_base: str) -> list[ChartBar]:
    symbol = f"{symbol_base.upper()}/EUR"
    df = load_cached(symbol, bot.timeframe)
    if df is None or len(df) == 0:
        return []
    df = compute_indicators(df, bot.config)
    bars = []
    for _, row in df.tail(200).iterrows():
        bars.append(ChartBar(
            timestamp=row["timestamp"],
            open=round(float(row["open"]), 2),
            high=round(float(row["high"]), 2),
            low=round(float(row["low"]), 2),
            close=round(float(row["close"]), 2),
            volume=round(float(row["volume"]), 2),
            donchian_upper=round(float(row["dc_upper"]), 2) if pd.notna(row.get("dc_upper")) else None,
            donchian_lower=round(float(row["dc_lower"]), 2) if pd.notna(row.get("dc_lower")) else None,
            adx=round(float(row["adx"]), 1) if pd.notna(row.get("adx")) else None,
            atr=round(float(row["atr"]), 2) if pd.notna(row.get("atr")) else None,
        ))
    return bars


# ---------------------------------------------------------------------------
# WebSocket for real-time updates
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _ws_clients.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        _ws_clients.discard(ws)
    except Exception:
        _ws_clients.discard(ws)
