"""Render Jinja2 templates with inline Plotly charts -> docs/*.html.

Pulls data through the indicator registry — never reads parquets directly.
Charts whose data is missing render as placeholders so the skeleton stays
visually complete."""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json
import math
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from jinja2 import Environment, FileSystemLoader, select_autoescape

from augury import indicators, layout, ta, backtest
from augury.strategies import SmaBand, SmaCross

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
DOCS_DIR = ROOT / "docs"

env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _refresh_ctx() -> tuple[str, dict]:
    """`updated` displayed in the header reflects the last *successful refresh
    run*, not render time. When no refresh has happened yet (fresh checkout),
    fall back to render time so the page still has a timestamp."""
    run = indicators.run_meta()
    completed = run.get("completed_at")
    if completed:
        try:
            dt = datetime.strptime(completed, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            return dt.strftime("%Y-%m-%d %H:%M UTC"), run
        except Exception:
            pass
    return _now(), run


# ---------- chart helpers ----------

def _range_selector() -> dict:
    return dict(
        bgcolor="#1f1f1f",
        activecolor="#3b82f6",
        font=dict(color="#e5e5e5", size=10),
        buttons=[
            dict(count=6, label="6m", step="month", stepmode="backward"),
            dict(count=1, label="1y", step="year", stepmode="backward"),
            dict(count=2, label="2y", step="year", stepmode="backward"),
            dict(count=3, label="3y", step="year", stepmode="backward"),
            dict(count=5, label="5y", step="year", stepmode="backward"),
            dict(count=10, label="10y", step="year", stepmode="backward"),
            dict(step="all", label="all"),
        ],
    )


def _price_chart(df: pd.DataFrame, title: str) -> str:
    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=df.index, open=df["Open"], high=df["High"],
        low=df["Low"], close=df["Close"], name=title,
        increasing_line_color="#22c55e", decreasing_line_color="#ef4444",
    ))
    fig.add_trace(go.Scatter(x=df.index, y=ta.sma(df["Close"], 50),
                             name="MA50", line=dict(color="#3b82f6", width=1)))
    fig.add_trace(go.Scatter(x=df.index, y=ta.sma(df["Close"], 200),
                             name="MA200", line=dict(color="#f59e0b", width=1)))
    default_start = df.index[-1] - pd.DateOffset(years=1)
    fig.update_layout(
        template="plotly_dark", title=title, height=400,
        margin=dict(l=40, r=20, t=60, b=30),
        xaxis_rangeslider_visible=False,
        paper_bgcolor="#111", plot_bgcolor="#111",
        legend=dict(orientation="h", y=1.10, x=0),
        hovermode="x unified",
        xaxis=dict(
            rangeselector=_range_selector(),
            range=[default_start.isoformat(), df.index[-1].isoformat()],
            dtick="M12", tickformat="%Y",
        ),
    )
    return fig.to_html(include_plotlyjs=False, full_html=False,
                       config={"responsive": True})


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
                       config={"displayModeBar": False, "responsive": True})


def _apply_transform(s: pd.Series, transform: str | None, is_rate: bool) -> pd.Series:
    """Apply a value transform before plotting. 'yoy' = 12-month change
    (% for indices, pp diff for rates). None = raw."""
    if transform is None:
        return s
    if transform == "yoy":
        if is_rate:
            return s - s.shift(12)
        return s.pct_change(periods=12) * 100
    raise ValueError(f"unknown transform: {transform}")


def _overlay_chart(main: pd.Series, leader: pd.Series, lead_months: int,
                   main_name: str, leader_name: str,
                   invert: bool = False, invert_main: bool = False) -> str:
    """Generic lead-lag overlay: main on left axis; leader shifted forward N months
    on right axis. Anything right of "today" on the leader = the prediction.

    `invert` negates the leader's values (use for negative-correlation pairs where
    you want the leader's axis to read "normally").
    `invert_main` reverses the left axis direction (visually flips main so an
    inversely-related leader appears to move with it)."""
    leader_s = -leader if invert else leader.copy()
    leader_s = leader_s.copy()
    leader_s.index = leader_s.index + pd.DateOffset(months=lead_months)

    today = pd.Timestamp.now().normalize()
    default_start = today - pd.DateOffset(years=8)
    default_end = today + pd.DateOffset(months=lead_months + 1)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=main.index, y=main.values, name=main_name,
        line=dict(color="#3b82f6", width=2), yaxis="y1",
        hovertemplate="%{x|%Y-%m-%d}: %{y:.2f}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=leader_s.index, y=leader_s.values,
        name=f"{leader_name} +{lead_months}m" if lead_months else leader_name,
        line=dict(color="#f59e0b", width=1.5),
        yaxis="y2",
        hovertemplate="%{x|%Y-%m-%d}: %{y:.2f}<extra></extra>",
    ))
    fig.add_vline(x=today.isoformat(), line_dash="dash", line_color="#888")
    fig.add_annotation(x=today, y=1, yref="paper", xref="x",
                       text="today", showarrow=False,
                       font=dict(color="#888", size=10),
                       xanchor="left", yanchor="top", xshift=4)
    y1 = dict(title=main_name, side="left")
    if invert_main:
        y1["autorange"] = "reversed"
    fig.update_layout(
        template="plotly_dark", height=380,
        margin=dict(l=40, r=50, t=70, b=30),
        paper_bgcolor="#111", plot_bgcolor="#111",
        yaxis=y1,
        yaxis2=dict(title=leader_name, side="right", overlaying="y", showgrid=False),
        legend=dict(orientation="h", y=1.18, x=0),
        hovermode="x unified",
        xaxis=dict(
            rangeselector=_range_selector(),
            range=[default_start.isoformat(), default_end.isoformat()],
            dtick="M12", tickformat="%Y",
            hoverformat="%Y-%m-%d",
        ),
    )
    return fig.to_html(include_plotlyjs=False, full_html=False,
                       config={"displayModeBar": False, "responsive": True})


def _line_chart(s: pd.Series, y_label: str) -> str:
    """Single-series full-width chart (for 'line' kind entries)."""
    today = pd.Timestamp.now().normalize()
    default_start = today - pd.DateOffset(years=10)
    fig = go.Figure(go.Scatter(
        x=s.index, y=s.values, name=y_label,
        line=dict(color="#3b82f6", width=1.5),
        hovertemplate="%{x|%Y-%m-%d}: %{y:.2f}<extra></extra>",
    ))
    fig.update_layout(
        template="plotly_dark", height=300,
        margin=dict(l=40, r=20, t=50, b=30),
        paper_bgcolor="#111", plot_bgcolor="#111",
        showlegend=False,
        hovermode="x unified",
        yaxis=dict(title=y_label),
        xaxis=dict(rangeselector=_range_selector(),
                   range=[default_start.isoformat(), today.isoformat()],
                   hoverformat="%Y-%m-%d"),
    )
    return fig.to_html(include_plotlyjs=False, full_html=False,
                       config={"displayModeBar": False, "responsive": True})


def _lines_chart(items: list[tuple[pd.Series, str, str]], y_label: str,
                 ref_50: bool = False) -> str:
    """Multi-series full-width chart (for 'lines' kind entries).

    Each item is (series, label, color). If ref_50, draws a 50 reference line —
    useful for diffusion indices like PMI/NMI."""
    today = pd.Timestamp.now().normalize()
    default_start = today - pd.DateOffset(years=10)
    fig = go.Figure()
    for s, label, color in items:
        fig.add_trace(go.Scatter(
            x=s.index, y=s.values, name=label,
            line=dict(color=color, width=1.4),
            hovertemplate="%{y:.2f}<extra>" + label + "</extra>",
        ))
    if ref_50:
        fig.add_hline(y=50, line=dict(color="rgba(160,160,160,0.4)",
                                       width=0.8, dash="dot"))
    fig.update_layout(
        template="plotly_dark", height=320,
        margin=dict(l=40, r=20, t=50, b=30),
        paper_bgcolor="#111", plot_bgcolor="#111",
        showlegend=True,
        legend=dict(orientation="h", y=1.08, x=0,
                    font=dict(size=10), bgcolor="rgba(0,0,0,0)"),
        hovermode="x unified",
        yaxis=dict(title=y_label),
        xaxis=dict(rangeselector=_range_selector(),
                   range=[default_start.isoformat(), today.isoformat()],
                   hoverformat="%Y-%m-%d"),
    )
    return fig.to_html(include_plotlyjs=False, full_html=False,
                       config={"displayModeBar": False, "responsive": True})


