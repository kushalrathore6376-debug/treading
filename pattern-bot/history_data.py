"""
Load and query XAUUSD historical M1 CSVs (Dukascopy format).

CSV columns: timestamp(ms), open, high, low, close, volume
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent
DOWNLOAD_DIR = ROOT / "download"
IST = timezone(timedelta(hours=5, minutes=30))


def find_history_csvs(pattern: str = "xauusd-m1-bid-*.csv") -> list[Path]:
    if not DOWNLOAD_DIR.exists():
        return []
    return sorted(DOWNLOAD_DIR.glob(pattern))


def preferred_history_csv() -> Optional[Path]:
    """Prefer the longest / newest 1y-style file; else latest month file."""
    files = find_history_csvs()
    if not files:
        return None
    # Prefer filenames that span ~365 days in the name, else largest file
    scored = []
    for p in files:
        scored.append((p.stat().st_size, p))
    scored.sort(reverse=True)
    return scored[0][1]


def _row_to_candle(row: dict) -> dict:
    ts_ms = int(float(row["timestamp"]))
    dt_utc = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
    dt_ist = dt_utc.astimezone(IST)
    return {
        "timestamp": ts_ms,
        "time": int(ts_ms // 1000),  # unix sec for Lightweight Charts
        "time_utc": dt_utc.strftime("%Y-%m-%d %H:%M:%S"),
        "time_ist": dt_ist.strftime("%Y-%m-%d %H:%M:%S"),
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
        "volume": float(row.get("volume") or 0),
    }


def load_candles(
    path: Optional[Path] = None,
    start_ms: Optional[int] = None,
    end_ms: Optional[int] = None,
    limit: Optional[int] = None,
    tail: bool = False,
) -> list[dict]:
    """
    Load candles from CSV.
    If `limit` is set and `tail=True` (default when no start_ms), keep the
    most recent `limit` bars instead of the oldest.
    """
    path = path or preferred_history_csv()
    if path is None or not path.exists():
        raise FileNotFoundError(
            f"No history CSV in {DOWNLOAD_DIR}. Run: py download_history.py"
        )

    if limit and start_ms is None and end_ms is None:
        tail = True

    candles = []
    with path.open("r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ts_ms = int(float(row["timestamp"]))
            if start_ms is not None and ts_ms < start_ms:
                continue
            if end_ms is not None and ts_ms > end_ms:
                continue
            candles.append(_row_to_candle(row))

    if limit and len(candles) > limit:
        candles = candles[-limit:] if tail else candles[:limit]
    return candles


def summarize_file(path: Optional[Path] = None) -> dict:
    path = path or preferred_history_csv()
    if path is None:
        return {"ok": False, "error": "no_csv", "path": None}
    candles = load_candles(path)
    if not candles:
        return {"ok": False, "error": "empty", "path": str(path)}
    vols = [c["volume"] for c in candles]
    return {
        "ok": True,
        "path": str(path),
        "filename": path.name,
        "bars": len(candles),
        "start_utc": candles[0]["time_utc"],
        "end_utc": candles[-1]["time_utc"],
        "start_ist": candles[0]["time_ist"],
        "end_ist": candles[-1]["time_ist"],
        "price_min": min(c["low"] for c in candles),
        "price_max": max(c["high"] for c in candles),
        "volume_sum": round(sum(vols), 4),
        "volume_avg": round(sum(vols) / len(vols), 6),
        "gaps": _count_gaps(candles),
    }


def _count_gaps(candles: list[dict], step_ms: int = 60_000) -> int:
    gaps = 0
    for i in range(1, len(candles)):
        dt = candles[i]["timestamp"] - candles[i - 1]["timestamp"]
        if dt > step_ms * 1.5:
            gaps += 1
    return gaps


def slice_window(candles: list[dict], end_idx: int, lookback: int) -> list[dict]:
    start = max(0, end_idx - lookback + 1)
    return candles[start : end_idx + 1]
