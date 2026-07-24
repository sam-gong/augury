// Backtest engine — vectorbt `from_signals` default semantics, hand-ported to JS.
// SINGLE SOURCE OF TRUTH: render.py inlines this into every page; parity_check.mjs
// imports it for headless JS-vs-Python parity. Keep it DOM-free so browser and
// node run the identical code.

var Backtest = (function() {
  // pandas .rolling(n).mean() — NaN for the first n-1 elements.
  function sma(arr, n) {
    var out = new Array(arr.length);
    var sum = 0;
    for (var i = 0; i < arr.length; i++) {
      sum += arr[i];
      if (i >= n) sum -= arr[i - n];
      out[i] = (i >= n - 1) ? sum / n : NaN;
    }
    return out;
  }

  function signalsSmaBand(close, p) {
    var ma = sma(close, p.ma);
    var en = new Array(close.length);
    var ex = new Array(close.length);
    var up = new Array(close.length);
    var lo = new Array(close.length);
    for (var i = 0; i < close.length; i++) {
      if (isNaN(ma[i])) {
        en[i] = false; ex[i] = false;
        up[i] = NaN; lo[i] = NaN;
        continue;
      }
      up[i] = ma[i] * (1 + p.threshold);
      lo[i] = ma[i] * (1 - p.threshold);
      // Cross-based, not level-based (mirrors augury.strategies.SmaBand):
      // enter on a cross UP through the upper band, exit on a cross DOWN
      // through the lower band — "already above" is not a crossing.
      var prevUp = i > 0 ? up[i - 1] : NaN;
      var prevLo = i > 0 ? lo[i - 1] : NaN;
      en[i] = !isNaN(prevUp) && close[i] > up[i] && close[i - 1] <= prevUp;
      ex[i] = !isNaN(prevLo) && close[i] < lo[i] && close[i - 1] >= prevLo;
    }
    return {entries: en, exits: ex, overlays: {ma: ma, upper: up, lower: lo}};
  }

  function signalsSmaCross(close, p) {
    var f = sma(close, p.fast);
    var s = sma(close, p.slow);
    var en = new Array(close.length);
    var ex = new Array(close.length);
    for (var i = 0; i < close.length; i++) {
      // Cross-based, not level-based (mirrors augury.strategies.SmaCross):
      // golden cross to enter, death cross to exit — "already above" is
      // not a crossing.
      if (isNaN(f[i]) || isNaN(s[i]) || i === 0 || isNaN(f[i-1]) || isNaN(s[i-1])) {
        en[i] = false; ex[i] = false; continue;
      }
      en[i] = f[i] > s[i] && f[i-1] <= s[i-1];
      ex[i] = f[i] < s[i] && f[i-1] >= s[i-1];
    }
    return {entries: en, exits: ex, overlays: {ma_fast: f, ma_slow: s}};
  }

  // ThermoBand: the thermometer is too expensive to recompute in JS
  // (8 oscillators + composite). The server hands us a pre-computed
  // thermo array aligned to `close`; we apply level-crossing logic.
  // `null` entries in `thermo` mark warmup bars where no signal fires.
  function signalsThermo(close, p, thermo) {
    var n = close.length;
    var en = new Array(n), ex = new Array(n);
    en[0] = false; ex[0] = false;
    for (var i = 1; i < n; i++) {
      en[i] = false; ex[i] = false;
      var t = thermo[i], tp = thermo[i - 1];
      if (t == null || tp == null) continue;
      for (var j = 0; j < p.enter_up.length; j++) {
        var l = p.enter_up[j];
        if (t > l && tp <= l) en[i] = true;
      }
      for (var j = 0; j < p.exit_down.length; j++) {
        var l = p.exit_down[j];
        if (t < l && tp >= l) ex[i] = true;
      }
      for (var j = 0; j < p.exit_up.length; j++) {
        var l = p.exit_up[j];
        if (t > l && tp <= l) ex[i] = true;
      }
    }
    return {entries: en, exits: ex, overlays: {}};
  }

  // precomp_band: cross-asset SmaBand (e.g. BTC→META). Signal source is a
  // different asset whose MA can't be reproduced from `close` alone, so the
  // server precomputed entries/exits aligned to `close` — we just pass them
  // through. Same pattern as thermo, but here the precompute is the
  // signals themselves rather than a continuous oscillator.
  function signalsPrecompBand(close, p, slice) {
    return {entries: slice.precomp_entries || [],
            exits:   slice.precomp_exits   || [],
            overlays: {}};
  }

  function signals(close, strategy, slice) {
    if (strategy.type === 'sma_band')     return signalsSmaBand(close, strategy.params);
    if (strategy.type === 'sma_cross')    return signalsSmaCross(close, strategy.params);
    if (strategy.type === 'thermo')       return signalsThermo(close, strategy.params, slice.thermo);
    if (strategy.type === 'precomp_band') return signalsPrecompBand(close, strategy.params, slice);
    throw new Error('unknown strategy: ' + strategy.type);
  }

  // Execution model: **T+1 open**. Yesterday's close-based signal fires
  // at today's open. Mirrors augury.backtest with `open_` passed in:
  //   - signal[T] computed from close[T] (in `signals()`)
  //   - we evaluate signal[i-1] at bar i, execute at open[i]
  //   - LongOnly, accumulate=False, conflict_mode='ignore'
  //   - fees on order value, slippage on price
  //   - buy with all cash: shares = cash / (open[i]*(1+slip)*(1+fee))
  //   - sell all shares: proceeds = shares * open[i]*(1-slip)*(1-fee)
  //   - NAV marked-to-close: equity[i] = cash + shares * close[i]
  function runPortfolio(close, opens, dates, entries, exits, fee, slip, cap,
                        carryEntry, carryExit) {
    var n = close.length;
    var cash = cap, shares = 0;
    var equityRaw = new Array(n);
    var trades = [];
    var openTrade = null;

    for (var i = 0; i < n; i++) {
      // Use yesterday's signal to decide today's open execution. At the
      // first in-window bar there's no prior bar inside the slice, so we
      // fall back to the carried signal from the bar just before the
      // window — this lets a position open right at the window start when
      // the regime is already active (mirrors Python's shift-then-slice).
      var sigIdx = i - 1;
      var flat = shares < 1e-12;
      var e = sigIdx >= 0 ? entries[sigIdx] === true : carryEntry === true;
      var x = sigIdx >= 0 ? exits[sigIdx] === true : carryExit === true;
      // conflict_mode='ignore': both → no-op
      var action = (e === x) ? null : (e ? 'buy' : 'sell');

      if (action === 'buy' && flat) {
        var preCash = cash;
        var bp = opens[i] * (1 + slip);
        shares = preCash / (bp * (1 + fee));
        cash = 0;
        openTrade = {entryIdx: i, entryPrice: bp,
                     entryCash: preCash, entryShares: shares};
      } else if (action === 'sell' && !flat) {
        var sp = opens[i] * (1 - slip);
        var proceeds = shares * sp * (1 - fee);
        // MAE = lowest close within [entry, exit] vs entry price.
        var lo2 = Infinity;
        for (var j = openTrade.entryIdx; j <= i; j++) {
          if (close[j] < lo2) lo2 = close[j];
        }
        trades.push({
          entryIdx: openTrade.entryIdx, entryDate: dates[openTrade.entryIdx],
          entryPrice: openTrade.entryPrice,
          exitIdx: i, exitDate: dates[i], exitPrice: sp,
          pnl: proceeds - openTrade.entryCash,
          ret: proceeds / openTrade.entryCash - 1,
          mae: lo2 / openTrade.entryPrice - 1,
          status: 'Closed',
        });
        cash = proceeds; shares = 0; openTrade = null;
      }
      equityRaw[i] = cash + shares * close[i];
    }

    // Open trade at end: mark-to-market at last close (no exit fee).
    if (openTrade && n > 0) {
      var last = n - 1;
      var lo3 = Infinity;
      for (var k = openTrade.entryIdx; k <= last; k++) {
        if (close[k] < lo3) lo3 = close[k];
      }
      var mtm = openTrade.entryShares * close[last];
      trades.push({
        entryIdx: openTrade.entryIdx, entryDate: dates[openTrade.entryIdx],
        entryPrice: openTrade.entryPrice,
        exitIdx: null, exitDate: null, exitPrice: null,
        pnl: mtm - openTrade.entryCash,
        ret: mtm / openTrade.entryCash - 1,
        mae: lo3 / openTrade.entryPrice - 1,
        status: 'Open',
      });
    }

    // Normalize NAV so series starts at 1.0.
    var equity = new Array(n);
    for (var m = 0; m < n; m++) equity[m] = equityRaw[m] / cap;

    // B&H: buy at open[0] (T+1 semantics → enter immediately).
    var bhEquity = new Array(n);
    if (n > 0) {
      var bhP = opens[0] * (1 + slip);
      var bhShares = cap / (bhP * (1 + fee));
      for (var q = 0; q < n; q++) bhEquity[q] = (bhShares * close[q]) / cap;
    }

    // Drawdown — equity / running peak - 1.
    var dd = new Array(n);
    var peak = -Infinity;
    for (var r = 0; r < n; r++) {
      if (equity[r] > peak) peak = equity[r];
      dd[r] = peak > 0 ? equity[r] / peak - 1 : 0;
    }

    return {equity: equity, bhEquity: bhEquity, drawdown: dd, trades: trades};
  }

  function dailyReturns(eq) {
    var out = [];
    for (var i = 1; i < eq.length; i++) {
      if (eq[i-1] > 0) out.push(eq[i] / eq[i-1] - 1);
    }
    return out;
  }
  function mean(a) {
    if (!a.length) return NaN;
    var s = 0;
    for (var i = 0; i < a.length; i++) s += a[i];
    return s / a.length;
  }
  function stdSample(a) {
    if (a.length < 2) return NaN;
    var mu = mean(a);
    var ss = 0;
    for (var i = 0; i < a.length; i++) { var d = a[i] - mu; ss += d * d; }
    return Math.sqrt(ss / (a.length - 1));
  }
  function cagrFromEquity(equity, dates) {
    if (!equity.length) return NaN;
    var n = equity.length;
    var d0 = Date.parse(dates[0]);
    var d1 = Date.parse(dates[n-1]);
    var years = (d1 - d0) / 86400000 / 365.25;
    if (years <= 0 || equity[0] <= 0) return NaN;
    return Math.pow(equity[n-1] / equity[0], 1/years) - 1;
  }

  // Return/risk metrics for an equity curve, shared by the strategy and its
  // buy-and-hold baseline (no trade stats — B&H is one perpetual position).
  // Conventions: Vol annualizes with sqrt(252) (augury._annualized_vol);
  // Sharpe/Sortino with vbt's calendar-day sqrt(365); CAGR is calendar-time
  // geometric; Calmar uses vbt's ann_return = (1+total)^(365/n_returns)-1,
  // which differs from CAGR when rows span less calendar time. `mdd` may be
  // passed precomputed (the strategy already has a drawdown series); when
  // omitted it's derived from the curve (equity / running peak - 1).
  function returnRisk(equity, dates, mdd) {
    var n = equity.length;
    if (!n) return {cagr: NaN, max_drawdown: NaN, vol: NaN, sharpe: NaN,
                    sortino: NaN, calmar: NaN, end_nav: NaN};
    var rets = dailyReturns(equity);
    var meanR = mean(rets);
    var stdR = stdSample(rets);
    var vol = stdR * Math.sqrt(252);
    var sharpe = (stdR > 0) ? (meanR / stdR) * Math.sqrt(365) : NaN;
    var dsSq = 0;
    for (var i = 0; i < rets.length; i++) {
      if (rets[i] < 0) dsSq += rets[i] * rets[i];
    }
    var dsStd = (rets.length > 1) ? Math.sqrt(dsSq / (rets.length - 1)) : 0;
    var sortino = (dsStd > 0) ? (meanR / dsStd) * Math.sqrt(365) : NaN;
    if (mdd === undefined) {
      mdd = 0; var peak = -Infinity;
      for (var j = 0; j < n; j++) {
        if (equity[j] > peak) peak = equity[j];
        if (peak > 0) { var d = equity[j] / peak - 1; if (d < mdd) mdd = d; }
      }
    }
    var vbtAnnReturn = NaN;
    if (rets.length > 0 && equity[0] > 0) {
      var totalRet = equity[n-1] / equity[0] - 1;
      vbtAnnReturn = Math.pow(1 + totalRet, 365 / rets.length) - 1;
    }
    var calmar = (mdd < 0 && isFinite(vbtAnnReturn)) ? vbtAnnReturn / Math.abs(mdd) : NaN;
    return {cagr: cagrFromEquity(equity, dates), max_drawdown: mdd, vol: vol,
            sharpe: sharpe, sortino: sortino, calmar: calmar, end_nav: equity[n-1]};
  }

  function computeMetrics(equity, bhEquity, drawdown, trades, dates) {
    var n = equity.length;
    if (!n) return {};
    var s = returnRisk(equity, dates, Math.min.apply(null, drawdown));
    var bh = returnRisk(bhEquity, dates);

    var closed = trades.filter(function(t) { return t.status === 'Closed'; });
    var wins = closed.filter(function(t) { return t.pnl > 0; });
    var losses = closed.filter(function(t) { return t.pnl <= 0; });
    var winRate = closed.length > 0 ? wins.length / closed.length : NaN;
    var winSum = wins.reduce(function(a,t) { return a + t.pnl; }, 0);
    var lossSum = losses.reduce(function(a,t) { return a + Math.abs(t.pnl); }, 0);
    var pf = lossSum > 0 ? winSum / lossSum : NaN;
    var avgWin = wins.length ? wins.reduce(function(a,t){return a+t.ret;}, 0) / wins.length : NaN;
    var avgLoss = losses.length ? losses.reduce(function(a,t){return a+t.ret;}, 0) / losses.length : NaN;
    var payoff = (losses.length && avgLoss !== 0) ? Math.abs(avgWin / avgLoss) : NaN;
    var maeAll = trades.length
                 ? trades.reduce(function(a,t){return Math.min(a, t.mae);}, 0)
                 : 0;

    return {
      cagr: s.cagr, max_drawdown: s.max_drawdown, mae: maeAll, vol: s.vol,
      sharpe: s.sharpe, sortino: s.sortino, calmar: s.calmar,
      win_rate: winRate, payoff: payoff, profit_factor: pf,
      end_nav: s.end_nav, bh_end_nav: bh.end_nav,
      bh_cagr: bh.cagr, bh_max_drawdown: bh.max_drawdown, bh_vol: bh.vol,
      bh_sharpe: bh.sharpe, bh_sortino: bh.sortino, bh_calmar: bh.calmar,
      total_trades: trades.length, closed_trades: closed.length,
      open_trades: trades.length - closed.length,
    };
  }

  // Slice every overlay array to [lo, hi) so it aligns with the
  // windowed date axis (overlays are computed full-length for warmup).
  function _sliceOverlays(ov, lo, hi) {
    var out = {};
    Object.keys(ov || {}).forEach(function(k) {
      out[k] = ov[k].slice(lo, hi);
    });
    return out;
  }

  // First index whose date is >= startDate (the window's left edge).
  function _startIdx(dates, startDate) {
    var i = 0;
    if (startDate) while (i < dates.length && dates[i] < startDate) i++;
    return i;
  }

  // Inclusive slice on [startDate, endDate] over the ISO-date axis.
  function slicePayload(payload, startDate, endDate) {
    var dates = payload.dates;
    var i0 = 0, i1 = dates.length - 1;
    if (startDate) {
      while (i0 <= i1 && dates[i0] < startDate) i0++;
    }
    if (endDate) {
      while (i1 >= i0 && dates[i1] > endDate) i1--;
    }
    if (i1 < i0) return {dates: [], close: [], opens: [], thermo: []};
    var out = {
      dates: dates.slice(i0, i1 + 1),
      close: payload.close.slice(i0, i1 + 1),
      opens: payload.open.slice(i0, i1 + 1),
    };
    if (payload.thermo) out.thermo = payload.thermo.slice(i0, i1 + 1);
    return out;
  }

  // Pending action — signal at the last bar that wasn't executed yet.
  // Mirrors augury.backtest._pending_action: same conflict_mode='ignore'
  // semantics, gated by current position.
  function pendingAction(entries, exits, currentState) {
    if (!entries.length) return 'hold';
    var last = entries.length - 1;
    var e = entries[last] === true;
    var x = exits[last] === true;
    if (e === x) return 'hold';
    if (e && currentState === 'flat') return 'buy';
    if (x && currentState === 'long') return 'sell';
    return 'hold';
  }

  function run(payload, startDate, endDate) {
    // Signals on the FULL series so rolling indicators (SMA, crosses)
    // warm up on pre-window history, THEN restrict to [startDate,
    // endDate]. Slicing before computing them would cold-start the SMA
    // (NaN for the first n bars of the window) and silently delay the
    // first trade — mirrors augury.backtest.run.
    var sigFull = signals(payload.close, payload.strategy,
                          {thermo: payload.thermo,
                           precomp_entries: payload.precomp_entries,
                           precomp_exits:   payload.precomp_exits});
    var dates = payload.dates;
    var i0 = _startIdx(dates, startDate);
    var i1 = dates.length - 1;
    if (endDate) while (i1 >= i0 && dates[i1] > endDate) i1--;

    var slDates = [], slClose = [], slOpens = [], slThermo;
    var entries = [], exits = [], overlays = {};
    var carryEntry = false, carryExit = false;
    if (i1 >= i0) {
      slDates = dates.slice(i0, i1 + 1);
      slClose = payload.close.slice(i0, i1 + 1);
      slOpens = payload.open.slice(i0, i1 + 1);
      entries = sigFull.entries.slice(i0, i1 + 1);
      exits   = sigFull.exits.slice(i0, i1 + 1);
      overlays = _sliceOverlays(sigFull.overlays, i0, i1 + 1);
      carryEntry = i0 > 0 ? sigFull.entries[i0 - 1] === true : false;
      carryExit  = i0 > 0 ? sigFull.exits[i0 - 1] === true : false;
      if (payload.thermo) slThermo = payload.thermo.slice(i0, i1 + 1);
    }

    var pf = runPortfolio(slClose, slOpens, slDates, entries, exits,
                          payload.fee, payload.slippage,
                          payload.init_capital, carryEntry, carryExit);
    var metrics = computeMetrics(pf.equity, pf.bhEquity, pf.drawdown,
                                  pf.trades, slDates);
    // B&H entry = the open at the window's first bar (where the hold starts).
    metrics.bh_entry = slOpens.length ? slOpens[0] : null;
    var openTrade = pf.trades.length && pf.trades[pf.trades.length-1].status === 'Open'
                    ? pf.trades[pf.trades.length-1] : null;
    var state = openTrade ? 'long' : 'flat';
    var pending = pendingAction(entries, exits, state);
    return {
      dates: slDates, close: slClose, opens: slOpens,
      entries: entries, exits: exits, overlays: overlays,
      equity: pf.equity, bhEquity: pf.bhEquity, drawdown: pf.drawdown,
      trades: pf.trades, metrics: metrics,
      state: state, pending: pending,
      thermo: slThermo,
    };
  }

  // =================================================================
  // Hybrid engine — primary asset + substitute basket, same semantics
  // as augury.backtest.run_hybrid. Always 100% invested: holds primary
  // when base strategy is long, rotates into the basket when flat.
  // No daily rebalance during the basket holding period.
  // =================================================================
  function sliceHybridPayload(payload, startDate, endDate) {
    var dates = payload.dates;
    var i0 = 0, i1 = dates.length - 1;
    if (startDate) while (i0 <= i1 && dates[i0] < startDate) i0++;
    if (endDate) while (i1 >= i0 && dates[i1] > endDate) i1--;
    if (i1 < i0) {
      return {dates: [], close: [], opens: [], subs: []};
    }
    return {
      dates: dates.slice(i0, i1 + 1),
      close: payload.close.slice(i0, i1 + 1),
      opens: payload.open.slice(i0, i1 + 1),
      subs: payload.substitutes.map(function(s) {
        return {
          ticker: s.ticker, weight: s.weight,
          close: s.close.slice(i0, i1 + 1),
          opens: s.open.slice(i0, i1 + 1),
        };
      }),
    };
  }

  function runHybridPortfolio(sl, entries, exits, fee, slip, cap) {
    var n = sl.close.length;
    var subs = sl.subs;
    var subShares = subs.map(function() { return 0; });
    var inPrimary = false;
    var primaryShares = 0, primaryEntryIdx = -1;
    var primaryEntryPrice = 0, primaryEntryCapital = 0;
    var equity = new Array(n);
    var trades = [];

    // Initialize: buy substitute basket at open[0].
    if (n > 0) {
      for (var k = 0; k < subs.length; k++) {
        var leg = cap * subs[k].weight;
        var px = subs[k].opens[0] * (1 + slip);
        if (px && !isNaN(px) && px > 0) {
          subShares[k] = leg / (px * (1 + fee));
        }
      }
    }

    for (var i = 0; i < n; i++) {
      if (i > 0) {
        var sigIdx = i - 1;
        var e = entries[sigIdx] === true;
        var x = exits[sigIdx] === true;
        var action = (e === x) ? null : (e ? 'buy' : 'sell');

        if (action === 'buy' && !inPrimary) {
          // Sub -> primary
          var proceeds = 0;
          for (var k2 = 0; k2 < subs.length; k2++) {
            var spx = subs[k2].opens[i];
            if (spx == null || isNaN(spx)) spx = subs[k2].close[i - 1];
            spx = spx * (1 - slip);
            proceeds += subShares[k2] * spx * (1 - fee);
            subShares[k2] = 0;
          }
          var bp = sl.opens[i] * (1 + slip);
          if (bp && !isNaN(bp) && bp > 0) {
            primaryShares = proceeds / (bp * (1 + fee));
            primaryEntryIdx = i;
            primaryEntryPrice = bp;
            primaryEntryCapital = proceeds;
            inPrimary = true;
          }
        } else if (action === 'sell' && inPrimary) {
          // Primary -> sub
          var sp = sl.opens[i] * (1 - slip);
          var proceedsOut = primaryShares * sp * (1 - fee);
          var lo = Infinity;
          for (var j = primaryEntryIdx; j <= i; j++) {
            if (sl.close[j] < lo) lo = sl.close[j];
          }
          trades.push({
            entryIdx: primaryEntryIdx, entryDate: sl.dates[primaryEntryIdx],
            entryPrice: primaryEntryPrice,
            exitIdx: i, exitDate: sl.dates[i], exitPrice: sp,
            pnl: proceedsOut - primaryEntryCapital,
            ret: primaryEntryCapital > 0 ?
                 proceedsOut / primaryEntryCapital - 1 : 0,
            mae: lo / primaryEntryPrice - 1,
            status: 'Closed',
          });
          primaryShares = 0;
          inPrimary = false;
          for (var k3 = 0; k3 < subs.length; k3++) {
            var bpx = subs[k3].opens[i] * (1 + slip);
            if (bpx && !isNaN(bpx) && bpx > 0) {
              subShares[k3] = (proceedsOut * subs[k3].weight) / (bpx * (1 + fee));
            }
          }
        }
      }

      // NAV at close[i]
      var nav;
      if (inPrimary) {
        nav = primaryShares * sl.close[i];
      } else {
        nav = 0;
        for (var k4 = 0; k4 < subs.length; k4++) {
          nav += subShares[k4] * subs[k4].close[i];
        }
      }
      equity[i] = nav;
    }

    // Open trade at end
    if (inPrimary && n > 0) {
      var last = n - 1;
      var mtm = primaryShares * sl.close[last];
      var loEnd = Infinity;
      for (var kE = primaryEntryIdx; kE <= last; kE++) {
        if (sl.close[kE] < loEnd) loEnd = sl.close[kE];
      }
      trades.push({
        entryIdx: primaryEntryIdx, entryDate: sl.dates[primaryEntryIdx],
        entryPrice: primaryEntryPrice,
        exitIdx: null, exitDate: null, exitPrice: null,
        pnl: mtm - primaryEntryCapital,
        ret: primaryEntryCapital > 0 ? mtm / primaryEntryCapital - 1 : 0,
        mae: loEnd / primaryEntryPrice - 1,
        status: 'Open',
      });
    }

    // Normalize NAV
    var normEquity = new Array(n);
    for (var m = 0; m < n; m++) normEquity[m] = equity[m] / cap;
    // B&H: TSLA buy-and-hold at open[0]
    var bhEquity = new Array(n);
    if (n > 0) {
      var bhP = sl.opens[0] * (1 + slip);
      var bhShares = cap / (bhP * (1 + fee));
      for (var q = 0; q < n; q++) {
        bhEquity[q] = (bhShares * sl.close[q]) / cap;
      }
    }
    // Drawdown
    var dd = new Array(n);
    var peak = -Infinity;
    for (var r = 0; r < n; r++) {
      if (normEquity[r] > peak) peak = normEquity[r];
      dd[r] = peak > 0 ? normEquity[r] / peak - 1 : 0;
    }
    return {
      equity: normEquity, bhEquity: bhEquity, drawdown: dd,
      trades: trades, inPrimary: inPrimary,
    };
  }

  function runHybrid(payload, startDate, endDate) {
    var sl = sliceHybridPayload(payload, startDate, endDate);
    var n = sl.close.length;
    if (!n) {
      return {
        dates: [], close: [], opens: [],
        entries: [], exits: [], overlays: {},
        equity: [], bhEquity: [], drawdown: [], trades: [],
        metrics: {}, state: 'flat', pending: 'hold',
      };
    }
    // Signals on the FULL series (warmup), then align to the slice —
    // same rationale as `run()`. The basket loop starts flat at i=0
    // regardless, so no carry-in is needed here.
    var sigFull = signals(payload.close, payload.strategy,
                          {thermo: payload.thermo,
                           precomp_entries: payload.precomp_entries,
                           precomp_exits:   payload.precomp_exits});
    var i0 = _startIdx(payload.dates, startDate);
    var entries = sigFull.entries.slice(i0, i0 + n);
    var exits   = sigFull.exits.slice(i0, i0 + n);
    var overlays = _sliceOverlays(sigFull.overlays, i0, i0 + n);
    var pf = runHybridPortfolio(sl, entries, exits,
                                 payload.fee, payload.slippage,
                                 payload.init_capital);
    var metrics = computeMetrics(pf.equity, pf.bhEquity, pf.drawdown,
                                  pf.trades, sl.dates);
    metrics.bh_entry = sl.opens.length ? sl.opens[0] : null;
    var state = pf.inPrimary ? 'long' : 'flat';
    var pending = pendingAction(entries, exits, state);
    return {
      dates: sl.dates, close: sl.close, opens: sl.opens,
      entries: entries, exits: exits, overlays: overlays,
      equity: pf.equity, bhEquity: pf.bhEquity, drawdown: pf.drawdown,
      trades: pf.trades, metrics: metrics,
      state: state, pending: pending,
    };
  }

  return {run: run, runHybrid: runHybrid, signals: signals, sma: sma};
})();

if (typeof module !== 'undefined' && module.exports) module.exports = Backtest;
if (typeof window !== 'undefined') window.Backtest = Backtest;
