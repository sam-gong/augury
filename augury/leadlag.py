"""Lead/lag validation for the leading-indicator claims on cycle.html.

For every (leader → target) pair we scan k = 0..KMAX months, take the k with
the strongest correlation, and then try to kill it four ways:

1. **Scan correction.** Picking the best of 25 lags is 25 tests. The null is
   built by circularly shifting the leader (which preserves its own
   autocorrelation) and re-running the whole scan, so the p-value is on
   max|corr| across the scan, not on one lag.
2. **Block bootstrap CI.** Monthly overlapping windows with rho1 ~ 0.9 make
   textbook p-values meaningless. Moving-block resampling (24m blocks) keeps
   the persistence.
3. **Per-decade correlation.** Not a two-way split — a two-way split of a
   sample that sits entirely inside one regime looks stable no matter what.
   The decade spread is what actually shows regime dependence.
4. **n_eff.** corr 0.4 at n_eff 11 and corr 0.4 at n_eff 60 are not the
   same claim.

`usable_lead = k* - publication_lag`: OECD CLI leads ISM PMI by 6 months but
prints ~2 months late, so only 4 of those months are tradeable. Ranking on k*
instead of usable_lead overstates every slow-publishing series.

Caveat that this module does NOT handle: every series here is the *revised*
vintage from FRED/OECD. Real-time first prints were different, so these
correlations are an upper bound on what was actually knowable.

    python -m augury leadlag            # both targets
    python -m augury leadlag --target spx
"""
from __future__ import annotations

import zlib

import numpy as np
import pandas as pd

from augury import indicators

KMAX = 24          # months of lead to scan
BLOCK = 24         # moving-block length, months
NBOOT = 400
MIN_OBS = 60


def _rng(*key: str) -> np.random.Generator:
    """Seeded per (target, leader) rather than from one shared stream. A shared
    stream makes the numbers depend on iteration order, so the table printed by
    `report()` and the table baked into leadlag.html — two separate runs — would
    never agree, and adding one leader would shift every p-value below it."""
    return np.random.default_rng(zlib.crc32(":".join(key).encode()))


# ---------- series prep ----------

def _monthly(id: str, col: str = "value") -> pd.Series:
    df = indicators.load(id)
    if df is None or df.empty:
        raise KeyError(id)
    s = df[col] if col in df.columns else df.iloc[:, 0]
    s.index = pd.to_datetime(s.index)
    return s.resample("MS").last().dropna()


def series(id: str, how: str = "level") -> pd.Series:
    """`how` declares the stationarity transform. Levels are only valid for
    series that mean-revert on their own (PMI, spreads, diffusion, z-scores);
    trending levels need yoy or diff or the correlation is spurious."""
    s = _monthly(id, "Close" if id in _PRICES else "value")
    if how == "level":
        out = s
    elif how == "yoy":
        out = s.pct_change(12) * 100
    elif how == "diff":
        out = s.diff()
    elif how == "diff3":
        out = s.diff(3)
    else:
        raise ValueError(how)
    out = out.dropna()
    # Never winsorize a trending level: SPX's top 1% *is* its most recent year,
    # so clipping flattens those months to a constant and any return computed
    # downstream comes out as a silent zero. Clip the transform, not the price.
    return out if (how == "level" and id in _PRICES) else _winsorize(out)


def _winsorize(s: pd.Series, p: float = 0.01) -> pd.Series:
    """Pearson correlation is not robust: initial claims YoY hit +3000% in
    2020, which alone dragged the pooled ICSA↔PMI correlation to -0.17 while
    every individual decade sat between -0.6 and -0.8. Clipping the tails
    keeps the coefficient describing the bulk of the sample instead of one
    month."""
    return s.clip(s.quantile(p), s.quantile(1 - p))


_PRICES = {"SPX", "NDX", "COPPER_GOLD"}


# Leader → (transform, publication lag in months).
# Publication lags are the normal calendar gap between a reference month and
# its first print: ISM is out on business day 1, so 0; OECD CLI is out ~2
# months later; BIS credit is quarterly and ~5 months late.
LEADERS = {
    "OECD_CLI_DIFFUSION":  ("level", 2),
    "GAFDFSA066MSFRBPHI":  ("level", 0),   # Philly Fed future activity 6m
    "GACDFSA066MSFRBPHI":  ("level", 0),   # Philly Fed current activity
    "ISM_NOC_MINUS_IVC":   ("level", 0),   # new orders - inventories
    "T10Y2Y":              ("level", 0),
    "FCI_G_1Y":            ("level", 1),
    "CFNAI":               ("level", 1),
    "NFCI":                ("level", 0),
    "BAMLH0A0HYM2":        ("level", 0),   # HY OAS
    "ICSA":                ("yoy", 0),     # initial claims
    "JTSJOL":              ("yoy", 2),
    "M2SL":                ("yoy", 1),
    "INDPRO":              ("yoy", 1),
    "COPPER_GOLD":         ("yoy", 0),
    "CRDQCNAPABIS":        ("yoy", 5),     # China credit (BIS proxy)
    "UNRATE":              ("diff3", 1),
    "CSUSHPINSA":          ("yoy", 2),
    "MICH":                ("level", 0),
}

