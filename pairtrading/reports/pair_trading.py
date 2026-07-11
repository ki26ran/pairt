import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime, timedelta
import os, sys, json
from pairtrading.configs.settings import DATA_DIR
from pairtrading.configs.symbols import get_nifty200
from pairtrading.live.cache import get_pair_cache

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(BASE_DIR)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
from common.market_data.cache import get_cache

PAIRS_FILE = os.path.join(DATA_DIR, "pairs.csv")
BT_DIR = os.path.join(DATA_DIR, "bt_results")
THRESHOLDS_FILE = os.path.join(os.path.dirname(DATA_DIR), "configs", "pair_thresholds.json")


def _bt_path(key):
    return os.path.join(BT_DIR, f"{key}.json")


def _save_bt(key, data):
    os.makedirs(BT_DIR, exist_ok=True)
    ser = dict(data)
    ser["results"] = [_trade_to_serializable(t) for t in data.get("results", [])]
    ser["train_trades"] = [_trade_to_serializable(t) for t in data.get("train_trades", [])]
    ser["test_trades"] = [_trade_to_serializable(t) for t in data.get("test_trades", [])]
    with open(_bt_path(key), "w") as f:
        json.dump(ser, f, indent=2)


def _trade_to_serializable(t):
    return {k: str(v) if isinstance(v, pd.Timestamp) else v for k, v in t.items()}


def _load_all_bt():
    if not os.path.isdir(BT_DIR):
        return {}
    out = {}
    for fn in os.listdir(BT_DIR):
        if not fn.endswith(".json"):
            continue
        key = fn[:-5]
        try:
            with open(os.path.join(BT_DIR, fn)) as f:
                out[key] = json.load(f)
        except Exception:
            pass
    return out


def _color_zscore(val):
    if val > 2:
        return "color: #ff5252; font-weight: bold"
    elif val < -2:
        return "color: #00e676; font-weight: bold"
    elif val > 1 or val < -1:
        return "color: #ffab00; font-weight: bold"
    return ""


def _style_pairs(df):
    df = df.copy()
    for col in df.select_dtypes("float").columns:
        df[col] = df[col].apply(lambda x: f"{x:.2f}" if x else "-")
    return df.style


def _run_pair_backtest(p1, p2, spread, zscore, hr, l1, l2, lot1, lot2, entry_z=2.0, exit_z=0.0, o1=None, o2=None):
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
                pos = 1
                entry_idx = next_i if use_open else i
            elif prev_z >= entry_z and curr_z < entry_z:
                pos = -1
                entry_idx = next_i if use_open else i

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
                p1x = fill_exit_p1
                p2x = fill_exit_p2
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


