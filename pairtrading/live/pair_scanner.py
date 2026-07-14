import streamlit as st
import pandas as pd
import numpy as np
import os, sys, math
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(BASE_DIR)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
from pairtrading.live.cache import get_pair_cache
from common.market_data.cache import get_cache
from pairtrading.configs.symbols import get_nifty200
THRESHOLDS_FILE = os.path.join(BASE_DIR, "configs", "pair_thresholds.json")


def _load_positions():
    """Read open positions from PairTrading DuckDB."""
    try:
        return get_pair_cache().load_positions()
    except Exception:
        return {}

COL = ["Time", "s1", "s2", "s1_price", "s2_price", "Z-score", "Entry Z", "Exit Z", "Signal"]


_LOT_CACHE = None


def _lot_size(sym):
    global _LOT_CACHE
    if _LOT_CACHE is None:
        try:
            _LOT_CACHE = {e["Symbol"]: e["LotSize"] for e in get_nifty200()}
        except Exception:
            _LOT_CACHE = {}
    return _LOT_CACHE.get(sym + ".NS", 1)


@st.cache_data(ttl=5)
def _fetch_live_prices(tickers):
    today_str = datetime.now().strftime('%Y-%m-%d')
    tomorrow_str = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
    cache = get_cache()
    df = cache.get_bulk_multiindex(tickers, today_str, tomorrow_str, interval='1m')
    if df.empty or not isinstance(df.columns, pd.MultiIndex):
        return {}
    closes = df["Close"].iloc[-1]
    return {t: float(closes[t + ".NS"]) for t in tickers 
            if (t + ".NS") in closes.index and not pd.isna(closes[t + ".NS"])}


def _pnl(p, live_prices):
    import math
    lot1 = _lot_size(p["s1"].replace(".NS", ""))
    lot2 = _lot_size(p["s2"].replace(".NS", ""))
    ep1, ep2 = float(p["entry_p1"]), float(p["entry_p2"])
    s1 = p["s1"].replace(".NS", "")
    s2 = p["s2"].replace(".NS", "")
    cp1 = live_prices.get(s1, p.get("last_p1", ep1))
    cp2 = live_prices.get(s2, p.get("last_p2", ep2))
    if isinstance(cp1, float) and math.isnan(cp1): cp1 = ep1
    if isinstance(cp2, float) and math.isnan(cp2): cp2 = ep2
    if p["direction"] == "SHORT":
        return round((ep1 - cp1) * lot1 + (cp2 - ep2) * lot2, 2)
    return round((cp1 - ep1) * lot1 + (ep2 - cp2) * lot2, 2)


def _icon(row):
    if isinstance(row, str):
        s = row.upper()
        if "ENTRY" in s:
            return "🔴" if "SHORT" in s else "🟢"
        if "EXIT" in s:
            return "🟢"
        return "⚫"
    d = row.get("direction", "")
    if d == "LONG":
        return "🟢"
    if d == "SHORT":
        return "🔴"
    return "⚫"


