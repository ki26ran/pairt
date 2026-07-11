"""PairTrading with Options (CE/PE) -- standalone isolated backtest."""
import os, sys, json
from datetime import datetime, timedelta, date
from collections import defaultdict
import pandas as pd
import numpy as np

_BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _BASE)
sys.path.insert(0, os.path.join(_BASE, "common"))

THRESHOLDS_FILE = os.path.join(_BASE, "pairtrading", "configs", "pair_thresholds.json")
ROLL_WIN = 63; STOP_LOSS_MULT = 2.0; MAX_HOLD_DAYS = 20
MAX_POSITIONS = 5; LOT_MULTIPLIER = 1
STRIKE_MODE = "ATM"; TV_PCT = 0.02
TX_COST_PER_LOT = 100; SLIPPAGE_PCT = 0.001
ENTRY_MIN_DTE = 10; ROLL_DTE = 7

import duckdb
from common.market_data.cache import get_cache

def _option_premium(price, strike, direction, tv_pct):
    if direction == "LONG": intrinsic = max(0, price - strike)
    else: intrinsic = max(0, strike - price)
    return intrinsic + price * tv_pct

def load_thresholds():
    with open(THRESHOLDS_FILE) as f: return json.load(f)

def sector(sym):
    s = sym.replace(".NS", "").upper()
    m = {"ANGELONE":"Financial","BAJAJFINSV":"Financial","HDFCLIFE":"Insurance","HDFCBANK":"Banking","BANKINDIA":"Banking","SBIN":"Banking","BDL":"Defence","CONCOR":"Logistics","MAZDOCK":"Ship Building","CANBK":"Banking","MUTHOOTFIN":"Financial","CDSL":"Financial","MCX":"Financial","SBICARD":"Financial","COCHINSHIP":"Ship Building","HAL":"Defence","INOXWIND":"Energy","CUMMINSIND":"Capital Goods","KEI":"Capital Goods","GVT&D":"Capital Goods","HINDALCO":"Metals","JSWSTEEL":"Metals","HYUNDAI":"Automobile","UNOMINDA":"Auto Ancillary","ICICIBANK":"Banking","IRFC":"Financial","IEX":"Energy","IREDA":"Financial","JINDALSTEL":"Metals","TATASTEEL":"Metals","JIOFIN":"Financial","KOTAKBANK":"Banking","KFINTECH":"Financial","TCS":"IT","M&M":"Automobile","MARUTI":"Automobile","PIIND":"Chemicals","SHREECEM":"Cement","TATACHEM":"Chemicals"}
    return m.get(s, "")

