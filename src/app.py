from __future__ import annotations

import asyncio
import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

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
from src.database.db import init_db, fetch_equity_history, fetch_trades, fetch_positions, fetch_circuit_breaker_state
from src.bot import TradingBot
from src.backtester import run_backtest
from src.data_handler.cache import load_cached
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
# Mock data generators — replaced by real DB queries in Phase 2+
# ---------------------------------------------------------------------------

def _mock_equity_curve() -> list[EquitySnapshot]:
    now = datetime.now(timezone.utc)
    points = []
    equity = 500.0
    peak = 500.0
    for i in range(180):
        t = now - timedelta(days=180 - i)
        change = random.gauss(0.003, 0.02) * equity
        equity = max(equity + change, 100)
        peak = max(peak, equity)
        dd = (peak - equity) / peak if peak > 0 else 0
        points.append(EquitySnapshot(
            timestamp=t, equity=round(equity, 2),
            peak_equity=round(peak, 2), drawdown_pct=round(dd, 4),
        ))
    return points


def _mock_chart_data(symbol: str) -> list[ChartBar]:
    now = datetime.now(timezone.utc)
    bars = []
    base = 74000.0 if "BTC" in symbol else 3200.0
    price = base
    ema = base
    for i in range(200):
        t = now - timedelta(hours=(200 - i) * 4)
        change_pct = random.gauss(0.0005, 0.015)
        price *= (1 + change_pct)
        o = price
        h = price * (1 + abs(random.gauss(0, 0.008)))
        l = price * (1 - abs(random.gauss(0, 0.008)))
        c = price * (1 + random.gauss(0, 0.005))
        vol = random.uniform(50, 500) if "BTC" in symbol else random.uniform(200, 3000)
        ema = ema * 0.99 + c * 0.01
        adx_val = random.uniform(15, 45)
        atr_val = (h - l) * 0.7
        bars.append(ChartBar(
            timestamp=t, open=round(o, 2), high=round(h, 2),
            low=round(l, 2), close=round(c, 2), volume=round(vol, 2),
            donchian_upper=round(c * 1.03, 2) if i > 20 else None,
            donchian_lower=round(c * 0.97, 2) if i > 20 else None,
            ema_200=round(ema, 2) if i > 50 else None,
            adx=round(adx_val, 1) if i > 14 else None,
            atr=round(atr_val, 2) if i > 14 else None,
        ))
    return bars


def _mock_positions() -> list[Position]:
    now = datetime.now(timezone.utc)
    positions = []
    if random.random() > 0.4:
        entry = 72500.0 + random.uniform(-2000, 2000)
        current = entry * (1 + random.uniform(-0.03, 0.06))
        units = 0.00139
        pnl = (current - entry) * units
        r = pnl / (entry * 0.01 * units) if units > 0 else 0
        positions.append(Position(
            symbol="BTC/EUR", side="long", entry_price=round(entry, 2),
            current_price=round(current, 2), units=units,
            unrealized_pnl=round(pnl, 2),
            stop_price=round(entry - 2800, 2),
            r_multiple=round(r, 2),
            entry_time=now - timedelta(hours=random.randint(4, 72)),
            bars_in_trade=random.randint(1, 18),
        ))
    if random.random() > 0.6:
        entry = 3100.0 + random.uniform(-200, 200)
        current = entry * (1 + random.uniform(-0.03, 0.06))
        units = 0.0227
        pnl = (current - entry) * units
        r = pnl / (entry * 0.01 * units) if units > 0 else 0
        positions.append(Position(
            symbol="ETH/EUR", side="long", entry_price=round(entry, 2),
            current_price=round(current, 2), units=units,
            unrealized_pnl=round(pnl, 2),
            stop_price=round(entry - 440, 2),
            r_multiple=round(r, 2),
            entry_time=now - timedelta(hours=random.randint(4, 48)),
            bars_in_trade=random.randint(1, 12),
        ))
    return positions


def _mock_trades() -> list[Trade]:
    now = datetime.now(timezone.utc)
    trades = []
    reasons = ["trailing_stop", "protective_stop", "donchian_breakdown", "time_stop"]
    for i in range(25):
        entry_t = now - timedelta(days=random.randint(5, 170))
        exit_t = entry_t + timedelta(hours=random.randint(8, 300))
        sym = random.choice(["BTC/EUR", "ETH/EUR"])
        if "BTC" in sym:
            entry_p = 68000 + random.uniform(-8000, 8000)
            exit_p = entry_p * (1 + random.uniform(-0.05, 0.10))
            units = round(random.uniform(0.0005, 0.002), 6)
        else:
            entry_p = 2800 + random.uniform(-500, 500)
            exit_p = entry_p * (1 + random.uniform(-0.05, 0.10))
            units = round(random.uniform(0.01, 0.04), 4)
        pnl = round((exit_p - entry_p) * units, 2)
        risk = entry_p * 0.01 * units
        r = round(pnl / risk, 2) if risk > 0 else 0
        fees = round((entry_p + exit_p) * units * 0.004, 2)
        trades.append(Trade(
            id=i + 1, symbol=sym, entry_time=entry_t, exit_time=exit_t,
            entry_price=round(entry_p, 2), exit_price=round(exit_p, 2),
            units=units, pnl=pnl, r_multiple=r,
            exit_reason=random.choice(reasons), fees=fees,
        ))
    trades.sort(key=lambda t: t.exit_time, reverse=True)
    return trades


