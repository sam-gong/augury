"""All indicator declarations. One place to add/remove/modify data series.

Convention: use FRED series IDs as-is for FRED-sourced data (e.g. "CPIAUCSL")
so the source_url and id stay aligned. Use friendly short IDs for scraped or
yfinance series ("SPX", "ISM_PMI")."""
from . import register, Indicator
from . import fetchers as f


def _fred(id, title, **kw):
    return register(Indicator(
        id=id, title=title, fetcher=f.fred,
        source_url=f"https://fred.stlouisfed.org/series/{id}",
        **kw,
    ))


def _price(id, ticker, title, **kw):
    return register(Indicator(
        id=id, title=title, fetcher=f.yahoo,
        source_url=f"https://finance.yahoo.com/quote/{ticker}",
        frequency="daily", value_column="Close",
        params={"ticker": ticker}, **kw,
    ))


# ---------- Prices (yfinance) ----------
# Indices / macro
_price("SPX",   "^GSPC",   "S&P 500",          unit="$")
_price("NDX",   "^NDX",    "Nasdaq 100",       unit="$")
# Tradeable proxies for the index strategies (T+1 open execution needs a
# real instrument with an open price). SPY since 1993, QQQ since 1999;
# total-return (includes dividend reinvestment) so the equity curve reflects
# what an investor actually earns.
_price("SPY",   "SPY",     "S&P 500 ETF",      unit="$")
_price("QQQ",   "QQQ",     "Nasdaq 100 ETF",   unit="$")
_price("MOVE",  "^MOVE",   "ICE BofA MOVE",    unit="Index")  # 2002-11+ (yfinance hard limit; pre-2002 requires paid ICE/Bloomberg feed)
_price("VIX3M", "^VIX3M",  "VIX 3-Month",      unit="Index")

# CBOE direct: VIX/SKEW back to 1990-01 (yfinance ^VIX/^SKEW only cover 2000+).
def _cboe(id, slug, title):
    return register(Indicator(
        id=id, title=title, fetcher=f.cboe,
        source_url=f"https://www.cboe.com/tradable_products/vix/{slug.lower()}",
        frequency="daily", value_column="Close", unit="Index",
        params={"csv_url": f"https://cdn.cboe.com/api/global/us_indices/"
                           f"daily_prices/{slug}_History.csv"},
    ))
_cboe("VIX",  "VIX",  "VIX")
_cboe("SKEW", "SKEW", "CBOE SKEW Index")
_price("US10Y", "^TNX",    "US 10Y Yield",     unit="%")
register(Indicator(
    id="BTC", title="Bitcoin / USD",
    fetcher=f.binance_btcusdt,
    source_url="https://www.binance.com/en/trade/BTC_USDT",
    frequency="daily", value_column="Close", unit="$",
))

# Real positions
_price("NVDA",  "NVDA",    "NVIDIA",           unit="$")
_price("TSLA",  "TSLA",    "Tesla",            unit="$")
# Substitute assets — used by hybrid strategies that park capital in
# defensive names when the primary strategy goes flat.
_price("AZO",   "AZO",     "AutoZone",         unit="$")
_price("ORLY",  "ORLY",    "O'Reilly Auto",    unit="$")

# Learning positions
_price("FICO",  "FICO",    "Fair Isaac",       unit="$")
_price("META",  "META",    "Meta",             unit="$")
_price("RKLB",  "RKLB",    "Rocket Lab",       unit="$")
_price("PLTR",  "PLTR",    "Palantir",         unit="$")
_price("LLY",   "LLY",     "Eli Lilly",        unit="$")
_price("NFLX",  "NFLX",    "Netflix",          unit="$")
_price("APP",   "APP",     "AppLovin",         unit="$")
_price("AAPL",  "AAPL",    "Apple",            unit="$")