def _cycle_returns_chart(comp_pmi: pd.Series, spx: pd.Series,
                         btc: pd.Series | None) -> str:
    pmi_yoy = comp_pmi - comp_pmi.shift(12)
    spx_yoy = spx.pct_change(periods=252) * 100

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.04,
        specs=[[{"secondary_y": True}], [{"secondary_y": True}]],
        row_heights=[0.5, 0.5],
    )

    fig.add_trace(go.Scatter(
        x=pmi_yoy.index, y=pmi_yoy.values, name="Composite PMI YoY (pp)",
        line=dict(color="#3b82f6", width=1.5),
        hovertemplate="%{x|%Y-%m-%d}: %{y:.2f}<extra></extra>",
    ), row=1, col=1, secondary_y=False)
    fig.add_trace(go.Scatter(
        x=spx_yoy.index, y=spx_yoy.values, name="S&P 500 YoY %",
        line=dict(color="#22c55e", width=1.2),
        hovertemplate="%{x|%Y-%m-%d}: %{y:.2f}<extra></extra>",
    ), row=1, col=1, secondary_y=True)
    fig.add_hline(y=0, line_color="#444", line_width=0.5, row=1, col=1)

    fig.add_trace(go.Scatter(
        x=spx.index, y=spx.values, name="S&P 500 (log)",
        line=dict(color="#e5e5e5", width=1.2),
        hovertemplate="%{x|%Y-%m-%d}: %{y:.2f}<extra></extra>",
    ), row=2, col=1, secondary_y=False)
    if btc is not None and not btc.empty:
        fig.add_trace(go.Scatter(
            x=btc.index, y=btc.values, name="BTC/USD (log)",
            line=dict(color="#f59e0b", width=1.2),
            hovertemplate="%{x|%Y-%m-%d}: %{y:.2f}<extra></extra>",
        ), row=2, col=1, secondary_y=True)

    fig.update_yaxes(title_text="PMI YoY (pp)", row=1, col=1, secondary_y=False)
    fig.update_yaxes(title_text="SPX YoY %", row=1, col=1,
                     secondary_y=True, showgrid=False)
    fig.update_yaxes(title_text="SPX", type="log", row=2, col=1, secondary_y=False)
    if btc is not None and not btc.empty:
        fig.update_yaxes(title_text="BTC", type="log", row=2, col=1,
                         secondary_y=True, showgrid=False)

    today = pd.Timestamp.now().normalize()
    default_start = pd.Timestamp("2000-01-01")
    fig.update_xaxes(rangeselector=_range_selector(),
                     range=[default_start.isoformat(), today.isoformat()],
                     dtick="M12", tickformat="%Y",
                     hoverformat="%Y-%m-%d",
                     row=1, col=1)
    fig.update_xaxes(range=[default_start.isoformat(), today.isoformat()],
                     dtick="M12", tickformat="%Y",
                     hoverformat="%Y-%m-%d",
                     row=2, col=1)

    fig.update_layout(
        template="plotly_dark", height=660,
        margin=dict(l=50, r=60, t=110, b=30),
        paper_bgcolor="#111", plot_bgcolor="#111",
        hovermode="x unified",
        legend=dict(orientation="h", y=1.13, x=0),
    )
    return fig.to_html(include_plotlyjs=False, full_html=False,
                       config={"responsive": True})


# ---------- formatters ----------

def _format_value(value: float, unit: str) -> str:
    """Format a value for display. Unit conventions:
      "%"    → percent: "2.30%"
      "Index"→ 1 decimal: "335.4"
      "$"    → raw dollars; auto-scales (K/M/B for large values)
      "$M"   → value is already in millions of dollars
      "$B"   → value is already in billions of dollars
      "K"    → raw count; displays as K/M for large values
      ""     → auto-scaled raw number"""
    if unit == "%":
        return f"{value:.2f}%"
    if unit == "Index":
        return f"{value:.1f}"
    if unit in ("$M", "$B"):
        scale = 1e6 if unit == "$M" else 1e9
        abs_v = abs(value) * scale
        if abs_v >= 1e12: return f"${value * scale / 1e12:.2f}T"
        if abs_v >= 1e9:  return f"${value * scale / 1e9:.1f}B"
        return f"${value * scale / 1e6:.1f}M"
    if unit == "$":
        if abs(value) >= 1e9: return f"${value/1e9:.2f}B"
        if abs(value) >= 1e6: return f"${value/1e6:.2f}M"
        if abs(value) >= 1e3: return f"${value/1e3:.1f}K"
        return f"${value:.2f}"
    # "K" and unitless: raw count, auto-scale
    if abs(value) >= 1e6: return f"{value/1e6:.2f}M"
    if abs(value) >= 1e3: return f"{value/1e3:.1f}K"
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


def _load_series(id: str) -> pd.Series | None:
    df = indicators.load(id)
    if df is None or df.empty:
        return None
    ind = indicators.REGISTRY.get(id)
    col = ind.value_column if ind else "value"
    return df[col] if col in df.columns else None


# ---------- card / chart builders ----------

def _build_card(card_def: dict) -> dict:
    """Always returns a dict — uses status='placeholder' when data is missing,
    so the skeleton stays complete. `link` (optional) turns the placeholder
    into a click-through to an external dashboard when the data isn't fetched
    in-repo (e.g. Trueflation: paid API, we just point users to the site)."""
    id = card_def["id"]
    ind = indicators.REGISTRY.get(id)
    base = {
        "series_id": id,
        "title": card_def.get("title") or (ind.title if ind else id),
        "priority": card_def.get("priority", 2),
        "desc": card_def.get("desc", ""),
        "spec": card_def.get("spec", ""),
        "link": card_def.get("link"),
        "status": "placeholder",
    }
    # Only registered indicators can be unhealthy — link-only cards aren't
    # fetched, so they're always "healthy" in a refresh sense.
    base["unhealthy"] = indicators.is_unhealthy(id) if ind else False
    base["error"] = (indicators.meta(id) or {}).get("last_error") if ind else None
    if ind is None:
        return base
    df = indicators.load(id)
    if df is None or df.empty:
        return base
    s = df[ind.value_column]
    current = float(s.iloc[-1])
    change, change_label = _yoy(s, ind.is_rate)
    return {
        **base,
        "status": "live",
        "current": _format_value(current, ind.unit),
        "change_label": change_label,
        "change_pos": (change or 0) >= 0,
        "sparkline": _sparkline(s, color="#22c55e" if (change or 0) >= 0 else "#ef4444"),
        "last_date": s.index[-1].strftime("%Y-%m-%d"),
    }


def _latest(s: pd.Series, unit: str, label: str) -> dict:
    s = s.dropna()
    if s.empty:
        return None
    return {
        "label": label,
        "value": _format_value(float(s.iloc[-1]), unit),
        "date": s.index[-1].strftime("%Y-%m"),
    }


def _build_featured(chart: dict, prices: dict) -> dict:
    """Resolve a featured chart entry to a render-ready dict. Falls back to a
    placeholder card (no html) when data is unavailable."""
    out = {**chart, "html": None, "placeholder_reason": "", "latest": []}

    kind = chart["kind"]

    if kind == "regime":
        comp_pmi = _load_series("ISM_COMPOSITE_PMI")
        if comp_pmi is None:
            comp_pmi = _load_series("ISM_PMI")
        spx_df = prices.get("SPX")
        btc_df = prices.get("BTC")
        if comp_pmi is None or spx_df is None or spx_df.empty:
            out["placeholder_reason"] = "PMI 或 SPX 缺失"
            return out
        btc_close = btc_df["Close"] if btc_df is not None and not btc_df.empty else None
        out["html"] = _cycle_returns_chart(comp_pmi, spx_df["Close"], btc_close)
        # Latest values for the regime chart's transformed series
        pmi_yoy = (comp_pmi - comp_pmi.shift(12)).dropna()
        spx_yoy = (spx_df["Close"].pct_change(periods=252) * 100).dropna()
        out["latest"] = [
            _latest(pmi_yoy, "%", "PMI YoY"),
            _latest(spx_yoy, "%", "SPX YoY"),
        ]
        out["latest"] = [x for x in out["latest"] if x]
        return out

    if kind == "overlay":
        main_s = _load_series(chart["main_id"])
        leader_s = _load_series(chart["leader_id"])
        if main_s is None or leader_s is None:
            missing = [n for n, s in [(chart["main_id"], main_s),
                                      (chart["leader_id"], leader_s)] if s is None]
            out["placeholder_reason"] = "缺数据: " + ", ".join(missing)
            return out
        main_ind = indicators.REGISTRY.get(chart["main_id"])
        leader_ind = indicators.REGISTRY.get(chart["leader_id"])
        main_s = _apply_transform(main_s, chart.get("main_transform"),
                                   main_ind.is_rate if main_ind else False)
        leader_s = _apply_transform(leader_s, chart.get("leader_transform"),
                                     leader_ind.is_rate if leader_ind else False)
        # Latest values — use transformed unit ("%" if transformed via yoy)
        main_unit = "%" if chart.get("main_transform") == "yoy" else (main_ind.unit if main_ind else "")
        leader_unit = "%" if chart.get("leader_transform") == "yoy" else (leader_ind.unit if leader_ind else "")
        out["latest"] = [
            x for x in [
                _latest(main_s, main_unit, chart["main_name"]),
                _latest(leader_s, leader_unit, chart["leader_name"]),
            ] if x
        ]
        out["html"] = _overlay_chart(
            main=main_s.dropna(), leader=leader_s.dropna(),
            lead_months=chart["lead_months"],
            main_name=chart["main_name"], leader_name=chart["leader_name"],
            invert=chart.get("invert", False),
            invert_main=chart.get("invert_main", False),
        )
        return out

    if kind == "line":
        s = _load_series(chart["series_id"])
        if s is None:
            out["placeholder_reason"] = f"缺数据: {chart['series_id']}"
            return out
        ind = indicators.REGISTRY.get(chart["series_id"])
        s = _apply_transform(s, chart.get("transform"),
                             ind.is_rate if ind else False)
        unit = "%" if chart.get("transform") == "yoy" else (ind.unit if ind else "")
        out["latest"] = [x for x in [_latest(s, unit, chart.get("y_label", chart["series_id"]))] if x]
        out["html"] = _line_chart(s.dropna(), chart.get("y_label", chart["series_id"]))
        return out

    if kind == "lines":
        items = []
        missing = []
        latest = []
        for line in chart.get("lines", []):
            s = _load_series(line["series_id"])
            if s is None:
                missing.append(line["series_id"])
                continue
            ind = indicators.REGISTRY.get(line["series_id"])
            s = _apply_transform(s, line.get("transform"),
                                 ind.is_rate if ind else False)
            unit = "%" if line.get("transform") == "yoy" else (ind.unit if ind else "")
            items.append((s.dropna(), line["label"], line["color"]))
            l = _latest(s, unit, line["label"])
            if l:
                latest.append(l)
        if not items:
            out["placeholder_reason"] = "缺数据: " + ", ".join(missing)
            return out
        out["latest"] = latest
        out["html"] = _lines_chart(items, chart.get("y_label", ""),
                                    ref_50=chart.get("ref_50", True))
        return out

    out["placeholder_reason"] = f"未知 kind: {kind}"
    return out


