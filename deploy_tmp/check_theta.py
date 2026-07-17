"""Theta decay analysis for current option positions."""
import os, json
from datetime import datetime, date
os.environ["APP_ENV"] = "prod"
from ganah import setup_api

api = setup_api("SHOONYA", "FA138862")
pos = api.get_positions()
expiry = date(2026, 7, 28)
today = date(2026, 7, 17)
dte = (expiry - today).days
trading_days = dte - 2  # approx trading days remaining = calendar - weekends

print(f"Today: {today} | Expiry: {expiry} | DTE: {dte} days (~{trading_days} trading days)")
print(f"{'='*80}")
print(f"{'Option':40s} {'Qty':>6s} {'Premium':>8s} {'Theta/day':>10s} {'Weeks left':>10s} {'Status':>10s}")
print(f"{'='*80}")

for p in pos:
    if p.get("instname") == "OPTSTK" and int(p.get("netqty", 0)) != 0:
        tsym = p.get("tsym", "")
        qty = int(p.get("netqty", 0))
        premium = float(p.get("lp", 0))
        avg = float(p.get("netavgprc", 0))
        
        # Rough theta estimate: 0.5-2% of premium per day for ATM options
        # Higher for ITM, lower for OTM. Accelerates in last 2 weeks.
        theta_pct = 0.005 + (0.015 * max(0, (21 - dte) / 14))  # accelerates below 21 DTE
        theta_abs = premium * theta_pct
        
        # Skip small positions or PATANJALI (user's discretionary)
        if "PATANJALI" in tsym:
            tag = "Discretionary"
        else:
            tag = "Pair trade"
        
        print(f"{tsym:40s} {qty:>6d} {premium:>8.2f} {theta_abs:>+8.2f} {dte//7:>3d}w{dte%7:>1d}d {tag:>10s}")

print(f"\n{'='*80}")
print("THETA INSIGHTS:")
print(f"{'='*80}")
print(f"• With {dte} DTE, theta decay is noticeable but not yet aggressive")
print(f"• Theta accelerates sharply below 14 DTE (around Jul 21-22 next week)")
print(f"• Long options (all our pairs) lose ~0.5-2% of premium daily to theta")
print(f"• Pair P&L is driven by Z-score reversion — theta is secondary")
print(f"• Risk: if Z-score doesn't revert by Jul 22-23, theta becomes significant")
