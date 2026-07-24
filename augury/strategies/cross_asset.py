"""Cross-asset SMA-band strategy.

Trade asset A based on a *different* asset B's close-vs-SMA breakout. The
signal asset (B) is identified by ticker; the renderer injects the price
series via `signal_close` before backtest.

Use case: BTC as a risk-on/risk-off proxy for META. BTC trades 24/7 so the
SMA is computed on BTC's NATIVE calendar (120 = 120 calendar days,
~4 months), then resampled to the trade asset's trading-day index for
crossing detection.

Why not just use a SmaBand on META directly? Because the signal source is a
different asset whose own regime (risk appetite) is what we want to track —
META's MA tells you about META's own trend, BTC's MA tells you about
broader risk appetite.
"""
from __future__ import annotations
from dataclasses import dataclass
import pandas as pd

from ._base import Strategy, OverlayLine


@dataclass(kw_only=True)
class CrossAssetSmaBand(Strategy):
    """SmaBand on `signal_ticker`'s close; trades the host asset on T+1 open.

    The renderer detects `signal_ticker` and assigns `signal_close` before
    `signals()` runs. Tests can pass `signal_close` directly."""
    signal_ticker: str = "BTC"
    signal_close: pd.Series | None = None  # injected by renderer
    ma: int = 120
    threshold: float = 0.01
    name: str = "Cross-Asset SMA Band"

    def __post_init__(self):
        t = f" ±{self.threshold:.0%}" if self.threshold > 0 else ""
        self.spec = (f"{self.signal_ticker} 上穿 SMA({self.ma}){t} 做多,"
                     f"下穿平仓(信号源:{self.signal_ticker} 24/7 历法)")

    def label(self) -> str:
        return f"{self.signal_ticker}→ME · SMA({self.ma}) ±{self.threshold:.0%}"

    def _bands(self, close_index: pd.Index):
        """Compute (signal, upper, lower) on the asset's calendar.

        SMA is computed on the signal's NATIVE 24/7 calendar (so "120" means
        120 calendar days), then forward-filled onto the asset's trading-day
        index. Crossings are detected on the asset's calendar — picking up
        weekend BTC moves at Monday open."""
        if self.signal_close is None:
            raise RuntimeError(
                f"CrossAssetSmaBand needs signal_close for {self.signal_ticker}; "
                f"renderer should inject it before backtest")
        sig_native = self.signal_close.dropna()
        sma_native = sig_native.rolling(self.ma).mean()
        upper_native = sma_native * (1 + self.threshold)
        lower_native = sma_native * (1 - self.threshold)
        signal = sig_native.reindex(close_index, method="ffill")
        upper  = upper_native.reindex(close_index, method="ffill")
        lower  = lower_native.reindex(close_index, method="ffill")
        return signal, upper, lower

    def signals(self, close: pd.Series) -> tuple[pd.Series, pd.Series]:
        signal, upper, lower = self._bands(close.index)
        entries = (signal > upper) & (signal.shift(1) <= upper.shift(1))
        exits   = (signal < lower) & (signal.shift(1) >= lower.shift(1))
        return entries, exits

    def overlay(self, close: pd.Series) -> list[OverlayLine]:
        # Overlay belongs on the SIGNAL chart (BTC), not the host (META).
        # The host price chart shows META; rendering a BTC MA on it would
        # mislead. Return empty — header readout via reference() carries
        # the relevant numbers.
        return []

    def reference(self, close: pd.Series) -> dict:
        """Header readout — shows BTC vs its MA, not META's price."""
        signal, _, _ = self._bands(close.index)
        sig_native = self.signal_close.dropna()
        sma_native = sig_native.rolling(self.ma).mean().dropna()
        if sma_native.empty:
            return {"label": f"{self.signal_ticker} MA{self.ma}",
                    "value": None, "pct": None}
        last_sig = float(sig_native.iloc[-1])
        last_sma = float(sma_native.iloc[-1])
        return {
            "label": f"{self.signal_ticker} vs MA{self.ma}",
            "value": last_sma,
            "pct": last_sig / last_sma - 1,
        }
