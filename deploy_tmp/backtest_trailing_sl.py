"""
Compare fixed stop-loss vs trailing stop-loss for pair trading.
"""
import os, sys, json, duckdb, pandas as pd, numpy as np
from collections import defaultdict

sys.path.insert(0, "/opt/pairt")
os.environ["APP_ENV"] = "prod"

from common.market_data.cache import get_cache
from pairtrading.live.cache import get_pair_cache
from pairtrading.configs.symbols import get_nifty200

th_data = get_pair_cache().load_thresholds()
if not th_data:
    th_data = json.load(open("/opt/pairt/pairtrading/configs/pair_thresholds.json"))
print(f"Loaded {len(th_data)} pairs")

cache = get_cache()
con = duckdb.connect(cache.db_path, read_only=True)
all_stocks = list(set([s for pair in th_data for s in pair.split("|")]))
clean = [s.replace(".NS", "") for s in all_stocks]
ph = ", ".join(["?" for _ in clean])
df = con.execute(f"""
    SELECT ticker, datetime_ist, close FROM hourly_bars
    WHERE ticker IN ({ph}) AND datetime_ist >= '2025-09-01' AND datetime_ist < '2026-07-01'
    ORDER BY datetime_ist
""", clean).df()
con.close()

df["datetime_ist"] = pd.to_datetime(df["datetime_ist"])
closes = df.pivot_table(index="datetime_ist", columns="ticker", values="close", aggfunc="first").ffill()
closes.index = pd.to_datetime(closes.index)
lot_map = {e["Symbol"]: int(e["LotSize"]) for e in get_nifty200()}
ROLL_WIN = 63


def run_bt(s1, s2, hr, entry_z, exit_z, sl_mult, trail_mult=None):
    c1, c2 = s1.replace(".NS",""), s2.replace(".NS","")
    if c1 not in closes.columns or c2 not in closes.columns:
        return []
    p1, p2 = closes[c1], closes[c2]
    combined = pd.concat([p1, p2], axis=1).dropna()
    if len(combined) < ROLL_WIN + 1:
        return []
    spread = combined.iloc[:, 0] - hr * combined.iloc[:, 1]
    zscore = ((spread - spread.rolling(ROLL_WIN).mean()) / spread.rolling(ROLL_WIN).std()).dropna()
    if len(zscore) < 2:
        return []

    zvals, n = zscore.values, len(zscore)
    fixed_sl = entry_z * sl_mult
    lot1, lot2 = lot_map.get(c1, 1), lot_map.get(c2, 1)
    trades = []
    pos = 0
    entry_i = None
    best_abs_z = 0
    trail_active = False

    for i in range(1, n):
        pz, cz = zvals[i-1], zvals[i]
        if pos == 0:
            if pz <= -entry_z and cz > -entry_z:
                pos, entry_i = 1, i
                best_abs_z = abs(cz); trail_active = False
            elif pz >= entry_z and cz < entry_z:
                pos, entry_i = -1, i
                best_abs_z = abs(cz); trail_active = False
        elif pos == 1:
            exit_r = None
            if cz >= exit_z:
                exit_r = "mean-reversion"
            elif trail_mult and abs(cz) > best_abs_z:
                best_abs_z = abs(cz); trail_active = True
            elif trail_active and cz <= best_abs_z - exit_z * trail_mult:
                exit_r = "trail_sl"
            elif not trail_active and cz <= -fixed_sl:
                exit_r = "stop-loss"
            elif (i - entry_i) >= 160:
                exit_r = "timeout"
            if exit_r:
                pnl = (p1.iloc[i] - p1.iloc[entry_i]) * lot1 + (p2.iloc[entry_i] - p2.iloc[i]) * lot2
                trades.append({"pair": f"{s1}|{s2}", "dir": "LONG",
                               "entry": str(zscore.index[entry_i])[:10], "exit": str(zscore.index[i])[:10],
                               "reason": exit_r, "pnl": round(pnl, 2)})
                pos = 0
        elif pos == -1:
            exit_r = None
            if cz <= -exit_z:
                exit_r = "mean-reversion"
            elif trail_mult and abs(cz) > best_abs_z:
                best_abs_z = abs(cz); trail_active = True
            elif trail_active and cz >= -best_abs_z + exit_z * trail_mult:
                exit_r = "trail_sl"
            elif not trail_active and cz >= fixed_sl:
                exit_r = "stop-loss"
            elif (i - entry_i) >= 160:
                exit_r = "timeout"
            if exit_r:
                pnl = (p1.iloc[entry_i] - p1.iloc[i]) * lot1 + (p2.iloc[i] - p2.iloc[entry_i]) * lot2
                trades.append({"pair": f"{s1}|{s2}", "dir": "SHORT",
                               "entry": str(zscore.index[entry_i])[:10], "exit": str(zscore.index[i])[:10],
                               "reason": exit_r, "pnl": round(pnl, 2)})
                pos = 0
    return trades


