"""Render Jinja2 templates with inline Plotly charts -> docs/*.html."""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
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


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ---------- chart helpers ----------

def _price_chart(df: pd.DataFrame, title: str) -> str:
    df = df.tail(252)  # ~1 trading year
    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=df.index, open=df["Open"], high=df["High"],
        low=df["Low"], close=df["Close"], name=title,
        increasing_line_color="#22c55e", decreasing_line_color="#ef4444",
    ))
    fig.add_trace(go.Scatter(x=df.index, y=indicators.sma(df["Close"], 50),
                             name="MA50", line=dict(color="#3b82f6", width=1)))
    fig.add_trace(go.Scatter(x=df.index, y=indicators.sma(df["Close"], 200),
                             name="MA200", line=dict(color="#f59e0b", width=1)))
    fig.update_layout(
        template="plotly_dark", title=title, height=400,
        margin=dict(l=40, r=20, t=40, b=30),
        xaxis_rangeslider_visible=False,
        paper_bgcolor="#111", plot_bgcolor="#111",
        legend=dict(orientation="h", y=1.02, x=0),
    )
    return fig.to_html(include_plotlyjs=False, full_html=False)


def _sparkline(s: pd.Series, color: str = "#3b82f6") -> str:
    cutoff = s.index[-1] - pd.Timedelta(days=730)
    s = s[s.index >= cutoff]
    fig = go.Figure(go.Scatter(x=s.index, y=s, mode="lines",
                               line=dict(color=color, width=1.5),
                               hovertemplate="%{x|%Y-%m-%d}: %{y:.2f}<extra></extra>"))
    fig.update_layout(
        height=60, margin=dict(l=0, r=0, t=0, b=0),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
        xaxis=dict(visible=False), yaxis=dict(visible=False),
    )
    return fig.to_html(include_plotlyjs=False, full_html=False,
                       config={"displayModeBar": False})


def _overlay_chart(pmi: pd.Series, leader: pd.Series, lead_months: int,
                   leader_name: str, invert: bool = False) -> str:
    """PMI on left axis; leader shifted forward N months on right axis.
    Anything to the right of "today" on the leader line = the prediction."""
    leader_s = -leader if invert else leader.copy()
    leader_s = leader_s.copy()
    leader_s.index = leader_s.index + pd.DateOffset(months=lead_months)

    cutoff = pd.Timestamp.now() - pd.DateOffset(years=8)
    pmi_t = pmi[pmi.index >= cutoff]
    leader_t = leader_s[leader_s.index >= cutoff]
    today = pd.Timestamp.now().normalize()

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=pmi_t.index, y=pmi_t.values, name="PMI proxy",
        line=dict(color="#3b82f6", width=2), yaxis="y1",
    ))
    fig.add_trace(go.Scatter(
        x=leader_t.index, y=leader_t.values,
        name=f"{leader_name} +{lead_months}m",
        line=dict(color="#f59e0b", width=1.5, dash="dot"),
        yaxis="y2",
    ))
    fig.add_vline(x=today.isoformat(), line_dash="dash", line_color="#888")
    fig.add_annotation(x=today, y=1, yref="paper", xref="x",
                       text="today", showarrow=False,
                       font=dict(color="#888", size=10),
                       xanchor="left", yanchor="top", xshift=4)
    fig.update_layout(
        template="plotly_dark", height=320,
        margin=dict(l=40, r=50, t=40, b=30),
        paper_bgcolor="#111", plot_bgcolor="#111",
        yaxis=dict(title="PMI", side="left"),
        yaxis2=dict(title=leader_name, side="right", overlaying="y", showgrid=False),
        legend=dict(orientation="h", y=1.10, x=0),
    )
    return fig.to_html(include_plotlyjs=False, full_html=False,
                       config={"displayModeBar": False})


# ---------- formatters ----------

def _format_value(value: float, unit: str) -> str:
    if unit == "%":
        return f"{value:.2f}%"
    if unit == "Index":
        return f"{value:.1f}"
    if unit.startswith("$"):
        scale = 1e9 if unit == "$B" else 1e6
        abs_v = abs(value) * scale
        if abs_v >= 1e12:
            return f"${value * scale / 1e12:.2f}T"
        if abs_v >= 1e9:
            return f"${value * scale / 1e9:.1f}B"
        return f"${value * scale / 1e6:.1f}M"
    if unit == "K":
        if abs(value) >= 1000:
            return f"{value/1000:.2f}M"
        return f"{value:.1f}K"
    if abs(value) >= 1e6:
        return f"{value/1e6:.2f}M"
    if abs(value) >= 1e3:
        return f"{value/1e3:.1f}K"
    return f"{value:.2f}"


