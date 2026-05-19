"""Fetcher functions for indicators.

Each fetcher has one of two signatures:
  - primary:   (indicator, existing_df)   -> new rows DataFrame
  - composite: (indicator, deps_dfs_dict) -> recomputed DataFrame

The dispatcher in indicators.refresh() picks which to call based on
whether the indicator declares `deps`."""
from __future__ import annotations
import io
import json
import os
import re
import urllib.request
from datetime import date, timedelta
from pathlib import Path
import pandas as pd
import yfinance as yf
from fredapi import Fred

_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}

_PKG_DATA = Path(__file__).resolve().parent / "data"

_fred: Fred | None = None


def _get_fred() -> Fred:
    global _fred
    if _fred is None:
        key = os.environ.get("FRED_API_KEY")
        if not key:
            raise RuntimeError("FRED_API_KEY not set (check .env or env vars)")
        _fred = Fred(api_key=key)
    return _fred


# ---------- FRED ----------

def fred(indicator, existing):
    f = _get_fred()
    if existing is not None and not existing.empty:
        start = (existing.index[-1] + timedelta(days=1)).strftime("%Y-%m-%d")
        s = f.get_series(indicator.id, observation_start=start)
    else:
        s = f.get_series(indicator.id)
    if s is None or s.empty:
        return pd.DataFrame()
    s = s.dropna()
    if s.empty:
        return pd.DataFrame()
    new = pd.DataFrame({"value": s})
    new.index = pd.to_datetime(new.index).normalize()
    new.index.name = "date"
    return new


# ---------- yfinance ----------

def yahoo(indicator, existing):
    ticker = indicator.params["ticker"]
    if existing is not None and not existing.empty:
        start = (existing.index[-1] + timedelta(days=1)).strftime("%Y-%m-%d")
    else:
        start = "2000-01-01"
    today = date.today().strftime("%Y-%m-%d")
    if start >= today:
        return pd.DataFrame()
    new = yf.download(ticker, start=start, auto_adjust=False, progress=False)
    if new is None or new.empty:
        return pd.DataFrame()
    if isinstance(new.columns, pd.MultiIndex):
        new.columns = new.columns.get_level_values(0)
    new.index = pd.to_datetime(new.index).tz_localize(None).normalize()
    new = new[new.index < pd.Timestamp(date.today())]
    return new


# ---------- Market breadth (% constituents > N-day MA) ----------
# NDFI / S5FI: not on yfinance; we compute it ourselves from current
# constituents. Survivorship bias is acceptable for a regime-detection
# indicator (stockcharts NDFI/S5FI does the same thing).

_WIKI_NDX = "https://en.wikipedia.org/wiki/Nasdaq-100"
_WIKI_SPX = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def _ndx_constituents() -> list[str]:
    html = _http_get(_WIKI_NDX)
    import io as _io
    tbl = pd.read_html(_io.StringIO(html), attrs={"id": "constituents"})[0]
    return [str(t).strip() for t in tbl["Ticker"].tolist()]


def _spx_constituents() -> list[str]:
    html = _http_get(_WIKI_SPX)
    import io as _io
    tbl = pd.read_html(_io.StringIO(html))[0]
    # Wikipedia uses BRK.B style; yfinance wants BRK-B.
    return [str(s).replace(".", "-").strip() for s in tbl["Symbol"].tolist()]


def breadth_above_ma(indicator, existing):
    """% of constituents whose close > N-day SMA, computed daily.

    Params:
      universe: "ndx" or "spx"
      ma:       window length (default 50)
      start:    history start date (default "2010-01-01")
    """
    universe = indicator.params["universe"]
    n = int(indicator.params.get("ma", 50))
    start = indicator.params.get("start", "2010-01-01")
    tickers = _ndx_constituents() if universe == "ndx" else _spx_constituents()
    df = yf.download(tickers, start=start, auto_adjust=False, progress=False,
                     group_by="ticker", threads=True)
    if df is None or df.empty:
        return pd.DataFrame()
    closes = df.xs("Close", axis=1, level=-1) if isinstance(df.columns, pd.MultiIndex) else df[["Close"]]
    sma = closes.rolling(n, min_periods=n).mean()
    # mean of bools where SMA defined; NaN-tolerant per row.
    above = (closes > sma).where(sma.notna())
    pct = above.mean(axis=1, skipna=True) * 100
    out = pd.DataFrame({"value": pct}).dropna()
    out.index = pd.to_datetime(out.index).tz_localize(None).normalize()
    out.index.name = "date"
    out = out[out.index < pd.Timestamp(date.today())]
    return out


