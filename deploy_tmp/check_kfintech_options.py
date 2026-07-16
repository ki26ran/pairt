import duckdb, os
os.environ["APP_ENV"] = "prod"

# Check pairt's market data DB (symlinked to ngen26)
con = duckdb.connect("/opt/pairt/data/market_data.duckdb")
r = con.execute("SELECT COUNT(*) FROM nfo_symbols WHERE symbol = 'KFINTECH'").fetchone()
print(f"KFINTECH nfo_symbols: {r[0]} rows")
if r[0] > 0:
    r2 = con.execute("SELECT DISTINCT option_type, strike_price, expiry FROM nfo_symbols WHERE symbol = 'KFINTECH' ORDER BY expiry, strike_price").fetchdf()
    print(r2.to_string())
else:
    print("No KFINTECH options found in nfo_symbols")
    # Check what symbols are available
    r3 = con.execute("SELECT DISTINCT symbol FROM nfo_symbols WHERE symbol LIKE '%KF%' OR symbol LIKE '%FIN%' ORDER BY symbol").fetchdf()
    print(f"\nSimilar symbols: {r3['symbol'].tolist() if len(r3) > 0 else 'none'}")

# Also check IRFC for comparison
r4 = con.execute("SELECT COUNT(*) FROM nfo_symbols WHERE symbol = 'IRFC'").fetchone()
print(f"\nIRFC nfo_symbols: {r4[0]} rows")
if r4[0] > 0:
    r5 = con.execute("SELECT DISTINCT option_type, strike_price, expiry FROM nfo_symbols WHERE symbol = 'IRFC' ORDER BY expiry, strike_price LIMIT 10").fetchdf()
    print(r5.to_string())

# Check for KFINTECH in the universe
r6 = con.execute("SELECT ticker FROM daily_bars WHERE ticker LIKE '%KFINTECH%' LIMIT 1").fetchone()
print(f"\nKFINTECH in daily_bars: {'Yes' if r6 else 'No'}")

con.close()
