import duckdb, os
from datetime import datetime
os.environ["APP_ENV"] = "prod"
con = duckdb.connect("/opt/pairt/pairtrading/pairtrading.duckdb")
now = datetime.now()

# Close JUBLFOOD|PATANJALI in pair_trades
con.execute("""
    UPDATE pair_trades 
    SET status = 'CLOSED', exit_date = ?,
        exit_price_s1 = 12.96, exit_price_s2 = 4.15,
        pnl_s1 = (12.96 - 11.78) * 3750,
        pnl_s2 = (4.15 - 11.10) * 2150,
        total_pnl = ((12.96 - 11.78) * 3750) + ((4.15 - 11.10) * 2150),
        exit_reason = 'manual_close'
    WHERE pair_key = 'JUBLFOOD.NS|PATANJALI.NS' AND status = 'OPEN'
""", (now.isoformat(),))

# Remove from pair_positions
con.execute("DELETE FROM pair_positions WHERE pair_key = 'JUBLFOOD.NS|PATANJALI.NS'")

r = con.execute("SELECT * FROM pair_positions").fetchdf()
print("=== Remaining positions ===")
print(r.to_string())
r2 = con.execute("SELECT trade_id, pair_key, status, total_pnl, exit_reason FROM pair_trades ORDER BY trade_id DESC LIMIT 3").fetchdf()
print("\n=== Recent trades ===")
print(r2.to_string())
con.close()
print("\n✅ DB updated")
