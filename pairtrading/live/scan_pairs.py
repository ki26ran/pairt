"""
Scheduled script: scans all saved pairs from pair_thresholds.json for entry signals.
Tracks open positions (entry -> exit lifecycle) across days.
Uses 1H candles, caps concurrent positions at 3, prioritizes by z-score magnitude.

Modes:
  --mode scan     (default) Full run: data download + entry scan + exit check. Run hourly.
  --mode monitor  Light run: cached data, exit check + P&L update only. Run every 5 min.
"""
import os, sys, json, argparse, math, time
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
from pairtrading.configs.telegram_config import send_message as pt_send
from pairtrading.configs.symbols import get_nifty200
from pairtrading.configs.settings import LIVE, BROKER_NAME, BROKER_USERNAME

_LOT_CACHE = None
_broker_api = None
_SECTOR_CACHE = None  # {symbol_with_NS: sector}
_cooldowns = {}       # {pair_key: datetime} — stop-loss cooldown (24h)
_lot_scales = {}      # {pair_key: float} — position size multiplier
_retry_legs = {}      # {pair_key: dict} — partially filled pairs needing leg retry

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
        from ganah import setup_api
        _broker_api = setup_api(BROKER_NAME, BROKER_USERNAME)
        return _broker_api
    except Exception as e:
        print(f"  Broker login failed: {e}")
        return None


def _strike_interval(price):
    """Determine NSE option strike interval based on underlying price."""
    if price <= 50: return 2.5
    if price <= 100: return 5
    if price <= 250: return 10
    if price <= 500: return 25
    if price <= 1000: return 50
    if price <= 5000: return 100
    if price <= 10000: return 200
    return 500


def resolve_option_contract(symbol, option_type, min_dte=ENTRY_MIN_DTE):
    """Resolve ATM option contract from cached NFO symbols table.
    
    Falls back to the NFO symbols DuckDB table (synced from ngen26).
    Returns dict with trading_symbol, lot_size, strike, expiry, bid, ask
    or None on failure.
    """
    try:
        from common.market_data.cache import get_cache
        import duckdb
        cache = get_cache()
        con = duckdb.connect(cache.db_path, read_only=True)
        clean = symbol.replace(".NS", "").replace(".BO", "")
        
        # Get current underlying price
        price_row = con.execute(
            "SELECT close FROM hourly_bars WHERE ticker = ? ORDER BY datetime_ist DESC LIMIT 1",
            [clean]
        ).fetchone()
        if not price_row:
            con.close()
            return None
        underlying = float(price_row[0])
        
        # Find ATM strike
        interval = _strike_interval(underlying)
        target_strike = round(underlying / interval) * interval
        
        # Search for the ATM option in NFO table, preferring higher DTE
        opt_map = {"PE": "PE", "CE": "CE"}
        nfo_type = opt_map.get(option_type, option_type)
        
        rows = con.execute("""
            SELECT trading_symbol, strike_price, expiry, lot_size 
            FROM nfo_symbols 
            WHERE symbol = ? AND option_type = ? 
              AND strike_price >= ? AND strike_price <= ?
              AND expiry > CURRENT_DATE
            ORDER BY ABS(strike_price - ?), expiry
            LIMIT 5
        """, [clean, nfo_type, target_strike - interval, target_strike + interval, target_strike]).fetchall()
        con.close()
        
        if not rows:
            return None
        
        # Pick the closest strike with sufficient DTE
        best = None
        for r in rows:
            expiry = r[2]
            dte = (expiry - datetime.now().date()).days if hasattr(expiry, 'date') else 99
            if dte >= min_dte:
                best = {
                    "trading_symbol": r[0],
                    "strike": float(r[1]),
                    "expiry": str(r[2]),
                    "lot_size": int(r[3]),
                    "dte": dte,
                }
                break
        
        if not best:
            # Take whatever is available (even if low DTE)
            r = rows[0]
            best = {
                "trading_symbol": r[0],
                "strike": float(r[1]),
                "expiry": str(r[2]),
                "lot_size": int(r[3]),
                "dte": (r[2] - datetime.now().date()).days if hasattr(r[2], 'date') else 99,
            }
        
        # Estimate ATM premium (~2% of underlying for ATM option)
        tick = _strike_interval(underlying * 0.02)
        tick_size = max(0.05, _strike_interval(underlying) / 10)  # price tick from underlying
        best["estimated_premium"] = round(underlying * 0.02 / tick) * tick
        best["limit_price"] = round(underlying * 0.03 / tick) * tick  # generous limit (3%), rounded to tick
        best["tick_size"] = tick_size
        return best
    except Exception as e:
        print(f"  [NFO] Error resolving {symbol} {option_type}: {e}")
        return None


