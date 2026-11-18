"""
Backtest v7
2-candle HA pattern + trailing step TP with ratcheting SL.
Matches live bot logic exactly.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from heikin_ashi import convert_to_heikin_ashi
from strategy import detect_buy_pattern, detect_sell_pattern


def run_backtest(candles, timeframe_label):
    ha_candles = convert_to_heikin_ashi(candles)

    trades = []
    in_trade = False
    trade = None
    initial_balance = 100000.0
    balance = initial_balance
    peak_balance = initial_balance
    max_drawdown = 0

    pending_breakout = None
    contract_size = config.CONTRACT_SIZE
    step = config.TRAILING_TP_STEP

    # Fixed lot size
    def calc_lots(bal, price):
        return 0.01

    # Structural SL/TP calculation
    def calc_structural_sl_tp(entry, direction, sl_level):
        if direction == "BUY":
            sl_dist = entry - sl_level
        else:
            sl_dist = sl_level - entry

        if sl_dist <= 0:
            return None, None, None, "SL on wrong side"
        if sl_dist < config.MIN_SL_DISTANCE:
            return None, None, None, f"SL too tight ({sl_dist:.2f})"
        if sl_dist > config.MAX_SL_DISTANCE:
            return None, None, None, f"SL too wide ({sl_dist:.2f})"

        tp_dist = sl_dist * config.MIN_RR_RATIO

        if direction == "BUY":
            sl_price = round(sl_level - config.SL_BUFFER, 2)
            tp_price = round(entry + tp_dist, 2)
        else:
            sl_price = round(sl_level + config.SL_BUFFER, 2)
            tp_price = round(entry - tp_dist, 2)

        return sl_price, tp_price, sl_dist, None

    def update_drawdown():
        nonlocal peak_balance, max_drawdown
        if balance > peak_balance:
            peak_balance = balance
        dd = (peak_balance - balance) / peak_balance * 100
        if dd > max_drawdown:
            max_drawdown = dd

    for i in range(2, len(ha_candles)):
        window_ha = ha_candles[:i + 1]
        window_raw = candles[:i + 1]
        latest_raw = candles[i]

        # --- MANAGE OPEN TRADE ---
        if in_trade:
            ppd = 1.0 / (trade["lots"] * contract_size)

            # Check if original SL is hit first
            sl_hit = False
            if trade["direction"] == "BUY":
                if latest_raw["low"] <= trade["sl_price"]:
                    trade["exit_price"] = trade["sl_price"]
                    sl_hit = True
            else:
                if latest_raw["high"] >= trade["sl_price"]:
                    trade["exit_price"] = trade["sl_price"]
                    sl_hit = True

            if sl_hit:
                trade["exit_time"] = latest_raw["time"]
                if trade["direction"] == "BUY":
                    pnl = (trade["exit_price"] - trade["entry_price"]) * trade["lots"] * contract_size
                else:
                    pnl = (trade["entry_price"] - trade["exit_price"]) * trade["lots"] * contract_size
                trade["pnl"] = round(pnl, 2)
                trade["exit_reason"] = "STOP LOSS" if trade["locked_sl_dollars"] == 0 else "TRAILING SL"
                balance += pnl
                trade["balance_after"] = round(balance, 2)
                trades.append(trade)
                in_trade = False
                trade = None
                pending_breakout = None
                update_drawdown()
                continue

            # Check if TP ratchet levels are reached
            if trade["direction"] == "BUY":
                max_profit = (latest_raw["high"] - trade["entry_price"]) * trade["lots"] * contract_size
            else:
                max_profit = (trade["entry_price"] - latest_raw["low"]) * trade["lots"] * contract_size

            while max_profit >= trade["current_tp_dollars"]:
                locked = trade["current_tp_dollars"] - step
                if locked < 0:
                    locked = 0
                trade["locked_sl_dollars"] = locked
                trade["current_tp_dollars"] += step

                # Update SL/TP prices
                if trade["direction"] == "BUY":
                    trade["sl_price"] = round(trade["entry_price"] + locked * ppd, 2)
                    trade["tp_price"] = round(trade["entry_price"] + trade["current_tp_dollars"] * ppd, 2)
                else:
                    trade["sl_price"] = round(trade["entry_price"] - locked * ppd, 2)
                    trade["tp_price"] = round(trade["entry_price"] - trade["current_tp_dollars"] * ppd, 2)

            # After ratcheting, check if new (higher) SL is hit
            if trade["locked_sl_dollars"] > 0:
                trailing_sl_hit = False
                if trade["direction"] == "BUY":
                    if latest_raw["low"] <= trade["sl_price"]:
                        trade["exit_price"] = trade["sl_price"]
                        trailing_sl_hit = True
                else:
                    if latest_raw["high"] >= trade["sl_price"]:
                        trade["exit_price"] = trade["sl_price"]
                        trailing_sl_hit = True

                if trailing_sl_hit:
                    trade["exit_time"] = latest_raw["time"]
                    if trade["direction"] == "BUY":
                        pnl = (trade["exit_price"] - trade["entry_price"]) * trade["lots"] * contract_size
                    else:
                        pnl = (trade["entry_price"] - trade["exit_price"]) * trade["lots"] * contract_size
                    trade["pnl"] = round(pnl, 2)
                    trade["exit_reason"] = "TRAILING SL"
                    balance += pnl
                    trade["balance_after"] = round(balance, 2)
                    trades.append(trade)
                    in_trade = False
                    trade = None
                    pending_breakout = None
                    update_drawdown()

            continue  # Still in trade or just closed

        # --- CHECK FOR BREAKOUT ON PENDING PATTERN ---
        if pending_breakout is not None:
            direction = pending_breakout["direction"]
            breakout_level = pending_breakout["breakout_level"]

            entered = False
            if direction == "BUY" and latest_raw["high"] > breakout_level:
                entry_price = breakout_level
                sl_level = pending_breakout["sl_level"]

                sl_price, tp_price, sl_dist, skip_reason = calc_structural_sl_tp(
                    entry_price, "BUY", sl_level
                )
                if skip_reason:
                    pending_breakout = None
                    continue

                lots = calc_lots(balance, entry_price)
                trade = {
                    "direction": "BUY",
                    "entry_price": entry_price,
                    "sl_price": sl_price,
                    "tp_price": tp_price,
                    "lots": lots,
                    "entry_time": latest_raw["time"],
                    "current_tp_dollars": config.TRAILING_TP_STEP,
                    "locked_sl_dollars": 0,
                }
                in_trade = True
                entered = True

            elif direction == "SELL" and latest_raw["low"] < breakout_level:
                entry_price = breakout_level
                sl_level = pending_breakout["sl_level"]

                sl_price, tp_price, sl_dist, skip_reason = calc_structural_sl_tp(
                    entry_price, "SELL", sl_level
                )
                if skip_reason:
                    pending_breakout = None
                    continue

                lots = calc_lots(balance, entry_price)
                trade = {
                    "direction": "SELL",
                    "entry_price": entry_price,
                    "sl_price": sl_price,
                    "tp_price": tp_price,
                    "lots": lots,
                    "entry_time": latest_raw["time"],
                    "current_tp_dollars": config.TRAILING_TP_STEP,
                    "locked_sl_dollars": 0,
                }
                in_trade = True
                entered = True

            if entered:
                # Check if SL hit in same candle
                sl_hit = False
                if trade["direction"] == "BUY":
                    if latest_raw["low"] <= trade["sl_price"]:
                        trade["exit_price"] = trade["sl_price"]
                        sl_hit = True
                else:
                    if latest_raw["high"] >= trade["sl_price"]:
                        trade["exit_price"] = trade["sl_price"]
                        sl_hit = True

                if sl_hit:
                    trade["exit_reason"] = "STOP LOSS"
                    trade["exit_time"] = latest_raw["time"]
                    if trade["direction"] == "BUY":
                        pnl = (trade["exit_price"] - trade["entry_price"]) * trade["lots"] * contract_size
                    else:
                        pnl = (trade["entry_price"] - trade["exit_price"]) * trade["lots"] * contract_size
                    trade["pnl"] = round(pnl, 2)
                    balance += pnl
                    trade["balance_after"] = round(balance, 2)
                    trades.append(trade)
                    in_trade = False
                    trade = None
                    update_drawdown()

                pending_breakout = None
                continue

        # --- CHECK 2-CANDLE PATTERN ---
        if not in_trade:
            buy_pattern, breakout_level, sl_level, _ = detect_buy_pattern(window_ha, window_raw)
            if buy_pattern:
                pending_breakout = {
                    "direction": "BUY",
                    "breakout_level": breakout_level,
                    "sl_level": sl_level,
                }
                continue

            sell_pattern, breakout_level, sl_level, _ = detect_sell_pattern(window_ha, window_raw)
            if sell_pattern:
                pending_breakout = {
                    "direction": "SELL",
                    "breakout_level": breakout_level,
                    "sl_level": sl_level,
                }
                continue

            pending_breakout = None

    # --- REPORT ---
    print(f"\n{'=' * 70}")
    print(f"  BACKTEST v6 -- {timeframe_label}")
    print(f"{'=' * 70}")
    print(f"  Strategy: 2-candle HA pattern + trailing step TP")
    print(f"  Filters:  VWAP | ADX | ATR")
    print(f"  SL: structural (min {config.MIN_SL_DISTANCE}, max {config.MAX_SL_DISTANCE}) | R:R min 1:{config.MIN_RR_RATIO} | Step: ${config.TRAILING_TP_STEP}")
    print(f"  Period:          {ha_candles[0]['time'][:10]} to {ha_candles[-1]['time'][:10]}")
    print(f"  Total candles:   {len(ha_candles)}")
    print(f"  Total trades:    {len(trades)}")

    if trades:
        wins = [t for t in trades if t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] <= 0]
        total_pnl = sum(t["pnl"] for t in trades)
        avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0

        sl_exits = len([t for t in trades if t.get("exit_reason", "") == "STOP LOSS"])
        trail_exits = len([t for t in trades if t.get("exit_reason", "") == "TRAILING SL"])

        max_single_loss = min(t["pnl"] for t in trades)

        print(f"  Wins:            {len(wins)}")
        print(f"  Losses:          {len(losses)}")
        print(f"  Win rate:        {len(wins) / len(trades) * 100:.1f}%")
        print(f"  Avg win:         ${avg_win:,.2f}")
        print(f"  Avg loss:        ${avg_loss:,.2f}")
        print(f"  Max single loss: ${max_single_loss:,.2f}")
        print(f"  Total P&L:       ${total_pnl:,.2f}")
        print(f"  Final balance:   ${balance:,.2f}")
        print(f"  Return:          {(balance - initial_balance) / initial_balance * 100:.2f}%")
        print(f"  Max drawdown:    {max_drawdown:.2f}%")
        print(f"  Exit types:      SL: {sl_exits} | Trailing SL: {trail_exits}")

        if avg_loss != 0:
            rr = abs(avg_win / avg_loss)
            print(f"  Risk/Reward:     1:{rr:.2f}")

        print(f"\n  {'─' * 76}")
        print(f"  {'#':>3} {'Dir':<5} {'Entry':>10} {'Exit':>10} {'SL':>10} {'TP':>10} {'Lots':>6} {'P&L':>10} {'Reason'}")
        print(f"  {'─' * 76}")
        for idx, t in enumerate(trades, 1):
            pnl_str = f"${t['pnl']:,.2f}"
            marker = "+" if t["pnl"] > 0 else " "
            reason = t.get("exit_reason", "?")
            print(f"  {idx:>3} {t['direction']:<5} {t['entry_price']:>10.2f} {t['exit_price']:>10.2f} {t['sl_price']:>10.2f} {t['tp_price']:>10.2f} {t['lots']:>6.2f} {marker}{pnl_str:>9} {reason}")
        print(f"  {'─' * 76}")
    else:
        print("  No trades found in this period.")

    print()
    return trades, balance


def generate_sample_data():
    """Generate realistic price data for testing."""
    import random
    random.seed(42)

    candles = []

    if config.INSTRUMENT_TYPE == "crypto":
        price = 60000.00
        base_vol = 50.0
        quiet_vol = 20.0
    elif config.INSTRUMENT == "EURUSD":
        price = 1.0850
        base_vol = 0.0005
        quiet_vol = 0.0002
    else:
        price = 2300.00
        base_vol = 0.7
        quiet_vol = 0.3

    for i in range(5000):
        hour = (i % 1440) // 60

        if 8 <= hour < 20:
            volatility = base_vol
        else:
            volatility = quiet_vol

        trend = 0.01 * (base_vol / 0.7) if i % 500 < 250 else -0.01 * (base_vol / 0.7)
        change = random.gauss(trend, volatility)

        open_p = price
        close_p = open_p + change
        high_p = max(open_p, close_p) + abs(random.gauss(0, volatility * 0.5))
        low_p = min(open_p, close_p) - abs(random.gauss(0, volatility * 0.5))

        if config.INSTRUMENT == "EURUSD":
            dp = 5
        elif config.INSTRUMENT_TYPE == "crypto":
            dp = 2
        else:
            dp = 2

        candles.append({
            "time": f"2026-04-{7 + i // 1440:02d}T{hour:02d}:{i % 60:02d}:00Z",
            "open": round(open_p, dp),
            "high": round(high_p, dp),
            "low": round(low_p, dp),
            "close": round(close_p, dp),
            "volume": random.randint(100, 1000),
        })
        price = close_p

    return candles


def main():
    print(f"\n{config.INSTRUMENT_NAME} Heikin Ashi Strategy v6 -- Backtest")
    print("=" * 70)
    print(f"Strategy: 2-candle HA + structural SL (R:R 1:{config.MIN_RR_RATIO}) / ${config.TRAILING_TP_STEP} step")

    print("\nUsing generated sample data (connect MT5 for real data)\n")
    sample_candles = generate_sample_data()

    run_backtest(sample_candles, f"{config.INSTRUMENT_NAME} 1-Minute (Sample Data)")

    candles_3m = sample_candles[::3]
    run_backtest(candles_3m, f"{config.INSTRUMENT_NAME} 3-Minute (Sample Data)")


if __name__ == "__main__":
    main()
