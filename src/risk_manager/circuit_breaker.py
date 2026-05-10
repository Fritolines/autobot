"""
Circuit breaker state machine. Persisted to SQLite.
Rules:
  - daily_pnl_pct <= -4%:   24h soft pause
  - consecutive_losses >= 6: soft pause until manual /resume
  - drawdown >= 10%:         soft pause (no new entries)
  - drawdown >= 20%:         hard kill (flatten all)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.database.db import fetch_circuit_breaker_state, update_circuit_breaker


class CircuitBreakerStateMachine:
    def __init__(self):
        self._load()

    def _load(self):
        state = fetch_circuit_breaker_state()
        if state:
            self.daily_pnl_pct = state["daily_pnl_pct"]
            self.consecutive_losses = state["consecutive_losses"]
            self.drawdown_pct = state["drawdown_pct"]
            self.peak_equity = state["peak_equity"]
            self.soft_pause = bool(state["soft_pause"])
            self.hard_kill = bool(state["hard_kill"])
            self.paused_until = state.get("paused_until")
        else:
            self.daily_pnl_pct = 0.0
            self.consecutive_losses = 0
            self.drawdown_pct = 0.0
            self.peak_equity = 500.0
            self.soft_pause = False
            self.hard_kill = False
            self.paused_until = None

    def update(
        self,
        equity: float,
        daily_pnl_pct: float,
        last_trade_won: bool | None = None,
        config: dict | None = None,
    ):
        cb = config or {}
        daily_limit = cb.get("daily_pnl_pct_limit", -0.04)
        loss_limit = cb.get("consecutive_loss_limit", 6)
        soft_pct = cb.get("drawdown_soft_pause_pct", 0.10)
        hard_pct = cb.get("drawdown_hard_kill_pct", 0.20)

        self.daily_pnl_pct = daily_pnl_pct

        if last_trade_won is not None:
            if last_trade_won:
                self.consecutive_losses = 0
            else:
                self.consecutive_losses += 1

        if equity > self.peak_equity:
            self.peak_equity = equity
        self.drawdown_pct = (self.peak_equity - equity) / self.peak_equity if self.peak_equity > 0 else 0

        # Check timed pause expiry
        now = datetime.now(timezone.utc)
        if self.paused_until:
            try:
                pause_end = datetime.fromisoformat(self.paused_until)
                if now >= pause_end:
                    self.soft_pause = False
                    self.paused_until = None
            except (ValueError, TypeError):
                pass

        # Evaluate rules (most severe first)
        if self.drawdown_pct >= hard_pct:
            self.hard_kill = True
            self.soft_pause = True
        elif self.drawdown_pct >= soft_pct:
            self.soft_pause = True
        elif self.consecutive_losses >= loss_limit:
            self.soft_pause = True
        elif daily_pnl_pct <= daily_limit:
            self.soft_pause = True
            self.paused_until = (now + timedelta(hours=24)).isoformat()

        self._save()

    def resume(self):
        """Manual resume — clears soft pause but not hard kill."""
        if not self.hard_kill:
            self.soft_pause = False
            self.paused_until = None
            self._save()

    def is_entry_allowed(self) -> bool:
        return not self.soft_pause and not self.hard_kill

    def _save(self):
        update_circuit_breaker({
            "daily_pnl_pct": self.daily_pnl_pct,
            "consecutive_losses": self.consecutive_losses,
            "drawdown_pct": self.drawdown_pct,
            "peak_equity": self.peak_equity,
            "soft_pause": self.soft_pause,
            "hard_kill": self.hard_kill,
            "paused_until": self.paused_until,
        })
