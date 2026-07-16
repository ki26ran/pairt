import os, json
os.environ["APP_ENV"] = "prod"
from ganah import setup_api
api = setup_api("SHOONYA", "FA138862")
pos = api.get_positions()
print("=== OPTION POSITIONS AT BROKER ===")
for p in pos:
    if p.get("instname") == "OPTSTK" and int(p.get("netqty", 0)) != 0:
        print(f"  {p['tsym']:35s} qty={int(p['netqty']):>6}  avg={float(p['netavgprc']):>8.2f}  lp={float(p['lp']):>8.2f}  mtm={float(p['urmtom']):>+8.2f}")

# Also verify the mid price logic for KFINTECH
q = api.get_quotes("NFO", "KFINTECH28JUL26P900")
if isinstance(q, dict):
    bid = float(q.get("bp1", 0))
    ask = float(q.get("sp1", 0))
    mid = (bid + ask) / 2
    print(f"\nKFINTECH PE 900 quote: bid={bid}, ask={ask}, mid={mid:.2f}")
