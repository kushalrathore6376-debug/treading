"""
Pattern Bot — trades ONLY from historical chart-shape consensus.

Flow:
  1. Pull recent M1 closes from MT5
  2. Find similar windows in 1y Dukascopy history (normalized path)
  3. If matches mostly went UP after → BUY; DOWN → SELL
  4. Place small fixed-risk trade (or DRY_RUN log only)

This is intentionally separate from the HA breakout bot in ../treading.
"""
from __future__ import annotations

import logging
import sys
import time
from datetime import datetime
from pathlib import Path

# Live trading needs MetaTrader5 — Windows only
try:
    import MetaTrader5 as mt5
except ImportError:
    print(
        "ERROR: MetaTrader5 is not available on this machine.\n"
        "  - Live bot runs on Windows with MT5 installed.\n"
        "  - On Mac: use download + UI only:\n"
        "      python3 -m pip install -r requirements.txt\n"
        "      # install Node.js, then:\n"
        "      python3 download_history.py\n"
        "      python3 app.py\n"
    )
    sys.exit(1)

import config
from broker import MT5Broker
from pattern_engine import (
    consensus_from_matches,
    find_similar_patterns,
    get_cached_history,
)

Path("logs").mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(config.LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

_last_signal_key = None


def _scale_tp_from_avg_move(avg_move_pts: float) -> float:
    """Use historical avg absolute move to set TP dollars (capped)."""
    # 0.02 lots * 100 oz * 1 pt ≈ $2 per point
    pts = abs(avg_move_pts)
    dollars = pts * config.LOTS_FIXED * config.CONTRACT_SIZE
    return max(config.MAX_LOSS_DOLLARS, min(dollars, 40.0))


def evaluate_pattern(live_closes: list[float]) -> dict:
    history = get_cached_history()
    if len(history) < config.PATTERN_LOOKBACK + config.PATTERN_HORIZON + 100:
        return {"ok": False, "error": "history_too_short", "bars": len(history)}

    matches = find_similar_patterns(
        live_closes,
        history=history,
        lookback=config.PATTERN_LOOKBACK,
        horizon=config.PATTERN_HORIZON,
        top_k=config.PATTERN_TOP_K,
        min_score=config.PATTERN_MIN_SCORE,
        stride=config.PATTERN_STRIDE,
        exclude_recent=config.PATTERN_EXCLUDE_RECENT,
    )
    consensus = consensus_from_matches(matches)
    return {"ok": True, **consensus}


def maybe_enter(broker: MT5Broker, signal: dict):
    global _last_signal_key

    bias = signal.get("bias")
    conf = signal.get("confidence", 0)
    if bias not in ("BUY", "SELL"):
        logger.info(f"No trade bias ({bias}) conf={conf}")
        return
    if conf < config.PATTERN_MIN_CONFIDENCE:
        logger.info(f"Bias {bias} but conf {conf} < {config.PATTERN_MIN_CONFIDENCE}")
        return

    open_pos = broker.get_open_positions_for_instrument(config.INSTRUMENT)
    if len(open_pos) >= config.MAX_OPEN_POSITIONS:
        logger.info("Already in position — skip")
        return

    # de-dupe same consensus within a short window
    key = f"{bias}:{round(conf, 2)}:{signal.get('avg_move_pts')}"
    if key == _last_signal_key:
        return
    _last_signal_key = key

    lots = config.LOTS_FIXED
    tick = broker.get_tick(config.INSTRUMENT)
    if tick is None:
        return
    entry = tick.ask if bias == "BUY" else tick.bid

    # Temporarily scale TP from history
    old_tp = config.TAKE_PROFIT_DOLLARS
    config.TAKE_PROFIT_DOLLARS = _scale_tp_from_avg_move(signal.get("avg_move_pts") or 0)
    sl, tp = broker.dollar_sl_tp(entry, bias, lots)
    config.TAKE_PROFIT_DOLLARS = old_tp

    logger.info(
        f"SIGNAL {bias} conf={conf:.0%} n={signal.get('n')} "
        f"avg_move={signal.get('avg_move_pts')} | "
        f"entry~{entry:.2f} SL={sl} TP={tp} lots={lots} dry={config.DRY_RUN}"
    )

    if config.DRY_RUN:
        logger.info("DRY_RUN=True — order not sent. Set DRY_RUN=False in config.py to go live.")
        return

    broker.place(bias, lots, sl, tp)


def main():
    logger.info("=" * 60)
    logger.info("  PATTERN BOT (history shape-match only)")
    logger.info(f"  Instrument: {config.INSTRUMENT}")
    logger.info(
        f"  Lookback={config.PATTERN_LOOKBACK} Horizon={config.PATTERN_HORIZON} "
        f"MinScore={config.PATTERN_MIN_SCORE} MinConf={config.PATTERN_MIN_CONFIDENCE}"
    )
    logger.info(f"  DRY_RUN={config.DRY_RUN} lots={config.LOTS_FIXED}")
    logger.info("=" * 60)

    # Warm history cache
    hist = get_cached_history()
    if not hist:
        logger.error(
            "No history CSV in download/. Run: py download_history.py"
        )
        sys.exit(1)
    logger.info(f"History loaded: {len(hist)} bars")

    broker = MT5Broker()
    try:
        while True:
            try:
                candles = broker.get_candles(
                    config.INSTRUMENT, mt5.TIMEFRAME_M1, config.CANDLE_COUNT
                )
                if len(candles) < config.PATTERN_LOOKBACK:
                    logger.warning("Not enough live candles")
                else:
                    closes = [c["close"] for c in candles]
                    signal = evaluate_pattern(closes)
                    if not signal.get("ok"):
                        logger.warning(f"Pattern eval failed: {signal}")
                    else:
                        logger.info(
                            f"Consensus {signal['bias']} conf={signal['confidence']} "
                            f"↑{signal['up']} ↓{signal['down']} ↔{signal['flat']} "
                            f"avg={signal['avg_move_pts']}"
                        )
                        maybe_enter(broker, signal)
            except Exception as e:
                logger.error(f"Cycle error: {e}")

            time.sleep(config.CHECK_INTERVAL_SEC)
    except KeyboardInterrupt:
        logger.info("Stopped")
    finally:
        broker.shutdown()


if __name__ == "__main__":
    main()
