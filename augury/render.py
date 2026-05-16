"""Render Jinja2 templates with inline Plotly charts -> docs/*.html."""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import plotly.graph_objects as go
from jinja2 import Environment, FileSystemLoader, select_autoescape

from augury import data, indicators

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
DOCS_DIR = ROOT / "docs"

env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)


def _price_chart(df, title: str) -> str:
    df = df.tail(252)  # ~1 trading year
    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=df.index, open=df["Open"], high=df["High"],
        low=df["Low"], close=df["Close"], name=title,
        increasing_line_color="#22c55e", decreasing_line_color="#ef4444",
    ))
    fig.add_trace(go.Scatter(
        x=df.index, y=indicators.sma(df["Close"], 50),
        name="MA50", line=dict(color="#3b82f6", width=1),
    ))
    fig.add_trace(go.Scatter(
        x=df.index, y=indicators.sma(df["Close"], 200),
        name="MA200", line=dict(color="#f59e0b", width=1),
    ))
    fig.update_layout(
        template="plotly_dark",
        title=title,
        height=400,
        margin=dict(l=40, r=20, t=40, b=30),
        xaxis_rangeslider_visible=False,
        paper_bgcolor="#111",
        plot_bgcolor="#111",
        legend=dict(orientation="h", y=1.02, x=0),
    )
    return fig.to_html(include_plotlyjs="cdn", full_html=False)


def _summary(prices: dict) -> dict:
    s: dict = {}
    for sym in ("SPX", "NDX"):
        df = prices.get(sym)
        if df is None or df.empty:
            continue
        close = df["Close"]
        ma200 = indicators.sma(close, 200).iloc[-1]
        last = float(close.iloc[-1])
        pct = (last / ma200 - 1) * 100
        s[sym] = {"last": last, "vs_ma200": pct, "above": last > ma200}
    if (vix := prices.get("VIX")) is not None and not vix.empty:
        s["VIX"] = {"last": float(vix["Close"].iloc[-1])}
    if (yld := prices.get("US10Y")) is not None and not yld.empty:
        s["US10Y"] = {"last": float(yld["Close"].iloc[-1])}
    return s


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def render_index(prices: dict) -> None:
    charts = {}
    for sym in ("SPX", "NDX"):
        df = prices.get(sym)
        if df is not None and not df.empty:
            charts[sym] = _price_chart(df, sym)
    html = env.get_template("index.html").render(
        charts=charts, summary=_summary(prices),
        updated=_now(), page="index",
    )
    (DOCS_DIR / "index.html").write_text(html)


def render_macro_stub() -> None:
    html = env.get_template("macro.html").render(updated=_now(), page="macro")
    (DOCS_DIR / "macro.html").write_text(html)


def render_strategies_stub() -> None:
    html = env.get_template("strategies.html").render(updated=_now(), page="strategies")
    (DOCS_DIR / "strategies.html").write_text(html)


def all() -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    prices = {sym: data.load_prices(sym) for sym in data.PRICE_SYMBOLS}
    render_index(prices)
    render_strategies_stub()
    render_macro_stub()
