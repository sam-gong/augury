"""Headless JS↔Python backtest parity.

The client-side engine (``augury/static/backtest.js``) is a hand-port of
``augury.backtest``. Drift between the two is silent — the browser only
checks parity at the full window, and only when someone opens the page and
reads the console. This module closes that gap: it dumps, for every
strategy, the engine's raw inputs plus Python's metrics at several window
starts; ``parity_check.mjs`` re-runs the JS engine on the same inputs and
asserts the numbers match. Quartile starts exercise the warmup/carry-in
boundary that whole-window checks miss.

Driven by ``python -m augury parity``.
"""
from __future__ import annotations

import json
import pathlib

import pandas as pd

from augury import backtest, indicators, layout
from augury.render import (
    STRATEGY_PAGE_KEYS, _hybrid_payload, _json_safe, _strategy_payload,
)
from augury.strategies import HybridStrategy


def _starts(index: pd.DatetimeIndex) -> list[str | None]:
    """Window starts to test: full window, then quartile cut-points. The
    quartiles land some windows mid-position, exercising the carry-in/warmup
    behaviour at the window's left edge."""
    n = len(index)
    if n < 8:
        return [None]
    qs = [index[n // 4], index[n // 2], index[3 * n // 4]]
    return [None] + [d.strftime("%Y-%m-%d") for d in qs]


def _run(close, open_, strat, prices, start, fee, slippage):
    if isinstance(strat, HybridStrategy):
        sub_prices = {t: prices[t] for t in strat.substitutes}
        return backtest.run_hybrid(close, strat, open_, sub_prices,
                                   start=start, fee=fee, slippage=slippage)
    return backtest.run(close, strat, open_, start=start,
                        fee=fee, slippage=slippage)


def _asset_fixtures(asset: dict, prices: dict) -> list[dict]:
    df = prices.get(asset["ticker"])
    strategies = asset.get("strategies") or []
    if df is None or df.empty or not strategies:
        return []
    close = df["Close"].dropna()
    open_ = df["Open"].reindex(close.index)
    fee = asset.get("fee", backtest.DEFAULT_FEE)
    slippage = asset.get("slippage", backtest.DEFAULT_SLIPPAGE)
    starts = _starts(close.index)

    out = []
    for strat in strategies:
        strat.prepare(df)
        cases, full = [], None
        for start in starts:
            result = _run(close, open_, strat, prices, start, fee, slippage)
            cases.append({
                "start": start,
                "metrics": {k: _json_safe(v) for k, v in result.metrics.items()},
            })
            if start is None:
                full = result
        if isinstance(strat, HybridStrategy):
            payload = _hybrid_payload(asset, close, open_, strat, prices,
                                      fee, slippage, full)
        else:
            payload = _strategy_payload(asset, close, open_, strat,
                                        fee, slippage, full)
        out.append({
            "ticker": asset["ticker"], "label": strat.label(),
            "kind": payload["kind"], "payload": payload, "cases": cases,
        })
    return out


def fixtures() -> list[dict]:
    prices = {pid: indicators.load(pid) for pid in layout.PRICE_IDS}
    out = []
    for key in STRATEGY_PAGE_KEYS:
        for asset in layout.strategy_page(key)["sections"]:
            out.extend(_asset_fixtures(asset, prices))
    return out


def dump(path: pathlib.Path) -> tuple[int, int]:
    """Write fixtures to `path`; return (strategy count, case count)."""
    fx = fixtures()
    path.write_text(json.dumps(fx))
    return len(fx), sum(len(f["cases"]) for f in fx)
