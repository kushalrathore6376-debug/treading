"""
Side-by-side backtest: V1 (current) vs V2 (Option 1 + Option 4).

Pulls real M1 XAUUSD bars from MT5 and runs both strategies on the SAME dataset.
Indicators (HA, VWAP, ADX, ATR) are pre-computed once for speed.

V1: 2-candle HA pattern + breakout of HA Candle-2 high/low.
V2: 1-candle HA indecision + breakout of RAW indecision high/low.

Both share identical exit logic (structural SL, ratchet TP, R:R 2.0).
"""
import sys
import os
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import MetaTrader5 as mt5
import config
from heikin_ashi import (
    convert_to_heikin_ashi, is_green, is_red, body_size,
    calculate_vwap, calculate_adx, calculate_atr,
)
from strategy import has_both_wicks, has_only_upper_wick, has_only_lower_wick

PENDING_TTL_BARS = 3
WARMUP = 60  # skip first N bars so ADX/ATR/VWAP have data


def fetch_real_candles(instrument, timeframe, n_bars):
    if not mt5.initialize():
        return None
    if not mt5.login(login=config.MT5_LOGIN, password=config.MT5_PASSWORD, server=config.MT5_SERVER):
        mt5.shutdown()
        return None
    rates = mt5.copy_rates_from_pos(instrument, timeframe, 0, n_bars)
    mt5.shutdown()
    if rates is None or len(rates) == 0:
        return None
    candles = []
    for r in rates:
        ts = int(r["time"])
        candles.append({
            "time": datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "volume": int(r["tick_volume"]),
        })
    return candles