# ---------- ISM PMI / NMI (jin10 bulk + investing.com live tail) ----------

_JIN10_API = "https://datacenter-api.jin10.com/reports/list_v2"
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15")


def _http_get(url: str, headers: dict | None = None) -> str:
    hdrs = {"User-Agent": _UA}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    return urllib.request.urlopen(req, timeout=30).read().decode("utf-8", errors="ignore")


def _investing_recent(url: str) -> pd.DataFrame:
    """investing.com: ~100 most-recent months from __NEXT_DATA__ JSON.
    Each event's occurrence_time is the release date; reference month = release - 1."""
    html = _http_get(url)
    m = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.+?)</script>',
        html, re.DOTALL,
    )
    if not m:
        raise RuntimeError(f"investing.com: __NEXT_DATA__ not found at {url}")
    payload = json.loads(m.group(1))
    occ = (payload["props"]["pageProps"]["state"]
                  ["economicCalendarEventStore"]["occurrences"])
    rows = []
    for o in occ:
        actual = o.get("actualRaw") if o.get("actualRaw") is not None else o.get("actual")
        release = o.get("occurrence_time")
        if actual is None or release is None:
            continue
        ref = (pd.to_datetime(release).tz_convert(None)
               - pd.DateOffset(months=1)).normalize().replace(day=1)
        rows.append({"date": ref, "value": float(actual)})
    return pd.DataFrame(rows).set_index("date").sort_index()


def _jin10_history(attr_id: int) -> pd.DataFrame:
    """jin10 datacenter API paginated walk-back. release_date - 1 month = reference month.
    Currently lags ~6-8 months behind live (investing.com fills the tail)."""
    import time
    headers = {
        "x-app-id": "rU6QIu7JHe2gOUeR",
        "x-csrf-token": "x-csrf-token",
        "x-version": "1.0.0",
    }
    rows: list[tuple[pd.Timestamp, float]] = []
    max_date = ""
    while True:
        url = (f"{_JIN10_API}?category=ec&attr_id={attr_id}&max_date={max_date}"
               f"&_={int(time.time()*1000)}")
        page = json.loads(_http_get(url, headers))
        values = page.get("data", {}).get("values", [])
        if not values:
            break
        for release_date_str, actual, _f, _p in values:
            if actual is None:
                continue
            release_dt = pd.Timestamp(release_date_str)
            ref = (release_dt - pd.DateOffset(months=1)).normalize().replace(day=1)
            rows.append((ref, float(actual)))
        oldest = pd.Timestamp(values[-1][0]) - pd.Timedelta(days=1)
        max_date = oldest.strftime("%Y-%m-%d")
    df = pd.DataFrame(rows, columns=["date", "value"]).set_index("date").sort_index()
    return df[~df.index.duplicated(keep="last")]


def ism_calendar(indicator, existing):
    """ISM Manufacturing PMI / Services PMI (NMI).
    params: {jin10_attr_id, investing_url}.
    Strategy: investing.com __NEXT_DATA__ live tail + jin10 bulk history backfill.
    Bulk pull only runs when the parquet doesn't yet cover pre-2000 history."""
    p = indicator.params
    try:
        recent = _investing_recent(p["investing_url"])
    except Exception as e:
        print(f"  warn: investing.com tail failed ({e}); falling back to jin10 only")
        recent = pd.DataFrame()
    needs_backfill = (
        existing is None
        or existing.empty
        or existing.index.min() > pd.Timestamp("2000-01-01")
    )
    if needs_backfill:
        try:
            bulk = _jin10_history(p["jin10_attr_id"])
            df = pd.concat([bulk, recent]).sort_index() if not recent.empty else bulk
        except Exception as e:
            print(f"  warn: jin10 backfill failed ({e}); using recent only")
            df = recent
    else:
        df = recent
    if df.empty:
        return df
    df = df[~df.index.duplicated(keep="last")]
    df.index.name = "date"
    return df


# ---------- DBnomics (ISM Manufacturing sub-indices, 2021- → present) ----------

