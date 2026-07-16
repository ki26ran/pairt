import duckdb, os
from datetime import datetime
os.environ["APP_ENV"] = "prod"
con = duckdb.connect("/opt/pairt/pairtrading/pairtrading.duckdb")
now = datetime.now().isoformat()

con.execute("""
    UPDATE pair_positions 
    SET broker_order_id = '26071600216743', 
        expiry_date = '2026-07-28',
        last_updated = ?
    WHERE pair_key = 'IRFC.NS|KFINTECH.NS'
""", (now,))

con.execute("""
    UPDATE pair_trades 
    SET entry_price_s1 = 1.51,
        entry_price_s2 = 35.65,
        lot_size_s1 = 5425,
        lot_size_s2 = 575
    WHERE pair_key = 'IRFC.NS|KFINTECH.NS' AND status = 'OPEN'
""")

r1 = con.execute("SELECT pair_key, direction, broker_order_id, expiry_date FROM pair_positions").fetchdf()
print("=== pair_positions ===")
print(r1.to_string())
r2 = con.execute("SELECT trade_id, pair_key, entry_price_s1, entry_price_s2, lot_size_s1, lot_size_s2 FROM pair_trades ORDER BY trade_id DESC LIMIT 3").fetchdf()
print("\n=== pair_trades ===")
print(r2.to_string())
con.close()
print("Done")
