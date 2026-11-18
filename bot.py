import time
import logging
import sys
from datetime import datetime

import MetaTrader5 as mt5
import config
from broker import MT5Broker
from heikin_ashi import convert_to_heikin_ashi
from strategy import (
    detect_buy_pattern,
    detect_sell_pattern,
    is_trading_hours,
    get_current_adx,
)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(config.LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# Track open position
active_trade = None
# {"direction", "entry_price", "lots", "timeframe", "sl_price", "tp_price", "conservative_mode"}

# Track patterns already acted on: (timeframe, candle_2_time, direction) -> "triggered"/"failed"
acted_patterns = {}

# Adaptive mode tracking
trade_results = []          # P&L of last 10 completed trades
conservative_remaining = 0  # trades still to run in conservative mode


def tf_label(timeframe):
    return config.TIMEFRAME_NAMES.get(timeframe, str(timeframe))


def manage_trade(broker):
    """
    Monitor active trade. Two exit approaches:
    1. Conservative mode (last 3 trades in loss): close at first profit trigger
    2. Ratchet mode (last 3 trades profitable): trail with lot-size-aware lock
       - 0.03+ lots: lock 50% of step on first ratchet
       - 0.02 lots:  lock 33% of step on first ratchet
       - 0.01 lots:  lock $0 (breakeven) on first ratchet
    """
    global active_trade, trade_results, conservative_remaining

    if active_trade is None:
        return

    instrument = config.INSTRUMENT
    label = tf_label(active_trade["timeframe"])

    actual_profit, mt5_position = broker.get_position_profit(instrument)

    if mt5_position is None:
        # Trade closed — look up P&L by position ticket so we never get stale data
        ticket = active_trade.get("position_ticket")
        if ticket:
            pnl = broker.get_deal_profit_for_position(ticket)
        else:
            pnl = broker.get_last_deal_profit(instrument)

        if pnl is not None:
            trade_results.append(pnl)
            if len(trade_results) > 10:
                trade_results.pop(0)
            if conservative_remaining > 0:
                conservative_remaining -= 1
            last3 = trade_results[-3:] if len(trade_results) >= 3 else trade_results
            next_mode = "CONSERVATIVE" if sum(last3) < 0 else "RATCHET"
            logger.info(
                f"[{label}] Trade closed | P&L: ${pnl:.2f} | "
                f"Last 3: {[round(x, 2) for x in last3]} | "
                f"Next mode: {next_mode} | Cons remaining: {conservative_remaining}"
            )
        else:
            logger.warning(
                f"[{label}] Trade closed — P&L not found in history "
                f"(ticket {ticket})"
            )
        active_trade = None
        return

    step = active_trade["ratchet_step_dollars"]
    current_tp = active_trade["current_tp_dollars"]

    # --- Conservative mode: close at first profit trigger ---
    if active_trade.get("conservative_mode") and actual_profit >= current_tp:
        try:
            broker.close_position(mt5_position)
            logger.info(
                f"[{label}] CONSERVATIVE CLOSE | Profit: ${actual_profit:.2f} | "
                f"Locked: ${actual_profit:.2f}"
            )
        except Exception as e:
            logger.error(f"[{label}] Conservative close failed: {e}")
        return

    # --- Ratchet TP ---
    if actual_profit >= current_tp:
        new_tp = current_tp + step
        locked_profit = current_tp - step
        if locked_profit < 0:
            locked_profit = 0

        # Approach 1: lot-size-aware lock on first ratchet
        if locked_profit == 0:
            lots = active_trade["lots"]
            if lots >= 0.03:
                locked_profit = round(step * 0.50, 2)
            elif lots >= 0.02:
                locked_profit = round(step * 0.33, 2)

        active_trade["current_tp_dollars"] = new_tp
        active_trade["locked_sl_dollars"] = locked_profit

        entry = active_trade["entry_price"]
        lots = active_trade["lots"]
        sym_info = broker._get_symbol_info(instrument)
        ppd = 1.0 / (lots * sym_info.trade_contract_size)

        if active_trade["direction"] == "BUY":
            new_sl_price = round(entry + locked_profit * ppd, 2)
            new_tp_price = round(entry + 500, 2)
        else:
            new_sl_price = round(entry - locked_profit * ppd, 2)
            new_tp_price = round(entry - 500, 2)

        active_trade["sl_price"] = new_sl_price
        active_trade["tp_price"] = new_tp_price

        try:
            broker.modify_position_sl_tp(instrument, new_sl_price, new_tp_price)
            logger.info(
                f"[{label}] TP RATCHET | Profit: ${actual_profit:.2f} hit ${current_tp:.2f} | "
                f"New TP: ${new_tp:.2f} | Locked: ${locked_profit:.2f} | "
                f"SL: {new_sl_price:.2f} | TP: {new_tp_price:.2f}"
            )
        except Exception as e:
            logger.error(f"[{label}] Failed to modify SL/TP: {e}")

    # Log current status
    tick = broker.get_tick(instrument)
    if tick:
        current_price = tick.bid if active_trade["direction"] == "BUY" else tick.ask
        mode_label = "CONS" if active_trade.get("conservative_mode") else "RATCHET"
        logger.info(
            f"  [{label}] {active_trade['direction']} | "
            f"Entry: {active_trade['entry_price']:.2f} | "
            f"Price: {current_price:.2f} | "
            f"SL: {active_trade['sl_price']:.2f} | "
            f"TP target: ${active_trade['current_tp_dollars']:.2f} | "
            f"Locked: ${active_trade['locked_sl_dollars']:.2f} | "
            f"Profit: ${actual_profit:.2f} | Mode: {mode_label}"
        )


def check_entries(broker, cycle_count=0):
    """
    Check for entry signals:
    1. Get completed candles, convert to HA
    2. Check 2-candle pattern + filters
    3. If pattern valid, check live tick vs breakout level
    4. Calculate lots from position_size_pct, calculate fixed $SL/$TP prices
    5. Place order with SL and TP on broker
    """
    global active_trade, trade_results, conservative_remaining

    if active_trade is not None:
        return

    if not is_trading_hours():
        return

    # --- Determine adaptive mode ---
    if conservative_remaining == 0 and len(trade_results) >= 3:
        if sum(trade_results[-3:]) < 0:
            conservative_remaining = 3
            logger.info(
                f"Mode → CONSERVATIVE | Last 3 P&L: {[round(x, 2) for x in trade_results[-3:]]}"
            )
    cons_mode = conservative_remaining > 0
    mode_label = "CONSERVATIVE" if cons_mode else "RATCHET"

    instrument = config.INSTRUMENT
    balance = broker.get_account_balance()

    for timeframe in config.TIMEFRAMES:
        label = tf_label(timeframe)

        raw_candles = broker.get_candles(instrument, timeframe, config.CANDLE_COUNT)
        if len(raw_candles) < 2:
            continue

        ha_candles = convert_to_heikin_ashi(raw_candles)
        candle_2_time = raw_candles[-1]["time"]

        # --- Check BUY pattern ---
        buy_pattern, breakout_level, sl_level, buy_reason = detect_buy_pattern(
            ha_candles, raw_candles
        )
        if buy_pattern:
            pattern_key = (timeframe, candle_2_time, "BUY")
            if pattern_key in acted_patterns:
                continue

            tick = broker.get_tick(instrument)
            if tick is None:
                continue

            # Live price must cross above breakout level
            if tick.ask > breakout_level:
                entry_price = tick.ask

                # Gate on structural SL
                sl_price, tp_price, sl_distance, skip_reason = (
                    broker.calculate_structural_sl_tp(
                        entry_price, "BUY", sl_level, instrument
                    )
                )
                if skip_reason:
                    logger.info(f"[{label}] BUY skipped: {skip_reason}")
                    acted_patterns[pattern_key] = "failed"
                    continue

                lots = broker.calculate_lots(
                    balance, entry_price, instrument, sl_distance
                )
                ratchet_step = round(
                    config.TRAILING_TP_STEP * lots * config.CONTRACT_SIZE, 2
                )

                adx_val = get_current_adx(raw_candles)
                adx_str = f"{adx_val:.1f}" if adx_val is not None else "n/a"
                logger.info(
                    f"[{label}] *** BUY SIGNAL *** | "
                    f"Breakout: {breakout_level:.2f} | Live: {tick.ask:.2f} | "
                    f"ADX: {adx_str} | Lots: {lots} | SL: {sl_price:.2f} | TP: {tp_price:.2f} | "
                    f"SL dist: {sl_distance:.2f} | Ratchet: ${ratchet_step:.2f} | Mode: {mode_label}"
                )
                try:
                    result = broker.place_buy(instrument, lots, sl_price, tp_price)
                    fill_price = result.price if result.price > 0 else entry_price
                    # Recalculate from actual fill price
                    sl_price, tp_price, sl_distance, _ = (
                        broker.calculate_structural_sl_tp(
                            fill_price, "BUY", sl_level, instrument
                        )
                    )
                    ratchet_step = round(
                        config.TRAILING_TP_STEP * lots * config.CONTRACT_SIZE, 2
                    )
                    # Capture position ticket for reliable P&L lookup on close
                    open_pos = broker.get_open_positions_for_instrument(instrument)
                    position_ticket = open_pos[0].ticket if open_pos else None
                    active_trade = {
                        "direction": "BUY",
                        "entry_price": fill_price,
                        "lots": lots,
                        "timeframe": timeframe,
                        "sl_price": sl_price,
                        "tp_price": tp_price,
                        "current_tp_dollars": ratchet_step,
                        "locked_sl_dollars": 0,
                        "ratchet_step_dollars": ratchet_step,
                        "conservative_mode": cons_mode,
                        "position_ticket": position_ticket,
                    }
                    acted_patterns[pattern_key] = "triggered"
                    logger.info(
                        f"[{label}] BUY FILLED @ {fill_price:.2f} | SL: {sl_price:.2f} | TP: {tp_price:.2f} | Lots: {lots} | SL dist: {sl_distance:.2f} | Ratchet: ${ratchet_step:.2f}"
                    )
                except Exception as e:
                    logger.error(f"[{label}] BUY FAILED: {e}")
                    acted_patterns[pattern_key] = "failed"
                return
            else:
                logger.info(
                    f"[{label}] BUY pattern valid, waiting for breakout | "
                    f"Need ask > {breakout_level:.2f}, got {tick.ask:.2f}"
                )
        else:
            if buy_reason and cycle_count % 20 == 0:
                logger.info(f"[{label}] No BUY: {buy_reason}")

        # --- Check SELL pattern ---
        sell_pattern, breakout_level, sl_level, sell_reason = detect_sell_pattern(
            ha_candles, raw_candles
        )
        if sell_pattern:
            pattern_key = (timeframe, candle_2_time, "SELL")
            if pattern_key in acted_patterns:
                continue

            tick = broker.get_tick(instrument)
            if tick is None:
                continue

            # Live price must cross below breakout level
            if tick.bid < breakout_level:
                entry_price = tick.bid

                # Gate on structural SL
                sl_price, tp_price, sl_distance, skip_reason = (
                    broker.calculate_structural_sl_tp(
                        entry_price, "SELL", sl_level, instrument
                    )
                )
                if skip_reason:
                    logger.info(f"[{label}] SELL skipped: {skip_reason}")
                    acted_patterns[pattern_key] = "failed"
                    continue

                lots = broker.calculate_lots(
                    balance, entry_price, instrument, sl_distance
                )
                ratchet_step = round(
                    config.TRAILING_TP_STEP * lots * config.CONTRACT_SIZE, 2
                )

                adx_val = get_current_adx(raw_candles)
                adx_str = f"{adx_val:.1f}" if adx_val is not None else "n/a"
                logger.info(
                    f"[{label}] *** SELL SIGNAL *** | "
                    f"Breakout: {breakout_level:.2f} | Live: {tick.bid:.2f} | "
                    f"ADX: {adx_str} | Lots: {lots} | SL: {sl_price:.2f} | TP: {tp_price:.2f} | "
                    f"SL dist: {sl_distance:.2f} | Ratchet: ${ratchet_step:.2f} | Mode: {mode_label}"
                )
                try:
                    result = broker.place_sell(instrument, lots, sl_price, tp_price)
                    fill_price = result.price if result.price > 0 else entry_price
                    # Recalculate from actual fill price
                    sl_price, tp_price, sl_distance, _ = (
                        broker.calculate_structural_sl_tp(
                            fill_price, "SELL", sl_level, instrument
                        )
                    )
                    ratchet_step = round(
                        config.TRAILING_TP_STEP * lots * config.CONTRACT_SIZE, 2
                    )
                    # Capture position ticket for reliable P&L lookup on close
                    open_pos = broker.get_open_positions_for_instrument(instrument)
                    position_ticket = open_pos[0].ticket if open_pos else None
                    active_trade = {
                        "direction": "SELL",
                        "entry_price": fill_price,
                        "lots": lots,
                        "timeframe": timeframe,
                        "sl_price": sl_price,
                        "tp_price": tp_price,
                        "current_tp_dollars": ratchet_step,
                        "locked_sl_dollars": 0,
                        "ratchet_step_dollars": ratchet_step,
                        "conservative_mode": cons_mode,
                        "position_ticket": position_ticket,
                    }
                    acted_patterns[pattern_key] = "triggered"
                    logger.info(
                        f"[{label}] SELL FILLED @ {fill_price:.2f} | SL: {sl_price:.2f} | TP: {tp_price:.2f} | Lots: {lots} | SL dist: {sl_distance:.2f} | Ratchet: ${ratchet_step:.2f}"
                    )
                except Exception as e:
                    logger.error(f"[{label}] SELL FAILED: {e}")
                    acted_patterns[pattern_key] = "failed"
                return
            else:
                logger.info(
                    f"[{label}] SELL pattern valid, waiting for breakout | "
                    f"Need bid < {breakout_level:.2f}, got {tick.bid:.2f}"
                )
        else:
            if sell_reason and cycle_count % 20 == 0:
                logger.info(f"[{label}] No SELL: {sell_reason}")


def main():
    global active_trade

    logger.info("=" * 60)
    logger.info(f"  {config.INSTRUMENT_NAME} HEIKIN ASHI BOT v6")
    logger.info(f"  Instrument: {config.INSTRUMENT}")
    logger.info(f"  Timeframes: {[tf_label(t) for t in config.TIMEFRAMES]}")
    logger.info(f"  Position size: {config.POSITION_SIZE_PCT * 100:.1f}% of balance")
    logger.info(
        f"  SL: ${config.MAX_LOSS_DOLLARS} | TP: ${config.TAKE_PROFIT_DOLLARS} | Step: ${config.TRAILING_TP_STEP}"
    )
    logger.info(f"  Check interval: {config.CHECK_INTERVAL}s")
    logger.info("=" * 60)

    broker = MT5Broker()

    balance = broker.get_account_balance()
    logger.info(f"Account balance: ${balance:,.2f}")

    # Clean up any existing positions
    existing = broker.get_open_positions_for_instrument(config.INSTRUMENT)
    if existing:
        logger.warning(f"Found {len(existing)} existing position(s) -- closing")
        broker.close_all_positions_for_instrument(config.INSTRUMENT)

    cycle = 0
    try:
        while True:
            cycle += 1

            try:
                manage_trade(broker)
            except Exception as e:
                logger.error(f"Trade management error: {e}")

            try:
                check_entries(broker, cycle)
            except Exception as e:
                logger.error(f"Entry check error: {e}")

            if active_trade is None and cycle % 10 == 0:
                logger.info("Scanning... no open trades")

            time.sleep(config.CHECK_INTERVAL)

    except KeyboardInterrupt:
        logger.info("Bot stopped (Ctrl+C)")
    finally:
        broker.shutdown()
        logger.info("MT5 disconnected")


if __name__ == "__main__":
    main()
