"""Data layer: fetch from yfinance/FRED, cache to parquet, incremental updates."""
from __future__ import annotations
import os
from datetime import date, timedelta
from pathlib import Path
import pandas as pd
import yfinance as yf
from dotenv import load_dotenv
from fredapi import Fred

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
PRICES_DIR = ROOT / "data" / "prices"
MACRO_DIR = ROOT / "data" / "macro"

DEFAULT_START = "2000-01-01"

# yfinance tickers: friendly symbol -> Yahoo ticker
PRICE_SYMBOLS: dict[str, str] = {
    "SPX": "^GSPC",
    "NDX": "^NDX",
    "VIX": "^VIX",
    "US10Y": "^TNX",
}

# FRED series catalog.
# is_rate=True -> values already in percentage-point space (YoY shown as +/- pp)
# is_rate=False -> levels (YoY shown as %)
MACRO_SERIES: dict[str, dict] = {
    # ---- Signal zone components (PMI proxy + leading indicators) ----
    "GACDFSA066MSFRBPHI": {"title": "Philly Fed Current Activity (PMI proxy)", "unit": "", "is_rate": True},
    "GAFDFSA066MSFRBPHI": {"title": "Philly Fed Future Activity 6m",            "unit": "", "is_rate": True},
    "NOCDFSA066MSFRBPHI": {"title": "Philly Fed New Orders",                     "unit": "", "is_rate": True},
    "IVCDFSA066MSFRBPHI": {"title": "Philly Fed Inventories",                    "unit": "", "is_rate": True},
    "NFCI":               {"title": "Chicago Fed NFCI",                          "unit": "", "is_rate": True},
    # T10Y2Y already in background, but referenced in signal zone too
    # ---- Position zone (current cycle state) ----
    "CFNAI":              {"title": "Chicago Fed Activity Index",                "unit": "", "is_rate": True},
    "INDPRO":             {"title": "Industrial Production",                     "unit": "Index", "is_rate": False},
    # ---- Background zone (rates / liquidity / inflation / employment) ----
    "FEDFUNDS":  {"title": "Fed Funds Rate",         "unit": "%",    "is_rate": True},
    "DGS2":      {"title": "2Y Treasury",            "unit": "%",    "is_rate": True},
    "DGS10":     {"title": "10Y Treasury",           "unit": "%",    "is_rate": True},
    "T10Y2Y":    {"title": "10Y-2Y Spread",          "unit": "%",    "is_rate": True},
    "M2SL":      {"title": "M2 Money Stock",         "unit": "$B",   "is_rate": False},
    "WALCL":     {"title": "Fed Balance Sheet",      "unit": "$M",   "is_rate": False},
    "WTREGEN":   {"title": "Treasury General Acct",  "unit": "$M",   "is_rate": False},
    "RRPONTSYD": {"title": "Overnight Reverse Repo", "unit": "$B",   "is_rate": False},
    "CPIAUCSL":  {"title": "CPI All Items",          "unit": "Index", "is_rate": False},
    "CPILFESL":  {"title": "Core CPI",               "unit": "Index", "is_rate": False},
    "UNRATE":    {"title": "Unemployment Rate",      "unit": "%",    "is_rate": True},
    "ICSA":      {"title": "Initial Claims",         "unit": "",     "is_rate": False},
    "PAYEMS":    {"title": "Nonfarm Payrolls",       "unit": "K",    "is_rate": False},
}

# Macro page layout: explicit zones + which series go where.
# Driven from this single source of truth so the renderer stays declarative.
PMI_PROXY = "GACDFSA066MSFRBPHI"

MACRO_LAYOUT = {
    "signal": {
        "label": "周期信号",
        "subtitle": "PMI 是同步指标; 看它的领先指标预判 2-12 月后的方向。",
        "pmi_proxy": PMI_PROXY,
        "charts": [
            {
                "anchor": "future-activity",
                "title": "费城联储未来活动 (leads 6m)",
                "leader_id": "GAFDFSA066MSFRBPHI",
                "leader_name": "Future Activity",
                "lead_months": 6,
                "interp": "上行 → 6 月后 PMI 上行;下行 → 6 月后 PMI 下行。",
                "invert": False,
            },
            {
                "anchor": "new-orders-minus-inventories",
                "title": "新订单 − 库存 (leads 2m)",
                "leader_id": "_NOC_MINUS_IVC",
                "leader_name": "New Orders − Inventories",
                "lead_months": 2,
                "interp": "正值且上升 → 2 月后 PMI 上行;负值且下降 → 即将走弱。",
                "invert": False,
                "computed": {"a": "NOCDFSA066MSFRBPHI", "b": "IVCDFSA066MSFRBPHI", "op": "sub"},
            },
            {
                "anchor": "nfci",
                "title": "NFCI 金融条件 (leads 6m, 反转视图)",
                "leader_id": "NFCI",
                "leader_name": "NFCI (inverted)",
                "lead_months": 6,
                "interp": "图上「反转 NFCI」下行(实际 NFCI 上行,金融条件收紧)→ 6 月后 PMI 走弱。",
                "invert": True,
            },
            {
                "anchor": "yield-curve",
                "title": "10Y-2Y 收益曲线 (leads 12m+)",
                "leader_id": "T10Y2Y",
                "leader_name": "10Y-2Y Spread",
                "lead_months": 12,
                "interp": "倒挂 (<0) → 12-18 月内衰退风险显著上升;转正回升 → 周期触底。",
                "invert": False,
            },
        ],
    },
    "position": {
        "label": "周期定位",
        "subtitle": "当前在周期的哪一段 — 同步指标的现值。",
        "cards": [PMI_PROXY, "CFNAI", "INDPRO"],
    },
    "background": {
        "label": "背景",
        "subtitle": "利率 / 流动性 / 通胀 / 就业 — 解释为什么领先指标在动。",
        "subsections": [
            {"anchor": "rates",      "label": "利率",   "ids": ["FEDFUNDS", "DGS2", "DGS10", "T10Y2Y"]},
            {"anchor": "liquidity",  "label": "流动性", "ids": ["M2SL", "WALCL", "WTREGEN", "RRPONTSYD"]},
            {"anchor": "inflation",  "label": "通胀",   "ids": ["CPIAUCSL", "CPILFESL"]},
            {"anchor": "employment", "label": "就业",   "ids": ["UNRATE", "ICSA", "PAYEMS"]},
        ],
    },
}