def _mock_signals() -> list[Signal]:
    now = datetime.now(timezone.utc)
    signals = []
    types = ["entry_check", "exit_check", "regime_filter", "volume_confirm"]
    msgs = [
        "ADX(14) = 31.2 > 25 — trending regime confirmed",
        "ADX(14) = 18.4 < 25 — no trend, skipping",
        "Breakout! Close 74,250 > Donchian upper 73,800",
        "Volume 324 > 1.5x SMA(20) 198 — confirmed",
        "Volume 142 < 1.5x SMA(20) 198 — insufficient",
        "EMA(200) daily = 71,500, close 74,250 above — bullish",
        "Trailing stop updated: 73,100 → 73,450",
        "Circuit breaker: daily P&L -2.1%, within limits",
        "No breakout: close 73,400 < upper 73,800",
        "Position BTC/EUR: R = +1.4, bars = 8",
    ]
    for i in range(12):
        signals.append(Signal(
            symbol=random.choice(["BTC/EUR", "ETH/EUR"]),
            timestamp=now - timedelta(hours=i * 4),
            signal_type=random.choice(types),
            price=random.uniform(72000, 76000),
            reason=random.choice(msgs),
        ))
    return signals


_cached_equity = _mock_equity_curve()
_cached_positions = _mock_positions()
_cached_trades = _mock_trades()
_cached_signals = _mock_signals()
_cached_chart_btc = _mock_chart_data("BTC/EUR")
_cached_chart_eth = _mock_chart_data("ETH/EUR")


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

    # Try real equity from bot, fall back to mock
    eq = bot.get_equity() if bot.running else (_cached_equity[-1].equity if _cached_equity else 500)
    prev_eq = eq  # simplified
    db_equity = fetch_equity_history(2)
    if len(db_equity) >= 2:
        eq = db_equity[-1]["equity"]
        prev_eq = db_equity[-2]["equity"]

    daily_pnl = round(eq - prev_eq, 2)
    daily_pct = round(daily_pnl / prev_eq * 100, 2) if prev_eq else 0

    # Real positions risk
    db_positions = fetch_positions()
    pos_risk = sum(
        abs(p.get("entry_price", 0) - p.get("stop_price", 0)) * p.get("units", 0)
        for p in db_positions
    )
    risk_pct = round(pos_risk / eq * 100, 2) if eq else 0

    last_bar = now.replace(minute=0, second=0, microsecond=0)
    last_bar = last_bar.replace(hour=(last_bar.hour // 4) * 4)
    next_bar = last_bar + timedelta(hours=4)

    # Health status
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
        health=health, equity=eq,
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
    """Run a backtest on cached historical data."""
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
    return _cached_equity


@app.get("/api/positions")
async def get_positions() -> list[Position]:
    db_rows = fetch_positions()
    if db_rows:
        return [Position(**r) for r in db_rows]
    return _cached_positions


@app.get("/api/trades")
async def get_trades() -> list[Trade]:
    db_rows = fetch_trades()
    if db_rows:
        return [Trade(**r) for r in db_rows]
    return _cached_trades


@app.get("/api/signals")
async def get_signals() -> list[Signal]:
    return _cached_signals


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
    return CircuitBreakerStatus(
        daily_pnl_pct=-0.012, consecutive_losses=1,
        drawdown_pct=0.034, soft_pause=False, hard_kill=False,
    )


@app.get("/api/chart/{symbol_base}")
async def get_chart(symbol_base: str) -> list[ChartBar]:
    if symbol_base.upper() == "ETH":
        return _cached_chart_eth
    return _cached_chart_btc


# ---------------------------------------------------------------------------
# WebSocket for real-time updates
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _ws_clients.add(ws)
    try:
        while True:
            await asyncio.sleep(5)
            eq = _cached_equity[-1] if _cached_equity else None
            if eq:
                tick = eq.equity * (1 + random.gauss(0, 0.001))
                await ws.send_json({
                    "type": "equity_tick",
                    "equity": round(tick, 2),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
    except WebSocketDisconnect:
        _ws_clients.discard(ws)
    except Exception:
        _ws_clients.discard(ws)
