"""
PairTrading Threshold Optimizer — walk-forward grid search to find optimal
z-score entry/exit thresholds for each pair, maximizing Sharpe ratio.

Usage:
  python PairTrading/optimizer.py                     # optimize all pairs
  python PairTrading/optimizer.py --pair INFY.NS|TCS  # optimize one pair
  python PairTrading/optimizer.py --months 12          # use 12 months of data
"""
import os, sys, json, argparse
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE_DIR)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if os.path.join(ROOT, "common") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "common"))
from pairtrading.configs.symbols import get_nifty200
from common.market_data.cache import get_cache

THRESHOLDS_FILE = os.path.join(BASE_DIR, "configs", "pair_thresholds.json")
ROLL_WIN = 63  # rolling window for z-score — aligned with scanner (63 hours ~ 8 trading days)

def load_data(all_stocks, months=6):
    """Fetch 1-hour data from DuckDB for all stocks."""
    import duckdb
    cache = get_cache()
    end = datetime.now()
    start = end - timedelta(days=months * 30)
    start_str = start.strftime("%Y-%m-%d")
    end_str = end.strftime("%Y-%m-%d")
    print("  Reading %s to %s from DuckDB..." % (start_str, end_str))
    try:
        con = duckdb.connect(cache.db_path, read_only=True)
        symbols_str = ", ".join(["'%s'" % s for s in all_stocks])
        query = """
            SELECT ticker, datetime_ist, open, high, low, close, volume
            FROM hourly_bars
            WHERE ticker IN (%s)
            AND datetime_ist >= '%s' AND datetime_ist < '%s'
            ORDER BY datetime_ist
        """ % (symbols_str, start_str, end_str + " 23:59:59")
        df = con.execute(query).df()
        con.close()
        if df.empty:
            print("  WARN: No hourly data in DuckDB")
            return None
        return df
    except Exception as e:
        print("  ERROR loading data: %s" % e)
        print("  Falling back to cache method...")
        raw = cache.get_bulk_multiindex(all_stocks, start_str, end_str, interval="1h")
        return raw

def prepare_closes(raw):
    """Convert raw data to a clean close-price DataFrame indexed by datetime."""
    if raw is None:
        return None
    if isinstance(raw, pd.DataFrame) and 'ticker' in raw.columns:
        raw['datetime_ist'] = pd.to_datetime(raw['datetime_ist'])
        closes = raw.pivot_table(index='datetime_ist', columns='ticker', values='close', aggfunc='first')
        closes = closes.ffill()
        closes.index = pd.to_datetime(closes.index)
        if closes.index.tz is None:
            closes.index = closes.index.tz_localize('Asia/Kolkata')
        mkt = (closes.index.hour * 60 + closes.index.minute >= 9 * 60 + 15) & \
              (closes.index.hour * 60 + closes.index.minute <= 15 * 60 + 30)
        closes = closes[mkt]
        closes.index = closes.index.tz_localize(None)
    else:
        ts = raw.index
        if isinstance(ts, pd.DatetimeIndex):
            if ts.tz is not None:
                ts_ist = ts.tz_convert("Asia/Kolkata")
            else:
                ts_ist = ts.tz_localize("UTC").tz_convert("Asia/Kolkata")
            mkt = (ts_ist.hour * 60 + ts_ist.minute >= 9 * 60 + 15) & \
                  (ts_ist.hour * 60 + ts_ist.minute <= 15 * 60 + 30)
            raw = raw[mkt]
            raw.index = raw.index.tz_convert("Asia/Kolkata").tz_localize(None)
        closes = raw["Close"]
        closes.columns = [c[0].replace(".NS", "").replace(".BO", "") if isinstance(c, tuple)
                          else c.replace(".NS", "").replace(".BO", "") for c in closes.columns]
        closes = closes.ffill()
    return closes