# ---------- Sentiment ----------
register(Indicator(
    id="COPPER_GOLD", title="Copper/Gold Ratio (×100)",
    fetcher=f.copper_gold,
    source_url="https://finance.yahoo.com/quote/HG=F",
    frequency="daily", unit="Ratio",
))
register(Indicator(
    id="CNN_FEAR_GREED", title="CNN Fear & Greed",
    fetcher=f.cnn_fear_greed,
    source_url="https://edition.cnn.com/markets/fear-and-greed",
    frequency="daily", unit="Index",
))
register(Indicator(
    id="NAAIM_EXPOSURE", title="NAAIM Exposure Index",
    fetcher=f.naaim,
    source_url="https://www.naaim.org/programs/naaim-exposure-index/",
    frequency="weekly", unit="%",
))


# Market breadth (NDFI / S5FI = % constituents > 50DMA). Not on yfinance;
# we compute it ourselves from Wikipedia's current constituent lists.
register(Indicator(
    id="NDFI", title="Nasdaq 100 % above 50DMA",
    source_url="https://stockcharts.com/h-sc/ui?s=$NDFI",
    frequency="daily", fetcher=f.breadth_above_ma, unit="%",
    params={"universe": "ndx", "ma": 50, "start": "2010-01-01"},
))
register(Indicator(
    id="S5FI", title="S&P 500 % above 50DMA",
    source_url="https://stockcharts.com/h-sc/ui?s=$S5FI",
    frequency="daily", fetcher=f.breadth_above_ma, unit="%",
    params={"universe": "spx", "ma": 50, "start": "2010-01-01"},
))


# ---------- ISM (jin10 bulk history + investing.com live tail) ----------
_ISM_PMI_URL = "https://www.investing.com/economic-calendar/ism-manufacturing-pmi-173"
_ISM_NMI_URL = "https://www.investing.com/economic-calendar/ism-non-manufacturing-pmi-176"
register(Indicator(
    id="ISM_PMI", title="ISM Manufacturing PMI",
    source_url=_ISM_PMI_URL,
    frequency="monthly", is_rate=True, fetcher=f.ism_calendar,
    params={"jin10_attr_id": 28, "investing_url": _ISM_PMI_URL},
))
register(Indicator(
    id="ISM_NMI", title="ISM Services PMI (NMI)",
    source_url=_ISM_NMI_URL,
    frequency="monthly", is_rate=True, fetcher=f.ism_calendar,
    params={"jin10_attr_id": 29, "investing_url": _ISM_NMI_URL},
))


# ---------- ISM Manufacturing sub-indices ----------
# DBnomics gives 2021-01 → ~5y history (capped at 60 obs), then ISM's own
# public PDF reports fill the live tail (~8 most-recent months). PDF parser
# pulls each sub-index row from the first-page table.
def _ism_sub(id, title, dbn_dataset, pdf_row):
    return register(Indicator(
        id=id, title=title, fetcher=f.ism_subindex,
        source_url="https://www.ismworld.org/supply-management-news-and-reports/"
                   "reports/ism-pmi-reports/",
        frequency="monthly", is_rate=True,
        params={"dbn_dataset": dbn_dataset, "pdf_row": pdf_row},
    ))
_ism_sub("ISM_NEW_ORDERS",  "ISM Manufacturing New Orders",  "neword",      "New Orders")
_ism_sub("ISM_INVENTORIES", "ISM Manufacturing Inventories", "inventories", "Inventories")
_ism_sub("ISM_MFG_PRICES",  "ISM Manufacturing Prices",      "prices",      "Prices")


# ---------- Philly Fed regional manufacturing survey ----------
_fred("GACDFSA066MSFRBPHI", "Philly Fed Current Activity",   frequency="monthly", is_rate=True)
_fred("GAFDFSA066MSFRBPHI", "Philly Fed Future Activity 6m", frequency="monthly", is_rate=True)


# ---------- Activity / cycle ----------
_fred("CFNAI",  "Chicago Fed Activity Index", frequency="monthly", is_rate=True)
_fred("INDPRO", "Industrial Production",      frequency="monthly", unit="Index")
_fred("NFCI",   "Chicago Fed NFCI",           frequency="weekly",  is_rate=True)