def in_blackout_ist(time_str):
    """2 AM - 4 AM IST blackout."""
    try:
        dt = datetime.strptime(time_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    dt_ist = dt + timedelta(hours=5, minutes=30)
    return config.NO_TRADE_START_IST <= dt_ist.hour < config.NO_TRADE_END_IST


def detect_v1(i, ha, raw, vwap, adx, atr):
    """V1: 2-candle pattern, HA breakout level. Needs ha[i-1] (C1) and ha[i] (C2) both closed."""
    if i < 1:
        return None
    if in_blackout_ist(ha[i]["time"]):
        return None
    # Filters
    if vwap[i] is None or adx[i] is None or atr[i] is None:
        return None
    if adx[i] < config.ADX_MIN_THRESHOLD or atr[i] < config.ATR_MIN_THRESHOLD:
        return None

    c1 = ha[i - 1]
    c2 = ha[i]

    # body size
    if body_size(c1) < config.MIN_BODY_POINTS or body_size(c2) < config.MIN_BODY_POINTS:
        return None

    close = raw[i]["close"]

    # BUY: green C1 with both wicks, green C2 with only upper wick
    if (is_green(c1) and has_both_wicks(c1) and
            is_green(c2) and has_only_upper_wick(c2) and
            close > vwap[i]):
        return {"direction": "BUY", "breakout_level": c2["high"], "sl_level": raw[i - 1]["low"]}

    # SELL: red C1 with both wicks, red C2 with only lower wick
    if (is_red(c1) and has_both_wicks(c1) and
            is_red(c2) and has_only_lower_wick(c2) and
            close < vwap[i]):
        return {"direction": "SELL", "breakout_level": c2["low"], "sl_level": raw[i - 1]["high"]}

    return None


def detect_v2(i, ha, raw, vwap, adx, atr):
    """V2: single indecision bar, RAW breakout level."""
    if i < 0:
        return None
    if in_blackout_ist(ha[i]["time"]):
        return None
    if vwap[i] is None or adx[i] is None or atr[i] is None:
        return None
    if adx[i] < config.ADX_MIN_THRESHOLD or atr[i] < config.ATR_MIN_THRESHOLD:
        return None

    bar = ha[i]
    raw_bar = raw[i]
    if body_size(bar) < config.MIN_BODY_POINTS:
        return None

    close = raw[i]["close"]

    # BUY: green indecision with both wicks, above VWAP -> breakout above raw high
    if is_green(bar) and has_both_wicks(bar) and close > vwap[i]:
        return {"direction": "BUY", "breakout_level": raw_bar["high"], "sl_level": raw_bar["low"]}

    # SELL: red indecision with both wicks, below VWAP -> breakdown below raw low
    if is_red(bar) and has_both_wicks(bar) and close < vwap[i]:
        return {"direction": "SELL", "breakout_level": raw_bar["low"], "sl_level": raw_bar["high"]}

    return None


def calc_structural_sl_tp(entry, direction, sl_level):
    if direction == "BUY":
        sl_dist = entry - sl_level
    else:
        sl_dist = sl_level - entry
    if sl_dist <= 0:
        return None, None, None, "wrong side"
    if sl_dist < config.MIN_SL_DISTANCE:
        return None, None, None, f"tight"
    if sl_dist > config.MAX_SL_DISTANCE:
        return None, None, None, f"wide"
    tp_dist = sl_dist * config.MIN_RR_RATIO
    if direction == "BUY":
        return round(sl_level - config.SL_BUFFER, 2), round(entry + tp_dist, 2), sl_dist, None
    return round(sl_level + config.SL_BUFFER, 2), round(entry - tp_dist, 2), sl_dist, None


def run_backtest(candles, ha, vwap, adx, atr, detect_fn):
    trades = []
    in_trade = False
    trade = None
    initial = 100.0
    balance = initial
    peak = initial
    max_dd = 0
    pending = None
    contract_size = config.CONTRACT_SIZE
    step = config.TRAILING_TP_STEP
    LOTS = 0.01

    def update_dd():
        nonlocal peak, max_dd
        if balance > peak:
            peak = balance
        dd = (peak - balance) / peak * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    for i in range(WARMUP, len(candles)):
        bar = candles[i]

        if in_trade:
            ppd = 1.0 / (LOTS * contract_size)
            sl_hit = False
            exit_price = None

            if trade["direction"] == "BUY" and bar["low"] <= trade["sl_price"]:
                exit_price = trade["sl_price"]
                sl_hit = True
            elif trade["direction"] == "SELL" and bar["high"] >= trade["sl_price"]:
                exit_price = trade["sl_price"]
                sl_hit = True

            if sl_hit:
                if trade["direction"] == "BUY":
                    pnl = (exit_price - trade["entry_price"]) * LOTS * contract_size
                else:
                    pnl = (trade["entry_price"] - exit_price) * LOTS * contract_size
                trade["exit_price"] = exit_price
                trade["pnl"] = round(pnl, 2)
                trade["exit_reason"] = "TRAIL_SL" if trade["locked"] > 0 else "STOP_LOSS"
                trade["exit_time"] = bar["time"]
                balance += pnl
                trades.append(trade)
                in_trade = False
                trade = None
                pending = None
                update_dd()
                continue

            # Ratchet
            if trade["direction"] == "BUY":
                max_p = (bar["high"] - trade["entry_price"]) * LOTS * contract_size
            else:
                max_p = (trade["entry_price"] - bar["low"]) * LOTS * contract_size

            while max_p >= trade["current_tp"]:
                locked = max(0.0, trade["current_tp"] - step)
                trade["locked"] = locked
                trade["current_tp"] += step
                if trade["direction"] == "BUY":
                    trade["sl_price"] = round(trade["entry_price"] + locked * ppd, 2)
                else:
                    trade["sl_price"] = round(trade["entry_price"] - locked * ppd, 2)

            if trade["locked"] > 0:
                hit = False
                if trade["direction"] == "BUY" and bar["low"] <= trade["sl_price"]:
                    exit_price = trade["sl_price"]
                    hit = True
                elif trade["direction"] == "SELL" and bar["high"] >= trade["sl_price"]:
                    exit_price = trade["sl_price"]
                    hit = True
                if hit:
                    if trade["direction"] == "BUY":
                        pnl = (exit_price - trade["entry_price"]) * LOTS * contract_size
                    else:
                        pnl = (trade["entry_price"] - exit_price) * LOTS * contract_size
                    trade["exit_price"] = exit_price
                    trade["pnl"] = round(pnl, 2)
                    trade["exit_reason"] = "TRAIL_SL"
                    trade["exit_time"] = bar["time"]
                    balance += pnl
                    trades.append(trade)
                    in_trade = False
                    trade = None
                    pending = None
                    update_dd()
            continue

        # Pending breakout trigger
        if pending is not None:
            pending["age"] += 1
            triggered = False
            if pending["direction"] == "BUY" and bar["high"] > pending["breakout_level"]:
                entry = pending["breakout_level"]
                sl_p, tp_p, sl_d, skip = calc_structural_sl_tp(entry, "BUY", pending["sl_level"])
                if skip is None:
                    trade = {
                        "direction": "BUY",
                        "entry_price": entry,
                        "sl_price": sl_p,
                        "tp_price": tp_p,
                        "entry_time": bar["time"],
                        "current_tp": step,
                        "locked": 0.0,
                        "sl_dist": round(sl_d, 2),
                    }
                    in_trade = True
                triggered = True
            elif pending["direction"] == "SELL" and bar["low"] < pending["breakout_level"]:
                entry = pending["breakout_level"]
                sl_p, tp_p, sl_d, skip = calc_structural_sl_tp(entry, "SELL", pending["sl_level"])
                if skip is None:
                    trade = {
                        "direction": "SELL",
                        "entry_price": entry,
                        "sl_price": sl_p,
                        "tp_price": tp_p,
                        "entry_time": bar["time"],
                        "current_tp": step,
                        "locked": 0.0,
                        "sl_dist": round(sl_d, 2),
                    }
                    in_trade = True
                triggered = True

            if triggered:
                pending = None
                if in_trade:
                    # same-bar SL check
                    sl_hit = False
                    if trade["direction"] == "BUY" and bar["low"] <= trade["sl_price"]:
                        exit_price = trade["sl_price"]
                        sl_hit = True
                    elif trade["direction"] == "SELL" and bar["high"] >= trade["sl_price"]:
                        exit_price = trade["sl_price"]
                        sl_hit = True
                    if sl_hit:
                        if trade["direction"] == "BUY":
                            pnl = (exit_price - trade["entry_price"]) * LOTS * contract_size
                        else:
                            pnl = (trade["entry_price"] - exit_price) * LOTS * contract_size
                        trade["exit_price"] = exit_price
                        trade["pnl"] = round(pnl, 2)
                        trade["exit_reason"] = "STOP_LOSS"
                        trade["exit_time"] = bar["time"]
                        balance += pnl
                        trades.append(trade)
                        in_trade = False
                        trade = None
                        update_dd()
                continue
            elif pending["age"] > PENDING_TTL_BARS:
                pending = None

        # New pattern
        if not in_trade and pending is None:
            sig = detect_fn(i, ha, candles, vwap, adx, atr)
            if sig:
                pending = {**sig, "age": 0}

    return trades, balance, max_dd, initial


def summarize(trades, balance, max_dd, initial, label):
    print()
    print("=" * 78, flush=True)
    print(f"  {label}", flush=True)
    print("=" * 78, flush=True)
    if not trades:
        print("  No trades.", flush=True)
        return None
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    total = sum(t["pnl"] for t in trades)
    aw = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
    al = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
    pf = (sum(t["pnl"] for t in wins) / abs(sum(t["pnl"] for t in losses))) if losses and sum(t["pnl"] for t in losses) < 0 else 0
    sl = sum(1 for t in trades if t.get("exit_reason") == "STOP_LOSS")
    tr = sum(1 for t in trades if t.get("exit_reason") == "TRAIL_SL")
    bt = max(t["pnl"] for t in trades)
    wt = min(t["pnl"] for t in trades)

    print(f"  Trades:           {len(trades)}", flush=True)
    print(f"  Wins / Losses:    {len(wins)} / {len(losses)}", flush=True)
    print(f"  Win rate:         {len(wins)/len(trades)*100:.1f}%", flush=True)
    print(f"  Avg win / loss:   ${aw:.2f} / ${al:.2f}", flush=True)
    print(f"  Best / Worst:     ${bt:.2f} / ${wt:.2f}", flush=True)
    print(f"  Total P&L:        ${total:.2f}", flush=True)
    print(f"  Final balance:    ${balance:.2f}  (start ${initial:.2f}, return {(balance-initial)/initial*100:+.2f}%)", flush=True)
    print(f"  Max drawdown:     {max_dd:.2f}%", flush=True)
    print(f"  Profit factor:    {pf:.2f}", flush=True)
    print(f"  Exit mix:         StopLoss: {sl}  Trail: {tr}", flush=True)
    if al != 0:
        print(f"  Realized R:R:     1:{abs(aw/al):.2f}", flush=True)
    return {
        "trades": len(trades), "wins": len(wins), "losses": len(losses),
        "wr": len(wins)/len(trades)*100, "total": total, "max_dd": max_dd,
        "pf": pf, "balance": balance, "best": bt, "worst": wt,
    }


def main():
    n_bars = 10000  # ~7 days of M1
    print(f"Fetching {n_bars} M1 bars of {config.INSTRUMENT} from MT5...", flush=True)
    candles = fetch_real_candles(config.INSTRUMENT, mt5.TIMEFRAME_M1, n_bars)
    if candles is None:
        print("MT5 fetch failed; aborting.", flush=True)
        sys.exit(1)
    print(f"Got {len(candles)} bars: {candles[0]['time']}  ->  {candles[-1]['time']}", flush=True)

    print("Pre-computing indicators (HA, VWAP, ADX, ATR)...", flush=True)
    ha = convert_to_heikin_ashi(candles)
    vwap = calculate_vwap(candles, config.VWAP_SESSION_START_HOUR_UTC)
    adx = calculate_adx(candles, config.ADX_PERIOD)
    atr = calculate_atr(candles, config.ATR_PERIOD)
    print("Done.", flush=True)

    print("\nRunning V1 (current)...", flush=True)
    t1, b1, dd1, init1 = run_backtest(candles, ha, vwap, adx, atr, detect_v1)
    s1 = summarize(t1, b1, dd1, init1, "V1 — current 2-candle HA, HA breakout level")

    print("\nRunning V2 (Option 1 + Option 4)...", flush=True)
    t2, b2, dd2, init2 = run_backtest(candles, ha, vwap, adx, atr, detect_v2)
    s2 = summarize(t2, b2, dd2, init2, "V2 — single indecision bar, RAW breakout level")

    print()
    print("=" * 78, flush=True)
    print("  COMPARISON", flush=True)
    print("=" * 78, flush=True)
    if s1 and s2:
        print(f"  {'metric':<22} {'V1':>14} {'V2':>14} {'delta':>14}", flush=True)
        def row(name, a, b, fmt="{:.2f}"):
            d = b - a
            print(f"  {name:<22} {fmt.format(a):>14} {fmt.format(b):>14} {fmt.format(d):>14}", flush=True)
        row("trades", s1["trades"], s2["trades"], "{:d}")
        row("win rate %", s1["wr"], s2["wr"])
        row("net P&L $", s1["total"], s2["total"])
        row("profit factor", s1["pf"], s2["pf"])
        row("max DD %", s1["max_dd"], s2["max_dd"])
        row("best trade $", s1["best"], s2["best"])
        row("worst trade $", s1["worst"], s2["worst"])


if __name__ == "__main__":
    main()