# ---------- page renderers ----------

def _summary(prices: dict) -> dict:
    s: dict = {}
    for sym in ("SPX", "NDX"):
        df = prices.get(sym)
        if df is None or df.empty:
            continue
        close = df["Close"]
        ma200 = ta.sma(close, 200).iloc[-1]
        last = float(close.iloc[-1])
        pct = (last / ma200 - 1) * 100
        s[sym] = {"last": last, "vs_ma200": pct, "above": last > ma200}
    if (vix := prices.get("VIX")) is not None and not vix.empty:
        s["VIX"] = {"last": float(vix["Close"].iloc[-1])}
    if (yld := prices.get("US10Y")) is not None and not yld.empty:
        s["US10Y"] = {"last": float(yld["Close"].iloc[-1])}
    return s


def _signals_status(prices: dict) -> list[dict]:
    """Current signal status for every (asset, strategy) pair across all
    strategy pages — the dashboard's status board.

    Each row carries: position (long/flat), days since last signal, what the
    last signal was, whether a fresh signal is pending at next open, and the
    strategy's reference value (e.g. thermo reading, distance to MA).
    Sorted by recency so freshly-fired signals float to the top."""
    rows: list[dict] = []
    for page_key in ("indices", "stocks", "learning", "crypto"):
        for asset in layout.strategy_page(page_key)["sections"]:
            strategies = asset.get("strategies") or []
            if not strategies:
                continue
            df = prices.get(asset["ticker"])
            if df is None or df.empty:
                continue
            close = df["Close"].dropna()
            open_ = df["Open"].reindex(close.index)
            for strat in strategies:
                if hasattr(strat, "prepare"):
                    strat.prepare(df)
                result = backtest.run(
                    close=close, strategy=strat, open_=open_,
                    start=asset.get("backtest_start", backtest.DEFAULT_START),
                    fee=asset.get("fee", backtest.DEFAULT_FEE),
                    slippage=asset.get("slippage", backtest.DEFAULT_SLIPPAGE),
                )
                trades = result.trades
                current_pnl = None
                current_mae = None
                if trades:
                    last_t = trades[-1]
                    if last_t.exit_date is None:
                        in_pos = True
                        last_kind = "buy"
                        last_date = last_t.entry_date
                        current_pnl = float(close.iloc[-1]) / last_t.entry_price - 1
                        # MAE in backtest is min(close)/entry−1; can come back
                        # slightly positive when the trade has never closed
                        # below entry (entry is T+1 open, MAE uses closes).
                        # Clamp at 0 so the column reads correctly as
                        # "max underwater" (always ≤ 0).
                        current_mae = min(last_t.mae, 0.0)
                    else:
                        in_pos = False
                        last_kind = "sell"
                        last_date = last_t.exit_date
                else:
                    in_pos = False
                    last_kind = None
                    last_date = None
                today = close.index[-1]
                days_since = (today - last_date).days if last_date is not None else None
                ref = strat.reference(close)
                ref_value = ref.get("value")
                ref_pct = ref.get("pct")
                ref_label = ref.get("label") or ""
                # SMA-family labels start with "vs " or contain " vs " — for
                # these the deviation % is the natural readout. Others (e.g.
                # thermo's "温度") show the absolute reading.
                if ref_value is None:
                    ref_display = None
                elif ref_label.startswith("vs ") or " vs " in ref_label:
                    ref_display = (f"{ref_pct*100:+.2f}%" if ref_pct is not None
                                   else f"{ref_value:.2f}")
                else:
                    ref_display = f"{ref_value:.2f}"
                rows.append({
                    "ticker": asset["ticker"],
                    "asset_name": asset.get("name", asset["ticker"]),
                    "strategy_label": strat.label(),
                    "in_position": in_pos,
                    "last_action_kind": last_kind,
                    "last_action_date": last_date.strftime("%Y-%m-%d") if last_date else None,
                    "days_since": days_since,
                    "current_pnl": current_pnl,
                    "current_mae": current_mae,
                    "pending_action": result.pending_action,
                    "ref_label": ref_label,
                    "ref_display": ref_display,
                    "page_key": page_key,
                })
    # Sort: category (指数 → 个股 → 币 → 学习仓), then ticker alphabetical.
    # Pending-action rows still stand out via the row's pill/highlight styling,
    # so they don't need to be pinned to the top.
    _PAGE_ORDER = {"indices": 0, "stocks": 1, "crypto": 2, "learning": 3}
    rows.sort(key=lambda r: (_PAGE_ORDER.get(r["page_key"], 9), r["ticker"]))
    return rows


def _macro_sparkline(s: pd.Series, rangemode_tozero: bool = False) -> str:
    """Restrained gray sparkline for the index page macro stress cards.
    Two-year window, no axes, no hover decoration. `rangemode_tozero` forces
    y-axis to include 0 — used by RRP so the eye sees the absolute distance
    to a zero floor."""
    cutoff = s.index[-1] - pd.Timedelta(days=730)
    s = s[s.index >= cutoff]
    fig = go.Figure(go.Scatter(
        x=s.index, y=s.values, mode="lines",
        line=dict(color="#888888", width=1.1),
        hovertemplate="%{x|%Y-%m-%d}: %{y:.2f}<extra></extra>",
    ))
    fig.update_layout(
        height=56, margin=dict(l=0, r=0, t=2, b=2),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
        xaxis=dict(visible=False),
        yaxis=dict(visible=False,
                   rangemode="tozero" if rangemode_tozero else "normal"),
    )
    return fig.to_html(include_plotlyjs=False, full_html=False,
                       config={"displayModeBar": False, "responsive": True})


def _macro_card(series_id: str, label: str, fmt: str,
                regime: callable | None = None,
                description: str = "",
                rangemode_tozero: bool = False) -> dict | None:
    """Build one sparkline-style card for the index page macro stress row.

    `regime(value)` returns a short Chinese tag ('极度宽松' / '紧缩' / '') describing
    the current reading vs historical extremes; rendered subtly below the number.
    `description` is the hover-tooltip text on the ⓘ icon."""
    s = _load_series(series_id)
    if s is None or s.empty:
        return None
    s = s.dropna()
    if s.empty:
        return None
    last_v = float(s.iloc[-1])
    last_d = s.index[-1].strftime("%Y-%m-%d")
    return {
        "id": series_id,
        "label": label,
        "value": fmt.format(last_v),
        "date": last_d,
        "regime": regime(last_v) if regime else "",
        "description": description,
        "sparkline": _macro_sparkline(s, rangemode_tozero=rangemode_tozero),
        "unhealthy": indicators.is_unhealthy(series_id),
        "error": (indicators.meta(series_id) or {}).get("last_error"),
    }


def _sentiment_sparkline(s: pd.Series, lo: float, hi: float,
                         thresholds: list[float] | None = None,
                         days: int = 730) -> str:
    """Sparkline for bounded sentiment indicators. Unlike `_macro_sparkline`
    (auto-scaled), the y-axis is pinned to the indicator's natural range
    [lo, hi] so the absolute level reads correctly, and faint dotted lines
    mark the regime thresholds — this is the context the old gauge bar gave."""
    cutoff = s.index[-1] - pd.Timedelta(days=days)
    s = s[s.index >= cutoff]
    fig = go.Figure()
    for t in (thresholds or []):
        fig.add_hline(y=t, line=dict(color="#3f3f46", width=0.6, dash="dot"))
    fig.add_trace(go.Scatter(
        x=s.index, y=s.values, mode="lines",
        line=dict(color="#888888", width=1.1),
        hovertemplate="%{x|%Y-%m-%d}: %{y:.2f}<extra></extra>",
    ))
    fig.update_layout(
        height=56, margin=dict(l=0, r=0, t=2, b=2),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
        xaxis=dict(visible=False),
        yaxis=dict(visible=False, range=[lo, hi]),
    )
    return fig.to_html(include_plotlyjs=False, full_html=False,
                       config={"displayModeBar": False, "responsive": True})


def _sentiment_card(series_id: str | None, label: str, lo: float, hi: float,
                    fmt: str, regime: callable | None, thresholds: list[float],
                    description: str, series: pd.Series | None = None) -> dict | None:
    """Sparkline-card for a bounded sentiment indicator (same shape as
    `_macro_card`, rendered by index.html's `sparkline_card` macro). Pass
    `series` directly for computed series (e.g. VIX/VIX3M ratio)."""
    s = series if series is not None else _load_series(series_id)
    if s is None or s.empty:
        return None
    s = s.dropna()
    if s.empty:
        return None
    last_v = float(s.iloc[-1])
    return {
        "id": series_id,
        "label": label,
        "value": fmt.format(last_v),
        "date": s.index[-1].strftime("%Y-%m-%d"),
        "regime": regime(last_v) if regime else "",
        "description": description,
        "sparkline": _sentiment_sparkline(s, lo, hi, thresholds),
        "unhealthy": indicators.is_unhealthy(series_id) if series_id else False,
        "error": (indicators.meta(series_id) or {}).get("last_error") if series_id else None,
    }


def _vix_term_ratio() -> pd.Series | None:
    vix = _load_series("VIX")
    vix3m = _load_series("VIX3M")
    if vix is None or vix3m is None:
        return None
    df = pd.concat([vix.rename("vix"), vix3m.rename("vix3m")], axis=1).dropna()
    if df.empty:
        return None
    return df["vix"] / df["vix3m"]


