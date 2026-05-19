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

GitHub Actions runs `python -m augury run` at UTC 02:00 (post-US close).
Manual trigger: `gh workflow run daily.yml` or the Actions tab.

## Layout

- `augury/` — Python package (data, indicators, strategies, backtest, render)
- `data/` — parquet cache, committed to git for historical snapshots
- `docs/` — rendered HTML output, committed, served by GitHub Pages
- `.github/workflows/daily.yml` — scheduled refresh
- `scripts/` — one-off maintenance scripts (e.g. ISM history backfill)

## Indicators

See [INDICATORS.md](INDICATORS.md) for the full inventory by page (商业周期 / 就业 / 通胀 / 背景), including data source, frequency, and status (✅ working / ❌ todo / ⚠️ paused).
