"""
Full analysis with ROLL_WIN=63. Extended metrics.
"""
import sys, os, json, math
_me = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(_me))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'PairTrading'))
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from common.market_data.cache import get_cache
from PairTrading.reports.pair_trading import _run_pair_backtest, _compute_metrics, _compute_sharpe
from configs.symbols import get_nifty200

TH_FILE = os.path.join(ROOT, 'PairTrading', 'configs', 'pair_thresholds.json')
with open(TH_FILE, encoding='utf-8') as f:
    all_th = json.load(f)

_lot_map = {e['Symbol']: e['LotSize'] for e in get_nifty200()}
def lot_size(s): return _lot_map.get(s, 1)

cache = get_cache()

def load_hourly(s1, s2, days=60):
    end = datetime.now()
    start = end - timedelta(days=days)
    s1c, s2c = s1.replace('.NS',''), s2.replace('.NS','')
    import duckdb as _duckdb
    con = _duckdb.connect(cache.db_path, read_only=True)
    query = f"""SELECT ticker, datetime_ist, close FROM hourly_bars
        WHERE ticker IN ('{s1c}', '{s2c}')
        AND datetime_ist >= '{start.strftime("%Y-%m-%d")}' AND datetime_ist < '{end.strftime("%Y-%m-%d")}'
        ORDER BY datetime_ist"""
    dfh = con.execute(query).df()
    con.close()
    if dfh.empty: return None, None, None
    p1 = dfh[dfh['ticker']==s1c].set_index('datetime_ist')['close']
    p2 = dfh[dfh['ticker']==s2c].set_index('datetime_ist')['close']
    ch = pd.concat([p1, p2], axis=1).dropna()
    if ch.empty: return None, None, None
    ts = pd.to_datetime(ch.index)
    mkt = (ts.hour*60+ts.minute >= 555) & (ts.hour*60+ts.minute <= 930)
    ch = ch[mkt]
    ch.index = ts[mkt]
    if ch.empty: return None, None, None
    return ch.iloc[:,0], ch.iloc[:,1], ch

def calc_duration(trades):
    """Estimate trade duration in days."""
    durations = []
    for t in trades:
        try:
            entry = pd.Timestamp(t['Entry'])
            exit_ = pd.Timestamp(t['Exit'])
            durations.append((exit_ - entry).total_seconds() / 3600)  # hours
        except:
            pass
    return durations

print('='*110)
print('FULL BACKTEST METRICS — ROLL_WIN=63, Hourly, 60 days')
print('='*110)

all_rows = []
all_trades_flat = []
total_capitals = []

