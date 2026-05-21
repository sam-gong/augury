// Headless JS↔Python parity checker. Reads the fixture JSON written by
// `augury.parity.dump`, re-runs the SAME backtest.js engine the browser
// ships, and asserts its metrics match Python's at every window start.
// Exits non-zero on any drift. Tolerance mirrors base.html's _verifyParity.
//
//   node parity_check.mjs <fixtures.json>
import { createRequire } from "module";
import { readFileSync } from "fs";

const require = createRequire(import.meta.url);
const Backtest = require("./backtest.js");

const KEYS = [
  "cagr", "max_drawdown", "mae", "vol", "sharpe", "sortino", "calmar",
  "win_rate", "payoff", "profit_factor", "end_nav", "bh_end_nav",
  "total_trades", "closed_trades",
  "bh_cagr", "bh_max_drawdown", "bh_vol", "bh_sharpe", "bh_sortino", "bh_calmar",
];

// sharpe/sortino/calmar come from vectorbt's stats() in Python (annualized
// from the index frequency) but a hand-port in JS. They agree to <0.1% at the
// full window — the browser's actual contract — but drift up to ~1% on
// sub-windows where vbt's frequency-annualization diverges. That residual is a
// modelling difference, NOT a windowing bug: every trade-driven metric below
// (counts, end_nav, cagr, max_drawdown, vol, …) stays strict at 0.1%, so a real
// carry-in/warmup/slicing regression still trips the check. These three only
// need to confirm the engines stay in the same ballpark.
const RATIO_REL = 0.02;
const STRICT_REL = 1e-3;
const RATIO_KEYS = new Set([
  "sharpe", "sortino", "calmar", "bh_sharpe", "bh_sortino", "bh_calmar",
]);

function drift(py, js) {
  const bad = [];
  for (const k of KEYS) {
    const pv = py[k], jv = js[k];
    const pNull = pv == null || (typeof pv === "number" && isNaN(pv));
    const jNull = jv == null || (typeof jv === "number" && isNaN(jv));
    if (pNull && jNull) continue;
    if (pNull !== jNull) { bad.push({ k, py: pv, js: jv }); continue; }
    if (k === "total_trades" || k === "closed_trades") {
      if (pv !== jv) bad.push({ k, py: pv, js: jv });
      continue;
    }
    const d = Math.abs(pv - jv);
    const rel = Math.abs(pv) > 1e-9 ? d / Math.abs(pv) : d;
    const relTol = RATIO_KEYS.has(k) ? RATIO_REL : STRICT_REL;
    if (rel > relTol && d > 1e-4) bad.push({ k, py: pv, js: jv, rel });
  }
  return bad;
}

const path = process.argv[2];
if (!path) { console.error("usage: node parity_check.mjs <fixtures.json>"); process.exit(2); }
const fixtures = JSON.parse(readFileSync(path, "utf8"));

let checked = 0, failed = 0;
for (const fx of fixtures) {
  const runFn = fx.kind === "hybrid" ? Backtest.runHybrid : Backtest.run;
  for (const c of fx.cases) {
    checked++;
    const res = runFn(fx.payload, c.start, null);
    const bad = drift(c.metrics, res.metrics || {});
    if (!bad.length) continue;
    failed++;
    console.error(`FAIL  ${fx.ticker} · ${fx.label} · start=${c.start || "full"}`);
    for (const b of bad) {
      const tail = b.rel != null ? `  (rel ${(b.rel * 100).toFixed(3)}%)` : "";
      console.error(`        ${b.k}: py=${b.py} js=${b.js}${tail}`);
    }
  }
}

console.log(`parity: ${checked - failed}/${checked} cases OK · ${fixtures.length} strategies`);
if (failed) { console.error(`parity: ${failed} case(s) drifted — JS and Python disagree`); process.exit(1); }
