import streamlit as st
import pandas as pd
import os, json, threading, time, traceback, io
from datetime import datetime
from pairtrading.configs.settings import DATA_DIR
from pairtrading.configs.symbols import get_nifty100, get_nifty200
from pairtrading.core.pair_discovery import discover_pairs as run_discovery
from pairtrading.live.cache import get_pair_cache
from common.market_data.provider import is_config_locked


def _save_status(status):
    pair_cache = get_pair_cache()
    pair_cache.set_config("discovery_status", status)


def _load_status():
    pair_cache = get_pair_cache()
    return pair_cache.get_config("discovery_status") or {}


def _discover(corr, pvalue, years, universe):
    if is_config_locked():
        _save_status({"status": "failed", "completed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                       "error": "Config is locked (config_locked=true). Discovery disabled."})
        return
    _save_status({"status": "running", "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
    buf = io.StringIO()
    try:
        symbols = get_nifty200() if universe == "Nifty 200" else get_nifty100()
        df = run_discovery(symbols, corr_threshold=corr, pvalue_threshold=pvalue, years=years)
        if not df.empty:
            pair_cache = get_pair_cache()
            pair_cache.save_discovered_pairs(df)
            _save_status({"status": "completed", "completed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                           "pairs": len(df), "output": buf.getvalue()})
        else:
            _save_status({"status": "failed", "completed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                           "error": "No pairs found meeting criteria", "output": "Discovery completed but no pairs passed all filters."})
    except Exception as e:
        _save_status({"status": "failed", "completed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                       "error": str(e)[:200], "output": traceback.format_exc()[-1000:]})


def _render_pairs_table(df):
    st.subheader(f"Pairs: {len(df)}")
    stock_filter = st.text_input("Filter by stock", placeholder="e.g. IRFC or INFY,TCS", key="pair_filter")
    filtered = df
    if stock_filter:
        terms = [t.strip().upper() for t in stock_filter.replace(",", " ").split()]
        mask = pd.Series(False, index=df.index)
        for term in terms:
            mask |= df["Stock1"].str.upper().str.contains(term, na=False)
            mask |= df["Stock2"].str.upper().str.contains(term, na=False)
        filtered = df[mask]
        st.caption(f"Showing {len(filtered)} of {len(df)} pairs")
    st.dataframe(filtered.style.format({c: "{:.4f}" for c in filtered.select_dtypes("float").columns}),
                 use_container_width=True, height=400)


def _configure_discovered_as_thresholds(corr=None, pval=None, years=None, universe=None):
    """Replace all thresholds with the currently discovered pairs (default params)."""
    import json
    from pairtrading.configs.settings import DATA_DIR
    pc = get_pair_cache()
    disc = pc.load_discovered_pairs()
    if not disc:
        return False, "No discovered pairs to configure"
    thresholds = {}
    for row in disc:
        s1 = row.get("Stock1") or row.get("stock1", "")
        s2 = row.get("Stock2") or row.get("stock2", "")
        key = f"{s1}|{s2}"
        thresholds[key] = {
            "entry_z": 2.0,
            "exit_z": 0.5,
            "hr": float(row.get("hedge_ratio", row.get("Hedge_Ratio", 1.0))),
        }
    pc.save_thresholds(thresholds)
    th_path = os.path.join(os.path.dirname(DATA_DIR), "configs", "pair_thresholds.json")
    with open(th_path, "w") as f:
        json.dump(thresholds, f, indent=2, default=str)
    # Save the discovery params used
    if corr is not None:
        pc.set_config("last_discovery_params", {
            "corr": corr, "pval": pval, "years": years, "universe": universe
        })
    return True, f"Configured {len(thresholds)} pairs for trading"


def show():
    st.title("🔎 Discover Pairs")
    st.markdown("Find cointegrated stock pairs from Nifty 100 (same-sector only). Results feed into Backtest & Optimize.")
    st.markdown(
        "<div style='background:#1a3a5c;border-radius:6px;padding:8px 14px;font-size:13px;color:#ccc'>"
        "<b>Workflow:</b> "
        "<span style='color:#1f77b4;font-weight:bold'>Step 1: Discover</span> → "
        "Step 2: Backtest → "
        "Step 3: Optimize → "
        "Step 4: Monitor"
        "</div>",
        unsafe_allow_html=True,
    )

    if "_disc_thread" not in st.session_state:
        st.session_state._disc_thread = None

    status = _load_status()

    running = bool(status and status.get("status") == "running")

    if running and st.session_state._disc_thread and not st.session_state._disc_thread.is_alive():
        status = _load_status()
        running = False

    if running:
        st.info("⏳ Pair discovery running... (started %s)" % status["started_at"])
        time.sleep(0.5)
        st.rerun()

    _locked = is_config_locked()

    # ── Load last discovery params and show ──
    _pair_cache = get_pair_cache()
    _last = _pair_cache.get_config("last_discovery_params")
    if _last:
        st.info(f"📋 Last configured with: Corr≥{_last['corr']}, P-val<{_last['pval']}, Years={_last['years']}, {_last['universe']}")
    elif _pair_cache.load_thresholds():
        st.info(f"📋 {len(_pair_cache.load_thresholds())} pairs currently configured")

    if _locked:
        st.warning("🔒 Configuration is locked (config_locked=true). Discovery and threshold changes are disabled.")

    pc1, pc2, pc3, pc4 = st.columns(4)
    with pc1:
        corr = st.number_input("Corr ≥", min_value=0.0, max_value=1.0,
                               value=_last["corr"] if _last else 0.80, step=0.05)
    with pc2:
        pval = st.number_input("P-val <", min_value=0.001, max_value=0.5,
                               value=_last["pval"] if _last else 0.05, step=0.01, format="%.3f")
    with pc3:
        years = st.number_input("Years", min_value=1, max_value=5,
                                value=_last["years"] if _last else 2, step=1)
    with pc4:
        universe = st.selectbox("Universe", ["Nifty 100", "Nifty 200"],
                                index=1 if _last and _last.get("universe") == "Nifty 200" else 0)

    if st.button("🔍 Discover Pairs", disabled=running or _locked, key="disc_btn"):
        st.session_state._disc_thread = threading.Thread(target=_discover, args=(corr, pval, years, universe), daemon=True)
        st.session_state._disc_thread.start()
        st.rerun()

    if status and status.get("status") == "completed":
        st.success("✅ Discovery completed — %d pairs found" % status.get("pairs", 0))
        st.caption("Last updated: %s" % status.get("completed_at", ""))

        pair_cache = get_pair_cache()
        pairs_list = pair_cache.load_discovered_pairs()
        if pairs_list:
            df = pd.DataFrame(pairs_list)
            _render_pairs_table(df)

            col_a, col_b = st.columns([1, 1])
            with col_a:
                if st.button("⚙️ Configure for Trading", type="primary", key="cfg_trading", disabled=_locked):
                    ok, msg = _configure_discovered_as_thresholds(corr, pval, years, universe)
                    if ok:
                        st.success(msg)
                        pair_cache.set_config("discovery_status", {"status": "configured",
                            "configured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "pairs": len(pairs_list)})
                        st.rerun()
                    else:
                        st.error(msg)
            with col_b:
                if st.button("🗑 Clear Results", key="clear_pairs"):
                    pair_cache.clear_discovered_pairs()
                    pair_cache.delete_config("discovery_status")
                    st.rerun()

        out = status.get("output", "")
        if out:
            with st.expander("📋 Console Output"):
                st.code(out)

    elif status and status.get("status") == "failed":
        st.error("❌ Discovery failed: %s" % status.get("error", "Unknown error"))
        out = status.get("output", "")
        if out:
            with st.expander("📋 Console Output"):
                st.code(out)
        if st.button("Clear Status", key="clear_fail"):
            pair_cache = get_pair_cache()
            pair_cache.delete_config("discovery_status")
            st.rerun()

    else:
        pair_cache = get_pair_cache()
        pairs_list = pair_cache.load_discovered_pairs()
        if pairs_list:
            df = pd.DataFrame(pairs_list)
            _render_pairs_table(df)
        else:
            st.info("No pairs discovered yet. Set parameters and click **🔍 Discover Pairs**.")