def _sentiment_gauges() -> list[dict]:
    """Bounded sentiment / risk indicators: CNN F&G, VIX term structure, SKEW.
    Rendered as fixed-range sparklines with threshold reference lines."""
    def fng_regime(v):
        if v < 20: return "极度恐慌"
        if v < 40: return "恐慌"
        if v > 80: return "极度贪婪"
        if v > 60: return "贪婪"
        return "中性"

    def skew_regime(v):
        if v < 120: return "毫无防备"
        if v > 150: return "极端防备"
        if v > 140: return "高度警惕"
        return ""

    def vix_ratio_regime(v):
        if v > 1.15: return "崩盘"
        if v > 1.00: return "倒挂 · 危机"
        if v < 0.80: return "平静"
        return ""

    cards = [
        _sentiment_card("CNN_FEAR_GREED", "CNN 恐惧 / 贪婪",
                        lo=0, hi=100, fmt="{:.0f}", regime=fng_regime,
                        thresholds=[20, 80],
                        description="CNN 综合 7 项市场指标(动量、强势、宽度、看涨看跌、垃圾债需求、波动率、避险需求)合成的情绪仪表盘。<20 极度恐慌(常为历史底部);>80 极度贪婪。曲线参考线 = 20 / 80。"),
        _sentiment_card("VIX_VIX3M", "VIX / VIX3M 期限结构",
                        lo=0.70, hi=1.30, fmt="{:.2f}", regime=vix_ratio_regime,
                        thresholds=[1.00],
                        description="VIX 当月 / VIX 3 月期权波动率比。正常 contango (<1) = 远期不确定性更高;倒挂 (>1) = 短期恐慌爆发,是危机模式的物理开关。曲线参考线 = 1.00。",
                        series=_vix_term_ratio()),
        _sentiment_card("SKEW", "SKEW 尾部风险",
                        lo=100, hi=160, fmt="{:.1f}", regime=skew_regime,
                        thresholds=[140],
                        description="CBOE SKEW 指数,机构对深价外看跌期权的定价 = 黑天鹅尾部风险溢价。100 = 正态分布;<120 机构毫无防备(易被突袭);>140 高度警惕;>150 极端防备(常为暴风雨前夜)。曲线参考线 = 140。"),
    ]
    return [c for c in cards if c]


def _position_gauges() -> list[dict]:
    """Position indicator in its own section. NAAIM is weekly-updated investment
    manager survey, distinct cadence from the daily sentiment row."""
    def naaim_regime(v):
        if v < 30:  return "机构极度空仓"
        if v > 100: return "机构加杠杆"
        return ""

    cards = [
        _sentiment_card("NAAIM_EXPOSURE", "NAAIM 机构仓位",
                        lo=0, hi=150, fmt="{:.0f}%", regime=naaim_regime,
                        thresholds=[30, 100],
                        description="全美主动投资经理协会(NAAIM)调查的实际仓位敞口(0-200%)。<30 极度空仓(常为左侧买点);>100 加杠杆。比 AAII 散户调查更硬核 — 真金白银仓位而非问卷情绪。周更(周四发布)。曲线参考线 = 30 / 100。"),
    ]
    return [c for c in cards if c]


def _macro_stress_cards() -> list[dict]:
    """First batch of index-page indicators: macro stress sparklines.
    10Y-2Y curve, HY OAS, MOVE — all daily FRED/yfinance, cheap to fetch."""
    def t10y2y_regime(v):
        if v < 0:   return "倒挂"
        if v > 1.5: return "陡峭"
        return ""

    def hyoas_regime(v):
        if v < 3.5: return "极度宽松"
        if v > 8.0: return "恐慌"
        if v > 5.5: return "压力"
        return ""

    def move_regime(v):
        if v < 80:  return "平静"
        if v > 150: return "恐慌"
        if v > 120: return "压力"
        return ""

    cards = [
        _macro_card("T10Y2Y", "10Y-2Y 利差", "{:+.2f}%", t10y2y_regime,
            description="10 年期与 2 年期美债利差。倒挂 (<0) 历史上领先衰退 12-18 月,转正陡峭化常是周期触底信号。"),
        _macro_card("BAMLH0A0HYM2", "高收益债利差", "{:.2f}%", hyoas_regime,
            description="美国高收益债相对国债的期权调整利差 (OAS),信用市场压力的硬指标。<3.5% 信用极度宽松;>5.5% 压力显现;>8% 危机模式。"),
        _macro_card("MOVE", "MOVE 债波动率", "{:.1f}", move_regime,
            description="ICE BofA MOVE 指数,债市的 VIX,衡量未来一个月利率不确定性。<80 平静;>120 压力;>150 恐慌。"),
    ]
    return [c for c in cards if c]


def _real_liquidity_cards() -> list[dict]:
    """Second batch of sparklines: real-economy + Fed liquidity.
    Copper/Gold ratio + Overnight RRP balance."""
    def cugc_regime(v):
        if v < 0.15: return "衰退担忧"
        if v > 0.30: return "强增长预期"
        return ""

    def rrp_regime(v):
        if v < 200:  return "流动性红灯"
        if v > 1000: return "过剩"
        return ""

    cards = [
        _macro_card("COPPER_GOLD", "铜金比 ×100", "{:.3f}", cugc_regime,
            description="铜金比 ×100。铜代表实体工业需求 (Dr. Copper),金代表避险与货币超发,两者拔河反映周期与货币环境。历史上与 10Y 收益率高度同步;股市涨但铜金比跌通常是衰退式假牛市。"),
        _macro_card("RRPONTSYD", "Fed 隔夜逆回购", "${:.0f}B",  rrp_regime,
            rangemode_tozero=True,
            description="美联储隔夜逆回购余额(十亿美元),金融系统过剩流动性的「血库」。2022 年峰值 2.5 万亿,逼近 0 时 QT 才会真正刺痛市场;预判流动性枯竭危机的先导指标。"),
    ]
    return [c for c in cards if c]


def render_index(prices: dict) -> None:
    updated, run = _refresh_ctx()
    html = env.get_template("index.html").render(
        macro_cards=_macro_stress_cards(),
        liquidity_cards=_real_liquidity_cards(),
        gauge_cards=_sentiment_gauges(),
        position_cards=_position_gauges(),
        updated=updated, run=run, page="index",
        pages=layout.sidebar_pages(),
    )
    (DOCS_DIR / "index.html").write_text(html)


def render_strategy_overview(prices: dict) -> None:
    """Strategy section's landing page — the cross-asset status board.
    Lives under the 策略 nav (sibling of indices/stocks/learning/crypto)
    so signals are co-located with the strategy detail pages."""
    cfg = layout.strategy_page("overview")
    updated, run = _refresh_ctx()
    html = env.get_template("strategy_overview.html").render(
        updated=updated, run=run, page="overview",
        page_cfg=cfg, signals=_signals_status(prices),
        pages=layout.sidebar_strategy_pages(),
        section_nav=layout.sidebar_strategy_pages(),
    )
    (DOCS_DIR / "overview.html").write_text(html)


def _by_priority(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda x: x.get("priority", 9))


def render_macro_page(page_key: str, prices: dict) -> None:
    """Render one of the four macro pages (cycle/employment/inflation/background)
    using the generic macro.html template."""
    cfg = layout.page(page_key)
    sections = []
    for sec in cfg["sections"]:
        featured = [_build_featured(c, prices) for c in sec.get("featured", [])]
        cards = [_build_card(c) for c in sec.get("cards", [])]
        sections.append({
            "key": sec["key"], "label": sec["label"],
            "subtitle": sec.get("subtitle", ""),
            "featured": _by_priority(featured),
            "cards": _by_priority(cards),
        })

    updated, run = _refresh_ctx()
    html = env.get_template("macro.html").render(
        updated=updated, run=run, page=page_key,
        page_cfg=cfg, sections=sections,
        pages=layout.sidebar_pages(),
        section_nav=layout.sidebar_pages(),
    )
    (DOCS_DIR / f"{page_key}.html").write_text(html)


# ---------- Strategy page ----------

