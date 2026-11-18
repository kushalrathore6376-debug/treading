from datetime import datetime, timezone


def convert_to_heikin_ashi(candles):
    """
    Convert regular OHLC candles to Heikin Ashi candles.

    Input:  list of dicts with keys: open, high, low, close, time, volume
    Output: list of dicts with same keys but HA-transformed values
    """
    if not candles or len(candles) < 2:
        return candles

    ha_candles = []

    for i, candle in enumerate(candles):
        ha = {"time": candle["time"], "volume": candle.get("volume", 0)}

        # HA Close = (Open + High + Low + Close) / 4
        ha["close"] = (candle["open"] + candle["high"] + candle["low"] + candle["close"]) / 4

        if i == 0:
            # First candle: HA Open = (Open + Close) / 2
            ha["open"] = (candle["open"] + candle["close"]) / 2
        else:
            # HA Open = (prev HA Open + prev HA Close) / 2
            ha["open"] = (ha_candles[i - 1]["open"] + ha_candles[i - 1]["close"]) / 2

        # HA High = max(High, HA Open, HA Close)
        ha["high"] = max(candle["high"], ha["open"], ha["close"])

        # HA Low = min(Low, HA Open, HA Close)
        ha["low"] = min(candle["low"], ha["open"], ha["close"])

        ha_candles.append(ha)

    return ha_candles


def is_green(candle):
    """Check if candle is green (bullish): close > open."""
    return candle["close"] > candle["open"]


def is_red(candle):
    """Check if candle is red (bearish): close < open."""
    return candle["close"] < candle["open"]


def body_size(candle):
    """Absolute size of the candle body."""
    return abs(candle["close"] - candle["open"])


def upper_wick(candle):
    """Size of upper wick."""
    return candle["high"] - max(candle["open"], candle["close"])


def lower_wick(candle):
    """Size of lower wick."""
    return min(candle["open"], candle["close"]) - candle["low"]


def wick_ratio(wick_size, body):
    """Wick size as a ratio of body size. Returns 0 if body is 0."""
    if body == 0:
        return 0
    return wick_size / body


def calculate_vwap(candles, session_start_hour=8):
    """
    Calculate VWAP (Volume Weighted Average Price).
    Resets at session_start_hour UTC each day.
    Returns list of VWAP values (same length as candles).
    """
    vwap_values = []
    cum_vol = 0
    cum_tp_vol = 0

    for i, c in enumerate(candles):
        # Check if we need to reset VWAP (new session)
        try:
            time_str = str(c["time"])
            if "T" in time_str:
                hour = int(time_str.split("T")[1].split(":")[0])
            else:
                dt = datetime.fromtimestamp(int(time_str), tz=timezone.utc)
                hour = dt.hour
        except (IndexError, ValueError, OSError):
            hour = -1

        if hour == session_start_hour and (i == 0 or vwap_values):
            cum_vol = 0
            cum_tp_vol = 0

        typical_price = (c["high"] + c["low"] + c["close"]) / 3
        vol = c.get("volume", 1)
        if vol == 0:
            vol = 1

        cum_vol += vol
        cum_tp_vol += typical_price * vol

        vwap_values.append(cum_tp_vol / cum_vol)

    return vwap_values


def calculate_adx(candles, period=10):
    """
    Calculate ADX (Average Directional Index).
    Returns list of ADX values (same length as candles, early values are None).
    """
    n = len(candles)
    if n < period * 2:
        return [None] * n

    # Calculate True Range, +DM, -DM
    tr_list = []
    plus_dm_list = []
    minus_dm_list = []

    for i in range(1, n):
        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_high = candles[i - 1]["high"]
        prev_low = candles[i - 1]["low"]
        prev_close = candles[i - 1]["close"]

        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_list.append(tr)

        up_move = high - prev_high
        down_move = prev_low - low

        plus_dm = up_move if (up_move > down_move and up_move > 0) else 0
        minus_dm = down_move if (down_move > up_move and down_move > 0) else 0

        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)

    # Smooth with Wilder's method
    def wilder_smooth(data, p):
        result = [None] * (p - 1)
        first = sum(data[:p])
        result.append(first)
        for i in range(p, len(data)):
            val = result[-1] - (result[-1] / p) + data[i]
            result.append(val)
        return result

    atr_smooth = wilder_smooth(tr_list, period)
    plus_dm_smooth = wilder_smooth(plus_dm_list, period)
    minus_dm_smooth = wilder_smooth(minus_dm_list, period)

    # Calculate +DI, -DI, DX
    dx_list = []
    for i in range(len(atr_smooth)):
        if atr_smooth[i] is None or atr_smooth[i] == 0:
            dx_list.append(None)
            continue
        plus_di = (plus_dm_smooth[i] / atr_smooth[i]) * 100
        minus_di = (minus_dm_smooth[i] / atr_smooth[i]) * 100
        di_sum = plus_di + minus_di
        if di_sum == 0:
            dx_list.append(0)
        else:
            dx_list.append(abs(plus_di - minus_di) / di_sum * 100)

    # Smooth DX to get ADX
    valid_dx = [x for x in dx_list if x is not None]
    if len(valid_dx) < period:
        return [None] * n

    adx_values = [None] * n
    offset = n - len(valid_dx)

    first_adx = sum(valid_dx[:period]) / period
    adx_start = offset + period - 1
    if adx_start < n:
        adx_values[adx_start] = first_adx

    for i in range(period, len(valid_dx)):
        idx = offset + i
        if idx < n:
            adx_values[idx] = (adx_values[idx - 1] * (period - 1) + valid_dx[i]) / period

    return adx_values


def calculate_atr(candles, period=10):
    """
    Calculate ATR (Average True Range).
    Returns list of ATR values (same length as candles, early values are None).
    """
    n = len(candles)
    if n < period + 1:
        return [None] * n

    tr_list = []
    for i in range(1, n):
        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_close = candles[i - 1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_list.append(tr)

    atr_values = [None] * period
    first_atr = sum(tr_list[:period]) / period
    atr_values.append(first_atr)

    for i in range(period, len(tr_list)):
        atr = (atr_values[-1] * (period - 1) + tr_list[i]) / period
        atr_values.append(atr)

    # Pad to match candle length (first candle has no TR)
    while len(atr_values) < n:
        atr_values.insert(0, None)

    return atr_values