_DBN_API = "https://api.db.nomics.world/v22/series"


def dbnomics(indicator, existing):
    """DBnomics single-series fetcher. params: {provider, dataset, series}."""
    p = indicator.params
    url = f"{_DBN_API}/{p['provider']}/{p['dataset']}/{p['series']}?observations=1"
    resp = json.loads(_http_get(url))
    docs = resp.get("series", {}).get("docs", [])
    if not docs:
        return pd.DataFrame()
    s = docs[0]
    periods = s.get("period", [])
    values = s.get("value", [])
    rows = [(pd.Timestamp(per).replace(day=1), float(v))
            for per, v in zip(periods, values) if v is not None]
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["date", "value"]).set_index("date").sort_index()
    df.index.name = "date"
    return df


# ---------- ISM Manufacturing sub-indices (DBnomics history + ISM PDF live tail) ----------

_ism_pdf_cache: dict[str, str] | None = None


def _ism_pdf_urls() -> dict[str, str]:
    """Discover currently-live ISM Manufacturing PMI PDFs.
    Lists candidates via Wayback CDX (which indexes them), then HEAD-probes
    each to filter to URLs the ISM CDN still serves (~8 most-recent months).
    Returns {'YYYY-MM': full_url}, cached per-process."""
    global _ism_pdf_cache
    if _ism_pdf_cache is not None:
        return _ism_pdf_cache
    cdx = ("https://web.archive.org/cdx/search/cdx?"
           "url=ismworld.org/globalassets/pub/research-and-surveys/rob/pmi/"
           "&matchType=prefix&output=json&filter=mimetype:application/pdf"
           "&collapse=urlkey&limit=300")
    try:
        rows = json.loads(_download(cdx).decode("utf-8", errors="ignore"))
    except Exception as e:
        print(f"  warn: Wayback CDX failed ({e}); ISM PDF tail unavailable")
        _ism_pdf_cache = {}
        return _ism_pdf_cache
    pat = re.compile(r"/pmi/([A-Za-z0-9]+)(\d{4})(\d{2})pmi\.pdf", re.I)
    candidates: dict[str, str] = {}
    for row in rows[1:]:
        if len(row) < 3:
            continue
        url = row[2].split("?")[0]   # drop query string
        m = pat.search(url)
        if not m:
            continue
        ym = f"{m.group(2)}-{m.group(3)}"
        candidates.setdefault(ym, url)
    live: dict[str, str] = {}
    for ym, url in candidates.items():
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA}, method="HEAD")
            if urllib.request.urlopen(req, timeout=8).status == 200:
                live[ym] = url
        except Exception:
            continue
    _ism_pdf_cache = live
    return live


def _parse_ism_pdf(pdf_bytes: bytes, row_name: str) -> float | None:
    """Extract a sub-index's current-month value from an ISM Manufacturing
    Report on Business PDF. The first page has a table where each row reads:
      RowName  current  prior  +/-X.X  Direction  Rate  Trend
    """
    import pypdf
    from io import BytesIO
    text = pypdf.PdfReader(BytesIO(pdf_bytes)).pages[0].extract_text()
    # Handle straight vs curly apostrophe (Customers' Inventories).
    esc = re.escape(row_name).replace(r"\'", "['’]")
    m = re.search(rf"{esc}\s+(\d+\.\d+)\s+\d+\.\d+\s+[+\-−]?\d+\.\d+", text)
    return float(m.group(1)) if m else None