def compute_z_score_series(closes, s1, s2, hr_val, roll_win=ROLL_WIN):
    """Compute the full z-score series for a pair.

    Returns (zs, p1, p2) with all aligned to the same index.
    """
    s1c = s1.replace(".NS", "")
    s2c = s2.replace(".NS", "")
    if s1c not in closes.columns or s2c not in closes.columns:
        return None, None, None
    pair_data = closes[[s1c, s2c]].dropna()
    if len(pair_data) < roll_win:
        return None, None, None
    p1, p2 = pair_data[s1c], pair_data[s2c]
    spread = p1 - hr_val * p2
    sm = spread.rolling(roll_win).mean()
    ss = spread.rolling(roll_win).std()
    zs = (spread - sm) / ss
    zs = zs.dropna()
    # Align price series to z-score index so positional indices match
    p1 = p1.loc[zs.index]
    p2 = p2.loc[zs.index]
    return zs, p1, p2

def simulate_thresholds(zs, p1, p2, entry_z, exit_z):
    """Simulate trades for a given (entry_z, exit_z) threshold pair."""
    trades = []
    in_position = False
    position = None
    stop_loss_z = entry_z * 3.0
    max_hold_bars = 160  # ~20 trading days * 8 bars/day

    for i in range(1, len(zs)):
        pz = zs.iloc[i-1]
        z = zs.iloc[i]
        idx = zs.index[i]

        if not in_position:
            if pz <= -entry_z and z > -entry_z:
                in_position = True
                position = {"direction": "LONG", "entry_z": z, "entry_p1": p1.iloc[i], "entry_p2": p2.iloc[i], "entry_idx": i, "entry_time": idx}
            elif pz >= entry_z and z < entry_z:
                in_position = True
                position = {"direction": "SHORT", "entry_z": z, "entry_p1": p1.iloc[i], "entry_p2": p2.iloc[i], "entry_idx": i, "entry_time": idx}
        else:
            exited = False
            exit_reason = ""

            # Normal exit
            if position["direction"] == "LONG":
                if pz < exit_z and z >= exit_z:
                    exit_reason = "mean-reversion"
                elif abs(z) >= stop_loss_z:
                    exit_reason = "stop-loss"
                elif i - position["entry_idx"] >= max_hold_bars:
                    exit_reason = "timeout"
            else:
                if pz > -exit_z and z <= -exit_z:
                    exit_reason = "mean-reversion"
                elif abs(z) >= stop_loss_z:
                    exit_reason = "stop-loss"
                elif i - position["entry_idx"] >= max_hold_bars:
                    exit_reason = "timeout"

            if exit_reason:
                if position["direction"] == "LONG":
                    pnl_p1 = p1.iloc[i] - position["entry_p1"]
                    pnl_p2 = position["entry_p2"] - p2.iloc[i]
                else:
                    pnl_p1 = position["entry_p1"] - p1.iloc[i]
                    pnl_p2 = p2.iloc[i] - position["entry_p2"]
                trades.append({
                    "direction": position["direction"], "entry_z": position["entry_z"], "exit_z": z,
                    "entry_time": str(position["entry_time"]), "exit_time": str(idx),
                    "entry_p1": float(position["entry_p1"]), "exit_p1": float(p1.iloc[i]),
                    "entry_p2": float(position["entry_p2"]), "exit_p2": float(p2.iloc[i]),
                    "pnl_p1": float(pnl_p1), "pnl_p2": float(pnl_p2),
                    "pnl": float(pnl_p1 + pnl_p2), "exit_reason": exit_reason})
                in_position = False
                position = None
    return trades

def score_thresholds(trades):
    """Score a set of trades by Sharpe-like ratio."""
    if not trades:
        return -999, 0, 0, 0, 0
    pnls = np.array([t["pnl"] for t in trades])
    total_pnl = float(pnls.sum())
    wins = int((pnls > 0).sum())
    total = len(pnls)
    win_rate = wins / total
    avg_pnl = float(pnls.mean())
    std_pnl = float(pnls.std()) if len(pnls) > 1 and pnls.std() > 0 else 1.0
    sharpe = avg_pnl / std_pnl * np.sqrt(max(total, 1))
    score = sharpe + win_rate * 3.0 + (total_pnl / 5000) * 0.5
    return round(score, 3), round(sharpe, 3), total, round(win_rate * 100, 1), round(total_pnl, 2)