def _strategy_price_chart(close: pd.Series, overlay_lines, trades,
                          default_start: pd.Timestamp | None = None) -> str:
    """Price (log) + MA overlays + entry/exit markers.

    The MA-band strategies emit overlay lines whose `fill_to` references the
    name of the line they shade towards. We resolve those indices to Plotly's
    `fill='tonexty'` by drawing the target line first, then the filler with
    `fill='tonexty'` — Plotly fills towards the previous trace.

    `default_start` controls the initial visible x-range (defaults to last 3y
    if unspecified)."""
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=close.index, y=close.values, name="Close",
        line=dict(color="#e5e5e5", width=1.2),
        hovertemplate="%{x|%Y-%m-%d}: %{y:.2f}<extra></extra>",
    ))
    # Plot overlay lines in declaration order. For fill_to, we expect the
    # target line to have been drawn immediately prior so 'tonexty' fills
    # between them. Strategy.overlay() is responsible for the ordering.
    name_to_idx = {}
    for line in overlay_lines:
        s = line.series.dropna()
        trace_kw = dict(
            x=s.index, y=s.values, name=line.name,
            line=dict(color=line.color, width=line.width,
                      dash=line.dash) if line.dash else dict(color=line.color, width=line.width),
            hovertemplate="%{x|%Y-%m-%d}: %{y:.2f}<extra></extra>",
        )
        if line.fill_to is not None and line.fill_to in name_to_idx:
            trace_kw["fill"] = "tonexty"
            trace_kw["fillcolor"] = "rgba(245,158,11,0.08)"
        fig.add_trace(go.Scatter(**trace_kw))
        name_to_idx[line.name] = len(fig.data) - 1

    # Entry / exit markers
    if trades:
        entries_x = [t.entry_date for t in trades]
        entries_y = [t.entry_price for t in trades]
        exits_x = [t.exit_date for t in trades if t.exit_date is not None]
        exits_y = [t.exit_price for t in trades if t.exit_date is not None]
        fig.add_trace(go.Scatter(
            x=entries_x, y=entries_y, mode="markers", name="Buy",
            marker=dict(symbol="triangle-up", size=10, color="#22c55e",
                        line=dict(color="#0a0a0a", width=1)),
            hovertemplate="Buy %{x|%Y-%m-%d} @ %{y:.2f}<extra></extra>",
        ))
        if exits_x:
            fig.add_trace(go.Scatter(
                x=exits_x, y=exits_y, mode="markers", name="Sell",
                marker=dict(symbol="triangle-down", size=10, color="#ef4444",
                            line=dict(color="#0a0a0a", width=1)),
                hovertemplate="Sell %{x|%Y-%m-%d} @ %{y:.2f}<extra></extra>",
            ))

    if default_start is None:
        default_start = close.index[-1] - pd.DateOffset(years=3)
    # Two-row header: legend (top) above the rangeselector (bottom) so the
    # many MA/band/signal traces don't get clipped by the range buttons.
    fig.update_layout(
        template="plotly_dark", height=460,
        margin=dict(l=50, r=20, t=90, b=30),
        paper_bgcolor="#111", plot_bgcolor="#111",
        legend=dict(orientation="h", y=1.22, x=0, font=dict(size=10),
                    bgcolor="rgba(0,0,0,0)"),
        hovermode="x unified",
        yaxis=dict(type="log", title="Price (log)"),
        xaxis=dict(
            rangeselector=_range_selector(),
            range=[default_start.isoformat(), close.index[-1].isoformat()],
            hoverformat="%Y-%m-%d",
        ),
    )
    return fig.to_html(include_plotlyjs=False, full_html=False,
                       config={"displayModeBar": False, "responsive": True})


def _equity_chart(equity: pd.Series, bh_equity: pd.Series,
                  default_start: pd.Timestamp | None = None) -> str:
    """Strategy NAV vs buy-and-hold, log y so multi-thousand-percent runs
    don't crush the early years. Title carries terminal NAVs so the user
    can read final performance without hunting in the metrics row."""
    end_s = float(equity.iloc[-1]) if not equity.empty else 0
    end_b = float(bh_equity.iloc[-1]) if not bh_equity.empty else 0
    title_html = (f"<span style='color:#a1a1aa'>净值 (log) · 终值</span> "
                  f"<span style='color:#22c55e'>Strategy {end_s:.2f}×</span> "
                  f"<span style='color:#a1a1aa'>·</span> "
                  f"<span style='color:#9ca3af'>B&amp;H {end_b:.2f}×</span>")
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=equity.index, y=equity.values, name="Strategy",
        line=dict(color="#22c55e", width=1.5),
        hovertemplate="%{x|%Y-%m-%d}: %{y:.2f}×<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=bh_equity.index, y=bh_equity.values, name="B&H",
        line=dict(color="#6b7280", width=1.2, dash="dot"),
        hovertemplate="%{x|%Y-%m-%d}: %{y:.2f}×<extra></extra>",
    ))
    xaxis = dict(hoverformat="%Y-%m-%d")
    if default_start is not None and not equity.empty:
        xaxis["range"] = [default_start.isoformat(), equity.index[-1].isoformat()]
    fig.update_layout(
        template="plotly_dark", height=240,
        margin=dict(l=40, r=10, t=30, b=20),
        paper_bgcolor="#111", plot_bgcolor="#111",
        title=dict(text=title_html, font=dict(size=11), x=0, y=0.97),
        showlegend=False,
        hovermode="x unified",
        yaxis=dict(type="log"),
        xaxis=xaxis,
    )
    return fig.to_html(include_plotlyjs=False, full_html=False,
                       config={"displayModeBar": False, "responsive": True})


def _drawdown_chart(drawdown: pd.Series,
                    default_start: pd.Timestamp | None = None) -> str:
    fig = go.Figure(go.Scatter(
        x=drawdown.index, y=(drawdown * 100).values, name="Drawdown %",
        line=dict(color="#ef4444", width=1.0),
        fill="tozeroy", fillcolor="rgba(239,68,68,0.15)",
        hovertemplate="%{x|%Y-%m-%d}: %{y:.1f}%<extra></extra>",
    ))
    xaxis = dict(hoverformat="%Y-%m-%d")
    if default_start is not None and not drawdown.empty:
        xaxis["range"] = [default_start.isoformat(), drawdown.index[-1].isoformat()]
    fig.update_layout(
        template="plotly_dark", height=240,
        margin=dict(l=40, r=10, t=30, b=20),
        paper_bgcolor="#111", plot_bgcolor="#111",
        title=dict(text="回撤 (%)", font=dict(size=11, color="#a1a1aa"), x=0, y=0.97),
        showlegend=False,
        hovermode="x unified",
        xaxis=xaxis,
    )
    return fig.to_html(include_plotlyjs=False, full_html=False,
                       config={"displayModeBar": False, "responsive": True})


def _trade_pnl_chart(trades) -> str:
    """One bar per closed trade, x = sequential trade index (so each bar
    gets equal width regardless of trade frequency — date-spaced bars get
    crushed when trades are sparse and long-running)."""
    closed = [t for t in trades if t.status == "Closed"]
    if not closed:
        return ""
    x = list(range(1, len(closed) + 1))
    y = [t.ret * 100 for t in closed]
    colors = ["#22c55e" if r > 0 else "#ef4444" for r in y]
    hover = [f"#{i} · {t.entry_date.date()} → {t.exit_date.date()}<br>{t.ret*100:+.1f}%"
             for i, t in enumerate(closed, 1)]
    fig = go.Figure(go.Bar(
        x=x, y=y, marker_color=colors,
        marker_line_width=0,
        text=hover, hovertemplate="%{text}<extra></extra>",
    ))
    fig.add_hline(y=0, line_color="#3f3f46", line_width=0.5)
    fig.update_layout(
        template="plotly_dark", height=240,
        margin=dict(l=40, r=10, t=30, b=20),
        paper_bgcolor="#111", plot_bgcolor="#111",
        title=dict(text="单笔 PnL (%)", font=dict(size=11, color="#a1a1aa"), x=0, y=0.97),
        showlegend=False,
        hovermode="closest",
        xaxis=dict(title="trade #", tickfont=dict(size=9),
                   showgrid=False, zeroline=False),
        yaxis=dict(zeroline=False, gridcolor="#1f1f1f"),
        bargap=0.15,
    )
    return fig.to_html(include_plotlyjs=False, full_html=False,
                       config={"displayModeBar": False, "responsive": True})


# Breadth thresholds (right-side cross-up) — see 技巧专区/指数底用市场宽度.
# In a bull market (price > 225DMA) breadth swings 20–90; in a bear market
# (price < 225DMA) it swings 5–70. We mark add-position signals when breadth
# crosses BACK UP through the threshold (right-side confirmation, not knife-
# catching on the way down).
_BREADTH_BULL_THRESHOLD = 20
_BREADTH_BEAR_THRESHOLD = 15
_BREADTH_RECENT_DAYS = 10  # how recent a cross counts as "still actionable"


def _regime_segments(is_bull: pd.Series) -> list[tuple[pd.Timestamp, pd.Timestamp, bool]]:
    """Collapse a daily bull/bear boolean into contiguous (start, end, is_bull)
    segments — fewer Plotly shapes, much faster to render than per-day rects."""
    if is_bull.empty:
        return []
    groups = is_bull.ne(is_bull.shift()).cumsum()
    out = []
    for _, grp in is_bull.groupby(groups):
        out.append((grp.index[0], grp.index[-1], bool(grp.iloc[0])))
    return out


def _breadth_crossovers(breadth: pd.Series, is_bull: pd.Series) -> dict:
    """Right-side cross-ups: yesterday < threshold, today >= threshold.
    Bull/bear regime selects which threshold applies on that day."""
    prev = breadth.shift(1)
    bull_cross = is_bull & (prev < _BREADTH_BULL_THRESHOLD) & (breadth >= _BREADTH_BULL_THRESHOLD)
    bear_cross = (~is_bull) & (prev < _BREADTH_BEAR_THRESHOLD) & (breadth >= _BREADTH_BEAR_THRESHOLD)
    return {
        "bull_dates": breadth.index[bull_cross.fillna(False)],
        "bear_dates": breadth.index[bear_cross.fillna(False)],
    }


def _breadth_status(close: pd.Series, ma225: pd.Series, breadth: pd.Series,
                    crossovers: dict) -> dict:
    """Status-bar fields: regime, current breadth, suggestion."""
    last_date = breadth.index[-1]
    last_b = float(breadth.iloc[-1])
    last_c = float(close.loc[last_date]) if last_date in close.index else float(close.iloc[-1])
    last_ma = float(ma225.loc[last_date]) if last_date in ma225.index else float(ma225.iloc[-1])
    is_bull_now = last_c > last_ma
    threshold = _BREADTH_BULL_THRESHOLD if is_bull_now else _BREADTH_BEAR_THRESHOLD

    all_crosses = pd.DatetimeIndex(sorted(
        list(crossovers["bull_dates"]) + list(crossovers["bear_dates"])
    ))
    last_cross = all_crosses[-1] if len(all_crosses) else None

    if last_b < threshold:
        tone = "warn"
        suggestion = f"等右侧反弹 · 阈值 {threshold}%"
    elif last_cross is not None and (last_date - last_cross).days <= _BREADTH_RECENT_DAYS:
        tone = "buy"
        suggestion = f"加仓机会 · {last_cross.strftime('%Y-%m-%d')} 右侧穿越"
    else:
        tone = "flat"
        suggestion = f"观望 · 距阈值 +{last_b - threshold:.0f}pp"

    return {
        "regime": "牛市 · 价格 > 225DMA" if is_bull_now else "熊市 · 价格 < 225DMA",
        "regime_kind": "bull" if is_bull_now else "bear",
        "breadth_now": last_b,
        "threshold": threshold,
        "suggestion": suggestion,
        "tone": tone,
        "last_date": last_date.strftime("%Y-%m-%d"),
    }