# Fed Board FCI-G — Financial Conditions Impulse on Growth, 1-year lookback.
# This is macromicro's "FCI-G-1年期" variant. The 1yr lookback measures the
# *impulse* (change in financial conditions over the past year) and is what
# 宏观框架 / Lee22 uses. Monthly since 1990.
register(Indicator(
    id="FCI_G_1Y",
    title="Fed FCI-G (1yr lookback)",
    source_url="https://www.federalreserve.gov/econres/notes/feds-notes/"
               "a-new-index-to-measure-us-financial-conditions-20230630.html",
    frequency="monthly", is_rate=True, fetcher=f.fed_fci_g,
    params={"variant": "monthly_1yr"},
))

# BIS Total Credit to Private Non-Financial Sector for China (quarterly,
# 1985+). Used as a proxy/component for "China Credit Impulse" since the
# headline credit impulse needs custom calc from PBoC TSF; this BIS series
# is the cleanest free aggregate available on FRED.
_fred("CRDQCNAPABIS",
      "China Total Credit to Private NFCs (BIS, proxy for Credit Impulse)",
      frequency="quarterly", unit="Index")


# ---------- Rates ----------
_fred("FEDFUNDS", "Fed Funds Rate", frequency="monthly", unit="%", is_rate=True)
_fred("DGS2",     "2Y Treasury",    frequency="daily",   unit="%", is_rate=True)
_fred("DGS10",    "10Y Treasury",   frequency="daily",   unit="%", is_rate=True)
_fred("T10Y2Y",   "10Y-2Y Spread",  frequency="daily",   unit="%", is_rate=True)
# FRED truncated this series to a rolling 3-year window in April 2026 (licensing
# change with ICE BofA). We restore full 1996+ history from a Wayback snapshot
# of the public CSV (captured 2025-11), then let FRED fill the tail.
register(Indicator(
    id="BAMLH0A0HYM2", title="ICE BofA US High Yield OAS",
    source_url="https://fred.stlouisfed.org/series/BAMLH0A0HYM2",
    frequency="daily", unit="%", is_rate=True,
    fetcher=f.fred_with_wayback_backfill,
    params={
        "wayback_url": "https://web.archive.org/web/20251104204105if_/"
                       "https://fred.stlouisfed.org/graph/fredgraph.csv?id=BAMLH0A0HYM2",
        "backfill_until": "2000-01-01",
    },
))


# ---------- Liquidity ----------
_fred("M2SL",      "M2 Money Stock",         frequency="monthly", unit="$B")
_fred("WALCL",     "Fed Balance Sheet",      frequency="weekly",  unit="$M")
_fred("WTREGEN",   "Treasury General Acct",  frequency="weekly",  unit="$M")
_fred("RRPONTSYD", "Overnight Reverse Repo", frequency="daily",   unit="$B")


# ---------- Inflation ----------
_fred("CPIAUCSL",      "CPI All Items",   frequency="monthly", unit="Index")
_fred("CPILFESL",      "Core CPI",        frequency="monthly", unit="Index")
_fred("PCEPILFE",      "Core PCE",        frequency="monthly", unit="Index")
_fred("PPIACO",        "PPI All Commodities",   frequency="monthly", unit="Index")
_fred("IR",            "Import Price Index",              frequency="monthly", unit="Index")
_fred("CSUSHPINSA",    "Case-Shiller US National HPI (NSA)", frequency="monthly", unit="Index")
_fred("CUSR0000SAH1",  "CPI Shelter",      frequency="monthly", unit="Index")
_fred("CPIMEDSL",      "CPI Medical Care",            frequency="monthly", unit="Index")
_fred("T5YIE",         "5Y Breakeven Inflation",  frequency="daily",   unit="%", is_rate=True)
_fred("T10YIE",        "10Y Breakeven Inflation", frequency="daily",   unit="%", is_rate=True)
_fred("MICH",          "UMich 1Y Inflation Expectation", frequency="monthly", unit="%", is_rate=True)


