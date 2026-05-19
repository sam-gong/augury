"""Backtest engine.

Thin adapter around vectorbt. All vectorbt usage is contained in `run()`;
strategies produce `(entries, exits)` signals, the renderer consumes
`BacktestResult`. To swap engines, change this file only.

Execution model: **T+1 open**. Signals are computed from close[T] (e.g.
MA crossover observed after market close); orders execute at open[T+1] —
the earliest opportunity a human trader can act on, since you can't trade
at a close you've only just observed. NAV and drawdown still mark-to-market
on close prices."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import math
import numpy as np
import pandas as pd
import vectorbt as vbt

from augury.strategies import Strategy, HybridStrategy

DEFAULT_START: str | None = None
DEFAULT_FEE = 3e-4         # 3 bps per side — US equities rarely cost more
                           # than this round-trip with discount brokers.
DEFAULT_SLIPPAGE = 0.0     # T+1 open is conservative enough — opens at the
                           # public auction price, no further haircut needed.
DEFAULT_INIT_CAPITAL = 100.0

# What date the chart / picker should default to (purely a display choice).
# The backtest itself still spans the full data range so users can zoom back.
DISPLAY_DEFAULT_START = "2015-01-01"


@dataclass
class Trade:
    entry_date: pd.Timestamp
    exit_date: Optional[pd.Timestamp]
    entry_price: float
    exit_price: Optional[float]
    pnl: float
    ret: float          # signed return, decimal (0.10 = +10%)
    status: str         # 'Closed' | 'Open'
    mae: float          # max adverse excursion within trade, decimal (≤ 0)


@dataclass
class BacktestResult:
    # Time series (indexed by date, full backtest window)
    close:     pd.Series
    equity:    pd.Series        # strategy NAV (init 1.0)
    bh_equity: pd.Series        # buy-and-hold NAV (init 1.0)
    drawdown:  pd.Series        # equity / cummax − 1 (≤ 0)
    position:  pd.Series        # 0/1 long-flat indicator
    # Trade records
    trades:    list[Trade] = field(default_factory=list)
    # Aggregate metrics — see _metrics() for keys
    metrics:   dict = field(default_factory=dict)
    # Strategy state right now
    state:     str = "flat"     # 'long' | 'flat'
    state_since: Optional[pd.Timestamp] = None
    # What to do at next open — derived from the latest close-based signal
    # and our current position. 'buy'  : flat now, signal says enter at next open.
    #                          'sell' : long now, signal says exit at next open.
    #                          'hold' : no fresh signal — keep current position.
    pending_action: str = "hold"


def _annualized_vol(returns: pd.Series) -> float:
    return float(returns.std() * math.sqrt(252))


def _cagr(equity: pd.Series) -> float:
    if equity.empty or equity.iloc[0] <= 0:
        return float("nan")
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    if years <= 0:
        return float("nan")
    return float(equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1


def _compute_trade_mae(trade: Trade, close: pd.Series) -> float:
    """For an open trade, look from entry to today; for closed, entry to exit."""
    end = trade.exit_date if trade.exit_date is not None else close.index[-1]
    window = close.loc[trade.entry_date:end]
    if window.empty:
        return 0.0
    return float(window.min() / trade.entry_price - 1)


def run(close: pd.Series,
        strategy: Strategy,
        open_: pd.Series,
        start: str | pd.Timestamp | None = DEFAULT_START,
        fee: float = DEFAULT_FEE,
        slippage: float = DEFAULT_SLIPPAGE,
        init_capital: float = DEFAULT_INIT_CAPITAL) -> BacktestResult:
    """Run `strategy` against `close` with T+1 open execution.

    `start=None` means "use all available data". We slice price BEFORE
    computing rolling stats — so the SMA at the start of the window is
    NaN, which produces no signal, which is the correct behavior (no
    trading until enough history).

    T+1 mechanics: `strategy.signals(close)` produces entries/exits keyed
    by close-date T. We shift them forward one bar so vbt sees them at
    T+1, then pass `open_` as the execution price. NAV stays close-based.
    The very last bar's signal is dropped (no T+1 in the window) — it's
    surfaced separately as `pending_action`."""
    close = close.dropna().astype(float)
    open_ = open_.reindex(close.index).astype(float)
    if start is not None:
        start_ts = pd.Timestamp(start)
        close = close[close.index >= start_ts]
        open_ = open_[open_.index >= start_ts]
    if close.empty:
        return _empty_result(close)

    raw_entries, raw_exits = strategy.signals(close)
    raw_entries = raw_entries.astype("boolean").fillna(False).astype(bool)
    raw_exits = raw_exits.astype("boolean").fillna(False).astype(bool)
    # T+1 execution: yesterday's close-based signal fires today at open.
    entries = raw_entries.shift(1, fill_value=False)
    exits = raw_exits.shift(1, fill_value=False)

    pf = vbt.Portfolio.from_signals(
        close, entries=entries, exits=exits,
        price=open_,
        fees=fee, slippage=slippage, freq="1D",
        init_cash=init_capital,
    )
    # Buy-and-hold also enters at first available open (consistent with
    # the strategy's earliest-possible entry mechanic).
    bh_entries = pd.Series(False, index=close.index)
    bh_entries.iloc[0] = True
    bh = vbt.Portfolio.from_signals(
        close, entries=bh_entries, exits=pd.Series(False, index=close.index),
        price=open_,
        fees=fee, slippage=slippage, freq="1D",
        init_cash=init_capital,
    )

    equity = (pf.value() / init_capital).rename("equity")
    bh_equity = (bh.value() / init_capital).rename("bh_equity")
    drawdown = pf.drawdown().rename("drawdown")
    shares = pf.asset_flow().cumsum()
    position = (shares > 1e-9).astype(int).rename("position")

    trades = _extract_trades(pf, close)
    state = "long" if position.iloc[-1] == 1 else "flat"
    state_since = _state_since(position)
    pending = _pending_action(raw_entries, raw_exits, state)

    metrics = _metrics(pf, bh, equity, bh_equity, drawdown, trades)

    return BacktestResult(
        close=close, equity=equity, bh_equity=bh_equity,
        drawdown=drawdown, position=position,
        trades=trades, metrics=metrics,
        state=state, state_since=state_since,
        pending_action=pending,
    )


def _pending_action(raw_entries: pd.Series, raw_exits: pd.Series,
                    current_state: str) -> str:
    """The signal observed on the last close that wasn't executed yet.
    Maps to 'buy' (flat → enter), 'sell' (long → exit), or 'hold'."""
    if raw_entries.empty:
        return "hold"
    e_last = bool(raw_entries.iloc[-1])
    x_last = bool(raw_exits.iloc[-1])
    # conflict_mode='ignore' parity: both fire → no-op
    if e_last == x_last:
        return "hold"
    if e_last and current_state == "flat":
        return "buy"
    if x_last and current_state == "long":
        return "sell"
    return "hold"


def _empty_result(close: pd.Series) -> BacktestResult:
    empty = pd.Series(dtype=float)
    return BacktestResult(
        close=close, equity=empty, bh_equity=empty,
        drawdown=empty, position=pd.Series(dtype=int),
        trades=[], metrics={}, state="flat", state_since=None,
    )


def _extract_trades(pf, close: pd.Series) -> list[Trade]:
    rec = pf.trades.records_readable
    out: list[Trade] = []
    for _, r in rec.iterrows():
        entry_date = pd.Timestamp(r["Entry Timestamp"]).normalize()
        is_open = r["Status"] != "Closed"
        exit_date = None if is_open else pd.Timestamp(r["Exit Timestamp"]).normalize()
        entry_price = float(r["Avg Entry Price"])
        exit_price = None if is_open else float(r["Avg Exit Price"])
        t = Trade(
            entry_date=entry_date, exit_date=exit_date,
            entry_price=entry_price, exit_price=exit_price,
            pnl=float(r["PnL"]), ret=float(r["Return"]),
            status="Open" if is_open else "Closed",
            mae=0.0,
        )
        t.mae = _compute_trade_mae(t, close)
        out.append(t)
    return out


def _state_since(position: pd.Series) -> Optional[pd.Timestamp]:
    """Date of the most recent state transition (long→flat or flat→long).
    Returns None if there's never been a transition (i.e. always one state)."""
    if position.empty:
        return None
    changes = position != position.shift(1)
    changes.iloc[0] = False  # the first sample isn't a change
    indices = position.index[changes]
    if len(indices) == 0:
        return position.index[0]
    return indices[-1]