def ism_subindex(indicator, existing):
    """ISM Manufacturing sub-index. History from DBnomics (5y), live tail
    from ISM's public PDF reports (most recent ~8 months).
    params: {dbn_dataset, pdf_row}.

    PDF tail strategy: always re-fetch the 2 most-recent live months (ISM
    revises prior-month values in the next monthly release; refresh() merges
    with keep='last' so revisions actually land in parquet). Older months are
    only fetched when missing from `existing`. Net cost: ~10s/indicator in
    steady state (2 PDFs) vs. the original always-fetch-all (~50s). Pre-2021
    history comes from `scripts/backfill_ism_subindices.py`, served from the
    local PDF cache at `data/raw_pdfs/ISM_MFG/` to avoid Wayback dependency."""
    p = indicator.params
    rows: list[tuple[pd.Timestamp, float]] = []

    # DBnomics: ISM/{dbn_dataset}/in (e.g. neword, inventories, prices)
    try:
        url = f"{_DBN_API}/ISM/{p['dbn_dataset']}/in?observations=1"
        docs = json.loads(_http_get(url)).get("series", {}).get("docs", [])
        if docs:
            for per, v in zip(docs[0].get("period", []), docs[0].get("value", [])):
                if v is not None:
                    rows.append((pd.Timestamp(per).replace(day=1), float(v)))
    except Exception as e:
        print(f"  warn: DBnomics ISM/{p['dbn_dataset']} failed ({e})")

    # Always re-fetch the 2 most-recent live months (captures ISM's
    # next-month revisions of prior values; refresh() keep='last' preserves).
    # Older live months: only fetch if not already in parquet/DBnomics.
    have = set()
    if existing is not None and not existing.empty:
        have |= set(existing.index.strftime("%Y-%m"))
    have |= {d.strftime("%Y-%m") for d, _ in rows}

    live = _ism_pdf_urls()
    pdf_months = sorted(live, reverse=True)
    fetch = set(pdf_months[:2])
    fetch.update(ym for ym in pdf_months[2:] if ym not in have)
    for ym in sorted(fetch):
        try:
            v = _parse_ism_pdf(_download(live[ym]), p["pdf_row"])
            if v is not None:
                rows.append((pd.Timestamp(ym + "-01"), v))
        except Exception as e:
            print(f"  warn: ISM PDF {ym} failed: {e}")

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["date", "value"])
    df = df.drop_duplicates("date", keep="last").set_index("date").sort_index()
    df.index.name = "date"
    return df


# ---------- remote XLSX (Atlanta Fed, NY Fed SCE) ----------

def _download(url: str, headers: dict | None = None) -> bytes:
    hdrs = {"User-Agent": _UA}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    return urllib.request.urlopen(req, timeout=60).read()


def atlanta_fed_wage(indicator, existing):
    """Atlanta Fed Wage Growth Tracker.
    Excel from atlantafed.org; sheet=data_overall.
    Layout: row 0=source, row 1=column headers (col 0='', col 1='Overall', ...),
    row 2+=data (col 0=date, col 1=Overall 3-mma YoY%, col 2=Services, ...).
    Headline = 'Overall' column (3-month moving average)."""
    from io import BytesIO
    url = indicator.params.get("url",
        "https://www.atlantafed.org/-/media/Project/Atlanta/FRBA/Documents/"
        "datafiles/chcs/wage-growth-tracker/wage-growth-data.xlsx")
    data = _download(url)
    df = pd.read_excel(BytesIO(data), sheet_name="data_overall", header=1)
    col_date = df.columns[0]
    col_value = "Overall"
    out = df[[col_date, col_value]].copy()
    out.columns = ["date", "value"]
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out["value"] = pd.to_numeric(out["value"], errors="coerce")
    out = out.dropna().set_index("date").sort_index()
    out.index = out.index.normalize()
    return out


def nyfed_sce(indicator, existing):
    """NY Fed Survey of Consumer Expectations — inflation expectations.
    params: {column}. column is one of:
      'Median one-year ahead expected inflation rate'
      'Median three-year ahead expected inflation rate'
    Excel sheet 'Inflation expectations', headers at row 3, date col uses
    YYYYMM integer format."""
    from io import BytesIO
    url = ("https://www.newyorkfed.org/medialibrary/interactives/sce/sce/"
           "downloads/data/FRBNY-SCE-Data.xlsx")
    data = _download(url)
    df = pd.read_excel(BytesIO(data), sheet_name="Inflation expectations", header=3)
    col_date = df.columns[0]
    col_value = indicator.params.get("column",
                                     "Median one-year ahead expected inflation rate")
    out = df[[col_date, col_value]].copy()
    out.columns = ["date", "value"]
    out["date"] = pd.to_datetime(out["date"].astype(str), format="%Y%m", errors="coerce")
    out["value"] = pd.to_numeric(out["value"], errors="coerce")
    out = out.dropna().set_index("date").sort_index()
    out.index = out.index.normalize()
    return out