def _breadth_section(ticker: str, name: str, breadth_id: str,
                     close: pd.Series, breadth: pd.Series,
                     default_start: pd.Timestamp) -> dict:
    """One asset's breadth study, rendered as a single combo chart with
    dual y-axes — price (log, left) and breadth %% (0-100, right). Hover-
    unified so a single date cursor reads price + breadth + 225DMA at once.
    Pale bull/bear regime bands shade the whole plot."""
    ma225 = ta.sma(close, 225)
    common = breadth.index.intersection(close.index)
    b = breadth.reindex(common).dropna()
    c = close.reindex(b.index)
    ma = ma225.reindex(b.index)
    is_bull = (c > ma) & ma.notna()

    crossovers = _breadth_crossovers(b, is_bull)
    status = _breadth_status(c, ma, b, crossovers)
    segments = _regime_segments(is_bull)
    end_ts = b.index[-1]

    fig = make_subplots(specs=[[{"secondary_y": True}]])

    # ---- Price (left y, log) ----
    fig.add_trace(go.Scatter(
        x=close.index, y=close.values, name="Close",
        line=dict(color="#e5e5e5", width=1.1),
        hovertemplate="%{x|%Y-%m-%d}: %{y:.2f}<extra></extra>",
    ), secondary_y=False)
    fig.add_trace(go.Scatter(
        x=ma225.index, y=ma225.values, name="225DMA",
        line=dict(color="#71717a", width=1.0, dash="dot"),
        hovertemplate="%{x|%Y-%m-%d} · 225DMA %{y:.2f}<extra></extra>",
    ), secondary_y=False)

    bull_x = crossovers["bull_dates"]
    bear_x = crossovers["bear_dates"]
    if len(bull_x):
        fig.add_trace(go.Scatter(
            x=bull_x, y=close.reindex(bull_x).values,
            mode="markers", name=f"牛市穿越 ({_BREADTH_BULL_THRESHOLD}%)",
            marker=dict(symbol="triangle-up", size=10, color="#22c55e",
                        line=dict(color="#0a0a0a", width=1)),
            hovertemplate="牛 %{x|%Y-%m-%d} @ %{y:.2f}<extra></extra>",
        ), secondary_y=False)
    if len(bear_x):
        fig.add_trace(go.Scatter(
            x=bear_x, y=close.reindex(bear_x).values,
            mode="markers", name=f"熊市穿越 ({_BREADTH_BEAR_THRESHOLD}%)",
            marker=dict(symbol="triangle-up-open", size=11, color="#22c55e",
                        line=dict(color="#22c55e", width=1.8)),
            hovertemplate="熊 %{x|%Y-%m-%d} @ %{y:.2f}<extra></extra>",
        ), secondary_y=False)

    # ---- Breadth (right y, 0-100) ----
    fig.add_trace(go.Scatter(
        x=b.index, y=b.values, name=breadth_id,
        line=dict(color="#3b82f6", width=1.0),
        hovertemplate=f"%{{x|%Y-%m-%d}} · {breadth_id} %{{y:.1f}}%<extra></extra>",
    ), secondary_y=True)

    # Bull/bear regime bands span the whole figure
    for seg_start, seg_end, seg_bull in segments:
        fig.add_vrect(
            x0=seg_start, x1=seg_end,
            fillcolor="#1f2a1f" if seg_bull else "#2a1f1f",
            opacity=0.5, layer="below", line_width=0,
        )

    # Threshold reference lines on the breadth (right) axis
    fig.add_hline(y=_BREADTH_BULL_THRESHOLD, line_color="#3b82f6",
                  line_width=0.6, line_dash="dot",
                  secondary_y=True)
    fig.add_hline(y=_BREADTH_BEAR_THRESHOLD, line_color="#1e3a8a",
                  line_width=0.6, line_dash="dot",
                  secondary_y=True)

    fig.update_xaxes(
        rangeselector=_range_selector(),
        range=[default_start.isoformat(), end_ts.isoformat()],
        hoverformat="%Y-%m-%d",
    )
    fig.update_yaxes(type="log", title_text="Price (log)", secondary_y=False)
    fig.update_yaxes(range=[0, 100], ticksuffix="%", tickfont=dict(size=9),
                     gridcolor="rgba(0,0,0,0)",
                     title_text=f"{breadth_id} %", secondary_y=True)
    fig.update_layout(
        template="plotly_dark", height=520,
        margin=dict(l=55, r=55, t=110, b=30),
        paper_bgcolor="#111", plot_bgcolor="#111",
        legend=dict(orientation="h", y=1.22, x=0, font=dict(size=10),
                    bgcolor="rgba(0,0,0,0)"),
        hovermode="x unified",
    )

    chart_html = fig.to_html(include_plotlyjs=False, full_html=False,
                             config={"displayModeBar": False, "responsive": True})
    return {
        "ticker": ticker,
        "name": name,
        "breadth_id": breadth_id,
        "status": status,
        "chart_html": chart_html,
    }


# Thermometer rainbow palette — matches v8_lambda2_0_refine_balanced.pine.
# 5 contiguous bands; values within each band render in that band's color.
_THERMO_BANDS: list[tuple[float, float, str]] = [
    (0.0,   20.0, "#2862ff"),  # deep navy → vivid blue
    (20.0,  30.0, "#00c040"),  # blue → green
    (30.0,  55.0, "#ffd000"),  # green → golden yellow
    (55.0,  75.0, "#ff7000"),  # yellow → vivid orange
    (75.0, 101.0, "#c01010"),  # orange → deep red
]


def _rainbow_thermo_traces(thermo: pd.Series, *, yaxis: str = "y2",
                           xaxis: str = "x") -> list[go.Scatter]:
    """5 NaN-masked band traces that together render the thermometer as a
    rainbow line. Each band keeps points whose value is inside its range OR
    immediately precedes a transition into the band (1-bar overshoot) so the
    line stays connected across band boundaries with no visible gap.

    One additional invisible trace carries the unified hover label, so the
    hover shows a single '温度 N' entry rather than one per band trace."""
    traces: list[go.Scatter] = []
    for lo, hi, color in _THERMO_BANDS:
        in_band = (thermo >= lo) & (thermo < hi)
        # Include the FIRST point after exiting (shift(1) of in_band) so the
        # next band's start point is also in this trace — guarantees adjacent
        # bands meet visually.
        keep = in_band | in_band.shift(1, fill_value=False)
        y = thermo.where(keep)
        traces.append(go.Scatter(
            x=thermo.index, y=y, mode="lines",
            line=dict(color=color, width=1.6),
            showlegend=False, hoverinfo="skip",
            xaxis=xaxis, yaxis=yaxis,
        ))
    # Invisible hover proxy — one entry in the unified hover card.
    traces.append(go.Scatter(
        x=thermo.index, y=thermo, mode="lines",
        name="温度",
        line=dict(color="rgba(0,0,0,0)", width=0),
        hovertemplate="温度 %{y:.1f}<extra></extra>",
        showlegend=False,
        xaxis=xaxis, yaxis=yaxis,
    ))
    return traces


