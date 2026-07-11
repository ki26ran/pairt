import sys, os, time
_me = os.path.dirname(os.path.abspath(__file__))
# ROOT is PairTrading/scripts/../../  = parent of PairTrading
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_me)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'PairTrading'))
from common.market_data.cache import get_cache
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

s1, s2 = 'HDFCBANK.NS', 'HDFCLIFE.NS'
hr = 1.1919
cache = get_cache()
end_dt = datetime.now()
start_dt = end_dt - timedelta(days=60)
s1c, s2c = s1.replace('.NS',''), s2.replace('.NS','')

t0 = time.time()
try:
    con = cache._db_read()
    print(f'Step 1: _db_read() = {time.time()-t0:.3f}s')
except Exception as e:
    print(f'Step 1 FAILED: {e}'); sys.exit(1)

t0 = time.time()
query = f"""SELECT ticker, datetime_ist, close FROM hourly_bars
    WHERE ticker IN ('{s1c}', '{s2c}')
    AND datetime_ist >= '{start_dt.strftime("%Y-%m-%d")}' AND datetime_ist < '{end_dt.strftime("%Y-%m-%d")}'
    ORDER BY datetime_ist"""
dfh = con.execute(query).df()
con.close()
print(f'Step 2: SQL query = {time.time()-t0:.3f}s, rows={len(dfh)}')

if dfh.empty:
    print('NO DATA FOUND')
    sys.exit(1)

t0 = time.time()
p1h = dfh[dfh['ticker'] == s1c].set_index('datetime_ist')['close']
p2h = dfh[dfh['ticker'] == s2c].set_index('datetime_ist')['close']
p1h.index = pd.to_datetime(p1h.index, errors='coerce')
p2h.index = pd.to_datetime(p2h.index, errors='coerce')
p1h = p1h.dropna(); p2h = p2h.dropna()
ch = pd.concat([p1h, p2h], axis=1).dropna()
print(f'Step 3: Prepare = {time.time()-t0:.3f}s, candles={len(ch)}')

t0 = time.time()
ts_h = pd.to_datetime(ch.index, utc=True)
ts_ist = ts_h.tz_convert('Asia/Kolkata')
mkt = (ts_ist.hour * 60 + ts_ist.minute >= 555) & (ts_ist.hour * 60 + ts_ist.minute <= 930)
ch = ch[mkt]
ch.index = ts_ist[mkt].tz_localize(None)
bp1, bp2 = ch.iloc[:, 0], ch.iloc[:, 1]
print(f'Step 4: Market filter = {time.time()-t0:.3f}s, filtered={len(ch)}')

t0 = time.time()
bspread = bp1 - hr * bp2
bmean = bspread.rolling(126).mean()
bstd = bspread.rolling(126).std()
bz = ((bspread - bmean) / bstd).dropna()
print(f'Step 5: Z-score = {time.time()-t0:.3f}s, z_len={len(bz)}')

from pairtrading.reports.pair_trading import _run_pair_backtest, _compute_metrics
t0 = time.time()
trades = _run_pair_backtest(bp1, bp2, bspread, bz, hr, s1c, s2c, 1, 1, 1.25, 1.0)
print(f'Step 6: Single backtest = {time.time()-t0:.3f}s, trades={len(trades)}')

print('Grid search (65 combos)...')
entry_vals = [round(x,2) for x in np.arange(0.5, 3.25, 0.25)]
exit_vals = [round(x,2) for x in np.arange(0.25, 2.75, 0.25)]
count = 0; t0 = time.time()
for ez in entry_vals:
    for xz in exit_vals:
        if xz >= ez: continue
        _run_pair_backtest(bp1, bp2, bspread, bz, hr, s1c, s2c, 1, 1, ez, xz)
        count += 1
print(f'Step 7: {count} combos in {time.time()-t0:.3f}s')
print('ALL STEPS PASSED')
