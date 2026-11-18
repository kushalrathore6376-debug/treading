#!/usr/bin/env python3
"""
HA Trading Bot Dashboard — Flask/SocketIO Web Server
Run:  python dashboard.py
Open: http://localhost:5000
Deps: pip install flask flask-socketio flask-cors
"""

import sys, os, time, threading, subprocess, random, math
from datetime import datetime, timedelta
from collections import deque

from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit
from flask_cors import CORS

# ── Project root on path ─────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MT5_AVAILABLE = False
_broker_inst  = None

try:
    import MetaTrader5 as mt5
    import config
    from broker import MT5Broker
    from heikin_ashi import convert_to_heikin_ashi, calculate_vwap, calculate_adx, calculate_atr
    from strategy   import detect_buy_pattern, detect_sell_pattern
    MT5_AVAILABLE = True
    print("✓  MT5 modules loaded")
except Exception as exc:
    print(f"⚠  MT5 unavailable ({exc}) — demo mode active")


# ── Flask / SocketIO ──────────────────────────────────────────────
app = Flask(__name__)
app.config["SECRET_KEY"] = "ha-bot-dash-2026"
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# ── Runtime state ─────────────────────────────────────────────────
bot_process  = None
bot_running  = False
log_buffer   = deque(maxlen=500)
_demo_price  = 2341.50


# ── Broker helper ─────────────────────────────────────────────────
def get_broker():
    global _broker_inst
    if _broker_inst is None and MT5_AVAILABLE:
        try:
            _broker_inst = MT5Broker()
        except Exception as e:
            print(f"Broker init failed: {e}")
    return _broker_inst


# ── Data getters ──────────────────────────────────────────────────

def fetch_account():
    b = get_broker()
    if not b:
        return {
            "balance": 10234.50, "equity": 10289.80, "margin": 0.0,
            "free_margin": 10289.80, "profit": 55.30, "margin_level": 0.0,
            "currency": "USD", "leverage": 100,
            "name": "Demo Trader", "server": "ICMarketsSC-Demo",
        }
    try:
        info = mt5.account_info()
        if info:
            return {
                "balance":      round(float(info.balance), 2),
                "equity":       round(float(info.equity), 2),
                "margin":       round(float(info.margin), 2),
                "free_margin":  round(float(info.margin_free), 2),
                "profit":       round(float(info.profit), 2),
                "margin_level": round(float(info.margin_level or 0), 2),
                "currency":     info.currency,
                "leverage":     info.leverage,
                "name":         info.name,
                "server":       info.server,
            }
    except Exception as e:
        print(f"Account error: {e}")
    return {"balance": 0, "equity": 0, "profit": 0, "currency": "USD"}


def fetch_positions():
    b = get_broker()
    if not b:
        return []
    try:
        return [{
            "ticket":        p.ticket,
            "symbol":        p.symbol,
            "type":          "BUY" if p.type == 0 else "SELL",
            "volume":        p.volume,
            "open_price":    round(float(p.price_open), 2),
            "current_price": round(float(p.price_current), 2),
            "sl":            round(float(p.sl), 2),
            "tp":            round(float(p.tp), 2),
            "profit":        round(float(p.profit), 2),
            "swap":          round(float(p.swap), 2),
            "time":          datetime.fromtimestamp(p.time).strftime("%H:%M:%S"),
            "comment":       p.comment,
        } for p in b.get_open_positions()]
    except Exception as e:
        print(f"Positions error: {e}")
        return []


def fetch_history(hours=48):
    # Ensure MT5 is initialised before querying history; without this,
    # mt5.history_deals_get returns None if no other API call has triggered
    # the broker connection yet (e.g. bot started from terminal, not dashboard).
    b = get_broker()
    if not MT5_AVAILABLE or not b:
        return _demo_history()
    try:
        dt_from = datetime.now() - timedelta(hours=hours)
        deals = mt5.history_deals_get(dt_from, datetime.now())
        if not deals:
            return []
        result = []
        for d in deals:
            if d.entry == mt5.DEAL_ENTRY_OUT and d.symbol:
                # DEAL_ENTRY_OUT type is the CLOSING side, which is the opposite
                # of the original position direction. Flip so the table shows the
                # direction the trader actually traded (BUY position → show BUY).
                orig_dir = "BUY" if d.type == mt5.DEAL_TYPE_SELL else "SELL"
                result.append({
                    "ticket":     d.ticket,
                    "symbol":     d.symbol,
                    "type":       orig_dir,
                    "volume":     d.volume,
                    "price":      round(float(d.price), 2),
                    "profit":     round(float(d.profit), 2),
                    "commission": round(float(d.commission), 2),
                    "swap":       round(float(d.swap), 2),
                    "time":       datetime.fromtimestamp(d.time).strftime("%m/%d %H:%M"),
                    "comment":    d.comment,
                })
        return list(reversed(result))
    except Exception as e:
        print(f"History error: {e}")
        return []


