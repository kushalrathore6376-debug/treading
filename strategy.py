from datetime import datetime, timezone, timedelta
from heikin_ashi import (is_green, is_red, body_size, upper_wick, lower_wick,
                         wick_ratio, calculate_vwap,
                         calculate_adx, calculate_atr)
import config


def has_both_wicks(candle):
    body = body_size(candle)
    if body == 0:
        return False
    uw = upper_wick(candle)
    lw = lower_wick(candle)
    return (wick_ratio(uw, body) >= config.BOTH_WICKS_MIN and
            wick_ratio(lw, body) >= config.BOTH_WICKS_MIN)


def has_only_upper_wick(candle):
    body = body_size(candle)
    if body == 0:
        return False
    uw = upper_wick(candle)
    lw = lower_wick(candle)
    return (wick_ratio(uw, body) >= config.REQUIRED_WICK_MIN and
            wick_ratio(lw, body) < config.NO_WICK_TOLERANCE)




def has_only_lower_wick(candle):
    body = body_size(candle)
    if body == 0:
        return False
    uw = upper_wick(candle)
    lw = lower_wick(candle)
    return (wick_ratio(lw, body) >= config.REQUIRED_WICK_MIN and
            wick_ratio(uw, body) < config.NO_WICK_TOLERANCE)


# --- Time Filter ---

def _in_session_block(hour):
    """True if local hour is inside thin-session block (e.g. 22:00–06:00)."""
    if not getattr(config, "USE_SESSION_BLOCK", False):
        return False
    start = config.SESSION_BLOCK_START
    end = config.SESSION_BLOCK_END
    if start == end:
        return False
    if start > end:
        # wraps midnight: e.g. 22 → 6 means hour>=22 or hour<6
        return hour >= start or hour < end
    return start <= hour < end


def is_trading_hours():
    now_local = datetime.now()
    now_utc = datetime.now(timezone.utc)
    now_ist = now_utc + timedelta(hours=5, minutes=30)
    if config.NO_TRADE_START_IST <= now_ist.hour < config.NO_TRADE_END_IST:
        return False
    if _in_session_block(now_local.hour):
        return False
    if not config.USE_TIME_FILTER:
        return True
    return config.TRADE_START_HOUR_UTC <= now_utc.hour < config.TRADE_END_HOUR_UTC


def is_trading_hours_for_time(time_str):
    try:
        dt = None
        for fmt in ["%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"]:
            try:
                dt = datetime.strptime(str(time_str), fmt)
                break
            except ValueError:
                continue
        if dt is None:
            dt = datetime.utcfromtimestamp(int(time_str))
        if _in_session_block(dt.hour):
            return False
        if not config.USE_TIME_FILTER:
            return True
        return config.TRADE_START_HOUR_UTC <= dt.hour < config.TRADE_END_HOUR_UTC
    except Exception:
        return True


# --- VWAP Filter ---

def is_above_vwap(raw_candles):
    if not config.USE_VWAP_FILTER:
        return True
    vwap = calculate_vwap(raw_candles, config.VWAP_SESSION_START_HOUR_UTC)
    if not vwap or vwap[-1] is None:
        return True
    return raw_candles[-1]["close"] > vwap[-1]


def is_below_vwap(raw_candles):
    if not config.USE_VWAP_FILTER:
        return True
    vwap = calculate_vwap(raw_candles, config.VWAP_SESSION_START_HOUR_UTC)
    if not vwap or vwap[-1] is None:
        return True
    return raw_candles[-1]["close"] < vwap[-1]


# --- ADX Chop Filter ---

def is_trending(raw_candles):
    if not config.USE_ADX_FILTER:
        return True
    adx = calculate_adx(raw_candles, config.ADX_PERIOD)
    if not adx or adx[-1] is None:
        return True
    return adx[-1] >= config.ADX_MIN_THRESHOLD


def get_current_adx(raw_candles):
    adx = calculate_adx(raw_candles, config.ADX_PERIOD)
    if adx and adx[-1] is not None:
        return adx[-1]
    return None


def get_current_atr(raw_candles):
    atr = calculate_atr(raw_candles, config.ATR_PERIOD)
    if atr and atr[-1] is not None:
        return atr[-1]
    return None


# --- ATR Volatility Filter ---

def has_enough_volatility(raw_candles):
    if not config.USE_ATR_FILTER:
        return True
    atr = get_current_atr(raw_candles)
    if atr is None:
        return True
    return atr >= config.ATR_MIN_THRESHOLD


# --- Pattern Detection (2-candle HA pattern) ---
# Returns (pattern_valid, breakout_level, sl_level)
#   breakout_level: HA Candle 2 high/low (where to enter)
#   sl_level: RAW Candle 1 low/high (structural stop — actual price market tested)

