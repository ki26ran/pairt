import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'PairTrading'))
from common.market_data.cache import get_cache
from datetime import datetime, timedelta
import pandas as pd

s1, s2 = 'HDFCBANK.NS', 'HDFCLIFE.NS'
hr = 1.1919
cache = get_cache()
end_dt = datetime.now()
start_dt = end_dt - timedelta(days=60)
s1c, s2c = s1.replace('.NS',''), s2.replace('.NS','')

con = cache._db_read()
query = f"""SELECT ticker, datetime_ist, close FROM hourly_bars
    WHERE ticker IN ('{s1c}', '{s2c}')
    AND datetime_ist >= '{start_dt.strftime("%Y-%m-%d")}' AND datetime_ist < '{end_dt.strftime("%Y-%m-%d")}'
    ORDER BY datetime_ist"""
dfh = con.execute(query).df()
con.close()
print(f'Raw data: {len(dfh)} rows')

p1h = dfh[dfh['ticker']==s1c].set_index('datetime_ist')['close']
p2h = dfh[dfh['ticker']==s2c].set_index('datetime_ist')['close']
ch = pd.concat([p1h, p2h], axis=1).dropna()

# OLD approach (broken)
ts_h = pd.to_datetime(ch.index, utc=True)
ts_ist = ts_h.tz_convert('Asia/Kolkata')
mkt_old = (ts_ist.hour*60+ts_ist.minute >= 555) & (ts_ist.hour*60+ts_ist.minute <= 930)
print(f'OLD tz: {mkt_old.sum()} market candles (of {len(ch)})')

# NEW approach (fixed)
ts_local = pd.to_datetime(ch.index)
mkt_new = (ts_local.hour*60+ts_local.minute >= 555) & (ts_local.hour*60+ts_local.minute <= 930)
print(f'NEW tz: {mkt_new.sum()} market candles (of {len(ch)})')

if mkt_new.sum() > mkt_old.sum():
    print('FIX WORKS: NEW captures more market hours candles')
elif mkt_old.sum() == mkt_new.sum():
    print('SAME RESULTS (data might not have market hours overlap issue)')
else:
    print('OLD was better (unexpected)')
