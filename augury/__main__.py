import argparse
from augury import indicators, render


def cli() -> None:
    parser = argparse.ArgumentParser(prog="augury")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="Refresh data and render all pages")
    args = parser.parse_args()
    if args.cmd == "run":
        run()


def run() -> None:
    print("[augury] refreshing data…")
    indicators.refresh_all()
    print("[augury] rendering pages…")
    render.all()
    print("[augury] done. open docs/cycle.html")


if __name__ == "__main__":
    cli()
