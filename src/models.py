from __future__ import annotations
from datetime import datetime
from enum import Enum
from pydantic import BaseModel


class BotMode(str, Enum):
    DRYRUN = "dryrun"
    LIVE = "live"
    BACKTEST = "backtest"
    STOPPED = "stopped"


class HealthStatus(str, Enum):
    OK = "ok"
    PAUSED = "paused"
    KILLED = "killed"
    STOPPED = "stopped"


class EquitySnapshot(BaseModel):
    timestamp: datetime
    equity: float
    peak_equity: float
    drawdown_pct: float


class Position(BaseModel):
    symbol: str
    side: str = "long"
    entry_price: float
    current_price: float
    units: float
    unrealized_pnl: float
    stop_price: float
    r_multiple: float
    entry_time: datetime
    bars_in_trade: int = 0


class Trade(BaseModel):
    id: int
    symbol: str
    side: str = "long"
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    units: float
    pnl: float
    r_multiple: float
    exit_reason: str
    fees: float = 0.0


class CircuitBreakerStatus(BaseModel):
    daily_pnl_pct: float = 0.0
    consecutive_losses: int = 0
    drawdown_pct: float = 0.0
    soft_pause: bool = False
    hard_kill: bool = False
    paused_until: datetime | None = None


class BotStatus(BaseModel):
    mode: BotMode = BotMode.STOPPED
    uptime_seconds: int = 0
    last_bar_time: datetime | None = None
    next_bar_time: datetime | None = None
    pairs: list[str] = ["BTC/EUR", "ETH/EUR"]
    health: HealthStatus = HealthStatus.STOPPED
    equity: float = 500.0
    daily_pnl: float = 0.0
    daily_pnl_pct: float = 0.0
    open_risk_pct: float = 0.0


class Signal(BaseModel):
    symbol: str
    timestamp: datetime
    signal_type: str
    price: float
    reason: str
    indicators: dict = {}


class ChartBar(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    donchian_upper: float | None = None
    donchian_lower: float | None = None
    ema_200: float | None = None
    adx: float | None = None
    atr: float | None = None
