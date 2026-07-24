"""跨资产策略:用 BTC 作为信号,交易 META。

信号:BTC close 上穿 BTC.SMA(120) × 1.01 → 买 META(T+1 开盘)
      BTC close 下穿 BTC.SMA(120) × 0.99 → 卖 META(T+1 开盘)

假设:BTC 是 risk-on/risk-off 的领先指标,且 BTC 24/7 交易可能更早发出
      信号。META 跟随 BTC 的"风险偏好"波动而非自身均线。

风险:META 有独立基本面(广告周期、元宇宙、AI),BTC 信号未必能捕到这些。
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dataclasses import dataclass
import pandas as pd
import yfinance as yf

from augury.backtest import run
from augury.strategies._base import Strategy, OverlayLine


@dataclass(kw_only=True)
class CrossAssetSmaBand(Strategy):
    """Trade META based on SIGNAL_TICKER's close-vs-SMA breakout.

    Same band logic as SmaBand, but signal series comes from a different
    asset (e.g. BTC). Asset close is ignored in signals() — used only for
    backtest execution (entry/exit at META's T+1 open)."""
    signal_close: pd.Series = None
    signal_label: str = "BTC"
    ma: int = 120
    threshold: float = 0.01
    name: str = "Cross-Asset SMA Band"

    def __post_init__(self):
        t = f" ±{self.threshold:.0%}" if self.threshold > 0 else ""
        self.spec = f"{self.signal_label} 上穿 SMA({self.ma}){t} 做多 META,下穿平仓"

    def label(self) -> str:
        return f"{self.signal_label}-SMA({self.ma}) ±{self.threshold:.0%}"

    def signals(self, close: pd.Series) -> tuple[pd.Series, pd.Series]:
        # SMA on BTC's NATIVE 24/7 calendar — "120 日" 在 BTC 语境下指 120
        # 个自然日,不是 120 个 META 交易日(那会变成 ~5.5 月)。
        sig_native = self.signal_close.dropna()
        sma_native = sig_native.rolling(self.ma).mean()
        upper_native = sma_native * (1 + self.threshold)
        lower_native = sma_native * (1 - self.threshold)
        # Resample to META's trading-day index for crossing detection
        # (ffill picks up the last available BTC observation, including
        # weekend moves that became actionable by Monday open).
        signal = sig_native.reindex(close.index, method="ffill")
        upper  = upper_native.reindex(close.index, method="ffill")
        lower  = lower_native.reindex(close.index, method="ffill")
        entries = (signal > upper) & (signal.shift(1) <= upper.shift(1))
        exits   = (signal < lower) & (signal.shift(1) >= lower.shift(1))
        return entries, exits

    def overlay(self, close: pd.Series) -> list[OverlayLine]:
        return []  # overlay on signal asset, not META — skip for research

    def reference(self, close: pd.Series) -> dict:
        signal = self.signal_close.reindex(close.index).ffill()
        sma = signal.rolling(self.ma).mean().dropna()
        if sma.empty:
            return {"label": f"{self.signal_label} MA{self.ma}", "value": None, "pct": None}
        last_sig = float(signal.dropna().iloc[-1])
        last_sma = float(sma.iloc[-1])
        return {
            "label": f"{self.signal_label} vs MA{self.ma}",
            "value": last_sma,
            "pct": last_sig / last_sma - 1,
        }


def fetch():
    meta = yf.download("META", start="2012-05-01", auto_adjust=False, progress=False)
    btc  = yf.download("BTC-USD", start="2014-01-01", auto_adjust=False, progress=False)
    for df in (meta, btc):
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    return meta.dropna(subset=["Open","Close"]), btc.dropna(subset=["Close"])


def backtest_window(meta: pd.DataFrame, strat, end: str | None, start: str):
    close = meta["Close"] if end is None else meta["Close"].loc[:end]
    open_ = meta["Open"]  if end is None else meta["Open"].loc[:end]
    return run(close, strat, open_, start=start)


def fmt_pct(x):  return "    -" if x is None or pd.isna(x) else f"{x*100:>+6.1f}%"
def fmt_num(x):  return "    -" if x is None or pd.isna(x) else f"{x:>6.2f}"
def fmt_nav(x):  return "    -" if x is None or pd.isna(x) else f"{x:>5.2f}x"


def print_row(label, m, state):
    wr = m.get("win_rate")
    ct = m.get("closed_trades")
    wr_s = f"{wr*100:>4.0f}%" if wr is not None and not pd.isna(wr) else "    -"
    ct_s = f"{ct:>5d}" if ct is not None else "    -"
    print(f"{label:<26}"
          f"{fmt_pct(m.get('cagr')):>9}"
          f"{fmt_pct(m.get('max_drawdown')):>9}"
          f"{fmt_num(m.get('sharpe')):>9}"
          f"{fmt_num(m.get('calmar')):>9}"
          f"{wr_s:>7}"
          f"{ct_s:>7}"
          f"{fmt_nav(m.get('end_nav')):>8}  {state}")


def main():
    meta, btc = fetch()
    inter = meta.index.intersection(btc.index)
    print(f"\nMETA: {meta.index[0].date()} → {meta.index[-1].date()}")
    print(f"BTC : {btc.index[0].date()} → {btc.index[-1].date()}")
    print(f"交集: {inter[0].date()} → {inter[-1].date()}\n")

    from augury.strategies import SmaCross, SmaBand
    candidates = [
        ("SMA(20)/(50) META",      SmaCross(fast=20, slow=50)),
        ("SMA(22)/(40) META",      SmaCross(fast=22, slow=40)),
        ("SMA(130) ±3% META",      SmaBand(ma=130, threshold=0.03)),
        ("BTC-SMA(120) ±1% → META", CrossAssetSmaBand(
            signal_close=btc["Close"], signal_label="BTC",
            ma=120, threshold=0.01)),
    ]

    hdr = f"{'策略':<26}{'CAGR':>9}{'MDD':>9}{'Sharpe':>9}{'Calmar':>9}{'胜率':>7}{'交易':>7}{'NAV':>8}  状态"

    print("=" * 78)
    print("段 2 · 稳态成长 + 隐私门 + 疫情泡沫 (2015-01 → 2021-09, 6.7 年)")
    print("=" * 78)
    print(hdr)
    print("-" * len(hdr))
    bh_m = None
    for label, strat in candidates:
        r = backtest_window(meta, strat, end="2021-09-30", start="2015-01-01")
        print_row(label, r.metrics, r.state)
        if bh_m is None:
            bh_m = r.metrics
    print_row("Buy & Hold META", {
        "cagr": bh_m.get("bh_cagr"), "max_drawdown": bh_m.get("bh_max_drawdown"),
        "sharpe": bh_m.get("bh_sharpe"), "calmar": bh_m.get("bh_calmar"),
        "end_nav": bh_m.get("bh_end_nav"),
    }, "long")

    print()
    print("=" * 78)
    print("段 3 · 元宇宙崩盘 + AI 复苏 (2021-10 → 现在, 4.6 年)")
    print("=" * 78)
    print(hdr)
    print("-" * len(hdr))
    bh_m = None
    for label, strat in candidates:
        r = backtest_window(meta, strat, end=None, start="2021-10-01")
        print_row(label, r.metrics, r.state)
        if bh_m is None:
            bh_m = r.metrics
    print_row("Buy & Hold META", {
        "cagr": bh_m.get("bh_cagr"), "max_drawdown": bh_m.get("bh_max_drawdown"),
        "sharpe": bh_m.get("bh_sharpe"), "calmar": bh_m.get("bh_calmar"),
        "end_nav": bh_m.get("bh_end_nav"),
    }, "long")

    # Diagnostic: signal alignment using BTC native calendar
    print()
    print("=" * 78)
    print("诊断 · BTC 信号当前状态 (BTC 24/7 历法 SMA(120))")
    print("=" * 78)
    sig = btc["Close"].dropna()
    sma120 = sig.rolling(120).mean()
    last_btc = float(sig.iloc[-1])
    last_sma = float(sma120.iloc[-1])
    upper = last_sma * 1.01
    lower = last_sma * 0.99
    pct = (last_btc / last_sma - 1) * 100
    if last_btc > upper:
        zone = "上轨之上 → 处于做多区"
    elif last_btc < lower:
        zone = "下轨之下 → 处于空仓区"
    else:
        zone = "在带内 → 维持上次状态"
    print(f"BTC 今日 close: ${last_btc:,.0f}  ({sig.index[-1].date()})")
    print(f"BTC SMA(120) : ${last_sma:,.0f}  (上轨 ${upper:,.0f} / 下轨 ${lower:,.0f})")
    print(f"BTC 距 SMA   : {pct:+.1f}%")
    print(f"区位          : {zone}")


if __name__ == "__main__":
    main()