def load_hourly_data(all_stocks):
    cache = get_cache(); end = datetime.now(); start = end - timedelta(days=365)
    con = duckdb.connect(cache.db_path, read_only=True)
    clean = [s.replace(".NS","") for s in all_stocks]
    ph = ", ".join(["?" for _ in clean])
    df = con.execute(f"SELECT ticker, datetime_ist, close FROM hourly_bars WHERE ticker IN ({ph}) AND datetime_ist >= ? AND datetime_ist < ? ORDER BY datetime_ist", clean + [start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")]).df()
    con.close()
    if df.empty: return None
    df["datetime_ist"] = pd.to_datetime(df["datetime_ist"])
    closes = df.pivot_table(index="datetime_ist", columns="ticker", values="close", aggfunc="first")
    closes = closes.ffill(); closes.index = pd.to_datetime(closes.index)
    mkt = (closes.index.hour*60+closes.index.minute >= 555) & (closes.index.hour*60+closes.index.minute <= 930)
    return closes[mkt]

# Load data
thresholds = load_thresholds(); print(f"Loaded {len(thresholds)} pairs")
all_stocks = list(set([s for pair in thresholds for s in pair.split("|")]))
closes = load_hourly_data(all_stocks)
if closes is None: print("No hourly data"); sys.exit(1)
print(f"Data: {len(closes)} hourly candles\n")

# Pre-compute z-scores
pair_data = {}
for pair_key, th in sorted(thresholds.items()):
    s1, s2 = pair_key.split("|"); c1, c2 = s1.replace(".NS",""), s2.replace(".NS","")
    if c1 not in closes.columns or c2 not in closes.columns: continue
    hr = th.get("hr",1.0); entry_z = th.get("entry_z",2.0); exit_z = th.get("exit_z",0.5)
    p1, p2 = closes[c1], closes[c2]
    combined = pd.concat([p1,p2],axis=1).dropna()
    if len(combined) < ROLL_WIN+1: continue
    spread = combined.iloc[:,0] - hr*combined.iloc[:,1]
    sm = spread.rolling(ROLL_WIN).mean(); ss = spread.rolling(ROLL_WIN).std()
    z = ((spread-sm)/ss).dropna()
    pair_data[pair_key] = {"s1":s1,"s2":s2,"hr":hr,"entry_z":entry_z,"exit_z":exit_z,"z":z,"p1":p1,"p2":p2,"sector":sector(s1)}

from pairtrading.configs.symbols import get_nifty200
lot_map = {e["Symbol"]:e["LotSize"] for e in get_nifty200()}
for pd_ in pair_data.values():
    pd_["lot1"] = lot_map.get(pd_["s1"],1) * LOT_MULTIPLIER
    pd_["lot2"] = lot_map.get(pd_["s2"],1) * LOT_MULTIPLIER

# Capital per pair
print(f"{'Pair':45s} {'Lot1':>5s} {'Lot2':>5s} {'FuturesMargin':>15s} {'OptPremium':>15s}")
print("-"*85)
for pk, pd_ in sorted(pair_data.items()):
    m1 = pd_["lot1"]*closes[pd_["s1"].replace(".NS","")].median()*0.15
    m2 = pd_["lot2"]*closes[pd_["s2"].replace(".NS","")].median()*0.15
    prem1 = closes[pd_["s1"].replace(".NS","")].median()*pd_["lot1"]*0.02
    prem2 = closes[pd_["s2"].replace(".NS","")].median()*pd_["lot2"]*0.02
    print(f"  {pk:45s} {pd_['lot1']:>5d} {pd_['lot2']:>5d} Rs {m1+m2:>8,.0f}  Rs {prem1+prem2:>8,.0f}")

# Helper: estimate next expiry with DTE >= min_dte
def next_expiry(from_ts, min_dte=10):
    """Return estimated expiry date with at least min_dte days from from_ts."""
    from datetime import date as dt_date
    d = from_ts.date() if hasattr(from_ts, 'date') else from_ts
    if not hasattr(d, 'weekday'): return d + timedelta(days=30)
    # Find next Thursday (weekly expiry) or last Tuesday (monthly)
    # Simple: use next month-end's Tuesday as monthly expiry
    cur = d
    for _ in range(60):  # search up to 60 days
        cur += timedelta(days=1)
        if cur.weekday() == 1:  # Tuesday
            dte = (cur - d).days
            if dte >= min_dte:
                return cur
    return d + timedelta(days=30)

# Backtest
print(f"\n\n=== Backtest (max {MAX_POSITIONS} pairs, {LOT_MULTIPLIER} lots, {STRIKE_MODE}) ===")
print(f"  Entry min DTE: {ENTRY_MIN_DTE}  Roll DTE: {ROLL_DTE}")
all_ts = sorted(set(ts for pd_ in pair_data.values() for ts in pd_["z"].index))
active_positions = {}; closed_trades = []; daily_max = defaultdict(int); max_concurrent = 0

for ts in all_ts:
    today_d = ts.date() if hasattr(ts, 'date') else ts
    # Exits (including expiry rollover)
    for pk in list(active_positions.keys()):
        pos = active_positions[pk]; pd_ = pair_data.get(pk)
        if pd_ is None or ts not in pd_["z"].index: continue
        zv, cp1, cp2 = pd_["z"].loc[ts], pd_["p1"].loc[ts], pd_["p2"].loc[ts]
        days_held = (ts - pos["entry_date"]).days
        exit_reason = None

        # Expiry rollover check
        pos_expiry = pos.get("expiry")
        if pos_expiry:
            dte = (pos_expiry - today_d).days if hasattr(pos_expiry, '__sub__') else 99
            if dte < ROLL_DTE:
                exit_reason = "expiry_roll"

        if not exit_reason:
            if zv <= -pd_["exit_z"]: exit_reason = "mean-reversion"
            elif zv >= pos["entry_z_actual"] * STOP_LOSS_MULT: exit_reason = "stop-loss"
            elif days_held >= MAX_HOLD_DAYS: exit_reason = "timeout"

        if exit_reason:
            # Calculate PnL using stored strikes
            ec1 = _option_premium(pos["entry_p1"],pos["strike1"],"SHORT",TV_PCT)*pd_["lot1"]
            ec2 = _option_premium(pos["entry_p2"],pos["strike2"],"LONG",TV_PCT)*pd_["lot2"]
            total_cost = ec1+ec2
            xv1 = _option_premium(cp1,pos["strike1"],"SHORT",TV_PCT)*pd_["lot1"]
            xv2 = _option_premium(cp2,pos["strike2"],"LONG",TV_PCT)*pd_["lot2"]
            exit_val = xv1+xv2
            gross = round(exit_val - total_cost, 2)
            tx = TX_COST_PER_LOT * LOT_MULTIPLIER * 2 * 2
            slip = round(total_cost*SLIPPAGE_PCT + exit_val*SLIPPAGE_PCT, 2)
            net = round(gross - tx - slip, 2)
            closed_trades.append({"pair":pk,"entry_date":pos["entry_date"],"exit_date":ts,
                "gross_pnl":gross,"tx_cost":tx,"slippage":slip,"total_costs":round(tx+slip,2),
                "total_pnl":net,"reason":exit_reason,"dte_at_exit":dte if exit_reason=="expiry_roll" else None})
            del active_positions[pk]
            # If expiry roll, re-enter immediately
            if exit_reason == "expiry_roll" and len(active_positions) < MAX_POSITIONS:
                sc = pd_["sector"]
                open_sec = {p.get("sector","") for p in active_positions.values()}
                if not (sc and sc in open_sec) and ts in pd_["z"].index and pd_["z"].loc[ts] >= pd_["entry_z"]:
                    cp1, cp2 = pd_["p1"].loc[ts], pd_["p2"].loc[ts]
                    s1s = pos["strike1"]; s2s = pos["strike2"]  # same strikes, new expiry
                    act_exp = next_expiry(ts, ENTRY_MIN_DTE)
                    active_positions[pk] = {"entry_date":ts,"entry_p1":cp1,"entry_p2":cp2,
                        "entry_z_actual":pd_["z"].loc[ts],"sector":sc,
                        "strike1":s1s,"strike2":s2s,"expiry":act_exp}
                    open_sec.add(sc)

    # Entries
    if len(active_positions) < MAX_POSITIONS:
        open_sec = {pos.get("sector","") for pos in active_positions.values()}
        for pk in sorted(pair_data.keys()):
            if len(active_positions) >= MAX_POSITIONS: break
            if pk in active_positions: continue
            pd_ = pair_data[pk]
            if ts not in pd_["z"].index: continue
            if pd_["z"].loc[ts] < pd_["entry_z"]: continue
            sc = pd_["sector"]
            if sc and sc in open_sec: continue
            cp1, cp2 = pd_["p1"].loc[ts], pd_["p2"].loc[ts]
            # Resolve strikes via NFO table with min_dte, fallback to formula
            s1s = None; s2s = None
            act_expiry = None
            try:
                cache = get_cache()
                nfo_s1 = cache.resolve_option_contract(pd_["s1"], cp1, "SHORT", strike_mode=STRIKE_MODE, min_dte=ENTRY_MIN_DTE)
                nfo_s2 = cache.resolve_option_contract(pd_["s2"], cp2, "LONG", strike_mode=STRIKE_MODE, min_dte=ENTRY_MIN_DTE)
                if nfo_s1 and nfo_s2:
                    s1s = nfo_s1["strike_price"]
                    s2s = nfo_s2["strike_price"]
                    act_expiry = nfo_s1["expiry"]
            except Exception:
                pass
            if s1s is None:
                # Fallback: formula strikes + estimated expiry
                t1 = max(0.05, cp1*0.001); t2 = max(0.05, cp2*0.001)
                a1 = round(cp1/t1)*t1; a2 = round(cp2/t2)*t2
                if STRIKE_MODE == "ITM1":
                    s1s = a1+t1; s2s = a2-t2 if a2>t2 else a2
                else: s1s, s2s = a1, a2
                act_expiry = next_expiry(ts, ENTRY_MIN_DTE)
            active_positions[pk] = {"entry_date":ts,"entry_p1":cp1,"entry_p2":cp2,
                "entry_z_actual":pd_["z"].loc[ts],"sector":sc,
                "strike1":s1s,"strike2":s2s,"expiry":act_expiry}
            open_sec.add(sc)
    d = ts.date() if hasattr(ts,'date') else ts
    n = len(active_positions)
    if n > daily_max[d]: daily_max[d] = n
    if n > max_concurrent: max_concurrent = n

# Report
tdf = pd.DataFrame(closed_trades)
gross_total = tdf["gross_pnl"].sum() if not tdf.empty else 0
total_tx = tdf["tx_cost"].sum(); total_slip = tdf["slippage"].sum()
total_costs = tdf["total_costs"].sum(); net_total = tdf["total_pnl"].sum()
wins = (tdf["total_pnl"]>0).sum(); total = len(tdf); wr = wins/total*100 if total else 0
avg_prem_est = 75000  # est. premium per trade (2 lots x 2 legs)

pair_s = {}
for t in closed_trades: pair_s.setdefault(t["pair"],[]).append(t["total_pnl"])
print(f"\n{'Pair':45s} {'Trades':>6s} {'Net P&L':>12s} {'WR':>5s}")
print("-"*70)
for pk in sorted(pair_s.keys()):
    pl = pair_s[pk]; pnl = sum(pl); w = sum(1 for p in pl if p>0)
    print(f"  {pk:45s} {len(pl):>6d} Rs {pnl:>+9.0f}  {w/len(pl)*100:>3.0f}%")
print("-"*70)
print(f"{'TOTAL':45s} {total:>6d} Rs {net_total:>+9.0f}  {wr:>3.0f}%")

print(f"\n=== Cost Breakdown (10 months) ===")
print(f"  Gross P&L (option spread move):      Rs {gross_total:>+10,.0f}")
print(f"  Transaction costs (Rs {TX_COST_PER_LOT}/lot/side): Rs {total_tx:>+10,.0f}")
print(f"  Slippage ({SLIPPAGE_PCT*100:.1f}%):              Rs {total_slip:>+10,.0f}")
print(f"  Total costs:                         Rs {total_costs:>+10,.0f}")
print(f"  {'-'*45}")
print(f"  NET P&L (after all costs):           Rs {net_total:>+10,.0f}")
print(f"  Cost impact: {total_costs/(abs(gross_total)+total_costs)*100:.1f}% of gross")

tdf["month"] = tdf["entry_date"].apply(lambda x: str(x)[:7])
mg = tdf.groupby("month")["gross_pnl"].sum()
mc = tdf.groupby("month")["total_costs"].sum()
mn = tdf.groupby("month")["total_pnl"].sum()
print(f"\n{'Month':>10s} {'Gross':>12s} {'Costs':>10s} {'Net':>12s}")
print("-"*45)
for m in mn.index:
    print(f"  {m:>8s} Rs {mg[m]:>+8,.0f} Rs {mc[m]:>7,.0f} Rs {mn[m]:>+8,.0f}")

print(f"\n=== Capital & ROI ===")
dists = sorted(daily_max.values()); avg_conc = sum(dists)/len(dists)
print(f"  Max concurrent: {max_concurrent}")
print(f"  Avg daily concurrent: {avg_conc:.1f}")
for buf in [1,2,3]:
    cap = round(avg_prem_est*MAX_POSITIONS*buf)
    roi_mo = net_total/len(mn)/cap*100 if cap else 0
    print(f"  {buf}x buffer ({buf}x premium x {MAX_POSITIONS} pairs):  Cap Rs {cap:>7,.0f}  ROI/mo {roi_mo:.1f}%")