def _metrics(pf, bh, equity: pd.Series, bh_equity: pd.Series,
             drawdown: pd.Series, trades: list[Trade]) -> dict:
    """All ratio-style metrics come straight from vectorbt's pf.stats()
    so the page matches the numbers the user has previously validated.
    Vol and MAE we compute (vbt doesn't surface them in stats())."""
    if equity.empty:
        return {}
    stats = pf.stats()
    strat_ret = equity.pct_change().dropna()
    closed = [t for t in trades if t.status == "Closed"]
    wins = [t for t in closed if t.ret > 0]
    losses = [t for t in closed if t.ret <= 0]
    avg_win = float(np.mean([t.ret for t in wins])) if wins else float("nan")
    avg_loss = float(np.mean([t.ret for t in losses])) if losses else float("nan")
    payoff = abs(avg_win / avg_loss) if losses and avg_loss != 0 else float("nan")
    mae_all = min((t.mae for t in trades), default=0.0)

    return {
        "cagr": _cagr(equity),
        "max_drawdown": -abs(float(stats["Max Drawdown [%]"])) / 100,
        "mae": mae_all,
        "vol": _annualized_vol(strat_ret),
        "sharpe": float(stats["Sharpe Ratio"]),
        "sortino": float(stats["Sortino Ratio"]),
        "calmar": float(stats["Calmar Ratio"]),
        "win_rate": float(stats["Win Rate [%]"]) / 100 if not pd.isna(stats["Win Rate [%]"]) else float("nan"),
        "payoff": payoff,
        "profit_factor": float(stats["Profit Factor"]) if "Profit Factor" in stats and not pd.isna(stats["Profit Factor"]) else float("nan"),
        "end_nav": float(equity.iloc[-1]),
        "bh_end_nav": float(bh_equity.iloc[-1]),
        "total_trades": int(stats["Total Trades"]),
        "closed_trades": int(stats["Total Closed Trades"]),
        "open_trades": int(stats["Total Open Trades"]),
    }


