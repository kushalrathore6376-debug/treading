# Gold Bot Setup v8 — Changes & Rating

**Instrument:** XAUUSD (M1 Heikin-Ashi 2-candle breakout)  
**Bot version:** v8 (Scenario B + hybrid exit + ADX≥25)  
**Date:** 2026-11-18  
**Test window:** ~1 month Dukascopy M1 bid (2026-10-19 → 2026-11-18 IST)

---

## 1. What we changed

### Before (baseline live behavior)
| Piece | Old |
|-------|-----|
| Session filter | IST blackout 02:00–04:00 only |
| Entry chase | None (any touch past breakout) |
| ADX floor | ≥ 20 |
| Exit | Always ratchet (lot-scaled step); last-3 adaptive “conservative” rarely fired because P&L lookup often failed |
| Logging | Basic fill / ratchet lines |

### After (v8 live pack) — what is in code now

| Change | Detail | Files |
|--------|--------|--------|
| **Scenario B — session block** | No new trades local **22:00–06:00** (plus existing IST 02–04) | `config.py`, `strategy.py` |
| **Scenario B — chase band** | Enter only if price is **0.05–0.20 pts** past breakout; wait if too low, skip if too high | `config.py`, `bot.py` |
| **ADX floor raised** | XAUUSD **ADX ≥ 25** (was 20) | `config.py` |
| **Hybrid exit** | Lots **≤ 0.02** → close at **first profit step (~$6)**; lots **≥ 0.03** → full **ratchet** trail | `config.py`, `bot.py` |
| **Richer EVENT logs** | SIGNAL / FILL / SKIP / SNAPSHOT / RATCHET / FIRST_PROFIT_CLOSE / TRADE_CLOSE | `bot.py` |
| **Stress harness** | Dukascopy month sim for B vs live pack | `stress_test_scenario_b.py` |

### Unchanged core strategy
- HA 2-candle pattern (both-wicks → single-wick continuation)
- VWAP filter, ATR min, body min, structural SL 2–5 pts + 0.30 buffer
- Risk ~`$MAX_LOSS` ($8) sized into lots; step = `$3 × lots × 100`
- Last-3 adaptive conservative still available as overlay on large lots

### Config knobs (XAUUSD)
```
adx_min = 25
use_session_block = True   # 22 → 06 local
use_chase_filter = True    # chase 0.05–0.20
USE_HYBRID_EXIT = True
HYBRID_FIRST_PROFIT_MAX_LOTS = 0.02
```

---

## 2. Why these changes

| Problem seen in Oct–Nov live log (~50 fills, ~−$59) | Fix |
|-----------------------------------------------------|-----|
| Many **false / limp breakouts** | Chase band forces a real push, rejects late chase |
| Thin overnight liquidity damage | Session block 22–06 |
| Weak trend / chop entries | ADX ≥ 25 |
| Small lots often locked tiny ratchet (~$1.98) then gave back | Hybrid banks full first step (~$6) on ≤0.02 |
| Big lots still need runners | 0.03+ keep ratchet |

---

## 3. Stress-test results (same month tape)

| Pack | Trades | Win % | Net P&L | Max DD |
|------|-------:|------:|--------:|-------:|
| Baseline (no B, ADX20, ratchet) | ~334 | — | **−$342** | — |
| Scenario B only (ADX20 + ratchet) | 221 | 53% | **−$138** | 1.93% |
| **Live v8 (B + ADX≥25 + hybrid)** | **166** | **54%** | **+$55** | **0.92%** |

**v8 vs Scenario B alone: +$193 improvement on this tape.**

### Hybrid split (v8)
| Size / exit | n | PnL |
|-------------|--:|----:|
| ≤0.02 → FIRST_PROFIT (~$6) | 113 | −$65 |
| ≥0.03 → RATCHET | 53 | **+$120** |
| Exits | 62 FIRST_PROFIT · 27 TRAIL_SL · 77 STOP_LOSS | |

### Rejected / weaker what-ifs (same tape, not live)
| Idea | Result |
|------|--------|
| B + no ratchet, SL$7, FP$12 | −$80 |
| Same + ADX≥28 | +$10 |
| Stricter ADX32 + chase 0.05–0.15 + hybrid | +$84 (fewer trades; not implemented) |

---

## 4. Rating of this setup

### Overall score: **7.0 / 10** (promising demo pack — not proven live edge yet)

| Dimension | Score | Notes |
|-----------|------:|-------|
| **Logic fit to loss drivers** | 8/10 | Chase + session hit the main false-breakout / overnight drag |
| **Simulated month P&L** | 7.5/10 | Flipped −$138 → +$55 with lower DD |
| **Risk / drawdown** | 8/10 | Max DD ~0.9% on $10k sim; per-trade SL capped ~$8–10 |
| **Robustness** | 5/10 | Tuned on **one** month of bid CSV; params (ADX 25, chase 0.20) can overfit |
| **Live readiness** | 6/10 | Code is wired; fill/slippage/spread vs Dukascopy bid will differ |
| **Operational clarity** | 8/10 | EVENT logs + hybrid mode labels make review easy |

### Strengths
- Clear story: filter junk entries, bank small winners, let size-up runners trail
- Measurable lift on the same stress tape used for Scenario B
- Conservative dollar risk per trade; hybrid stops “$1.98 scratch then loss” on 0.02 lots

### Weaknesses / risks
- **In-sample** green — not walk-forward / multi-month validated
- Bid-only backtest understates spread/slippage (live Gold M1 is harsher)
- Small-lot book still net negative; edge rides on **fewer, larger ratchet winners**
- ADX≥25 cuts frequency (~221 → 166); quieter weeks may feel “dead”
- Last-3 conservative + hybrid can both fire; watch logs if modes confuse analysis

### Verdict
**Good enough to run on demo and collect 2–4 weeks of live EVENT logs.**  
Treat **+$55** as a *hypothesis confirmation on historical bid data*, not a guarantee.  
Promote to small live risk only after live fills roughly match sim win rate / MFE-MAE — not before.

### Suggested next checks (not implemented)
1. Re-run stress on a second month / different Gold feed  
2. Add spread/slippage haircut (e.g. 0.05–0.10 pt) in the harness  
3. If still green live, optionally test stricter pack (ADX≥30, chase max 0.15) as A/B

---

## 5. How to run

```bash
# Live bot (MT5 demo)
py bot.py

# Month stress vs Scenario B
py stress_test_scenario_b.py
```

Expect startup log: `HEIKIN ASHI BOT v8 (B + hybrid + ADX)` with ADX min, chase band, and hybrid line.

---

## 6. Quick reference — live decision tree

```
Pattern + VWAP + ATR + ADX≥25?
  → Outside 22–06 and IST 02–04?
    → Live chase in [0.05, 0.20]?
      → Structural SL in [2, 5] pts?
        → Size lots to ~$8 risk
          → lots ≤ 0.02: exit at first step (~$6) or SL
          → lots ≥ 0.03: ratchet trail (or last-3 CONS first-profit)
```
