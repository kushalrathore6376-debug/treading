# Pattern Bot — Gold chart-shape matcher

**Separate project** from `../treading` (HA breakout bot).  
This bot **only** trades when the *current* Gold M1 path looks like past windows, then follows the historical forward bias.

## Features

1. **Download ~1 year XAUUSD M1** (Dukascopy bid)  
2. **Validate UI** — candles + volume by time/price so you can check data is sane  
3. **Pattern engine** — normalize last N closes → find similar history → consensus BUY/SELL  
4. **Pattern bot** — enters on consensus (starts in `DRY_RUN=True`)

## Setup

### Mac (download + validate UI only)

`MetaTrader5` **does not install on Mac** — live trading is Windows-only.

```bash
cd pattern-bot
python3 -m pip install -r requirements.txt

# 1) Install Node.js (needed for Dukascopy download)
#    https://nodejs.org  OR:  brew install node
node -v && npx -v

# 2) Download ~1 year Gold M1
python3 download_history.py

# 3) Validate UI
python3 app.py
# → http://127.0.0.1:5055/
```

### Windows (UI + live MT5 bot)

```powershell
cd pattern-bot
py -m pip install -r requirements-windows.txt
py download_history.py
py app.py
py bot.py
```

Edit `config.py` for MT5 login if needed.

## Validate data (UI)

Open **http://127.0.0.1:5055/**

Checklist:
- Summary: bars, start/end UTC+IST, price min/max, gaps  
- Load last 1500 bars — OHLC continuous (weekend gaps OK)  
- Compare a day vs TradingView / MT5 Gold M1  
- Volume = Dukascopy relative volume (shape only)  
- **Find similar patterns** — see historical matches + forward move

## Run the pattern bot (Windows + MT5)

```powershell
py bot.py
```

Default: **`DRY_RUN=True`** → logs signals only.  
When you trust the UI + signals, set `DRY_RUN = False` in `config.py`.

### Signal logic (v1)

| Step | What |
|------|------|
| Lookback | Last **60** M1 closes from MT5 |
| Match | Cosine similarity ≥ **0.85** on %-normalized path vs 1y history |
| Horizon | Next **30** minutes after each match |
| Trade | Majority UP → BUY, DOWN → SELL if confidence ≥ **0.55** |
| Risk | Fixed lots `0.02`, SL ~$8, TP scaled from avg historical move |

## vs HA bot (`../treading`)

| | HA bot (treading) | Pattern bot (this) |
|--|-------------------|--------------------|
| Entry | HA 2-candle breakout + Scenario B | History shape consensus only |
| Exit | Hybrid / ratchet | Broker SL + TP |
| UI | Live dashboard + `/validate` | Dedicated validate UI on `:5055` |

## Honest limits

- Shape match ≠ same news/regime  
- Dukascopy bid ≠ your broker feed  
- Treat as research → dry-run → tiny live size  

## Layout

```
pattern-bot/
  download_history.py   # 1y downloader
  history_data.py       # CSV load / summary
  pattern_engine.py     # similarity + consensus
  app.py                # validate UI
  bot.py                # pattern-only trader
  broker.py             # MT5 helpers
  config.py
  templates/validate.html
  download/             # CSVs land here
```
