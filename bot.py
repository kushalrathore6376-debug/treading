import time
import logging
import sys
from datetime import datetime

import MetaTrader5 as mt5
import config
from broker import MT5Broker
from heikin_ashi import convert_to_heikin_ashi, calculate_vwap
from strategy import (
    detect_buy_pattern,
    detect_sell_pattern,
    is_trading_hours,
    get_current_adx,
    get_current_atr,
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

# Track patterns already acted on: (timeframe, candle_2_time, direction) -> "triggered"/"failed"
acted_patterns = {}

# Adaptive mode tracking
trade_results = []          # P&L of last 10 completed trades
conservative_remaining = 0  # trades still to run in conservative mode

# Throttle session-block notices
_last_session_block_log = 0.0


def tf_label(timeframe):
    return config.TIMEFRAME_NAMES.get(timeframe, str(timeframe))


def calc_chase(direction, price, breakout_level):
    """Pts past breakout at the given price (positive = beyond breakout)."""
    if direction == "BUY":
        return round(price - breakout_level, 4)
    return round(breakout_level - price, 4)


def chase_allowed(chase):
    if not config.USE_CHASE_FILTER:
        return True, "chase_filter_off"
    if chase < config.CHASE_MIN:
        return False, f"chase_too_low ({chase:.4f} < {config.CHASE_MIN})"
    if chase > config.CHASE_MAX:
        return False, f"chase_too_high ({chase:.4f} > {config.CHASE_MAX})"
    return True, "chase_ok"


def log_event(event, **fields):
    """Structured one-line event for later parsing/analysis."""
    parts = [f"EVENT={event}"]
    for k, v in fields.items():
        if v is None:
            continue
        if isinstance(v, float):
            parts.append(f"{k}={v:.4f}" if abs(v) < 1000 else f"{k}={v:.2f}")
        else:
            parts.append(f"{k}={v}")
    logger.info(" | ".join(parts))


def _fmt(v, digits=2):
    if v is None:
        return "n/a"
    return f"{v:.{digits}f}"


def _hold_seconds(trade):
    opened = trade.get("open_time")
    if not opened:
        return None
    return round((datetime.now() - opened).total_seconds(), 1)


def _exit_mode_label(trade_or_flags):
    """Resolve human/mode label from trade dict or (cons_mode, hybrid_fp) tuple."""
    if isinstance(trade_or_flags, dict):
        if trade_or_flags.get("hybrid_first_profit"):
            return "HYBRID_FP"
        if trade_or_flags.get("conservative_mode"):
            return "CONS"
        return "RATCHET"
    cons_mode, hybrid_fp = trade_or_flags
    if hybrid_fp:
        return "HYBRID_FP"
    if cons_mode:
        return "CONS"
    return "RATCHET"


def _uses_first_profit(trade):
    """True when trade should bank the first profit step instead of ratcheting."""
    if trade.get("hybrid_first_profit") or trade.get("conservative_mode"):
        return True
    if (
        config.USE_HYBRID_EXIT
        and trade.get("lots", 0) <= config.HYBRID_FIRST_PROFIT_MAX_LOTS
    ):
        return True
    return False


def manage_trade(broker, cycle_count=0):
    """
    Monitor active trade. Exit approaches:
    1. Hybrid first-profit (lots <= HYBRID_FIRST_PROFIT_MAX_LOTS): close at first step (~$6)
    2. Conservative mode (last 3 trades in loss): close at first profit trigger
    3. Ratchet mode (0.03+ lots): trail with lot-size-aware lock
    """
    global active_trade, trade_results, conservative_remaining

    if active_trade is None:
        return

    instrument = config.INSTRUMENT
    label = tf_label(active_trade["timeframe"])

    actual_profit, mt5_position = broker.get_position_profit(instrument)

    if mt5_position is None:
        ticket = active_trade.get("position_ticket")
        if ticket:
            pnl = broker.get_deal_profit_for_position(ticket)
        else:
            pnl = broker.get_last_deal_profit(instrument)

        hold_s = _hold_seconds(active_trade)
        max_p = active_trade.get("max_profit")
        min_p = active_trade.get("min_profit")
        ratchets = active_trade.get("ratchet_count", 0)
        mode_now = _exit_mode_label(active_trade)

        if pnl is not None:
            trade_results.append(pnl)
            if len(trade_results) > 10:
                trade_results.pop(0)
            if conservative_remaining > 0:
                conservative_remaining -= 1
            last3 = trade_results[-3:] if len(trade_results) >= 3 else trade_results
            next_mode = "CONSERVATIVE" if sum(last3) < 0 else "RATCHET"
            exit_guess = (
                "TRAIL_OR_MANUAL"
                if (max_p is not None and max_p >= active_trade.get("ratchet_step_dollars", 0))
                else "STOP_LOSS_OR_MANUAL"
            )
            log_event(
                "TRADE_CLOSE",
                tf=label,
                dir=active_trade["direction"],
                ticket=ticket,
                entry=active_trade["entry_price"],
                breakout=active_trade.get("breakout_level"),
                chase_signal=active_trade.get("chase_signal"),
                chase_fill=active_trade.get("chase_fill"),
                lots=active_trade["lots"],
                sl_dist=active_trade.get("sl_distance"),
                adx=active_trade.get("adx"),
                atr=active_trade.get("atr"),
                vwap=active_trade.get("vwap"),
                mode=mode_now,
                pnl=pnl,
                hold_s=hold_s,
                max_profit=max_p,
                min_profit=min_p,
                ratchet_count=ratchets,
                locked=active_trade.get("locked_sl_dollars"),
                exit_guess=exit_guess,
                last3=str([round(x, 2) for x in last3]),
                next_mode=next_mode,
                cons_remaining=conservative_remaining,
            )
            logger.info(
                f"[{label}] Trade closed | P&L: ${pnl:.2f} | Hold: {hold_s}s | "
                f"Max$: {_fmt(max_p)} | Min$: {_fmt(min_p)} | Ratchets: {ratchets} | "
                f"Mode: {mode_now} | Last 3: {[round(x, 2) for x in last3]} | "
                f"Next mode: {next_mode}"
            )
        else:
            log_event(
                "TRADE_CLOSE_NO_PNL",
                tf=label,
                dir=active_trade["direction"],
                ticket=ticket,
                entry=active_trade["entry_price"],
                hold_s=hold_s,
                max_profit=max_p,
                min_profit=min_p,
                ratchet_count=ratchets,
                mode=mode_now,
            )
            logger.warning(
                f"[{label}] Trade closed — P&L not found in history "
                f"(ticket {ticket}) | Hold: {hold_s}s | Max$: {_fmt(max_p)}"
            )
        active_trade = None
        return

    # Track MFE / MAE while open
    if active_trade.get("max_profit") is None or actual_profit > active_trade["max_profit"]:
        active_trade["max_profit"] = actual_profit
    if active_trade.get("min_profit") is None or actual_profit < active_trade["min_profit"]:
        active_trade["min_profit"] = actual_profit

    step = active_trade["ratchet_step_dollars"]
    current_tp = active_trade["current_tp_dollars"]

    # --- First-profit exit (hybrid small size OR adaptive conservative) ---
    if _uses_first_profit(active_trade) and actual_profit >= current_tp:
        mode_now = _exit_mode_label(active_trade)
        try:
            broker.close_position(mt5_position)
            log_event(
                "FIRST_PROFIT_CLOSE",
                tf=label,
                profit=actual_profit,
                step=current_tp,
                hold_s=_hold_seconds(active_trade),
                mode=mode_now,
                lots=active_trade["lots"],
            )
            logger.info(
                f"[{label}] {mode_now} CLOSE | Profit: ${actual_profit:.2f} | "
                f"Step: ${current_tp:.2f} | Lots: {active_trade['lots']}"
            )
        except Exception as e:
            logger.error(f"[{label}] First-profit close failed: {e}")
        return

    # --- Ratchet TP (0.03+ lots) ---
    if actual_profit >= current_tp:
        new_tp = current_tp + step
        locked_profit = current_tp - step
        if locked_profit < 0:
            locked_profit = 0

        if locked_profit == 0:
            lots = active_trade["lots"]
            if lots >= 0.03:
                locked_profit = round(step * 0.50, 2)
            elif lots >= 0.02:
                locked_profit = round(step * 0.33, 2)

        active_trade["current_tp_dollars"] = new_tp
        active_trade["locked_sl_dollars"] = locked_profit
        active_trade["ratchet_count"] = active_trade.get("ratchet_count", 0) + 1

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
            log_event(
                "TP_RATCHET",
                tf=label,
                dir=active_trade["direction"],
                profit=actual_profit,
                hit=current_tp,
                new_tp=new_tp,
                locked=locked_profit,
                sl=new_sl_price,
                ratchet_n=active_trade["ratchet_count"],
                hold_s=_hold_seconds(active_trade),
            )
            logger.info(
                f"[{label}] TP RATCHET | Profit: ${actual_profit:.2f} hit ${current_tp:.2f} | "
                f"New TP: ${new_tp:.2f} | Locked: ${locked_profit:.2f} | "
                f"SL: {new_sl_price:.2f} | TP: {new_tp_price:.2f}"
            )
        except Exception as e:
            logger.error(f"[{label}] Failed to modify SL/TP: {e}")

    # Periodic open-trade snapshot (detailed, for analysis)
    snapshot_every = getattr(config, "TRADE_SNAPSHOT_EVERY_N", 10)
    if cycle_count % snapshot_every == 0:
        tick = broker.get_tick(instrument)
        if tick:
            current_price = tick.bid if active_trade["direction"] == "BUY" else tick.ask
            mode_label = _exit_mode_label(active_trade)
            log_event(
                "TRADE_SNAPSHOT",
                tf=label,
                dir=active_trade["direction"],
                entry=active_trade["entry_price"],
                price=current_price,
                sl=active_trade["sl_price"],
                profit=actual_profit,
                max_profit=active_trade.get("max_profit"),
                min_profit=active_trade.get("min_profit"),
                tp_target=active_trade["current_tp_dollars"],
                locked=active_trade["locked_sl_dollars"],
                ratchet_n=active_trade.get("ratchet_count", 0),
                hold_s=_hold_seconds(active_trade),
                mode=mode_label,
                chase_fill=active_trade.get("chase_fill"),
                adx=active_trade.get("adx"),
            )
            logger.info(
                f"  [{label}] {active_trade['direction']} | "
                f"Entry: {active_trade['entry_price']:.2f} | "
                f"Price: {current_price:.2f} | "
                f"SL: {active_trade['sl_price']:.2f} | "
                f"TP target: ${active_trade['current_tp_dollars']:.2f} | "
                f"Locked: ${active_trade['locked_sl_dollars']:.2f} | "
                f"Profit: ${actual_profit:.2f} | "
                f"Max$: {_fmt(active_trade.get('max_profit'))} | "
                f"Min$: {_fmt(active_trade.get('min_profit'))} | "
                f"Mode: {mode_label}"
            )


def _current_vwap(raw_candles):
    if not config.USE_VWAP_FILTER:
        return None
    vwap = calculate_vwap(raw_candles, config.VWAP_SESSION_START_HOUR_UTC)
    if vwap and vwap[-1] is not None:
        return vwap[-1]
    return None


def _try_enter(
    broker,
    direction,
    label,
    timeframe,
    pattern_key,
    breakout_level,
    sl_level,
    live_price,
    raw_candles,
    balance,
    cons_mode,
    mode_label,
):
    """Shared entry path for BUY/SELL after live price has crossed breakout."""
    global active_trade

    chase = calc_chase(direction, live_price, breakout_level)
    ok, chase_reason = chase_allowed(chase)
    adx_val = get_current_adx(raw_candles)
    atr_val = get_current_atr(raw_candles)
    vwap_val = _current_vwap(raw_candles)

    if not ok:
        log_event(
            "ENTRY_SKIP",
            tf=label,
            dir=direction,
            reason=chase_reason,
            breakout=breakout_level,
            live=live_price,
            chase=chase,
            chase_min=config.CHASE_MIN,
            chase_max=config.CHASE_MAX,
            adx=adx_val,
            atr=atr_val,
            vwap=vwap_val,
        )
        logger.info(
            f"[{label}] {direction} skipped: {chase_reason} | "
            f"Breakout: {breakout_level:.2f} | Live: {live_price:.2f} | Chase: {chase:.4f}"
        )
        # Late chase = done with this pattern. Too-low = keep waiting for extension.
        if chase > config.CHASE_MAX:
            acted_patterns[pattern_key] = "failed"
            return True
        return False  # chase still too small — wait for more push

    sl_price, tp_price, sl_distance, skip_reason = broker.calculate_structural_sl_tp(
        live_price, direction, sl_level, config.INSTRUMENT
    )
    if skip_reason:
        log_event(
            "ENTRY_SKIP",
            tf=label,
            dir=direction,
            reason=skip_reason,
            breakout=breakout_level,
            live=live_price,
            chase=chase,
            sl_level=sl_level,
            adx=adx_val,
            atr=atr_val,
        )
        logger.info(f"[{label}] {direction} skipped: {skip_reason}")
        acted_patterns[pattern_key] = "failed"
        return True

    lots = broker.calculate_lots(balance, live_price, config.INSTRUMENT, sl_distance)
    ratchet_step = round(config.TRAILING_TP_STEP * lots * config.CONTRACT_SIZE, 2)
    hybrid_fp = (
        config.USE_HYBRID_EXIT and lots <= config.HYBRID_FIRST_PROFIT_MAX_LOTS
    )
    # Hybrid small size overrides adaptive label for this fill
    fill_mode = _exit_mode_label((cons_mode, hybrid_fp))

    log_event(
        "SIGNAL",
        tf=label,
        dir=direction,
        breakout=breakout_level,
        live=live_price,
        chase=chase,
        sl_level=sl_level,
        sl=sl_price,
        tp=tp_price,
        sl_dist=sl_distance,
        lots=lots,
        ratchet=ratchet_step,
        adx=adx_val,
        atr=atr_val,
        vwap=vwap_val,
        balance=balance,
        mode=fill_mode,
        hybrid_fp=hybrid_fp,
        hour=datetime.now().hour,
    )
    logger.info(
        f"[{label}] *** {direction} SIGNAL *** | "
        f"Breakout: {breakout_level:.2f} | Live: {live_price:.2f} | Chase: {chase:.4f} | "
        f"ADX: {_fmt(adx_val, 1)} | ATR: {_fmt(atr_val, 4)} | VWAP: {_fmt(vwap_val)} | "
        f"Lots: {lots} | SL: {sl_price:.2f} | TP: {tp_price:.2f} | "
        f"SL dist: {sl_distance:.2f} | Ratchet: ${ratchet_step:.2f} | Mode: {fill_mode}"
    )

    try:
        if direction == "BUY":
            result = broker.place_buy(config.INSTRUMENT, lots, sl_price, tp_price)
        else:
            result = broker.place_sell(config.INSTRUMENT, lots, sl_price, tp_price)

        fill_price = result.price if result.price > 0 else live_price
        chase_fill = calc_chase(direction, fill_price, breakout_level)

        sl_price, tp_price, sl_distance, _ = broker.calculate_structural_sl_tp(
            fill_price, direction, sl_level, config.INSTRUMENT
        )
        ratchet_step = round(config.TRAILING_TP_STEP * lots * config.CONTRACT_SIZE, 2)

        open_pos = broker.get_open_positions_for_instrument(config.INSTRUMENT)
        position_ticket = open_pos[0].ticket if open_pos else None

        # If fill slipped outside chase band, log but keep managing (already filled)
        fill_ok, fill_chase_reason = chase_allowed(chase_fill)
        if not fill_ok:
            log_event(
                "FILL_CHASE_WARN",
                tf=label,
                dir=direction,
                reason=fill_chase_reason,
                chase_signal=chase,
                chase_fill=chase_fill,
                fill=fill_price,
                ticket=position_ticket,
            )

        active_trade = {
            "direction": direction,
            "entry_price": fill_price,
            "lots": lots,
            "timeframe": timeframe,
            "sl_price": sl_price,
            "tp_price": tp_price,
            "current_tp_dollars": ratchet_step,
            "locked_sl_dollars": 0,
            "ratchet_step_dollars": ratchet_step,
            "conservative_mode": cons_mode and not hybrid_fp,
            "hybrid_first_profit": hybrid_fp,
            "position_ticket": position_ticket,
            "open_time": datetime.now(),
            "breakout_level": breakout_level,
            "sl_level": sl_level,
            "sl_distance": sl_distance,
            "chase_signal": chase,
            "chase_fill": chase_fill,
            "adx": adx_val,
            "atr": atr_val,
            "vwap": vwap_val,
            "balance_at_entry": balance,
            "max_profit": 0.0,
            "min_profit": 0.0,
            "ratchet_count": 0,
        }
        acted_patterns[pattern_key] = "triggered"
        log_event(
            "FILL",
            tf=label,
            dir=direction,
            fill=fill_price,
            breakout=breakout_level,
            chase_signal=chase,
            chase_fill=chase_fill,
            sl=sl_price,
            tp=tp_price,
            sl_dist=sl_distance,
            lots=lots,
            ratchet=ratchet_step,
            ticket=position_ticket,
            adx=adx_val,
            atr=atr_val,
            vwap=vwap_val,
            mode=fill_mode,
            hybrid_fp=hybrid_fp,
        )
        logger.info(
            f"[{label}] {direction} FILLED @ {fill_price:.2f} | "
            f"Breakout: {breakout_level:.2f} | ChaseFill: {chase_fill:.4f} | "
            f"SL: {sl_price:.2f} | TP: {tp_price:.2f} | Lots: {lots} | "
            f"SL dist: {sl_distance:.2f} | Ratchet: ${ratchet_step:.2f} | "
            f"Mode: {fill_mode} | Ticket: {position_ticket}"
        )
    except Exception as e:
        log_event("ORDER_FAIL", tf=label, dir=direction, error=str(e))
        logger.error(f"[{label}] {direction} FAILED: {e}")
        acted_patterns[pattern_key] = "failed"
    return True


def check_entries(broker, cycle_count=0):
    """
    Check for entry signals with Scenario B filters:
    - Session block (local 22:00–06:00)
    - Chase band 0.05–0.20 pts past breakout
    """
    global active_trade, trade_results, conservative_remaining, _last_session_block_log

    if active_trade is not None:
        return

    if not is_trading_hours():
        now = time.time()
        if now - _last_session_block_log > 60:
            _last_session_block_log = now
            hour = datetime.now().hour
            log_event(
                "SESSION_BLOCK",
                hour=hour,
                block=f"{config.SESSION_BLOCK_START}-{config.SESSION_BLOCK_END}",
                ist_blackout=f"{config.NO_TRADE_START_IST}-{config.NO_TRADE_END_IST}",
            )
            logger.info(
                f"Session block — no new trades "
                f"(local hour={hour}, block={config.SESSION_BLOCK_START}:00–"
                f"{config.SESSION_BLOCK_END}:00 / IST blackout "
                f"{config.NO_TRADE_START_IST}-{config.NO_TRADE_END_IST})"
            )
        return

    # --- Determine adaptive mode ---
    if conservative_remaining == 0 and len(trade_results) >= 3:
        if sum(trade_results[-3:]) < 0:
            conservative_remaining = 3
            logger.info(
                f"Mode → CONSERVATIVE | Last 3 P&L: {[round(x, 2) for x in trade_results[-3:]]}"
            )
    cons_mode = conservative_remaining > 0
    # Base label before lot size known; fill path upgrades to HYBRID_FP when applicable
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
            if pattern_key not in acted_patterns:
                tick = broker.get_tick(instrument)
                if tick is not None:
                    if tick.ask > breakout_level:
                        handled = _try_enter(
                            broker,
                            "BUY",
                            label,
                            timeframe,
                            pattern_key,
                            breakout_level,
                            sl_level,
                            tick.ask,
                            raw_candles,
                            balance,
                            cons_mode,
                            mode_label,
                        )
                        if handled or active_trade is not None:
                            return
                    else:
                        if cycle_count % 20 == 0:
                            chase_now = calc_chase("BUY", tick.ask, breakout_level)
                            logger.info(
                                f"[{label}] BUY pattern valid, waiting for breakout | "
                                f"Need ask > {breakout_level:.2f}, got {tick.ask:.2f} | "
                                f"ChaseNow: {chase_now:.4f}"
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
            if pattern_key not in acted_patterns:
                tick = broker.get_tick(instrument)
                if tick is not None:
                    if tick.bid < breakout_level:
                        handled = _try_enter(
                            broker,
                            "SELL",
                            label,
                            timeframe,
                            pattern_key,
                            breakout_level,
                            sl_level,
                            tick.bid,
                            raw_candles,
                            balance,
                            cons_mode,
                            mode_label,
                        )
                        if handled or active_trade is not None:
                            return
                    else:
                        if cycle_count % 20 == 0:
                            chase_now = calc_chase("SELL", tick.bid, breakout_level)
                            logger.info(
                                f"[{label}] SELL pattern valid, waiting for breakout | "
                                f"Need bid < {breakout_level:.2f}, got {tick.bid:.2f} | "
                                f"ChaseNow: {chase_now:.4f}"
                            )
        else:
            if sell_reason and cycle_count % 20 == 0:
                logger.info(f"[{label}] No SELL: {sell_reason}")


def main():
    global active_trade

    logger.info("=" * 60)
    logger.info(f"  {config.INSTRUMENT_NAME} HEIKIN ASHI BOT v8 (B + hybrid + ADX)")
    logger.info(f"  Instrument: {config.INSTRUMENT}")
    logger.info(f"  Timeframes: {[tf_label(t) for t in config.TIMEFRAMES]}")
    logger.info(f"  Position size: {config.POSITION_SIZE_PCT * 100:.1f}% of balance")
    logger.info(
        f"  SL: ${config.MAX_LOSS_DOLLARS} | TP: ${config.TAKE_PROFIT_DOLLARS} | "
        f"Step: ${config.TRAILING_TP_STEP}"
    )
    logger.info(f"  ADX min: {config.ADX_MIN_THRESHOLD}")
    if config.USE_SESSION_BLOCK:
        logger.info(
            f"  Session block: {config.SESSION_BLOCK_START}:00–"
            f"{config.SESSION_BLOCK_END}:00 local (no new trades)"
        )
    if config.USE_CHASE_FILTER:
        logger.info(
            f"  Chase band: {config.CHASE_MIN}–{config.CHASE_MAX} pts past breakout"
        )
    if config.USE_HYBRID_EXIT:
        logger.info(
            f"  Hybrid exit: lots <= {config.HYBRID_FIRST_PROFIT_MAX_LOTS} "
            f"→ first profit (~${config.TRAILING_TP_STEP * config.HYBRID_FIRST_PROFIT_MAX_LOTS * config.CONTRACT_SIZE:.0f}); "
            f"else ratchet"
        )
    logger.info(f"  Check interval: {config.CHECK_INTERVAL}s")
    logger.info("=" * 60)
    log_event(
        "BOT_START",
        instrument=config.INSTRUMENT,
        session_block=f"{config.SESSION_BLOCK_START}-{config.SESSION_BLOCK_END}",
        chase_min=config.CHASE_MIN,
        chase_max=config.CHASE_MAX,
        use_session_block=config.USE_SESSION_BLOCK,
        use_chase_filter=config.USE_CHASE_FILTER,
        use_hybrid_exit=config.USE_HYBRID_EXIT,
        hybrid_fp_max_lots=config.HYBRID_FIRST_PROFIT_MAX_LOTS,
        adx_min=config.ADX_MIN_THRESHOLD,
    )

    broker = MT5Broker()

    balance = broker.get_account_balance()
    logger.info(f"Account balance: ${balance:,.2f}")

    existing = broker.get_open_positions_for_instrument(config.INSTRUMENT)
    if existing:
        logger.warning(f"Found {len(existing)} existing position(s) -- closing")
        broker.close_all_positions_for_instrument(config.INSTRUMENT)

    cycle = 0
    try:
        while True:
            cycle += 1

            try:
                manage_trade(broker, cycle)
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
        log_event("BOT_STOP", reason="KeyboardInterrupt")
    finally:
        broker.shutdown()
        logger.info("MT5 disconnected")


if __name__ == "__main__":
    main()