def _place_with_retry(api, trading_symbol, side, qty, remarks, initial_price, tick_size, is_buy):
    """Place a limit order, wait ~30s, retry tick-by-tick if unfilled (max 3 attempts)."""
    from ganah import place_live_order, order_status
    price = initial_price
    for attempt in range(3):
        oid = place_live_order(trading_symbol, side, qty, remarks,
                               exchange="NFO", product_type="M", price_type="LMT", price=price)
        if not oid:
            print(f"  Order placement failed for {trading_symbol}")
            return 0, 0
        print(f"  Attempt {attempt+1}/3: {trading_symbol} limit=₹{price:.2f} oid={oid}")
        time.sleep(30)
        filled, avg, _, _ = order_status(oid)
        if filled == 1:
            print(f"  ✅ Filled at ₹{avg}")
            return 1, avg
        api.cancel_order(oid)
        print(f"  ⏳ Not filled yet — bumping price by 1 tick...")
        if is_buy:
            price = round((price + tick_size) / tick_size) * tick_size
        else:
            price = round((price - tick_size) / tick_size) * tick_size
    print(f"  ❌ Could not fill {trading_symbol} after 3 attempts")
    return 0, 0


def _place_pair_order(s1, s2, direction, z_score, lot_scale=1.0, retry_leg=None):
    """Place a pair entry order using options with live bid/ask quotes.
    Returns (order_id, opt_symbol, expiry, fill_status) where fill_status bitmask:
      0 = none filled, 1 = s1 filled, 2 = s2 filled, 3 = both filled.
    If retry_leg is specified, only place the missing leg."""
    api = _get_broker_api()
    if api is None or not LIVE:
        return None, None, None, 0
    
    try:
        opt1 = "PE" if direction == "SHORT" else "CE"
        opt2 = "CE" if direction == "SHORT" else "PE"
        nfo1 = resolve_option_contract(s1, opt1, ENTRY_MIN_DTE)
        nfo2 = resolve_option_contract(s2, opt2, ENTRY_MIN_DTE)
        if not nfo1 or not nfo2:
            print(f"  Cannot resolve options for {s1}/{s2} — skipping trade")
            return None, None, None, 0
        
        from ganah import place_live_order
        qty1 = max(1, int(round(nfo1["lot_size"] * lot_scale)))
        qty2 = max(1, int(round(nfo2["lot_size"] * lot_scale)))
        clean_s1 = s1.replace(".NS", "").replace(".BO", "")
        remarks = f"PT_{direction[:4]}_{clean_s1}_{z_score:.2f}_L{lot_scale:.1f}"
        
        # Fetch live bid/ask quotes for both legs
        q1 = api.get_quotes("NFO", nfo1["trading_symbol"]) if isinstance(api.get_quotes("NFO", nfo1["trading_symbol"]), dict) else {}
        q2 = api.get_quotes("NFO", nfo2["trading_symbol"]) if isinstance(api.get_quotes("NFO", nfo2["trading_symbol"]), dict) else {}
        bid1 = float(q1.get("bp1", 0)) if q1 else 0
        ask1 = float(q1.get("sp1", 0)) if q1 else 0
        bid2 = float(q2.get("bp1", 0)) if q2 else 0
        ask2 = float(q2.get("sp1", 0)) if q2 else 0
        tick1 = float(nfo1.get("tick_size", 0.05))
        tick2 = float(nfo2.get("tick_size", 0.05))
        # Buy at ask (matches best seller) — near-guaranteed fill
        limit1 = round(ask1 / tick1) * tick1 if ask1 > 0 else nfo1["limit_price"]
        limit2 = round(ask2 / tick2) * tick2 if ask2 > 0 else nfo2["limit_price"]
        # On retries, add 1 tick above ask for priority
        if retry_leg is not None:
            limit1 = round((ask1 + tick1) / tick1) * tick1 if ask1 > 0 else nfo1["limit_price"]
            limit2 = round((ask2 + tick2) / tick2) * tick2 if ask2 > 0 else nfo2["limit_price"]
        
        fill_status = 0
        oid1, oid2 = None, None
        
        if retry_leg != 2:
            print(f"  {s1}: {nfo1['trading_symbol']} bid={q1.get('bp1','?')} ask={q1.get('sp1','?')} limit=₹{limit1:.2f}")
            f1, ap1 = _place_with_retry(api, nfo1["trading_symbol"], "LONG", qty1, remarks, limit1, tick1, is_buy=True)
            if f1:
                fill_status |= 1
                print(f"  {s1} filled ✅ at ₹{ap1}")
        if retry_leg != 1:
            print(f"  {s2}: {nfo2['trading_symbol']} bid={q2.get('bp1','?')} ask={q2.get('sp1','?')} limit=₹{limit2:.2f}")
            f2, ap2 = _place_with_retry(api, nfo2["trading_symbol"], "LONG", qty2, remarks, limit2, tick2, is_buy=True)
            if f2:
                fill_status |= 2
                print(f"  {s2} filled ✅ at ₹{ap2}")
        
        return (oid1 or oid2, nfo1["trading_symbol"], nfo1["expiry"], fill_status)
    except Exception as e:
        print(f"  Pair order failed: {e}")
        return None, None, None, 0


