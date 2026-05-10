"""
Live exchange executor — ccxt wrapper with idempotent orders, STOP_MARKET stops,
and exponential backoff retries.
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone

import ccxt

logger = logging.getLogger(__name__)

MAX_RETRIES = 5
BASE_DELAY = 1.0


def _generate_client_order_id(symbol: str) -> str:
    epoch_ms = int(time.time() * 1000)
    nonce = uuid.uuid4().hex[:6]
    clean_sym = symbol.replace("/", "").lower()
    return f"trend4h-{clean_sym}-{epoch_ms}-{nonce}"


class LiveExecutor:
    def __init__(self, exchange: ccxt.Exchange, taker_fee: float = 0.004):
        self.exchange = exchange
        self.taker_fee = taker_fee

    def execute_entry(self, symbol: str, units: float, price: float) -> dict:
        """Place a market buy order on the exchange."""
        client_order_id = _generate_client_order_id(symbol)
        order = self._retry_order(
            symbol=symbol,
            side="buy",
            order_type="market",
            units=units,
            price=None,
            client_order_id=client_order_id,
        )
        if not order:
            return {}

        fill_price = order.get("average") or order.get("price") or price
        fee_cost = order.get("fee", {}).get("cost", fill_price * units * self.taker_fee)

        return {
            "client_order_id": client_order_id,
            "exchange_order_id": order.get("id"),
            "symbol": symbol,
            "side": "buy",
            "order_type": "market",
            "price": round(float(fill_price), 2),
            "units": units,
            "fees": round(float(fee_cost), 4),
            "status": "FILLED",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def execute_exit(self, symbol: str, units: float, price: float, reason: str) -> dict:
        """Place a market sell order on the exchange."""
        client_order_id = _generate_client_order_id(symbol)
        order = self._retry_order(
            symbol=symbol,
            side="sell",
            order_type="market",
            units=units,
            price=None,
            client_order_id=client_order_id,
        )
        if not order:
            return {}

        fill_price = order.get("average") or order.get("price") or price
        fee_cost = order.get("fee", {}).get("cost", fill_price * units * self.taker_fee)

        return {
            "client_order_id": client_order_id,
            "exchange_order_id": order.get("id"),
            "symbol": symbol,
            "side": "sell",
            "order_type": "market",
            "price": round(float(fill_price), 2),
            "units": units,
            "fees": round(float(fee_cost), 4),
            "status": "FILLED",
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def place_stop_market(self, symbol: str, units: float, stop_price: float) -> dict | None:
        """Place a STOP_MARKET sell order (protective stop). Never STOP_LIMIT."""
        client_order_id = _generate_client_order_id(symbol)

        for attempt in range(MAX_RETRIES):
            try:
                order = self.exchange.create_order(
                    symbol=symbol,
                    type="stop-loss",
                    side="sell",
                    amount=units,
                    price=None,
                    params={
                        "clientOrderId": client_order_id,
                        "stopPrice": stop_price,
                        "orderType": "market",
                    },
                )
                logger.info(f"STOP_MARKET placed: {symbol} {units} @ trigger {stop_price}")
                return {
                    "client_order_id": client_order_id,
                    "exchange_order_id": order.get("id"),
                    "symbol": symbol,
                    "side": "sell",
                    "order_type": "stop_market",
                    "stop_price": stop_price,
                    "units": units,
                    "status": order.get("status", "open"),
                }
            except ccxt.RateLimitExceeded:
                delay = BASE_DELAY * (2 ** attempt)
                logger.warning(f"Rate limited on stop order, retrying in {delay:.1f}s")
                time.sleep(delay)
            except ccxt.InsufficientFunds as e:
                logger.error(f"Insufficient funds for stop: {e}")
                return None
            except ccxt.InvalidOrder as e:
                logger.error(f"Invalid stop order: {e}")
                return None
            except ccxt.NetworkError as e:
                delay = BASE_DELAY * (2 ** attempt)
                logger.warning(f"Network error on stop: {e}, retrying in {delay:.1f}s")
                time.sleep(delay)
            except ccxt.ExchangeError as e:
                logger.error(f"Exchange error on stop: {e}")
                return None

        logger.error(f"Failed to place stop after {MAX_RETRIES} retries")
        return None

    def cancel_stop(self, symbol: str, order_id: str) -> bool:
        """Cancel an existing stop order (e.g., to move the trailing stop)."""
        for attempt in range(MAX_RETRIES):
            try:
                self.exchange.cancel_order(order_id, symbol)
                logger.info(f"Cancelled stop {order_id} for {symbol}")
                return True
            except ccxt.OrderNotFound:
                logger.warning(f"Stop {order_id} already gone (filled or cancelled)")
                return True
            except ccxt.RateLimitExceeded:
                delay = BASE_DELAY * (2 ** attempt)
                time.sleep(delay)
            except ccxt.NetworkError:
                delay = BASE_DELAY * (2 ** attempt)
                time.sleep(delay)
            except ccxt.ExchangeError as e:
                logger.error(f"Failed to cancel stop {order_id}: {e}")
                return False
        return False

    def fetch_open_orders(self, symbol: str) -> list[dict]:
        """Fetch all open orders for a symbol from the exchange."""
        for attempt in range(3):
            try:
                orders = self.exchange.fetch_open_orders(symbol)
                return orders
            except ccxt.RateLimitExceeded:
                time.sleep(BASE_DELAY * (2 ** attempt))
            except Exception as e:
                logger.error(f"Failed to fetch open orders for {symbol}: {e}")
                return []
        return []

    def fetch_balance(self) -> dict:
        """Fetch account balance from exchange."""
        for attempt in range(3):
            try:
                return self.exchange.fetch_balance()
            except ccxt.RateLimitExceeded:
                time.sleep(BASE_DELAY * (2 ** attempt))
            except Exception as e:
                logger.error(f"Failed to fetch balance: {e}")
                return {}
        return {}

    def _retry_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        units: float,
        price: float | None,
        client_order_id: str,
    ) -> dict | None:
        """Execute an order with exponential backoff retries."""
        for attempt in range(MAX_RETRIES):
            try:
                params = {"clientOrderId": client_order_id}
                order = self.exchange.create_order(
                    symbol=symbol,
                    type=order_type,
                    side=side,
                    amount=units,
                    price=price,
                    params=params,
                )
                logger.info(
                    f"Order filled: {side.upper()} {symbol} {units} "
                    f"(id={order.get('id')}, client={client_order_id})"
                )
                return order

            except ccxt.RateLimitExceeded:
                delay = BASE_DELAY * (2 ** attempt)
                logger.warning(f"Rate limited, retrying in {delay:.1f}s (attempt {attempt+1})")
                time.sleep(delay)

            except ccxt.InsufficientFunds as e:
                logger.error(f"Insufficient funds: {e}")
                return None

            except ccxt.InvalidOrder as e:
                logger.error(f"Invalid order: {e}")
                return None

            except ccxt.NetworkError as e:
                delay = BASE_DELAY * (2 ** attempt)
                logger.warning(f"Network error: {e}, retrying in {delay:.1f}s")
                time.sleep(delay)

            except ccxt.ExchangeError as e:
                if "duplicate" in str(e).lower() or "already" in str(e).lower():
                    logger.info(f"Order {client_order_id} already exists (idempotent)")
                    try:
                        orders = self.exchange.fetch_orders(symbol, limit=5)
                        for o in orders:
                            if o.get("clientOrderId") == client_order_id:
                                return o
                    except Exception:
                        pass
                    return None
                logger.error(f"Exchange error: {e}")
                return None

        logger.error(f"Order failed after {MAX_RETRIES} retries: {side} {symbol} {units}")
        return None


def reconcile_positions(exchange: ccxt.Exchange, db_positions: list[dict], pairs: list[str]) -> list[str]:
    """
    Startup reconciliation: compare DB state to exchange state.
    Returns list of warnings/actions taken.
    """
    warnings = []

    try:
        balance = exchange.fetch_balance()
    except Exception as e:
        warnings.append(f"Cannot fetch balance for reconciliation: {e}")
        return warnings

    for symbol in pairs:
        base = symbol.split("/")[0]
        exchange_amount = float(balance.get(base, {}).get("total", 0) or 0)
        db_pos = next((p for p in db_positions if p["symbol"] == symbol), None)
        db_units = db_pos["units"] if db_pos else 0

        if abs(exchange_amount - db_units) > 1e-8:
            if db_pos and exchange_amount < 1e-8:
                warnings.append(
                    f"MISMATCH {symbol}: DB has {db_units} units but exchange has 0. "
                    f"Position may have been stopped out externally."
                )
            elif not db_pos and exchange_amount > 1e-8:
                warnings.append(
                    f"MISMATCH {symbol}: Exchange has {exchange_amount} {base} but DB has no position. "
                    f"Manual intervention needed."
                )
            elif db_pos and abs(exchange_amount - db_units) / max(db_units, 1e-8) > 0.01:
                warnings.append(
                    f"MISMATCH {symbol}: DB={db_units}, Exchange={exchange_amount}. "
                    f"Partial fill or external trade detected."
                )

    for symbol in pairs:
        try:
            open_orders = exchange.fetch_open_orders(symbol)
            stop_orders = [o for o in open_orders if "stop" in (o.get("type", "") or "").lower()]
            db_pos = next((p for p in db_positions if p["symbol"] == symbol), None)

            if db_pos and not stop_orders:
                warnings.append(
                    f"WARNING {symbol}: Position exists but no stop order found on exchange."
                )
            elif not db_pos and stop_orders:
                warnings.append(
                    f"ORPHAN STOP {symbol}: No position but {len(stop_orders)} stop(s) on exchange."
                )
        except Exception as e:
            warnings.append(f"Cannot check open orders for {symbol}: {e}")

    return warnings
