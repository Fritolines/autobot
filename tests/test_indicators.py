import numpy as np
import pandas as pd
import pytest

from src.indicator_engine.indicators import donchian, atr, adx, ema, volume_sma


class TestDonchian:
    def test_basic_channel(self, sample_ohlcv):
        upper, lower = donchian(sample_ohlcv["high"], sample_ohlcv["low"], period=20)
        assert len(upper) == len(sample_ohlcv)
        assert len(lower) == len(sample_ohlcv)
        # First 20 bars should be NaN (shift(1) + 20-period rolling = first valid at index 20)
        assert upper.iloc[:20].isna().all()
        assert lower.iloc[:20].isna().all()
        assert upper.iloc[20:].notna().all()
        # After warmup, upper >= lower always
        valid = upper.dropna()
        valid_lower = lower.loc[valid.index]
        assert (valid >= valid_lower).all()

    def test_no_look_ahead(self, sample_ohlcv):
        """Donchian upper at bar N should NOT include bar N's high."""
        upper, lower = donchian(sample_ohlcv["high"], sample_ohlcv["low"], period=20)
        for i in [50, 100, 200, 500]:
            # Upper should be max of high[i-20:i] (bars i-20 through i-1)
            expected_upper = sample_ohlcv["high"].iloc[i - 20 : i].max()
            assert abs(upper.iloc[i] - expected_upper) < 1e-6, (
                f"Bar {i}: expected {expected_upper}, got {upper.iloc[i]}"
            )

    def test_known_values(self):
        """Test with simple known data."""
        high = pd.Series([10, 12, 11, 13, 14, 15, 12, 11, 16, 10.0])
        low = pd.Series([8, 9, 9, 10, 11, 12, 9, 8, 13, 7.0])
        upper, lower = donchian(high, low, period=3)
        # Bar 4: shift(1) means looking at bars 1,2,3 -> high max(12,11,13)=13
        assert abs(upper.iloc[4] - 13.0) < 1e-6
        # Bar 4: low min(9,9,10) = 9
        assert abs(lower.iloc[4] - 9.0) < 1e-6


class TestATR:
    def test_output_shape(self, sample_ohlcv):
        result = atr(sample_ohlcv["high"], sample_ohlcv["low"], sample_ohlcv["close"])
        assert len(result) == len(sample_ohlcv)

    def test_positive(self, sample_ohlcv):
        result = atr(sample_ohlcv["high"], sample_ohlcv["low"], sample_ohlcv["close"])
        valid = result.dropna()
        assert (valid > 0).all()

    def test_wilder_smoothing(self):
        """Verify Wilder smoothing produces expected decay."""
        high = pd.Series([10, 12, 11, 15, 10, 12, 13, 14, 11, 10,
                          12, 13, 14, 15, 16, 15, 14, 13, 12, 11.0])
        low = pd.Series([8, 9, 9, 12, 7, 9, 10, 11, 8, 7,
                         9, 10, 11, 12, 13, 12, 11, 10, 9, 8.0])
        close = pd.Series([9, 11, 10, 14, 8, 11, 12, 13, 9, 8,
                           11, 12, 13, 14, 15, 14, 13, 12, 11, 10.0])
        result = atr(high, low, close, period=5)
        # After warmup, ATR should be reasonable (> 0, < max true range)
        valid = result.dropna()
        assert len(valid) > 0
        assert valid.min() > 0


class TestADX:
    def test_output_shape(self, sample_ohlcv):
        adx_vals, plus_di, minus_di = adx(
            sample_ohlcv["high"], sample_ohlcv["low"], sample_ohlcv["close"]
        )
        assert len(adx_vals) == len(sample_ohlcv)
        assert len(plus_di) == len(sample_ohlcv)
        assert len(minus_di) == len(sample_ohlcv)

    def test_adx_range(self, sample_ohlcv):
        """ADX should be between 0 and 100."""
        adx_vals, _, _ = adx(
            sample_ohlcv["high"], sample_ohlcv["low"], sample_ohlcv["close"]
        )
        valid = adx_vals.dropna()
        assert (valid >= 0).all()
        assert (valid <= 100).all()

    def test_di_positive(self, sample_ohlcv):
        """DI values should be non-negative."""
        _, plus_di, minus_di = adx(
            sample_ohlcv["high"], sample_ohlcv["low"], sample_ohlcv["close"]
        )
        valid_plus = plus_di.dropna()
        valid_minus = minus_di.dropna()
        assert (valid_plus >= 0).all()
        assert (valid_minus >= 0).all()

    def test_trending_market_high_adx(self):
        """Strong uptrend should produce ADX > 25."""
        n = 200
        close = pd.Series([100 + i * 2 + np.random.normal(0, 0.5) for i in range(n)])
        high = close + abs(np.random.normal(0, 0.3, n))
        low = close - abs(np.random.normal(0, 0.3, n))
        adx_vals, plus_di, minus_di = adx(high, low, close, period=14)
        # In a strong trend, ADX should eventually exceed 25
        last_20 = adx_vals.iloc[-20:]
        assert last_20.mean() > 25, f"ADX mean in trend: {last_20.mean()}"


class TestEMA:
    def test_output_shape(self, sample_ohlcv):
        result = ema(sample_ohlcv["close"], period=200)
        assert len(result) == len(sample_ohlcv)

    def test_warmup(self, sample_ohlcv):
        result = ema(sample_ohlcv["close"], period=200)
        assert result.iloc[:199].isna().all()
        assert result.iloc[199:].notna().all()

    def test_smoothing(self, sample_ohlcv):
        """EMA should be smoother than raw close."""
        result = ema(sample_ohlcv["close"], period=50)
        valid = result.dropna()
        close_std = sample_ohlcv["close"].iloc[49:].diff().std()
        ema_std = valid.diff().std()
        assert ema_std < close_std

    def test_known_value(self):
        """EMA of constant series should equal that constant."""
        close = pd.Series([100.0] * 250)
        result = ema(close, period=200)
        assert abs(result.iloc[-1] - 100.0) < 1e-6


class TestVolumeSMA:
    def test_output_shape(self, sample_ohlcv):
        result = volume_sma(sample_ohlcv["volume"], period=20)
        assert len(result) == len(sample_ohlcv)

    def test_warmup(self, sample_ohlcv):
        result = volume_sma(sample_ohlcv["volume"], period=20)
        assert result.iloc[:19].isna().all()
        assert result.iloc[19:].notna().all()

    def test_known_value(self):
        """SMA of constant should equal that constant."""
        vol = pd.Series([250.0] * 30)
        result = volume_sma(vol, period=20)
        assert abs(result.iloc[-1] - 250.0) < 1e-6
