"""
Compare MAX_POSITIONS=3 vs MAX_POSITIONS=5 on 43-pair hourly backtest.
"""
import sys, os, json, math
_me = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(_me))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'PairTrading'))
import pandas as pd
import numpy as np
import numpy as np
from datetime import datetime, timedelta
from common.market_data.cache import get_cache
from pairtrading.reports.pair_trading import _run_pair_backtest, _compute_metrics
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

# Generate all trades (sorted by entry time)
print('Generating all trades for 43 pairs...')
all_trades = []
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
    for t in trades:
        t['pair_key'] = pk
        t['lot1'] = lot1
        t['lot2'] = lot2
        t['capital'] = abs(t['s1_entry']*lot1) + abs(t['s2_entry']*lot2)
        all_trades.append(t)

all_trades.sort(key=lambda t: t['Entry'])
print(f'Total trades generated: {len(all_trades)}')

def simulate(max_pos):
    """Simulate trading with max concurrent position limit."""
    positions = []  # list of (exit_timestamp, capital)
    realized = []
    skipped = 0
    
    for t in all_trades:
        positions = [p for p in positions if p[0] > t['Entry']]
        if len(positions) < max_pos:
            positions.append((t['Exit'], t['capital']))
            realized.append(t)
        else:
            skipped += 1
    
    pnl = sum(t['P&L'] for t in realized)
    wins = sum(1 for t in realized if t['P&L'] > 0)
    avg_cap = np.mean([t['capital'] for t in realized]) if realized else 0
    max_cap = avg_cap * max_pos
    
    weekly = {}
    for t in realized:
        w = pd.Timestamp(t['Entry']).strftime('%Y-W%W')
        weekly.setdefault(w, []).append(t)
    
    return {
        'total': len(realized),
        'skipped': skipped,
        'pnl': pnl,
        'wins': wins,
        'win_rate': wins/len(realized)*100 if realized else 0,
        'max_capital': max_cap,
        'weekly': {w: sum(t['P&L'] for t in ts) for w, ts in weekly.items()}
    }

print('\n' + '='*90)
print('COMPARISON: 3 vs 5 MAX CONCURRENT POSITIONS')
print('='*90)

results = {}
for mp in [3, 5]:
    r = simulate(mp)
    results[mp] = r
    print(f'\n  MAX_POSITIONS = {mp}:')
    print(f'    Total trades executed:  {r["total"]} ({r["skipped"]} skipped due to capacity)')
    print(f'    Total P&L:              Rs.{r["pnl"]:+,.0f}')
    print(f'    Win Rate:               {r["win_rate"]:.1f}%')
    print(f'    Max capital deployed:   Rs.{r["max_capital"]:,.0f}')
    print(f'    ROC (60 days):          {r["pnl"]/r["max_capital"]*100:.1f}%' if r['max_capital'] else '')
    
    print(f'    Weekly P&L:')
    for w in sorted(r['weekly']):
        print(f'      {w}: Rs.{r["weekly"][w]:+,.0f}')

# Side-by-side comparison
print(f'\n{"="*90}')
print(f'DIRECT COMPARISON')
print(f'{"Metric":40s} {"MAX=3":>20s} {"MAX=5":>20s}')
print(f'{"-"*90}')
r3, r5 = results[3], results[5]
diff_pnl = r5['pnl'] - r3['pnl']
diff_trades = r5['total'] - r3['total']
diff_cap = r5['max_capital'] - r3['max_capital']
p3, p5 = r3['pnl'], r5['pnl']
w3, w5 = r3['win_rate'], r5['win_rate']
c3, c5 = r3['max_capital'], r5['max_capital']
sk3, sk5 = r3['skipped'], r5['skipped']
t3, t5 = r3['total'], r5['total']
print(f'{"Trades executed":40s} {t3:>20d} {t5:>20d}')
print(f'{"Trades skipped (capacity)":40s} {sk3:>20d} {sk5:>20d}')
print(f'{"Total P&L":40s} {"Rs."+f"{p3:+,.0f}":>20s} {"Rs."+f"{p5:+,.0f}":>20s}')
print(f'{"P&L difference":40s} {"":>20s} {"Rs."+f"{diff_pnl:+,.0f}":>20s}')
print(f'{"Win Rate":40s} {f"{w3:.1f}%":>20s} {f"{w5:.1f}%":>20s}')
print(f'{"Max capital deployed":40s} {"Rs."+f"{c3:,.0f}":>20s} {"Rs."+f"{c5:,.0f}":>20s}')
print(f'{"Return on Capital (60d)":40s} {f"{p3/c3*100:.1f}%":>20s} {f"{p5/c5*100:.1f}%":>20s}')

print(f'\n{"="*90}')
print(f'RECOMMENDATION')
print(f'{"="*90}')
if diff_pnl > 0:
    pct_improvement = (diff_pnl / p3) * 100 if p3 != 0 else 0
    extra_cap_pct = (diff_cap / c3) * 100 if c3 else 0
    print(f'  MAX=5 adds {diff_trades} more trades (+Rs.{diff_pnl:+,.0f}, {pct_improvement:.0f}% more P&L)')
    print(f'  but requires {extra_cap_pct:.0f}% more capital (Rs.{diff_cap:,.0f} extra)')
    print(f'  ROC is {"better" if r5["pnl"]/r5["max_capital"] > r3["pnl"]/r3["max_capital"] else "similar"} with MAX=5')
else:
    print(f'  MAX=3 performs better for this period')

# Weekly comparison
print(f'\n{"="*90}')
print(f'WEEKLY COMPARISON')
print(f'{"Week":15s} {"MAX=3 P&L":>15s} {"MAX=5 P&L":>15s} {"Diff":>15s}')
print(f'{"-"*60}')
all_weeks = sorted(set(list(r3['weekly'].keys()) + list(r5['weekly'].keys())))
for w in all_weeks:
    w3 = r3['weekly'].get(w, 0)
    w5 = r5['weekly'].get(w, 0)
    print(f'{w:15s} {f"Rs.{w3:+,.0f}":>15s} {f"Rs.{w5:+,.0f}":>15s} {f"Rs.{w5-w3:+,.0f}":>15s}')
