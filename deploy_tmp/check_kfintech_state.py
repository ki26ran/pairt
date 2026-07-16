import duckdb, os, json
os.environ["APP_ENV"] = "prod"
con = duckdb.connect("/opt/pairt/pairtrading/pairtrading.duckdb")
r = con.execute("SELECT pair_key, direction, entry_date, entry_z, last_z, broker_order_id FROM pair_positions").fetchdf()
print("=== Positions ===")
print(r.to_string())
r2 = con.execute("SELECT pair_key, z_score, signal FROM pair_scanner_results WHERE pair_key LIKE '%IRFC%'").fetchdf()
print("\n=== IRFC Scanner ===")
print(r2.to_string())
r3 = con.execute("SELECT id, timestamp, s1, s2, signal FROM pair_signals WHERE s1 LIKE '%IRFC%' ORDER BY timestamp DESC").fetchdf()
print("\n=== IRFC Signals ===")
print(r3.to_string())
con.close()

# Also check if scan_pairs.py has the retry logic
print("\n=== Check retry logic deployed ===")
import subprocess
r4 = subprocess.run(["grep", "-c", "_retry_legs", "/opt/pairt/pairtrading/live/scan_pairs.py"], capture_output=True, text=True)
print(f"_retry_legs references: {r4.stdout.strip()}")
