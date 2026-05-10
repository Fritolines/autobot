"""
Telegram notification service. Sends trade alerts, daily summaries, and heartbeat pings.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import aiohttp

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = bool(bot_token and chat_id)
        if not self.enabled:
            logger.warning("Telegram notifier disabled (missing token or chat_id)")

    async def send(self, text: str, parse_mode: str = "HTML") -> bool:
        if not self.enabled:
            return False

        url = TELEGRAM_API.format(token=self.bot_token)
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        return True
                    body = await resp.text()
                    logger.error(f"Telegram API error {resp.status}: {body}")
                    return False
        except asyncio.TimeoutError:
            logger.error("Telegram send timed out")
            return False
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")
            return False

    async def notify_entry(self, symbol: str, units: float, price: float, stop: float, equity: float):
        text = (
            f"<b>ENTRY {symbol}</b>\n"
            f"Units: {units}\n"
            f"Price: {price:.2f}\n"
            f"Stop: {stop:.2f}\n"
            f"Equity: {equity:.2f}"
        )
        await self.send(text)

    async def notify_exit(self, symbol: str, price: float, pnl: float, r_mult: float, reason: str):
        emoji = "✅" if pnl > 0 else "❌"
        text = (
            f"{emoji} <b>EXIT {symbol}</b>\n"
            f"Price: {price:.2f}\n"
            f"PnL: {pnl:+.2f} ({r_mult:+.1f}R)\n"
            f"Reason: {reason}"
        )
        await self.send(text)

    async def notify_circuit_breaker(self, reason: str, paused: bool):
        status = "PAUSED" if paused else "KILLED"
        text = f"⚠️ <b>Circuit Breaker: {status}</b>\n{reason}"
        await self.send(text)

    async def heartbeat(self, equity: float, open_positions: int, uptime_hours: float):
        text = (
            f"\U0001f49a <b>Heartbeat</b>\n"
            f"Equity: {equity:.2f}\n"
            f"Open positions: {open_positions}\n"
            f"Uptime: {uptime_hours:.1f}h"
        )
        await self.send(text)

    async def notify_reconciliation_warning(self, warnings: list[str]):
        if not warnings:
            return
        text = "⚠️ <b>Reconciliation Warnings</b>\n" + "\n".join(f"- {w}" for w in warnings)
        await self.send(text)

    async def notify_error(self, error: str):
        text = f"\U0001f6a8 <b>Bot Error</b>\n<code>{error[:500]}</code>"
        await self.send(text)
