"""Thermometer-band strategy.

The thermometer is a composite oscillator (5 base + 3 velocity oscillators,
weighted; see [augury.ta.composite][]) that reads 0-100. We trade BREAKOUTS
of named thermometer levels — not the textbook overbought/oversold reversal.
For trending assets (TSLA, BTC), the bulk of gains happen in the HIGH band
(>70), so we enter on cross-up of a high level and exit on cross-down.

Specs are expressed as 3 disjoint level lists:
  - `enter_up`   = cross-up levels that trigger entry (any one fires)
  - `exit_down`  = cross-down levels that trigger exit
  - `exit_up`    = cross-up levels that ALSO trigger exit (used to bail on
                   half-baked dip-buys: e.g. you entered at cross-up(22),
                   give up if it never reaches 50)

`BALANCED_PARAMS` is the fitted v8_refine_balanced (seed=501) thermometer:
9-asset R² = 0.9519 vs the teacher, balanced risk/reward at the trading
boundary. Lives here as the canonical thermometer; if/when we add more
flavors, move to a separate module."""
from __future__ import annotations
from dataclasses import dataclass, field
import pandas as pd

from augury import ta
from ._base import Strategy, OverlayLine


# ============================================================
# Fitted thermometer parameters (v8_refine_balanced, seed 501)
# ============================================================
# Source: research repo `runs/exp_super_search_results.json` →
# `refine_seed_501.params`. 28 fields, one set of
# (period, weight, smooth, smooth_kind [, vel_n]) per slot.
#
# All canonical candidates (v5_fitted / refine_balanced / refine_max) live in
# the research repo's `CANDIDATES.md` — use that as the source of truth when
# swapping to a different fitted thermometer:
#   /Users/sam/Fin/augury_bakup/indicators/temperature/CANDIDATES.md

BALANCED_PARAMS: dict = {
    "rsi_period": 25,
    "rsi_weight": 0.1692106268200882,
    "rsi_smooth": 7,
    "rsi_smooth_kind": "wma",
    "stoch_period": 13,
    "stoch_weight": 0.41981450380701063,
    "stoch_smooth": 2,
    "stoch_smooth_kind": "sma",
    "cci_period": 17,
    "cci_weight": 0.4751557438397894,
    "cci_smooth": 10,
    "cci_smooth_kind": "sma",
    "bb_period": 14,
    "bb_weight": 0.399026625923174,
    "bb_smooth": 1,
    "mfi_period": 17,
    "mfi_weight": 0.14902442327591164,
    "mfi_smooth": 5,
    "mfi_smooth_kind": "hma",
    "rsi_v_period": 20,
    "rsi_v_weight": 0.07549739557708529,
    "rsi_v_smooth": 7,
    "rsi_v_smooth_kind": "hma",
    "rsi_v_vel_n": 32,
    "bb_v_period": 20,
    "bb_v_weight": 0.030521012867689157,
    "bb_v_smooth": 8,
    "bb_v_smooth_kind": "dema",
    "bb_v_vel_n": 13,
    "cci_v_period": 50,
    "cci_v_weight": 0.10075722064340413,
    "cci_v_smooth": 11,
    "cci_v_smooth_kind": "wma",
    "cci_v_vel_n": 22,
    "smooth_n": 2,
    "smooth_type": "dema",
}


# ============================================================
# Strategy
# ============================================================

def _cross_up(s: pd.Series, level: float) -> pd.Series:
    return (s > level) & (s.shift(1) <= level)


def _cross_down(s: pd.Series, level: float) -> pd.Series:
    return (s < level) & (s.shift(1) >= level)


def _spec(enter_up, exit_down, exit_up) -> str:
    """Render the entry/exit rules into a one-line human description."""
    e_parts = [f"上穿 {lvl}" for lvl in enter_up]
    x_parts = [f"下穿 {lvl}" for lvl in exit_down] + \
              [f"上穿 {lvl}" for lvl in exit_up]
    return (f"温度计 {' 或 '.join(e_parts)} 做多;"
            f"{' 或 '.join(x_parts)} 平仓")


def _label(enter_up, exit_down, exit_up) -> str:
    """Compact tag, e.g. 'Thermo 80|22 / 70|22|50'."""
    e = "|".join(str(x) for x in enter_up)
    x = "|".join([str(x) for x in exit_down]
                 + [str(x) for x in exit_up])
    return f"Thermo {e} / {x}"


@dataclass
class ThermoBand(Strategy):
    enter_up: tuple[int, ...] = ()
    exit_down: tuple[int, ...] = ()
    exit_up: tuple[int, ...] = ()
    params: dict = field(default_factory=lambda: BALANCED_PARAMS)
    name: str = "ThermoBand"
    # Filled in by `prepare()` — typed as Series so a missing prepare()
    # call surfaces clearly via the AttributeError in `signals()`.
    thermo: pd.Series | None = None

    def __post_init__(self):
        if not self.enter_up:
            raise ValueError("ThermoBand needs at least one enter_up level")
        if not (self.exit_down or self.exit_up):
            raise ValueError("ThermoBand needs at least one exit level")
        self.spec = _spec(self.enter_up, self.exit_down, self.exit_up)

    def label(self) -> str:
        return _label(self.enter_up, self.exit_down, self.exit_up)

    def prepare(self, df: pd.DataFrame) -> None:
        """Pre-compute the thermometer from full OHLCV. Called by the
        renderer before signals()."""
        self.thermo = ta.composite(df, self.params)

    def signals(self, close: pd.Series) -> tuple[pd.Series, pd.Series]:
        if self.thermo is None:
            raise RuntimeError(
                "ThermoBand.signals() called before prepare(); the renderer "
                "must hand the strategy the full OHLCV DataFrame first.")
        th = self.thermo.reindex(close.index)
        e = pd.Series(False, index=close.index)
        for lvl in self.enter_up:
            e |= _cross_up(th, lvl)
        x = pd.Series(False, index=close.index)
        for lvl in self.exit_down:
            x |= _cross_down(th, lvl)
        for lvl in self.exit_up:
            x |= _cross_up(th, lvl)
        return e.fillna(False), x.fillna(False)

    def overlay(self, close: pd.Series) -> list[OverlayLine]:
        # Thermometer is on a 0-100 scale, not the price scale. Drawing it
        # on the price chart would distort the chart. Leave empty for now;
        # entry/exit markers from the backtest still appear on the price
        # chart via the renderer's trade-marker code.
        return []

    def reference(self, close: pd.Series) -> dict:
        if self.thermo is None:
            return {"label": "温度", "value": None, "pct": None}
        last = self.thermo.dropna()
        if last.empty:
            return {"label": "温度", "value": None, "pct": None}
        # Pct = deviation from neutral 50, expressed as (value-50)/50 so the
        # renderer's _fmt_pct(2) renders "+24.00%" when the thermometer is
        # at 62 — communicates "above/below neutral" intuitively.
        v = float(last.iloc[-1])
        return {"label": "温度", "value": v, "pct": (v - 50) / 50}
