# ============================================================
# TRADING BOT CONFIGURATION -- MetaTrader 5
# ============================================================
# Setup:
#   1. Install MT5 from your broker (Exness, XM, IC Markets etc.)
#   2. Open a DEMO account (free, paper money)
#   3. Note your login, password, and server name
#   4. Fill in below
# ============================================================

# MT5 Demo Account credentials Kk@6376127095
MT5_LOGIN = 5058870440
MT5_PASSWORD = "*yL0PbNt"
MT5_SERVER = "MetaQuotes-Demo"

# MT5 terminal path (usually auto-detected, set if needed)
MT5_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"

# ============================================================
# EASY SWITCH: Change this ONE line to switch instrument
# ============================================================
ACTIVE_INSTRUMENT = "XAUUSD"  # "XAUUSD", "BTCUSD", "EURUSD", etc.

# ============================================================
# INSTRUMENT PROFILES
# Each profile has its own strategy settings.
# Forex = session-based trading with VWAP + time filter
# Crypto = 24/7 trading, no session filters
# ============================================================
INSTRUMENTS = {
    "XAUUSD": {
        "type": "forex",
        "name": "Gold",
        "contract_size": 100,  # 1 lot = 100 oz
        "timeframes": [1],  # M1, M3
        "position_size_pct": 0.005,  # 0.5%
        "max_loss": 8.00,  # $10 SL per trade
        "take_profit": 20.00,  # $20 TP per trade
        "trail_activation": 3.00,  # Trail starts at $3 profit
        "trail_distance": 3.00,  # SL trails $3 behind profit
        "trailing_tp_step": 3.00,  # TP ratchets up by $3 each time (faster breakeven)
        "adx_min": 25,  # require stronger trend (was 20)
        "atr_min": 0.50,
        "use_time_filter": False,
        "trade_start_utc": 0,
        "trade_end_utc": 24,
        "use_vwap": True,
        "vwap_session_start": 0,  # midnight UTC reset
        "min_sl_distance": 2.00,  # skip if structural SL < 2 pts (noise)
        "max_sl_distance": 5.00,   # skip if structural SL > 5 pts (exhausted move)
        "min_rr_ratio": 2.0,  # minimum reward:risk ratio
        "min_body_points": 0.50,  # reject doji candles (body < 0.50 pts)
        "sl_buffer": 0.30,  # pad SL 0.30 pts beyond structural level
        "no_trade_start_ist": 2,  # blackout start (2AM IST)
        "no_trade_end_ist": 4,  # blackout end   (4AM IST)
        # Scenario B: thin-session block (local clock) + breakout chase band
        "use_session_block": True,
        "session_block_start": 22,  # no new trades from 22:00
        "session_block_end": 6,     # until 06:00 (hour < 6 blocked)
        "use_chase_filter": True,
        "chase_min": 0.05,  # need at least 0.05 pts past breakout
        "chase_max": 0.20,  # reject late chase beyond 0.20 pts
    },
    "BTCUSD": {
        "type": "crypto",
        "name": "Bitcoin",
        "contract_size": 1,  # 1 lot = 1 BTC
        "timeframes": [1, 3],  # M1, M3
        "position_size_pct": 0.005,  # 0.5%
        "max_loss": 20.00,  # $20 max loss per trade
        "take_profit": 40.00,  # $40 TP per trade
        "trail_activation": 30.00,  # Trail starts at $30 profit
        "trail_distance": 20.00,  # SL trails $20 behind profit
        "trailing_tp_step": 10.00,  # TP ratchets up by $10 each time
        "adx_min": 20,
        "atr_min": 50.00,  # BTC moves big
        "use_time_filter": False,  # Crypto = 24/7
        "trade_start_utc": 0,
        "trade_end_utc": 24,
        "use_vwap": False,  # No session VWAP for 24/7
        "vwap_session_start": 0,
        "min_sl_distance": 50.00,
        "max_sl_distance": 500.00,
        "min_rr_ratio": 2.0,
        "min_body_points": 20.0,
        "sl_buffer": 10.0,
        "no_trade_start_ist": 2,
        "no_trade_end_ist": 4,
        "use_session_block": False,
        "session_block_start": 22,
        "session_block_end": 6,
        "use_chase_filter": False,
        "chase_min": 0.05,
        "chase_max": 0.20,
    },
    "EURUSD": {
        "type": "forex",
        "name": "Euro/USD",
        "contract_size": 100000,  # 1 lot = 100,000 units
        "timeframes": [1, 3],
        "position_size_pct": 0.005,
        "max_loss": 2.00,
        "take_profit": 4.00,
        "trail_activation": 3.00,
        "trail_distance": 3.00,
        "trailing_tp_step": 1.00,  # TP ratchets up by $1 each time
        "adx_min": 20,
        "atr_min": 0.00020,  # Forex pips are tiny
        "use_time_filter": True,
        "trade_start_utc": 8,
        "trade_end_utc": 20,
        "use_vwap": False,
        "vwap_session_start": 8,
        "min_sl_distance": 0.00020,
        "max_sl_distance": 0.00200,
        "min_rr_ratio": 2.0,
        "min_body_points": 0.00010,
        "sl_buffer": 0.00010,
        "no_trade_start_ist": 2,
        "no_trade_end_ist": 4,
        "use_session_block": False,
        "session_block_start": 22,
        "session_block_end": 6,
        "use_chase_filter": False,
        "chase_min": 0.05,
        "chase_max": 0.20,
    },
}