def bls_ntrr(indicator, existing):
    """BLS New Tenant Rent Index (quarterly). Headline = NTR_4qtr_Change (YoY %).
    BLS blocks direct curl, so we fetch through web.archive.org (Wayback).
    Latest known file is hardcoded; update params['file'] when BLS resumes."""
    from io import BytesIO
    # As of 2026-05, BLS paused publication after 2025q1 (Oct 2025 CPI Housing
    # Survey data was lost due to govt shutdown). Most recent file:
    file = indicator.params.get("file", "newtenantrentindex2025q1.xlsx")
    direct = f"https://www.bls.gov/pir/ntr/{file}"
    # Wayback "if_" suffix returns raw bytes (no archive toolbar wrap):
    wayback = f"https://web.archive.org/web/2026if_/{direct}"
    try:
        data = _download(direct)
    except Exception:
        data = _download(wayback)
    df = pd.read_excel(BytesIO(data), sheet_name="Data", header=1)
    col_date = df.columns[0]   # "date" e.g. "2024q3"
    col_value = "NTR_4qtr_Change"  # YoY % change
    out = df[[col_date, col_value]].copy()
    out.columns = ["date", "value"]
    # parse "YYYYqQ" -> end of quarter
    out["date"] = pd.PeriodIndex(out["date"].astype(str), freq="Q").to_timestamp(how="end").normalize()
    out["value"] = pd.to_numeric(out["value"], errors="coerce")
    out = out.dropna().set_index("date").sort_index()
    return out


# ---------- Fed Board FCI-G (Financial Conditions Impulse on Growth) ----------

def fed_fci_g(indicator, existing):
    """Federal Reserve Board Financial Conditions Impulse on Growth.
    params: {variant} — one of 'monthly_3yr'/'monthly_1yr'/'quarterly_3yr'/'quarterly_1yr'.
    CSV with date + FCI-G index baseline + 7 component contributions."""
    from io import StringIO
    variant = indicator.params.get("variant", "monthly_3yr")
    url = (f"https://www.federalreserve.gov/econres/notes/feds-notes/"
           f"fci_g_public_{variant}.csv")
    text = _download(url).decode("utf-8", errors="ignore")
    df = pd.read_csv(StringIO(text))
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    # Headline = first numeric column ("FCI-G Index (baseline)" or "...one-year lookback")
    val_col = df.columns[1]
    out = df[["date", val_col]].copy()
    out.columns = ["date", "value"]
    out = out.dropna().set_index("date").sort_index()
    return out


# ---------- OECD SDMX (CLI + diffusion) ----------

def oecd_cli_diffusion(indicator, existing):
    """OECD Composite Leading Indicator — month-over-month diffusion across
    the 17 individual economies returned by the SDMX query: % whose CLI rose
    m/m. Uses OECD SDMX REST CSV (no API key).

    Matches MacroMicro's chart (denominator 17 economies = G7 + Australia +
    Korea + Mexico + Spain + Turkey + BRICS+IDN). A5M is OECD's "5 Major
    Asian Economies" aggregate and must be excluded too — it's 3 chars so
    the len-3 filter doesn't catch it."""
    from io import StringIO
    url = ("https://sdmx.oecd.org/public/rest/data/"
           "OECD.SDD.STES,DSD_STES@DF_CLI/.M.LI.IX..AA...H/"
           "?startPeriod=2000-01&format=csv")
    text = _download(url).decode("utf-8", errors="ignore")
    df = pd.read_csv(StringIO(text))
    aggregates = {"OECD", "OECDE", "G7", "G20", "G4E", "NAFTA", "A5M",
                  "EU27", "EU27_2020", "EA20", "EA19", "EA17", "EU"}
    df = df[~df["REF_AREA"].isin(aggregates)]
    df = df[df["REF_AREA"].str.len() == 3]
    df["TIME_PERIOD"] = pd.to_datetime(df["TIME_PERIOD"]).dt.to_period("M").dt.to_timestamp()
    wide = df.pivot_table(index="TIME_PERIOD", columns="REF_AREA",
                          values="OBS_VALUE", aggfunc="last")
    diff = wide.diff()
    # Diffusion = share of countries with CLI rising vs prior month
    rising = (diff > 0).sum(axis=1)
    valid = diff.notna().sum(axis=1)
    diffusion = (rising / valid * 100).dropna()
    out = pd.DataFrame({"value": diffusion})
    out.index.name = "date"
    return out


