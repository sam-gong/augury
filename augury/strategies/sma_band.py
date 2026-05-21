"""SMA breakout-with-band strategy.

Entry: close > SMA(n) × (1 + threshold)
Exit:  close < SMA(n) × (1 − threshold)

Threshold = 0 collapses to a plain MA cross. The band creates a
neutral zone that filters chop near the average."""
from __future__ import annotations
from dataclasses import dataclass
import pandas as pd

from ._base import Strategy, OverlayLine


@dataclass(kw_only=True)
class SmaBand(Strategy):
    ma: int = 225
    threshold: float = 0.0
    name: str = "SMA Band"

    def __post_init__(self):
        t = f" ±{self.threshold:.0%}" if self.threshold > 0 else ""
        self.spec = f"close 上穿 SMA({self.ma}){t} 做多,下穿平仓"

    def label(self) -> str:
        if self.threshold > 0:
            return f"SMA({self.ma}) ±{self.threshold:.0%}"
        return f"SMA({self.ma})"

    def signals(self, close: pd.Series) -> tuple[pd.Series, pd.Series]:
        sma = close.rolling(self.ma).mean()
        upper = sma * (1 + self.threshold)
        lower = sma * (1 - self.threshold)
        # Cross-based, not level-based: enter only when close crosses UP
        # through the upper band, exit only on a cross DOWN through the lower
        # band — matching the spec ("上穿…做多,下穿平仓"). A level test
        # (close > upper) would open a position on bar 1 whenever the window
        # starts with price already above the band, which isn't a crossing.
        entries = (close > upper) & (close.shift(1) <= upper.shift(1))
        exits = (close < lower) & (close.shift(1) >= lower.shift(1))
        return entries, exits

    def reference(self, close: pd.Series) -> dict:
        sma = close.rolling(self.ma).mean().dropna()
        if sma.empty:
            return {"label": f"MA{self.ma}", "value": None, "pct": None}
        last_close = float(close.dropna().iloc[-1])
        last_sma = float(sma.iloc[-1])
        return {
            "label": f"vs MA{self.ma}",
            "value": last_sma,
            "pct": last_close / last_sma - 1,
        }

    def overlay(self, close: pd.Series) -> list[OverlayLine]:
        sma = close.rolling(self.ma).mean()
        out = [OverlayLine(name=f"MA{self.ma}", series=sma, color="#f59e0b", width=1.2)]
        if self.threshold > 0:
            upper = sma * (1 + self.threshold)
            lower = sma * (1 - self.threshold)
            # Draw band as two lines; the template uses fill_to to shade between.
            out.append(OverlayLine(name=f"MA{self.ma}×(1+{self.threshold:.0%})",
                                   series=upper, color="rgba(245,158,11,0.35)",
                                   width=0.7, dash="dot"))
            out.append(OverlayLine(name=f"MA{self.ma}×(1−{self.threshold:.0%})",
                                   series=lower, color="rgba(245,158,11,0.35)",
                                   width=0.7, dash="dot",
                                   fill_to=f"MA{self.ma}×(1+{self.threshold:.0%})"))
        return out