def _yoy(s: pd.Series, is_rate: bool) -> tuple[float | None, str]:
    if len(s) < 2:
        return None, ""
    cutoff = s.index[-1] - pd.Timedelta(days=365)
    prior = s[s.index <= cutoff]
    if prior.empty:
        return None, ""
    current = float(s.iloc[-1])
    year_ago = float(prior.iloc[-1])
    if is_rate:
        diff = current - year_ago
        return diff, f"{diff:+.2f}pp"
    if year_ago == 0:
        return None, ""
    pct = (current / year_ago - 1) * 100
    return pct, f"{pct:+.1f}%"


# ---------- card builder (used by position + background zones) ----------

def _build_card(series_id: str) -> dict | None:
    meta = data.MACRO_SERIES.get(series_id)
    if not meta:
        return None
    df = data.load_macro(series_id)
    if df is None or df.empty:
        return None
    s = df["value"]
    current = float(s.iloc[-1])
    change, change_label = _yoy(s, meta["is_rate"])
    return {
        "series_id": series_id,
        "title": meta["title"],
        "current": _format_value(current, meta["unit"]),
        "change_label": change_label,
        "change_pos": (change or 0) >= 0,
        "sparkline": _sparkline(s, color="#22c55e" if (change or 0) >= 0 else "#ef4444"),
        "last_date": s.index[-1].strftime("%Y-%m-%d"),
    }


# ---------- page renderers ----------

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


def _load_leader_series(chart_cfg: dict) -> pd.Series | None:
    """Load the leader series for an overlay chart. Supports computed series."""
    if "computed" in chart_cfg:
        c = chart_cfg["computed"]
        a = data.load_macro(c["a"])
        b = data.load_macro(c["b"])
        if a is None or b is None:
            return None
        a_s, b_s = a["value"], b["value"]
        if c["op"] == "sub":
            # Align on common index
            joined = pd.concat([a_s.rename("a"), b_s.rename("b")], axis=1).dropna()
            return joined["a"] - joined["b"]
        return None
    df = data.load_macro(chart_cfg["leader_id"])
    return df["value"] if df is not None else None


def render_macro() -> None:
    layout = data.MACRO_LAYOUT

    # Zone 1: signal — build overlay charts
    pmi_df = data.load_macro(layout["signal"]["pmi_proxy"])
    pmi_s = pmi_df["value"] if pmi_df is not None else pd.Series(dtype=float)
    signal_charts = []
    for cfg in layout["signal"]["charts"]:
        leader_s = _load_leader_series(cfg)
        if leader_s is None or leader_s.empty or pmi_s.empty:
            continue
        signal_charts.append({
            "anchor": cfg["anchor"],
            "title": cfg["title"],
            "interp": cfg["interp"],
            "html": _overlay_chart(
                pmi=pmi_s,
                leader=leader_s,
                lead_months=cfg["lead_months"],
                leader_name=cfg["leader_name"],
                invert=cfg.get("invert", False),
            ),
        })

    # Zone 2: position — cards
    position_cards = [c for sid in layout["position"]["cards"]
                      if (c := _build_card(sid)) is not None]

    # Zone 3: background — subsection grids
    background_subs = []
    for sub in layout["background"]["subsections"]:
        cards = [c for sid in sub["ids"] if (c := _build_card(sid)) is not None]
        if cards:
            background_subs.append({"anchor": sub["anchor"], "label": sub["label"], "cards": cards})

    # Sidebar items
    sidebar = [
        {
            "anchor": "signal", "label": layout["signal"]["label"],
            "subitems": [{"anchor": c["anchor"], "label": c["title"]} for c in signal_charts],
        },
        {
            "anchor": "position", "label": layout["position"]["label"],
            "subitems": [],
        },
        {
            "anchor": "background", "label": layout["background"]["label"],
            "subitems": [{"anchor": s["anchor"], "label": s["label"]} for s in background_subs],
        },
    ]

    html = env.get_template("macro.html").render(
        updated=_now(), page="macro",
        sidebar=sidebar,
        signal_zone=layout["signal"], signal_charts=signal_charts,
        position_zone=layout["position"], position_cards=position_cards,
        background_zone=layout["background"], background_subs=background_subs,
    )
    (DOCS_DIR / "macro.html").write_text(html)


def render_strategies_stub() -> None:
    html = env.get_template("strategies.html").render(updated=_now(), page="strategies")
    (DOCS_DIR / "strategies.html").write_text(html)


def all() -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    prices = {sym: data.load_prices(sym) for sym in data.PRICE_SYMBOLS}
    render_index(prices)
    render_macro()
    render_strategies_stub()
