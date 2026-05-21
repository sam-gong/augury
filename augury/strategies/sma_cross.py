"""Dual-MA crossover strategy.

Entry: SMA(fast) > SMA(slow)
Exit:  SMA(fast) < SMA(slow)
"""
from __future__ import annotations
from dataclasses import dataclass
import pandas as pd

from ._base import Strategy, OverlayLine


@dataclass(kw_only=True)
class SmaCross(Strategy):
    fast: int = 5
    slow: int = 30
    name: str = "SMA Cross"

    def __post_init__(self):
        self.spec = (f"MA({self.fast}) 上穿 MA({self.slow}) 做多,"
                     f"下穿平仓")

    def label(self) -> str:
        return f"SMA({self.fast}) / SMA({self.slow})"

    def signals(self, close: pd.Series) -> tuple[pd.Series, pd.Series]:
        f = close.rolling(self.fast).mean()
        s = close.rolling(self.slow).mean()
        # Cross-based, not level-based: enter on a golden cross (fast crosses
        # UP through slow), exit on a death cross — matching the spec
        # ("上穿…做多,下穿平仓"). A level test (f > s) would open a position
        # on bar 1 whenever the window starts already in a golden-cross state.
        entries = (f > s) & (f.shift(1) <= s.shift(1))
        exits = (f < s) & (f.shift(1) >= s.shift(1))
        return entries, exits

    def reference(self, close: pd.Series) -> dict:
        f = close.rolling(self.fast).mean().dropna()
        s = close.rolling(self.slow).mean().dropna()
        if f.empty or s.empty:
            return {"label": f"MA{self.fast}/MA{self.slow}", "value": None, "pct": None}
        last_f, last_s = float(f.iloc[-1]), float(s.iloc[-1])
        return {
            "label": f"MA{self.fast} vs MA{self.slow}",
            "value": last_f,
            "pct": last_f / last_s - 1,
        }

    def overlay(self, close: pd.Series) -> list[OverlayLine]:
        return [
            OverlayLine(name=f"MA{self.fast}", series=close.rolling(self.fast).mean(),
                        color="#3b82f6", width=1.2),
            OverlayLine(name=f"MA{self.slow}", series=close.rolling(self.slow).mean(),
                        color="#f59e0b", width=1.2),
        ]
