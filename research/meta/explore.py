"""META 均线策略探索

为什么挑这些参数:
  - SmaBand(200): 经典长线趋势过滤,机构常用 200 日线作牛熊分界
  - SmaBand(150) / SmaBand(225): 200 上下扫一下,看灵敏度
  - SmaBand(200, ±3%) / (200, ±5%): 加缓冲带过滤毛刺
  - SmaCross(50, 200): "黄金/死亡交叉",最经典的双均线
  - SmaCross(20, 50) / (5, 30): 短中期 momentum

回测口径(沿用 augury.backtest 的默认):
  - T+1 开盘成交
  - 单边手续费 3bps,滑点 0
  - 起始资金 100
  - 信号在全样本上计算(rolling 预热),回测窗口可裁剪
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
    """META 2012-05-18 IPO 起的全部 OHLC。"""
    df = yf.download("META", start="2012-05-01", auto_adjust=False, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    return df.dropna(subset=["Open", "Close"])


STRATEGIES = [
    SmaBand(ma=120),
    SmaBand(ma=120, threshold=0.03),
    SmaBand(ma=150),
    SmaBand(ma=200),
    SmaBand(ma=225),
    SmaBand(ma=200, threshold=0.03),
    SmaBand(ma=200, threshold=0.05),
    SmaCross(fast=50, slow=200),
    SmaCross(fast=20, slow=50),
    SmaCross(fast=5, slow=30),
]


def fmt_pct(x):  return "    -" if pd.isna(x) else f"{x*100:>+6.1f}%"
def fmt_num(x):  return "    -" if pd.isna(x) else f"{x:>6.2f}"
def fmt_x(x):    return "    -" if pd.isna(x) else f"{x:>5.2f}x"


def summarize(df: pd.DataFrame) -> None:
    close, open_ = df["Close"], df["Open"]
    bh = None
    rows = []
    for strat in STRATEGIES:
        r = run(close, strat, open_)
        m = r.metrics
        rows.append({
            "策略":    strat.label(),
            "CAGR":   m.get("cagr"),
            "Vol":    m.get("vol"),
            "MDD":    m.get("max_drawdown"),
            "Sharpe": m.get("sharpe"),
            "Sortino": m.get("sortino"),
            "Calmar": m.get("calmar"),
            "胜率":    m.get("win_rate"),
            "盈亏比":  m.get("payoff"),
            "交易数":  m.get("closed_trades"),
            "NAV":    m.get("end_nav"),
            "状态":    r.state,
        })
        if bh is None:
            bh = m  # B&H baseline is identical across strategies
    rows.append({
        "策略":    "Buy & Hold",
        "CAGR":   bh.get("bh_cagr"),
        "Vol":    bh.get("bh_vol"),
        "MDD":    bh.get("bh_max_drawdown"),
        "Sharpe": bh.get("bh_sharpe"),
        "Sortino": bh.get("bh_sortino"),
        "Calmar": bh.get("bh_calmar"),
        "胜率":    None, "盈亏比": None, "交易数": None,
        "NAV":    bh.get("bh_end_nav"),
        "状态":    "long",
    })

    hdr = f"{'策略':<22}{'CAGR':>9}{'Vol':>9}{'MDD':>9}{'Sharpe':>9}{'Sortino':>9}{'Calmar':>9}{'胜率':>8}{'盈亏比':>9}{'交易':>6}{'NAV':>9}  状态"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        nav = f"{r['NAV']:>6.2f}x" if r['NAV'] is not None else "     -"
        tr  = f"{r['交易数']:>6d}" if r['交易数'] is not None else "     -"
        wr  = fmt_pct(r['胜率']) if r['胜率'] is not None else "    -"
        pf  = fmt_x(r['盈亏比']) if r['盈亏比'] is not None else "    -"
        print(f"{r['策略']:<22}"
              f"{fmt_pct(r['CAGR']):>9}"
              f"{fmt_pct(r['Vol']):>9}"
              f"{fmt_pct(r['MDD']):>9}"
              f"{fmt_num(r['Sharpe']):>9}"
              f"{fmt_num(r['Sortino']):>9}"
              f"{fmt_num(r['Calmar']):>9}"
              f"{wr:>8}"
              f"{pf:>9}"
              f"{tr:>6}"
              f"{nav:>9}  "
              f"{r['状态']}")


def summarize_window(close: pd.Series, open_: pd.Series, start: str) -> None:
    """Backtest each strategy with `start=` filter — rolling indicators warm up
    on pre-start history (so SMA(225) gets ~1y of warm-up data from before
    `start` if you pass enough left-padding in `close`)."""
    bh = None
    rows = []
    for strat in STRATEGIES:
        r = run(close, strat, open_, start=start)
        m = r.metrics
        rows.append({
            "策略":    strat.label(),
            "CAGR":   m.get("cagr"),
            "Vol":    m.get("vol"),
            "MDD":    m.get("max_drawdown"),
            "Sharpe": m.get("sharpe"),
            "Sortino": m.get("sortino"),
            "Calmar": m.get("calmar"),
            "胜率":    m.get("win_rate"),
            "盈亏比":  m.get("payoff"),
            "交易数":  m.get("closed_trades"),
            "NAV":    m.get("end_nav"),
            "状态":    r.state,
        })
        if bh is None:
            bh = m
    rows.append({
        "策略":    "Buy & Hold",
        "CAGR":   bh.get("bh_cagr"),
        "Vol":    bh.get("bh_vol"),
        "MDD":    bh.get("bh_max_drawdown"),
        "Sharpe": bh.get("bh_sharpe"),
        "Sortino": bh.get("bh_sortino"),
        "Calmar": bh.get("bh_calmar"),
        "胜率":    None, "盈亏比": None, "交易数": None,
        "NAV":    bh.get("bh_end_nav"),
        "状态":    "long",
    })

    hdr = f"{'策略':<22}{'CAGR':>9}{'Vol':>9}{'MDD':>9}{'Sharpe':>9}{'Sortino':>9}{'Calmar':>9}{'胜率':>8}{'盈亏比':>9}{'交易':>6}{'NAV':>9}  状态"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        nav = f"{r['NAV']:>6.2f}x" if r['NAV'] is not None else "     -"
        tr  = f"{r['交易数']:>6d}" if r['交易数'] is not None else "     -"
        wr  = fmt_pct(r['胜率']) if r['胜率'] is not None else "    -"
        pf  = fmt_x(r['盈亏比']) if r['盈亏比'] is not None else "    -"
        print(f"{r['策略']:<22}"
              f"{fmt_pct(r['CAGR']):>9}"
              f"{fmt_pct(r['Vol']):>9}"
              f"{fmt_pct(r['MDD']):>9}"
              f"{fmt_num(r['Sharpe']):>9}"
              f"{fmt_num(r['Sortino']):>9}"
              f"{fmt_num(r['Calmar']):>9}"
              f"{wr:>8}"
              f"{pf:>9}"
              f"{tr:>6}"
              f"{nav:>9}  "
              f"{r['状态']}")


if __name__ == "__main__":
    df = fetch_meta()
    print(f"\nMETA OHLC: {len(df)} 行 · {df.index[0].date()} → {df.index[-1].date()}")
    print("\n方案 A · 跳过 IPO 转型期(2012-05 → 2014-12 vol 48%),分两段评估\n")

    # 段 2:close 切到 2021-09-30 止(防止段 3 数据泄漏),
    # 但保留 2012-2014 数据作为 SMA(225) 的预热窗口
    seg2_close = df["Close"].loc[:"2021-09-30"]
    seg2_open  = df["Open"].loc[:"2021-09-30"]
    print("=" * 50)
    print("段 2 · 稳态成长 + 隐私门 + 疫情泡沫  (2015-01 → 2021-09, 6.7 年)")
    print("=" * 50)
    summarize_window(seg2_close, seg2_open, start="2015-01-01")

    # 段 3:close 给全样本(2012 起),让长周期 MA 充分预热,start 卡 2021-10
    print()
    print("=" * 50)
    print("段 3 · 元宇宙崩盘 + AI 复苏           (2021-10 → 现在, 4.6 年)")
    print("=" * 50)
    summarize_window(df["Close"], df["Open"], start="2021-10-01")
