import os, json
os.environ["APP_ENV"] = "prod"
from ganah import setup_api

api = setup_api("SHOONYA", "FA138862")

# Check order book for PATANJALI 400 CE
orders = api.get_order_book()
print(f"Total orders: {len(orders)}")
pat_orders = [o for o in orders if 'PATANJALI' in str(o) and '400' in str(o)]
print(f"\nPATANJALI 400 CE orders: {len(pat_orders)}")
for o in pat_orders:
    print(json.dumps(o, indent=2, default=str))

# Also check who has positions
print("\n=== Current PATANJALI positions ===")
pos = api.get_positions()
for p in pos:
    if 'PATANJALI' in str(p):
        print(f"  {p['tsym']:35s} qty={int(p['netqty']):>6}  avg={float(p['netavgprc']):>8.2f}")
        print(f"    order_source={p.get('ordersource','?')} uid={p.get('uid','?')}")