def show():
    st.subheader("📊 Live Monitor")
    st.markdown("Track open positions, P&L, and signals. The scanner runs automatically every hour — no manual action needed.")
    st.markdown(
        "<div style='background:#1a3a2a;border-radius:6px;padding:8px 14px;font-size:13px;color:#ccc'>"
        "<b>Workflow:</b> "
        "Step 1: Discover → "
        "Step 2: Backtest → "
        "<span style='color:#1f77b4;font-weight:bold'>Step 3: Monitor</span>"
        "</div>",
        unsafe_allow_html=True,
    )

    if not os.path.exists(THRESHOLDS_FILE) and not get_pair_cache().load_thresholds():
        st.info("No saved thresholds found.")
        return

    # Load data from PairTrading DuckDB
    pair_cache = get_pair_cache()
    rdata = pair_cache.load_scanner_results()
    pairs = rdata.get("pairs", [])
    active = rdata.get("active_signals", [])
    last_upd = rdata.get("last_updated", "never")

    # Positions come from PairTrading DuckDB
    open_pos = _load_positions()

    if not pairs and not open_pos:
        st.warning("No scan data yet.")
        _show_log()
        return

    st.write(f"**Last scan:** {last_upd}  ·  **Pairs monitored:** {len(pairs)}  ·  **Open positions:** {len(open_pos)}")

    def _color_pnl(val):
        if val > 0:
            return "color: #00e676; font-weight: bold"
        elif val < 0:
            return "color: #ff5252; font-weight: bold"
        return ""

    # -- Open Positions --
    st.subheader(f"📂 Open Positions ({len(open_pos)})")
    if open_pos:
        tickers = list(set([p["s1"].replace(".NS", "") for p in open_pos.values()] +
                           [p["s2"].replace(".NS", "") for p in open_pos.values()]))
        live_prices = _fetch_live_prices(tickers) if tickers else {}
        rows = []
        total_pnl = 0.0
        for k, p in open_pos.items():
            d = "🟢 LONG" if p["direction"] == "LONG" else "🔴 SHORT"
            try:
                pnl = _pnl(p, live_prices)
                if isinstance(pnl, float) and math.isnan(pnl): pnl = 0.0
            except Exception:
                pnl = 0.0
            total_pnl += float(pnl) if isinstance(pnl, (int, float)) else 0
            s1 = p["s1"].replace(".NS", "")
            s2 = p["s2"].replace(".NS", "")
            is_short = p["direction"] == "SHORT"
            entry_z = float(p.get("entry_z_threshold", 2.0))
            sl_z = round(entry_z * 3, 1)
            entry_date_str = p.get("entry_date", "")
            days_held = 0
            if entry_date_str:
                try:
                    days_held = (datetime.now() - pd.Timestamp(entry_date_str)).days
                except Exception:
                    pass
            remaining_days = max(0, 20 - days_held)
            rows.append({
                "Dir": d,
                "s1": f"{s1} 🔴 Short" if is_short else f"{s1} 🟢 Long",
                "s2": f"{s2} 🟢 Long" if is_short else f"{s2} 🔴 Short",
                "Lot1": _lot_size(s1),
                "Lot2": _lot_size(s2),
                "Entry": p.get("entry_date", ""),
                "P1 LTP": live_prices.get(s1, p.get("last_p1", "-")),
                "P2 LTP": live_prices.get(s2, p.get("last_p2", "-")),
                "Entry Z Th": f"±{p.get('entry_z_threshold', '-')}",
                "Exit Z Th": p.get("exit_z_threshold", "-"),
                "Stop-Loss": f"±{sl_z}σ",
                "Days Left": remaining_days,
                "Current Z": p.get("last_z", ""),
                "P&L": round(float(pnl), 2) if isinstance(pnl, (int, float)) else 0,
            })
        df = pd.DataFrame(rows)
        st.metric("Unrealized P&L", f"₹{total_pnl:+,.0f}")
        styled = df.style.map(_color_pnl, subset=["P&L"])
        st.dataframe(styled, use_container_width=True, hide_index=True)
    else:
        st.info("No open positions — waiting for z-score to cross entry threshold.")

    # -- Broker Positions (actual option contracts) --
    st.divider()
    st.subheader("📊 Broker Option Positions")
    try:
        from ganah import setup_api as _sapi
        from pairtrading.live.cache import get_pair_cache as _gpc
        from pairtrading.configs.settings import BROKER_NAME, BROKER_USERNAME
        _ba = _sapi(BROKER_NAME, BROKER_USERNAME)
        _broker_pos = _ba.get_positions()
        _pair_th = _gpc().load_thresholds()
        
        if isinstance(_broker_pos, list) and len(_broker_pos) > 0:
            _opt_rows = []
            for _p in _broker_pos:
                # Show only NFO option positions with open quantity
                _inst = _p.get("instname", "")
                _net = int(_p.get("netqty", 0))
                if _inst != "OPTSTK" or _net == 0:
                    continue
                
                _tsym = _p.get("tsym", "")
                _side = "BUY" if _net > 0 else "SELL"
                _qty = abs(_net)
                _avg = float(_p.get("netavgprc", 0))
                _ltp = float(_p.get("lp", 0))
                _urmtom = float(_p.get("urmtom", 0))
                _rpnl = float(_p.get("rpnl", 0))
                _total_pnl = _urmtom + _rpnl
                
                # Identify which pair this belongs to
                _pair_tag = ""
                for _pk in (_pair_th or {}):
                    _s1 = _pk.split("|")[0].replace(".NS", "")
                    _s2 = _pk.split("|")[1].replace(".NS", "")
                    if _s1 in _tsym or _s2 in _tsym:
                        _pair_tag = f"{_s1}/{_s2}"
                        break
                
                _opt_rows.append({
                    "Symbol": _tsym[:20],
                    "Side": _side,
                    "Qty": _qty,
                    "Avg": round(_avg, 2),
                    "LTP": round(_ltp, 2),
                    "MTM P&L": round(_urmtom, 2),
                    "Realized": round(_rpnl, 2),
                    "Total P&L": round(_total_pnl, 2),
                    "Pair": _pair_tag,
                })
            
            if _opt_rows:
                _df = pd.DataFrame(_opt_rows)
                _df_styled = _df.style.map(_color_pnl, subset=["MTM P&L", "Realized", "Total P&L"])
                st.dataframe(_df_styled, use_container_width=True, hide_index=True)
                _bp_total = sum(r["Total P&L"] for r in _opt_rows)
                st.caption(f"Net broker option P&L: ₹{_bp_total:+,.0f}")
            else:
                st.info("No open option positions at broker.")
        else:
            st.info("No position data from broker.")
    except Exception as _e:
        st.caption(f"Broker positions unavailable: {_e}")

    # -- Charts for open positions --
    if open_pos:
        st.divider()
        st.subheader("📈 Pair Charts")
        pos_keys = list(open_pos.keys())
        if len(pos_keys) > 1:
            selected_key = st.selectbox("Select pair:", pos_keys,
                                        format_func=lambda k: k.replace(".NS", "").replace("|", " / "))
        else:
            selected_key = pos_keys[0]

        p = open_pos[selected_key]
        s1, s2 = p["s1"], p["s2"]
        l1, l2 = s1.replace(".NS", ""), s2.replace(".NS", "")
        hr = p.get("hr", 1)

        with st.spinner("Loading chart data..."):
            end_dt = datetime.now()
            start_dt = end_dt - timedelta(days=10)
            cache = get_cache()
            df = cache.get_bulk_multiindex([s1, s2], start_dt.strftime('%Y-%m-%d'),
                                            end_dt.strftime('%Y-%m-%d'), interval='1h')
            if not df.empty and isinstance(df.columns, pd.MultiIndex):
                raw_idx = df.index
                if isinstance(raw_idx, pd.DatetimeIndex):
                    if raw_idx.tz is not None:
                        ts_ist = raw_idx.tz_convert("Asia/Kolkata")
                    else:
                        ts_ist = raw_idx.tz_localize("UTC").tz_convert("Asia/Kolkata")
                else:
                    try:
                        ts_ist = pd.to_datetime(raw_idx, utc=True).tz_convert("Asia/Kolkata")
                    except Exception:
                        st.error("Could not parse datetime index")
                        return
                mkt_mask = (ts_ist.hour * 60 + ts_ist.minute >= 555) & (ts_ist.hour * 60 + ts_ist.minute <= 930)
                c1 = df['Close'][s1][mkt_mask].copy()
                c2 = df['Close'][s2][mkt_mask].copy()
                idx = c1.index.tz_convert("Asia/Kolkata").tz_localize(None)
                c1.index = idx; c2.index = idx

                if len(c1) > 20:
                    p1s, p2s = c1, c2
                    spread = p1s - hr * p2s
                    sm = spread.rolling(63, min_periods=1).mean()
                    ss = spread.rolling(63, min_periods=1).std()
                    zscore = ((spread - sm) / ss).dropna()

                    fig = make_subplots(rows=3, cols=1, row_heights=[0.30, 0.35, 0.35],
                                        shared_xaxes=True, vertical_spacing=0.05)

                    # Row 1: Normalized prices
                    norm1 = p1s / p1s.iloc[0] * 100
                    norm2 = p2s / p2s.iloc[0] * 100
                    fig.add_trace(go.Scatter(x=norm1.index, y=norm1, name=l1,
                                             line=dict(color='#0066cc', width=2),
                                             showlegend=True), row=1, col=1)
                    fig.add_trace(go.Scatter(x=norm2.index, y=norm2, name=l2,
                                             line=dict(color='#ff9800', width=2),
                                             showlegend=True), row=1, col=1)

                    # Row 2: Spread (centered at mean for visibility)
                    spread_centered = spread - sm
                    fig.add_trace(go.Scatter(x=spread_centered.index, y=spread_centered, name="Spread (centered)",
                                              line=dict(color='#6f42c1', width=2),
                                              fill='tozeroy', fillcolor='rgba(111,66,193,0.15)',
                                              showlegend=False), row=2, col=1)

                    # Row 3: Z-Score with entry/exit markers
                    latest_z = zscore.iloc[-1]
                    fig.add_trace(go.Scatter(x=zscore.index, y=zscore, name="Z-Score",
                                              line=dict(color='#28a745', width=2),
                                              showlegend=False), row=3, col=1)

                    # Threshold lines
                    entry_z_th = float(p.get("entry_z_threshold", 2.0))
                    exit_z_th = float(p.get("exit_z_threshold", 0.5))
                    sl_z = entry_z_th * 3.0
                    fig.add_hline(y=0, line=dict(color="white", width=1, dash="dash"), row=3, col=1)
                    fig.add_hline(y=exit_z_th, line=dict(color="orange", width=1, dash="dot"),
                                  row=3, col=1, annotation_text=f"Exit ±{exit_z_th}")
                    fig.add_hline(y=-exit_z_th, line=dict(color="orange", width=1, dash="dot"), row=3, col=1)
                    fig.add_hline(y=entry_z_th, line=dict(color="red", width=1, dash="dot"),
                                  row=3, col=1, annotation_text=f"Entry ±{entry_z_th}")
                    fig.add_hline(y=-entry_z_th, line=dict(color="red", width=1, dash="dot"), row=3, col=1)
                    fig.add_hline(y=sl_z, line=dict(color="#ff5252", width=1, dash="dash"),
                                  row=3, col=1, annotation_text=f"SL ±{sl_z:.1f}")
                    fig.add_hline(y=-sl_z, line=dict(color="#ff5252", width=1, dash="dash"), row=3, col=1)

                    # Entry marker
                    entry_z_val = float(p.get("entry_z", 0))
                    if entry_z_val != 0:
                        fig.add_hline(y=entry_z_val, line=dict(color="cyan", width=1, dash="dot"),
                                      row=3, col=1, annotation_text="Entry Z")

                    # Position entry/exit markers
                    entry_date = p.get("entry_date", "")
                    if entry_date:
                        try:
                            ed = pd.Timestamp(entry_date)
                            if ed >= zscore.index[0]:
                                ez = zscore.loc[ed:].iloc[0] if ed in zscore.index else zscore.iloc[0]
                                dir_ = p.get("direction", "LONG")
                                c_mark = "#00e676" if dir_ == "LONG" else "#ff5252"
                                sym = "triangle-up" if dir_ == "LONG" else "triangle-down"
                                fig.add_trace(go.Scatter(x=[ed], y=[ez], mode="markers",
                                    marker=dict(size=14, symbol=sym, color=c_mark, line=dict(width=2, color="white")),
                                    name=f"{dir_} Entry", showlegend=True), row=3, col=1)
                        except Exception:
                            pass

                    # Range breaks
                    for ri in range(1, 4):
                        fig.update_xaxes(rangebreaks=[
                            dict(bounds=[15.5, 9.15], pattern="hour"),
                            dict(bounds=["sat", "mon"], pattern="day of week"),
                        ], row=ri, col=1)

                    fig.update_layout(title=f"{l1} / {l2} — Last 10 Days (Hourly)",
                                      height=700, template="plotly_dark",
                                      xaxis_rangeslider_visible=False,
                                      legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
                    st.plotly_chart(fig, use_container_width=True)

                    # Info cards
                    entry_date = p.get("entry_date", "")
                    days_held = 0
                    if entry_date:
                        try:
                            days_held = (datetime.now() - pd.Timestamp(entry_date)).days
                        except Exception:
                            pass
                    remaining = max(0, 20 - days_held)
                    pnl = 0
                    try:
                        lot1 = _lot_size(p["s1"].replace(".NS", ""))
                        lot2 = _lot_size(p["s2"].replace(".NS", ""))
                        ep1, ep2 = float(p["entry_p1"]), float(p["entry_p2"])
                        if p["direction"] == "SHORT":
                            pnl = (ep1 - p1s.iloc[-1]) * lot1 + (p2s.iloc[-1] - ep2) * lot2
                        else:
                            pnl = (p1s.iloc[-1] - ep1) * lot1 + (ep2 - p2s.iloc[-1]) * lot2
                    except Exception:
                        pass

                    dz = st.columns(6)
                    dz[0].metric(f"{l1}", f"₹{p1s.iloc[-1]:.2f}",
                                 delta=f"Entry ₹{p.get('entry_p1', 0):.2f}", delta_color="off")
                    dz[1].metric(f"{l2}", f"₹{p2s.iloc[-1]:.2f}",
                                 delta=f"Entry ₹{p.get('entry_p2', 0):.2f}", delta_color="off")
                    dz[2].metric("Z-Score", f"{latest_z:.2f}",
                                 delta=f"Entry Z: ±{entry_z_th}")
                    dz[3].metric("P&L", f"₹{pnl:+,.0f}",
                                 delta=f"{days_held}d held / {remaining}d left",
                                 delta_color="off")
                    dz[4].metric("Hedge Ratio", f"{hr:.2f}")
                    dz[5].metric("Direction",
                                 "🔴 SHORT" if p.get("direction") == "SHORT" else "🟢 LONG",
                                 delta=f"SL: ±{sl_z:.1f}σ", delta_color="off")
                else:
                    st.info("Not enough data points (need >20 candles).")
            else:
                st.info("Could not fetch 15m chart data.")

    # -- Active Signals (new entries + exits today) --
    st.subheader(f"🚨 Today's Signals ({len(active)})")
    if active:
        df_a = pd.DataFrame(active)
        df_a.insert(0, "", df_a["Signal"].apply(_icon))
        st.dataframe(df_a, use_container_width=True, hide_index=True)
        for _, r in df_a.iterrows():
            s = r["Signal"]
            if "ENTRY" in s:
                (st.success if "LONG" in s else st.error)(
                    f"**{r['s1']}/{r['s2']}**: {s} at Z={r['Z-score']:.2f}")
            elif "EXIT" in s:
                st.info(f"**{r['s1']}/{r['s2']}**: {s} at Z={r['Z-score']:.2f}")
    else:
        st.info("No signals yet today.")

    # -- All Pairs --
    st.subheader(f"📋 All Pairs ({len(pairs)})")
    if pairs:
        df = pd.DataFrame(pairs)
        df.insert(0, "", df["Signal"].apply(_icon))
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No pairs in latest scan results.")

    _show_log()


def _show_log():
    st.subheader("📜 Trade Journal")
    try:
        pair_cache = get_pair_cache()
        trades = pair_cache.get_signal_history(limit=20)
        if trades:
            df = pd.DataFrame(trades)
            df.insert(0, "", df.apply(_icon, axis=1))
            cols = ["", "entry_date", "exit_date", "s1", "s2", "direction", "status",
                    "entry_price_s1", "exit_price_s1", "entry_price_s2", "exit_price_s2",
                    "lot_size_s1", "lot_size_s2", "pnl_s1", "pnl_s2", "total_pnl",
                    "exit_reason"]
            avail = [c for c in cols if c in df.columns]
            df = df[avail]
            fmt_cols = ["pnl_s1", "pnl_s2", "total_pnl"]
            for c in fmt_cols:
                if c in df.columns:
                    df[c] = df[c].apply(lambda v: f"₹{v:+,.0f}" if v is not None and not pd.isna(v) else "")
            st.dataframe(df, use_container_width=True)
            return
    except Exception:
        pass
    st.info("No trades yet. Entries and exits will appear here once detected.")
