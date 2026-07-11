"""
Full analysis with ROLL_WIN=63. Shows all weeks in range.
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
from PairTrading.reports.pair_trading import _run_pair_backtest, _compute_metrics
from configs.symbols import get_nifty200

TH_FILE = os.path.join(ROOT, 'PairTrading', 'configs', 'pair_thresholds.json')
with open(TH_FILE) as f:
    all_th = json.load(f)

_lot_map = {e['Symbol']: e['LotSize'] for e in get_nifty200()}
def lot_size(s): return _lot_map.get(s, 1)

cache = get_cache()

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
    if ch.empty: return None, None, None
    ts = pd.to_datetime(ch.index)
    mkt = (ts.hour*60+ts.minute >= 555) & (ts.hour*60+ts.minute <= 930)
    ch = ch[mkt]
    ch.index = ts[mkt]
    if ch.empty: return None, None, None
    return ch.iloc[:,0], ch.iloc[:,1], ch

print('='*100)
print('ANALYSIS: ROLL_WIN=63, 60-day hourly')
print('='*100)

all_trades = {}
grand_pnl = 0
total_trades = 0

for pk, th in sorted(all_th.items()):
    s1, s2 = pk.split('|')
    hr = th['hr']; ez = th['entry_z']; xz = th['exit_z']
    lot1, lot2 = lot_size(s1), lot_size(s2)
    l1, l2 = s1.replace('.NS',''), s2.replace('.NS','')
    bp1, bp2, ch = load_hourly(s1, s2)
    if bp1 is None: continue
    spread = bp1 - hr * bp2
    sm = spread.rolling(63).mean(); ss = spread.rolling(63).std()
    zs = ((spread - sm) / ss).dropna()
    if len(zs) < 5: continue
    trades = _run_pair_backtest(bp1, bp2, spread, zs, hr, l1, l2, lot1, lot2, ez, xz)
    m = _compute_metrics(trades) or {}
    all_trades[pk] = trades
    total_trades += len(trades)
    pair_pnl = sum(t['P&L'] for t in trades)
    grand_pnl += pair_pnl
    avg_cap = sum(abs(t['s1_entry']*lot1)+abs(t['s2_entry']*lot2) for t in trades) / max(len(trades),1)
    print(f'  {l1:15s}/{l2:15s}  '
          f'{len(trades):2d} trades  '
          f'pnl={pair_pnl:+8.0f}  '
          f'wr={m.get("win_rate",0):.0f}%  '
          f'pf={m.get("profit_factor",0):.1f}  '
          f'cap=Rs.{avg_cap:,.0f}')

# Weekly (all weeks, with zeros)
print(f'\nWEEKLY BREAKDOWN (all pairs combined)')
print('-'*80)
weekly = {}
for pk, trades in all_trades.items():
    for t in trades:
        w = pd.Timestamp(t['Entry']).strftime('%Y-W%W')
        weekly.setdefault(w, []).append(t)

# Show all weeks from start to end
end = datetime.now()
start = end - timedelta(days=60)
all_weeks = pd.date_range(start, end, freq='W-MON')
cumulative = 0
for wk_start in all_weeks:
    w = wk_start.strftime('%Y-W%W')
    wt = weekly.get(w, [])
    w_pnl = sum(t['P&L'] for t in wt)
    w_wins = sum(1 for t in wt if t['P&L'] > 0)
    cumulative += w_pnl
    label = f'{wk_start.strftime("%b %d")}'
    if wt:
        print(f'  {label:10s} {w}: {len(wt):2d} trades ({w_wins}W/{len(wt)-w_wins}L)  '
              f'wr={w_wins/max(len(wt),1)*100:.0f}%  '
              f'pnl={w_pnl:+8.0f}  cum={cumulative:+8.0f}')
    else:
        print(f'  {label:10s} {w}:  0 trades                          '
              f'pnl=     +0  cum={cumulative:+8.0f}')

print(f'\nTOTAL: {total_trades} trades, P&L={grand_pnl:+,.0f}')

# Capital
max_cap = 0
for pk in all_th:
    s1, s2 = pk.split('|')
    lot1, lot2 = lot_size(s1), lot_size(s2)
    bp1, bp2, _ = load_hourly(s1, s2)
    if bp1 is None: continue
    _, _, ch = load_hourly(s1, s2)
    if ch is None: continue
    avg_price = (ch.iloc[:,0].mean() * lot1 + ch.iloc[:,1].mean() * lot2)
    max_cap = max(max_cap, avg_price)

print(f'\nCAPITAL:')
print(f'  Max position cost:      Rs.{max_cap:,.0f}')
print(f'  3 concurrent (cap):     Rs.{max_cap * 3:,.0f}')
print(f'  With 20% buffer:        Rs.{max_cap * 3 * 1.2:,.0f}')
print(f'  Recommended:            Rs.{max_cap * 3 * 1.2:,.0f}')
