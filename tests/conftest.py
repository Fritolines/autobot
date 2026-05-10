import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def sample_ohlcv() -> pd.DataFrame:
    """Generate a deterministic 1000-bar OHLCV DataFrame for testing."""
    np.random.seed(42)
    n = 1000

    close = np.zeros(n)
    close[0] = 70000.0

    for i in range(1, n):
        close[i] = close[i - 1] * (1 + np.random.normal(0.0002, 0.015))

    high = close * (1 + np.abs(np.random.normal(0, 0.005, n)))
    low = close * (1 - np.abs(np.random.normal(0, 0.005, n)))
    open_ = close * (1 + np.random.normal(0, 0.003, n))
    volume = np.random.uniform(50, 500, n)

    timestamps = pd.date_range("2024-01-01", periods=n, freq="4h")

    return pd.DataFrame({
        "timestamp": timestamps,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })
