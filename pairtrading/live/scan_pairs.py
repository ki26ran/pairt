"""
Scheduled script: scans all saved pairs from pair_thresholds.json for entry signals.
Tracks open positions (entry -> exit lifecycle) across days.
Uses 1H candles, caps concurrent positions at 3, prioritizes by z-score magnitude.

Modes:
  --mode scan     (default) Full run: data download + entry scan + exit check. Run hourly.
  --mode monitor  Light run: cached data, exit check + P&L update only. Run every 5 min.
"""
import os, sys, json, argparse, math
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import logging
logging.getLogger("yfinance").setLevel(logging.WARNING)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(BASE_DIR)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from common.market_data.cache import get_cache
from pairtrading.live.cache import get_pair_cache
from configs.telegram_config import send_message as pt_send
from configs.symbols import get_nifty200
from configs.settings import LIVE, BROKER_NAME, BROKER_USERNAME

_LOT_CACHE = None
_broker_api = None
_SECTOR_CACHE = None  # {symbol_with_NS: sector}
_cooldowns = {}       # {pair_key: datetime} — stop-loss cooldown (24h)
_lot_scales = {}      # {pair_key: float} — position size multiplier

THRESHOLDS_FILE = os.path.join(BASE_DIR, "configs", "pair_thresholds.json")

MAX_POSITIONS = int(os.environ.get("PT_MAX_POSITIONS", 5))
ROLL_WIN = 63
CACHE_MAX_AGE_HOURS = 1
STOP_LOSS_MULTIPLIER = 2.0
MAX_HOLD_DAYS = 20
ENTRY_MIN_DTE = 10   # skip expiries within 10 days for new entries
ROLL_DTE = 7         # roll existing positions when expiry is within 7 days

SIGNAL_HEADERS = ["Time", "s1", "s2", "s1_price", "s2_price", "Z-score", "Entry Z", "Exit Z", "Signal", "hr"]

# Track EOD summary to avoid duplicate sends
_EOD_SENT_KEY = "eod_summary_sent"


def _get_broker_api():
    global _broker_api
    if not LIVE:
        return None
    if _broker_api is not None:
        return _broker_api
    try:
        sys.path.insert(0, ROOT)
        sys.path.insert(1, os.path.join(ROOT, "broker"))
        from broker.broker.api import setup_api
        _broker_api = setup_api(BROKER_NAME, BROKER_USERNAME)
        return _broker_api
    except Exception as e:
        print(f"  Broker login failed: {e}")
        return None


def _place_pair_order(s1, s2, direction, z_score, lot_scale=1.0):
    """Place a pair entry order using options (PE on s1, CE on s2). Returns (order_id, option_symbol, expiry)."""
    api = _get_broker_api()
    opt_symbol = None
    expiry_date = None

    try:
        cache = get_cache()
        # Buy PE on s1 (expect it to fall), Buy CE on s2 (expect it to rise)
        nfo1 = cache.resolve_option_contract(s1, None, "SHORT", strike_mode="ATM", min_dte=ENTRY_MIN_DTE)
        nfo2 = cache.resolve_option_contract(s2, None, "LONG", strike_mode="ATM", min_dte=ENTRY_MIN_DTE)
        if nfo1 and nfo2:
            opt_symbol = nfo1["trading_symbol"]
            expiry_date = nfo1["expiry"]
    except Exception:
        pass

    if api is None or not LIVE:
        return None, opt_symbol, expiry_date

    try:
        side = "LONG" if direction == "LONG" else "SHORT"
        from broker.broker.api import place_live_order
        clean_s1 = s1.replace(".NS", "").replace(".BO", "")
        base_qty = _lot_size(s1)
        qty = max(1, int(round(base_qty * lot_scale)))
        remarks = f"PT_{direction[:4]}_{clean_s1}_{z_score:.2f}_L{lot_scale:.1f}"
        trading_symbol = opt_symbol if opt_symbol else f"{clean_s1}-EQ"
        exchange = "NFO" if opt_symbol else "NSE"
        oid = place_live_order(
            trading_symbol, side, qty, remarks,
            exchange=exchange, product_type="I", price_type="MKT"
        )
        return oid, opt_symbol, expiry_date
    except Exception as e:
        print(f"  Pair order failed for {s1}: {e}")
        return None, opt_symbol, expiry_date


