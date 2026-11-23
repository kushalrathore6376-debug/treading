"""
Download ~1 year of XAUUSD M1 bid OHLC from Dukascopy via dukascopy-node.

Usage:
  py download_history.py              # last 365 days
  py download_history.py --days 90
  py download_history.py --from 2025-11-23 --to 2026-11-23

Requires Node.js (uses npx dukascopy-node).
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOWNLOAD_DIR = ROOT / "download"


def _parse_args():
    p = argparse.ArgumentParser(description="Download XAUUSD M1 history (Dukascopy)")
    p.add_argument("--days", type=int, default=365, help="Lookback days (default 365)")
    p.add_argument("--from", dest="date_from", type=str, default=None, help="YYYY-MM-DD")
    p.add_argument("--to", dest="date_to", type=str, default=None, help="YYYY-MM-DD")
    p.add_argument("--instrument", default="xauusd")
    p.add_argument("--timeframe", default="m1", help="m1 / m5 / h1 / d1")
    return p.parse_args()


def main():
    args = _parse_args()
    if shutil.which("npx") is None:
        print("ERROR: Node.js / npx not found. Install Node.js first.")
        sys.exit(1)

    end = date.fromisoformat(args.date_to) if args.date_to else date.today()
    start = (
        date.fromisoformat(args.date_from)
        if args.date_from
        else (end - timedelta(days=args.days))
    )

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    print("=" * 60)
    print(f"  Download {args.instrument.upper()} {args.timeframe}")
    print(f"  Range:   {start} -> {end}  (~{(end - start).days} days)")
    print(f"  Out dir: {DOWNLOAD_DIR}")
    print("=" * 60)
    print("This can take several minutes for a full year of M1...")

    cmd = [
        "npx",
        "--yes",
        "dukascopy-node",
        "-i",
        args.instrument,
        "-from",
        start.isoformat(),
        "-to",
        end.isoformat(),
        "-t",
        args.timeframe,
        "-f",
        "csv",
        "-v",
        "-dir",
        str(DOWNLOAD_DIR),
    ]
    print("Running:", " ".join(cmd))
    # Windows: npx.cmd
    shell = sys.platform == "win32"
    result = subprocess.run(cmd, cwd=str(ROOT), shell=shell)
    if result.returncode != 0:
        print(f"Download failed with exit {result.returncode}")
        sys.exit(result.returncode)

    # Summarize newest matching file
    from history_data import preferred_history_csv, summarize_file

    path = preferred_history_csv()
    if path:
        summary = summarize_file(path)
        print()
        print("Download complete:")
        for k, v in summary.items():
            print(f"  {k}: {v}")
    else:
        print("Done, but no CSV found under download/ — check dukascopy output.")


if __name__ == "__main__":
    main()
