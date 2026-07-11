"""Debug script: check z-scores for all pair thresholds."""
import os, sys, json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE_DIR)
sys.path.insert(0, ROOT)

from common.market_data.cache import get_cache

THRESHOLDS_FILE = os.path.join(BASE_DIR, "configs", "pair_thresholds.json")
with open(THRESHOLDS_FILE) as f:
    thresholds = json.load(f)

ROLL_WIN = 126

all_stocks = set()
for pair_key in thresholds:
    s1, s2 = pair_key.split("|")
    all_stocks.add(s1.replace(".NS", ""))
    all_stocks.add(s2.replace(".NS", ""))
all_stocks = list(all_stocks)
print(f"Total unique stocks: {len(all_stocks)}")

cache = get_cache()
end = datetime.now()
start = end - timedelta(days=90)
raw = cache.get_bulk_multiindex(all_stocks, start.strftime("%Y-%m-%d"),
                                 end.strftime("%Y-%m-%d"), interval="1h")

if raw is None or raw.empty:
    print("ERROR: No data returned from cache")
    exit(1)

print(f"Raw data: {len(raw)} rows, {raw.index[0]} to {raw.index[-1]}")

ts_ist = raw.index
mkt = (ts_ist.hour * 60 + ts_ist.minute >= 9 * 60 + 15) & \
      (ts_ist.hour * 60 + ts_ist.minute <= 15 * 60 + 30)
raw = raw[mkt]
closes = raw["Close"]
if isinstance(closes.columns, pd.MultiIndex):
    closes.columns = [c[0] for c in closes.columns]
closes = closes.ffill()

print(f"After market-hour filter: {len(closes)} rows")
print(f"Columns sample: {closes.columns[:5].tolist()}")

if len(closes) < ROLL_WIN:
    print(f"ERROR: Only {len(closes)} rows, need {ROLL_WIN}")
    exit(1)

tested = 0
crossings = 0
issues = 0

for pair_key, cfg in sorted(thresholds.items()):
    s1, s2 = pair_key.split("|")
    hr_val = cfg.get("hr")
    entry_z = cfg.get("entry_z", 2.0)
    if hr_val is None:
        print(f"  SKIP (no hr): {s1}|{s2}")
        issues += 1
        continue
    if s1 not in closes.columns:
        print(f"  MISSING: {s1} not in data")
        issues += 1
        continue
    if s2 not in closes.columns:
        print(f"  MISSING: {s2} not in data")
        issues += 1
        continue
    pair_data = closes[[s1, s2]].dropna()
    if len(pair_data) < ROLL_WIN:
        print(f"  SHORT: {s1}|{s2} only {len(pair_data)} rows")
        issues += 1
        continue
    p1, p2 = pair_data[s1], pair_data[s2]
    spread = p1 - hr_val * p2
    sm = spread.rolling(ROLL_WIN).mean()
    ss = spread.rolling(ROLL_WIN).std()
    zs = ((spread - sm) / ss).dropna()
    if len(zs) < 2:
        print(f"  NO Z: {s1}|{s2}")
        issues += 1
        continue
    pz = float(zs.iloc[-2])
    _z = float(zs.iloc[-1])
    tested += 1
    entry_hit = ""
    if pz <= -entry_z and _z > -entry_z:
        entry_hit = " <<< ENTRY LONG"
        crossings += 1
    elif pz >= entry_z and _z < entry_z:
        entry_hit = " <<< ENTRY SHORT"
        crossings += 1
    if abs(_z) >= entry_z * 0.5 or entry_hit:
        s1n = s1.replace(".NS", "")
        s2n = s2.replace(".NS", "")
        print(f"  {s1n:12s} {s2n:12s}  prev={pz:+7.3f}  cur={_z:+7.3f}  entry=±{entry_z:.2f}{entry_hit}")

print(f"\n=== Summary ===")
print(f"Total pairs: {len(thresholds)}")
print(f"Tested: {tested}, Skipped/issues: {issues}, Crossings found: {crossings}")
