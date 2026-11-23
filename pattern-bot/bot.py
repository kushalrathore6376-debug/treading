"""
Pattern Bot — trades ONLY from historical chart-shape consensus.

Modes (like main dashboard on Mac):
  - MT5 available (Windows) → live candles + optional real orders
  - No MT5 (Mac)            → PAPER mode: history CSV as market, virtual trades

Flow:
  1. Get recent M1 closes (MT5 or last bars of downloaded history)
  2. Find similar windows in 1y history
  3. Consensus UP → BUY, DOWN → SELL
  4. Enter (real / dry-run / paper ledger)
"""
from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import config
from pattern_engine import (
    analyze_recent,
    consensus_from_matches,
    find_similar_patterns,
    get_cached_history,
)

Path("logs").mkdir(exist_ok=True)

try:
    import MetaTrader5 as mt5
    from broker import MT5Broker

    MT5_AVAILABLE = True
except Exception as exc:
    mt5 = None
    MT5Broker = None
    MT5_AVAILABLE = False
    _mt5_import_error = exc

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
PAPER_LEDGER = Path("logs/paper_trades.jsonl")


def _scale_tp_from_avg_move(avg_move_pts: float) -> float:
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


def _paper_log(event: dict):
    event = {"ts": datetime.utcnow().isoformat() + "Z", **event}
    with PAPER_LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")
    logger.info(
        f"PAPER {event.get('action')} {event.get('bias')} "
        f"@ {event.get('entry')} conf={event.get('confidence')}"
    )


def maybe_enter_paper(signal: dict, entry: float):
    global _last_signal_key

    bias = signal.get("bias")
    conf = signal.get("confidence", 0)
    if bias not in ("BUY", "SELL"):
        logger.info(f"No trade bias ({bias}) conf={conf}")
        return
    if conf < config.PATTERN_MIN_CONFIDENCE:
        logger.info(f"Bias {bias} but conf {conf} < {config.PATTERN_MIN_CONFIDENCE}")
        return

    key = f"{bias}:{round(conf, 2)}:{signal.get('avg_move_pts')}"
    if key == _last_signal_key:
        return
    _last_signal_key = key

    tp_dollars = _scale_tp_from_avg_move(signal.get("avg_move_pts") or 0)
    _paper_log(
        {
            "action": "SIGNAL",
            "bias": bias,
            "confidence": conf,
            "entry": entry,
            "lots": config.LOTS_FIXED,
            "sl_dollars": config.MAX_LOSS_DOLLARS,
            "tp_dollars": round(tp_dollars, 2),
            "n": signal.get("n"),
            "avg_move_pts": signal.get("avg_move_pts"),
            "mode": "PAPER",
        }
    )


def maybe_enter_mt5(broker, signal: dict):
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

    key = f"{bias}:{round(conf, 2)}:{signal.get('avg_move_pts')}"
    if key == _last_signal_key:
        return
    _last_signal_key = key

    lots = config.LOTS_FIXED
    tick = broker.get_tick(config.INSTRUMENT)
    if tick is None:
        return
    entry = tick.ask if bias == "BUY" else tick.bid

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
        logger.info("DRY_RUN=True — order not sent. Set DRY_RUN=False for live MT5 orders.")
        return

    broker.place(bias, lots, sl, tp)


def run_paper_loop():
    """Mac / no-MT5: scan patterns on downloaded history (like main demo mode)."""
    logger.info("MODE: PAPER (no MT5) — using history CSV as market")
    logger.info("Virtual signals → logs/paper_trades.jsonl")

    while True:
        try:
            # Force refresh in case CSV was re-downloaded
            hist = get_cached_history(force=False)
            if len(hist) < config.PATTERN_LOOKBACK + 50:
                logger.error("Not enough history. Run: python3 download_history.py")
                time.sleep(config.CHECK_INTERVAL_SEC)
                continue

            # Same idea as UI "Find similar patterns" on latest bars
            signal = analyze_recent(
                lookback=config.PATTERN_LOOKBACK,
                horizon=config.PATTERN_HORIZON,
                top_k=config.PATTERN_TOP_K,
                min_score=config.PATTERN_MIN_SCORE,
            )
            if not signal.get("ok"):
                logger.warning(f"Pattern eval failed: {signal}")
            else:
                logger.info(
                    f"Consensus {signal['bias']} conf={signal['confidence']} "
                    f"↑{signal['up']} ↓{signal['down']} ↔{signal['flat']} "
                    f"avg={signal['avg_move_pts']} | end={signal.get('query_end_utc')} "
                    f"close={signal.get('query_close')}"
                )
                maybe_enter_paper(signal, float(signal.get("query_close") or 0))
        except Exception as e:
            logger.error(f"Cycle error: {e}")

        time.sleep(config.CHECK_INTERVAL_SEC)


def run_mt5_loop():
    logger.info("MODE: MT5 live candles")
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
                        maybe_enter_mt5(broker, signal)
            except Exception as e:
                logger.error(f"Cycle error: {e}")

            time.sleep(config.CHECK_INTERVAL_SEC)
    finally:
        broker.shutdown()


def main():
    force_paper = getattr(config, "FORCE_PAPER_MODE", False)
    use_mt5 = MT5_AVAILABLE and not force_paper

    logger.info("=" * 60)
    logger.info("  PATTERN BOT (history shape-match)")
    logger.info(f"  Instrument: {config.INSTRUMENT}")
    logger.info(
        f"  Lookback={config.PATTERN_LOOKBACK} Horizon={config.PATTERN_HORIZON} "
        f"MinScore={config.PATTERN_MIN_SCORE} MinConf={config.PATTERN_MIN_CONFIDENCE}"
    )
    logger.info(
        f"  MT5_AVAILABLE={MT5_AVAILABLE} FORCE_PAPER={force_paper} "
        f"DRY_RUN={config.DRY_RUN} lots={config.LOTS_FIXED}"
    )
    if not MT5_AVAILABLE:
        logger.info(f"  MT5 import skip: {_mt5_import_error}")
    logger.info("=" * 60)

    hist = get_cached_history()
    if not hist:
        logger.error("No history CSV in download/. Run: python3 download_history.py")
        sys.exit(1)
    logger.info(f"History loaded: {len(hist)} bars")

    try:
        if use_mt5:
            run_mt5_loop()
        else:
            run_paper_loop()
    except KeyboardInterrupt:
        logger.info("Stopped")


if __name__ == "__main__":
    main()
