import argparse, sys, os, json, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
import pandas as pd
import numpy as np
from PairTrading.reports.pair_trading import _run_pair_backtest, _compute_metrics, _save_bt
from common.market_data.cache import get_cache
from datetime import datetime, timedelta

def run_walk_forward(s1, s2, hr, lookback_months=9):
    cache = get_cache()
    end = datetime.now()
    start = end - timedelta(days=730)
    cache.ensure_fresh([s1, s2], "daily")
    df = cache.get_bulk_multiindex([s1, s2], start.strftime("%Y-%m-%d"),
                                    end.strftime("%Y-%m-%d"), interval="1d")
    if df.empty or not isinstance(df.columns, pd.MultiIndex):
        return None
    p1 = df['Close'][s1].copy()
    p2 = df['Close'][s2].copy()
    combined = pd.concat([p1, p2], axis=1).dropna()
    if combined.empty:
        return None
    p1, p2 = combined.iloc[:, 0], combined.iloc[:, 1]
    spread = p1 - hr * p2
    spread_mean = spread.rolling(21).mean()
    spread_std = spread.rolling(21).std()
    zscore = ((spread - spread_mean) / spread_std).dropna()
    if len(zscore) < 20:
        return None

    entry_vals = [round(x, 2) for x in np.arange(0.5, 3.25, 0.25)]
    exit_vals = [round(x, 2) for x in np.arange(0.25, 2.75, 0.25)]
    best_score = -999
    best_entry = None
    best_exit = None
    best_trades = []
    best_metrics = {}
    n_windows = 3
    total_len = len(zscore)

    for ei, entry_z_v in enumerate(entry_vals):
        for xi, exit_z_v in enumerate(exit_vals):
            if exit_z_v >= entry_z_v:
                continue
            wf_scores = []
            all_trades = []
            wf_size = total_len // (n_windows + 1)
            wf_windows = [(0, total_len)] if wf_size < 500 else [(0, wf_size * (w + 1)) for w in range(n_windows)]
            for tr_end in wf_windows:
                zs_tr = zscore.iloc[:tr_end[1]]
                p1_tr = p1.loc[zs_tr.index]
                p2_tr = p2.loc[zs_tr.index]
                trades = _run_pair_backtest(p1_tr, p2_tr, spread, zs_tr, hr,
                                            s1.replace('.NS',''), s2.replace('.NS',''),
                                            1, 1, entry_z_v, exit_z_v)
                m = _compute_metrics(trades)
                wf_scores.append(m["total_pnl"] if m and m["total"] >= 2 else -999)
                if tr_end == wf_windows[-1]:
                    all_trades = trades
            avg_pnl = float(np.mean(wf_scores)) if wf_scores else -999
            if avg_pnl > best_score:
                best_score = avg_pnl
                best_entry = entry_z_v
                best_exit = exit_z_v
                best_trades = all_trades
                best_metrics = _compute_metrics(all_trades) or {}
    return dict(entry_z=best_entry, exit_z=best_exit, trades=len(best_trades),
                total_pnl=best_metrics.get('total_pnl', 0),
                win_rate=best_metrics.get('win_rate', 0),
                profit_factor=best_metrics.get('profit_factor', 0))

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--pair-index', type=int, default=0)
    args = parser.parse_args()
    df = pd.read_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'pairs.csv'))
    if args.pair_index >= len(df):
        print(f'Only {len(df)} pairs available')
        sys.exit(1)
    r = df.iloc[args.pair_index]
    s1, s2, hr = r['Stock1'], r['Stock2'], r['Hedge_Ratio']
    print(json.dumps({"pair": f"{s1}|{s2}", "hr": hr, "corr": r['Correlation']}))
    result = run_walk_forward(s1, s2, hr)
    print(json.dumps(result))
