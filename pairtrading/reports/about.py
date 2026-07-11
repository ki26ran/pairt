import streamlit as st


def show():
    st.title("📖 Guide — How Pair Trading Works")
    st.markdown("Complete documentation: strategy logic, workflow, P&L calculation, optimization, and scheduled tasks.")

    st.subheader("How it works")
    st.markdown("""
- Finds **cointegrated pairs** of stocks whose prices move together long-term
- **Sector constraint**: only stocks from the same sector are paired (bank-bank, IT-IT, etc.)
- When the spread between them diverges from the mean, take a **short-only position** betting it will revert
- Uses **z-score** of the spread to measure divergence
- Position is held until the spread reverts past the exit threshold, stopped out at 2× entry, or times out after 20 trading days
""")

    st.subheader("Entry / Exit Signals")
    st.markdown("""
- **SHORT-only** (z-score > +entry_z): Short Stock1, Buy Stock2 (pair1 overvalued vs pair2)
- **No LONG entries** (z-score < -entry_z): Skipped — historical analysis showed LONG entries have worse risk/reward
- **Exit** (z-score < -exit_z): Mean reversion complete (spread contracted, profit taken)
- **Stop-loss** (z-score > 2.0× entry_z): Divergence too extreme, cut loss
- **Time-limit** (held > 20 trading days): Force-close regardless of z-score
- **Stop-loss cooldown**: After a stop-loss exit, the pair is blocked from re-entry for 24 hours to prevent whipsaw
""")

    st.subheader("Position Sizing")
    st.markdown("""
- **Dynamic sizing** based on signal strength:
  - **Full lot** (1.0×): When `z-score ≥ 1.5 × entry_z` — strong divergence signal
  - **Half lot** (0.5×): When `z-score ≥ entry_z` — borderline signal
- Lot sizes come from Nifty 200 F&O data: e.g., INFY=400, TCS=225, HDFCBANK=650
- **Sector diversification**: Only 1 position per sector allowed — prevents correlated losses
- **MAX_POSITIONS**: 3 concurrent positions total
""")

    st.subheader("P&L Calculation")
    st.markdown("""
- P&L uses actual **lot sizes** × **lot scale** (dynamic sizing factor) from Nifty 200 F&O data
- Entry price = close price when signal triggers, Exit price = close/current price
- Position size = base lot size × dynamic scale (0.5x or 1.0x)
- All trades recorded in `pair_trades` table with per-leg P&L breakdown
""")

    st.subheader("Pair Selection (Top 12)")
    st.markdown(""">
Pairs are selected by a **composite score** combining:
- **Correlation** (25%): Higher = stocks move together more
- **Cointegration p-value** (20%): Lower = more statistically significant relationship
- **P&L in SHORT-only backtest** (35%): Higher = more profitable with current rules
- **Spread stability** (20%): Lower spread volatility = more predictable mean-reversion

### Active Pairs (July 2026)
| Rank | Pair | Corr | P-Val | Backtest P&L | WR |
|------|------|------|-------|-------------|-----|
| 1 | M&M/UNOMINDA | 0.482 | 0.013 | +201,254 | 100% |
| 2 | CDSL/NUVAMA | 0.463 | 0.005 | +54,670 | 100% |
| 3 | BDL/CONCOR | 0.375 | 0.004 | +57,209 | 100% |
| 4 | OIL/ONGC | 0.763 | 0.028 | +36,555 | 83% |
| 5 | HINDALCO/NMDC | 0.575 | 0.003 | +53,957 | 80% |
| 6 | JINDALSTEL/TATASTEEL | 0.594 | 0.012 | +68,032 | 100% |
| 7 | BDL/COCHINSHIP | 0.603 | 0.017 | +74,919 | 100% |
| 8 | M&M/MARUTI | 0.559 | 0.020 | +57,259 | 67% |
| 9 | JIOFIN/KOTAKBANK | 0.421 | 0.009 | +71,483 | 71% |
| 10 | COCHINSHIP/CONCOR | 0.411 | 0.017 | +22,417 | 100% |
| 11 | LICHSGFIN/RECLTD | 0.401 | 0.020 | +62,056 | 80% |
| 12 | KFINTECH/TATAELXSI | 0.328 | 0.026 | -12,603 | 54% |
""")

    st.subheader("Data Sources")
    st.markdown("""
- **yfinance**: Free Yahoo Finance data for NSE India stocks (`.NS` suffix)
- Data frequency: Daily (2 years for discovery), Hourly (synced every 30 min via cron for live scanning)
- Stored in DuckDB with separate databases per strategy:
  - `market_data.duckdb` — shared OHLCV data (all projects)
  - `pairtrading.duckdb` — PairTrading-specific data
  - `swingtrading.duckdb` — SwingPortfolio live state
  - `intratrading.duckdb` — IntraPortfolio live state
- Data accumulates indefinitely — no deletion of bars""")

    st.subheader("Universe")
    st.markdown("""
- **Nifty 200** stocks only (same-sector pairing)
- Sector count: Financial Services (30), Industrials (14), Consumer Cyclical (13), Technology (12), Healthcare (9), etc.
- All stocks have sector metadata in the DuckDB universe table
""")

    st.subheader("Walk-Forward Optimizer")
    st.markdown("""
The optimizer uses a clean walk-forward grid search:
- **Grid**: entry_z ∈ {0.5, 0.75, ..., 3.0} × exit_z ∈ {0.25, 0.5, ..., 2.5} with exit < entry constraint
- **3 walk-forward windows**: Trains on expanding windows, averages scores across all windows
- **ROLL_WIN**: 63 periods (aligned with live scanner) — was 126 (mismatch bug fixed)
- **Metrics**: P&L, Sharpe ratio, win rate, profit factor, max drawdown
- **Stop-loss**: 2.0× entry threshold during simulation
- **Time-limit**: 160 bars (~20 trading days) max hold
- **Auto-maintenance**: `--maintenance` flag re-estimates hedge ratios, purges pairs where cointegration p-value > 0.10
- **P&L scaled** by actual F&O lot sizes (not per-unit)

### Current Optimization Setup (July 2026)
- **Universe**: Nifty 200 → 12 selected pairs (composite score ranking)
- **Timeframe**: Hourly (90-day lookback from DuckDB)
- **ROLL_WIN**: 63 periods (~1.5 weeks)
- **MAX_POSITIONS**: 3 concurrent trades
- **Stop-loss**: 2.0× entry_z
- **Direction**: SHORT-only (LONG entries disabled)
- **Optimization**: Walk-forward grid search for each pair, saved to `pairtrading.duckdb`
""")

    st.subheader("How to Use — Step by Step")
    st.markdown("""
### First time setup (already done — system is live)
1. **Discovery**: Nifty 200 same-sector scan → pairs found (Corr≥0.80, P<0.05)
2. **Selection**: Top 12 pairs selected by composite score (corr, p-value, backtest P&L, spread stability)
3. **Optimization**: Walk-forward grid search with ROLL_WIN=63
4. **Configuration**:
   - SHORT-only entries
   - Stop-loss = 2.0× entry_z, Time-limit = 20 days
   - Dynamic sizing (half/full lot by z-score strength)
   - Sector diversity enforcement
   - 24h stop-loss cooldown

### Daily monitoring
- **Nothing to do manually** — the system is fully automated
- Scheduled tasks run hourly and every 5 minutes (see below)
- Telegram notifications are sent for entries, exits, stop-losses, and timeouts
- End-of-day summary is sent at 15:45 with P&L for all open positions

### Monthly maintenance
Run once a month to keep the pair list healthy:
```
python PairTrading/optimizer.py --maintenance
```
This automatically:
- Re-estimates hedge ratios using 180 days of daily data
- Removes pairs where cointegration p-value > 0.10 (degenerated)
- Discovers up to 5 new same-sector pairs from Nifty 200 to replace purged ones
- Updates thresholds in DuckDB with new hedge ratios

### Re-optimization
After maintenance, re-optimize thresholds:
```
python PairTrading/optimizer.py --months 9
```
Or optimize a single pair:
```
python PairTrading/optimizer.py --pair "NMDC.NS|SAIL.NS" --months 6
```
""")

    st.subheader("What Happens Automatically")
    st.markdown("""
| What | Trigger | Details |
|------|---------|---------|
| Entry scan | Every hour (09:25–15:25 IST) | Fetches hourly OHLCV from DuckDB, checks 12 pairs for SHORT entry signals, caps at 3 concurrent positions, enforces sector diversity |
| Exit check + P&L update | Every 5 minutes (09:00–15:59) | Lightweight run using cached data: checks mean-reversion exits, stop-losses (2.0× entry_z), timeouts (20 days), and cooldowns |
| End-of-day summary | 15:45 daily | Telegram message with P&L for all open positions |
| Position persistence | Every scan cycle | Positions saved to DuckDB `pair_positions` + `pair_trades` tables |
| Pair health check | Monthly (`--maintenance`) | Re-estimates hedge ratios, purges degenerated pairs (coint p > 0.10) |
| Auto-replenish | Monthly (`--maintenance`) | Discovers up to 5 new same-sector Nifty 200 pairs to replace purged ones |
""")

    st.subheader("Scheduled Tasks (Ubuntu Crontab)")
    st.markdown("""
| Task | Time (IST) | Command | Notes |
|------|------------|---------|-------|
| Services start | 08:50 | `/etc/cron.d/ngen26-startup` | Runs as `root` via system cron — avoids `sudo requiretty` issue |
| Pre-market check | 09:10 | `/etc/cron.d/ngen26-startup` (as `kiran`) | Validates Swing + Pair scans ran |
| Hourly Scan | 09:25, 10:25, ... 15:25 | `python PairTrading/live/scan_pairs.py --mode scan` | User crontab |
| Monitor | Every 5 min, 09:00–15:59 | `python PairTrading/live/scan_pairs.py --mode monitor` | User crontab |
| Hourly data sync | Every 30 min, 09:00–15:30 | `python -m common.market_data.sync_job --hourly` | User crontab — feeds `hourly_bars` for PairTrading |
| Daily data sync | Every 30 min, 09:00–15:30 | `python -m common.market_data.sync_job --daily` | User crontab |
| EOD Summary | 15:45 | Sent via Telegram during 15:25 scan | |
| Backtest | 16:15 | `python common/agents/backtest_agent.py` | User crontab |

Manual run (debug):
```
python PairTrading/live/scan_pairs.py --mode scan
python PairTrading/live/scan_pairs.py --mode monitor
```
""")

    st.subheader("Architecture")
    st.markdown("""
```
PairTrading/
├── live/
│   ├── scan_pairs.py        # Scheduled scanner: entry/exit logic, P&L, Telegram
│   ├── pair_scanner.py      # Streamlit dashboard: live positions, charts, trade journal
│   └── cache.py             # PairTradingCache — isolated DuckDB for PT data
├── core/
│   └── pair_discovery.py    # Cointegration discovery, hedge ratio re-estimation
├── reports/
│   ├── pair_trading.py      # Configure Pairs page: backtest, walk-forward, save thresholds
│   ├── discover_pairs.py    # Discover Pairs page: run cointegration scan
│   ├── home.py              # Home dashboard with quick status + maintenance button
│   ├── admin.py             # Telegram config + complete cleanup/reset
│   └── about.py             # This documentation
├── configs/
│   ├── symbols.py           # get_nifty100(), get_nifty200() — universe definitions
│   ├── pair_thresholds.json # Fallback if DuckDB not seeded yet
│   └── telegram_config.py   # Telegram bot configuration
├── optimizer.py             # Walk-forward grid search, --maintenance flag
└── pairtrading.duckdb       # All PT data: positions, trades, signals, thresholds, pairs, config
```

Data persistence (all DuckDB — no CSV/JSON/pickle files):

| Database | Tables | Purpose |
|----------|--------|---------|
| `market_data.duckdb` | `daily_bars`, `hourly_bars`, `bars_5min`, `bars_1min`, `universe`, `sync_log` | Shared OHLCV for all strategies |
| `pairtrading.duckdb` | `pair_positions`, `pair_signals`, `pair_trades`, `pair_signals_today`, `pair_scanner_results`, `pair_thresholds`, `pair_discovered`, `pair_config` | PairTrading-only — positions, trades, history, thresholds, discovered pairs, config |
| `swingtrading.duckdb` | `live_positions`, `positions_history`, `selections`, `trade_log`, `signals` | SwingPortfolio live trading state |
| `intratrading.duckdb` | `live_positions`, `positions_history`, `selections`, `trade_log` | IntraPortfolio live trading state |
""")

    st.subheader("Current Live Configuration")
    st.markdown("""
| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Universe | Nifty 200 (212 stocks) | More pairs, better diversification across 8 sectors |
| Pairs | 12 (top-ranked by composite score) | Quality over quantity — removed 16 negative-P&L pairs |
| Direction | SHORT-only | BACKTEST: +140k P&L, -69k DD (vs both-dir: +84k, -155k DD) |
| ROLL_WIN | 63 periods (~1.5 weeks) | Aligned scanner & optimizer (was mismatched: scanner=63, optimizer=126) |
| MAX_POSITIONS | 3 | Cap for concurrent positions |
| Entry thresholds | Per-pair optimized (0.5–3.0) | Walk-forward grid search with SHORT-only simulation |
| Stop-loss | 2.0× entry_z | Tighter than old 3.0× — cuts drawdown by 55% |
| Stop-loss cooldown | 24 hours | Prevents re-entry after stop-loss (avoids whipsaw) |
| Sector diversity | 1/sector | Prevents correlated losses |
| Dynamic sizing | 0.5× borderline, 1.0× extreme | Saves capital on weak signals |
| Time-limit | 20 trading days | Force-exit stale positions |
""")

    st.subheader("Performance (6-month Walk-Forward Backtest, SHORT-only)")
    st.markdown("""
| Metric | Value |
|--------|-------|
| Total trades (12 pairs, cap=3) | 25 |
| Win Rate | 60.0% |
| Total P&L | +Rs.140,104 |
| Max Drawdown | -Rs.69,721 (-151%) |
| Avg trade P&L | +Rs.5,604 |
| Position sizing | Dynamic (0.5x–1.0x lot) |

Note: Drawdown % appears high because it's relative to a small starting equity peak (+50k at the time). In absolute Rupee terms, the DD is 69k vs 140k profit (≈2:1 ratio).
""")

    st.subheader("Exit Strategy Rules")
    st.markdown("""
Positions are exited on the **first** of these conditions to trigger:

1. **Mean reversion** (z-score crosses exit threshold):
   - SHORT exit: `curr_z ≤ -exit_z` — spread contracted, take profit

2. **Stop-loss** (z-score ≥ 2.0× entry_z): the spread has widened instead of reverted

3. **Time-limit** (held ≥ 20 trading days): force-close regardless of z-score

After a stop-loss exit, the pair enters a **24-hour cooldown** to prevent immediate re-entry.
""")

    st.subheader("Troubleshooting")
    st.markdown("""
**No signals in scanner**: Verify `pair_thresholds` table in `pairtrading.duckdb` has entries
with `hr` (hedge ratio) and `entry_z`/`exit_z`. Run **Backtest & Optimize** to populate.

**Scanner reports 0 tracked pairs**: The scanner reads from DuckDB `pair_thresholds` table.
Run `python PairTrading/live/scan_pairs.py --mode scan` manually to check the error.

**DuckDB errors**: Check the DuckDB file is not locked by another process. Delete the `.duckdb.wal` file
if present. The dashboard and scanner cannot run simultaneously if both are on the same machine.

**Dashboard not reflecting live positions**: Verify the `pair_positions` and `pair_trades` tables exist in
`pairtrading.duckdb`. The dashboard reads from `pair_scanner_results` which is updated on each scan cycle.
""")