def _demo_history():
    base = datetime.now() - timedelta(hours=10)
    rows = [
        (1001,"XAUUSD","BUY", 0.01,2318.50, 18.20),
        (1002,"XAUUSD","SELL",0.01,2325.20, -7.50),
        (1003,"XAUUSD","BUY", 0.02,2330.80, 21.60),
        (1004,"XAUUSD","BUY", 0.01,2328.10, -8.00),
        (1005,"XAUUSD","SELL",0.01,2335.60, 19.80),
        (1006,"XAUUSD","BUY", 0.02,2338.40, 24.30),
        (1007,"XAUUSD","SELL",0.01,2340.20, -7.80),
        (1008,"XAUUSD","BUY", 0.01,2337.90, 20.10),
    ]
    return [{
        "ticket": r[0], "symbol": r[1], "type": r[2], "volume": r[3],
        "price": r[4], "profit": r[5], "commission": -0.10, "swap": 0.0,
        "time": (base + timedelta(minutes=i*65)).strftime("%m/%d %H:%M"),
        "comment": f"HA Bot {r[2]}",
    } for i, r in enumerate(rows)]


def fetch_tick(symbol):
    global _demo_price
    b = get_broker()
    if not b:
        # Smaller, more realistic tick movement so candle body is visible
        _demo_price += random.gauss(0, 0.40)
        _demo_price  = max(2200, min(2500, _demo_price))
        spread = 0.30
        return {"bid": round(_demo_price, 2),
                "ask": round(_demo_price + spread, 2),
                "time": datetime.now().strftime("%H:%M:%S")}
    try:
        t = b.get_tick(symbol)
        if t:
            return {"bid": round(float(t.bid), 2), "ask": round(float(t.ask), 2),
                    "time": datetime.fromtimestamp(t.time).strftime("%H:%M:%S")}
    except Exception:
        pass
    return None


def fetch_candles(symbol, timeframe, count=150):
    b = get_broker()
    if not b:
        return _demo_candles(count)
    try:
        return b.get_candles(symbol, timeframe, count + 1)
    except Exception as e:
        print(f"Candles error: {e}")
        return _demo_candles(count)