TARGETS = {
    "pmi": "ISM 制造业 PMI",
    "spx": "标普未来6个月回报",
}


def target(name: str) -> pd.Series:
    if name == "pmi":
        return series("ISM_PMI")
    if name == "spx":
        px = series("SPX")
        return _winsorize(((px.shift(-6) / px - 1) * 100).dropna())
    raise ValueError(name)


# ---------- statistics ----------

def _corr_at(x: pd.Series, y: pd.Series, k: int) -> tuple[float, int]:
    d = pd.concat({"a": x, "b": y.shift(-k)}, axis=1).dropna()
    if len(d) < MIN_OBS:
        return np.nan, len(d)
    return d.a.corr(d.b), len(d)


def _scan(x: pd.Series, y: pd.Series) -> tuple[int, float, int]:
    best_k, best_c, best_n = 0, 0.0, 0
    for k in range(0, KMAX + 1):
        c, n = _corr_at(x, y, k)
        if c == c and abs(c) > abs(best_c):
            best_k, best_c, best_n = k, c, n
    return best_k, best_c, best_n


def _n_eff(a: np.ndarray, b: np.ndarray) -> float:
    """Standard Bartlett/Quenouille adjustment: persistent series carry far
    less independent information than their row count suggests."""
    def r1(v):
        return np.corrcoef(v[:-1], v[1:])[0, 1]
    ra, rb = r1(a), r1(b)
    return len(a) * (1 - ra * rb) / (1 + ra * rb)


def _block_ci(a: np.ndarray, b: np.ndarray, rng: np.random.Generator) -> tuple[float, float]:
    n = len(a)
    if n < BLOCK * 2:
        return np.nan, np.nan
    nblocks = int(np.ceil(n / BLOCK))
    starts_pool = np.arange(n - BLOCK + 1)
    out = np.empty(NBOOT)
    for i in range(NBOOT):
        idx = np.concatenate([np.arange(s, s + BLOCK)
                              for s in rng.choice(starts_pool, nblocks)])[:n]
        out[i] = np.corrcoef(a[idx], b[idx])[0, 1]
    return float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))


def _scan_pvalue(x: pd.Series, y: pd.Series, observed: float,
                 rng: np.random.Generator) -> float:
    """Null = the leader circularly shifted by a random offset, which destroys
    any real timing link while preserving its own autocorrelation. Then run
    the identical 0..KMAX scan and keep max|corr|. p = share of null scans
    that beat the observed one."""
    d = pd.concat({"a": x, "b": y}, axis=1).dropna()
    if len(d) < MIN_OBS:
        return np.nan
    a, b = d.a.to_numpy(), d.b.to_numpy()
    n = len(a)
    hits = 0
    for _ in range(NBOOT):
        shifted = np.roll(a, rng.integers(BLOCK, n - BLOCK))
        best = 0.0
        for k in range(0, KMAX + 1):
            if n - k < MIN_OBS:
                break
            c = np.corrcoef(shifted[:n - k], b[k:])[0, 1]
            if abs(c) > abs(best):
                best = c
        if abs(best) >= abs(observed):
            hits += 1
    return hits / NBOOT


