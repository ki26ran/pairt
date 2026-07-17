"""Check August expiry options for all current positions."""
import os, duckdb
os.environ["APP_ENV"] = "prod"

con = duckdb.connect("/opt/pairt/data/market_data.duckdb")

pairs = [
    ("IRFC", "CE", "IRFC28JUL26C90", 90),
    ("WIPRO", "CE", "WIPRO28JUL26C180", 180),
    ("TCS", "PE", "TCS28JUL26P2200", 2200),
    ("KFINTECH", "PE", "KFINTECH28JUL26P900", 900),
    ("JUBLFOOD", "PE", "JUBLFOOD28JUL26P425", 425),
    ("LUPIN", "CE", "LUPIN28JUL26C2500", 2500),
    ("TORNTPHARM", "PE", "TORNTPHARM28JUL26P5000", 5000),
]

print(f"{'Symbol':15s} {'Type':5s} {'Current Jul':20s} {'August Available':25s} {'Aug Strike':>10s} {'Lot':>6s}")
print("="*85)

for sym, opt_type, jul_sym, jul_strike in pairs:
    # Find August contracts near the July strike
    rows = con.execute("""
        SELECT trading_symbol, strike_price, expiry, lot_size 
        FROM nfo_symbols 
        WHERE symbol = ? AND option_type = ? AND expiry = '2026-08-25'
        ORDER BY ABS(strike_price - ?)
        LIMIT 3
    """, [sym, opt_type, jul_strike]).fetchall()
    
    for r in rows:
        trading_symbol, strike_price, expiry_date, lot_size = r[0], float(r[1]), r[2], int(r[3])
        print(f"{sym:15s} {opt_type:5s} {jul_sym:20s} {trading_symbol:25s} {strike_price:>10.2f} {lot_size:>6d}")

# Check if any strikes need adjustment for current price
print(f"\n{'='*85}")
print("Current underlying prices for reference:")
for sym, opt_type, _, _ in pairs:
    pr = con.execute("SELECT close FROM hourly_bars WHERE ticker = ? ORDER BY datetime_ist DESC LIMIT 1", [sym]).fetchone()
    if pr:
        print(f"  {sym:15s} -> ₹{float(pr[0]):.2f}")

con.close()
