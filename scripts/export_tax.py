"""
Export trade history in Portuguese Anexo-J format (FIFO) for tax reporting.
Outputs a CSV with the required columns for crypto capital gains declaration.

Usage:
    python scripts/export_tax.py --year 2025
    python scripts/export_tax.py --year 2025 --output tax_2025.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.database.db import get_connection


def fetch_trades_for_year(year: int) -> list[dict]:
    """Fetch all closed trades for a given tax year."""
    start = f"{year}-01-01T00:00:00"
    end = f"{year}-12-31T23:59:59"

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM trades WHERE exit_time >= ? AND exit_time <= ? ORDER BY exit_time",
            (start, end),
        ).fetchall()
    return [dict(r) for r in rows]


def compute_fifo_gains(trades: list[dict]) -> list[dict]:
    """
    Compute capital gains using FIFO method.
    Each trade already has entry/exit prices, so we compute:
    - Acquisition value (entry_price * units + entry_fee)
    - Disposal value (exit_price * units - exit_fee)
    - Capital gain = disposal - acquisition
    """
    results = []

    for trade in trades:
        units = trade["units"]
        entry_price = trade["entry_price"]
        exit_price = trade["exit_price"]
        fees = trade.get("fees", 0) or 0

        acquisition_value = entry_price * units + fees / 2
        disposal_value = exit_price * units - fees / 2
        capital_gain = disposal_value - acquisition_value

        entry_date = _parse_date(trade["entry_time"])
        exit_date = _parse_date(trade["exit_time"])
        holding_days = (exit_date - entry_date).days if entry_date and exit_date else 0

        results.append({
            "symbol": trade["symbol"],
            "units": units,
            "acquisition_date": entry_date.strftime("%Y-%m-%d") if entry_date else "",
            "disposal_date": exit_date.strftime("%Y-%m-%d") if exit_date else "",
            "holding_days": holding_days,
            "acquisition_value_eur": round(acquisition_value, 2),
            "disposal_value_eur": round(disposal_value, 2),
            "capital_gain_eur": round(capital_gain, 2),
            "fees_eur": round(fees, 4),
            "exit_reason": trade.get("exit_reason", ""),
        })

    return results


def _parse_date(dt_str: str | None) -> datetime | None:
    if not dt_str:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(dt_str.replace("+00:00", "").replace("Z", ""), fmt)
        except ValueError:
            continue
    return None


def export_csv(gains: list[dict], output_path: str, year: int):
    """Write gains to CSV in Anexo-J compatible format."""
    fieldnames = [
        "symbol", "units", "acquisition_date", "disposal_date",
        "holding_days", "acquisition_value_eur", "disposal_value_eur",
        "capital_gain_eur", "fees_eur", "exit_reason",
    ]

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=";")
        writer.writeheader()
        writer.writerows(gains)

    total_gain = sum(g["capital_gain_eur"] for g in gains)
    total_fees = sum(g["fees_eur"] for g in gains)
    num_trades = len(gains)
    winners = sum(1 for g in gains if g["capital_gain_eur"] > 0)

    print(f"\nAnexo-J Tax Export for {year}")
    print(f"{'='*40}")
    print(f"Total trades: {num_trades}")
    print(f"Winners: {winners} ({winners/num_trades*100:.0f}%)" if num_trades else "")
    print(f"Total capital gains: EUR {total_gain:+.2f}")
    print(f"Total fees paid: EUR {total_fees:.2f}")
    print(f"Output: {output_path}")
    print(f"\nNote: Portuguese crypto gains are taxed at 28% flat rate (IRS Category G).")
    print(f"Gains held > 365 days may be exempt under certain conditions.")


def main():
    parser = argparse.ArgumentParser(description="Export trades for Portuguese tax (Anexo-J)")
    parser.add_argument("--year", type=int, required=True, help="Tax year to export")
    parser.add_argument("--output", default=None, help="Output CSV path")
    args = parser.parse_args()

    output = args.output or f"tax_anexo_j_{args.year}.csv"

    trades = fetch_trades_for_year(args.year)
    if not trades:
        print(f"No trades found for {args.year}")
        return

    gains = compute_fifo_gains(trades)
    export_csv(gains, output, args.year)


if __name__ == "__main__":
    main()
