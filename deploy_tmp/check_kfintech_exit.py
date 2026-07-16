import os, json, sys
sys.path[:0] = ["/opt/pairt", "/opt/pairt/pairtrading"]
os.environ["APP_ENV"] = "prod"

from ganah import setup_api
api = setup_api("SHOONYA", "FA138862")

# Check pending orders
try:
    orders = api.get_order_book()
    print(f"=== PENDING ORDERS ({len(orders)} total) ===")
    for o in orders:
        if 'KFINTECH' in str(o) or 'kfintech' in str(o).lower() or o.get('status') != 'COMPLETE':
            print(json.dumps(o, indent=2, default=str))
except Exception as e:
    print(f"Order book error: {e}")

# Check positions for KFINTECH specifically
print("\n=== KFINTECH POSITION ===")
pos = api.get_positions()
for p in pos:
    if 'KFINTECH' in str(p):
        print(json.dumps(p, indent=2, default=str))

# Also check if there's a way to get trade history via position diff
print("\n=== NET POSITION CHANGES TODAY ===")
for p in pos:
    if 'KFINTECH' in str(p):
        dbq = int(p.get("daybuyqty", 0)) - int(p.get("daysellqty", 0))
        cfq = int(p.get("cfbuyqty", 0)) - int(p.get("cfsellqty", 0))
        print(f"  Day net: {dbq}, Carry-forward net: {cfq}, Total net: {int(p['netqty'])}")
        print(f"  Day buy avg: {p.get('daybuyavgprc')}, Carry-forward avg: {p.get('cfbuyavgprc')}")
        print(f"  Day buy amt: {p.get('daybuyamt')}, Day sell amt: {p.get('daysellamt')}")