def score(trades):
    if not trades:
        return {"trades": 0, "pnl": 0, "wr": 0, "pf": 0, "avg_win": 0, "avg_loss": 0}
    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    n = len(pnls)
    return {
        "trades": n, "pnl": round(sum(pnls), 0),
        "wr": round(len(wins) / n * 100, 1),
        "pf": round(abs(sum(wins) / sum(losses)), 2) if losses and sum(losses) != 0 else None,
        "avg_win": round(np.mean(wins), 0) if wins else 0,
        "avg_loss": round(abs(np.mean(losses)), 0) if losses else 0,
    }


# Run comparison
configs = [
    ("Fixed SL (3x)", 3.0, None),
    ("Fixed SL (2x)", 2.0, None),
    ("Trail 0.5x (base 3x)", 3.0, 0.5),
    ("Trail 1.0x (base 3x)", 3.0, 1.0),
    ("Trail 1.5x (base 3x)", 3.0, 1.5),
    ("Trail 2.0x (base 3x)", 3.0, 2.0),
]

print("\n=== AGGREGATE RESULTS (all pairs) ===")
print(f"{'Config':25s} {'Trades':>7s} {'P&L':>10s} {'WR':>6s} {'PF':>6s} {'AvgWin':>8s} {'AvgLoss':>8s}")
print("-" * 75)

best_score = -999999
best_label = None
for label, sl_mult, trail_mult in configs:
    all_trades = []
    for pk, th in th_data.items():
        s1, s2 = pk.split("|")
        hr = th.get("hr", 1.0)
        ez = th.get("entry_z", 2.0)
        xz = th.get("exit_z", 0.0)
        trades = run_bt(s1, s2, hr, ez, xz, sl_mult, trail_mult)
        all_trades.extend(trades)
    s = score(all_trades)
    print(f"{label:25s} {s['trades']:>7d} {s['pnl']:>10,.0f} {s['wr']:>5.1f}% {str(s['pf']):>6s} {s['avg_win']:>8,.0f} {s['avg_loss']:>8,.0f}")
    # Score: weighted combo of pnl + wr + pf
    sc = s['pnl'] / 1000 + s['wr'] * 2 + (s['pf'] or 0) * 5
    if sc > best_score:
        best_score = sc
        best_label = label

print(f"\nBest config: {best_label}")

# Per-pair comparison: Fixed 3x vs Trail 1.0x
print("\n\n=== PER-PAIR: Fixed SL (3x) vs Trail 1.0x ===")
print(f"{'Pair':30s} {'FixT':>4s} {'FixP&L':>10s} {'FixWR':>5s} {'TrT':>4s} {'TrP&L':>10s} {'TrWR':>5s} {'Chg':>8s}")
print("-" * 85)

for pk, th in sorted(th_data.items())[:15]:  # top 15 by default sort
    s1, s2 = pk.split("|")
    hr = th.get("hr", 1.0)
    ez = th.get("entry_z", 2.0)
    xz = th.get("exit_z", 0.0)
    t1 = run_bt(s1, s2, hr, ez, xz, 3.0, None)
    t2 = run_bt(s1, s2, hr, ez, xz, 3.0, 1.0)
    s1s = score(t1)
    s2s = score(t2)
    print(f"{pk:30s} {s1s['trades']:>4d} {s1s['pnl']:>9,.0f} {s1s['wr']:>4.1f}% {s2s['trades']:>4d} {s2s['pnl']:>9,.0f} {s2s['wr']:>4.1f}% {s2s['pnl']-s1s['pnl']:>+7,.0f}")

print("\n\nDone.")
