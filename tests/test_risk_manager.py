import pytest

from src.risk_manager.position_sizer import compute_position_size, compute_stop_distance
from src.risk_manager.portfolio_heat import check_portfolio_heat


class TestPositionSizer:
    def test_btc_example_from_plan(self):
        """Plan example: €500 equity, BTC at €74,000, ATR(14)=€3,600."""
        units = compute_position_size(
            equity=500, atr_value=3600, risk_pct=0.01, price=74000,
            lot_size=0.00001, min_notional=5.0, stop_atr_mult=2.0,
        )
        # risk = 500*0.01 = 5 EUR, stop = 2*3600 = 7200, units = 5/7200 = 0.000694
        assert units > 0
        # Verify risk constraint: units * stop_distance <= risk_eur
        stop_d = compute_stop_distance(3600, 2.0)
        assert units * stop_d <= 500 * 0.01 + 0.01  # small epsilon

    def test_eth_example_from_plan(self):
        """Plan example: €500 equity, ETH at €3,200, ATR(14)=€220."""
        units = compute_position_size(
            equity=500, atr_value=220, risk_pct=0.01, price=3200,
            lot_size=0.0001, min_notional=5.0, stop_atr_mult=2.0,
        )
        # risk = 5 EUR, stop = 440, units = 5/440 = 0.01136
        assert units > 0
        stop_d = compute_stop_distance(220, 2.0)
        assert units * stop_d <= 500 * 0.01 + 0.01

    def test_below_minimum_notional(self):
        """If calculated notional is below exchange minimum, return 0."""
        units = compute_position_size(
            equity=10, atr_value=5000, risk_pct=0.01, price=74000,
            lot_size=0.00001, min_notional=5.0, stop_atr_mult=2.0,
        )
        # risk = 0.10, stop = 10000, units = 0.00001 -> notional = 0.74 < 5
        assert units == 0.0

    def test_zero_atr(self):
        units = compute_position_size(equity=500, atr_value=0, risk_pct=0.01, price=74000)
        assert units == 0.0

    def test_zero_equity(self):
        units = compute_position_size(equity=0, atr_value=3600, risk_pct=0.01, price=74000)
        assert units == 0.0

    def test_risk_invariant(self):
        """For any valid inputs, actual risk <= intended risk."""
        test_cases = [
            (500, 3600, 0.01, 74000),
            (1000, 220, 0.01, 3200),
            (500, 1000, 0.02, 50000),
            (10000, 5000, 0.005, 80000),
        ]
        for equity, atr_val, risk_pct, price in test_cases:
            units = compute_position_size(
                equity=equity, atr_value=atr_val, risk_pct=risk_pct,
                price=price, stop_atr_mult=2.0,
            )
            if units > 0:
                actual_risk = units * 2 * atr_val
                intended_risk = equity * risk_pct
                assert actual_risk <= intended_risk + 0.01


class TestStopDistance:
    def test_basic(self):
        assert compute_stop_distance(3600, 2.0) == 7200.0

    def test_trailing(self):
        assert compute_stop_distance(3600, 3.0) == 10800.0


class TestPortfolioHeat:
    def test_no_positions(self):
        assert check_portfolio_heat([], proposed_risk=5.0, equity=500) is True

    def test_within_limit(self):
        positions = [
            {"entry_price": 70000, "current_price": 71000, "stop_price": 68000, "units": 0.001},
        ]
        # current heat = |71000-68000| * 0.001 = 3.0, proposed = 5.0, total = 8.0
        # 8/500 = 1.6% < 5%
        assert check_portfolio_heat(positions, proposed_risk=5.0, equity=500) is True

    def test_exceeds_limit(self):
        positions = [
            {"entry_price": 70000, "current_price": 71000, "stop_price": 68000, "units": 0.01},
        ]
        # current heat = 3000 * 0.01 = 30, proposed = 5, total = 35
        # 35/500 = 7% > 5%
        assert check_portfolio_heat(positions, proposed_risk=5.0, equity=500) is False

    def test_zero_equity(self):
        assert check_portfolio_heat([], proposed_risk=5.0, equity=0) is False