for pk, th in sorted(all_th.items()):
    s1, s2 = pk.split('|')
    hr = th['hr']; ez = th['entry_z']; xz = th['exit_z']
    lot1, lot2 = lot_size(s1), lot_size(s2)
    l1, l2 = s1.replace('.NS',''), s2.replace('.NS','')
    bp1, bp2, ch = load_hourly(s1, s2)
    if bp1 is None: continue
    spread = bp1 - hr * bp2
    sm = spread.rolling(63).mean(); ss = spread.rolling(63).std()
    zs = ((spread - sm) / ss).dropna()
    if len(zs) < 5: continue
    trades = _run_pair_backtest(bp1, bp2, spread, zs, hr, l1, l2, lot1, lot2, ez, xz)
    m = _compute_metrics(trades) or {}
    sharpe = _compute_sharpe(trades) if trades else 0

    for t in trades:
        all_trades_flat.append(t)

    # Extended metrics
    wins = [t for t in trades if t['P&L'] > 0]
    losses = [t for t in trades if t['P&L'] <= 0]
    avg_win = sum(t['P&L'] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t['P&L'] for t in losses) / len(losses) if losses else 0
    best_trade = max([t['P&L'] for t in trades]) if trades else 0
    worst_trade = min([t['P&L'] for t in trades]) if trades else 0
    durations = calc_duration(trades)
    avg_dur = np.mean(durations) if durations else 0

    # Consecutive wins/losses
    max_consec_w = max_consec_l = cur_w = cur_l = 0
    for t in trades:
        if t['P&L'] > 0: cur_w += 1; cur_l = 0
        else: cur_l += 1; cur_w = 0
        max_consec_w = max(max_consec_w, cur_w)
        max_consec_l = max(max_consec_l, cur_l)

    # Capital deployed
    capitals = [abs(t['s1_entry']*lot1) + abs(t['s2_entry']*lot2) for t in trades]
    avg_cap = np.mean(capitals) if capitals else 0
    total_capitals.extend(capitals)

    pair_pnl = m.get('total_pnl', 0)
    calmar = pair_pnl / m.get('max_dd', 1) if m.get('max_dd', 0) > 0 else float('inf')
    ret_on_cap = pair_pnl / avg_cap * 100 if avg_cap > 0 else 0

    print(f'\n  {l1:15s} / {l2:15s}')
    print(f'    Thresholds:     ±{ez:.2f} / {xz:.2f}  |  HR: {hr:.4f}  |  Lots: {lot1}, {lot2}')
    print(f'    Trades:         {m.get("total",0):3d}  |  Wins: {len(wins):2d}  |  Losses: {len(losses):2d}')
    print(f'    Win Rate:       {m.get("win_rate",0):.1f}%  |  Avg Win: Rs.{avg_win:+,.0f}  |  Avg Loss: Rs.{avg_loss:+,.0f}')
    print(f'    Total P&L:      Rs.{pair_pnl:+,.0f}  |  Profit Factor: {m.get("profit_factor",0):.2f}')
    print(f'    Best/Worst:     Rs.{best_trade:+,.0f} / Rs.{worst_trade:+,.0f}')
    print(f'    Sharpe:         {sharpe:.2f}  |  Calmar: {calmar:.2f}')
    print(f'    Max DD:         Rs.{m.get("max_dd",0):+,.0f}  |  ROC: {ret_on_cap:.1f}%')
    print(f'    Max Consec W/L: {max_consec_w}W / {max_consec_l}L  |  Avg Duration: {avg_dur:.1f}h')
    print(f'    Avg Capital:    Rs.{avg_cap:,.0f}')

    all_rows.append({
        'Pair': f'{l1}/{l2}', 'EZ': ez, 'XZ': xz,
        'Trades': m.get("total",0), 'WR': f'{m.get("win_rate",0):.0f}%',
        'P&L': round(pair_pnl,0), 'PF': round(m.get("profit_factor",0),1),
        'Sharpe': round(sharpe,2), 'MaxDD': round(m.get("max_dd",0),0),
        'AvgW': round(avg_win,0), 'AvgL': round(avg_loss,0),
        'Best': round(best_trade,0), 'Worst': round(worst_trade,0),
        'AvgDur': f'{avg_dur:.0f}h', 'ROC': f'{ret_on_cap:.1f}%',
        'Cap': round(avg_cap,0),
    })

# Aggregate
print('\n' + '='*110)
print('AGGREGATE METRICS (all 14 pairs combined)')
print('='*110)

total_trades = len(all_trades_flat)
total_pnl = sum(t['P&L'] for t in all_trades_flat)
total_wins = sum(1 for t in all_trades_flat if t['P&L'] > 0)
total_losses = total_trades - total_wins
avg_pnl = total_pnl / total_trades if total_trades > 0 else 0
avg_win_agg = sum(t['P&L'] for t in all_trades_flat if t['P&L'] > 0) / total_wins if total_wins > 0 else 0
avg_loss_agg = sum(t['P&L'] for t in all_trades_flat if t['P&L'] <= 0) / total_losses if total_losses > 0 else 0
win_rate = total_wins / total_trades * 100 if total_trades > 0 else 0
sharpe_agg = _compute_sharpe(all_trades_flat) if all_trades_flat else 0
gross_profit = sum(t['P&L'] for t in all_trades_flat if t['P&L'] > 0)
gross_loss = abs(sum(t['P&L'] for t in all_trades_flat if t['P&L'] <= 0))
pf_agg = gross_profit / gross_loss if gross_loss > 0 else float('inf')
max_dd_agg = max([a['MaxDD'] for a in all_rows]) if all_rows else 0
avg_cap_agg = np.mean(total_capitals) if total_capitals else 0

