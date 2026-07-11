"""
Re-optimize all pairs using HOURLY data (60 days).
Saves new thresholds optimized for the scanner's actual timeframe.
"""
import sys, os, json, time
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

TH_FILE = os.path.join(ROOT, 'PairTrading', 'configs', 'pair_thresholds.json')

# Load existing thresholds + discovered pairs
existing_th = {}
if os.path.exists(TH_FILE):
    with open(TH_FILE, encoding='utf-8') as f:
        existing_th = json.load(f)

# Get all pairs from discovered table
pair_cache = get_pair_cache()
discovered = pair_cache.load_discovered_pairs()

# Merge: discovered pairs get hr from discovery, thresholds get existing thresholds
all_pairs = {}
for d in discovered:
    pk = f"{d['Stock1']}|{d['Stock2']}"
    hr = d.get('Hedge_Ratio', 1.0)
    if pk in existing_th:
        all_pairs[pk] = existing_th[pk]
    else:
        all_pairs[pk] = {'entry_z': 2.0, 'exit_z': 0.5, 'hr': hr}

print(f'Found {len(all_pairs)} total pairs ({len(existing_th)} existing + {len(all_pairs)-len(existing_th)} new)')
print()

cache = get_cache()
pair_cache = get_pair_cache()

def load_hourly(s1, s2, days=60):
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
    if dfh.empty: return None, None, None
    p1 = dfh[dfh['ticker']==s1c].set_index('datetime_ist')['close']
    p2 = dfh[dfh['ticker']==s2c].set_index('datetime_ist')['close']
    ch = pd.concat([p1, p2], axis=1).dropna()
    if ch.empty or len(ch) < 20: return None, None, None
    ts = pd.to_datetime(ch.index)
    mkt = (ts.hour*60+ts.minute >= 555) & (ts.hour*60+ts.minute <= 930)
    ch = ch[mkt]
    ch.index = ts[mkt]
    if ch.empty or len(ch) < 20: return None, None, None
    return ch.iloc[:,0], ch.iloc[:,1], ch

print('Re-optimizing all pairs for HOURLY timeframe...')
print()

# Build lot size lookup
_LOT_CACHE = {e["Symbol"]: e["LotSize"] for e in get_nifty200()}

new_th = {}
for pair_key, th in sorted(all_pairs.items()):
    s1, s2 = pair_key.split('|')
    hr = th['hr']
    l1, l2 = s1.replace('.NS',''), s2.replace('.NS','')
    lot1 = _LOT_CACHE.get(s1, 1)
    lot2 = _LOT_CACHE.get(s2, 1)
    
    print(f'  {l1:15s} / {l2:15s}  hr={hr:.4f}  ', end='', flush=True)
    
    bp1, bp2, ch = load_hourly(s1, s2, days=60)
    if bp1 is None:
        print('NO DATA')
        continue
    
    # Compute z-score
    spread = bp1 - hr * bp2
    sm = spread.rolling(63).mean()
    ss = spread.rolling(63).std()
    zs = ((spread - sm) / ss).dropna()
    if len(zs) < 10:
        print('NO Z-SCORE')
        continue
    
    # Grid search: entry_z 0.5-3.0, exit_z 0.25-2.5
    entry_vals = [round(x,2) for x in np.arange(0.5, 3.25, 0.25)]
    exit_vals = [round(x,2) for x in np.arange(0.25, 2.75, 0.25)]
    best_score, best_entry, best_exit, best_trades = -999, None, None, []
    
    for ez in entry_vals:
        for xz in exit_vals:
            if xz >= ez: continue
            trades = _run_pair_backtest(bp1, bp2, spread, zs, hr, l1, l2, lot1, lot2, ez, xz)
            m = _compute_metrics(trades)
            score = m['total_pnl'] if m and m['total'] >= 1 else -999
            if score > best_score:
                best_score = score
                best_entry = ez
                best_exit = xz
                best_trades = trades
    
    if best_entry is not None:
        new_th[pair_key] = {'entry_z': best_entry, 'exit_z': best_exit, 'hr': hr}
        m = _compute_metrics(best_trades) or {}
        print(f'±{best_entry:.2f}/{best_exit:.2f}  '
              f'trades={m.get("total",0)}  pnl={m.get("total_pnl",0):+.0f}  '
              f'wr={m.get("win_rate",0):.0f}%')
    else:
        print('NO OPTIMUM')

# Save
if new_th:
    with open(TH_FILE, 'w') as f:
        json.dump(new_th, f, indent=2)
    pair_cache.save_thresholds(new_th)
    
    print(f'\nSaved {len(new_th)} optimized thresholds')
    print()
    
    # Count new vs existing
    new_count = sum(1 for pk in new_th if pk not in existing_th)
    existing_count = len(new_th) - new_count
    print(f'{existing_count} existing + {new_count} new = {len(new_th)} total pairs')
    
    # Estimate trades with new thresholds
    print(f'\nEstimated trade count with NEW thresholds:')
    total_trades = 0
    grand_pnl = 0
    for pk in sorted(new_th):
        s1, s2 = pk.split('|')
        hr = new_th[pk]['hr']
        ez = new_th[pk]['entry_z']
        xz = new_th[pk]['exit_z']
        bp1, bp2, _ = load_hourly(s1, s2, days=60)
        if bp1 is None: continue
        spread = bp1 - hr * bp2
        sm = spread.rolling(63).mean()
        ss = spread.rolling(63).std()
        zs = ((spread - sm) / ss).dropna()
        if len(zs) < 10: continue
        trades = _run_pair_backtest(bp1, bp2, spread, zs, hr, s1.replace('.NS',''), s2.replace('.NS',''), lot1, lot2, ez, xz)
        total_trades += len(trades)
        m = _compute_metrics(trades) or {}
        grand_pnl += m.get('total_pnl', 0)
        new_flag = ' [NEW]' if pk not in existing_th else ''
        print(f'  {s1.replace(".NS",""):15s}/{s2.replace(".NS",""):15s}  '
              f'±{ez:.2f}/{xz:.2f}  {len(trades):2d} trades  pnl={m.get("total_pnl",0):+8.0f}{new_flag}')
    
    print(f'\n  TOTAL: {total_trades} trades, Rs.{grand_pnl:+,.0f} P&L across {len(new_th)} pairs')
    print(f'  Avg trades/pair: {total_trades/len(new_th):.1f}')
else:
    print('No pairs re-optimized')
