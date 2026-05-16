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

# FRED series catalog. is_rate=True means values are already in percentage points
# (so YoY change is shown as +/- pp); is_rate=False means levels (YoY shown as %).
MACRO_SERIES: dict[str, dict] = {
    # Leading / business cycle
    "USSLIND":   {"category": "leading",    "title": "Leading Index (US)",       "unit": "%",     "is_rate": True},
    "CFNAI":     {"category": "leading",    "title": "Chicago Fed Activity",     "unit": "",      "is_rate": True},
    # Rates / policy
    "FEDFUNDS":  {"category": "rates",      "title": "Fed Funds Rate",           "unit": "%",     "is_rate": True},
    "DGS2":      {"category": "rates",      "title": "2Y Treasury",              "unit": "%",     "is_rate": True},
    "DGS10":     {"category": "rates",      "title": "10Y Treasury",             "unit": "%",     "is_rate": True},
    "T10Y2Y":    {"category": "rates",      "title": "10Y-2Y Spread",            "unit": "%",     "is_rate": True},
    # Liquidity
    "M2SL":      {"category": "liquidity",  "title": "M2 Money Stock",           "unit": "$B",    "is_rate": False},
    "WALCL":     {"category": "liquidity",  "title": "Fed Balance Sheet",        "unit": "$M",    "is_rate": False},
    "WTREGEN":   {"category": "liquidity",  "title": "Treasury General Acct",    "unit": "$M",    "is_rate": False},
    "RRPONTSYD": {"category": "liquidity",  "title": "Overnight Reverse Repo",   "unit": "$B",    "is_rate": False},
    # Inflation
    "CPIAUCSL":  {"category": "inflation",  "title": "CPI All Items",            "unit": "Index", "is_rate": False},
    "CPILFESL":  {"category": "inflation",  "title": "Core CPI",                 "unit": "Index", "is_rate": False},
    # Employment
    "UNRATE":    {"category": "employment", "title": "Unemployment Rate",        "unit": "%",     "is_rate": True},
    "ICSA":      {"category": "employment", "title": "Initial Claims",           "unit": "",      "is_rate": False},
    "PAYEMS":    {"category": "employment", "title": "Nonfarm Payrolls",         "unit": "K",     "is_rate": False},
}

CATEGORY_LABELS = {
    "leading":    "商业周期 / 领先指标",
    "rates":      "货币政策 / 利率",
    "liquidity":  "流动性",
    "inflation":  "通胀",
    "employment": "就业",
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