def walkforward_optimize(zs, p1, p2, entry_vals, exit_vals, n_windows=3):
    """Walk-forward grid search: train on expanding windows, average score.

    Returns (best_entry, best_exit, best_score, best_stats).
    """
    total = len(zs)
    wf_size = total // (n_windows + 1)
    if wf_size < 500:
        # Too little data: use single full-sample pass
        wf_windows = [(0, total)]
    else:
        wf_windows = [(0, wf_size * (w + 1)) for w in range(n_windows)]

    best_score = -999
    best_entry = None
    best_exit = None
    best_stats = None

    for entry_z in entry_vals:
        for exit_z in exit_vals:
            if exit_z >= entry_z:
                continue
            wf_scores = []
            total_trades = 0
            for tr_end in wf_windows:
                zs_tr = zs.iloc[:tr_end[1]]
                p1_tr = p1.loc[zs_tr.index]
                p2_tr = p2.loc[zs_tr.index]
                trades = simulate_thresholds(zs_tr, p1_tr, p2_tr, entry_z, exit_z)
                sc, _, n_t, _, _ = score_thresholds(trades)
                wf_scores.append(sc)
                total_trades += n_t
            avg_score = float(np.mean(wf_scores)) if wf_scores else -999
            if avg_score > best_score:
                best_score = avg_score
                best_entry = entry_z
                best_exit = exit_z
                # Final full-sample stats for display
                full_trades = simulate_thresholds(zs, p1, p2, entry_z, exit_z)
                _, best_sharpe, best_n, best_wr, best_pnl = score_thresholds(full_trades)
                best_stats = (best_sharpe, best_n + total_trades * 0, best_wr, best_pnl)

    return best_entry, best_exit, best_score, best_stats