def detect_buy_pattern(ha_candles, raw_candles=None):
    """
    BUY pattern (2 HA candles):
      Candle 1 (ha[-2]): has both wicks (indecision)
      Candle 2 (ha[-1]): GREEN + upper wick (buyers in control)
      Breakout: Candle 2's high
      SL: RAW Candle 1's low (structural)

    Returns (True, breakout_level, sl_level, reason) or (False, None, None, reason)
    """
    if len(ha_candles) < 2:
        return False, None, None, "not enough candles"

    if not is_trading_hours_for_time(ha_candles[-1]["time"]):
        return False, None, None, "outside trading hours"

    if raw_candles:
        if not is_above_vwap(raw_candles):
            return False, None, None, "price below VWAP"
        if not is_trending(raw_candles):
            return False, None, None, f"ADX too low (choppy market, need >= {config.ADX_MIN_THRESHOLD})"
        if not has_enough_volatility(raw_candles):
            return False, None, None, f"ATR too low (low volatility, need >= {config.ATR_MIN_THRESHOLD})"

    candle_1 = ha_candles[-2]
    candle_2 = ha_candles[-1]

    if body_size(candle_1) < config.MIN_BODY_POINTS:
        return False, None, None, f"Candle 1 body too small ({body_size(candle_1):.2f} < {config.MIN_BODY_POINTS})"
    if body_size(candle_2) < config.MIN_BODY_POINTS:
        return False, None, None, f"Candle 2 body too small ({body_size(candle_2):.2f} < {config.MIN_BODY_POINTS})"

    if not is_green(candle_1):
        return False, None, None, "Candle 1 not green (BUY needs both green)"
    if not has_both_wicks(candle_1):
        return False, None, None, "Candle 1 missing both wicks (no indecision)"
    if not is_green(candle_2):
        return False, None, None, "Candle 2 not green"
    if not has_only_upper_wick(candle_2):
        return False, None, None, "Candle 2 missing upper wick or has lower wick"

    breakout_level = candle_2["high"]

    # SL at RAW Candle 1's low (actual market level, not smoothed HA)
    if raw_candles and len(raw_candles) >= 2:
        sl_level = raw_candles[-2]["low"]
    else:
        sl_level = candle_1["low"]

    return True, breakout_level, sl_level, None


def detect_sell_pattern(ha_candles, raw_candles=None):
    """
    SELL pattern (2 HA candles):
      Candle 1 (ha[-2]): has both wicks (indecision)
      Candle 2 (ha[-1]): RED + lower wick (sellers in control)
      Breakout: Candle 2's low
      SL: RAW Candle 1's high (structural)

    Returns (True, breakout_level, sl_level, reason) or (False, None, None, reason)
    """
    if len(ha_candles) < 2:
        return False, None, None, "not enough candles"

    if not is_trading_hours_for_time(ha_candles[-1]["time"]):
        return False, None, None, "outside trading hours"

    if raw_candles:
        if not is_below_vwap(raw_candles):
            return False, None, None, "price above VWAP"
        if not is_trending(raw_candles):
            return False, None, None, f"ADX too low (choppy market, need >= {config.ADX_MIN_THRESHOLD})"
        if not has_enough_volatility(raw_candles):
            return False, None, None, f"ATR too low (low volatility, need >= {config.ATR_MIN_THRESHOLD})"

    candle_1 = ha_candles[-2]
    candle_2 = ha_candles[-1]

    if body_size(candle_1) < config.MIN_BODY_POINTS:
        return False, None, None, f"Candle 1 body too small ({body_size(candle_1):.2f} < {config.MIN_BODY_POINTS})"
    if body_size(candle_2) < config.MIN_BODY_POINTS:
        return False, None, None, f"Candle 2 body too small ({body_size(candle_2):.2f} < {config.MIN_BODY_POINTS})"

    if not is_red(candle_1):
        return False, None, None, "Candle 1 not red (SELL needs both red)"
    if not has_both_wicks(candle_1):
        return False, None, None, "Candle 1 missing both wicks (no indecision)"
    if not is_red(candle_2):
        return False, None, None, "Candle 2 not red"
    if not has_only_lower_wick(candle_2):
        return False, None, None, "Candle 2 missing lower wick or has upper wick"

    breakout_level = candle_2["low"]

    # SL at RAW Candle 1's high (actual market level)
    if raw_candles and len(raw_candles) >= 2:
        sl_level = raw_candles[-2]["high"]
    else:
        sl_level = candle_1["high"]

    return True, breakout_level, sl_level, None