def _demo_candles(count=150):
    price = _demo_price
    # End the last completed candle at the CURRENT completed minute so the
    # JS forming-candle (lastCandle.time + 60s) lands exactly at "now".
    import time as _time
    now_sec  = int(_time.time())
    last_min = (now_sec // 60) * 60          # floor to current minute
    ts       = last_min - (count - 1) * 60   # first candle starts here
    out   = []
    for i in range(count):
        chg = random.gauss(0, 0.9)
        o = round(price, 2)
        c = round(price + chg, 2)
        h = round(max(o, c) + abs(random.gauss(0, 0.5)), 2)
        l = round(min(o, c) - abs(random.gauss(0, 0.5)), 2)
        out.append({"time": str(ts + i * 60), "open": o, "high": h,
                    "low": l, "close": c, "volume": random.randint(80, 1800)})
        price = c
    return out


def fetch_indicators(symbol, timeframe=1, count=150):
    b = get_broker()
    if not b:
        adx_series = [round(20 + 15 * math.sin(i / 10) + random.gauss(0, 2), 1) for i in range(50)]
        return {"adx": adx_series[-1], "atr": 0.82, "vwap": 2338.20,
                "adx_series": adx_series, "vwap_series": None}
    try:
        raw = b.get_candles(symbol, timeframe, count + 1)
        if not raw:
            return {}
        adx_v  = calculate_adx(raw, 10)
        atr_v  = calculate_atr(raw, 10)
        vwap_v = calculate_vwap(raw, 0)
        return {
            "adx":        round(float(adx_v[-1]),  2) if adx_v  and adx_v[-1]  else None,
            "atr":        round(float(atr_v[-1]),  4) if atr_v  and atr_v[-1]  else None,
            "vwap":       round(float(vwap_v[-1]), 2) if vwap_v and vwap_v[-1] else None,
            "adx_series": [round(float(v), 2) if v else None for v in (adx_v  or [])[-50:]],
            "vwap_series":[round(float(v), 2) if v else None for v in (vwap_v or [])[-50:]],
        }
    except Exception as e:
        print(f"Indicators error: {e}")
        return {}


def fetch_config():
    if MT5_AVAILABLE:
        return {
            "instrument":     config.INSTRUMENT,
            "name":           config.INSTRUMENT_NAME,
            "timeframes":     config.TIMEFRAMES,
            "max_loss":       config.MAX_LOSS_DOLLARS,
            "take_profit":    config.TAKE_PROFIT_DOLLARS,
            "trail_activation":config.TRAIL_ACTIVATION_PROFIT,
            "ratchet_step":   config.TRAILING_TP_STEP,
            "adx_min":        config.ADX_MIN_THRESHOLD,
            "atr_min":        config.ATR_MIN_THRESHOLD,
            "use_vwap":       config.USE_VWAP_FILTER,
            "all_instruments":list(config.INSTRUMENTS.keys()),
            "check_interval": config.CHECK_INTERVAL,
        }
    return {
        "instrument": "XAUUSD", "name": "Gold", "timeframes": [1],
        "max_loss": 8.0, "take_profit": 20.0, "trail_activation": 3.0,
        "ratchet_step": 3.0, "adx_min": 20, "atr_min": 0.5,
        "use_vwap": True, "all_instruments": ["XAUUSD", "BTCUSD", "EURUSD"],
        "check_interval": 0.5,
    }


# ── Background live-push thread ───────────────────────────────────

def _live_push():
    sym = (config.INSTRUMENT if MT5_AVAILABLE else "XAUUSD")
    prev_tickets    = set()   # open position tickets from last cycle
    ind_counter     = 0       # emit indicators every ~10 s (every 13 cycles)
    hist_counter    = 0       # periodic history refresh every ~30 s (every 40 cycles)

    while True:
        try:
            positions = fetch_positions()
            account   = fetch_account()
            tick      = fetch_tick(sym)

            # ── Detect newly closed position ──────────────────────
            cur_tickets  = {p["ticket"] for p in positions}
            trade_closed = bool(prev_tickets - cur_tickets)  # ticket disappeared
            prev_tickets = cur_tickets

            # ── Indicators (expensive — refresh every ~10 s) ──────
            ind_counter += 1
            indicators = None
            if ind_counter >= 13:
                ind_counter = 0
                indicators = fetch_indicators(sym)

            payload = {
                "account":     account,
                "positions":   positions,
                "tick":        tick,
                "bot_running": bot_running,
                "symbol":      sym,
                "ts":          datetime.now().isoformat(),
            }
            if indicators:
                payload["indicators"] = indicators

            socketio.emit("live", payload)

            # ── Push history on close OR every ~30 s as a catch-all ──
            # The periodic push ensures the table stays current even when
            # the bot runs from a terminal (not via the dashboard Start button)
            # or when a fast SL hit slips between two position-tracking cycles.
            hist_counter += 1
            if trade_closed or hist_counter >= 40:
                hist_counter = 0
                history = fetch_history(48)
                socketio.emit("history_update", history)
                if trade_closed:
                    log_entry = {"t": _ts(), "msg": "Trade closed — history updated", "lvl": "info"}
                    log_buffer.append(log_entry)
                    socketio.emit("log_line", log_entry)

        except Exception as e:
            print(f"Live push error: {e}")
        time.sleep(0.75)


# ── REST API ─────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/account")
def r_account():
    return jsonify(fetch_account())


@app.route("/api/positions")
def r_positions():
    return jsonify(fetch_positions())


@app.route("/api/history")
def r_history():
    return jsonify(fetch_history(int(request.args.get("hours", 48))))


@app.route("/api/candles/<symbol>/<int:tf>")
def r_candles(symbol, tf):
    n   = int(request.args.get("count", 150))
    raw = fetch_candles(symbol, tf, n)
    ha  = convert_to_heikin_ashi(raw) if MT5_AVAILABLE and raw else raw
    ind = fetch_indicators(symbol, tf)
    return jsonify({"raw": raw, "ha": ha, "indicators": ind})


@app.route("/api/indicators/<symbol>")
def r_indicators(symbol):
    tf = int(request.args.get("tf", 1))
    return jsonify(fetch_indicators(symbol, tf))


@app.route("/api/tick/<symbol>")
def r_tick(symbol):
    return jsonify(fetch_tick(symbol) or {})


@app.route("/api/config")
def r_config():
    return jsonify(fetch_config())


@app.route("/api/bot/start", methods=["POST"])
def r_bot_start():
    global bot_process, bot_running
    if bot_running:
        return jsonify({"status": "already_running"})
    try:
        bot_process = subprocess.Popen(
            [sys.executable, "bot.py"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        bot_running = True
        threading.Thread(target=_tail_bot, args=(bot_process,), daemon=True).start()
        log_buffer.append({"t": _ts(), "msg": f"Bot started (PID {bot_process.pid})", "lvl": "info"})
        socketio.emit("bot_status", {"running": True, "pid": bot_process.pid})
        return jsonify({"status": "started", "pid": bot_process.pid})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/bot/stop", methods=["POST"])
def r_bot_stop():
    global bot_process, bot_running
    if bot_process:
        bot_process.terminate()
        bot_process = None
    bot_running = False
    log_buffer.append({"t": _ts(), "msg": "Bot stopped by user", "lvl": "warn"})
    socketio.emit("bot_status", {"running": False})
    return jsonify({"status": "stopped"})


@app.route("/api/bot/status")
def r_bot_status():
    return jsonify({"running": bot_running,
                    "pid": bot_process.pid if bot_process else None})


@app.route("/api/close/<int:ticket>", methods=["POST"])
def r_close(ticket):
    b = get_broker()
    if not b:
        return jsonify({"error": "Broker not connected"}), 503
    try:
        for pos in b.get_open_positions():
            if pos.ticket == ticket:
                b.close_position(pos)
                log_buffer.append({"t": _ts(), "msg": f"Closed position #{ticket}", "lvl": "info"})
                return jsonify({"status": "closed", "ticket": ticket})
        return jsonify({"error": "Position not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _ts():
    return datetime.now().strftime("%H:%M:%S")


def _tail_bot(proc):
    global bot_running
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        lvl = "buy"  if "BUY"   in line else \
              "sell" if "SELL"  in line else \
              "warn" if "WARN"  in line or "failed" in line.lower() else \
              "err"  if "ERROR" in line else "info"
        entry = {"t": _ts(), "msg": line, "lvl": lvl}
        log_buffer.append(entry)
        socketio.emit("log_line", entry)
    bot_running = False
    socketio.emit("bot_status", {"running": False})


# ── WebSocket events ──────────────────────────────────────────────

@socketio.on("connect")
def on_connect():
    emit("log_history",   list(log_buffer))
    emit("config",        fetch_config())
    emit("history_update", fetch_history(48))


@socketio.on("get_candles")
def on_get_candles(data):
    sym = data.get("symbol", "XAUUSD")
    tf  = int(data.get("tf", 1))
    n   = int(data.get("count", 150))
    raw = fetch_candles(sym, tf, n)
    ha  = convert_to_heikin_ashi(raw) if MT5_AVAILABLE and raw else raw
    ind = fetch_indicators(sym, tf)
    emit("candles_data", {"raw": raw, "ha": ha, "indicators": ind, "symbol": sym, "tf": tf})


# ── Entry point ───────────────────────────────────────────────────

if __name__ == "__main__":
    threading.Thread(target=_live_push, daemon=True).start()
    print("\n" + "─" * 52)
    print("  HA Trading Bot Dashboard")
    print("  Open → http://localhost:5000")
    print("─" * 52 + "\n")
    socketio.run(app, host="0.0.0.0", port=5000, debug=False,
                 allow_unsafe_werkzeug=True)