# ============================================================
# AUTO-LOADED FROM ACTIVE INSTRUMENT (DO NOT EDIT BELOW)
# These get set automatically based on ACTIVE_INSTRUMENT above
# ============================================================
_profile = INSTRUMENTS[ACTIVE_INSTRUMENT]

INSTRUMENT = ACTIVE_INSTRUMENT
INSTRUMENT_TYPE = _profile["type"]  # "forex" or "crypto"
INSTRUMENT_NAME = _profile["name"]
CONTRACT_SIZE = _profile["contract_size"]

TIMEFRAMES = _profile["timeframes"]
TIMEFRAME_NAMES = {1: "M1", 2: "M2", 3: "M3", 5: "M5", 15: "M15", 30: "M30", 60: "H1"}

POSITION_SIZE_PCT = _profile["position_size_pct"]
USE_LEVERAGE = False
MAX_OPEN_POSITIONS = 1

# Wick rules (ratios -- same for all instruments)
REQUIRED_WICK_MIN = 0.20
NO_WICK_TOLERANCE = 0.05
BOTH_WICKS_MIN = 0.15

# Trailing SL (dollar-based)
MAX_LOSS_DOLLARS = _profile["max_loss"]
TAKE_PROFIT_DOLLARS = _profile["take_profit"]
TRAIL_ACTIVATION_PROFIT = _profile["trail_activation"]
TRAIL_DISTANCE_PROFIT = _profile["trail_distance"]
TRAILING_TP_STEP = _profile["trailing_tp_step"]

# Time filter
USE_TIME_FILTER = _profile["use_time_filter"]
TRADE_START_HOUR_UTC = _profile["trade_start_utc"]
TRADE_END_HOUR_UTC = _profile["trade_end_utc"]

# VWAP
USE_VWAP_FILTER = _profile["use_vwap"]
VWAP_SESSION_START_HOUR_UTC = _profile["vwap_session_start"]

# ADX
USE_ADX_FILTER = True
ADX_PERIOD = 10
ADX_MIN_THRESHOLD = _profile["adx_min"]
ADX_STRONG_THRESHOLD = 35

# ATR
USE_ATR_FILTER = True
ATR_PERIOD = 10
ATR_MIN_THRESHOLD = _profile["atr_min"]

# General
CANDLE_COUNT = 100
CHECK_INTERVAL = 0.5
LOG_FILE = "trades.log"

# Structural SL
MIN_SL_DISTANCE = _profile["min_sl_distance"]
MAX_SL_DISTANCE = _profile["max_sl_distance"]
MIN_RR_RATIO = _profile["min_rr_ratio"]

# Candle body filter
MIN_BODY_POINTS = _profile["min_body_points"]

# SL buffer
SL_BUFFER = _profile["sl_buffer"]

# IST blackout window (no new trades)
NO_TRADE_START_IST = _profile["no_trade_start_ist"]
NO_TRADE_END_IST = _profile["no_trade_end_ist"]

# Thin liquidity session block (local clock hours) — Scenario B
USE_SESSION_BLOCK = _profile.get("use_session_block", False)
SESSION_BLOCK_START = _profile.get("session_block_start", 22)  # inclusive
SESSION_BLOCK_END = _profile.get("session_block_end", 6)      # exclusive (< end)

# Breakout chase band (pts past breakout at entry) — Scenario B
USE_CHASE_FILTER = _profile.get("use_chase_filter", False)
CHASE_MIN = _profile.get("chase_min", 0.05)
CHASE_MAX = _profile.get("chase_max", 0.20)

# How often to emit open-trade snapshots while in a position (cycles)
TRADE_SNAPSHOT_EVERY_N = 10  # 10 * CHECK_INTERVAL ≈ 5s

# Hybrid exit: small size takes first profit step; larger size ratchets
# 0.02 lots → step ≈ $6 (TRAILING_TP_STEP * lots * CONTRACT_SIZE) → bank ~$6
# 0.03+ lots → full ratchet trail
USE_HYBRID_EXIT = True
HYBRID_FIRST_PROFIT_MAX_LOTS = 0.02  # lots <= this → close at first profit step
