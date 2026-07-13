import streamlit as st
import os, sys, json, shutil
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

ROOT = os.path.dirname(BASE_DIR)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from common.market_data.provider import is_config_locked, _load_config

DATA_DIR = os.path.join(BASE_DIR, "data")
CONFIGS_DIR = os.path.join(BASE_DIR, "configs")
BT_DIR = os.path.join(DATA_DIR, "bt_results")
_MARKDOWN_LOCK_HELP = """
**When locked:**
- 🔍 Discover Pairs disabled
- ⚡ Optimize All disabled
- 🔄 Reset Defaults disabled
- Only Monitor and Backtest are active

Unlock only when you need to change pairs or re-optimize.
"""

def _cleanup_all():
    """Delete all PairTrading runtime data. Returns (success_count, errors)."""
    success = []
    errors = []
    
    # Files to delete (legacy — may still exist)
    files = [
        os.path.join(DATA_DIR, "pair_scanner_results.json"),
        os.path.join(DATA_DIR, "scanner_1h_cache.pkl"),
        os.path.join(DATA_DIR, ".eod_summary_sent"),
        os.path.join(DATA_DIR, "pair_scanner_positions.json"),
        os.path.join(DATA_DIR, "pair_discovery_status.json"),
        os.path.join(DATA_DIR, "pairs.csv"),
        os.path.join(CONFIGS_DIR, "pair_thresholds.json"),
        os.path.join(CONFIGS_DIR, "telegram_config.json"),
    ]
    
    for f in files:
        try:
            if os.path.exists(f):
                os.remove(f)
                success.append(f"Removed: {os.path.basename(f)}")
        except Exception as e:
            errors.append(f"Failed to remove {os.path.basename(f)}: {e}")
    
    # Clear bt_results directory
    try:
        if os.path.exists(BT_DIR):
            for fn in os.listdir(BT_DIR):
                fp = os.path.join(BT_DIR, fn)
                os.remove(fp)
            success.append("Cleared: bt_results/ directory")
    except Exception as e:
        errors.append(f"Failed to clear bt_results/: {e}")
    
    # Clear PairTrading DuckDB (pairtrading.duckdb) — all tables
    try:
        from pairtrading.live.cache import get_pair_cache
        import duckdb
        pair_cache = get_pair_cache()
        con = duckdb.connect(pair_cache.get_db_path())
        for r in con.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main'").fetchall():
            t = r[0]
            try:
                con.execute(f"DELETE FROM {t}")
            except Exception:
                pass
        con.close()
        success.append("Cleared: all PairTrading tables in pairtrading.duckdb")
    except Exception as e:
        errors.append(f"PairTrading DuckDB cleanup: {e}")
    
    # Remove pair_positions table from market_data.duckdb (cleanup legacy)
    try:
        from common.market_data.cache import get_cache
        import duckdb
        cache = get_cache()
        con = duckdb.connect(cache.db_path)
        con.execute("DROP TABLE IF EXISTS pair_positions")
        con.close()
        success.append("Removed: pair_positions table from market_data.duckdb")
    except Exception as e:
        errors.append(f"market_data.duckdb cleanup: {e}")
    
    # Remove maintenance last-run marker
    try:
        marker = os.path.join(BASE_DIR, ".maintenance_last_run")
        if os.path.exists(marker):
            os.remove(marker)
            success.append("Removed: .maintenance_last_run")
    except Exception as e:
        errors.append(f"Maintenance marker: {e}")
    
    return success, errors

