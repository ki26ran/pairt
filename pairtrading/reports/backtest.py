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
    diff = float(price) - float(strike)
    if direction == "LONG":
        intrinsic = diff if diff > 0 else 0.0
    else:
        intrinsic = -diff if diff < 0 else 0.0
    return intrinsic + float(price) * tv_pct


def _option_pnl(entry_p, exit_p, strike, direction, lot, tv_pct=0.02):
    entry_prem = _option_premium(entry_p, strike, direction, tv_pct)
    exit_prem = _option_premium(exit_p, strike, direction, 0.0)  # no time value at exit (near-expiry)
    return (exit_prem - entry_prem) * lot


def show():
    st.title("📊 Backtest")
    st.markdown("Run pair trading backtest with custom parameters.")
    st.markdown(
        "<div style='background:#1a3a5c;border-radius:6px;padding:8px 14px;font-size:13px;color:#ccc'>"
        "<b>Workflow:</b> "
        "Step 1: Discover → "
        "<span style='color:#1f77b4;font-weight:bold'>Step 2: Backtest</span> → "
        "Step 3: Optimize → "
        "Step 4: Monitor"
        "</div>",
        unsafe_allow_html=True,
    )

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
        instrument = st.selectbox("Instrument", ["Options (ATM)", "Futures", "Options + Rs40K SL", "Options + Rs50K SL"], index=0)

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
                use_hard_sl = use_options and "Rs" in instrument
                hard_sl_amount = 50000
                if use_hard_sl:
                    import re
                    m = re.search(r"Rs(\d+)K", instrument)
                    if m:
                        hard_sl_amount = int(m.group(1)) * 1000

                # Hard SL check (running P&L)
                if use_hard_sl:
                    # Estimate current P&L for this position
                    curr_p1 = float(cp1); curr_p2 = float(cp2)
                    ep1 = float(pos["entry_p1"]); ep2 = float(pos["entry_p2"])
                    if use_options:
                        tick1 = 0.05 if 0.05 > ep1 * 0.001 else ep1 * 0.001
                        tick2 = 0.05 if 0.05 > ep2 * 0.001 else ep2 * 0.001
                        atm1 = round(ep1 / tick1) * tick1
                        atm2 = round(ep2 / tick2) * tick2
                        sl_pnl_s1 = _option_pnl(ep1, curr_p1, atm1, "SHORT", pd_["lot1"])
                        sl_pnl_s2 = _option_pnl(ep2, curr_p2, atm2, "LONG", pd_["lot2"])
                    else:
                        sl_pnl_s1 = (ep1 - curr_p1) * pd_["lot1"]
                        sl_pnl_s2 = (curr_p2 - ep2) * pd_["lot2"]
                    running_pnl = sl_pnl_s1 + sl_pnl_s2
                    if abs(running_pnl) >= hard_sl_amount:
                        exit_reason = "hard_sl"

                if not exit_reason and pos["direction"] == "SHORT":
                    if zv <= -pd_["exit_z"]:
                        exit_reason = "mean-reversion"
                    elif zv >= pos["entry_z_actual"] * 2.0:
                        exit_reason = "stop-loss"
                if days_held >= 20:
                    exit_reason = "timeout"

                if exit_reason:
                    if use_options:
                        tick1 = 0.05 if 0.05 > float(pos["entry_p1"]) * 0.001 else float(pos["entry_p1"]) * 0.001
                        tick2 = 0.05 if 0.05 > float(pos["entry_p2"]) * 0.001 else float(pos["entry_p2"]) * 0.001
                        atm1 = round(float(pos["entry_p1"]) / tick1) * tick1
                        atm2 = round(float(pos["entry_p2"]) / tick2) * tick2
                        pnl_s1 = _option_pnl(float(pos["entry_p1"]), float(cp1), atm1, "SHORT", pd_["lot1"])
                        pnl_s2 = _option_pnl(float(pos["entry_p2"]), float(cp2), atm2, "LONG", pd_["lot2"])
                    else:
                        pnl_s1 = (float(pos["entry_p1"]) - float(cp1)) * pd_["lot1"]
                        pnl_s2 = (float(cp2) - float(pos["entry_p2"])) * pd_["lot2"]
                    entry_time = pos["entry_date"]
                    exit_time = ts
                    s1, s2 = pk.split("|")
                    t_pnl = round(float(pnl_s1) + float(pnl_s2), 2)
                    all_trades.append({
                        "pair": pk, "s1": s1, "s2": s2,
                        "entry_date": entry_time, "exit_date": exit_time,
                        "entry_p1": round(float(pos["entry_p1"]), 2),
                        "exit_p1": round(float(cp1), 2),
                        "entry_p2": round(float(pos["entry_p2"]), 2),
                        "exit_p2": round(float(cp2), 2),
                        "pnl_s1": round(float(pnl_s1), 2),
                        "pnl_s2": round(float(pnl_s2), 2),
                        "total_pnl": t_pnl,
                        "direction": "SHORT", "reason": exit_reason,
                    })
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
            _pv = daily_max.get(d, 0)
            daily_max[d] = _pv if _pv > len(active) else len(active)

        # Report
        if not all_trades:
            st.warning("No trades generated with the selected parameters.")
            return

        tdf = pd.DataFrame(all_trades)
        total_pnl = float(tdf["total_pnl"].sum())
        wins = int((tdf["total_pnl"] > 0).sum())
        total = len(tdf)
        wr = wins / total * 100 if total else 0

        st.success(f"Backtest complete: {total} trades, Net P&L Rs {total_pnl:+,.0f}, WR {wr:.0f}%")

        # Trade log
        st.subheader("Trade Log")
        tdf["entry_str"] = tdf["entry_date"].apply(lambda x: str(x)[:16])
        tdf["exit_str"] = tdf["exit_date"].apply(lambda x: str(x)[:16])
        log_cols = ["entry_str", "exit_str", "s1", "s2", "entry_p1", "exit_p1", "entry_p2", "exit_p2",
                    "pnl_s1", "pnl_s2", "total_pnl", "reason"]
        log_df = tdf[log_cols].copy()
        log_df.columns = ["Entry Time", "Exit Time", "Leg1", "Leg2",
                          "L1 Entry", "L1 Exit", "L2 Entry", "L2 Exit",
                          "L1 P&L", "L2 P&L", "Total P&L", "Reason"]
        for c in ["L1 Entry", "L1 Exit", "L2 Entry", "L2 Exit"]:
            log_df[c] = log_df[c].apply(lambda x: f"{float(x):.2f}")
        for c in ["L1 P&L", "L2 P&L", "Total P&L"]:
            log_df[c] = log_df[c].apply(lambda x: f"Rs {float(x):+,.0f}")
        st.dataframe(log_df, use_container_width=True, hide_index=True)
        csv_data = log_df.to_csv(index=False).encode("utf-8")
        st.download_button("Download CSV", csv_data, "pairt_trades.csv", "text/csv")

        # Per-pair breakdown
        try:
            pair_pnl = tdf.groupby("pair")["total_pnl"].agg(["sum", "count"])
            pair_pnl.columns = ["P&L", "Trades"]
            pair_pnl = pair_pnl.sort_values("P&L", ascending=False)
            st.subheader("Per-Pair P&L")
            st.dataframe(pair_pnl.style.format({"P&L": "Rs {:,.0f}"}), use_container_width=True)
        except Exception as e:
            st.error(f"Pair breakdown error: {e}")

        # Monthly
        monthly_total = pd.Series(dtype=float)
        try:
            tdf["month"] = tdf["entry_date"].apply(lambda x: str(x)[:7])
            monthly_total = tdf.groupby("month")["total_pnl"].sum()
            st.subheader("Monthly P&L")
            st.dataframe(monthly_total.to_frame("P&L").style.format({"P&L": "Rs {:+,.0f}"}), use_container_width=True)
        except Exception as e:
            st.error(f"Monthly P&L error: {e}")

        # Concurrent
        try:
            conc_vals = list(daily_max.values())
            max_conc = 0
            for v in conc_vals:
                if v > max_conc:
                    max_conc = v
            avg_conc = sum(conc_vals) / len(conc_vals) if conc_vals else 0.0
        except Exception:
            max_conc = 0
            avg_conc = 0.0
        st.caption(f"Max concurrent: {max_conc}, Avg: {avg_conc:.1f}")

        # Monthly chart
        try:
            fig = go.Figure()
            m_idx = [str(x) for x in monthly_total.index]
            m_vals = [float(monthly_total.iloc[i]) for i in range(len(monthly_total))]
            colors = ["#00e676" if v >= 0 else "#ff5252" for v in m_vals]
            fig.add_trace(go.Bar(x=m_idx, y=m_vals, marker_color=colors))
            fig.update_layout(title="Monthly P&L", height=350, template="plotly_dark",
                              xaxis_title="Month", yaxis_title="P&L (Rs)")
            st.plotly_chart(fig, use_container_width=True)
        except Exception as e:
            st.error(f"Chart error: {e}")
