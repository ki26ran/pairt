"""
Analyze all 14 pairs over 60 days of hourly data.
Shows weekly P&L, trade counts, capital needs, win rate.
"""
import sys, os, json
_me = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(_me))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'PairTrading'))
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from common.market_data.cache import get_cache
from pairtrading.reports.pair_trading import _run_pair_backtest, _compute_metrics
from pairtrading.live.cache import get_pair_cache
from pairtrading.configs.symbols import get_nifty200

pd.set_option('display.max_columns', None)
pd.set_option('display.width', 200)

THRESHOLDS_FILE = os.path.join(ROOT, 'PairTrading', 'configs', 'pair_thresholds.json')
with open(THRESHOLDS_FILE, encoding='utf-8-sig') as f:
    all_th = json.load(f)

# Build lot size lookup
_lot_map = {}
for e in get_nifty200():
    _lot_map[e['Symbol']] = e['LotSize']

def lot_size(s):
    return _lot_map.get(s, 1)

def load_hourly_data(s1, s2, days=60):
    cache = get_cache()
    end = datetime.now()
    start = end - timedelta(days=days)
    s1c, s2c = s1.replace('.NS',''), s2.replace('.NS','')
    import duckdb as _duckdb
    con = _duckdb.connect(cache.db_path, read_only=True)
    query = f"""SELECT ticker, datetime_ist, close FROM hourly_bars
        WHERE ticker IN ('{s1c}', '{s2c}')
        AND datetime_ist >= '{start.strftime("%Y-%m-%d")}' AND datetime_ist < '{end.strftime("%Y-%m-%d")}'
        ORDER BY datetime_ist"""
    dfh = con.execute(query).df()
    con.close()
    if dfh.empty:
        return None, None, None
    p1h = dfh[dfh['ticker']==s1c].set_index('datetime_ist')['close']
    p2h = dfh[dfh['ticker']==s2c].set_index('datetime_ist')['close']
    ch = pd.concat([p1h, p2h], axis=1).dropna()
    if ch.empty or len(ch) < 20:
        return None, None, None
    # Filter market hours (index is already IST)
    ts = pd.to_datetime(ch.index)
    mkt = (ts.hour*60+ts.minute >= 555) & (ts.hour*60+ts.minute <= 930)
    ch = ch[mkt]
    ch.index = ts[mkt]
    if ch.empty or len(ch) < 20:
        return None, None, None
    bp1, bp2 = ch.iloc[:,0], ch.iloc[:,1]
    return bp1, bp2, pd.concat([bp1, bp2], axis=1)

print('='*110)
print('PAIRTRADING — HOURLY BACKTEST ANALYSIS (60 days)')
print('='*110)

all_rows = []
all_trades_by_pair = {}
total_capital = 0
lot_costs = {}

for pair_key, th in sorted(all_th.items()):
    s1, s2 = pair_key.split('|')
    hr = th['hr']
    entry_z = th['entry_z']
    exit_z = th['exit_z']
    lot1, lot2 = lot_size(s1), lot_size(s2)
    l1, l2 = s1.replace('.NS',''), s2.replace('.NS','')

    bp1, bp2, ch = load_hourly_data(s1, s2, days=60)
    if bp1 is None:
        print(f'  {l1:15s} / {l2:15s}  SKIP — no hourly data')
        continue

    # Compute z-score
    spread = bp1 - hr * bp2
    sm = spread.rolling(126).mean()
    ss = spread.rolling(126).std()
    zs = ((spread - sm) / ss).dropna()
    if len(zs) < 10:
        print(f'  {l1:15s} / {l2:15s}  SKIP — too few z-scores ({len(zs)})')
        continue

    # Run backtest
    trades = _run_pair_backtest(bp1, bp2, spread, zs, hr, l1, l2, lot1, lot2, entry_z, exit_z)
    metrics = _compute_metrics(trades) or {}

    # Calculate notional capital needed (sum of entry notional)
    pair_capital = 0
    for t in trades:
        pair_capital += abs(t['s1_entry'] * lot1) + abs(t['s2_entry'] * lot2)
    avg_capital = pair_capital / len(trades) if trades else 0

    lot_costs[pair_key] = {'lot1': lot1, 'lot2': lot2, 'avg_capital': avg_capital}
    all_trades_by_pair[pair_key] = trades

    # Week-by-week breakdown
    weekly = {}
    for t in trades:
        w = pd.Timestamp(t['Entry']).strftime('%Y-W%U')
        weekly.setdefault(w, []).append(t)

    print(f'\n  {l1:15s} / {l2:15s}  '
          f'thr=±{entry_z:.2f}/{exit_z:.2f}  '
          f'trades={metrics.get("total",0):2d}  '
          f'win={metrics.get("win_rate",0):.0f}%  '
          f'pnl={metrics.get("total_pnl",0):+9.1f}  '
          f'pf={metrics.get("profit_factor",0):.1f}  '
          f'dd={metrics.get("max_dd",0):.0f}  '
          f'lot=({lot1},{lot2})  '
          f'avg_cap=Rs.{avg_capital:,.0f}')
    for w in sorted(weekly):
        wt = weekly[w]
        w_pnl = sum(t['P&L'] for t in wt)
        w_wins = sum(1 for t in wt if t['P&L'] > 0)
        print(f'    {w}: {len(wt)} trades, {w_wins}/{len(wt)} wins, P&L={w_pnl:+8.1f}')

    all_rows.append({
        'Pair': f'{l1}/{l2}', 'Entry Z': entry_z, 'Exit Z': exit_z,
        'Trades': metrics.get("total",0), 'Win Rate': f'{metrics.get("win_rate",0):.0f}%',
        'Total P&L': round(metrics.get("total_pnl",0), 0),
        'Profit Factor': round(metrics.get("profit_factor",0), 1) if metrics.get("profit_factor",float('inf')) != float('inf') else 'inf',
        'Max DD': round(metrics.get("max_dd",0), 0),
        'Lot1': lot1, 'Lot2': lot2,
        'Avg Capital': round(avg_capital, 0),
    })

