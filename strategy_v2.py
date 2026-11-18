"""
Strategy V2 — Option 1 + Option 4
=================================
Option 1: Trigger on break of the SINGLE indecision candle's range, instead of
          waiting for a second directional candle to also close.
Option 4: Use RAW OHLC for the breakout level (HA lags by ~1 bar by construction).
          HA is still used for shape/color/wick recognition on the indecision bar.

BUY  setup: most recent CLOSED bar is HA-green with both wicks (bullish indecision)
            -> breakout at RAW high; SL at RAW low.
SELL setup: most recent CLOSED bar is HA-red with both wicks (bearish indecision)
            -> breakdown at RAW low; SL at RAW high.

All other filters (VWAP / ADX / ATR / body / blackout) are unchanged so the
comparison isolates the entry-timing change.
"""
from heikin_ashi import is_green, is_red, body_size
import config
from strategy import (
    has_both_wicks,
    is_trading_hours_for_time,
    is_above_vwap,
    is_below_vwap,
    is_trending,
    has_enough_volatility,
)


def detect_buy_pattern_v2(ha_candles, raw_candles):
    """
    V2: only ONE bar required (the indecision bar).
    Returns (valid, breakout_level, sl_level, reason).
    """
    if len(ha_candles) < 1 or len(raw_candles) < 1:
        return False, None, None, "not enough candles"

    if not is_trading_hours_for_time(ha_candles[-1]["time"]):
        return False, None, None, "outside trading hours"

    if not is_above_vwap(raw_candles):
        return False, None, None, "price below VWAP"
    if not is_trending(raw_candles):
        return False, None, None, f"ADX too low (need >= {config.ADX_MIN_THRESHOLD})"
    if not has_enough_volatility(raw_candles):
        return False, None, None, f"ATR too low (need >= {config.ATR_MIN_THRESHOLD})"

    indecision = ha_candles[-1]
    raw_indecision = raw_candles[-1]

    if body_size(indecision) < config.MIN_BODY_POINTS:
        return False, None, None, "indecision body too small"
    if not is_green(indecision):
        return False, None, None, "indecision not green (BUY needs bullish bias)"
    if not has_both_wicks(indecision):
        return False, None, None, "indecision missing both wicks"

    # Option 4: breakout level from RAW high, not HA high.
    breakout_level = raw_indecision["high"]
    sl_level = raw_indecision["low"]
    return True, breakout_level, sl_level, None


def detect_sell_pattern_v2(ha_candles, raw_candles):
    if len(ha_candles) < 1 or len(raw_candles) < 1:
        return False, None, None, "not enough candles"

    if not is_trading_hours_for_time(ha_candles[-1]["time"]):
        return False, None, None, "outside trading hours"

    if not is_below_vwap(raw_candles):
        return False, None, None, "price above VWAP"
    if not is_trending(raw_candles):
        return False, None, None, f"ADX too low (need >= {config.ADX_MIN_THRESHOLD})"
    if not has_enough_volatility(raw_candles):
        return False, None, None, f"ATR too low (need >= {config.ATR_MIN_THRESHOLD})"

    indecision = ha_candles[-1]
    raw_indecision = raw_candles[-1]

    if body_size(indecision) < config.MIN_BODY_POINTS:
        return False, None, None, "indecision body too small"
    if not is_red(indecision):
        return False, None, None, "indecision not red (SELL needs bearish bias)"
    if not has_both_wicks(indecision):
        return False, None, None, "indecision missing both wicks"

    breakout_level = raw_indecision["low"]
    sl_level = raw_indecision["high"]
    return True, breakout_level, sl_level, None
