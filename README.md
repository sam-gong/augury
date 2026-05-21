# Augury

Personal market dashboard. EOD-refresh, static site, deploys to GitHub Pages.

## Quick start

```bash
pyenv local 3.12.3
python -m venv .venv
source .venv/bin/activate
pip install -e .
python -m augury run
open docs/index.html
```

## Daily run

GitHub Actions runs `python -m augury run` at UTC 23:17 (北京 07:17, post-US close).
Manual trigger: `gh workflow run daily.yml` or the Actions tab.

## Parity

Strategy backtests run twice: in Python at render time, and in JS in the
browser (so the date-range picker can re-run them client-side). The JS engine
([`augury/static/backtest.js`](augury/static/backtest.js)) is a hand-port of
[`augury/backtest.py`](augury/backtest.py) and must agree with it.

```bash
python -m augury parity   # replays the JS engine, asserts it matches Python
```

It dumps Python's metrics at several window starts and checks the JS engine
reproduces them. Trade-driven metrics (counts, NAV, CAGR, drawdown, vol) must
match strictly; the vectorbt-derived ratios (sharpe/sortino/calmar) allow ~1%
since the two annualize differently off the full window. CI re-runs this on any
push touching either engine ([`parity.yml`](.github/workflows/parity.yml)).

## Layout

- `augury/` — Python package (data, indicators, strategies, backtest, render)
- `augury/static/` — `backtest.js` (JS engine, inlined into pages) + `parity_check.mjs`
- `data/` — parquet cache, committed to git for historical snapshots
- `docs/` — rendered HTML output, committed, served by GitHub Pages
- `.github/workflows/` — `daily.yml` (scheduled refresh) + `parity.yml` (engine parity)
- `scripts/` — one-off maintenance scripts (e.g. ISM history backfill)

## Indicators

See [INDICATORS.md](INDICATORS.md) for the full inventory by page (商业周期 / 就业 / 通胀 / 背景), including data source, frequency, and status (✅ working / ❌ todo / ⚠️ paused).