# ===== SUMMARY =====
print('\n' + '='*110)
print('SUMMARY — ALL 14 PAIRS')
print('='*110)

df = pd.DataFrame(all_rows)
total_trades = df['Trades'].sum()
total_pnl = df['Total P&L'].sum()
avg_win_rate = df['Trades'].sum() > 0 and sum(r['Trades'] * float(r['Win Rate'].replace('%','')) for _, r in df.iterrows()) / total_trades if total_trades > 0 else 0
max_capital = df['Avg Capital'].max() if len(df) > 0 else 0
total_capital_need = max_capital * 3  # 3 concurrent positions max

print(f'\n  Total trades (all pairs):   {total_trades}')
print(f'  Overall P&L:               Rs.{total_pnl:+,.0f}')
print(f'  Avg win rate (weighted):   {avg_win_rate:.1f}%')
print(f'  Max single-pair capital:   Rs.{max_capital:,.0f}')
print(f'  Est. total capital needed: Rs.{total_capital_need:,.0f} (3x max position)')
print(f'  Profitable pairs:          {(df["Total P&L"] > 0).sum()}/{len(df)}')
print(f'  Avg trades per pair:       {total_trades/len(df):.1f}')
print()

# Weekly breakdown across all pairs
print('WEEKLY BREAKDOWN (all pairs combined)')
print('-'*80)
weekly_all = {}
for pk, trades in all_trades_by_pair.items():
    for t in trades:
        w = pd.Timestamp(t['Entry']).strftime('%Y-W%U')
        weekly_all.setdefault(w, []).append(t)

grand_total_pnl = 0
for w in sorted(weekly_all):
    wt = weekly_all[w]
    w_pnl = sum(t['P&L'] for t in wt)
    w_wins = sum(1 for t in wt if t['P&L'] > 0)
    w_losses = len(wt) - w_wins
    avg_pnl = w_pnl / len(wt) if wt else 0
    grand_total_pnl += w_pnl
    print(f'  {w}: {len(wt):3d} trades ({w_wins}W/{w_losses}L)  '
          f'win={w_wins/len(wt)*100:.0f}%  '
          f'P&L={w_pnl:+8.0f}  '
          f'avg={avg_pnl:+6.0f}  '
          f'cumulative={grand_total_pnl:+8.0f}')

print(f'\n  TOTAL: {grand_total_pnl:+,.0f} across all weeks')

# Capital analysis
print(f'\nCAPITAL ANALYSIS')
print('-'*80)
print(f'  Max single position cost:  Rs.{max_capital:,.0f}')
print(f'  With 3 concurrent (cap):   Rs.{max_capital * 3:,.0f}')
print(f'  Buffer (20%):              Rs.{max_capital * 3 * 0.2:,.0f}')
print(f'  Recommended capital:       Rs.{max_capital * 3 * 1.2:,.0f}')
print()

# Display table
print('PAIR-LEVEL SUMMARY')
print('-'*110)
print(df.to_string(index=False))
