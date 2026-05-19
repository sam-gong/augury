"""Pure-function technical indicators.

The thermometer composite (8 oscillators, weighted average) lives here too —
it's a TA artifact even though only `ThermoBand` consumes it today.
All implementations are Pine-compatible (matches TradingView `ta.*` where
applicable), so values agree with the upstream research repo bit-for-bit on
shared test data."""
import numpy as np
import pandas as pd


# ============================================================
# Smoothing primitives
# ============================================================

def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rma(s: pd.Series, n: int) -> pd.Series:
    """Wilder's smoothing = Pine `ta.rma`. alpha=1/n EMA after warmup."""
    return s.ewm(alpha=1.0 / n, adjust=False).mean()


def wma(s: pd.Series, n: int) -> pd.Series:
    w = np.arange(1, n + 1, dtype=float)
    w /= w.sum()
    return s.rolling(n).apply(lambda x: float(np.dot(x, w)), raw=True)


def hma(s: pd.Series, n: int) -> pd.Series:
    """Hull MA: WMA(round(sqrt(n)), 2*WMA(n/2) - WMA(n))."""
    half = max(int(n / 2), 1)
    sqrt_n = max(int(np.sqrt(n)), 1)
    a = wma(s, half)
    b = wma(s, n)
    return wma(2 * a - b, sqrt_n)


def dema(s: pd.Series, n: int) -> pd.Series:
    e1 = s.ewm(span=n, adjust=False).mean()
    e2 = e1.ewm(span=n, adjust=False).mean()
    return 2 * e1 - e2


def apply_smooth(s: pd.Series, n: int, kind: str) -> pd.Series:
    if n <= 1:
        return s
    if kind == "sma":  return sma(s, n)
    if kind == "ema":  return ema(s, n)
    if kind == "wma":  return wma(s, n)
    if kind == "hma":  return hma(s, n)
    if kind == "dema": return dema(s, n)
    raise ValueError(f"unknown smoothing kind: {kind}")


# ============================================================
# Oscillators — Pine `ta.*` compatible
# ============================================================

def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    gain = d.clip(lower=0)
    loss = (-d).clip(lower=0)
    rs = rma(gain, n) / rma(loss, n)
    return 100 - 100 / (1 + rs)


def stoch_k(close: pd.Series, high: pd.Series, low: pd.Series,
            n: int = 14) -> pd.Series:
    lo = low.rolling(n).min()
    hi = high.rolling(n).max()
    return 100 * (close - lo) / (hi - lo)


def cci(src: pd.Series, n: int = 20) -> pd.Series:
    """Pine `ta.cci(src, n)`. `src` used directly (no implicit hlc3).
    MAD uses deviations from the CURRENT window's SMA, not pointwise."""
    ma = src.rolling(n).mean()
    mad = src.rolling(n).apply(
        lambda x: float(np.mean(np.abs(x - x.mean()))), raw=True)
    return (src - ma) / (0.015 * mad)


def bb_pos(close: pd.Series, n: int = 20, mult: float = 2.0) -> pd.Series:
    """Close's position within the Bollinger Band. 0=lower, 100=upper."""
    basis = close.rolling(n).mean()
    dev = mult * close.rolling(n).std(ddof=0)
    return 100 * (close - (basis - dev)) / (2 * dev)


def mfi(high: pd.Series, low: pd.Series, close: pd.Series,
        volume: pd.Series, n: int = 14) -> pd.Series:
    tp = (high + low + close) / 3
    rmf = tp * volume
    up = tp > tp.shift()
    pos = rmf.where(up, 0.0).rolling(n).sum()
    neg = rmf.where(~up, 0.0).rolling(n).sum()
    return 100 - 100 / (1 + pos / neg)


def osc_velocity(osc: pd.Series, n_vel: int = 14) -> pd.Series:
    """MACD-style velocity: osc - SMA(osc, n_vel). Mean-zero, captures
    the speed of the oscillator's change."""
    return osc - osc.rolling(n_vel).mean()


# ============================================================
# Thermometer composite
# ============================================================
# 8 oscillators (5 base + 3 velocity) → per-row weighted average. The 3
# velocity slots are `50 + Δ * scale`, so the whole composite naturally
# centers at 50 and ranges roughly [0, 100] (overflow possible at extremes
# and intended — the rare >100 / <0 bars carry the strongest signal).
#
# `params` dict shape (one set of (period, weight, smooth, smooth_kind, vel_n)
# per slot) matches the research repo's Optuna output, so dropping in new
# parameters is just a JSON copy-paste.

# Slot type → (compute_one fn, velocity scale). Velocity slots are listed
# separately because they need a vel_n parameter on top of the period.
_BASE_SLOTS = ("rsi", "stoch", "cci", "bb", "mfi")
_VEL_SLOTS = ("rsi_v", "bb_v", "cci_v")
THERMO_SLOTS = _BASE_SLOTS + _VEL_SLOTS


def _compute_slot(slot: str, period: int, df: pd.DataFrame,
                  vel_n: int = 14) -> pd.Series:
    """Compute one normalized oscillator (~0..100, no final clamp)."""
    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]
    if slot == "rsi":   return rsi(c, period)
    if slot == "stoch": return stoch_k(c, h, l, period)
    if slot == "cci":   return (cci(c, period) + 200) / 4
    if slot == "bb":    return bb_pos(c, period)
    if slot == "mfi":   return mfi(h, l, c, v, period)
    # Velocity variants: 50 + (osc - SMA(osc, vel_n)) * scale
    if slot == "rsi_v": return 50 + osc_velocity(rsi(c, period), vel_n) * 2
    if slot == "bb_v":  return 50 + osc_velocity(bb_pos(c, period), vel_n) * 2
    if slot == "cci_v": return 50 + osc_velocity(cci(c, period), vel_n) * 0.5
    raise ValueError(f"unknown thermo slot: {slot}")


def composite(df: pd.DataFrame, params: dict) -> pd.Series:
    """Compute the thermometer composite from OHLCV `df` and a `params` dict.

    Required `df` columns: Close, High, Low, Volume (Pandas-style title-case,
    matching how `indicators.load()` returns price data).

    Per-row NaN handling: components that are NaN at row `t` (typically
    warmup bars) are excluded from that row's weighted average and the
    remaining weights are re-normalized. Means the composite is well-defined
    as soon as ANY slot has data, not only when ALL 8 do."""
    cols = {}
    weights = []
    for slot in THERMO_SLOTS:
        period = int(params[f"{slot}_period"])
        weight = float(params[f"{slot}_weight"])
        vel_n = int(params.get(f"{slot}_vel_n", 14))
        x = _compute_slot(slot, period, df, vel_n=vel_n)
        slen = int(params.get(f"{slot}_smooth", 1))
        skind = params.get(f"{slot}_smooth_kind", "sma")
        if slen > 1:
            x = apply_smooth(x, slen, skind)
        cols[slot] = x
        weights.append(weight)

    w = np.array(weights, dtype=float)
    total = w.sum()
    if total <= 0:
        return pd.Series(np.nan, index=df.index)
    w = w / total

    M = pd.DataFrame(cols).values
    weighted = M * w
    valid = ~np.isnan(weighted)
    row_w = (valid * w).sum(axis=1)
    row_sum = np.where(valid, weighted, 0).sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where(row_w > 0, row_sum / row_w, np.nan)
    out = pd.Series(out, index=df.index)

    # Optional post-composite smoothing.
    smooth_n = int(params.get("smooth_n", 1))
    smooth_type = params.get("smooth_type", "sma")
    if smooth_n > 1:
        out = apply_smooth(out, smooth_n, smooth_type)
    return out
