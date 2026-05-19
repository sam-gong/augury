"""Hybrid strategy — primary asset + substitute basket.

When the base strategy says "exit primary", capital rotates into a fixed
weighted basket of substitutes (held buy-and-hold) instead of going to cash.
When it says "re-enter primary", the basket is liquidated.

Wrap any plain `Strategy` (entries/exits-based) to turn it into a hybrid:

    HybridStrategy(base=SmaCross(5, 30), substitutes={"AZO": 0.5, "ORLY": 0.5})

Signals/overlays/reference all delegate to the base — the price chart still
draws the base strategy's MA lines and entry/exit markers (those are
the primary asset's events). The hybrid-ness lives in the backtest engine,
which sees the `substitutes` attribute and uses `run_hybrid()` instead of
`run()`."""
from __future__ import annotations
from dataclasses import dataclass, field
import pandas as pd

from ._base import Strategy, OverlayLine


@dataclass
class HybridStrategy(Strategy):
    base: Strategy = None
    # ticker -> weight (must sum to 1)
    substitutes: dict[str, float] = field(default_factory=dict)
    name: str = "Hybrid"

    def __post_init__(self):
        if self.base is None or not self.substitutes:
            raise ValueError("HybridStrategy needs both base and substitutes")
        sub_tickers = list(self.substitutes.keys())
        wts = list(self.substitutes.values())
        balanced = len(set(round(w, 6) for w in wts)) == 1
        joined = " / ".join(sub_tickers)
        if balanced:
            tail = f" · 空仓时均匀持有 {joined}"
        else:
            tail = " · 空仓时持有 " + ", ".join(
                f"{t} {int(w*100)}%" for t, w in self.substitutes.items())
        self.spec = self.base.spec + tail

    def label(self) -> str:
        return f"{self.base.label()} + {'/'.join(self.substitutes.keys())} 替补"

    def signals(self, close: pd.Series) -> tuple[pd.Series, pd.Series]:
        return self.base.signals(close)

    def overlay(self, close: pd.Series) -> list[OverlayLine]:
        return self.base.overlay(close)

    def reference(self, close: pd.Series) -> dict:
        return self.base.reference(close)
