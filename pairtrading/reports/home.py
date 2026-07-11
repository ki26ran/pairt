import streamlit as st
from datetime import datetime, date
import os, threading, sys, io

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
ROOT = os.path.dirname(BASE_DIR)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from pairtrading.live.cache import get_pair_cache

THRESHOLDS_FILE = os.path.join(BASE_DIR, "configs", "pair_thresholds.json")
MAINTENANCE_LOG = os.path.join(BASE_DIR, "data", ".maintenance_last_run")


def _last_maintenance():
    if os.path.exists(MAINTENANCE_LOG):
        try:
            with open(MAINTENANCE_LOG) as f:
                return f.read().strip()
        except Exception:
            pass
    try:
        th = get_pair_cache().load_thresholds()
        dates = [v["hr_updated_at"] for v in th.values() if v.get("hr_updated_at")]
        if dates:
            return max(dates)
    except Exception:
        pass
    return None


def _run_maintenance():
    try:
        from pairtrading.optimizer import maintenance
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            maintenance()
        finally:
            sys.stdout = old_stdout
        output = buf.getvalue()
        with open(MAINTENANCE_LOG, "w") as f:
            f.write(datetime.now().strftime("%Y-%m-%d %H:%M"))
        st.session_state._maint_output = output
        st.session_state._maint_running = False
        st.session_state._maint_done = True
    except Exception as e:
        st.session_state._maint_output = str(e)
        st.session_state._maint_running = False
        st.session_state._maint_done = False