# =====================================================================
# Hybrid engine — primary + substitute basket
# =====================================================================
# Used by `HybridStrategy`: when the base strategy is "long primary",
# hold the primary asset; otherwise rotate capital into the substitute
# basket (weighted, buy-and-hold during the out-of-primary period; no
# daily rebalance).
#
# Execution conventions:
#   - All trades execute at T+1 open (signal observed on close[T]).
#   - First bar of backtest: start in substitute basket (no signal yet).
#   - Fees: 3bps PER LEG. Switch sub→primary costs 1+N fees (sell N subs,
#     buy 1 primary). Switch primary→sub costs 1+N fees.
#
# Metrics computed manually to match the vbt conventions used in `run()`:
# vol uses sqrt(252), Sharpe/Sortino/Calmar use sqrt(365)/365 (vbt's
# default annualization).


def _max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    dd = equity / equity.cummax() - 1
    return float(dd.min())


def _sharpe(returns: pd.Series) -> float:
    if returns.empty or returns.std() == 0:
        return float("nan")
    return float(returns.mean() / returns.std(ddof=1) * math.sqrt(365))


def _sortino(returns: pd.Series) -> float:
    if returns.empty or len(returns) <= 1:
        return float("nan")
    # vbt: square only negative returns, divide by (n-1) (full sample),
    # take sqrt → downside std. Same as JS engine.
    negs = returns[returns < 0]
    if negs.empty:
        return float("nan")
    ds_var = (negs ** 2).sum() / (len(returns) - 1)
    ds_std = math.sqrt(ds_var)
    if ds_std == 0:
        return float("nan")
    return float(returns.mean() / ds_std * math.sqrt(365))


def _calmar(equity: pd.Series, mdd: float, n_returns: int) -> float:
    """vbt convention: ann_return = (1+total)^(365/n_returns)-1, divided by |mdd|.
    Differs from CAGR (which uses real elapsed years)."""
    if mdd >= 0 or equity.empty or equity.iloc[0] <= 0 or n_returns <= 0:
        return float("nan")
    total_ret = float(equity.iloc[-1] / equity.iloc[0]) - 1
    ann = (1 + total_ret) ** (365 / n_returns) - 1
    return ann / abs(mdd)