def _learning_chart(df: pd.DataFrame) -> str:
    """Watch-only chart for learning-pool assets: price + MA fan on top
    subplot, rainbow thermometer on bottom subplot.

    Layout is single shared x-axis with two stacked y-axes at different
    `domain` ranges — NOT make_subplots(rows=2), which would create a second
    x-axis (x2). The cross-subplot dual-hover ghost (`_attachCrossSubplotHover`
    in base.html) needs both subplots on the same x-axis identity to fire one
    `plotly_hover` event whose ghost-clone lands on the other subplot."""
    from augury import ta
    from augury.strategies.thermo_band import BALANCED_PARAMS

    close = df["Close"].dropna()
    thermo = ta.composite(df, BALANCED_PARAMS).reindex(close.index)

    fig = go.Figure()

    # Close = gd.data[0] (the ghost-clone code reads it as the price trace).
    fig.add_trace(go.Scatter(
        x=close.index, y=close.values, name="Close",
        line=dict(color="#e5e5e5", width=1.2),
        hovertemplate="%{y:.2f}<extra></extra>",
        xaxis="x", yaxis="y",
    ))
    # MA fan: visual only, no hover so the price subplot's natural card stays
    # at a single 'price' label (matches TSLA's strategy view).
    for n, color in [(20, "#3b82f6"), (60, "#f59e0b"),
                     (120, "#a855f7"), (225, "#10b981")]:
        ma = close.rolling(n).mean()
        fig.add_trace(go.Scatter(
            x=ma.index, y=ma.values, name=f"MA{n}",
            line=dict(color=color, width=0.9),
            hoverinfo="skip",
            xaxis="x", yaxis="y",
        ))

    # Rainbow thermometer on yaxis2 (same xaxis, different y-axis domain).
    for tr in _rainbow_thermo_traces(thermo, yaxis="y2", xaxis="x"):
        fig.add_trace(tr)

    default_start = close.index[-1] - pd.DateOffset(years=3)
    fig.update_layout(
        template="plotly_dark", height=520,
        margin=dict(l=50, r=20, t=90, b=30),
        paper_bgcolor="#111", plot_bgcolor="#111",
        legend=dict(orientation="h", y=1.16, x=0, font=dict(size=10),
                    bgcolor="rgba(0,0,0,0)"),
        # 'x unified' creates the `g.legend` DOM element that the
        # `_attachCrossSubplotHover` ghost-clone (in base.html) clones onto
        # the OTHER subplot. `hoversubplots: single` keeps each subplot's
        # card separate (no cross-subplot merging) so cursor in price shows
        # a price card on top + a ghost 温度 card on the thermo subplot,
        # and vice versa.
        hovermode="x unified",
        hoversubplots="single",
        xaxis=dict(
            anchor="y2",
            rangeselector=_range_selector(),
            range=[default_start.isoformat(), close.index[-1].isoformat()],
            hoverformat="%Y-%m-%d",
            showspikes=True, spikemode="across", spikethickness=1,
            spikedash="solid", spikecolor="rgba(180,180,180,0.55)",
        ),
        yaxis=dict(
            type="log",
            domain=[0.32, 1.0],
            showspikes=True, spikemode="across", spikethickness=1,
            spikedash="dot", spikecolor="rgba(180,180,180,0.35)",
        ),
        yaxis2=dict(
            visible=True, range=[0, 100], fixedrange=True,
            domain=[0.0, 0.26], anchor="x",
            tickvals=[20, 30, 50, 70, 80],
            tickfont=dict(size=9, color="#a1a1aa"),
            gridcolor="#1f1f1f", zeroline=False,
        ),
        # Reference hlines on the thermometer subplot at 20/30/50/70/80.
        shapes=[
            dict(type="line", xref="x domain", yref="y2",
                 x0=0, x1=1, y0=lvl, y1=lvl,
                 line=dict(color=col, width=1, dash=dash))
            for lvl, col, dash in [
                (80, "rgba(239,68,68,0.5)",  "dot"),
                (70, "rgba(239,68,68,0.3)",  "dash"),
                (50, "rgba(120,120,120,0.5)","solid"),
                (30, "rgba(34,197,94,0.3)",  "dash"),
                (20, "rgba(34,197,94,0.5)",  "dot"),
            ]
        ],
    )
    return fig.to_html(include_plotlyjs=False, full_html=False,
                       config={"displayModeBar": False, "responsive": True})


# ---------- formatters for strategy metrics ----------

# Definitions surfaced via tooltip on each metric cell. Keys must match the
# labels emitted in _build_strategy_asset's `metrics` dict.
METRIC_INFO = {
    "CAGR":    "复合年化增长率(Compound Annual Growth Rate)。把期末净值折算成几何年化收益。",
    "MDD":     "最大回撤(Max Drawdown)。策略净值从历史峰值跌落的最大幅度。",
    "MAE":     "最大不利偏移(Max Adverse Excursion)。所有交易内部出现过的最深浮亏 — 反映“持仓期间最难熬的时刻”。",
    "Vol":     "年化波动率。日收益标准差 × √252。",
    "Sharpe":  "夏普比率。年化超额收益 / 年化波动率。>1 良好,>2 优秀。",
    "Sortino": "索提诺比率。和 Sharpe 类似,但只惩罚下行波动,不惩罚上涨“波动”。",
    "Calmar":  "卡尔玛比率。CAGR / |MDD|。承担多少最大回撤换取年化收益。",
    "WinRate": "胜率。盈利交易笔数 / 已平仓总笔数。趋势策略胜率常常低于 50%。",
    "Payoff":  "盈亏比。平均盈利交易收益 / |平均亏损交易收益|。",
    "PF":      "Profit Factor。所有盈利总和 / |所有亏损总和|。>1 才能盈利。",
    "EndNAV":  "期末净值倍数。初始 1× 资金最终变成多少倍(含未平仓浮盈)。",
    "B&H":     "Buy & Hold 期末净值。同期买入持有的对照基准。",
    "Trades":  "总交易次数(包含当前仍持仓未平的开仓)。",
}


def _fmt_pct(v: float, digits: int = 1) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{v*100:+.{digits}f}%" if digits > 0 else f"{v*100:.0f}%"


def _fmt_pct_unsigned(v: float, digits: int = 1) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{v*100:.{digits}f}%"


def _fmt_num(v: float, digits: int = 2) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{v:.{digits}f}"


def _fmt_nav(v: float) -> str:
    """End NAV as a multiple (e.g. 344.19× starting from 1)."""
    if v is None or pd.isna(v):
        return "—"
    return f"{v:.2f}×"


def _strategy_serialize(strat) -> dict:
    """Serialize a strategy instance for the JS engine.

    The JS side has its own implementation of each strategy's `signals()` —
    keyed by `type`. Adding a new strategy = match the {type, params} schema
    in JS too (see _backtest_engine in base.html).

    ThermoBand is a special case: the thermometer is too expensive to
    recompute in JS (8 oscillators + composite), so the server-rendered
    thermo array travels alongside `close`/`open` in the payload itself
    (see `_strategy_payload`). The JS engine just applies threshold
    crossings to that pre-computed series."""
    from augury.strategies import HybridStrategy, ThermoBand
    if isinstance(strat, HybridStrategy):
        # Hybrid uses the BASE strategy's signals — we only need to serialize
        # that. Substitutes travel separately in the hybrid payload.
        return _strategy_serialize(strat.base)
    if isinstance(strat, SmaBand):
        return {"type": "sma_band",
                "params": {"ma": int(strat.ma), "threshold": float(strat.threshold)}}
    if isinstance(strat, SmaCross):
        return {"type": "sma_cross",
                "params": {"fast": int(strat.fast), "slow": int(strat.slow)}}
    if isinstance(strat, ThermoBand):
        return {"type": "thermo",
                "params": {"enter_up": list(strat.enter_up),
                           "exit_down": list(strat.exit_down),
                           "exit_up": list(strat.exit_up)}}
    raise ValueError(f"unknown strategy type for serialization: {type(strat).__name__}")


def _json_safe(v):
    """JSON can't carry NaN/Inf. Convert to None so JS sees null."""
    if v is None:
        return None
    if isinstance(v, (int,)):
        return v
    f = float(v)
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _strategy_payload(asset: dict, close: pd.Series, open_: pd.Series, strat,
                      fee: float, slippage: float,
                      result) -> dict:
    """Everything the JS recompute needs: raw OHLC prices, strategy spec,
    params, and Python's metrics for parity verification on page load.
    Both `close` and `open_` are aligned and indexed by date."""
    from augury.strategies import ThermoBand
    closed_trades = [t for t in result.trades if t.status == "Closed"]
    payload = {
        "ticker": asset["ticker"],
        "kind": "simple",
        "dates": [d.strftime("%Y-%m-%d") for d in close.index],
        "close": [float(v) for v in close.values],
        "open":  [float(v) for v in open_.values],
        "strategy": _strategy_serialize(strat),
        "fee": float(fee),
        "slippage": float(slippage),
        "init_capital": float(backtest.DEFAULT_INIT_CAPITAL),
        "python_metrics": {k: _json_safe(v) for k, v in result.metrics.items()},
        "python_closed_trade_count": len(closed_trades),
    }
    if isinstance(strat, ThermoBand):
        # Carry the server-computed thermometer aligned to the close index;
        # the JS engine reads it for threshold-crossing signal generation.
        th = strat.thermo.reindex(close.index)
        payload["thermo"] = [
            float(v) if pd.notna(v) else None for v in th.values
        ]
    return payload


def _hybrid_payload(asset: dict, close: pd.Series, open_: pd.Series,
                    strat, prices: dict,
                    fee: float, slippage: float,
                    result) -> dict:
    """Hybrid version — same shape as simple payload but also embeds the
    substitute basket's close + open series so the JS engine can simulate
    the rotation client-side. `strategy` carries the base's spec; the JS
    side reads `substitutes` to drive the basket leg."""
    closed_trades = [t for t in result.trades if t.status == "Closed"]
    subs = []
    for ticker, weight in strat.substitutes.items():
        sp = prices[ticker]
        # Align to primary's calendar (close.index) — same trick as the
        # Python engine uses internally.
        s_close = sp["Close"].reindex(close.index).astype(float)
        s_open = sp["Open"].reindex(close.index).astype(float)
        subs.append({
            "ticker": ticker,
            "weight": float(weight),
            "close": [float(v) if pd.notna(v) else None for v in s_close.values],
            "open":  [float(v) if pd.notna(v) else None for v in s_open.values],
        })
    return {
        "ticker": asset["ticker"],
        "kind": "hybrid",
        "dates": [d.strftime("%Y-%m-%d") for d in close.index],
        "close": [float(v) for v in close.values],
        "open":  [float(v) for v in open_.values],
        # JS engine reads `strategy` to compute signals (same as simple
        # payload); for hybrid it's the BASE strategy's serialization.
        "strategy": _strategy_serialize(strat),
        "substitutes": subs,
        "fee": float(fee),
        "slippage": float(slippage),
        "init_capital": float(backtest.DEFAULT_INIT_CAPITAL),
        "python_metrics": {k: _json_safe(v) for k, v in result.metrics.items()},
        "python_closed_trade_count": len(closed_trades),
    }