def show():
    st.title("🏠 Pair Trading Dashboard")

    # Quick status row
    th_count = 0
    hr_count = 0
    try:
        th = get_pair_cache().load_thresholds()
        th_count = len(th)
        hr_count = sum(1 for v in th.values() if v.get("hr"))
    except Exception:
        pass

    live_positions = 0
    last_scan = "never"
    try:
        rdata = get_pair_cache().load_scanner_results()
        if rdata:
            live_positions = len(get_pair_cache().load_positions())
            lu = rdata.get("last_updated", "")
            last_scan = str(lu).split()[0] if lu else "never"
    except Exception:
        pass

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Discovered Pairs", th_count)
    c2.metric("With Hedge Ratio", hr_count)
    c3.metric("Open Positions", live_positions)
    c4.metric("Last Scan", last_scan.split()[0] if last_scan != "never" else "—")

    st.divider()

    st.subheader("How Pair Trading Works")
    st.markdown("""
Pair trading finds **two stocks whose prices move together** (cointegrated). When they diverge, we
bet they'll converge back:

- **Buy the cheap one, short the expensive one** — profit when the spread narrows
- Exit when the spread reverts, hits a stop-loss, or times out
- Only pairs stocks from the **same sector** (banks with banks, IT with IT)
""")

    st.divider()

    st.subheader("The Workflow — 4 Steps")
    st.markdown("")

    wc1, wc2, wc3, wc4 = st.columns(4)

    with wc1:
        st.markdown("""
        <div style='background:#1a3a5c;border-radius:10px;padding:15px;text-align:center;height:200px'>
        <div style='font-size:32px;margin-bottom:8px'>🔎</div>
        <div style='font-size:14px;color:#aaa'>STEP 1</div>
        <div style='font-size:16px;font-weight:bold;margin:4px 0'>Discover Pairs</div>
        <div style='font-size:11px;color:#ccc'>Find cointegrated stock pairs from Nifty 100.
        Run once, then monthly via maintenance.</div>
        </div>
        """, unsafe_allow_html=True)

    with wc2:
        st.markdown("""
        <div style='background:#1a3a1a;border-radius:10px;padding:15px;text-align:center;height:200px'>
        <div style='font-size:32px;margin-bottom:8px'>📊</div>
        <div style='font-size:14px;color:#aaa'>STEP 2</div>
        <div style='font-size:16px;font-weight:bold;margin:4px 0'>Backtest</div>
        <div style='font-size:11px;color:#ccc'>Test strategies with custom parameters,
        date range, instrument type, and max pairs.</div>
        </div>
        """, unsafe_allow_html=True)

    with wc3:
        st.markdown("""
        <div style='background:#2a1a3a;border-radius:10px;padding:15px;text-align:center;height:200px'>
        <div style='font-size:32px;margin-bottom:8px'>🧪</div>
        <div style='font-size:14px;color:#aaa'>STEP 3</div>
        <div style='font-size:16px;font-weight:bold;margin:4px 0'>Optimize</div>
        <div style='font-size:11px;color:#ccc'>Walk-forward grid search to find optimal
        entry/exit thresholds for live trading.</div>
        </div>
        """, unsafe_allow_html=True)

    with wc4:
        st.markdown("""
        <div style='background:#1a3a2a;border-radius:10px;padding:15px;text-align:center;height:200px'>
        <div style='font-size:32px;margin-bottom:8px'>📊</div>
        <div style='font-size:14px;color:#aaa'>STEP 4</div>
        <div style='font-size:16px;font-weight:bold;margin:4px 0'>Live Monitor</div>
        <div style='font-size:11px;color:#ccc'>Watch open positions, P&L, and charts.
        The scanner runs fully automatically.</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("")
    st.info("👈 Select **1. Discover Pairs** from the sidebar to begin.")

    st.divider()

    # ── Monthly Maintenance ────────────────────────────────────────
    st.subheader("🔄 Monthly Maintenance")

    last_run = _last_maintenance()
    now = datetime.now()
    overdue = False
    if last_run:
        try:
            last_dt = datetime.strptime(last_run[:10], "%Y-%m-%d")
            days_since = (now - last_dt).days
            overdue = days_since > 35
        except Exception:
            days_since = 0
        st.caption(f"Last maintenance run: **{last_run}** ({days_since} days ago)")
        if overdue:
            st.warning(f"⏰ It's been {days_since} days — run maintenance soon.")
    else:
        st.caption("Last maintenance run: **never**")
        st.info("⚠️ Run maintenance once to re-estimate hedge ratios and check pair health.")

    if "_maint_running" not in st.session_state:
        st.session_state._maint_running = False
    if "_maint_done" not in st.session_state:
        st.session_state._maint_done = False
    if "_maint_output" not in st.session_state:
        st.session_state._maint_output = ""

    if st.session_state._maint_running:
        st.info("⏳ Maintenance running... (re-estimating hedge ratios, checking cointegration, replenishing pairs)")

    col_a, col_b = st.columns([1, 2])
    with col_a:
        disabled = st.session_state._maint_running
        if st.button("🔧 Run Maintenance Now", disabled=disabled, type="primary"):
            st.session_state._maint_running = True
            st.session_state._maint_done = False
            st.session_state._maint_output = ""
            threading.Thread(target=_run_maintenance, daemon=True).start()
            st.rerun()

    with col_b:
        st.markdown("Run **once per month** (first week). Re-estimates hedge ratios, removes "
                    "degenerated pairs, and discovers up to 5 new same-sector pairs.")

    if st.session_state._maint_done and st.session_state._maint_output:
        st.success("✅ Maintenance complete.")
        with st.expander("📋 Maintenance output"):
            st.code(st.session_state._maint_output[-3000:])
        if st.button("Clear output"):
            st.session_state._maint_output = ""
            st.session_state._maint_done = False
            st.rerun()

    st.divider()

    st.subheader("What Happens Automatically")
    st.markdown("""
| Frequency | Task | Details |
|-----------|------|---------|
| **Every hour** | Entry scan | Downloads hourly data, checks all pairs for z-score crossing entry threshold |
| **Every 5 min** | Exit check | Lightweight P&L update + exit checks (mean-reversion, stop-loss, timeout) |
| **3:15–3:30 PM** | EOD summary | Telegram message with P&L for all open positions |
| **Monthly (manual)** | Maintenance | Click the button above — re-estimates hedge ratios, purges degenerated pairs, adds new ones |

**You only need to:** 1. Discover Pairs → 2. Backtest & Optimize → 3. Let automation run. Click **Maintenance** once a month.
""")
