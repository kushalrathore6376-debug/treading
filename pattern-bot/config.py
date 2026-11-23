# ============================================================
# PATTERN BOT — Gold (XAUUSD) historical shape-match trader
# Separate from the HA breakout bot in ../treading
# ============================================================

# MT5 Demo (same account as HA bot is fine for testing)
MT5_LOGIN = 5058870440
MT5_PASSWORD = "*yL0PbNt"
MT5_SERVER = "MetaQuotes-Demo"
MT5_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"

INSTRUMENT = "XAUUSD"
CONTRACT_SIZE = 100  # 1 lot = 100 oz
TIMEFRAME_M1 = 1

# Risk
MAX_LOSS_DOLLARS = 8.00
TAKE_PROFIT_DOLLARS = 16.00  # default; bot may scale from historical avg move
LOTS_FIXED = 0.02  # keep small while validating pattern edge
MAX_OPEN_POSITIONS = 1

# Pattern match (core strategy — NOT Heikin-Ashi breakout)
PATTERN_LOOKBACK = 60       # minutes of shape to match
PATTERN_HORIZON = 30        # minutes ahead to score historical outcome
PATTERN_MIN_SCORE = 0.85    # cosine similarity floor
PATTERN_TOP_K = 8
PATTERN_MIN_CONFIDENCE = 0.55  # need majority of matches agreeing
PATTERN_STRIDE = 5
PATTERN_EXCLUDE_RECENT = 500

# Only trade when bias is clear
REQUIRE_BIAS = True  # BUY or SELL from history consensus
DRY_RUN = True       # True = log signals only, no MT5 orders (safe first)

# Mac / no-MT5: bot auto-uses PAPER mode (like main dashboard demo).
# Set True to force paper even on Windows.
FORCE_PAPER_MODE = False

# Loop
CHECK_INTERVAL_SEC = 30
CANDLE_COUNT = 120
LOG_FILE = "logs/pattern_bot.log"

# Validate UI
UI_HOST = "127.0.0.1"
UI_PORT = 5055
