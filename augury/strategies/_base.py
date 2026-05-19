"""Strategy abstraction.

Each strategy maps a close-price series to a pair of bool signal series:
`(entries, exits)`. The backtest engine consumes those signals; the renderer
only sees the resulting `BacktestResult`. Neither side imports vectorbt
directly — vectorbt lives behind `augury.backtest`.

New strategy = new subclass that implements `signals()` and `overlay()`.
`overlay()` returns the lines we want to draw on the price chart (e.g. the MA
itself, and any threshold-band shading) — keeps the visual representation
attached to the strategy that defines it."""
from __future__ import annotations
from dataclasses import dataclass
import pandas as pd


@dataclass(frozen=True)
class OverlayLine:
    name: str
    series: pd.Series
    color: str = "#f59e0b"
    width: float = 1.2
    dash: str | None = None
    fill_to: str | None = None  # name of the line this one fills towards


@dataclass
class Strategy:
    """Subclasses must set `name` and `spec` (one-liner for display) and
    implement `signals(close)`, `overlay(close)`, and `reference(close)`."""
    name: str = ""
    spec: str = ""

    def prepare(self, df: pd.DataFrame) -> None:
        """Optional hook to pre-compute indicators that need OHLCV.
        Called by the renderer with the full price DataFrame before
        `signals()` runs. Default is a no-op — close-only strategies
        (SmaCross/SmaBand/...) don't need to override this."""
        return None

    def signals(self, close: pd.Series) -> tuple[pd.Series, pd.Series]:
        raise NotImplementedError

    def overlay(self, close: pd.Series) -> list[OverlayLine]:
        raise NotImplementedError

    def reference(self, close: pd.Series) -> dict:
        """Header readout for the strategy card.

        Returns a dict {label, value, pct} where `value` is the most recent
        reference scalar and `pct` is its deviation from a meaningful anchor:
          - SmaBand: close vs MA (pct = close/MA - 1)
          - SmaCross: MA(fast) vs MA(slow) (pct = fast/slow - 1, the gap
            whose sign IS the position state)"""
        raise NotImplementedError

    def label(self) -> str:
        """Short human label, e.g. 'SMA(225) ±1%'. Override per subclass."""
        return self.name