def _by_decade(x: pd.Series, y: pd.Series, k: int) -> dict[str, float]:
    d = pd.concat({"a": x, "b": y.shift(-k)}, axis=1).dropna()
    out = {}
    for dec, g in d.groupby((d.index.year // 10) * 10):
        out[int(dec)] = g.a.corr(g.b) if len(g) >= 36 else np.nan
    return out


# ---------- driver ----------

RECENT = "2000"   # start of the "recent window" re-scan


def _tier(r: dict) -> str:
    """Graded rather than pass/fail. A signal that only works in the recent
    window is still worth showing — it just can't be leaned on the way a
    six-decade one can, so it gets its own row colour instead of being
    dropped."""
    if r["p"] >= 0.05 and abs(r["rho_recent"]) < 0.30:
        return "无效"                      # no link at any lag — lead time is moot
    if r["usable"] <= 0:
        return "同步"                      # real link, but publication eats the lead
    if r["p"] < 0.05 and r["sign_flips"] == 0 and r["n_dec"] >= 3:
        return "稳健"
    if r["p"] < 0.05 and r["n_dec"] < 3:
        return "样本短"                    # real in what we have, untested across regimes
    if abs(r["rho_recent"]) >= 0.30:
        return "近期有效"                  # holds post-2000 only — reference, not foundation
    return "无效"


TIER_ORDER = {"稳健": 0, "样本短": 1, "近期有效": 2, "同步": 3, "无效": 4}


def evaluate(leader: str, how: str, pub_lag: int, tgt: pd.Series,
             tgt_name: str) -> dict | None:
    try:
        x = series(leader, how)
    except KeyError:
        return None
    k, c, n = _scan(x, tgt)
    if not n:
        return None
    kr, cr, nr = _scan(x.loc[RECENT:], tgt)
    d = pd.concat({"a": x, "b": tgt.shift(-k)}, axis=1).dropna()
    a, b = d.a.to_numpy(), d.b.to_numpy()
    lo, hi = _block_ci(a, b, _rng(tgt_name, leader, "ci"))
    dec = _by_decade(x, tgt, k)
    vals = [v for v in dec.values() if v == v]
    row = {
        "leader": leader,
        "title": _title(leader),
        "k": k,
        "usable": k - pub_lag,
        "pub_lag": pub_lag,
        "rho": c,
        "lo": lo,
        "hi": hi,
        "p": _scan_pvalue(x, tgt, c, _rng(tgt_name, leader, "p")),
        "n": n,
        "n_eff": _n_eff(a, b),
        "start": d.index.min().year,
        "decades": dec,
        "n_dec": len(vals),
        "spread": (max(vals) - min(vals)) if len(vals) > 1 else np.nan,
        "sign_flips": sum(1 for v in vals if v * c < 0),
        "k_recent": kr,
        "rho_recent": cr,
        "n_recent": nr,
    }
    row["tier"] = _tier(row)
    return row


def _title(id: str) -> str:
    ind = indicators.REGISTRY.get(id)
    return ind.title if ind else id


def run(target_name: str = "pmi") -> pd.DataFrame:
    tgt = target(target_name)
    rows = [r for lid, (how, lag) in LEADERS.items()
            if (r := evaluate(lid, how, lag, tgt, target_name))]
    df = pd.DataFrame(rows)
    df["rank"] = df.tier.map(TIER_ORDER)
    return (df.sort_values(["rank", "rho"],
                           key=lambda s: s.abs() if s.name == "rho" else s,
                           ascending=[True, False])
              .drop(columns="rank").reset_index(drop=True))


def report(target_name: str = "pmi") -> pd.DataFrame:
    df = run(target_name)
    decs = sorted({d for row in df.decades for d, v in row.items() if v == v})
    print(f"\n目标 = {TARGETS[target_name]}    (扫描 k=0..{KMAX}m, "
          f"块自举 {BLOCK}m×{NBOOT})\n")
    head = (f"{'判定':<7}{'领先指标':<22}{'k*':>4}{'可用':>5}{'corr':>8}"
            f"{'95%CI':>16}{'p':>7}{'n_eff':>7}{'起':>6}{'近26年':>11}  ")
    print(head + "".join(f"{d % 100:>5d}s " for d in decs))
    print("-" * (len(head) + 7 * len(decs)))
    for _, r in df.iterrows():
        print(f"{r.tier:<5}{r.leader:<23}{r.k:>4}{r.usable:>5}{r.rho:>+8.3f}"
              f"{f'[{r.lo:+.2f},{r.hi:+.2f}]':>16}{r.p:>7.3f}"
              f"{r.n_eff:>7.0f}{r.start:>6}"
              f"{f'{r.k_recent}m {r.rho_recent:+.2f}':>11}  "
              + "".join(f"{r.decades.get(d, float('nan')):>+7.2f}"
                        if d in r.decades and r.decades[d] == r.decades[d]
                        else f"{'—':>7}" for d in decs))
    print("\n稳健 = 扫描校正后 p<0.05 且各年代不变号 且扣掉发布时滞后仍领先"
          "\n样本短 = 显著但覆盖不到三个年代，没经过跨周期检验"
          f"\n近期有效 = 全样本不成立，但 {RECENT} 年后 |corr|≥0.30（参考用）"
          "\n同步 = 关系成立，但扣掉发布时滞后已无领先"
          "\n无效 = 任何 lag 上都不成立，领先几个月无意义")
    print("注：全部基于修订后数据，实盘可得的首发值会更差。")
    return df
