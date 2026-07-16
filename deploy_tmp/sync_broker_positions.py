import duckdb, os, json, sys
sys.path[:0] = ["/opt/pairt", "/opt/pairt/pairtrading"]
os.environ["APP_ENV"] = "prod"

# 1. Get broker positions
from ganah import setup_api
api = setup_api("SHOONYA", "FA138862")
broker_pos = api.get_positions()
print(f"\n=== BROKER POSITIONS ({len(broker_pos)} total) ===")
for p in broker_pos:
    if p.get("instname") == "OPTSTK" and int(p.get("netqty", 0)) != 0:
        print(f"  {p['tsym']:35s} qty={int(p['netqty']):>6}  avg={float(p['netavgprc']):>8.2f} lp={float(p['lp']):>8.2f}  mtm={float(p['urmtom']):>+8.2f}")

# 2. Get trade history
try:
    trades = api.get_trade_history()
    print(f"\n=== TRADE HISTORY ({len(trades)} total) ===")
    kfintech_trades = [t for t in trades if 'KFINTECH' in str(t)]
    print(f"KFINTECH trades: {len(kfintech_trades)}")
    for t in kfintech_trades:
        print(json.dumps(t, indent=2, default=str))
except Exception as e:
    print(f"\nTrade history error: {e}")

# 3. Current pair positions in DB
con = duckdb.connect("/opt/pairt/pairtrading/pairtrading.duckdb")
r = con.execute("SELECT * FROM pair_positions").fetchdf()
print(f"\n=== PAIR POSITIONS DB ({len(r)}) ===")
print(r.to_string())

# 4. Signal history for IRFC|KFINTECH
r2 = con.execute("SELECT * FROM pair_signals WHERE s1 LIKE '%IRFC%' ORDER BY timestamp").fetchdf()
print(f"\n=== IRFC|KFINTECH SIGNALS ({len(r2)}) ===")
print(r2.to_string())

# 5. Trade history  
r3 = con.execute("SELECT * FROM pair_trades ORDER BY entry_date DESC").fetchdf()
print(f"\n=== TRADE HISTORY ({len(r3)}) ===")
print(r3.to_string())

con.close()
