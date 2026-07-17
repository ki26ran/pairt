"""Close JUBLFOOD PUT + PATANJALI CE using live bid/ask quotes."""
import os, time
os.environ["APP_ENV"] = "prod"
from ganah import setup_api, place_live_order, order_status

api = setup_api("SHOONYA", "FA138862")

# Get positions to confirm qty
pos = api.get_positions()
lotes = {}
for p in pos:
    if p.get("instname") == "OPTSTK" and int(p.get("netqty", 0)) != 0:
        lotes[p["tsym"]] = abs(int(p["netqty"]))

# Leg 1: JUBLFOOD PUT 425 - sell (we bought it)
sym1 = "JUBLFOOD28JUL26P425"
q1 = api.get_quotes("NFO", sym1)
if isinstance(q1, dict):
    bid1 = float(q1.get("bp1", 0))
    ti1 = float(q1.get("ti", 0.05))
    price1 = round(bid1 / ti1) * ti1
    qty1 = lotes.get(sym1, 3750)
    print(f"{sym1}: bid={bid1} -> sell limit=₹{price1}, qty={qty1}")
    oid1 = place_live_order(sym1, "SHORT", qty1, "CLOSE_JUBL_PUT",
                           exchange="NFO", product_type="M", price_type="LMT", price=price1)
    print(f"  Order: {oid1}")
    time.sleep(5)
    f1, ap1, _, _ = order_status(oid1) if oid1 else (0,0,0,"")
    if f1 == 1:
        print(f"  ✅ JUBLFOOD PUT closed at ₹{ap1}")
    else:
        print(f"  ⏳ Waiting... will retry tick-by-tick")
        for _ in range(3):
            time.sleep(30)
            f1, ap1, _, _ = order_status(oid1)
            if f1 == 1:
                print(f"  ✅ JUBLFOOD PUT closed at ₹{ap1}")
                break
            api.cancel_order(oid1)
            price1 = round((price1 - ti1) / ti1) * ti1
            print(f"  Bumping to ₹{price1}...")
            oid1 = place_live_order(sym1, "SHORT", qty1, "CLOSE_JUBL_PUT",
                                   exchange="NFO", product_type="M", price_type="LMT", price=price1)
            time.sleep(5)
            f1, ap1, _, _ = order_status(oid1) if oid1 else (0,0,0,"")
        else:
            print(f"  ❌ Could not close JUBLFOOD PUT")

# Leg 2: PATANJALI CE 370 - sell (we bought it, but this is user's discretionary)
sym2 = "PATANJALI28JUL26C370"
q2 = api.get_quotes("NFO", sym2)
if isinstance(q2, dict):
    bid2 = float(q2.get("bp1", 0))
    ti2 = float(q2.get("ti", 0.05))
    price2 = round(bid2 / ti2) * ti2
    qty2 = lotes.get(sym2, 2150)
    print(f"\n{sym2}: bid={bid2} -> sell limit=₹{price2}, qty={qty2}")
    print("  Closing PATANJALI CE (discretionary)...")
    oid2 = place_live_order(sym2, "SHORT", qty2, "CLOSE_PAT_CE",
                               exchange="NFO", product_type="M", price_type="LMT", price=price2)
    print(f"  Order: {oid2}")
    time.sleep(5)
    f2, ap2, _, _ = order_status(oid2) if oid2 else (0,0,0,"")
    if f2 == 1:
        print(f"  ✅ PATANJALI CE closed at ₹{ap2}")
    else:
        for _ in range(3):
            time.sleep(30)
            f2, ap2, _, _ = order_status(oid2)
            if f2 == 1:
                print(f"  ✅ PATANJALI CE closed at ₹{ap2}")
                break
            api.cancel_order(oid2)
            price2 = round((price2 - ti2) / ti2) * ti2
            print(f"  Bumping to ₹{price2}...")
            oid2 = place_live_order(sym2, "SHORT", qty2, "CLOSE_PAT_CE",
                                       exchange="NFO", product_type="M", price_type="LMT", price=price2)
            time.sleep(5)
            f2, ap2, _, _ = order_status(oid2) if oid2 else (0,0,0,"")
        else:
            print(f"  ❌ Could not close PATANJALI CE")

print("\n=== Remaining positions ===")
pos2 = api.get_positions()
for p in pos2:
    if p.get("instname") == "OPTSTK" and int(p.get("netqty", 0)) != 0:
        print(f"  {p['tsym']:35s} qty={int(p['netqty']):>6}")
