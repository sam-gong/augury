import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from augury import indicators, render


def cli() -> None:
    parser = argparse.ArgumentParser(prog="augury")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="Refresh data and render all pages")
    sub.add_parser("parity", help="Check JS engine matches Python backtest")
    args = parser.parse_args()
    if args.cmd == "run":
        run()
    elif args.cmd == "parity":
        sys.exit(parity())


def run() -> None:
    print("[augury] refreshing data…")
    indicators.refresh_all()
    print("[augury] rendering pages…")
    render.all()
    print("[augury] done. open docs/cycle.html")


def parity() -> int:
    """Dump fixtures from the Python engine and verify the JS engine
    reproduces them. Returns the checker's exit code (0 = aligned)."""
    from augury import parity as parity_mod

    checker = Path(__file__).resolve().parent / "static" / "parity_check.mjs"
    with tempfile.NamedTemporaryFile("w", suffix=".json") as f:
        n_strat, n_cases = parity_mod.dump(Path(f.name))
        print(f"[augury] {n_strat} strategies, {n_cases} cases — running JS engine…")
        return subprocess.run(["node", str(checker), f.name]).returncode


if __name__ == "__main__":
    cli()
