"""Data layer: fetch from yfinance/FRED, cache to parquet, incremental updates."""
from __future__ import annotations
from datetime import date, timedelta
from pathlib import Path
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
PRICES_DIR = ROOT / "data" / "prices"
MACRO_DIR = ROOT / "data" / "macro"

DEFAULT_START = "2000-01-01"

# Tracked tickers: friendly symbol -> Yahoo ticker
PRICE_SYMBOLS: dict[str, str] = {
    "SPX": "^GSPC",
    "NDX": "^NDX",
    "VIX": "^VIX",
    "US10Y": "^TNX",
}


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
    # Drop today's incomplete bar
    new = new[new.index < pd.Timestamp(date.today())]

    if existing is not None and not existing.empty:
        merged = pd.concat([existing, new])
        merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    else:
        merged = new

    PRICES_DIR.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(p)
    return merged


def refresh_all() -> dict[str, pd.DataFrame]:
    results: dict[str, pd.DataFrame] = {}
    for symbol, ticker in PRICE_SYMBOLS.items():
        try:
            df = refresh_symbol(symbol, ticker)
            results[symbol] = df
            print(f"  ok  {symbol}: {len(df)} bars")
        except Exception as e:
            print(f"  err {symbol}: {e}")
            existing = load_prices(symbol)
            if existing is not None:
                results[symbol] = existing
    return results