# ---------- prices (yfinance) ----------

def prices_path(symbol: str) -> Path:
    return PRICES_DIR / f"{symbol}.parquet"


def load_prices(symbol: str) -> pd.DataFrame | None:
    p = prices_path(symbol)
    if not p.exists():
        return None
    return pd.read_parquet(p)


def refresh_symbol(symbol: str, ticker: str) -> pd.DataFrame:
    p = prices_path(symbol)
    existing = pd.read_parquet(p) if p.exists() else None

    if existing is not None and not existing.empty:
        start = (existing.index[-1] + timedelta(days=1)).strftime("%Y-%m-%d")
    else:
        start = DEFAULT_START

    today_str = date.today().strftime("%Y-%m-%d")
    if start >= today_str:
        return existing  # type: ignore[return-value]

    new = yf.download(ticker, start=start, auto_adjust=False, progress=False)
    if new is None or new.empty:
        return existing if existing is not None else pd.DataFrame()

    if isinstance(new.columns, pd.MultiIndex):
        new.columns = new.columns.get_level_values(0)
    new.index = pd.to_datetime(new.index).tz_localize(None).normalize()
    new = new[new.index < pd.Timestamp(date.today())]

    if existing is not None and not existing.empty:
        merged = pd.concat([existing, new])
        merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    else:
        merged = new

    PRICES_DIR.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(p)
    return merged


# ---------- macro (FRED) ----------

_fred: Fred | None = None


def _get_fred() -> Fred:
    global _fred
    if _fred is None:
        key = os.environ.get("FRED_API_KEY")
        if not key:
            raise RuntimeError("FRED_API_KEY not set (check .env or env vars)")
        _fred = Fred(api_key=key)
    return _fred


def macro_path(series_id: str) -> Path:
    return MACRO_DIR / f"{series_id}.parquet"


def load_macro(series_id: str) -> pd.DataFrame | None:
    p = macro_path(series_id)
    if not p.exists():
        return None
    return pd.read_parquet(p)


def refresh_macro_series(series_id: str) -> pd.DataFrame:
    """Incremental fetch from FRED. Keeps first-seen values (preserves
    original release values before later revisions overwrite them)."""
    fred = _get_fred()
    p = macro_path(series_id)
    existing = pd.read_parquet(p) if p.exists() else None

    if existing is not None and not existing.empty:
        start = (existing.index[-1] + timedelta(days=1)).strftime("%Y-%m-%d")
        new_s = fred.get_series(series_id, observation_start=start)
    else:
        new_s = fred.get_series(series_id)

    if new_s is None or new_s.empty:
        return existing if existing is not None else pd.DataFrame()

    new_s = new_s.dropna()
    if new_s.empty:
        return existing if existing is not None else pd.DataFrame()

    new = pd.DataFrame({"value": new_s})
    new.index = pd.to_datetime(new.index).normalize()
    new.index.name = "date"

    if existing is not None and not existing.empty:
        merged = pd.concat([existing, new])
        merged = merged[~merged.index.duplicated(keep="first")].sort_index()
    else:
        merged = new

    MACRO_DIR.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(p)
    return merged


# ---------- top-level orchestration ----------

def refresh_all() -> None:
    print(" prices:")
    for symbol, ticker in PRICE_SYMBOLS.items():
        try:
            n = len(refresh_symbol(symbol, ticker))
            print(f"  ok  {symbol}: {n} bars")
        except Exception as e:
            print(f"  err {symbol}: {e}")

    if os.environ.get("FRED_API_KEY"):
        print(" macro:")
        for sid in MACRO_SERIES:
            try:
                n = len(refresh_macro_series(sid))
                print(f"  ok  {sid}: {n} obs")
            except Exception as e:
                print(f"  err {sid}: {e}")
    else:
        print(" macro: skipped (FRED_API_KEY not set)")
