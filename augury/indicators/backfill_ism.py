"""One-off backfill for ISM Manufacturing sub-indices from historical PDFs.

DBnomics gives us 5 years (2021-01+); ISM/court ordered FRED to drop the NAPMxxx
series in 2017, so older history is fragmented. This script walks Wayback's CDX
index for all archived ISM Manufacturing Report on Business PDFs, fetches each
one (via the snapshot's raw-bytes `if_` rewrite), parses the first-page table,
and merges the per-month sub-index values into the existing parquet.

Coverage is sparse and variable by year — Wayback didn't snapshot every month —
but we typically recover 2015-09 onward, which extends the chart back ~5 years
before DBnomics. The framework's `refresh()` preserves backfilled rows because
the merge keeps existing dates when the new fetch doesn't include them.

Run once: `python -m augury.indicators.backfill_ism`.
Idempotent: re-running only fetches PDFs for months not already in parquet.
"""
import json
import re
import urllib.error
from pathlib import Path

import pandas as pd

from augury.indicators.fetchers import _download, _parse_ism_pdf

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "indicators"
# Wayback-sourced historical PDFs are cached here because ISM is hostile to
# scrapers (random URL prefixes, no public archive) and Wayback can lose
# snapshots. Daily refresh's "live tail" PDFs are NOT cached — they're
# trivially re-fetchable from ISM's CDN any day.
PDF_CACHE = Path(__file__).resolve().parents[2] / "data" / "raw_pdfs" / "ISM_MFG"

# (parquet_id, PDF row label)
SUBINDICES = [
    ("ISM_NEW_ORDERS",  "New Orders"),
    ("ISM_INVENTORIES", "Inventories"),
    ("ISM_MFG_PRICES",  "Prices"),
]

# Wayback CDX prefixes that historically host ISM Manufacturing ROB PDFs.
# ISM migrated URL schemes around 2020 (`instituteforsupplymanagement.org/files`
# → `ismworld.org/globalassets/...`), and again adopted random URL-prefix
# garbage (`beef202506pmi.pdf`) around 2024 to defeat scrapers. Both eras are
# captured under `globalassets/` and the legacy filename pattern.
CDX_PREFIXES = [
    "ismworld.org/globalassets/",
    "instituteforsupplymanagement.org/files/",
]

# Extract YYYYMM from filename. Matches both `ROB201907PMI.pdf` and
# `rob202401pmi.pdf` / `beef202506pmi.pdf` shapes.
_DATE_PAT = re.compile(r"([12][09]\d{2})([01]\d)")

# Exclude non-manufacturing ROB reports.
_NMI_PAT = re.compile(r"(non.?mfg|nmi|services?[-_]?pmi)", re.I)


def discover_pdf_urls() -> dict[str, tuple[str, str]]:
    """Walk Wayback CDX for archived ISM Manufacturing PMI PDFs.
    Returns {YYYY-MM: (wayback_timestamp, original_url)}."""
    found: dict[str, tuple[str, str]] = {}
    for prefix in CDX_PREFIXES:
        cdx = (
            f"https://web.archive.org/cdx/search/cdx?url={prefix}"
            f"&matchType=prefix&output=json&filter=mimetype:application/pdf"
            f"&collapse=urlkey&limit=5000"
        )
        try:
            rows = json.loads(_download(cdx).decode("utf-8", errors="ignore"))
        except Exception as e:
            print(f"  warn: CDX {prefix} failed: {e}")
            continue
        for row in rows[1:]:
            if len(row) < 3:
                continue
            ts, original_url = row[1], row[2].split("?")[0]
            low = original_url.lower()
            if "pmi" not in low or _NMI_PAT.search(low):
                continue
            m = _DATE_PAT.search(original_url.split("/")[-1])
            if not m:
                continue
            y, mo = int(m.group(1)), int(m.group(2))
            if not (2010 <= y <= 2030 and 1 <= mo <= 12):
                continue
            ym = f"{y:04d}-{mo:02d}"
            # Prefer the latest snapshot per month.
            prev = found.get(ym)
            if prev is None or ts > prev[0]:
                found[ym] = (ts, original_url)
    return found