durations = calc_duration(all_trades_flat)
avg_dur_agg = np.mean(durations) if durations else 0

# Consecutive wins/losses (sorted by entry time)
sorted_trades = sorted(all_trades_flat, key=lambda t: t['Entry'])
max_cw = max_cl = cw = cl = 0
for t in sorted_trades:
    if t['P&L'] > 0: cw += 1; cl = 0
    else: cl += 1; cw = 0
    max_cw = max(max_cw, cw); max_cl = max(max_cl, cl)

roc_agg = total_pnl / avg_cap_agg * 100 if avg_cap_agg > 0 else 0
calmar_agg = total_pnl / max_dd_agg if max_dd_agg > 0 else float('inf')

print(f'\n  Total Trades:          {total_trades}')
print(f'  Wins / Losses:         {total_wins}W / {total_losses}L')
print(f'  Win Rate:              {win_rate:.1f}%')
print(f'')
print(f'  Total P&L:             Rs.{total_pnl:+,.0f}')
print(f'  Avg Trade P&L:         Rs.{avg_pnl:+,.0f}')
print(f'  Avg Win:               Rs.{avg_win_agg:+,.0f}')
print(f'  Avg Loss:              Rs.{avg_loss_agg:+,.0f}')
print(f'  Best Trade:            Rs.{max([t["P&L"] for t in all_trades_flat]):+,.0f}' if all_trades_flat else '')
print(f'  Worst Trade:           Rs.{min([t["P&L"] for t in all_trades_flat]):+,.0f}' if all_trades_flat else '')
print(f'')
print(f'  Profit Factor:         {pf_agg:.2f}')
print(f'  Sharpe Ratio:          {sharpe_agg:.2f}')
print(f'  Calmar Ratio:          {calmar_agg:.2f}')
print(f'  Max Drawdown:          Rs.{max_dd_agg:+,.0f}')
print(f'  Return on Capital:     {roc_agg:.1f}%')
print(f'')
print(f'  Max Consec Wins:       {max_cw}')
print(f'  Max Consec Losses:     {max_cl}')
print(f'  Avg Trade Duration:    {avg_dur_agg:.1f} hours ({avg_dur_agg/6.5:.1f} trading days)')
print(f'')
print(f'  Avg Capital/Trade:     Rs.{avg_cap_agg:,.0f}')
print(f'  Max Position Cost:     Rs.{max(total_capitals):,.0f}' if total_capitals else '')
print(f'  3 Concurrent (cap):    Rs.{max(total_capitals) * 3:,.0f}' if total_capitals else '')
print(f'  Recommended Capital:   Rs.{max(total_capitals) * 3 * 1.2:,.0f}' if total_capitals else '')

# Weekly
print(f'\n{"="*110}')
print('WEEKLY BREAKDOWN')
print('='*110)
weekly = {}
for t in all_trades_flat:
    w = pd.Timestamp(t['Entry']).strftime('%Y-W%W')
    weekly.setdefault(w, []).append(t)

end = datetime.now(); start = end - timedelta(days=60)
cumulative = 0
for wk_start in pd.date_range(start, end, freq='W-MON'):
    w = wk_start.strftime('%Y-W%W')
    wt = weekly.get(w, [])
    w_pnl = sum(t['P&L'] for t in wt); cumulative += w_pnl
    w_wins = sum(1 for t in wt if t['P&L'] > 0)
    w_avg = w_pnl / len(wt) if wt else 0
    label = wk_start.strftime('%b %d')
    if wt:
        print(f'  {label:8s} {w}: {len(wt):2d} trades  '
              f'{w_wins}W/{len(wt)-w_wins}L  wr={w_wins/len(wt)*100:.0f}%  '
              f'pnl={w_pnl:+8.0f}  avg={w_avg:+7.0f}  cum={cumulative:+8.0f}')
    else:
        print(f'  {label:8s} {w}:  0 trades  {" "*19}  pnl=     +0  cum={cumulative:+8.0f}')

print(f'\n  TOTAL: {total_trades} trades, Rs.{cumulative:+,.0f}')