def _compute_metrics(trades):
    if not trades:
        return None
    total = len(trades)
    wins = [t for t in trades if t["P&L"] > 0]
    losses = [t for t in trades if t["P&L"] <= 0]
    win_rate = len(wins) / total * 100
    total_pnl = sum(t["P&L"] for t in trades)
    gross_profit = sum(t["P&L"] for t in wins)
    gross_loss = abs(sum(t["P&L"] for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    cumulative = 0
    peak = 0
    max_dd = 0
    for t in trades:
        cumulative += t["P&L"]
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd
    return dict(total=total, win_rate=win_rate, total_pnl=total_pnl,
                profit_factor=profit_factor, max_dd=max_dd)


def _add_trade_markers(fig_z, fig_norm, trades, zscore):
    if not trades:
        return
    le_x, le_y = [], []
    se_x, se_y = [], []
    ex_x, ex_y, ex_c = [], [], []

    for t in trades:
        try:
            ez = zscore.loc[t["Entry"]]
            xz = zscore.loc[t["Exit"]]
        except Exception:
            continue
        if t["Direction"] == "LONG":
            le_x.append(t["Entry"]); le_y.append(ez)
        else:
            se_x.append(t["Entry"]); se_y.append(ez)
        ex_x.append(t["Exit"]); ex_y.append(xz)
        ex_c.append("#00e676" if t["P&L"] > 0 else "#ff5252")
        fig_norm.add_vline(x=t["Entry"], line=dict(color="#00e676" if t["Direction"]=="LONG" else "#ff5252", width=1, dash="dash"))
        fig_norm.add_vline(x=t["Exit"], line=dict(color=ex_c[-1], width=1, dash="dot"))

    if le_x:
        fig_z.add_trace(go.Scatter(x=le_x, y=le_y, mode="markers",
            marker=dict(size=10, symbol="triangle-up", color="#00e676", line=dict(width=1, color="white")),
            name="Entry LONG"))
    if se_x:
        fig_z.add_trace(go.Scatter(x=se_x, y=se_y, mode="markers",
            marker=dict(size=10, symbol="triangle-down", color="#ff5252", line=dict(width=1, color="white")),
            name="Entry SHORT"))
    if ex_x:
        fig_z.add_trace(go.Scatter(x=ex_x, y=ex_y, mode="markers",
            marker=dict(size=8, symbol="circle", color=ex_c, line=dict(width=1, color="white")),
            name="Exit"))


def _compute_sharpe(trades):
    rets = [t["P&L_%"] for t in trades]
    avg_r = np.mean(rets)
    std_r = np.std(rets)
    if std_r > 1e-8:
        return avg_r / std_r * np.sqrt(len(rets))
    return avg_r * 10


def show():
    try:
        _show_impl()
    except Exception as e:
        import traceback
        st.error(f"**{type(e).__name__}:** {e}")
        st.code(traceback.format_exc(), language="python")
        st.stop()


def _show_impl():
    st.title("🧪 Backtest & Optimize")
    st.markdown("Test entry/exit thresholds via walk-forward grid search. Save the best settings for live trading.")
    st.markdown(
        "<div style='background:#2a1a3a;border-radius:6px;padding:8px 14px;font-size:13px;color:#ccc'>"
        "<b>Workflow:</b> "
        "Step 1: Discover → "
        "<span style='color:#1f77b4;font-weight:bold'>Step 2: Backtest</span> → "
        "Step 3: Monitor"
        "</div>",
        unsafe_allow_html=True,
    )

    PAIRS_FILE = os.path.join(DATA_DIR, "pairs.csv")
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

    # Load pairs from DuckDB (primary), fall back to thresholds, then CSV
    pair_cache = get_pair_cache()
    pairs_list = pair_cache.load_discovered_pairs()
    if pairs_list:
        df_pairs = pd.DataFrame(pairs_list)
    elif th_data:
        # Build pairs list from existing thresholds
        rows = []
        for pk in th_data:
            s1, s2 = pk.split("|")
            rows.append({"Stock1": s1, "Stock2": s2, "Sector": "", "Pair_Key": pk})
        df_pairs = pd.DataFrame(rows)
    elif os.path.exists(PAIRS_FILE):
        try:
            df_pairs = pd.read_csv(PAIRS_FILE)
        except Exception as e:
            st.error(f"Failed to read pairs file: {e}")
            return
    else:
        st.warning("No pairs found. Run Discover Pairs first.")
        return

    required_pair_cols = {"Stock1", "Stock2"}
    if not required_pair_cols.issubset(df_pairs.columns):
        st.warning("pairs.csv missing required columns (Stock1, Stock2)")
        return

    df_pairs = df_pairs.copy()
    df_pairs["Pair_Key"] = df_pairs["Stock1"] + "|" + df_pairs["Stock2"]
    df_pairs["Entry Z"] = df_pairs["Pair_Key"].apply(lambda k: f"±{th_data[k]['entry_z']}" if k in th_data else "-")
    df_pairs["Exit Z"] = df_pairs["Pair_Key"].apply(lambda k: th_data[k].get('exit_z', '-') if k in th_data else "-")
    if "Pair_Key" in df_pairs.columns and "Entry Z" in df_pairs.columns and "Exit Z" in df_pairs.columns:
        df_valid = df_pairs[df_pairs["Pair_Key"].isin(th_data)].drop(columns=["Pair_Key"], errors="ignore")
    else:
        df_valid = df_pairs

    discovered_keys = set(df_pairs["Pair_Key"].tolist()) if "Pair_Key" in df_pairs.columns else set()
    extra_keys = [k for k in th_data if k not in discovered_keys]

    st.header(f"Configured for Trading: {len(th_data)} total")
    st.caption(f"{len(df_valid)} from discovery  ·  {len(extra_keys)} added manually (CLI)  ·  {max(0, len(df_pairs) - len(df_valid))} discovered but not yet configured")
    try:
        st.dataframe(_style_pairs(df_valid), use_container_width=True, height=200)
    except Exception:
        st.dataframe(df_valid, use_container_width=True, height=200)

    discovered_keys = set(df_pairs["Pair_Key"].tolist()) if "Pair_Key" in df_pairs.columns else set()
    drop_cols = [c for c in ["Pair_Key", "Entry Z", "Exit Z"] if c in df_pairs.columns]
    df_pairs = df_pairs.drop(columns=drop_cols, errors="ignore")
    extra_keys = [k for k in th_data if k not in discovered_keys]
    extra_rows = []
    for ek in extra_keys:
        try:
            s1n, s2n = ek.split("|")
        except Exception:
            continue
        hr_val = th_data[ek].get("hr", 1.0)
        extra_rows.append({"Stock1": s1n, "Stock2": s2n, "Hedge_Ratio": hr_val,
                           "Sector": "", "Correlation": 0.0, "Coint_PValue": 0.0, "Half_Life": 0})
    if extra_rows:
        try:
            df_extra = pd.DataFrame(extra_rows)
            df_pairs = pd.concat([df_pairs, df_extra], ignore_index=True)
        except Exception:
            pass

    st.divider()

    # ── Batch optimize all pairs ──────────────────────────────
    total_pairs = len(df_pairs)
    total_th = len(th_data)
    st.info(f"**{total_th} configured** + **{max(0, total_pairs - total_th)} unconfigured** = **{total_pairs} total pairs** available.")
    if st.button(f"⚡ Optimize All {total_pairs} Pairs", type="primary", use_container_width=True):
        # Merge discovered pairs into thresholds file with default params
        with open(THRESHOLDS_FILE) as f:
            saved_th = json.load(f)
        changed = 0
        for _, r in df_pairs.iterrows():
            pk = str(r.get("Stock1", "")) + "|" + str(r.get("Stock2", ""))
            if pk not in saved_th:
                saved_th[pk] = {"entry_z": 2.0, "exit_z": 1.0, "hr": float(r.get("Hedge_Ratio", 1.0))}
                changed += 1
        if changed:
            with open(THRESHOLDS_FILE, "w") as f:
                json.dump(saved_th, f, indent=2)
            st.info(f"Added {changed} new pairs to thresholds file. Optimizing all {total_pairs}...")
        from pairtrading.optimizer import run as run_optimizer
        with st.spinner(f"Optimizing {total_pairs} pairs — may take several minutes..."):
            import io, contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                run_optimizer(months=6)
            output = buf.getvalue()
        with st.expander("Optimizer Results", expanded=True):
            st.code(output[-3000:] if len(output) > 3000 else output)
        st.success(f"Optimization complete! Thresholds updated.")
        st.rerun()

    def _pair_label(r):
        s1n = str(r.get("Stock1", "")).replace(".NS", "")
        s2n = str(r.get("Stock2", "")).replace(".NS", "")
        corr = r.get("Correlation", 0)
        sec = r.get("Sector", "")
        tag = f" (r={corr:.2f})" if corr else ""
        tag += f" · {sec}" if sec else ""
        return f"{s1n} / {s2n}{tag}"

    labels = ["Select..."] + [_pair_label(r) for _, r in df_pairs.iterrows()]
    selected = st.selectbox("Select pair:", labels)
    if selected == "Select..." or not selected:
        return

    try:
        idx = labels.index(selected) - 1
        row = df_pairs.iloc[idx]
    except Exception:
        st.error("Invalid pair selection")
        return

    s1 = str(row.get("Stock1", ""))
    s2 = str(row.get("Stock2", ""))
    hr = float(row.get("Hedge_Ratio", 1.0))
    if not s1 or not s2:
        st.error("Invalid pair data")
        return
    l1, l2 = s1.replace(".NS", ""), s2.replace(".NS", "")
    _sector = str(row.get("Sector", ""))

    lot1, lot2 = 1, 1
    try:
        nifty_list = get_nifty200()
        _nifty_lookup = {e["Symbol"]: e for e in nifty_list} if nifty_list else {}
        lot1 = _nifty_lookup.get(s1, {}).get("LotSize", 1)
        lot2 = _nifty_lookup.get(s2, {}).get("LotSize", 1)
    except Exception:
        pass

    st.subheader(f"{l1} vs {l2}  {'· ' + _sector if _sector else ''}")

    cache = None
    p1 = p2 = spread = zscore = None
    try:
        with st.spinner("Fetching daily data..."):
            end = datetime.now()
            start = end - timedelta(days=730)
            cache = get_cache()
            cache.ensure_fresh([s1, s2], "daily")
            df = cache.get_bulk_multiindex([s1, s2], start.strftime("%Y-%m-%d"),
                                            end.strftime("%Y-%m-%d"), interval="1d")
            if df.empty or not isinstance(df.columns, pd.MultiIndex):
                st.warning("No daily price data available for this pair")
            else:
                p1 = df['Close'][s1].copy()
                p2 = df['Close'][s2].copy()
                combined = pd.concat([p1, p2], axis=1).dropna()
                if not combined.empty:
                    p1, p2 = combined.iloc[:, 0], combined.iloc[:, 1]
                    spread = p1 - hr * p2
                    spread_mean = spread.rolling(21).mean()
                    spread_std = spread.rolling(21).std()
                    zscore = ((spread - spread_mean) / spread_std).dropna()
    except Exception as e:
        st.warning(f"Could not load daily data: {e}")

    c1, c2, c3, c4 = st.columns(4)
    c1.markdown(
        f"<div style='background:#1a3a5c;border-radius:8px;padding:10px;text-align:center'>"
        f"<span style='font-size:12px;color:#aaa'>Hedge Ratio</span><br>"
        f"<span style='font-size:20px;font-weight:bold;color:white'>{hr:.2f}</span></div>",
        unsafe_allow_html=True,
    )
    corr_val = row.get("Correlation", 0)
    c2.markdown(
        f"<div style='background:#1a3a5c;border-radius:8px;padding:10px;text-align:center'>"
        f"<span style='font-size:12px;color:#aaa'>Correlation</span><br>"
        f"<span style='font-size:20px;font-weight:bold;color:white'>{corr_val:.2f}</span></div>"
        if corr_val else
        f"<div style='background:#1a3a5c;border-radius:8px;padding:10px;text-align:center'>"
        f"<span style='font-size:12px;color:#aaa'>Correlation</span><br>"
        f"<span style='font-size:20px;font-weight:bold;color:#666'>—</span></div>",
        unsafe_allow_html=True,
    )
    pval = row.get("Coint_PValue", 1)
    c3.markdown(
        f"<div style='background:#1a3a5c;border-radius:8px;padding:10px;text-align:center'>"
        f"<span style='font-size:12px;color:#aaa'>Coint P</span><br>"
        f"<span style='font-size:20px;font-weight:bold;color:#ffab00'>{pval:.2f}</span></div>"
        if pval and pval < 1 else
        f"<div style='background:#1a3a5c;border-radius:8px;padding:10px;text-align:center'>"
        f"<span style='font-size:12px;color:#aaa'>Coint P</span><br>"
        f"<span style='font-size:20px;font-weight:bold;color:#666'>—</span></div>",
        unsafe_allow_html=True,
    )
    hl = row.get("Half_Life")
    c4.markdown(
        f"<div style='background:#1a3a5c;border-radius:8px;padding:10px;text-align:center'>"
        f"<span style='font-size:12px;color:#aaa'>Half Life</span><br>"
        f"<span style='font-size:20px;font-weight:bold;color:white'>{hl:.2f}d</span></div>"
        if hl is not None and pd.notna(hl) else "N/A",
        unsafe_allow_html=True,
    )
    c5, c6 = st.columns(2)
    saved_cfg = th_data.get(f"{s1}|{s2}", {})
    sl_z = saved_cfg.get("entry_z", 2.0) * 3.0
    c5.markdown(
        f"<div style='background:#3a1a1a;border-radius:8px;padding:10px;text-align:center'>"
        f"<span style='font-size:12px;color:#aaa'>Stop-Loss</span><br>"
        f"<span style='font-size:18px;font-weight:bold;color:#ff5252'>±{sl_z:.1f}σ</span></div>",
        unsafe_allow_html=True,
    )
    c6.markdown(
        f"<div style='background:#1a1a3a;border-radius:8px;padding:10px;text-align:center'>"
        f"<span style='font-size:12px;color:#aaa'>Max Hold</span><br>"
        f"<span style='font-size:18px;font-weight:bold;color:#ffab00'>20 days</span></div>",
        unsafe_allow_html=True,
    )

    st.divider()

    if zscore is not None and len(zscore) > 10:
        norm1 = p1 / p1.iloc[0] * 100
        norm2 = p2 / p2.iloc[0] * 100
        fig_norm = go.Figure()
        fig_norm.add_trace(go.Scatter(x=norm1.index, y=norm1, name=l1, line=dict(color='#0066cc')))
        fig_norm.add_trace(go.Scatter(x=norm2.index, y=norm2, name=l2, line=dict(color='#ff9800')))
        fig_norm.update_layout(title="Normalized Prices", height=350, template="plotly_dark")

        fig_z = go.Figure()
        latest_z = zscore.iloc[-1]
        fig_z.add_trace(go.Scatter(x=zscore.index, y=zscore, name="Z-Score", line=dict(color='#6f42c1')))
        fig_z.add_hline(y=0, line=dict(color="white", width=1, dash="dash"))
        fig_z.add_hline(y=1, line=dict(color="orange", width=1, dash="dot"))
        fig_z.add_hline(y=-1, line=dict(color="orange", width=1, dash="dot"))
        fig_z.add_hline(y=2, line=dict(color="red", width=1, dash="dot"))
        fig_z.add_hline(y=-2, line=dict(color="red", width=1, dash="dot"))
        fig_z.update_layout(title=f"Z-Score (current: {latest_z:.2f})", height=350, template="plotly_dark")

        if "_bt_all" not in st.session_state:
            st.session_state._bt_all = _load_all_bt()
        bt_key = f"{s1}_{s2}"
        bt_data = st.session_state._bt_all.get(bt_key)
        bt_results = bt_data.get("results") if bt_data else None
        if bt_results is not None:
            try:
                _add_trade_markers(fig_z, fig_norm, bt_results, zscore)
            except Exception:
                pass

        st.plotly_chart(fig_norm, use_container_width=True)
        st.plotly_chart(fig_z, use_container_width=True)

        if latest_z > 2:
            signal = f"🔴 SHORT SPREAD: Sell {l1}, Buy {l2}"
            sig_color = "#ff5252"
        elif latest_z < -2:
            signal = f"🟢 LONG SPREAD: Buy {l1}, Sell {l2}"
            sig_color = "#00e676"
        elif latest_z > 1:
            signal = f"🟡 WATCH: {l1} rich vs {l2}"
            sig_color = "#ffab00"
        elif latest_z < -1:
            signal = f"🟡 WATCH: {l2} rich vs {l1}"
            sig_color = "#ffab00"
        else:
            signal = "⚪ NEUTRAL"
            sig_color = "#aaa"

        st.markdown(
            f"<div style='background:{sig_color}22;border:2px solid {sig_color};"
            f"border-radius:10px;padding:15px;text-align:center'>"
            f"<span style='font-size:18px;font-weight:bold;color:{sig_color}'>{signal}</span></div>",
            unsafe_allow_html=True,
        )
    else:
        st.info("📉 No z-score data available — backtest will use historical prices once data loads")

    st.divider()
    st.subheader("📊 Walk-Forward Backtest")
    st.caption("Grid searches entry_z (0.5–3.0) × exit_z (0.25–2.5) across walk-forward windows. Stop-loss at 3× entry_z. Max hold 20 trading days.")

    with st.expander("Parameters", expanded=True):
        cc1, cc2 = st.columns(2)
        wf_months = cc1.number_input("Lookback months", min_value=3, max_value=24, value=9, step=1)
        tf = cc2.selectbox("Timeframe", ["Daily", "Hourly"],
                           help="Hourly: ~60 days max (Yahoo 1h limit). Daily: 2 years.")

    if st.button("🎯 Run Walk-Forward Optimization", key="bt_wf"):
        try:
            if cache is None:
                cache = get_cache()
            status = None
            if tf == "Hourly":
                status = st.status("Loading hourly data...", expanded=True)
                end_dt = datetime.now()
                start_dt = end_dt - timedelta(days=min(wf_months * 30 + 30, 60))
                status.update(label=f"Hourly data range: {start_dt.strftime('%Y-%m-%d')} to {end_dt.strftime('%Y-%m-%d')}")
                s1c = s1.replace('.NS', '').replace('.BO', '')
                s2c = s2.replace('.NS', '').replace('.BO', '')
                status.update(label=f"Querying hourly data for {s1c}/{s2c}...")
                try:
                    con = cache._db_read()
                    placeholders = ", ".join(["?"] * 2)
                    query = f"""
                        SELECT ticker, datetime_ist, open, high, low, close, volume
                        FROM hourly_bars
                        WHERE ticker IN ({placeholders})
                        AND datetime_ist >= ? AND datetime_ist < ?
                        ORDER BY datetime_ist
                    """
                    dfh = con.execute(query, [s1c, s2c, start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")]).df()
                except Exception as e:
                    status.update(label=f"DB query failed: {e}", state="error")
                    st.error(f"Hourly DB query failed: {e}")
                    st.stop()
                if dfh.empty:
                    status.update(label="No hourly data found", state="error")
                    st.warning("No hourly data in database. Run the hourly scanner first.")
                    st.stop()
                p1h = dfh[dfh["ticker"] == s1c].set_index("datetime_ist")["close"]
                p2h = dfh[dfh["ticker"] == s2c].set_index("datetime_ist")["close"]
                p1h.index = pd.to_datetime(p1h.index, errors="coerce")
                p2h.index = pd.to_datetime(p2h.index, errors="coerce")
                p1h = p1h.dropna()
                p2h = p2h.dropna()
                if p1h.empty or p2h.empty:
                    status.update(label="No valid prices", state="error")
                    st.error("No valid hourly prices after parsing")
                    st.stop()
                ch = pd.concat([p1h, p2h], axis=1).dropna()
                if ch.empty or len(ch) < 20:
                    status.update(label=f"Only {len(ch)} candles, need 20", state="error")
                    st.error(f"Not enough hourly data ({len(ch)} candles)")
                    st.stop()
                # Index is already IST (from DuckDB hourly_bars). Filter market hours directly.
                ts_local = pd.to_datetime(ch.index)
                mkt = (ts_local.hour * 60 + ts_local.minute >= 555) & (ts_local.hour * 60 + ts_local.minute <= 930)
                ch = ch[mkt]
                ch.index = ts_local[mkt]
                if ch.empty or len(ch) < 20:
                    status.update(label="No market-hours data", state="error")
                    st.error("No market-hours hourly data available")
                    st.stop()
                bp1, bp2 = ch.iloc[:, 0], ch.iloc[:, 1]
                bspread = bp1 - hr * bp2
                bmean = bspread.rolling(63).mean()
                bstd = bspread.rolling(63).std()
                bz = ((bspread - bmean) / bstd).dropna()
                if len(bz) < 10:
                    status.update(label=f"Only {len(bz)} z-scores, need 10", state="error")
                    st.error(f"Too few z-score values ({len(bz)}) for hourly backtest")
                    st.stop()
                bt_p1, bt_p2, bt_z = bp1, bp2, bz
                status.update(label=f"Loaded {len(bz)} hourly candles. Running grid search...")
            else:
                if zscore is None or len(zscore) < 10:
                    st.error("Not enough daily z-score data to run backtest")
                    st.stop()
                bt_p1, bt_p2, bt_z = p1, p2, zscore
                bspread = spread
                start_dt, end_dt = start, end

            entry_vals = [round(x, 2) for x in np.arange(0.5, 3.25, 0.25)]
            exit_vals = [round(x, 2) for x in np.arange(0.25, 2.75, 0.25)]
            best_score = -999
            best_entry = None
            best_exit = None
            best_trades = []
            best_metrics = {}
            total_combos = len(entry_vals) * len(exit_vals)
            n_windows = 3
            total_len = len(bt_z)

            progress = st.progress(0, text="Grid searching...")
            for ei, entry_z_v in enumerate(entry_vals):
                for xi, exit_z_v in enumerate(exit_vals):
                    if exit_z_v >= entry_z_v:
                        continue
                    wf_scores = []
                    all_trades = []
                    wf_size = total_len // (n_windows + 1)
                    wf_windows = [(0, total_len)] if wf_size < 500 else [(0, wf_size * (w + 1)) for w in range(n_windows)]
                    for tr_end in wf_windows:
                        zs_tr = bt_z.iloc[:tr_end[1]]
                        p1_tr = bt_p1.loc[zs_tr.index]
                        p2_tr = bt_p2.loc[zs_tr.index]
                        try:
                            trades = _run_pair_backtest(p1_tr, p2_tr, bspread if tf == "Daily" else spread,
                                                         zs_tr, hr, l1, l2, lot1, lot2, entry_z_v, exit_z_v)
                            m = _compute_metrics(trades)
                            wf_scores.append(m["total_pnl"] if m and m["total"] >= 2 else -999)
                            if tr_end == wf_windows[-1]:
                                all_trades = trades
                        except Exception:
                            wf_scores.append(-999)
                    avg_pnl = float(np.mean(wf_scores)) if wf_scores else -999
                    pct = (ei * len(exit_vals) + xi) / total_combos
                    progress.progress(min(pct, 1.0), text=f"entry={entry_z_v:.2f} exit={exit_z_v:.2f}")
                    if avg_pnl > best_score:
                        best_score = avg_pnl
                        best_entry = entry_z_v
                        best_exit = exit_z_v
                        best_trades = all_trades
                        best_metrics = _compute_metrics(all_trades) or {}
            progress.empty()
            if status:
                status.update(label="Optimization complete", state="complete")

            if best_entry is not None:
                start_date = start_dt.strftime('%Y-%m-%d')
                end_date = end_dt.strftime('%Y-%m-%d')
                st.session_state._bt_all[bt_key] = dict(
                    results=[_trade_to_serializable(t) for t in best_trades],
                    entry_z=best_entry, exit_z=best_exit,
                    train_metrics=best_metrics, test_metrics={},
                    train_trades=[_trade_to_serializable(t) for t in best_trades],
                    test_trades=[],
                    config=dict(months=wf_months, timeframe=tf, n_windows=n_windows,
                                start_date=start_date, end_date=end_date))
                _save_bt(bt_key, st.session_state._bt_all[bt_key])
                st.rerun()
        except Exception as e:
            st.error(f"Walk-forward optimization failed: {e}")

    bt_data = st.session_state._bt_all.get(bt_key) if zscore is not None else None
    if bt_data is not None:
        entry_z = bt_data.get("entry_z", 2.0)
        exit_z = bt_data.get("exit_z", 0.5)
        train_metrics = bt_data.get("train_metrics", {})
        test_metrics = bt_data.get("test_metrics", {})
        train_trades = bt_data.get("train_trades", [])
        test_trades = bt_data.get("test_trades", [])
        ep_scores = bt_data.get("ep_scores", [])

        st.subheader("📈 Walk-Forward Results")
        cfg = bt_data.get("config", {})
        sd = cfg.get("start_date", "?")
        ed = cfg.get("end_date", "?")
        st.caption(f"Period: **{sd} → {ed}** ({cfg.get('timeframe','?')}, {cfg.get('n_windows',3)} windows)  ·  "
                   f"Optimal thresholds: entry **±{entry_z:.2f}**, exit **{exit_z:.2f}**  ·  "
                   f"Stop-loss **±{entry_z*3:.1f}σ**  ·  Max hold **20 days**")

        pair_key = f"{s1}|{s2}"
        if st.button("💾 Save Thresholds for Live Trading", key="save_th"):
            try:
                # Save to DuckDB
                db_th = get_pair_cache().load_thresholds()
                db_th[pair_key] = {"entry_z": round(entry_z, 2), "exit_z": round(exit_z, 2), "hr": round(hr, 4)}
                get_pair_cache().save_thresholds(db_th)
                # Also save to JSON file for fallback
                if os.path.exists(THRESHOLDS_FILE):
                    with open(THRESHOLDS_FILE) as f:
                        data = json.load(f)
                else:
                    data = {}
                data[pair_key] = {"entry_z": round(entry_z, 2), "exit_z": round(exit_z, 2), "hr": round(hr, 4)}
                with open(THRESHOLDS_FILE, "w") as f:
                    json.dump(data, f, indent=2)
                st.toast(f"Saved: entry ±{entry_z:.2f}, exit {exit_z:.2f} for {l1} / {l2}", icon="✅")
            except Exception as e:
                st.error(f"Failed to save thresholds: {e}")

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Trades", train_metrics.get("total", 0))
        c2.metric("Win Rate", f"{train_metrics.get('win_rate', 0):.0f}%")
        c3.metric("Total P&L", f"{train_metrics.get('total_pnl', 0):+.2f}")
        pf = train_metrics.get("profit_factor", 0)
        c4.metric("Profit Factor", f"{pf:.2f}" if pf != float("inf") else "∞")
        c5.metric("Max DD", f"{train_metrics.get('max_dd', 0):.2f}")
        if train_trades:
            try:
                df_tt = pd.DataFrame(train_trades)
                df_tt["Result"] = df_tt["P&L"].apply(lambda x: "✅" if x > 0 else "❌")
                cols = ["Entry", "Exit", "Direction", "Lot1", "s1_entry", "s1_exit", "s1_P&L", "Lot2", "s2_entry", "s2_exit", "s2_P&L", "P&L", "P&L_%", "Result"]
                cols = [c for c in cols if c in df_tt.columns]
                st.caption(f"**s1 = {l1}**, **s2 = {l2}**")
                st.dataframe(df_tt[cols], use_container_width=True, hide_index=True)
            except Exception:
                st.dataframe(pd.DataFrame(train_trades), use_container_width=True, hide_index=True)
    elif zscore is not None and len(zscore) > 10:
        st.info("🆕 New pair — click **🎯 Run Walk-Forward Optimization** above to backtest")

    st.divider()
    st.subheader("📊 15-Minute Intraday (Last 7 Days)")

    try:
        with st.spinner("Fetching 15m data..."):
            end_dt = datetime.now()
            start_dt = end_dt - timedelta(days=30)
            if cache is None:
                cache = get_cache()
            cache.ensure_fresh([s1, s2], "15m")
            df15 = cache.get_bulk_multiindex([s1, s2], start_dt.strftime("%Y-%m-%d"),
                                              end_dt.strftime("%Y-%m-%d"), interval="15m")
            if not df15.empty and isinstance(df15.columns, pd.MultiIndex) and 'Close' in df15.columns:
                p1_15 = df15['Close'][s1].copy()
                p2_15 = df15['Close'][s2].copy()
                combined15 = pd.concat([p1_15, p2_15], axis=1).dropna()
                if not combined15.empty and len(combined15) > 5:
                    p1_15, p2_15 = combined15.iloc[:, 0], combined15.iloc[:, 1]
                    ts = pd.to_datetime(combined15.index, utc=True)
                    ts_ist = ts.tz_convert("Asia/Kolkata")
                    mkt_mask = (ts_ist.hour * 60 + ts_ist.minute >= 9 * 60 + 15) & (ts_ist.hour * 60 + ts_ist.minute <= 15 * 60 + 30)
                    combined15 = combined15[mkt_mask]
                    combined15.index = ts_ist[mkt_mask].tz_localize(None)
                    if not combined15.empty and len(combined15) > 5:
                        p1_15, p2_15 = combined15.iloc[:, 0], combined15.iloc[:, 1]
                        spread15 = p1_15 - hr * p2_15
                        spread_mean15 = spread15.rolling(20).mean()
                        spread_std15 = spread15.rolling(20).std()
                        zscore15 = ((spread15 - spread_mean15) / spread_std15).dropna()
                        if len(zscore15) > 5:
                            norm1_15 = p1_15 / p1_15.iloc[0] * 100
                            norm2_15 = p2_15 / p2_15.iloc[0] * 100
                            fig_norm15 = go.Figure()
                            fig_norm15.add_trace(go.Scatter(x=norm1_15.index, y=norm1_15, name=l1, line=dict(color='#0066cc')))
                            fig_norm15.add_trace(go.Scatter(x=norm2_15.index, y=norm2_15, name=l2, line=dict(color='#ff9800')))
                            fig_norm15.update_xaxes(rangebreaks=[
                                dict(bounds=[15.5, 9.25], pattern="hour"),
                                dict(bounds=["sat", "mon"], pattern="day of week"),
                            ])
                            fig_norm15.update_layout(title="Normalized Prices (15m)", height=350, template="plotly_dark", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
                            fig_z15 = go.Figure()
                            latest_z15 = zscore15.iloc[-1]
                            fig_z15.add_trace(go.Scatter(x=zscore15.index, y=zscore15, name="Z-Score (15m)", line=dict(color='#6f42c1')))
                            fig_z15.update_xaxes(rangebreaks=[
                                dict(bounds=[15.5, 9.25], pattern="hour"),
                                dict(bounds=["sat", "mon"], pattern="day of week"),
                            ])
                            fig_z15.add_hline(y=0, line=dict(color="white", width=1, dash="dot"))
                            fig_z15.add_hline(y=1, line=dict(color="orange", width=1, dash="dot"))
                            fig_z15.add_hline(y=-1, line=dict(color="orange", width=1, dash="dot"))
                            fig_z15.add_hline(y=2, line=dict(color="red", width=1, dash="dot"))
                            fig_z15.add_hline(y=-2, line=dict(color="red", width=1, dash="dot"))
                            fig_z15.update_layout(title=f"Z-Score (15m, current: {latest_z15:.2f})", height=350, template="plotly_dark")
                            st.plotly_chart(fig_norm15, use_container_width=True)
                            st.plotly_chart(fig_z15, use_container_width=True)
                        else:
                            st.info("Insufficient 15m z-score data")
                    else:
                        st.info("No market-hours 15m data")
                else:
                    st.info("Insufficient 15m price data")
            else:
                st.info("15m data not available (may need market hours)")
    except Exception as e:
        st.info(f"15m data unavailable: {e}")