def _format_metrics(m: dict) -> dict:
    return {
        "CAGR": _fmt_pct(m.get("cagr"), 1),
        "MDD": _fmt_pct_unsigned(m.get("max_drawdown"), 1),
        "MAE": _fmt_pct_unsigned(m.get("mae"), 1),
        "Vol": _fmt_pct_unsigned(m.get("vol"), 1),
        "Sharpe": _fmt_num(m.get("sharpe")),
        "Sortino": _fmt_num(m.get("sortino")),
        "Calmar": _fmt_num(m.get("calmar")),
        "WinRate": _fmt_pct_unsigned(m.get("win_rate"), 0),
        "Payoff": _fmt_num(m.get("payoff"), 1),
        "PF": _fmt_num(m.get("profit_factor"), 1),
        "EndNAV": _fmt_nav(m.get("end_nav")),
        "B&H": _fmt_nav(m.get("bh_end_nav")),
        "Trades": str(m.get("total_trades", 0)),
    }


def _build_strategy_panel(asset: dict, close: pd.Series, open_: pd.Series,
                          strat, prices: dict, idx: int,
                          display_start_ts: pd.Timestamp,
                          start, fee: float, slippage: float) -> tuple[dict, "BacktestResult"]:
    """Build one strategy panel's render dict + return the BacktestResult so
    the caller can use the first panel's overlays/trades for the price chart."""
    from augury.strategies import HybridStrategy
    is_hybrid = isinstance(strat, HybridStrategy)
    # Strategies that need OHLCV (e.g. ThermoBand) get the asset's full df
    # via prepare(); close-only strategies inherit the no-op default.
    strat.prepare(prices[asset["ticker"]])
    if is_hybrid:
        sub_prices = {t: prices[t] for t in strat.substitutes.keys()}
        result = backtest.run_hybrid(close, strat, open_, sub_prices,
                                      start=start, fee=fee, slippage=slippage)
    else:
        result = backtest.run(close, strat, open_, start=start, fee=fee, slippage=slippage)

    panel = {
        "idx": idx,
        "id": f"strat-{idx}",
        "kind": "hybrid" if is_hybrid else "simple",
        "label": strat.label(),
        "spec": strat.spec,
        "state": result.state,
        "state_since": result.state_since.strftime("%Y-%m-%d") if result.state_since else "—",
        "pending_action": result.pending_action,
        "backtest_params": (
            f"手续费 {fee*1e4:.0f}bps · 滑点 {slippage*1e4:.0f}bps · T+1 开盘成交"
        ),
        "backtest_window": f"{start or close.index[0].strftime('%Y-%m-%d')} → "
                           f"{close.index[-1].strftime('%Y-%m-%d')}",
        "metric_info": METRIC_INFO,
        "metrics": _format_metrics(result.metrics),
        "equity_chart_html": _equity_chart(result.equity, result.bh_equity,
                                            default_start=display_start_ts),
        "drawdown_chart_html": _drawdown_chart(result.drawdown,
                                                default_start=display_start_ts),
        "pnl_chart_html": _trade_pnl_chart(result.trades),
        # SSR'd trade list — populated for hybrid panels (JS doesn't re-render
        # them). For simple panels, JS overwrites on page load anyway, but we
        # include SSR rows as a no-JS fallback.
        "trades": [
            {
                "entry_date": t.entry_date.strftime("%Y-%m-%d"),
                "exit_date": t.exit_date.strftime("%Y-%m-%d") if t.exit_date else "—",
                "entry_price": f"{t.entry_price:.2f}",
                "exit_price": f"{t.exit_price:.2f}" if t.exit_price else "—",
                "ret": _fmt_pct(t.ret, 1),
                "ret_pos": t.ret > 0,
                "mae": _fmt_pct(t.mae, 1),
                "status": t.status,
            }
            for t in reversed(result.trades)
        ],
    }

    # Reference price comparison (e.g. "MA5 vs MA30 +12.64%")
    ref = strat.reference(close)
    if ref.get("pct") is not None:
        panel["ref_label"] = ref["label"]
        panel["dist_pct"] = ref["pct"]
        panel["dist_label"] = _fmt_pct(ref["pct"], 2)

    # Client-side recompute payload. Both simple AND hybrid have JS engines;
    # the JS reads `kind` to dispatch.
    if is_hybrid:
        payload = _hybrid_payload(asset, close, open_, strat, prices,
                                   fee, slippage, result)
    else:
        payload = _strategy_payload(asset, close, open_, strat,
                                     fee, slippage, result)
    panel["payload_json"] = json.dumps(payload, separators=(",", ":"))
    return panel, result


def _build_strategy_asset(asset: dict, prices: dict) -> dict:
    """Render-ready dict for a strategy asset card.

    Asset-level fields (logo, name, last price, price chart) and per-strategy
    panels (metrics, equity, trades). Multiple strategies render as a tab
    switcher; the first strategy drives the asset's price chart overlays."""
    out = {
        "ticker": asset["ticker"],
        "name": asset.get("name", asset["ticker"]),
        "logo": asset.get("logo", ""),
        "breadth": asset.get("breadth"),
    }
    df = prices.get(asset["ticker"])
    if df is None or df.empty:
        out["status"] = "no_data"
        return out
    close = df["Close"].dropna()
    open_ = df["Open"].reindex(close.index)
    out["status"] = "ok"
    out["last_price"] = float(close.iloc[-1])
    out["last_date"] = close.index[-1].strftime("%Y-%m-%d")

    strategies = asset.get("strategies") or []
    if not strategies:
        out["mode"] = "learning"
        out["chart_html"] = _learning_chart(df)
        return out

    out["mode"] = "strategy"
    start = asset.get("backtest_start", backtest.DEFAULT_START)
    fee = asset.get("fee", backtest.DEFAULT_FEE)
    slippage = asset.get("slippage", backtest.DEFAULT_SLIPPAGE)

    # Date bounds — depend on close index, not on which strategy.
    data_start_ts = close.index[0]
    end_ts = close.index[-1]
    display_default = asset.get("display_start") or backtest.DISPLAY_DEFAULT_START
    display_start_ts = max(data_start_ts, pd.Timestamp(display_default))
    out["data_start"] = data_start_ts.strftime("%Y-%m-%d")
    out["display_start"] = display_start_ts.strftime("%Y-%m-%d")
    out["window_end"] = end_ts.strftime("%Y-%m-%d")

    # Build each strategy panel; keep the first one's result for the
    # asset-level price chart overlays.
    panels = []
    first_strat = strategies[0]
    first_result = None
    for idx, strat in enumerate(strategies):
        panel, result = _build_strategy_panel(
            asset, close, open_, strat, prices, idx,
            display_start_ts, start, fee, slippage,
        )
        panels.append(panel)
        if idx == 0:
            first_result = result
    out["strategies"] = panels

    out["price_chart_html"] = _strategy_price_chart(
        close, first_strat.overlay(close), first_result.trades,
        default_start=display_start_ts,
    )
    return out


def render_strategy_page(page_key: str, prices: dict) -> None:
    """Render one strategy sub-page (indices / stocks / learning / crypto).

    Each section in `cfg['sections']` IS an asset; we resolve it through
    `_build_strategy_asset` once and pass the enriched list to the template."""
    cfg = layout.strategy_page(page_key)
    assets = [_build_strategy_asset(sec, prices) for sec in cfg["sections"]]
    breadth_sections: list[dict] = []
    if cfg.get("extras") == "breadth":
        default_start = pd.Timestamp(backtest.DISPLAY_DEFAULT_START)
        for sec in cfg["sections"]:
            bid = sec.get("breadth")
            if not bid:
                continue
            breadth_df = indicators.load(bid)
            price_df = prices.get(sec["ticker"])
            if breadth_df is None or breadth_df.empty \
                    or price_df is None or price_df.empty:
                continue
            close = price_df["Close"].dropna()
            breadth_sections.append(_breadth_section(
                ticker=sec["ticker"], name=sec.get("name", sec["ticker"]),
                breadth_id=bid, close=close,
                breadth=breadth_df["value"], default_start=default_start,
            ))
    updated, run = _refresh_ctx()
    html = env.get_template("strategy_page.html").render(
        updated=updated, run=run, page=page_key,
        page_cfg=cfg, assets=assets,
        breadth_sections=breadth_sections,
        pages=layout.sidebar_strategy_pages(),
        section_nav=layout.sidebar_strategy_pages(),
    )
    (DOCS_DIR / f"{page_key}.html").write_text(html)


STRATEGY_PAGE_KEYS = ("indices", "stocks", "learning", "crypto")


def render_health_page() -> None:
    """Lists every registered indicator's refresh state. Unhealthy sources
    (most-recent attempt failed) sort to the top. Reached via the clickable
    timestamp+status pill in the global header on every page."""
    all_m = indicators.all_meta()
    rows = []
    for ind_id, ind in indicators.REGISTRY.items():
        m = all_m.get(ind_id) or {}
        last_a = m.get("last_attempt")
        last_s = m.get("last_success")
        unhealthy = bool(last_a) and last_s != last_a
        rows.append({
            "id": ind_id,
            "title": ind.title,
            "source_url": ind.source_url,
            "frequency": ind.frequency,
            "last_success": last_s,
            "last_attempt": last_a,
            "error": m.get("last_error"),
            "unhealthy": unhealthy,
        })
    rows.sort(key=lambda r: (0 if r["unhealthy"] else 1, r["id"]))

    updated, run = _refresh_ctx()
    html = env.get_template("health.html").render(
        updated=updated, run=run, page="health",
        rows=rows,
    )
    (DOCS_DIR / "health.html").write_text(html)


def all() -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    prices = {pid: indicators.load(pid) for pid in layout.PRICE_IDS}
    render_index(prices)
    for key in layout.PAGES:
        render_macro_page(key, prices)
    render_strategy_overview(prices)
    for key in STRATEGY_PAGE_KEYS:
        render_strategy_page(key, prices)
    render_health_page()
    # Old single-page artifact, no longer rendered. Delete if present so the
    # docs/ tree doesn't carry stale routes.
    stale = DOCS_DIR / "strategies.html"
    if stale.exists():
        stale.unlink()