def _place_pair_exit(s1, direction, reason, z_score):
    """Close the first leg of a pair position."""
    api = _get_broker_api()
    if api is None:
        return
    try:
        side = "SHORT" if direction == "LONG" else "LONG"
        from broker.broker.api import place_live_order
        clean_s1 = s1.replace(".NS", "").replace(".BO", "")
        qty = _lot_size(s1)
        reason_short = {"mean-reversion": "MR", "stop-loss": "SL", "timeout": "TO"}.get(reason, reason[:3])
        remarks = f"PT_{reason_short}_{clean_s1}_{z_score:.2f}"
        place_live_order(
            f"{clean_s1}-EQ", side, qty, remarks,
            exchange="NSE", product_type="I", price_type="MKT"
        )
    except Exception as e:
        print(f"  Pair exit failed for {s1}: {e}")


def _eod_already_sent(pair_cache):
    return pair_cache.get_config(_EOD_SENT_KEY) == datetime.now().strftime("%Y-%m-%d")


def _mark_eod_sent(pair_cache):
    pair_cache.set_config(_EOD_SENT_KEY, datetime.now().strftime("%Y-%m-%d"))


def _lot_size(sym):
    global _LOT_CACHE
    if _LOT_CACHE is None:
        try:
            _LOT_CACHE = {e["Symbol"]: e["LotSize"] for e in get_nifty200()}
        except Exception:
            _LOT_CACHE = {}
    return _LOT_CACHE.get(sym, _LOT_CACHE.get(sym.replace(".NS", "").replace(".BO", "") + ".NS", 1))


def _sector(sym):
    global _SECTOR_CACHE
    if _SECTOR_CACHE is None:
        try:
            _SECTOR_CACHE = {e["Symbol"]: e.get("Sector", "") for e in get_nifty200()}
        except Exception:
            _SECTOR_CACHE = {}
    return _SECTOR_CACHE.get(sym, _SECTOR_CACHE.get(sym.replace(".NS", "") + ".NS", ""))


def _in_cooldown(pair_key):
    """Check if pair is in stop-loss cooldown (24h)."""
    if pair_key not in _cooldowns:
        return False
    elapsed = (datetime.now() - _cooldowns[pair_key]).total_seconds()
    if elapsed >= 86400:  # 24 hours
        del _cooldowns[pair_key]
        return False
    return True


def load_thresholds():
    """Load thresholds from PairTrading DuckDB, fall back to JSON file."""
    try:
        from pairtrading.live.cache import get_pair_cache
        th = get_pair_cache().load_thresholds()
        if th:
            return th
        print("[WARN] No thresholds in DuckDB, falling back to JSON")
    except Exception as e:
        print(f"[WARN] DuckDB threshold load failed: {e}, falling back to JSON")
    if os.path.exists(THRESHOLDS_FILE):
        with open(THRESHOLDS_FILE) as f:
            return json.load(f)
    return {}


