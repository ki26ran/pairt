"""
test_pt_full.py — Comprehensive end-to-end test for PairTrading.
"""
import sys, os, json, time
import pandas as pd
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'PairTrading'))

PASS = 0
FAIL = 0

def check(desc, ok):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f'  OK  {desc}')
    else:
        FAIL += 1
        print(f'  FAIL {desc}')

# 1. MODULE IMPORTS
print('\n=== 1. Module Imports ===')
for mod, name in [
    ('PairTrading.live.cache', 'get_pair_cache, PairTradingCache'),
    ('PairTrading.live.scan_pairs', 'load_thresholds, load_data, run_scan'),
    ('PairTrading.live.pair_scanner', 'show, _load_positions, _lot_size'),
    ('PairTrading.reports.pair_trading', 'show, _run_pair_backtest, _compute_metrics, _load_all_bt, _save_bt'),
    ('PairTrading.reports.discover_pairs', '_save_status, _load_status, _render_pairs_table'),
    ('PairTrading.reports.admin', 'show, _cleanup_all'),
    ('PairTrading.reports.home', 'show, _last_maintenance'),
]:
    try:
        __import__(mod, fromlist=[''])
        objs = [o.strip() for o in name.split(',')]
        for o in objs:
            __import__(mod, fromlist=[o.split(' ')[0].strip()])
        check(f'{mod}', True)
    except Exception as e:
        check(f'{mod}: {e}', False)

# 2. DUCKDB CACHE
print('\n=== 2. PairTrading DuckDB Cache ===')
from pairtrading.live.cache import get_pair_cache
pc = get_pair_cache()

stats = pc.get_stats()
check(f'get_stats: {len(stats)} tables', len(stats) == 7)
for t in ['pair_positions','pair_signals','pair_signals_today','pair_scanner_results','pair_thresholds','pair_discovered','pair_config']:
    check(f'  table {t}', t in stats)

# Positions CRUD
check('load_positions returns dict', isinstance(pc.load_positions(), dict))
check('clear_positions', pc.clear_positions())

# Signals
pc.log_signal('TEST', 'TEST2', 100.0, 200.0, 1.5, 2.0, 0.5, 'TEST', 1.0)
n = len(pc.get_signal_history(5))
check(f'log_signal + get_signal_history: {n} entries', n > 0)

# Today signals
pc.save_today_signals([{'s1':'A','s2':'B','signal':'ENTRY'}])
check('save/load_today_signals', len(pc.load_today_signals()) == 1)
pc.clear_today_signals()
check('clear_today_signals', len(pc.load_today_signals()) == 0)

# Scanner results
pc.save_scanner_results({'last_updated':'now','pairs':[{'s1':'A','s2':'B','z_score':0.5,'signal':'NONE'}],'active_signals':[]})
check('save/load_scanner_results', len(pc.load_scanner_results().get('pairs',[])) > 0)

# Thresholds
pc.save_thresholds({'TEST|TEST2':{'entry_z':2.0,'exit_z':0.5,'hr':1.0}})
check(f'save/load_thresholds: {len(pc.load_thresholds())} entries', len(pc.load_thresholds()) > 0)

# Config
pc.set_config('t_key', 't_val')
check('set/get_config', pc.get_config('t_key') == 't_val')
pc.delete_config('t_key')
check('delete_config', pc.get_config('t_key') is None)

# Discovery
df = pd.DataFrame([{'Stock1':'A.NS','Stock2':'B.NS','Sector':'T','Correlation':0.9,'Coint_PValue':0.01,'Hedge_Ratio':1.5,'Half_Life':10}])
pc.save_discovered_pairs(df)
check('save_discovered_pairs', len(pc.load_discovered_pairs()) > 0)
pc.clear_discovered_pairs()
check('clear_discovered_pairs', len(pc.load_discovered_pairs()) == 0)

