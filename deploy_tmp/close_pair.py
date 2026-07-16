"""Close IRFC CE + KFINTECH PE using live bid/ask mid prices."""
import os, json, time
os.environ["APP_ENV"] = "prod"
from ganah import setup_api, place_live_order, order_status

api = setup_api("SHOONYA", "FA138862")

# Get current positions to know lot sizes
pos = api.get_positions()
lots = {}
for p in pos:
    if p.get("instname") == "OPTSTK" and int(p.get("netqty", 0)) != 0:
        lots[p["tsym"]] = {"qty": abs(int(p["netqty"])), "avg": float(p["netavgprc"])}

print("=== Positions to close ===")
for sym, info in lots.items():
    print(f"  {sym}: qty={info['qty']}, avg={info['avg']}")

# Close IRFC CE — sell at bid
sym1 = "IRFC28JUL26C90"
q1 = api.get_quotes("NFO", sym1)
if isinstance(q1, dict):
    bid1 = float(q1.get("bp1", 0))
    ask1 = float(q1.get("sp1", 0))
    mid1 = round((bid1 + ask1) / 2, 2) if bid1 and ask1 else 0
    # Sell at bid or slightly below for quick fill
    price1 = round((bid1 - 0.05) / 0.05) * 0.05 if bid1 else 0
    print(f"\n{sym1}: bid={bid1} ask={ask1} mid={mid1} -> sell limit=₹{price1}")
    if price1 > 0 and sym1 in lots:
        oid1 = place_live_order(sym1, "SHORT", lots[sym1]["qty"], "PT_CLOSE_IRFC",
                                exchange="NFO", product_type="M", price_type="LMT", price=price1)
        print(f"  Order placed: {oid1}")
        time.sleep(2)
        f1, ap1, _, r1 = order_status(oid1) if oid1 else (0, 0, None, "no oid")
        if f1 == 1:
            print(f"  ✅ IRFC CE closed at ₹{ap1}")
        else:
            print(f"  ❌ IRFC CE failed: {r1}")

# Close KFINTECH PE — sell at bid
sym2 = "KFINTECH28JUL26P900"
q2 = api.get_quotes("NFO", sym2)
if isinstance(q2, dict):
    bid2 = float(q2.get("bp1", 0))
    ask2 = float(q2.get("sp1", 0))
    mid2 = round((bid2 + ask2) / 2, 2) if bid2 and ask2 else 0
    price2 = round((bid2 - 0.05) / 0.05) * 0.05 if bid2 else 0
    print(f"\n{sym2}: bid={bid2} ask={ask2} mid={mid2} -> sell limit=₹{price2}")
    if price2 > 0 and sym2 in lots:
        oid2 = place_live_order(sym2, "SHORT", lots[sym2]["qty"], "PT_CLOSE_KFINTECH",
                                exchange="NFO", product_type="M", price_type="LMT", price=price2)
        print(f"  Order placed: {oid2}")
        time.sleep(2)
        f2, ap2, _, r2 = order_status(oid2) if oid2 else (0, 0, None, "no oid")
        if f2 == 1:
            print(f"  ✅ KFINTECH PE closed at ₹{ap2}")
        else:
            print(f"  ❌ KFINTECH PE failed: {r2}")

# Final positions
print("\n=== Remaining positions ===")
pos2 = api.get_positions()
for p in pos2:
    if p.get("instname") == "OPTSTK" and int(p.get("netqty", 0)) != 0:
        print(f"  {p['tsym']:35s} qty={int(p['netqty']):>6}")