def fetch_pdf(ym: str, ts: str, original_url: str) -> bytes:
    """Load a cached PDF if we have one; otherwise try ISM CDN, fall back to
    Wayback's raw-bytes rewrite (`if_` flag), and cache the result so we never
    re-fetch a successfully-parsed historical PDF."""
    cached = PDF_CACHE / f"{ym}.pdf"
    if cached.exists():
        data = cached.read_bytes()
        if data[:4] == b"%PDF":
            return data
        cached.unlink()   # corrupt — drop and re-fetch
    try:
        data = _download(original_url)
        if data[:4] != b"%PDF":
            raise ValueError("not a PDF")
    except Exception:
        wb = f"https://web.archive.org/web/{ts}if_/{original_url}"
        data = _download(wb)
    if data[:4] == b"%PDF":
        PDF_CACHE.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
    return data


def load_existing_months(sid: str) -> set[str]:
    p = DATA_DIR / f"{sid}.parquet"
    if not p.exists():
        return set()
    return set(pd.read_parquet(p).index.strftime("%Y-%m").tolist())


def merge_into_parquet(sid: str, new_rows: list[tuple[pd.Timestamp, float]]) -> None:
    if not new_rows:
        print(f"  [{sid}] no new rows")
        return
    p = DATA_DIR / f"{sid}.parquet"
    old = pd.read_parquet(p) if p.exists() else pd.DataFrame(columns=["value"])
    new = pd.DataFrame(new_rows, columns=["date", "value"]).set_index("date")
    new.index.name = "date"
    merged = pd.concat([old, new])
    merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    merged.to_parquet(p)
    span = f"{merged.index.min():%Y-%m} → {merged.index.max():%Y-%m}"
    print(f"  [{sid}] +{len(new_rows)} rows, total {len(merged)} ({span})")


def main() -> None:
    print("[backfill] discovering historical ISM PDF URLs via Wayback CDX...")
    urls = discover_pdf_urls()
    print(f"[backfill] found {len(urls)} unique months")

    existing = {sid: load_existing_months(sid) for sid, _ in SUBINDICES}
    new_rows: dict[str, list[tuple[pd.Timestamp, float]]] = {
        sid: [] for sid, _ in SUBINDICES
    }

    ok, miss, cached_only = 0, 0, 0
    for ym in sorted(urls.keys()):
        ts, original_url = urls[ym]
        all_parsed = all(ym in existing[sid] for sid, _ in SUBINDICES)
        cached_pdf = PDF_CACHE / f"{ym}.pdf"
        # Skip only when parquet has all values AND PDF is cached. Otherwise
        # we re-fetch to populate the cache so future re-parsing never needs
        # Wayback again.
        if all_parsed and cached_pdf.exists():
            continue
        try:
            pdf_bytes = fetch_pdf(ym, ts, original_url)
        except Exception as e:
            print(f"  {ym}: fetch FAILED ({e})")
            miss += 1
            continue
        if pdf_bytes[:4] != b"%PDF":
            print(f"  {ym}: not a PDF (got {pdf_bytes[:8]!r})")
            miss += 1
            continue
        if all_parsed:
            cached_only += 1
            print(f"  {ym}: cached PDF ({len(pdf_bytes)//1024} KB, values already in parquet)")
            continue
        any_parsed = False
        for sid, row_name in SUBINDICES:
            if ym in existing[sid]:
                continue
            try:
                v = _parse_ism_pdf(pdf_bytes, row_name)
            except Exception as e:
                print(f"  {ym} {sid}: parse error ({e})")
                continue
            if v is not None:
                new_rows[sid].append((pd.Timestamp(f"{ym}-01"), v))
                any_parsed = True
        if any_parsed:
            ok += 1
            vals = ", ".join(
                f"{sid.split('_',1)[1]}={new_rows[sid][-1][1]}"
                for sid, _ in SUBINDICES
                if new_rows[sid] and new_rows[sid][-1][0] == pd.Timestamp(f"{ym}-01")
            )
            print(f"  {ym}: {vals}")
        else:
            print(f"  {ym}: nothing parsed (layout mismatch?)")
            miss += 1

    print(f"\n[backfill] parsed={ok}  cached_only={cached_only}  miss={miss}")
    print("[backfill] merging into parquets:")
    for sid, _ in SUBINDICES:
        merge_into_parquet(sid, new_rows[sid])


if __name__ == "__main__":
    main()