def _place_pair_exit(s1, s2, direction, reason, z_score):
    """Close a pair position using live bid/ask quotes (sell at bid), retry tick-by-tick."""
    api = _get_broker_api()
    if api is None:
        return
    try:
        clean_s1 = s1.replace(".NS", "").replace(".BO", "")
        reason_short = {"mean-reversion": "MR", "stop-loss": "SL", "timeout": "TO"}.get(reason, reason[:3])
        remarks = f"PT_{reason_short}_{clean_s1}_{z_score:.2f}"

        opt_type1 = "PE" if direction == "SHORT" else "CE"
        opt_type2 = "CE" if direction == "SHORT" else "PE"
        nfo1 = resolve_option_contract(s1, opt_type1, 0)
        nfo2 = resolve_option_contract(s2, opt_type2, 0)

        for label, sym, nfo in [("s1", s1, nfo1), ("s2", s2, nfo2)]:
            if not nfo:
                print(f"  Cannot resolve option for exit on {label} — skipping")
                continue
            q = api.get_quotes("NFO", nfo["trading_symbol"])
            if isinstance(q, dict):
                bid = float(q.get("bp1", 0))
                tick = float(nfo.get("tick_size", 0.05))
                price = round(bid / tick) * tick if bid > 0 else round(nfo["estimated_premium"] * 0.5, 2)
            else:
                price = round(nfo["estimated_premium"] * 0.5, 2)
            _place_with_retry(api, nfo["trading_symbol"], "SHORT", nfo["lot_size"],
                              remarks, price, tick, is_buy=False)
    except Exception as e:
        print(f"  Pair exit failed: {e}")


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
                # Retry missing leg for partially filled positions
                retry_info = _retry_legs.get(pair_key)
                if retry_info:
                    missing_leg = 1 if not (retry_info["filled"] & 1) else 2
                    print(f"  ⚠️ Retrying missing leg ({'s1' if missing_leg == 1 else 's2'}) for {pair_key}")
                    _, _, _, new_fill = _place_pair_order(s1, s2, direction, latest_z, retry_info.get("lot_scale", 1.0), retry_leg=missing_leg)
                    if new_fill & missing_leg:
                        retry_info["filled"] |= missing_leg
                        print(f"  ✅ Missing leg filled for {pair_key}")
                    elif retry_info.get("retries", 0) < 3:
                        retry_info["retries"] = retry_info.get("retries", 0) + 1
                        print(f"  ⏳ Will retry (attempt {retry_info['retries']+1}/3)")
                    else:
                        print(f"  ❌ Max retries reached for {pair_key} missing leg")
                    if retry_info["filled"] == 3:
                        del _retry_legs[pair_key]
                else:
                    # Broker-based detection: check if both legs exist at broker
                    api = _get_broker_api()
                    if api:
                        _broker_pos = api.get_positions()
                        if isinstance(_broker_pos, list):
                            opt1 = "PE" if direction == "SHORT" else "CE"
                            opt2 = "CE" if direction == "SHORT" else "PE"
                            n1 = resolve_option_contract(s1, opt1, 0)
                            n2 = resolve_option_contract(s2, opt2, 0)
                            has1 = any(p.get("instname") == "OPTSTK" and int(p.get("netqty", 0)) != 0
                                       and n1 and p.get("tsym") == n1["trading_symbol"] for p in _broker_pos)
                            has2 = any(p.get("instname") == "OPTSTK" and int(p.get("netqty", 0)) != 0
                                       and n2 and p.get("tsym") == n2["trading_symbol"] for p in _broker_pos)
                            if has1 and not has2:
                                print(f"  ⚠️ {s2} {opt2} missing at broker — placing...")
                                _, _, _, new_fill = _place_pair_order(s1, s2, direction, latest_z, 1.0, retry_leg=2)
                                if new_fill & 2:
                                    print(f"  ✅ {s2} leg filled")
                            elif has2 and not has1:
                                print(f"  ⚠️ {s1} {opt1} missing at broker — placing...")
                                _, _, _, new_fill = _place_pair_order(s1, s2, direction, latest_z, 1.0, retry_leg=1)
                                if new_fill & 1:
                                    print(f"  ✅ {s1} leg filled")
                
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
                _place_pair_exit(s1, s2, direction, exit_reason, latest_z)
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
                if _in_cooldown(pair_key):
                    pair_data["Signal"] = "NONE (cooldown)"
                else:
                    signal = "ENTRY SHORT"
                    direction = "SHORT"
            elif latest_z <= -entry_z:
                if _in_cooldown(pair_key):
                    pair_data["Signal"] = "NONE (cooldown)"
                else:
                    signal = "ENTRY LONG"
                    direction = "LONG"

            if signal != "NONE" and len(open_positions) < MAX_POSITIONS:
                # Sector diversification: skip if same-sector position already open
                sec = _sector(s1)
                if sec:
                    same_sector = any(pair_key != p and _sector(p.split("|")[0]) == sec for p in open_positions)
                    if same_sector:
                        pair_data["Signal"] = "NONE (sector cap)"
                        pairs_data.append(pair_data)
                        continue

                lot_scale = 1.0  # fixed 1 lot
                _lot_scales[pair_key] = lot_scale
                oid, opt_symbol, expiry_date, fill_status = _place_pair_order(s1, s2, direction, latest_z, lot_scale)
                if fill_status > 0:
                    pair_cache.open_position(
                        pair_key, s1, s2, direction,
                        latest_z, latest_p1, latest_p2,
                        entry_z, exit_z, hr,
                        broker_order_id=oid,
                        expiry_date=str(expiry_date) if expiry_date else None
                    )
                    # If partial fill, mark position for leg retry
                    if fill_status < 3:
                        _retry_legs[pair_key] = {"filled": fill_status, "s1": s1, "s2": s2,
                                                  "direction": direction, "z_score": latest_z,
                                                  "lot_scale": lot_scale}
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

    pair_cache = None
    for attempt in range(10):
        try:
            pair_cache = get_pair_cache()
            # Verify connection by loading thresholds
            if pair_cache.load_thresholds() is not None:
                break
        except Exception as e:
            if "lock" in str(e).lower():
                print(f"  DB locked (attempt {attempt+1}/10), waiting 15s...")
                time.sleep(15)
            else:
                print(f"  DB error (attempt {attempt+1}): {e}")
                time.sleep(5)
    if pair_cache is None:
        print("  Could not access pairtrading DB after 10 attempts. Skipping.")
        return

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