# ---------- BIS Central Bank Policy Rates (cut ratio) ----------

def bis_cb_cut_ratio(indicator, existing):
    """% of central banks whose policy rate decreased vs the prior month.
    BIS publishes ~49 jurisdictions monthly back to 1945+ (per country); we
    compute diffusion across the panel.
    A natural proxy for 'global rate cutting cycle' — monetary transmission
    runs ~9 months, so a high cut ratio today predicts PMI lift next year."""
    from io import StringIO
    url = "https://stats.bis.org/api/v2/data/dataflow/BIS/WS_CBPOL/1.0/M."
    text = _download(url, headers={
        "Accept": "application/vnd.sdmx.data+csv;version=1.0.0"
    }).decode("utf-8", errors="ignore")
    df = pd.read_csv(StringIO(text), usecols=["REF_AREA", "TIME_PERIOD", "OBS_VALUE"])
    df = df.dropna(subset=["OBS_VALUE"])
    df["date"] = pd.to_datetime(df["TIME_PERIOD"]).dt.to_period("M").dt.to_timestamp()
    wide = df.pivot_table(index="date", columns="REF_AREA",
                          values="OBS_VALUE", aggfunc="last")
    diff = wide.diff()
    cutting = (diff < 0).sum(axis=1)
    valid   = diff.notna().sum(axis=1)
    # Need a meaningful panel — require ≥5 banks reporting changes that month
    ratio = (cutting / valid * 100).where(valid >= 5).dropna()
    out = pd.DataFrame({"value": ratio})
    out.index.name = "date"
    return out


# ---------- Binance BTCUSDT (spot, daily klines) ----------

def binance_btcusdt(indicator, existing):
    """Binance BTCUSDT daily OHLCV via public klines API.

    Used over yfinance BTC-USD because the research repo (`augury_bakup`)
    fitted the temperature strategy on Binance data — switching the source
    is what makes backtest numbers match the published table. First daily
    bar is 2017-08-17 UTC. No auth required. Crypto has no Adj Close, so we
    set it equal to Close for schema compatibility with the yfinance fetchers."""
    if existing is not None and not existing.empty:
        start_ms = int(
            (existing.index[-1] + timedelta(days=1)).tz_localize("UTC").timestamp() * 1000
        )
    else:
        start_ms = int(pd.Timestamp("2017-08-17", tz="UTC").timestamp() * 1000)

    today_ms = int(pd.Timestamp(date.today(), tz="UTC").timestamp() * 1000)
    if start_ms >= today_ms:
        return pd.DataFrame()

    rows: list = []
    cursor = start_ms
    while cursor < today_ms:
        url = ("https://api.binance.com/api/v3/klines"
               f"?symbol=BTCUSDT&interval=1d&startTime={cursor}&limit=1000")
        req = urllib.request.Request(url, headers=_BROWSER_HEADERS)
        with urllib.request.urlopen(req, timeout=20) as r:
            batch = json.load(r)
        if not batch:
            break
        rows.extend(batch)
        cursor = batch[-1][0] + 86_400_000  # advance past last bar's open_time
        if len(batch) < 1000:
            break

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades",
        "taker_base", "taker_quote", "ignore",
    ])
    df["date"] = (
        pd.to_datetime(df["open_time"], unit="ms", utc=True)
          .dt.tz_localize(None).dt.normalize()
    )
    df = df.set_index("date")
    out = df[["open", "high", "low", "close", "volume"]].astype(float)
    out.columns = ["Open", "High", "Low", "Close", "Volume"]
    out["Adj Close"] = out["Close"]
    out = out[["Adj Close", "Close", "High", "Low", "Open", "Volume"]]
    # Drop today's still-forming bar (Binance returns it as soon as 00:00 UTC).
    out = out[out.index < pd.Timestamp(date.today())]
    return out


# ---------- Copper / Gold ratio (composite of two yfinance futures) ----------