def load_data(thresholds, all_stocks, force_fetch=False):
    now = datetime.now()
    end = now
    start = end - timedelta(days=90)
    cache = get_cache()
    clean_stocks = [s.replace('.NS', '').replace('.BO', '') for s in all_stocks]
    import duckdb as _duckdb
    con = _duckdb.connect(cache.db_path)
    placeholders = ", ".join(["?"] * len(clean_stocks))
    query = f"""
        SELECT ticker, datetime_ist, open, high, low, close, volume
        FROM hourly_bars
        WHERE ticker IN ({placeholders})
        AND datetime_ist >= ? AND datetime_ist < ?
        ORDER BY datetime_ist
    """
    params = clean_stocks + [start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")]
    dfh = con.execute(query, params).df()
    con.close()
    if dfh.empty:
        return None
    pieces = {}
    for s, sc in zip(all_stocks, clean_stocks):
        sd = dfh[dfh["ticker"] == sc].set_index("datetime_ist")
        for col in ["open", "high", "low", "close", "volume"]:
            key = (col.capitalize(), s)
            pieces[key] = sd[col] if col in sd.columns else pd.Series(dtype=float)
    if not pieces:
        return None
    df = pd.DataFrame(pieces)
    df.columns = pd.MultiIndex.from_tuples(df.columns, names=["Price", "Ticker"])
    df = df.sort_index()
    return df


def process_signals(thresholds, raw, pair_cache, mode):
    """Main signal processing logic. Returns (signals, all_pairs_data, updated_positions)."""
    if raw is None or raw.empty or not isinstance(raw.columns, pd.MultiIndex):
        return [], [], {}

    # Extract close prices and compute z-scores
    pairs_data = []
    signals = []
    all_positions = pair_cache.load_positions()
    open_positions = {k: v for k, v in all_positions.items() if v.get("direction")}

    # Iterate through each threshold pair
    for pair_key, th in thresholds.items():
        s1, s2 = pair_key.split("|")
        if s1 not in raw.columns.get_level_values(1) or s2 not in raw.columns.get_level_values(1):
            continue
        hr = th.get("hr", 1.0)
        entry_z = th.get("entry_z", 2.0)
        exit_z = th.get("exit_z", 0.0)

        p1 = raw["Close"][s1].copy()
        p2 = raw["Close"][s2].copy()
        combined = pd.concat([p1, p2], axis=1).dropna()
        if combined.empty:
            continue
        p1, p2 = combined.iloc[:, 0], combined.iloc[:, 1]

        spread = p1 - hr * p2
        spread_mean = spread.rolling(ROLL_WIN).mean()
        spread_std = spread.rolling(ROLL_WIN).std()
        zscore = ((spread - spread_mean) / spread_std).dropna()
        if len(zscore) < 2:
            continue

        latest_z = zscore.iloc[-1]
        latest_time = zscore.index[-1]
        latest_p1 = p1.iloc[-1]
        latest_p2 = p2.iloc[-1]

        pair_data = {
            "Time": latest_time.strftime("%Y-%m-%d %H:%M:%S"),
            "s1": s1, "s2": s2,
            "s1_price": round(latest_p1, 2), "s2_price": round(latest_p2, 2),
            "Z-score": round(latest_z, 4),
            "Entry Z": entry_z, "Exit Z": exit_z,
            "Signal": "NONE", "hr": hr
        }

        # Check if we have an open position for this pair
        pos = open_positions.get(pair_key)
        if pos:
            # Manage existing position
            direction = pos["direction"]
            entry_z_val = float(pos.get("entry_z", 0))
            entry_date = pos.get("entry_date", "")
            days_held = (datetime.now() - pd.Timestamp(entry_date)).days if entry_date else 0

            exit_reason = None

            # Expiry rollover check (close if DTE < ROLL_DTE)
            pos_expiry = pos.get("expiry_date")
            if pos_expiry:
                try:
                    exp_dt = pd.Timestamp(pos_expiry).date() if not hasattr(pos_expiry, 'date') else pos_expiry
                    dte = (exp_dt - datetime.now().date()).days
                    if dte < ROLL_DTE:
                        exit_reason = "expiry_roll"
                except Exception:
                    pass

            if not exit_reason:
                if direction == "LONG":
                    if latest_z >= exit_z:
                        exit_reason = "mean-reversion"
                    elif latest_z <= -entry_z * STOP_LOSS_MULTIPLIER:
                        exit_reason = "stop-loss"
                else:  # SHORT
                    if latest_z <= -exit_z:
                        exit_reason = "mean-reversion"
                    elif latest_z >= entry_z * STOP_LOSS_MULTIPLIER:
                        exit_reason = "stop-loss"

                if days_held >= MAX_HOLD_DAYS:
                    exit_reason = "timeout"

            if exit_reason:
                # Record stop-loss cooldown (prevent re-entry for 24h)
                if "stop-loss" in exit_reason:
                    _cooldowns[pair_key] = datetime.now()
                # Place broker exit order if live
                _place_pair_exit(s1, direction, exit_reason, latest_z)
                # Close position and record trade
                ls = _lot_scales.pop(pair_key, 1.0)
                pair_cache.close_position(pair_key, exit_p1=latest_p1, exit_p2=latest_p2, exit_z=latest_z, exit_reason=exit_reason, lot_scale=ls)
                _log_signal(pair_cache, s1, s2, latest_p1, latest_p2, latest_z, entry_z, exit_z, f"EXIT {direction} ({exit_reason})", hr)
                signal = f"EXIT {direction} ({exit_reason})"
                signals.append({
                    "Time": latest_time.strftime("%Y-%m-%d %H:%M:%S"),
                    "s1": s1, "s2": s2,
                    "s1_price": round(latest_p1, 2), "s2_price": round(latest_p2, 2),
                    "Z-score": round(latest_z, 4),
                    "Entry Z": entry_z, "Exit Z": exit_z,
                    "Signal": signal, "hr": hr
                })
                # Remove from local open_positions dict so we don't double-close
                open_positions.pop(pair_key, None)
            else:
                # Update position with latest data
                pair_cache.update_position(pair_key, latest_z, latest_p1, latest_p2)
                signal = f"IN {direction}"
                pair_data["Signal"] = signal
        else:
            # Check for new entry (skip in monitor mode)
            if mode == "monitor":
                pair_data["Signal"] = "NONE (monitor)"
                pairs_data.append(pair_data)
                continue
            signal = "NONE"
            if latest_z >= entry_z:
                # Cooldown check: don't re-enter within 24h of stop-loss
                if _in_cooldown(pair_key):
                    pair_data["Signal"] = "NONE (cooldown)"
                else:
                    signal = "ENTRY SHORT"

            if signal != "NONE" and len(open_positions) < MAX_POSITIONS:
                # Sector diversification: skip if same-sector position already open
                sec = _sector(s1)
                if sec:
                    same_sector = any(pair_key != p and _sector(p.split("|")[0]) == sec for p in open_positions)
                    if same_sector:
                        pair_data["Signal"] = "NONE (sector cap)"
                        pairs_data.append(pair_data)
                        continue

                direction = "SHORT"
                lot_scale = 1.0  # fixed 1 lot
                _lot_scales[pair_key] = lot_scale
                broker_order_id, opt_symbol, expiry_date = _place_pair_order(s1, s2, direction, latest_z, lot_scale)
                pair_cache.open_position(
                    pair_key, s1, s2, direction,
                    latest_z, latest_p1, latest_p2,
                    entry_z, exit_z, hr,
                    broker_order_id=broker_order_id,
                    expiry_date=str(expiry_date) if expiry_date else None
                )
                signals.append({
                    "Time": latest_time.strftime("%Y-%m-%d %H:%M:%S"),
                    "s1": s1, "s2": s2,
                    "s1_price": round(latest_p1, 2), "s2_price": round(latest_p2, 2),
                    "Z-score": round(latest_z, 4),
                    "Entry Z": entry_z, "Exit Z": exit_z,
                    "Signal": signal, "hr": hr
                })
                _log_signal(pair_cache, s1, s2, latest_p1, latest_p2, latest_z, entry_z, exit_z, signal, hr)
                open_positions[pair_key] = {"direction": direction}

        pairs_data.append(pair_data)

    return signals, pairs_data, all_positions


def run_scan(pair_cache):
    """Full scan mode: fetch data, check entries/exits, save results."""
    print(f"=== PairTrading SCAN [1H, Cap={MAX_POSITIONS}] @ {datetime.now():%H:%M} ===")

    thresholds = load_thresholds()
    if not thresholds:
        print("No thresholds found. Run discover_pairs and backtest first.")
        return

    all_stocks = list(set([s for pair in thresholds for s in pair.split("|")]))
    raw = load_data(thresholds, all_stocks)

    signals, pairs_data, positions = process_signals(thresholds, raw, pair_cache, "scan")

    # Save results
    result = {
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pairs": pairs_data,
        "active_signals": signals,
        "config": {"max_positions": MAX_POSITIONS, "interval": "1h", "roll_window": ROLL_WIN}
    }
    pair_cache.save_scanner_results(result)
    pair_cache.save_today_signals(signals)

    print(f"Done. {len(pairs_data)} pairs tracked, {len(signals)} signals today, {len(pair_cache.load_positions())} open positions (cap={MAX_POSITIONS}).")


def run_monitor(pair_cache):
    """Lightweight monitor mode: use cached data, only check exits."""
    print(f"=== PairTrading MONITOR @ {datetime.now():%H:%M} ===")

    thresholds = load_thresholds()
    if not thresholds:
        print("No thresholds found.")
        return

    all_stocks = list(set([s for pair in thresholds for s in pair.split("|")]))
    raw = load_data(thresholds, all_stocks)
    if raw is None:
        print("No cached data available")
        return

    signals, pairs_data, positions = process_signals(thresholds, raw, pair_cache, "monitor")

    # Update results
    result = {
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pairs": pairs_data,
        "active_signals": signals,
        "config": {"max_positions": MAX_POSITIONS, "interval": "1h", "roll_window": ROLL_WIN}
    }
    pair_cache.save_scanner_results(result)
    pair_cache.save_today_signals(signals)

    print(f"Done. {len(pairs_data)} pairs, {len(signals)} signals, {len(pair_cache.load_positions())} open positions.")


def send_eod_summary(pair_cache):
    """Send end-of-day summary via Telegram."""
    if _eod_already_sent(pair_cache):
        return

    positions = pair_cache.load_positions()
    if not positions:
        return

    try:
        cache = get_cache()
        tickers = list(set([p["s1"].replace(".NS", "") for p in positions.values()] +
                           [p["s2"].replace(".NS", "") for p in positions.values()]))
        today_str = datetime.now().strftime('%Y-%m-%d')
        cache.ensure_fresh([t + ".NS" for t in tickers], "15m")
        df = cache.get_bulk_multiindex([t + ".NS" for t in tickers], today_str, today_str, interval="15m")

        if df.empty or not isinstance(df.columns, pd.MultiIndex):
            return

        msg_lines = ["*PairTrading EOD Summary*"]
        total_pnl = 0
        for pk, p in positions.items():
            s1, s2 = p["s1"].replace(".NS", ""), p["s2"].replace(".NS", "")
            if s1 + ".NS" not in df.columns.get_level_values(1) or s2 + ".NS" not in df.columns.get_level_values(1):
                continue
            p1 = df["Close"][s1 + ".NS"].dropna()
            p2 = df["Close"][s2 + ".NS"].dropna()
            if p1.empty or p2.empty:
                continue
            latest_p1 = p1.iloc[-1]
            latest_p2 = p2.iloc[-1]

            lot1 = _lot_size(s1)
            lot2 = _lot_size(s2)
            ep1, ep2 = p["entry_p1"], p["entry_p2"]
            if p["direction"] == "LONG":
                pnl = (latest_p1 - ep1) * lot1 + (ep2 - latest_p2) * lot2
            else:
                pnl = (ep1 - latest_p1) * lot1 + (latest_p2 - ep2) * lot2
            total_pnl += pnl

            days_held = (datetime.now() - pd.Timestamp(p["entry_date"])).days if p.get("entry_date") else 0
            emoji = "🟢" if p["direction"] == "LONG" else "🔴"
            msg_lines.append(
                f"{emoji} {s1}/{s2} {p['direction']} | "
                f"Entry Z={p['entry_z_threshold']} Exit Z={p['exit_z_threshold']} | "
                f"Day {days_held}/{MAX_HOLD_DAYS} | P&L: {pnl:+.0f}"
            )

        msg_lines.append(f"\nTotal Unrealized P&L: {total_pnl:+,.0f}")
        pt_send("\n".join(msg_lines))
        _mark_eod_sent(pair_cache)
    except Exception as e:
        print(f"[WARN] EOD summary failed: {e}")


def _log_signal(pair_cache, s1, s2, s1_price, s2_price, z_score, entry_z, exit_z, signal, hr):
    """Log a signal to history via pair cache only."""
    pair_cache.log_signal(s1, s2, s1_price, s2_price, z_score, entry_z, exit_z, signal, hr)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["scan", "monitor"], default="scan",
                        help="scan: full run (hourly), monitor: light run (every 5min)")
    args = parser.parse_args()

    # Write heartbeat
    hb_name = "pair_scan" if args.mode == "scan" else "pair_monitor"
    try:
        import sys, os
        _base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _base not in sys.path:
            sys.path.insert(0, _base)
        from common.heartbeat import write as _hbw
        _hbw(hb_name, "running")
    except Exception:
        pass

    pair_cache = get_pair_cache()

    if args.mode == "scan":
        run_scan(pair_cache)
        # Send EOD summary at 15:45
        now = datetime.now()
        if now.hour == 15 and now.minute >= 45:
            send_eod_summary(pair_cache)
    elif args.mode == "monitor":
        run_monitor(pair_cache)
    else:
        print(f"Unknown mode: {args.mode}")
        sys.exit(1)

    try:
        from common.heartbeat import write as _hbw
        _hbw(hb_name, "ok")
    except Exception:
        pass


if __name__ == "__main__":
    main()