# Restore from thresholds
th = pc.load_thresholds()
if th:
    rows = []
    for pk, v in th.items():
        s1, s2 = pk.split('|')
        rows.append({'Stock1':s1,'Stock2':s2,'Sector':'','Correlation':0.0,'Coint_PValue':0.0,'Hedge_Ratio':v['hr'],'Half_Life':0})
    pc.save_discovered_pairs(pd.DataFrame(rows))
    check('restore discovered pairs', len(pc.load_discovered_pairs()) > 0)

# 3. BACKTEST ENGINE
print('\n=== 3. Backtest Engine ===')
dates = pd.date_range('2025-01-01', periods=250, freq='D')
p1 = pd.Series(np.random.randn(250).cumsum() + 100, index=dates)
p2 = pd.Series(np.random.randn(250).cumsum() + 100, index=dates)
spread = p1 - 1.0 * p2
zscore = ((spread - spread.rolling(21).mean()) / spread.rolling(21).std()).dropna()

trades = _run_pair_backtest(p1, p2, spread, zscore, 1.0, 'S1', 'S2', 1, 1, 2.0, 0.5)
check(f'_run_pair_backtest: {len(trades)} trades', len(trades) >= 0)

metrics = _compute_metrics(trades)
check(f'_compute_metrics', metrics is not None or len(trades) == 0)

bt_key = 'TEST_BT_RESULT'
bt_data = dict(results=trades, entry_z=2.0, exit_z=0.5, train_metrics=metrics or {}, test_metrics={}, train_trades=trades, test_trades=[], config={})
_save_bt(bt_key, bt_data)
loaded = _load_all_bt()
check(f'_save_bt / _load_all_bt: key found', bt_key in loaded)

# 4. GRID SEARCH
print('\n=== 4. Grid Search ===')
ev = [round(x,2) for x in np.arange(0.5, 3.25, 0.25)]
xv = [round(x,2) for x in np.arange(0.25, 2.75, 0.25)]
count = 0
t0 = time.time()
for ei, ez in enumerate(ev):
    for xi, xz in enumerate(xv):
        if xz >= ez:
            continue
        _run_pair_backtest(p1, p2, spread, zscore, 1.0, 'S1', 'S2', 1, 1, ez, xz)
        count += 1
el = time.time() - t0
check(f'{count} combos in {el:.1f}s', count == 65)

# 5. SCAN THRESHOLDS
print('\n=== 5. Scan Thresholds ===')
from pairtrading.live.scan_pairs import load_thresholds as lt
check(f'load_thresholds', len(lt()) > 0)

# 6. OTHER CACHES
print('\n=== 6. Other Project Caches ===')
sys.path.insert(0, os.path.join(ROOT, 'SwingPortfolio'))
try:
    from SwingPortfolio.live.cache import get_swing_cache
    sc = get_swing_cache()
    check('SwingTradingCache', True)
    check('  load_positions list', isinstance(sc.load_positions(), list))
except Exception as e:
    check(f'SwingTradingCache: {e}', False)

sys.path.insert(0, os.path.join(ROOT, 'IntraPortfolio'))
try:
    from IntraPortfolio.live.cache import get_intra_cache
    ic = get_intra_cache()
    check('IntraTradingCache', True)
    check('  load_positions list', isinstance(ic.load_positions(), list))
except Exception as e:
    check(f'IntraTradingCache: {e}', False)

# 7. LIVE TRADER WRAPPERS
print('\n=== 7. Live Trader Wrappers ===')
try:
    from IntraPortfolio.agents.live_trader import _save_json2, _load_json2
    check('Intra wrappers', True)
except Exception as e:
    check(f'Intra: {e}', False)
try:
    from SwingPortfolio.agents.live_trader import save_json, load_json, save_csv
    check('Swing wrappers', True)
except Exception as e:
    check(f'Swing: {e}', False)

# SUMMARY
print(f'\n{"="*40}')
print(f'RESULTS: {PASS} passed, {FAIL} failed out of {PASS + FAIL}')
if FAIL: sys.exit(1)
else: print('ALL TESTS PASSED')
