"""Pure-function technical indicators."""
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    delta = s.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    down = -delta.clip(upper=0).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / down
    return 100 - (100 / (1 + rs))