def run(months=6, pair_filter=None):
    print("=== PairTrading Threshold Optimizer (Walk-Forward Grid Search) ===")
    print("Parameters: roll_window=%d, months=%d" % (ROLL_WIN, months))

    # Build lot size lookup
    _LOT_CACHE = {e["Symbol"]: e["LotSize"] for e in get_nifty200()}
    def _lot_size(sym):
        return _LOT_CACHE.get(sym, 1)

    if not os.path.exists(THRESHOLDS_FILE):
        print("ERROR: No pair_thresholds.json found")
        return

    with open(THRESHOLDS_FILE) as f:
        thresholds = json.load(f)

    if pair_filter:
        thresholds = {k: v for k, v in thresholds.items() if pair_filter in k}

    print("Pairs to optimize: %d" % len(thresholds))

    all_stocks = set()
    for pair_key in thresholds:
        s1, s2 = pair_key.split("|")
        all_stocks.add(s1.replace(".NS", ""))
        all_stocks.add(s2.replace(".NS", ""))
    all_stocks = list(all_stocks)

    raw = load_data(all_stocks, months)
    if raw is None:
        print("ERROR: No data available")
        return

    closes = prepare_closes(raw)
    if closes is None or len(closes) < ROLL_WIN:
        print("ERROR: Only %d 1H candles (need %d)" % (len(closes) if closes is not None else 0, ROLL_WIN))
        return

    print("Data: %d hourly candles, %s to %s" % (len(closes), closes.index[0], closes.index[-1]))

    entry_vals = [round(x, 2) for x in np.arange(0.5, 3.25, 0.25)]
    exit_vals = [round(x, 2) for x in np.arange(0.25, 2.75, 0.25)]

    results = {}
    improved_count = 0
    for i, (pair_key, cfg) in enumerate(thresholds.items()):
        s1, s2 = pair_key.split("|")
        hr_val = cfg.get("hr")
        old_entry = cfg.get("entry_z", 2.0)
        old_exit = cfg.get("exit_z", 0.5)

        if hr_val is None:
            print("  [%d/%d] %s: SKIP (no hedge ratio)" % (i+1, len(thresholds), pair_key))
            continue
        s1c, s2c = s1.replace(".NS", ""), s2.replace(".NS", "")
        if s1c not in closes.columns or s2c not in closes.columns:
            print("  [%d/%d] %s: SKIP (missing data)" % (i+1, len(thresholds), pair_key))
            continue

        print("  [%d/%d] %s (hr=%.4f, old entry=%.2f exit=%.2f)..." % (
            i+1, len(thresholds), pair_key, hr_val, old_entry, old_exit), end=" ", flush=True)

        zs, p1, p2 = compute_z_score_series(closes, s1, s2, hr_val)
        if zs is None:
            print("SKIP (z-score failed)")
            continue

        best_entry, best_exit, best_score, best_stats = walkforward_optimize(
            zs, p1, p2, entry_vals, exit_vals, n_windows=3)

        if best_entry is not None:
            lot1 = _lot_size(s1)
            lot2 = _lot_size(s2)

            # Scale per-unit P&L by actual lot sizes
            def _scale_trades(trades):
                scaled = []
                for t in trades:
                    t = dict(t)
                    t["pnl"] = t["pnl_p1"] * lot1 + t["pnl_p2"] * lot2
                    scaled.append(t)
                return scaled

            old_trades_raw = simulate_thresholds(zs, p1, p2, old_entry, old_exit)
            old_trades = _scale_trades(old_trades_raw)
            old_score, _, _, _, old_pnl = score_thresholds(old_trades)

            # Best full-sample trades scaled
            best_trades_raw = simulate_thresholds(zs, p1, p2, best_entry, best_exit)
            best_trades = _scale_trades(best_trades_raw)
            best_score_val, best_sharpe_val, best_n_val, best_wr_val, best_pnl_val = score_thresholds(best_trades)
            best_score = best_score_val if best_score_val > -999 else best_score
            best_stats = (best_sharpe_val, best_n_val, best_wr_val, best_pnl_val)

            improved = "IMPROVED" if best_score > old_score else "ok"
            if best_score > old_score:
                improved_count += 1

            results[pair_key] = {
                "entry_z": best_entry,
                "exit_z": best_exit,
                "score": best_score,
                "sharpe": best_stats[0],
                "trades": best_stats[1],
                "win_rate": best_stats[2],
                "pnl": best_stats[3],
                "old_entry": old_entry,
                "old_exit": old_exit,
                "old_score": old_score,
                "ol_pnl": old_pnl,
                "improved": improved,
            }
            print("entry=%.2f exit=%.2f score=%.1f sharpe=%.2f trades=%d WR=%.0f%% P&L=Rs.%+d [%s]" % (
                best_entry, best_exit, best_score, best_stats[0], best_stats[1],
                best_stats[2], best_stats[3], improved))
        else:
            print("FAILED")

    print("\n=== Results Summary ===")
    print("Pairs improved: %d/%d" % (improved_count, len(results)))

    updated = json.load(open(THRESHOLDS_FILE))
    changed = 0
    for pair_key, r in results.items():
        if pair_key in updated and r["pnl"] > 0 and r["score"] > r.get("old_score", -999):
            old_entry = updated[pair_key].get("entry_z")
            old_exit = updated[pair_key].get("exit_z")
            if r["entry_z"] != old_entry or r["exit_z"] != old_exit:
                updated[pair_key]["entry_z"] = r["entry_z"]
                updated[pair_key]["exit_z"] = r["exit_z"]
                updated[pair_key]["optimized_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                updated[pair_key]["opt_score"] = r["score"]
                updated[pair_key]["opt_sharpe"] = r["sharpe"]
                updated[pair_key]["opt_trades"] = r["trades"]
                updated[pair_key]["opt_win_rate"] = r["win_rate"]
                changed += 1

    with open(THRESHOLDS_FILE, "w") as f:
        json.dump(updated, f, indent=2)
    print("Updated %d/%d pairs in %s" % (changed, len(results), THRESHOLDS_FILE))

    ranked = sorted(results.values(), key=lambda x: -x["score"])[:10]
    print("\n=== Top 10 by Score ===")
    for r in ranked:
        print("  entry=%.2f exit=%.2f score=%.1f sharpe=%.2f trades=%d WR=%.0f%% P&L=%.0f" % (
            r["entry_z"], r["exit_z"], r["score"], r["sharpe"], r["trades"], r["win_rate"], r["pnl"]))

def maintenance():
    """Auto-remove degenerated pairs and re-estimate hedge ratios.

    Run monthly: python PairTrading/optimizer.py --maintenance
    """
    print("=== PairTrading Maintenance ===")
    if not os.path.exists(THRESHOLDS_FILE):
        print("ERROR: No pair_thresholds.json found")
        return

    from pairtrading.core.pair_discovery import reestimate_hedge_ratio

    with open(THRESHOLDS_FILE) as f:
        thresholds = json.load(f)

    print("Checking %d pairs..." % len(thresholds))
    purged = 0
    updated_hr = 0
    new_thresholds = {}

    for pair_key, cfg in thresholds.items():
        s1, s2 = pair_key.split("|")
        print("  %s..." % pair_key, end=" ", flush=True)
        try:
            hr_new, corr_new, coint_p = reestimate_hedge_ratio(s1, s2, lookback_days=180)
            print("hr=%.4f corr=%.4f coint_p=%.6f" % (hr_new, corr_new, coint_p), end=" ")

            if coint_p > 0.10:
                print("PURGED (coint p=%.4f > 0.10)" % coint_p)
                purged += 1
                continue

            cfg["hr"] = hr_new
            cfg["hr_updated_at"] = datetime.now().strftime("%Y-%m-%d")
            cfg["last_coint_p"] = coint_p
            new_thresholds[pair_key] = cfg
            updated_hr += 1
            print("OK")
        except Exception as e:
            print("ERROR: %s — removing" % e)
            purged += 1

    # Auto-replenish: discover new same-sector pairs from Nifty 100
    print("\n--- Auto-Replenish ---")
    try:
        from pairtrading.core.pair_discovery import discover_pairs
        from pairtrading.configs.symbols import get_nifty100
        print("Discovering new same-sector pairs from Nifty 100...")
        df_new = discover_pairs(get_nifty100(), corr_threshold=0.80,
                                pvalue_threshold=0.05, years=2,
                                require_same_sector=True)
        if df_new is not None and not df_new.empty:
            existing_keys = set(new_thresholds.keys())
            n_added = 0
            for _, r in df_new.iterrows():
                pk = "%s|%s" % (r["Stock1"], r["Stock2"])
                if pk in existing_keys:
                    continue
                new_thresholds[pk] = {
                    "entry_z": 2.0, "exit_z": 0.5,
                    "hr": float(r["Hedge_Ratio"]),
                    "added_at": datetime.now().strftime("%Y-%m-%d"),
                }
                print("  ADDED %s (corr=%.2f, coint_p=%.4f)" % (
                    pk, r["Correlation"], r["Coint_PValue"]))
                n_added += 1
                if n_added >= 5:
                    break
            print("Added %d new pair(s)." % n_added)
        else:
            print("No new pairs discovered.")
    except Exception as e:
        print("Auto-replenish skipped: %s" % e)

    with open(THRESHOLDS_FILE, "w") as f:
        json.dump(new_thresholds, f, indent=2)
    print("\nDone. %d pairs checked, %d purged, %d hedge ratios updated, %d replenished." % (
        len(thresholds), purged, updated_hr,
        len(new_thresholds) - len([k for k in new_thresholds if k in thresholds])))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PairTrading Threshold Optimizer")
    parser.add_argument("--months", type=int, default=6, help="Lookback months for backtest data")
    parser.add_argument("--pair", type=str, default=None, help="Optimize a single pair (e.g. INFY.NS|TCS.NS)")
    parser.add_argument("--maintenance", action="store_true", help="Purge degenerated pairs + re-estimate hedge ratios")
    args = parser.parse_args()
    if args.maintenance:
        maintenance()
    else:
        run(months=args.months, pair_filter=args.pair)
