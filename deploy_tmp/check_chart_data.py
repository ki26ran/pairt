"""Check why pair charts show no data."""
import sys, os, pandas as pd
sys.path[:0] = ["/opt/pairt", "/opt/pairt/pairtrading"]
os.environ["APP_ENV"] = "prod"
from common.market_data.cache import get_cache

cache = get_cache()
df = cache.get_bulk_multiindex(["LUPIN.NS", "TORNTPHARM.NS"], 
                                "2026-07-10", "2026-07-20", interval="1h")
if df.empty:
    print("Empty DataFrame")
else:
    print(f"Shape: {df.shape}")
    if isinstance(df.columns, pd.MultiIndex):
        print(f"Level 0 (Price): {df.columns.get_level_values(0).unique().tolist()}")
        print(f"Level 1 (Tickers): {df.columns.get_level_values(1).unique().tolist()}")
        for t in df.columns.get_level_values(1).unique():
            c = df["Close"][t] if "Close" in df.columns.get_level_values(0) else None
            if c is not None:
                print(f"  {t}: {len(c.dropna())} close candles, range {c.index[0]} to {c.index[-1]}")
