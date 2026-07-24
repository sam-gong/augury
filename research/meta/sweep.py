"""SmaCross(20,50) 和 SmaBand(120, ±3%) 周围的参数扫描。

每个组合在两段 regime 都跑一次,记录:
  - NAV 段2 / NAV 段3
  - 各段 MDD / Calmar / Sharpe / 交易数 / 胜率

筛选标准(不是看单段最高,看跨 regime 的稳健性):
  - min(NAV2, NAV3) ≥ 2.0 (短板不能太烂)
  - 总单数 ≥ 15 (样本量门槛)
  - 段 2 + 段 3 Calmar 平均
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pandas as pd
import yfinance as yf

from augury.backtest import run
from augury.strategies import SmaBand, SmaCross


def fetch_meta() -> pd.DataFrame:
    df = yf.download("META", start="2012-05-01", auto_adjust=False, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    return df.dropna(subset=["Open", "Close"])


def backtest_two_regimes(df: pd.DataFrame, strat) -> dict:
    """Run `strat` on segment 2 (2015-01 → 2021-09) and segment 3 (2021-10 → now).
    Returns flat dict with both segments' metrics."""
    s2 = run(df["Close"].loc[:"2021-09-30"], strat,
             df["Open"].loc[:"2021-09-30"], start="2015-01-01")
    s3 = run(df["Close"], strat, df["Open"], start="2021-10-01")
    m2, m3 = s2.metrics, s3.metrics
    return {
        "nav2": m2.get("end_nav"), "nav3": m3.get("end_nav"),
        "mdd2": m2.get("max_drawdown"), "mdd3": m3.get("max_drawdown"),
        "shp2": m2.get("sharpe"), "shp3": m3.get("sharpe"),
        "cal2": m2.get("calmar"), "cal3": m3.get("calmar"),
        "wr2":  m2.get("win_rate"), "wr3":  m3.get("win_rate"),
        "tr2":  m2.get("closed_trades"), "tr3":  m3.get("closed_trades"),
        "state": s3.state,  # current state from full-data segment 3
    }


# ---------------- SmaCross sweep ----------------
CROSS_FAST = [10, 12, 15, 18, 20, 22, 25, 28]
CROSS_SLOW = [35, 40, 45, 50, 55, 60, 70]

# ---------------- SmaBand sweep -----------------
BAND_MA   = [90, 100, 110, 120, 130, 140, 150, 160, 175]
BAND_THRE = [0.00, 0.02, 0.03, 0.04, 0.05]


def fmt_nav(x): return f"{x:>5.2f}x" if x is not None else "    -"
def fmt_mdd(x): return f"{x*100:>5.1f}%" if x is not None else "    -"
def fmt_int(x): return f"{x:>4d}" if x is not None else "   -"


def grid(rows, cols, fn, row_label, col_label, fmt, title):
    """Render a 2D heat grid as text."""
    print(f"\n{title}")
    hdr = f"{row_label:>6} | " + " ".join(f"{col_label}={c:<5}" for c in cols)
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        cells = [fmt(fn(r, c)) for c in cols]
        print(f"{r:>6} | " + " ".join(f"{cell:>7}" for cell in cells))


def sweep_cross(df: pd.DataFrame):
    print("\n" + "=" * 70)
    print("SmaCross 扫描 — 围绕 (20, 50)")
    print("=" * 70)
    cache = {}
    for f in CROSS_FAST:
        for s in CROSS_SLOW:
            if f >= s:
                continue
            cache[(f, s)] = backtest_two_regimes(df, SmaCross(fast=f, slow=s))

    def get(key):
        return lambda f, s: cache[(f, s)][key] if (f, s) in cache else None

    grid(CROSS_FAST, CROSS_SLOW, get("nav2"), "fast", "slow", fmt_nav,
         "段 2 NAV (2015-01 → 2021-09, B&H=4.32x)")
    grid(CROSS_FAST, CROSS_SLOW, get("nav3"), "fast", "slow", fmt_nav,
         "段 3 NAV (2021-10 → now, B&H=1.79x)")
    grid(CROSS_FAST, CROSS_SLOW, lambda f, s: min(cache[(f,s)]["nav2"], cache[(f,s)]["nav3"]) if (f,s) in cache else None,
         "fast", "slow", fmt_nav, "min(NAV2, NAV3) — 跨 regime 短板")
    grid(CROSS_FAST, CROSS_SLOW, lambda f, s: min(cache[(f,s)]["mdd2"], cache[(f,s)]["mdd3"]) if (f,s) in cache else None,
         "fast", "slow", fmt_mdd, "worst MDD — 两段 MDD 取最差")
    grid(CROSS_FAST, CROSS_SLOW, lambda f, s: cache[(f,s)]["tr2"] + cache[(f,s)]["tr3"] if (f,s) in cache else None,
         "fast", "slow", fmt_int, "总交易数 (段2 + 段3)")

    print("\nTop 10 综合(min(NAV2, NAV3) 降序,要求总单数 ≥ 15)")
    print(f"{'fast':>5} {'slow':>5} | {'NAV2':>7} {'NAV3':>7} {'minNAV':>7} | {'MDD2':>7} {'MDD3':>7} | {'Cal2':>5} {'Cal3':>5} | {'单数':>5} | 当前")
    print("-" * 85)
    rows = []
    for (f, s), m in cache.items():
        total = (m["tr2"] or 0) + (m["tr3"] or 0)
        if total < 15:
            continue
        rows.append((min(m["nav2"], m["nav3"]), f, s, m))
    rows.sort(reverse=True)
    for _, f, s, m in rows[:10]:
        print(f"{f:>5} {s:>5} | {fmt_nav(m['nav2']):>7} {fmt_nav(m['nav3']):>7} "
              f"{fmt_nav(min(m['nav2'], m['nav3'])):>7} | "
              f"{fmt_mdd(m['mdd2']):>7} {fmt_mdd(m['mdd3']):>7} | "
              f"{m['cal2']:>5.2f} {m['cal3']:>5.2f} | "
              f"{m['tr2'] + m['tr3']:>5} | {m['state']}")