# ---------- Employment ----------
_fred("UNRATE",  "Unemployment Rate",         frequency="monthly", unit="%", is_rate=True)
_fred("ICSA",    "Initial Claims",            frequency="weekly",  unit="K")
_fred("PAYEMS",  "Nonfarm Payrolls",          frequency="monthly", unit="K")
_fred("JTSJOL",  "JOLTS Job Openings",        frequency="monthly", unit="K")
_fred("JTSQUR",  "JOLTS Quits Rate",          frequency="monthly", unit="%", is_rate=True)
_fred("CIVPART", "Labor Force Participation", frequency="monthly", unit="%", is_rate=True)
_fred("CES0500000003", "Average Hourly Earnings (Production/Nonsupervisory)", frequency="monthly", unit="$")

# Atlanta Fed Wage Growth Tracker — Excel from atlantafed.org (not on FRED/DBnomics).
register(Indicator(
    id="ATLANTA_FED_WAGE_TRACKER",
    title="Atlanta Fed Wage Growth Tracker (3mma Overall)",
    source_url="https://www.atlantafed.org/research-and-data/data/wage-growth-tracker",
    frequency="monthly", unit="%", is_rate=True, fetcher=f.atlanta_fed_wage,
))


# ---------- Non-FRED inflation sources ----------

# BLS New Tenant Rent Index — quarterly Excel; BLS blocks direct curl, fetched
# via Wayback. Paused April 2026 (most recent file: 2025q1).
register(Indicator(
    id="BLS_NEW_TENANT_RENT",
    title="BLS New Tenant Rent Index (YoY %)",
    source_url="https://www.bls.gov/pir/new-tenant-rent.htm",
    frequency="quarterly", unit="%", is_rate=True, fetcher=f.bls_ntrr,
    params={"file": "newtenantrentindex2025q1.xlsx"},
))

# NY Fed Survey of Consumer Expectations — Median 1-year-ahead inflation.
register(Indicator(
    id="NYFED_INFL_EXP_1Y",
    title="NY Fed SCE 1Y Inflation Expectation (Median)",
    source_url="https://www.newyorkfed.org/microeconomics/sce",
    frequency="monthly", unit="%", is_rate=True, fetcher=f.nyfed_sce,
    params={"column": "Median one-year ahead expected inflation rate"},
))

# Trueflation: 非政府数据,API 付费且占用心智不大。我们在 inflation 页保留一张
# 链接卡引导到官网,不进 REGISTRY、不参与刷新。

# ---------- OECD CLI diffusion (computed across member countries) ----------
register(Indicator(
    id="OECD_CLI_DIFFUSION",
    title="OECD CLI Month-over-Month Diffusion (%)",
    source_url="https://www.oecd.org/en/data/indicators/composite-leading-indicator-cli.html",
    frequency="monthly", unit="%", is_rate=True, fetcher=f.oecd_cli_diffusion,
))


# ---------- Global Central Bank Cut Ratio ----------
# Tried building from BIS CBPOL panel with multiple methodologies (m/m net cut,
# 12m net cut, below 24m peak). All deviate from MacroMicro's chart by 15-25pp
# and sometimes invert at crisis peaks. MM's exact methodology is undocumented.
# Per "宁可没有数据也不能丢失准确度", placeholder for now.
# Fetcher `bis_cb_cut_ratio` kept in fetchers.py for future use.


# ---------- Composites ----------
register(Indicator(
    id="ISM_NOC_MINUS_IVC",
    title="ISM New Orders − Inventories",
    source_url="",
    frequency="monthly", is_rate=True,
    fetcher=f.composite,
    deps=["ISM_NEW_ORDERS", "ISM_INVENTORIES"],
    params={"op": "sub"},
))

# Weighted composite PMI (manufacturing 30% + services 70%, ~GDP share).
# Used for the regime chart's YoY-growth line.
register(Indicator(
    id="ISM_COMPOSITE_PMI",
    title="ISM Composite PMI (30% Mfg + 70% Svc)",
    source_url="",
    frequency="monthly", is_rate=True,
    fetcher=f.composite,
    deps=["ISM_PMI", "ISM_NMI"],
    params={"op": "weighted", "weights": [0.3, 0.7]},
))

