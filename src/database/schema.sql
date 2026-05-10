CREATE TABLE IF NOT EXISTS orders (
    client_order_id TEXT PRIMARY KEY,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    order_type      TEXT NOT NULL,
    price           REAL,
    units           REAL NOT NULL,
    status          TEXT NOT NULL DEFAULT 'PENDING',
    exchange_order_id TEXT,
    fees            REAL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL DEFAULT 'long',
    entry_time      TEXT NOT NULL,
    exit_time       TEXT NOT NULL,
    entry_price     REAL NOT NULL,
    exit_price      REAL NOT NULL,
    units           REAL NOT NULL,
    pnl             REAL NOT NULL,
    r_multiple      REAL,
    exit_reason     TEXT,
    fees            REAL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS equity_snapshots (
    timestamp       TEXT PRIMARY KEY,
    equity          REAL NOT NULL,
    peak_equity     REAL NOT NULL,
    drawdown_pct    REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS positions (
    symbol                  TEXT PRIMARY KEY,
    side                    TEXT NOT NULL DEFAULT 'long',
    entry_price             REAL NOT NULL,
    units                   REAL NOT NULL,
    entry_time              TEXT NOT NULL,
    stop_price              REAL NOT NULL,
    highest_high_since_entry REAL NOT NULL,
    bars_in_trade           INTEGER NOT NULL DEFAULT 0,
    client_order_id         TEXT,
    updated_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS circuit_breaker_state (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    daily_pnl_pct       REAL NOT NULL DEFAULT 0,
    consecutive_losses  INTEGER NOT NULL DEFAULT 0,
    drawdown_pct        REAL NOT NULL DEFAULT 0,
    peak_equity         REAL NOT NULL DEFAULT 500,
    soft_pause          INTEGER NOT NULL DEFAULT 0,
    hard_kill           INTEGER NOT NULL DEFAULT 0,
    paused_until        TEXT,
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS lots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT NOT NULL,
    buy_time        TEXT NOT NULL,
    buy_price       REAL NOT NULL,
    units           REAL NOT NULL,
    remaining_units REAL NOT NULL,
    fees            REAL DEFAULT 0
);

-- Seed circuit breaker row
INSERT OR IGNORE INTO circuit_breaker_state (id, peak_equity) VALUES (1, 500);
