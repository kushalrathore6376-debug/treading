"""
Stress-test Scenario B + hybrid exit on ~1 month XAUUSD M1 (Dukascopy CSV).

Baseline   = HA 2-candle strategy (IST 2-4 blackout only), ratchet exit
ScenarioB  = + session block 22:00-06:00 IST + chase band 0.05-0.20, ratchet
Live pack  = Scenario B + ADX>=config + hybrid exit
             (lots <= 0.02 bank first profit step; 0.03+ ratchet)
"""
from __future__ import annotations

import csv
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config
from heikin_ashi import (
    convert_to_heikin_ashi,
    calculate_vwap,
    calculate_adx,
    calculate_atr,
    is_green,
    is_red,
    body_size,
)
from strategy import (
    has_both_wicks,
    has_only_upper_wick,
    has_only_lower_wick,
    _in_session_block,
)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CONTRACT = config.CONTRACT_SIZE
STEP_PRICE = config.TRAILING_TP_STEP
DAYS = 30
WARMUP = 80
IST = timezone(timedelta(hours=5, minutes=30))
CSV_GLOB = "download/xauusd-m1-bid-*.csv"
HYBRID_FP_MAX = getattr(config, "HYBRID_FIRST_PROFIT_MAX_LOTS", 0.02)


def load_candles_from_csv(path: Path):
    candles = []
    with path.open("r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ts_ms = int(float(row["timestamp"]))
            dt_utc = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
            dt_ist = dt_utc.astimezone(IST).replace(tzinfo=None)
            candles.append(
                {
                    "time": dt_ist.strftime("%Y-%m-%d %H:%M:%S"),
                    "dt": dt_ist,
                    "hour": dt_ist.hour,
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": int(float(row.get("volume") or 0) * 1000),
                }
            )
    if len(candles) < WARMUP + 100:
        raise SystemExit(f"Not enough bars in {path}: {len(candles)}")
    trade_end = candles[-1]["dt"]
    trade_start = trade_end - timedelta(days=DAYS)
    start_idx = 0
    for i, c in enumerate(candles):
        if c["dt"] >= trade_start - timedelta(hours=8):
            start_idx = max(0, i - WARMUP)
            break
    return candles[start_idx:], trade_start, trade_end, path.name


def detect_signal(i, ha, raw, vwap, adx, atr, adx_min=None):
    if i < 1:
        return None
    if vwap[i] is None or adx[i] is None or atr[i] is None:
        return None
    floor = config.ADX_MIN_THRESHOLD if adx_min is None else adx_min
    if adx[i] < floor or atr[i] < config.ATR_MIN_THRESHOLD:
        return None
    c1, c2 = ha[i - 1], ha[i]
    if body_size(c1) < config.MIN_BODY_POINTS or body_size(c2) < config.MIN_BODY_POINTS:
        return None
    close = raw[i]["close"]
    if (
        is_green(c1)
        and has_both_wicks(c1)
        and is_green(c2)
        and has_only_upper_wick(c2)
        and close > vwap[i]
    ):
        return {"direction": "BUY", "breakout": c2["high"], "sl_level": raw[i - 1]["low"]}
    if (
        is_red(c1)
        and has_both_wicks(c1)
        and is_red(c2)
        and has_only_lower_wick(c2)
        and close < vwap[i]
    ):
        return {"direction": "SELL", "breakout": c2["low"], "sl_level": raw[i - 1]["high"]}
    return None


def calc_lots(sl_distance):
    if sl_distance <= 0:
        return 0.01
    raw = config.MAX_LOSS_DOLLARS / (sl_distance * CONTRACT)
    lots = max(0.01, round(round(raw / 0.01) * 0.01, 2))
    return min(lots, 1.0)


def calc_structural_sl_tp(entry, direction, sl_level):
    sl_dist = (entry - sl_level) if direction == "BUY" else (sl_level - entry)
    if sl_dist <= 0:
        return None, None, None, "wrong_side"
    if sl_dist < config.MIN_SL_DISTANCE:
        return None, None, None, "tight"
    if sl_dist > config.MAX_SL_DISTANCE:
        return None, None, None, "wide"
    tp_dist = sl_dist * config.MIN_RR_RATIO
    if direction == "BUY":
        return round(sl_level - config.SL_BUFFER, 2), round(entry + tp_dist, 2), sl_dist, None
    return round(sl_level + config.SL_BUFFER, 2), round(entry - tp_dist, 2), sl_dist, None


def first_lock(lots, step_dollars):
    if lots >= 0.03:
        return round(step_dollars * 0.50, 2)
    if lots >= 0.02:
        return round(step_dollars * 0.33, 2)
    return 0.0


def try_enter(bar, pending, chase_min, chase_max, skip_counts):
    direction = pending["direction"]
    brk = pending["breakout"]
    sl_level = pending["sl_level"]

    if direction == "BUY":
        open_chase = bar["open"] - brk
        if open_chase > chase_max:
            skip_counts["chase_high_open"] += 1
            return None, True
        if bar["high"] < brk + chase_min:
            return None, False
        entry = round(bar["open"], 2) if chase_min <= open_chase <= chase_max else round(brk + chase_min, 2)
        chase = round(entry - brk, 4)
    else:
        open_chase = brk - bar["open"]
        if open_chase > chase_max:
            skip_counts["chase_high_open"] += 1
            return None, True
        if bar["low"] > brk - chase_min:
            return None, False
        entry = round(bar["open"], 2) if chase_min <= open_chase <= chase_max else round(brk - chase_min, 2)
        chase = round(brk - entry, 4)

    if chase > chase_max:
        skip_counts["chase_high"] += 1
        return None, True
    if chase < chase_min:
        return None, False

    sl_p, tp_p, sl_d, skip = calc_structural_sl_tp(entry, direction, sl_level)
    if skip:
        skip_counts[f"sl_{skip}"] += 1
        return None, True

    lots = calc_lots(sl_d)
    step = round(STEP_PRICE * lots * CONTRACT, 2)
    trade = {
        "direction": direction,
        "entry": entry,
        "entry_dt": bar["dt"],
        "entry_time": bar["time"],
        "sl_price": sl_p,
        "lots": lots,
        "sl_dist": sl_d,
        "chase": chase,
        "step": step,
        "current_tp": step,
        "locked": 0.0,
        "ratchets": 0,
        "max_profit": 0.0,
    }
    # same-bar SL
    if direction == "BUY" and bar["low"] <= trade["sl_price"]:
        pnl = (trade["sl_price"] - entry) * lots * CONTRACT
        trade.update(exit=trade["sl_price"], pnl=round(pnl, 2), exit_time=bar["time"],
                     reason="STOP_LOSS", hold_m=0.0)
        return trade, True
    if direction == "SELL" and bar["high"] >= trade["sl_price"]:
        pnl = (entry - trade["sl_price"]) * lots * CONTRACT
        trade.update(exit=trade["sl_price"], pnl=round(pnl, 2), exit_time=bar["time"],
                     reason="STOP_LOSS", hold_m=0.0)
        return trade, True
    return trade, True


def run(
    candles,
    ha,
    vwap,
    adx,
    atr,
    trade_start,
    use_scenario_b=True,
    exit_mode="ratchet",
    adx_min=None,
):
    """
    exit_mode:
      ratchet — always trail
      hybrid  — lots <= HYBRID_FP_MAX bank first profit step; else ratchet
    """
    trades = []
    pending = None
    trade = None
    balance = 10000.0
    peak = balance
    max_dd = 0.0
    skip_counts = Counter()
    chase_min = config.CHASE_MIN if use_scenario_b else 0.0
    chase_max = config.CHASE_MAX if use_scenario_b else 1e9
    adx_floor = config.ADX_MIN_THRESHOLD if adx_min is None else adx_min

    def update_dd():
        nonlocal peak, max_dd
        if balance > peak:
            peak = balance
        dd = (peak - balance) / peak * 100 if peak else 0
        if dd > max_dd:
            max_dd = dd

    def close_trade(t, exit_price, reason, bar):
        nonlocal balance, trade, pending
        lots = t["lots"]
        if t["direction"] == "BUY":
            pnl = (exit_price - t["entry"]) * lots * CONTRACT
        else:
            pnl = (t["entry"] - exit_price) * lots * CONTRACT
        t.update(
            exit=exit_price,
            pnl=round(pnl, 2),
            exit_time=bar["time"],
            reason=reason,
            hold_m=round((bar["dt"] - t["entry_dt"]).total_seconds() / 60, 1),
        )
        balance += pnl
        trades.append(t)
        trade = None
        pending = None
        update_dd()

    for i in range(WARMUP, len(candles)):
        bar = candles[i]
        in_window = bar["dt"] >= trade_start

        if trade is not None:
            lots = trade["lots"]
            step = trade["step"]
            ppd = 1.0 / (lots * CONTRACT)
            use_fp = exit_mode == "hybrid" and lots <= HYBRID_FP_MAX

            if trade["direction"] == "BUY":
                max_p = (bar["high"] - trade["entry"]) * lots * CONTRACT
            else:
                max_p = (trade["entry"] - bar["low"]) * lots * CONTRACT
            trade["max_profit"] = max(trade.get("max_profit", 0), round(max_p, 2))

            # Structural / trail SL first
            sl_hit = (
                (trade["direction"] == "BUY" and bar["low"] <= trade["sl_price"])
                or (trade["direction"] == "SELL" and bar["high"] >= trade["sl_price"])
            )
            if sl_hit:
                close_trade(
                    trade,
                    trade["sl_price"],
                    "TRAIL_SL" if trade["locked"] > 0 else "STOP_LOSS",
                    bar,
                )
                continue

            # Hybrid small size: bank first profit step
            if use_fp:
                if max_p >= step:
                    if trade["direction"] == "BUY":
                        exit_price = round(trade["entry"] + step * ppd, 2)
                    else:
                        exit_price = round(trade["entry"] - step * ppd, 2)
                    close_trade(trade, exit_price, "FIRST_PROFIT", bar)
                continue

            # Ratchet path
            while max_p >= trade["current_tp"]:
                locked = max(0.0, trade["current_tp"] - step)
                if locked == 0:
                    locked = first_lock(lots, step)
                trade["locked"] = locked
                trade["current_tp"] += step
                trade["ratchets"] += 1
                if trade["direction"] == "BUY":
                    trade["sl_price"] = round(trade["entry"] + locked * ppd, 2)
                else:
                    trade["sl_price"] = round(trade["entry"] - locked * ppd, 2)

            if trade["locked"] > 0:
                hit = (
                    (trade["direction"] == "BUY" and bar["low"] <= trade["sl_price"])
                    or (trade["direction"] == "SELL" and bar["high"] >= trade["sl_price"])
                )
                if hit:
                    close_trade(trade, trade["sl_price"], "TRAIL_SL", bar)
                    continue

        if trade is not None or not in_window:
            continue

        local_hour = bar["hour"]
        if config.NO_TRADE_START_IST <= local_hour < config.NO_TRADE_END_IST:
            pending = None
            skip_counts["ist_blackout"] += 1
            continue
        if use_scenario_b and _in_session_block(local_hour):
            pending = None
            skip_counts["session_block"] += 1
            continue

        if pending is not None:
            new_trade, done = try_enter(bar, pending, chase_min, chase_max, skip_counts)
            if done:
                pending = None
            if new_trade is not None:
                if "pnl" in new_trade:  # same-bar exit
                    balance += new_trade["pnl"]
                    trades.append(new_trade)
                    update_dd()
                else:
                    trade = new_trade
                continue

        sig = detect_signal(i, ha, candles, vwap, adx, atr, adx_min=adx_floor)
        if sig:
            pending = sig

    return {"trades": trades, "balance": balance, "max_dd": max_dd, "skips": skip_counts}


def summarize(name, result):
    trades = result["trades"]
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    total = sum(t["pnl"] for t in trades)
    print()
    print("=" * 64)
    print(f" {name}")
    print("=" * 64)
    print(f" Trades:     {len(trades)}")
    wr = (len(wins) / len(trades) * 100) if trades else 0
    print(f" Wins/Loss:  {len(wins)}/{len(losses)}  ({wr:.0f}% win)")
    print(f" Net P&L:    ${total:+.2f}")
    print(f" Gross win:  ${sum(t['pnl'] for t in wins):+.2f}")
    print(f" Gross loss: ${sum(t['pnl'] for t in losses):+.2f}")
    if trades:
        print(f" Avg trade:  ${total/len(trades):+.2f}")
    if wins:
        print(f" Avg win:    ${sum(t['pnl'] for t in wins)/len(wins):+.2f}")
    if losses:
        print(f" Avg loss:   ${sum(t['pnl'] for t in losses)/len(losses):+.2f}")
    print(f" Max DD:     {result['max_dd']:.2f}%")
    print(f" End bal:    ${result['balance']:.2f} (start $10000)")
    print(f" Exits:      {dict(Counter(t['reason'] for t in trades))}")
    if result["skips"]:
        top = result["skips"].most_common(8)
        print(f" Skip tallies: {dict(top)}")
    by_month = Counter()
    pnl_month = Counter()
    for t in trades:
        m = t["entry_time"][:7]
        by_month[m] += 1
        pnl_month[m] += t["pnl"]
    if by_month:
        print(" By month:")
        for m in sorted(by_month):
            print(f"   {m}: n={by_month[m]}  pnl=${pnl_month[m]:+.2f}")
    if trades:
        best = max(trades, key=lambda t: t["pnl"])
        worst = min(trades, key=lambda t: t["pnl"])
        print(
            f" Best:  {best['direction']} {best['entry_time']} "
            f"chase={best.get('chase')} pnl=${best['pnl']:+.2f} ({best['reason']})"
        )
        print(
            f" Worst: {worst['direction']} {worst['entry_time']} "
            f"chase={worst.get('chase')} pnl=${worst['pnl']:+.2f} ({worst['reason']})"
        )
        print()
        print(f" {'#':>3} {'When':<17} {'Dir':<5} {'Entry':>8} {'Chase':>6} "
              f"{'Lots':>5} {'PnL':>8} {'R#':>3} {'Reason'}")
        for i, t in enumerate(trades, 1):
            print(
                f" {i:3d} {t['entry_time'][5:16]:<17} {t['direction']:<5} "
                f"{t['entry']:8.2f} {t.get('chase', 0):6.2f} {t['lots']:5.2f} "
                f"{t['pnl']:+8.2f} {t.get('ratchets', 0):3d} {t['reason']}"
            )
    return total


def main():
    root = Path(__file__).resolve().parent
    files = sorted(root.glob(CSV_GLOB))
    if not files:
        raise SystemExit(f"No CSV at {CSV_GLOB}")
    csv_path = files[-1]
    print(f"Loading Gold M1 from {csv_path} ...")
    candles, trade_start, trade_end, src = load_candles_from_csv(csv_path)
    print(
        f"Loaded {len(candles)} M1 bars | trade window (IST) "
        f"{trade_start} -> {trade_end} | source={src}"
    )
    print("Precomputing HA / VWAP / ADX / ATR ...")
    ha = convert_to_heikin_ashi(candles)
    vwap = calculate_vwap(candles, config.VWAP_SESSION_START_HOUR_UTC)
    adx = calculate_adx(candles, config.ADX_PERIOD)
    atr = calculate_atr(candles, config.ATR_PERIOD)

    live_adx = config.ADX_MIN_THRESHOLD
    hybrid_on = getattr(config, "USE_HYBRID_EXIT", False)
    print(
        f"Live config: session {config.SESSION_BLOCK_START}-{config.SESSION_BLOCK_END} IST, "
        f"chase {config.CHASE_MIN}-{config.CHASE_MAX} | "
        f"ADX>={live_adx} | hybrid={hybrid_on} (FP lots<={HYBRID_FP_MAX}) | "
        f"SL[{config.MIN_SL_DISTANCE},{config.MAX_SL_DISTANCE}]"
    )

    # Compare vs older ADX20 ratchet for context
    print("Running Scenario B (ADX>=20, ratchet) ...")
    sb = run(
        candles, ha, vwap, adx, atr, trade_start,
        use_scenario_b=True, exit_mode="ratchet", adx_min=20,
    )
    sb_pnl = summarize("SCENARIO B only (ADX>=20 + ratchet)", sb)

    print("Running LIVE pack (B + ADX config + hybrid) ...")
    live_exit = "hybrid" if hybrid_on else "ratchet"
    live = run(
        candles, ha, vwap, adx, atr, trade_start,
        use_scenario_b=True, exit_mode=live_exit, adx_min=live_adx,
    )
    live_pnl = summarize(
        f"LIVE PACK (B + ADX>={live_adx} + {live_exit})", live
    )

    # Lot split for hybrid clarity
    live_trades = live["trades"]
    small = [t for t in live_trades if t["lots"] <= HYBRID_FP_MAX]
    big = [t for t in live_trades if t["lots"] > HYBRID_FP_MAX]
    fp_exits = sum(1 for t in live_trades if t["reason"] == "FIRST_PROFIT")
    trail_exits = sum(1 for t in live_trades if t["reason"] == "TRAIL_SL")
    sl_exits = sum(1 for t in live_trades if t["reason"] == "STOP_LOSS")

    print()
    print("=" * 64)
    print(" COMPARISON")
    print("=" * 64)
    print(f" Scenario B (ADX20 ratchet): ${sb_pnl:+.2f}  on {len(sb['trades'])} trades")
    print(f" Live pack:                  ${live_pnl:+.2f}  on {len(live_trades)} trades")
    print(f" Improvement vs B:           ${live_pnl - sb_pnl:+.2f}")
    print(
        f" Hybrid split: small(<={HYBRID_FP_MAX}) n={len(small)} "
        f"pnl=${sum(t['pnl'] for t in small):+.2f} | "
        f"big n={len(big)} pnl=${sum(t['pnl'] for t in big):+.2f}"
    )
    print(
        f" Exits: FIRST_PROFIT={fp_exits} TRAIL_SL={trail_exits} STOP_LOSS={sl_exits}"
    )
    print()
    print("Notes:")
    print(" - Data: Dukascopy XAUUSD M1 bid (~1 month). MT5 offline here.")
    print(" - Hybrid: lots<=0.02 close at first step (~$6); 0.03+ ratchet.")
    print(" - Live bot now matches this pack (config ADX + USE_HYBRID_EXIT).")


if __name__ == "__main__":
    main()