def copper_gold(indicator, existing):
    """Copper/Gold ratio (HG=F / GC=F) × 100 to match the conventional
    0.15-0.30 reading range. Pulled together via yfinance multi-download
    rather than going through the indicator registry — the ratio is the
    only thing we ever use, so storing the raw legs is overhead."""
    if existing is not None and not existing.empty:
        start = (existing.index[-1] + timedelta(days=1)).strftime("%Y-%m-%d")
    else:
        start = "2000-01-01"
    today_str = date.today().strftime("%Y-%m-%d")
    if start >= today_str:
        return pd.DataFrame()
    df = yf.download(["HG=F", "GC=F"], start=start, auto_adjust=False, progress=False)
    if df is None or df.empty or "Close" not in df.columns.get_level_values(0):
        return pd.DataFrame()
    closes = df["Close"]
    if "HG=F" not in closes.columns or "GC=F" not in closes.columns:
        return pd.DataFrame()
    ratio = (closes["HG=F"] / closes["GC=F"]) * 100
    ratio = ratio.dropna()
    if ratio.empty:
        return pd.DataFrame()
    ratio.index = pd.to_datetime(ratio.index).tz_localize(None).normalize()
    out = pd.DataFrame({"value": ratio})
    out.index.name = "date"
    return out


# ---------- CNN Fear & Greed Index ----------

def cnn_fear_greed(indicator, existing):
    """Fetches CNN's Fear & Greed history JSON. Always returns full
    available history (≈ 1 year of daily points) — small payload, simpler
    than incremental fetch. UA + Referer + Origin are mandatory; without
    them CNN returns HTTP 418 "I'm a teapot. You're a bot.\""""
    url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
    req = urllib.request.Request(url, headers={
        **_BROWSER_HEADERS,
        "Referer": "https://edition.cnn.com/markets/fear-and-greed",
        "Origin": "https://edition.cnn.com",
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        payload = json.load(r)
    rows = payload["fear_and_greed_historical"]["data"]
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["x"], unit="ms").dt.normalize()
    out = df.set_index("date")[["y"]].rename(columns={"y": "value"})
    out.index.name = "date"
    return out


# ---------- NAAIM Exposure Index ----------
# The NAAIM site exposes a public Excel of all weekly readings since 2006
# behind a fixed URL. Schema is a single sheet with weeks as rows; the
# headline number is the "NAAIM Number" / "NAAIM Exposure Index" column.

def naaim(indicator, existing):
    # The xlsx URL changes weekly (path includes the upload date), so we
    # scrape the public landing page to find the current one.
    landing = "https://naaim.org/programs/naaim-exposure-index/"
    req = urllib.request.Request(landing, headers=_BROWSER_HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        html = r.read().decode("utf-8", errors="ignore")
    m = re.search(r'https://[^"\s]+\.xlsx', html)
    if not m:
        raise RuntimeError("NAAIM: could not find xlsx link on landing page")
    xlsx_url = m.group(0)
    req2 = urllib.request.Request(xlsx_url, headers=_BROWSER_HEADERS)
    with urllib.request.urlopen(req2, timeout=20) as r:
        raw = r.read()
    df = pd.read_excel(io.BytesIO(raw), engine="openpyxl")
    # Tolerant column resolution — header text occasionally varies.
    date_col = next((c for c in df.columns
                     if "date" in str(c).lower()), df.columns[0])
    val_col = next((c for c in df.columns
                    if "mean" in str(c).lower() and "average" not in str(c).lower()
                    or "naaim number" in str(c).lower()
                    or "exposure" in str(c).lower()),
                   df.columns[1])
    df = df[[date_col, val_col]].copy()
    df.columns = ["date", "value"]
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date", "value"]).sort_values("date")
    out = df.set_index("date")
    out.index.name = "date"
    return out


# ---------- composites ----------

def composite(indicator, deps):
    """Apply indicator.params['op'] over dep series (read from each dep's value_column)."""
    op = indicator.params["op"]
    from . import REGISTRY
    series = []
    for d in indicator.deps:
        df = deps[d]
        if df is None or df.empty:
            return pd.DataFrame()
        col = REGISTRY[d].value_column
        series.append(df[col])
    if op == "sub":
        result = series[0] - series[1]
    elif op == "add":
        result = sum(series[1:], series[0])
    elif op == "weighted":
        weights = indicator.params["weights"]
        result = sum((s * w for s, w in zip(series[1:], weights[1:])), series[0] * weights[0])
    else:
        raise ValueError(f"unknown composite op: {op}")
    new = pd.DataFrame({"value": result.dropna()})
    new.index.name = "date"
    return new