def _hybrid_metrics(equity: pd.Series, bh_equity: pd.Series,
                    trades: list[Trade]) -> dict:
    """Same metric set as `_metrics()`, computed without vectorbt."""
    if equity.empty:
        return {}
    rets = equity.pct_change().dropna()
    closed = [t for t in trades if t.status == "Closed"]
    open_t = [t for t in trades if t.status == "Open"]
    wins = [t for t in closed if t.pnl > 0]
    losses = [t for t in closed if t.pnl <= 0]
    win_pnl = sum(t.pnl for t in wins)
    loss_pnl = sum(abs(t.pnl) for t in losses)
    avg_win = float(np.mean([t.ret for t in wins])) if wins else float("nan")
    avg_loss = float(np.mean([t.ret for t in losses])) if losses else float("nan")
    mdd = _max_drawdown(equity)

    return {
        "cagr": _cagr(equity),
        "max_drawdown": mdd,
        "mae": min((t.mae for t in trades), default=0.0),
        "vol": _annualized_vol(rets),
        "sharpe": _sharpe(rets),
        "sortino": _sortino(rets),
        "calmar": _calmar(equity, mdd, len(rets)),
        "win_rate": (len(wins) / len(closed)) if closed else float("nan"),
        "payoff": abs(avg_win / avg_loss) if losses and avg_loss != 0 else float("nan"),
        "profit_factor": (win_pnl / loss_pnl) if loss_pnl > 0 else float("nan"),
        "end_nav": float(equity.iloc[-1]),
        "bh_end_nav": float(bh_equity.iloc[-1]) if not bh_equity.empty else float("nan"),
        "total_trades": len(trades),
        "closed_trades": len(closed),
        "open_trades": len(open_t),
    }


