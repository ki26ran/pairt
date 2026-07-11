"""
PairTrading Backtest Page — configurable backtest with date range, max pairs,
instrument type (options/futures), and timeframe selection.
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime, timedelta
import os, sys, json

from pairtrading.configs.settings import DATA_DIR
from pairtrading.configs.symbols import get_nifty200
from pairtrading.live.cache import get_pair_cache
from common.market_data.cache import get_cache

THRESHOLDS_FILE = os.path.join(os.path.dirname(DATA_DIR), "configs", "pair_thresholds.json")

def _run_pair_backtest(p1, p2, spread, zscore, hr, l1, l2, lot1, lot2,
                       entry_z=2.0, exit_z=0.0, o1=None, o2=None):
    trades = []
    pos = 0
    entry_idx = None
    n = len(zscore)
    use_open = o1 is not None and o2 is not None
    stop_loss_z = entry_z * 3.0
    max_hold_bars = 160

    for i in range(1, n):
        prev_z = zscore.iloc[i - 1]
        curr_z = zscore.iloc[i]
        t = zscore.index[i]
        next_i = min(i + 1, n - 1)
        t_next = zscore.index[next_i]

        fill_entry_p1 = o1.loc[t_next] if use_open and next_i != i else p1.loc[t]
        fill_entry_p2 = o2.loc[t_next] if use_open and next_i != i else p2.loc[t]
        fill_exit_p1 = o1.loc[t_next] if use_open and next_i != i else p1.loc[t]
        fill_exit_p2 = o2.loc[t_next] if use_open and next_i != i else p2.loc[t]

        if pos == 0:
            if prev_z <= -entry_z and curr_z > -entry_z:
                pos = 1; entry_idx = next_i if use_open else i
            elif prev_z >= entry_z and curr_z < entry_z:
                pos = -1; entry_idx = next_i if use_open else i

        elif pos != 0:
            exit_reason = None
            if pos == 1 and prev_z < exit_z and curr_z >= exit_z:
                exit_reason = "mean-reversion"
            elif pos == -1 and prev_z > -exit_z and curr_z <= -exit_z:
                exit_reason = "mean-reversion"
            elif abs(curr_z) >= stop_loss_z:
                exit_reason = "stop-loss"
            elif (i - entry_idx) >= max_hold_bars:
                exit_reason = "timeout"

            if exit_reason:
                p1e = o1.loc[zscore.index[entry_idx]] if use_open else p1.loc[zscore.index[entry_idx]]
                p2e = o2.loc[zscore.index[entry_idx]] if use_open else p2.loc[zscore.index[entry_idx]]
                p1x, p2x = fill_exit_p1, fill_exit_p2
                if pos == 1:
                    s1_pnl = (p1x - p1e) * lot1; s2_pnl = (p2e - p2x) * lot2
                else:
                    s1_pnl = (p1e - p1x) * lot1; s2_pnl = (p2x - p2e) * lot2
                pnl = s1_pnl + s2_pnl
                notional = p1e * lot1 + p2e * lot2
                pnl_pct = pnl / notional * 100 if notional else 0
                trades.append({"Entry": zscore.index[entry_idx], "Exit": t_next if use_open else t,
                               "Direction": "LONG" if pos == 1 else "SHORT",
                               "exit_reason": exit_reason,
                               "s1_P&L": round(s1_pnl, 2), "s2_P&L": round(s2_pnl, 2),
                               "P&L": round(pnl, 2), "P&L_%": round(pnl_pct, 2),
                               "s1_entry": round(p1e, 2), "s2_entry": round(p2e, 2),
                               "s1_exit": round(p1x, 2), "s2_exit": round(p2x, 2),
                               "Lot1": lot1, "Lot2": lot2})
                pos = 0
    return trades


def _option_premium(price, strike, direction, tv_pct=0.02):
    if direction == "LONG":
        intrinsic = max(0, price - strike)
    else:
        intrinsic = max(0, strike - price)
    return intrinsic + price * tv_pct


def _option_pnl(entry_p, exit_p, strike, direction, lot, tv_pct=0.02):
    entry_prem = _option_premium(entry_p, strike, direction, tv_pct)
    exit_prem = _option_premium(exit_p, strike, direction, tv_pct)
    return (exit_prem - entry_prem) * lot


def show():
    st.title("Backtest")
    st.markdown("Run pair trading backtest with custom parameters.")

    # ── Load thresholds ────────────────────────────────────────
    th_data = {}
    try:
        db_th = get_pair_cache().load_thresholds()
        th_data.update(db_th)
    except Exception:
        pass
    if os.path.exists(THRESHOLDS_FILE):
        with open(THRESHOLDS_FILE) as f:
            file_th = json.load(f)
            th_data.update({k: v for k, v in file_th.items() if k not in th_data})

    if not th_data:
        st.warning("No thresholds found. Run Discover Pairs or Optimize first.")
        return

    # ── Parameters ─────────────────────────────────────────────
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        from_date = st.date_input("From", value=datetime(2025, 9, 1))
    with col2:
        to_date = st.date_input("To", value=datetime(2026, 6, 30))
    with col3:
        max_pairs = st.number_input("Max pairs", min_value=1, max_value=50, value=5)
    with col4:
        instrument = st.selectbox("Instrument", ["Options (ATM)", "Futures"], index=0)

    # ── Run backtest ───────────────────────────────────────────
    if st.button("Run Backtest", type="primary", use_container_width=True):
        use_options = instrument.startswith("Options")
        cache = get_cache()
        start_str = from_date.strftime("%Y-%m-%d")
        end_str = to_date.strftime("%Y-%m-%d")

        # Load hourly data
        all_stocks = list(set([s for pair in th_data for s in pair.split("|")]))
        import duckdb
        con = duckdb.connect(cache.db_path, read_only=True)
        clean = [s.replace(".NS", "") for s in all_stocks]
        ph = ", ".join(["?" for _ in clean])
        df = con.execute(f"""
            SELECT ticker, datetime_ist, close FROM hourly_bars
            WHERE ticker IN ({ph}) AND datetime_ist >= ? AND datetime_ist < ?
            ORDER BY datetime_ist
        """, clean + [start_str, end_str]).df()
        con.close()

        if df.empty:
            st.error("No data available for the selected date range.")
            return

        df["datetime_ist"] = pd.to_datetime(df["datetime_ist"])
        closes = df.pivot_table(index="datetime_ist", columns="ticker", values="close", aggfunc="first")
        closes = closes.ffill()
        closes.index = pd.to_datetime(closes.index)
        mkt = (closes.index.hour * 60 + closes.index.minute >= 555) & \
              (closes.index.hour * 60 + closes.index.minute <= 930)
        closes = closes[mkt]

        lot_map = {e["Symbol"]: e["LotSize"] for e in get_nifty200()}

        progress = st.progress(0, text="Running backtest...")
        status = st.empty()

        all_trades = []
        pair_keys = sorted(th_data.keys())
        total = min(len(pair_keys), max_pairs * 3)

        active = {}
        pair_results = {}
        from collections import defaultdict

        all_ts = sorted(closes.index)
        ROLL_WIN = 63

        # Pre-compute z-scores
        pair_data = {}
        for pk in pair_keys:
            s1, s2 = pk.split("|")
            c1, c2 = s1.replace(".NS", ""), s2.replace(".NS", "")
            if c1 not in closes.columns or c2 not in closes.columns:
                continue
            hr = th_data[pk].get("hr", 1.0)
            entry_z = th_data[pk].get("entry_z", 2.0)
            exit_z = th_data[pk].get("exit_z", 0.5)
            p1 = closes[c1]; p2 = closes[c2]
            combined = pd.concat([p1, p2], axis=1).dropna()
            if len(combined) < ROLL_WIN + 1:
                continue
            spread = combined.iloc[:, 0] - hr * combined.iloc[:, 1]
            sm = spread.rolling(ROLL_WIN).mean()
            ss = spread.rolling(ROLL_WIN).std()
            z = ((spread - sm) / ss).dropna()
            pair_data[pk] = {"s1": s1, "s2": s2, "hr": hr,
                             "entry_z": entry_z, "exit_z": exit_z,
                             "z": z, "p1": p1, "p2": p2,
                             "lot1": lot_map.get(s1, 1), "lot2": lot_map.get(s2, 1)}

        # Simulate
        from datetime import date
        all_ts = sorted(set(ts for pd_ in pair_data.values() for ts in pd_["z"].index))
        daily_max = defaultdict(int)

        for ts in all_ts:
            # Exits
            for pk in list(active.keys()):
                pos = active[pk]; pd_ = pair_data.get(pk)
                if pd_ is None or ts not in pd_["z"].index:
                    continue
                zv = pd_["z"].loc[ts]
                cp1, cp2 = pd_["p1"].loc[ts], pd_["p2"].loc[ts]
                days_held = (ts - pos["entry_date"]).days

                exit_reason = None
                if pos["direction"] == "SHORT":
                    if zv <= -pd_["exit_z"]:
                        exit_reason = "mean-reversion"
                    elif zv >= pos["entry_z_actual"] * 2.0:
                        exit_reason = "stop-loss"
                if days_held >= 20:
                    exit_reason = "timeout"

                if exit_reason:
                    if use_options:
                        tick = max(0.05, pos["entry_p1"] * 0.001)
                        atm = round(pos["entry_p1"] / tick) * tick
                        pnl_s1 = _option_pnl(pos["entry_p1"], cp1, atm, "SHORT", pd_["lot1"])
                        pnl_s2 = _option_pnl(pos["entry_p2"], cp2, atm, "LONG", pd_["lot2"])
                    else:
                        pnl_s1 = (pos["entry_p1"] - cp1) * pd_["lot1"]
                        pnl_s2 = (cp2 - pos["entry_p2"]) * pd_["lot2"]
                    total_pnl = round(pnl_s1 + pnl_s2, 2)
                    all_trades.append({"pair": pk, "entry_date": pos["entry_date"], "exit_date": ts,
                                       "direction": "SHORT", "total_pnl": total_pnl, "reason": exit_reason})
                    del active[pk]

            # Entries
            if len(active) < max_pairs:
                open_sectors = set()
                for pk in sorted(pair_data.keys()):
                    if len(active) >= max_pairs:
                        break
                    if pk in active:
                        continue
                    pd_ = pair_data[pk]
                    if ts not in pd_["z"].index:
                        continue
                    zv = pd_["z"].loc[ts]
                    if zv < pd_["entry_z"]:
                        continue
                    cp1, cp2 = pd_["p1"].loc[ts], pd_["p2"].loc[ts]
                    active[pk] = {"entry_date": ts, "entry_p1": cp1, "entry_p2": cp2,
                                  "entry_z_actual": zv, "direction": "SHORT"}
                    open_sectors.add(pk)

            d = ts.date() if hasattr(ts, 'date') else ts
            daily_max[d] = max(daily_max.get(d, 0), len(active))

        # Report
        if not all_trades:
            st.warning("No trades generated with the selected parameters.")
            return

        tdf = pd.DataFrame(all_trades)
        total_pnl = tdf["total_pnl"].sum()
        wins = (tdf["total_pnl"] > 0).sum()
        total = len(tdf)
        wr = wins / total * 100 if total else 0

        st.success(f"Backtest complete: {total} trades, Net P&L Rs {total_pnl:+,.0f}, WR {wr:.0f}%")

        # Per-pair breakdown
        pair_pnl = tdf.groupby("pair")["total_pnl"].agg(["sum", "count"])
        pair_pnl.columns = ["P&L", "Trades"]
        pair_pnl = pair_pnl.sort_values("P&L", ascending=False)
        st.subheader("Per-Pair P&L")
        st.dataframe(pair_pnl.style.format({"P&L": "Rs {:,.0f}"}), use_container_width=True)

        # Monthly
        tdf["month"] = tdf["entry_date"].apply(lambda x: str(x)[:7])
        monthly = tdf.groupby("month")["total_pnl"].sum()
        st.subheader("Monthly P&L")
        st.dataframe(monthly.to_frame("P&L").style.format({"P&L": "Rs {:+,.0f}"}), use_container_width=True)

        # Concurrent
        avg_conc = sum(daily_max.values()) / len(daily_max) if daily_max else 0
        st.caption(f"Max concurrent: {max(daily_max.values()) if daily_max else 0}, Avg: {avg_conc:.1f}")

        # Monthly chart
        fig = go.Figure()
        fig.add_trace(go.Bar(x=list(monthly.index), y=list(monthly.values),
                             marker_color=["#00e676" if v >= 0 else "#ff5252" for v in monthly.values()]))
        fig.update_layout(title="Monthly P&L", height=350, template="plotly_dark",
                          xaxis_title="Month", yaxis_title="P&L (Rs)")
        st.plotly_chart(fig, use_container_width=True)
