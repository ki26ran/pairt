import duckdb, os
from datetime import datetime
os.environ["APP_ENV"] = "prod"
con = duckdb.connect("/opt/pairt/pairtrading/pairtrading.duckdb")
now = datetime.now()

# Close the pair trade
con.execute("""
    UPDATE pair_trades 
    SET status = 'CLOSED',
        exit_date = ?,
        exit_price_s1 = 1.45,
        exit_price_s2 = 41.35,
        pnl_s1 = (1.45 - 1.51) * 5425,
        pnl_s2 = (41.35 - 35.65) * 575,
        total_pnl = ((1.45 - 1.51) * 5425) + ((41.35 - 35.65) * 575),
        exit_reason = 'manual_test'
    WHERE pair_key = 'IRFC.NS|KFINTECH.NS' AND status = 'OPEN'
""", (now.isoformat(),))

# Remove from pair_positions
con.execute("DELETE FROM pair_positions WHERE pair_key = 'IRFC.NS|KFINTECH.NS'")

r = con.execute("SELECT * FROM pair_trades WHERE pair_key LIKE '%IRFC%'").fetchdf()
print("=== IRFC|KFINTECH trade ===")
print(r.to_string())
r2 = con.execute("SELECT * FROM pair_positions").fetchdf()
print("\n=== Remaining positions ===")
print(r2.to_string())
con.close()