def show():
    st.title("PairTrading Admin")

    tab1, tab2, tab3, tab4, tab5 = st.tabs(["Telegram", "Broker", "Config Lock", "Cleanup / Reset", "About"])

    with tab1:
        from pairtrading.configs.telegram_config import get_config, update_config, is_configured, send_message

        st.subheader("PairTrading Telegram Bot")
        st.markdown("Configure a Telegram bot for pair trading alerts (entry/exit signals, errors).")

        cfg = get_config()
        bot_token = st.text_input("Bot Token", value=cfg.get("bot_token", ""),
                                  type="password", key="pt_tg_token")
        chat_id = st.text_input("Chat ID", value=cfg.get("chat_id", ""), key="pt_tg_chat_id")

        col1, col2 = st.columns(2)
        with col1:
            if st.button("Save Config", type="primary", key="pt_save"):
                if len(bot_token) < 20 or "Error" in bot_token:
                    st.error("Invalid bot token.")
                else:
                    update_config(bot_token, chat_id)
                    st.success("PairTrading Telegram config saved.")
        with col2:
            if is_configured():
                if st.button("Send Test Message", key="pt_test"):
                    if send_message("*PairTrading Admin*\n\nTest message from dashboard.\nPairTrading bot is working."):
                        st.success("Test message sent.")
                    else:
                        st.error("Failed to send. Check token and chat ID.")

        if is_configured():
            st.success("PairTrading Telegram bot is configured.")
        else:
            st.info("Not configured yet. Enter bot token and chat ID above.")

        st.divider()
        st.markdown("""
        **Setup:**
        1. Create a bot via [@BotFather](https://t.me/BotFather)
        2. Copy the token into the field above
        3. Get your chat ID from [@userinfobot](https://t.me/userinfobot)
        4. Send any message to your bot first
        5. Save and test

        **Alerts sent to this bot:**
        - Entry signals (new pair position opened)
        - Exit signals (pair position closed via z-score reversion)
        - Error alerts (data fetch failures)

        Config stored at `configs/telegram_config.json` (gitignored).
        """)

    with tab2:
        st.subheader("Broker Configuration")
        st.caption("Settings for live broker order placement. Disabled = paper trading only.")

        from pairtrading.configs.settings import LIVE, BROKER_NAME, BROKER_USERNAME

        live = st.checkbox("Live Trading", value=LIVE, key="pt_live")
        bn = st.text_input("Broker Name", value=BROKER_NAME, key="pt_bn")
        bu = st.text_input("Broker Username", value=BROKER_USERNAME, key="pt_bu")

        if st.button("Save Broker Config", type="primary", key="pt_save_broker"):
            from pairtrading.configs.settings import BROKER_CONFIG_FILE
            with open(BROKER_CONFIG_FILE, "w") as f:
                json.dump({"live": live, "broker_name": bn, "broker_username": bu}, f, indent=2)
            st.success("Broker config saved. Restart scan_pairs.py to apply.")

    with tab3:
        st.subheader("🔒 Configuration Lock")
        st.caption("Protects pairs and thresholds from accidental changes. Lock during normal operation.")

        _locked = is_config_locked()
        _cfg = _load_config()
        _env = os.environ.get("APP_ENV", "dev")

        st.info(f"**Environment:** `{_env}`  ·  **Host:** `{_cfg.get('host', '?')}`  ·  **Provider:** `{_cfg.get('portfolio_providers', {}).get('__default__', '?')}`")

        locked = st.checkbox("Config Locked", value=_locked, key="pt_locked")
        st.markdown(_MARKDOWN_LOCK_HELP)

        if st.button("Save Lock Setting", type="primary", key="pt_save_lock"):
            config_path = os.path.join(ROOT, "common", "market_data", f"config.{_env}.json")
            if not os.path.exists(config_path):
                config_path = os.path.join(ROOT, "common", "market_data", "config.json")
            try:
                with open(config_path) as f:
                    d = json.load(f)
                d["config_locked"] = locked
                with open(config_path, "w") as f:
                    json.dump(d, f, indent=2)
                st.success(f"Config locked set to `{locked}` in `{os.path.basename(config_path)}`. Restarting...")
                st.rerun()
            except Exception as e:
                st.error(f"Failed to save: {e}")

    with tab4:
        st.subheader("🗑️ Complete Cleanup & Reset")
        st.warning(
            "**This will permanently delete ALL PairTrading data from `pairtrading.duckdb`:**\n"
            "- Open positions, signals, today's signals\n"
            "- Scanner results, discovered pairs, thresholds\n"
            "- Config (telegram, discovery status, EOD marker)\n"
            "- All backtest results files (`bt_results/`)\n"
            "- Legacy JSON/CSV cache files\n\n"
            "After cleanup, you'll need to:\n"
            "1. Run **Discover Pairs** to find new pairs\n"
            "2. Run **Backtest & Optimize** for each pair\n"
            "3. Run **Live Monitor** to start fresh"
        )

        if st.button("🚨 DELETE ALL PAIRTRADING DATA", type="primary", key="pt_nuke"):
            confirm = st.checkbox("I understand this is irreversible", key="pt_confirm_nuke")
            if confirm:
                with st.spinner("Cleaning up..."):
                    success, errors = _cleanup_all()
                if success:
                    st.success(f"Cleanup complete ({len(success)} items):")
                    for s in success:
                        st.write(f"  ✅ {s}")
                if errors:
                    st.error(f"Some errors occurred ({len(errors)}):")
                    for e in errors:
                        st.write(f"  ❌ {e}")
                if success:
                    st.toast("PairTrading reset complete — start from Discover Pairs", icon="✅")
                    st.rerun()

        st.divider()
        
        st.subheader("📋 Current Data Status")
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**PairTrading DuckDB (pairtrading.duckdb):**")
            try:
                pair_cache = get_pair_cache()
                stats = pair_cache.get_stats()
                for table, count in stats.items():
                    st.write(f"  📊 {table}: {count} rows")
            except Exception as e:
                st.write(f"  ❌ Could not connect: {e}")

        with col2:
            st.markdown("**Legacy Files (may exist):**")
            for name in ["pair_scanner_results.json", "pair_scanner_positions.json",
                         "pairs.csv", "pair_thresholds.json", "scanner_1h_cache.pkl",
                         "pair_signals_log.csv", ".eod_summary_sent"]:
                path = os.path.join(DATA_DIR, name)
                if not os.path.exists(path) and name in ("pair_thresholds.json", "telegram_config.json"):
                    path = os.path.join(CONFIGS_DIR, name)
                exists = os.path.exists(path)
                st.write(f"  {'✅' if exists else '❌'} {name}")

    with tab5:
        st.markdown("""
        **PairTrading Admin:**
        - **Telegram** — configure the dedicated bot for pair trading alerts
        - **Broker** — toggle live trading on/off
        - **Config Lock** — protect pairs from accidental changes
        - **Cleanup / Reset** — wipe all data and start fresh
        - New pairs discovered via **Discover Pairs** page
        - Thresholds optimized in **Backtest & Optimize** page
        - Open positions monitored in **Live Monitor** page
        """)
