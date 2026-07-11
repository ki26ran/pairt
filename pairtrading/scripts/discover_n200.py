import sys, os, time, json
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'PairTrading'))
from configs.symbols import get_nifty200
from core.pair_discovery import discover_pairs
from PairTrading.live.cache import get_pair_cache
import pandas as pd

TH_FILE = os.path.join(ROOT, 'PairTrading', 'configs', 'pair_thresholds.json')
print('NYFTY 200 DISCOVERY')
print(f'Universe: {len(get_nifty200())} stocks')
print(f'Criteria: Corr>=0.80, P-val<0.05, same-sector, 2 years')
print()

t0 = time.time()
df = discover_pairs(get_nifty200(), corr_threshold=0.80, pvalue_threshold=0.05, years=2, require_same_sector=True)
elapsed = time.time() - t0

print(f'Time: {elapsed:.0f}s')
print(f'Pairs found: {len(df)}')
print()

# Load existing thresholds
existing = {}
if os.path.exists(TH_FILE):
    with open(TH_FILE) as f:
        existing = json.load(f)

new_pairs_added = 0
for _, r in df.iterrows():
    pk = f"{r['Stock1']}|{r['Stock2']}"
    if pk not in existing:
        new_pairs_added += 1

print(f'Existing thresholds: {len(existing)}')
print(f'New pairs (no thresholds yet): {new_pairs_added}')
print()

if not df.empty:
    print(f'ALL PAIRS:')
    print(f'{"Sector":25s} {"Stock1":18s} {"Stock2":18s} {"r":8s} {"p-val":8s} {"HR":10s} {"HL":6s} {"Status":10s}')
    print('-'*95)
    for _, r in df.iterrows():
        s1 = r['Stock1'].replace('.NS','')
        s2 = r['Stock2'].replace('.NS','')
        pk = f"{r['Stock1']}|{r['Stock2']}"
        status = 'NEW' if pk not in existing else 'HAS TH'
        print(f'{r["Sector"]:25s} {s1:18s} {s2:18s} {r["Correlation"]:.4f}  {r["Coint_PValue"]:.4f}  {r["Hedge_Ratio"]:8.4f}  {r["Half_Life"]:4.1f}  {status:10s}')
    
    print(f'\nSector breakdown:')
    for s in sorted(df['Sector'].unique()):
        cnt = len(df[df['Sector'] == s])
        old = sum(1 for _, r in df[df['Sector'] == s].iterrows() if f"{r['Stock1']}|{r['Stock2']}" in existing)
        print(f'  {s:25s}: {cnt:2d} pairs ({old} existing)')
    
    print(f'\nNew pairs needing thresholds:')
    for _, r in df.iterrows():
        pk = f"{r['Stock1']}|{r['Stock2']}"
        if pk not in existing:
            s1 = r['Stock1'].replace('.NS','')
            s2 = r['Stock2'].replace('.NS','')
            print(f'  {r["Sector"]:25s} {s1:18s} / {s2:18s}  r={r["Correlation"]:.3f}  hr={r["Hedge_Ratio"]:.4f}')
    
    # Save to DuckDB
    pc = get_pair_cache()
    pc.save_discovered_pairs(df)
    print(f'\nSaved {len(df)} pairs to pairtrading.duckdb (pair_discovered table)')
else:
    print('No pairs found. Try lowering correlation threshold to 0.70.')