def sweep_band(df: pd.DataFrame):
    print("\n" + "=" * 70)
    print("SmaBand 扫描 — 围绕 (120, ±3%)")
    print("=" * 70)
    cache = {}
    for ma in BAND_MA:
        for th in BAND_THRE:
            cache[(ma, th)] = backtest_two_regimes(df, SmaBand(ma=ma, threshold=th))

    def get(key):
        return lambda ma, th: cache[(ma, th)][key]

    th_cols = [f"{int(t*100)}%" for t in BAND_THRE]

    def render(fn, fmt, title):
        print(f"\n{title}")
        hdr = f"{'ma':>4} | " + " ".join(f"±{c:<4}" for c in th_cols)
        print(hdr)
        print("-" * len(hdr))
        for ma in BAND_MA:
            cells = [fmt(fn(ma, th)) for th in BAND_THRE]
            print(f"{ma:>4} | " + " ".join(f"{c:>6}" for c in cells))

    render(get("nav2"), fmt_nav, "段 2 NAV (2015-01 → 2021-09, B&H=4.32x)")
    render(get("nav3"), fmt_nav, "段 3 NAV (2021-10 → now, B&H=1.79x)")
    render(lambda ma, th: min(cache[(ma, th)]["nav2"], cache[(ma, th)]["nav3"]),
           fmt_nav, "min(NAV2, NAV3) — 跨 regime 短板")
    render(lambda ma, th: min(cache[(ma, th)]["mdd2"], cache[(ma, th)]["mdd3"]),
           fmt_mdd, "worst MDD — 两段 MDD 取最差")
    render(lambda ma, th: cache[(ma, th)]["tr2"] + cache[(ma, th)]["tr3"],
           fmt_int, "总交易数 (段2 + 段3)")

    print("\nTop 10 综合(min(NAV2, NAV3) 降序,要求总单数 ≥ 10)")
    print(f"{'ma':>4} {'thre':>5} | {'NAV2':>7} {'NAV3':>7} {'minNAV':>7} | {'MDD2':>7} {'MDD3':>7} | {'Cal2':>5} {'Cal3':>5} | {'WR2':>5} {'WR3':>5} | {'单数':>5} | 当前")
    print("-" * 105)
    rows = []
    for (ma, th), m in cache.items():
        total = (m["tr2"] or 0) + (m["tr3"] or 0)
        if total < 10:
            continue
        rows.append((min(m["nav2"], m["nav3"]), ma, th, m))
    rows.sort(reverse=True)
    for _, ma, th, m in rows[:10]:
        wr2 = f"{m['wr2']*100:>4.0f}%" if m['wr2'] is not None else "   -"
        wr3 = f"{m['wr3']*100:>4.0f}%" if m['wr3'] is not None else "   -"
        print(f"{ma:>4} {th*100:>4.0f}% | {fmt_nav(m['nav2']):>7} {fmt_nav(m['nav3']):>7} "
              f"{fmt_nav(min(m['nav2'], m['nav3'])):>7} | "
              f"{fmt_mdd(m['mdd2']):>7} {fmt_mdd(m['mdd3']):>7} | "
              f"{m['cal2']:>5.2f} {m['cal3']:>5.2f} | "
              f"{wr2:>5} {wr3:>5} | "
              f"{m['tr2'] + m['tr3']:>5} | {m['state']}")


if __name__ == "__main__":
    df = fetch_meta()
    print(f"\nMETA OHLC: {len(df)} 行 · {df.index[0].date()} → {df.index[-1].date()}")
    sweep_cross(df)
    sweep_band(df)
