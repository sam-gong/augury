"""Indicator registry — single source of truth for all data series.

Each indicator declares its id, source URL, frequency, schema, and a fetcher
that knows how to pull data from upstream and merge with the local parquet
cache. Charts, cards, and layouts consume indicators by id only — they don't
care whether the data came from FRED, yfinance, a scraper, or a composite."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = ROOT / "data" / "indicators"


@dataclass
class Indicator:
    id: str
    title: str
    source_url: str
    frequency: str                # "daily" | "weekly" | "monthly"
    fetcher: Callable             # (indicator, existing_df_or_deps_dict) -> DataFrame
    unit: str = ""
    is_rate: bool = False
    value_column: str = "value"   # which column to read for cards / YoY
    deps: list[str] = field(default_factory=list)  # other indicator ids (composites)
    params: dict = field(default_factory=dict)     # fetcher-specific args


REGISTRY: dict[str, Indicator] = {}


def register(ind: Indicator) -> Indicator:
    REGISTRY[ind.id] = ind
    return ind


def _path(id: str) -> Path:
    return DATA_DIR / f"{id}.parquet"


def load(id: str) -> pd.DataFrame | None:
    p = _path(id)
    return pd.read_parquet(p) if p.exists() else None


def refresh(id: str) -> pd.DataFrame:
    """Run fetcher, merge with existing, persist to parquet."""
    ind = REGISTRY[id]
    existing = load(id)
    if ind.deps:
        deps = {d: load(d) for d in ind.deps}
        new = ind.fetcher(ind, deps)
    else:
        new = ind.fetcher(ind, existing)
    if new is None or new.empty:
        return existing if existing is not None else pd.DataFrame()
    if existing is not None and not existing.empty:
        # Always prefer the latest fetched value on duplicates. For monthly
        # series this captures upstream revisions (ISM monthly revisions, CPI
        # Feb seasonal adjustment, PCE Jul benchmark, BLS payrolls benchmark).
        # Daily series get intraday-style overwrite. Backtests that need
        # point-in-time first-release values should keep their own snapshots.
        merged = pd.concat([existing, new])
        merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    else:
        merged = new
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(_path(id))
    return merged


def refresh_all() -> None:
    """Refresh every registered indicator. Composites run last."""
    primaries  = [i for i in REGISTRY.values() if not i.deps]
    composites = [i for i in REGISTRY.values() if i.deps]
    for group, label in [(primaries, "primary"), (composites, "composite")]:
        if not group:
            continue
        print(f" {label}:")
        for ind in group:
            try:
                n = len(refresh(ind.id))
                print(f"  ok  {ind.id}: {n} obs")
            except Exception as e:
                print(f"  err {ind.id}: {e}")


# Register all indicators on package import.
from . import catalog  # noqa: E402, F401