def run_hybrid(close: pd.Series,
               strategy: HybridStrategy,
               open_: pd.Series,
               substitute_prices: dict[str, pd.DataFrame],
               start: str | pd.Timestamp | None = DEFAULT_START,
               fee: float = DEFAULT_FEE,
               slippage: float = DEFAULT_SLIPPAGE,
               init_capital: float = DEFAULT_INIT_CAPITAL) -> BacktestResult:
    """Primary asset + substitute basket. Always 100% invested.

    Same signal generation as the base strategy. Different execution:
    when base says exit, rotate capital into `strategy.substitutes` at
    fixed weights (no rebalance during the holding period). When base
    says enter, sell the basket and buy primary.

    Trades returned: only the primary's entries/exits (substitute
    holdings are between-trades bookkeeping). NAV/equity reflects
    everything including substitute returns."""
    close = close.dropna().astype(float)
    open_ = open_.reindex(close.index).astype(float)

    subs = list(strategy.substitutes.keys())
    weights = strategy.substitutes
    sub_close = pd.DataFrame({
        t: substitute_prices[t]["Close"].reindex(close.index).astype(float)
        for t in subs
    })
    sub_open = pd.DataFrame({
        t: substitute_prices[t]["Open"].reindex(close.index).astype(float)
        for t in subs
    })

    if start is not None:
        start_ts = pd.Timestamp(start)
        mask = close.index >= start_ts
        close = close[mask]
        open_ = open_[mask]
        sub_close = sub_close[mask]
        sub_open = sub_open[mask]

    if close.empty:
        return _empty_result(close)

    raw_e, raw_x = strategy.base.signals(close)
    raw_e = raw_e.astype("boolean").fillna(False).astype(bool)
    raw_x = raw_x.astype("boolean").fillna(False).astype(bool)
    # T+1 execution: signal at T fires at open[T+1]
    entries = raw_e.shift(1, fill_value=False)
    exits = raw_x.shift(1, fill_value=False)

    n = len(close)
    dates = close.index
    equity_arr = np.full(n, np.nan)
    position_arr = np.zeros(n, dtype=int)
    trades: list[Trade] = []

    # State
    in_primary = False
    primary_shares = 0.0
    primary_entry_date = None
    primary_entry_price = 0.0
    primary_entry_capital = 0.0
    sub_shares = {t: 0.0 for t in subs}

    # Initialize: buy substitute basket at open[0]
    nav = init_capital
    for t in subs:
        leg = nav * weights[t]
        px = float(sub_open[t].iloc[0])
        if not np.isnan(px) and px > 0:
            sub_shares[t] = leg * (1 - fee) / px

    for i in range(n):
        # 1. Act on signal (executes at open[i])
        if i > 0:
            if entries.iloc[i] and not in_primary:
                # Sell substitutes at open[i], buy primary
                proceeds = 0.0
                for t in subs:
                    px = float(sub_open[t].iloc[i])
                    if np.isnan(px):
                        px = float(sub_close[t].iloc[i - 1])
                    proceeds += sub_shares[t] * px * (1 - fee)
                    sub_shares[t] = 0.0
                primary_px = float(open_.iloc[i])
                if not np.isnan(primary_px) and primary_px > 0:
                    primary_shares = proceeds * (1 - fee) / primary_px
                    primary_entry_date = dates[i]
                    primary_entry_price = primary_px
                    # Gross capital before buy fee — so the trade's reported
                    # `ret` includes both the entry and exit fees, matching
                    # the convention vbt uses in `run()`.
                    primary_entry_capital = proceeds
                    in_primary = True
            elif exits.iloc[i] and in_primary:
                # Sell primary, buy substitute basket
                primary_px = float(open_.iloc[i])
                proceeds = primary_shares * primary_px * (1 - fee)
                # Record closed primary trade
                gross_ret = primary_px / primary_entry_price - 1
                # Net return is approximately gross - 2*fee (entry fee + exit fee).
                # Exact: (proceeds / primary_entry_capital) - 1
                net_ret = (proceeds / primary_entry_capital) - 1 if primary_entry_capital > 0 else gross_ret
                pnl = proceeds - primary_entry_capital
                trades.append(Trade(
                    entry_date=primary_entry_date,
                    exit_date=dates[i],
                    entry_price=primary_entry_price,
                    exit_price=primary_px,
                    pnl=pnl,
                    ret=net_ret,
                    status="Closed",
                    mae=0.0,  # computed later from close window
                ))
                primary_shares = 0.0
                in_primary = False
                # Buy substitutes
                for t in subs:
                    leg = proceeds * weights[t]
                    px = float(sub_open[t].iloc[i])
                    if np.isnan(px) or px <= 0:
                        continue
                    sub_shares[t] = leg * (1 - fee) / px

        # 2. Compute NAV at close[i]
        if in_primary:
            nav = primary_shares * close.iloc[i]
        else:
            nav = sum(sub_shares[t] * sub_close[t].iloc[i] for t in subs)
        equity_arr[i] = nav
        position_arr[i] = 1 if in_primary else 0

    # If still in primary at the end, record open trade
    if in_primary:
        last_close = float(close.iloc[-1])
        notional = primary_shares * last_close
        gross_ret = last_close / primary_entry_price - 1
        # Open trade ret reflects mark-to-market value (no exit fee)
        net_ret = (notional / primary_entry_capital) - 1 if primary_entry_capital > 0 else gross_ret
        pnl = notional - primary_entry_capital
        trades.append(Trade(
            entry_date=primary_entry_date,
            exit_date=None,
            entry_price=primary_entry_price,
            exit_price=None,
            pnl=pnl,
            ret=net_ret,
            status="Open",
            mae=0.0,
        ))

    equity = pd.Series(equity_arr / init_capital, index=dates, name="equity")
    position = pd.Series(position_arr, index=dates, name="position")
    drawdown = (equity / equity.cummax() - 1).rename("drawdown")

    # B&H benchmark = buy primary at open[0] and hold (same as run())
    primary_first_open = float(open_.iloc[0])
    bh_shares = init_capital * (1 - fee) / primary_first_open if primary_first_open > 0 else 0
    bh_equity = pd.Series(bh_shares * close.values / init_capital,
                          index=dates, name="bh_equity")

    # Compute MAE per trade (drawdown from entry within hold window)
    for t in trades:
        t.mae = _compute_trade_mae(t, close)

    state = "long" if (in_primary or (n > 0 and position_arr[-1] == 1)) else "flat"
    state_since = _state_since(position)
    pending = _pending_action(raw_e, raw_x, state)
    metrics = _hybrid_metrics(equity, bh_equity, trades)

    return BacktestResult(
        close=close, equity=equity, bh_equity=bh_equity,
        drawdown=drawdown, position=position,
        trades=trades, metrics=metrics,
        state=state, state_since=state_since,
        pending_action=pending,
    )
