"""Indicator registry — single source of truth for all data series.

Each indicator declares its id, source URL, frequency, schema, and a fetcher
that knows how to pull data from upstream and merge with the local parquet
cache. Charts, cards, and layouts consume indicators by id only — they don't
care whether the data came from FRED, yfinance, a scraper, or a composite.

Per-refresh health is tracked in `data/refresh_meta.json`: `last_success`
moves only on success so a stuck timestamp = stuck upstream, while
`last_attempt` and `last_error` reflect the most recent try."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
import json
import time
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = ROOT / "data" / "indicators"
META_PATH = ROOT / "data" / "refresh_meta.json"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_meta() -> dict:
    if META_PATH.exists():
        try:
            return json.loads(META_PATH.read_text())
        except Exception:
            return {}
    return {}


def _write_meta(meta: dict) -> None:
    META_PATH.parent.mkdir(parents=True, exist_ok=True)
    META_PATH.write_text(json.dumps(meta, indent=2, ensure_ascii=False))


def meta(id: str) -> dict:
    return _read_meta().get(id, {})


def all_meta() -> dict:
    return _read_meta()


def run_meta() -> dict:
    return _read_meta().get("_run", {})


def is_unhealthy(id: str) -> bool:
    """An indicator is unhealthy iff its most recent attempt failed — i.e.
    last_attempt > last_success. Low-frequency series that simply haven't had
    a new datapoint are NOT unhealthy as long as the fetcher returns cleanly."""
    m = _read_meta().get(id) or {}
    last_a = m.get("last_attempt")
    last_s = m.get("last_success")
    if not last_a:
        return False
    return last_s != last_a


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
    """Run fetcher, merge with existing, persist to parquet.

    Records refresh health in `data/refresh_meta.json`: `last_attempt` and
    `last_error` always reflect the latest try, while `last_success` only
    advances when this call returns cleanly — so a stuck timestamp signals
    a stuck upstream regardless of how many times we've tried since."""
    m = _read_meta()
    cur = dict(m.get(id, {}))  # preserve last_success on failure
    cur["last_attempt"] = _utc_iso()
    try:
        ind = REGISTRY[id]
        existing = load(id)
        if ind.deps:
            deps = {d: load(d) for d in ind.deps}
            new = ind.fetcher(ind, deps)
        else:
            new = ind.fetcher(ind, existing)
        if new is None or new.empty:
            merged = existing if existing is not None else pd.DataFrame()
        elif existing is not None and not existing.empty:
            # Always prefer the latest fetched value on duplicates. For monthly
            # series this captures upstream revisions (ISM monthly revisions, CPI
            # Feb seasonal adjustment, PCE Jul benchmark, BLS payrolls benchmark).
            # Daily series get intraday-style overwrite. Backtests that need
            # point-in-time first-release values should keep their own snapshots.
            merged = pd.concat([existing, new])
            merged = merged[~merged.index.duplicated(keep="last")].sort_index()
        else:
            merged = new
        if not merged.empty:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            merged.to_parquet(_path(id))
        cur["last_success"] = cur["last_attempt"]
        cur["last_error"] = None
        m[id] = cur
        _write_meta(m)
        return merged
    except Exception as e:
        cur["last_error"] = f"{type(e).__name__}: {e}"
        m[id] = cur
        _write_meta(m)
        raise


def refresh_all() -> None:
    """Refresh every registered indicator. Composites run last."""
    started_at = _utc_iso()
    t0 = time.time()
    ok = err = 0
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
                ok += 1
            except Exception as e:
                print(f"  err {ind.id}: {e}")
                err += 1
    m = _read_meta()
    m["_run"] = {
        "started_at": started_at,
        "completed_at": _utc_iso(),
        "duration_sec": round(time.time() - t0, 1),
        "ok": ok, "err": err, "total": ok + err,
    }
    _write_meta(m)


# Register all indicators on package import.
from . import catalog  # noqa: E402, F401
