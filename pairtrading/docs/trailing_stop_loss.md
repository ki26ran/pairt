# Trailing Stop-Loss for Pair Trading

## Overview

The trailing stop-loss (trail_sl) replaces the fixed stop-loss with a dynamic one that tightens as the z-score moves favorably. This protects profits while still allowing the trade room to breathe.

## Configuration

```python
# scan_pairs.py
TRAIL_SL_MULTIPLIER = 1.5   # trail distance = exit_z * TRAIL_SL_MULTIPLIER (min 1.0)
STOP_LOSS_MULTIPLIER = 2.0  # fixed SL as fallback at entry_z * 2.0
```

## How It Works

### Fixed SL (old):
```
Entry at Z = -2.0, SL at Z = -2.0 * 3.0 = -6.0
Z goes from -2.0 to -4.5 (good move) then reverses to -1.0
→ Still in trade, the -6.0 SL never triggered
→ Can ride from +profit all the way to -SL
```

### Trailing SL (new):
```
Entry at Z = -2.0
- Best |z| tracked in _best_abs_z dict
- trail_dist = max(exit_z * 1.5, 1.0)

Example with IRFC|KFINTECH (exit_z=1.0):
  trail_dist = max(1.0 * 1.5, 1.0) = 1.5 σ

  Entry at Z = -2.28, best_abs_z starts at 2.28
  Z moves to -3.50 → best_abs_z updates to 3.50 ✓
  Z reverses to -2.00 → 3.50 > 2.00 + 1.50 → EXIT (trail_sl) ✓
  Result: Exit at Z=-2.00 with profit, instead of riding to -5.25 SL
```

### Minimum Trail Floor:
```python
trail_dist = max(exit_z * TRAIL_SL_MULTIPLIER, 1.0)
```

Ensures no pair has a trail tighter than 1.0 σ, preventing premature exits from noise.

## Per-Pair Trail Distances

| Pair | exit_z | Trail 1.5x | Min 1.0 floor | Effective Trail |
|------|--------|-----------|---------------|-----------------|
| IRFC\|KFINTECH | 1.0 | 1.5 | 1.0 | **1.5 σ** |
| LUPIN\|TORNTPHARM | 2.5 | 3.75 | 1.0 | **3.75 σ** |
| JUBLFOOD\|PATANJALI | 0.75 | 1.125 | 1.0 | **1.125 σ** |
| TCS\|WIPRO | 1.0 | 1.5 | 1.0 | **1.5 σ** |

Higher exit_z means wider trailing distance — pairs with wider mean-reversion targets get more room.

## Backtest Validation

Run the comparison backtest:
```bash
cd /opt/swing && APP_ENV=prod /opt/pairt/venv/bin/python backtest_trailing_sl.py
```

### Results (all 13 pairs, Sep 2025 - Jul 2026):

| Config | Trades | P&L | Win Rate | Profit Factor |
|--------|--------|-----|----------|---------------|
| Fixed SL (3x) | 208 | -₹6,798 | 39.9% | 0.37 |
| **Trail 1.5x** | **482** | **-₹2,475** | **47.1%** | **0.72** |
| Trail 1.0x | 484 | -₹2,616 | 47.1% | 0.71 |

### Key Findings:
1. **Trailing SL reduces losses by 63%** (-₹6,798 → -₹2,475)
2. **Win rate improves 7 points** (39.9% → 47.1%)
3. **Profit factor nearly doubles** (0.37 → 0.72)
4. **Trail 1.5x is optimal** — best P&L and PF across all tested values (0.5x to 2.0x)
5. Only TCS|WIPRO performed worse with trailing SL (options spread too wide relative to z-score moves)

## Live Verification

Monitor the `exit_reason` field in:
- **Scanner results**: Shows "trail_sl" as exit reason in pair_scanner_results table
- **Telegram alerts**: Sent when a position exits via trailing stop
- **Trade journal**: `pair_trades` table records exit_reason = 'trail_sl'

Check active positions with:
```bash
/opt/pairt/venv/bin/python -c "
import duckdb
con = duckdb.connect('/opt/pairt/pairtrading/pairtrading.duckdb')
# Show trail distances for active positions
SELECT pair_key, exit_z_threshold, 
       MAX(exit_z_threshold * 1.5, 1.0) as trail_distance
FROM pair_positions
"
```

## Code References

| File | Lines | Purpose |
|------|-------|---------|
| `scan_pairs.py` | 42, 50, 504-516 | Constants + trail SL exit logic |
| `scan_pairs.py` | 527 | Cleanup _best_abs_z on exit |
| `backtest.py` | 26, 53 | Backtest trail SL support |
| `pair_trading.py` | 82, 113 | Original backtest engine trail SL support |

## Exit Priority Order

1. **Mean-reversion** — z-score crosses exit threshold (profitable exit)
2. **Stop-loss** — z-score hits fixed SL (entry_z * 2.0) — catastrophic protection
3. **Trail SL** — z-score retraces `trail_dist` from best level — protects gains
4. **Timeout** — max hold days (20) exceeded
