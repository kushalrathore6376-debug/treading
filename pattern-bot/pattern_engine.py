"""
Historical chart-pattern matching for XAUUSD M1.

Idea:
  1. Take the last `lookback` candles (shape of price path).
  2. Normalize to % returns from first close (price-level independent).
  3. Scan history for nearest windows by cosine similarity.
  4. Look `horizon` bars ahead of each match and summarize what price did
     (avg move, win rate up/down) — bot can bias BUY/SELL from that.

This is a research signal layer, not a guaranteed edge.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from history_data import load_candles, preferred_history_csv, slice_window

# Simple process cache so the bot does not re-parse CSV every signal
_CACHE: dict = {"path": None, "mtime": None, "candles": None}


def get_cached_history(force: bool = False) -> list[dict]:
    path = preferred_history_csv()
    if path is None:
        return []
    mtime = path.stat().st_mtime
    if (
        not force
        and _CACHE["candles"] is not None
        and _CACHE["path"] == str(path)
        and _CACHE["mtime"] == mtime
    ):
        return _CACHE["candles"]
    candles = load_candles(path)
    _CACHE.update({"path": str(path), "mtime": mtime, "candles": candles})
    return candles


@dataclass
class PatternMatch:
    start_idx: int
    end_idx: int
    score: float
    start_time_utc: str
    end_time_utc: str
    entry_close: float
    forward_close: Optional[float]
    forward_move_pts: Optional[float]
    forward_move_pct: Optional[float]
    direction: str  # UP / DOWN / FLAT


def _norm_path(closes: list[float]) -> list[float]:
    """Percent path from first close (shape, not absolute price)."""
    if not closes or closes[0] == 0:
        return []
    base = closes[0]
    return [(c / base) - 1.0 for c in closes]


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return -1.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return -1.0
    return dot / (na * nb)


def find_similar_patterns(
    query_closes: list[float],
    history: Optional[list[dict]] = None,
    lookback: int = 60,
    horizon: int = 30,
    top_k: int = 8,
    min_score: float = 0.85,
    stride: int = 5,
    exclude_recent: int = 500,
) -> list[PatternMatch]:
    """
    Search history for windows whose normalized close path matches query.
    Skips the last `exclude_recent` bars so we don't match "now" against itself.
    """
    if len(query_closes) < lookback:
        lookback = len(query_closes)
    q = _norm_path(query_closes[-lookback:])
    if len(q) < 10:
        return []

    history = history if history is not None else load_candles()
    n = len(history)
    if n < lookback + horizon + exclude_recent + 10:
        return []

    end_limit = n - exclude_recent - horizon
    matches: list[tuple[float, int]] = []

    for i in range(lookback - 1, end_limit, stride):
        window = history[i - lookback + 1 : i + 1]
        closes = [c["close"] for c in window]
        score = _cosine(q, _norm_path(closes))
        if score >= min_score:
            matches.append((score, i))

    matches.sort(reverse=True, key=lambda x: x[0])
    matches = matches[:top_k]

    out: list[PatternMatch] = []
    for score, end_idx in matches:
        start_idx = end_idx - lookback + 1
        entry = history[end_idx]["close"]
        fwd_idx = min(end_idx + horizon, n - 1)
        fwd = history[fwd_idx]["close"]
        move = fwd - entry
        pct = (move / entry) * 100 if entry else 0.0
        if move > 0.15:
            direction = "UP"
        elif move < -0.15:
            direction = "DOWN"
        else:
            direction = "FLAT"
        out.append(
            PatternMatch(
                start_idx=start_idx,
                end_idx=end_idx,
                score=round(score, 4),
                start_time_utc=history[start_idx]["time_utc"],
                end_time_utc=history[end_idx]["time_utc"],
                entry_close=entry,
                forward_close=fwd,
                forward_move_pts=round(move, 3),
                forward_move_pct=round(pct, 4),
                direction=direction,
            )
        )
    return out


def consensus_from_matches(matches: list[PatternMatch]) -> dict:
    if not matches:
        return {
            "bias": "NONE",
            "confidence": 0.0,
            "n": 0,
            "up": 0,
            "down": 0,
            "flat": 0,
            "avg_move_pts": 0.0,
            "matches": [],
        }
    up = sum(1 for m in matches if m.direction == "UP")
    down = sum(1 for m in matches if m.direction == "DOWN")
    flat = sum(1 for m in matches if m.direction == "FLAT")
    avg = sum(m.forward_move_pts or 0 for m in matches) / len(matches)
    if up > down and up >= max(2, len(matches) // 2):
        bias = "BUY"
        conf = up / len(matches)
    elif down > up and down >= max(2, len(matches) // 2):
        bias = "SELL"
        conf = down / len(matches)
    else:
        bias = "NONE"
        conf = max(up, down) / len(matches) if matches else 0.0
    return {
        "bias": bias,
        "confidence": round(conf, 3),
        "n": len(matches),
        "up": up,
        "down": down,
        "flat": flat,
        "avg_move_pts": round(avg, 3),
        "matches": [
            {
                "score": m.score,
                "start_utc": m.start_time_utc,
                "end_utc": m.end_time_utc,
                "move_pts": m.forward_move_pts,
                "move_pct": m.forward_move_pct,
                "direction": m.direction,
                "start_idx": m.start_idx,
                "end_idx": m.end_idx,
            }
            for m in matches
        ],
    }


def analyze_recent(
    lookback: int = 60,
    horizon: int = 30,
    top_k: int = 8,
    min_score: float = 0.85,
) -> dict:
    """Run matcher using the last `lookback` bars of stored history as query."""
    path = preferred_history_csv()
    if path is None:
        return {"ok": False, "error": "no_history_csv"}
    history = load_candles(path)
    if len(history) < lookback + 50:
        return {"ok": False, "error": "not_enough_bars", "bars": len(history)}

    query = [c["close"] for c in history[-lookback:]]
    matches = find_similar_patterns(
        query,
        history=history,
        lookback=lookback,
        horizon=horizon,
        top_k=top_k,
        min_score=min_score,
        exclude_recent=lookback + horizon + 10,
    )
    consensus = consensus_from_matches(matches)
    return {
        "ok": True,
        "path": str(path),
        "query_end_utc": history[-1]["time_utc"],
        "query_close": history[-1]["close"],
        "lookback": lookback,
        "horizon": horizon,
        "min_score": min_score,
        **consensus,
    }


def candles_for_match(start_idx: int, end_idx: int, horizon: int = 30) -> dict:
    history = load_candles()
    end = min(end_idx + horizon, len(history) - 1)
    window = history[start_idx : end + 1]
    return {
        "candles": window,
        "pattern_end_idx": end_idx - start_idx,
        "horizon": horizon,
    }
