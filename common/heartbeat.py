"""
Centralized heartbeat utility for all ngen26 jobs.
Every cron job/agent writes:
  TIMESTAMP | PID | STATUS | JOB_NAME

Pre-market check reads all heartbeats to verify run completion.
"""
import os, time, json, sys, tempfile

HEARTBEAT_DIR = tempfile.gettempdir() if sys.platform == "win32" else "/tmp"
HEARTBEAT_FILES = {
    "daily_login": "shoonya_login.heartbeat",
    "unified_agent": "unified_agent.heartbeat",
    "pre_market_health": "premarket_health.heartbeat",
    "selections_check": "selections_check.heartbeat",
    "swing_donchian": "swing_donchian.heartbeat",
    "swing_keltner": "swing_keltner.heartbeat",
    "swing_supertrend": "swing_supertrend.heartbeat",
    "sync_daily": "sync_daily.heartbeat",
    "sync_intraday": "sync_intraday.heartbeat",
    "sync_daily_intra": "sync_daily_intra.heartbeat",
    "sync_all": "sync_all.heartbeat",
    "sync_1m": "sync_1m.heartbeat",
    "sync_5m": "sync_5m.heartbeat",
    "sync_hourly": "sync_hourly.heartbeat",
    "pair_scan": "pair_scan.heartbeat",
    "pair_monitor": "pair_monitor.heartbeat",
    "flattrade_auth": "flattrade_auth.heartbeat",
    "intra_selection": "intra_selection.heartbeat",
}


def write(name, status="running"):
    path = os.path.join(HEARTBEAT_DIR, HEARTBEAT_FILES.get(name, f"{name}.heartbeat"))
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(f"{time.time()}|{os.getpid()}|{status}|{name}")
    except Exception:
        pass


def read(name):
    path = os.path.join(HEARTBEAT_DIR, HEARTBEAT_FILES.get(name, f"{name}.heartbeat"))
    try:
        with open(path) as f:
            parts = f.read().strip().split("|")
            return {
                "timestamp": float(parts[0]),
                "pid": int(parts[1]) if len(parts) > 1 else 0,
                "status": parts[2] if len(parts) > 2 else "unknown",
                "job": parts[3] if len(parts) > 3 else name,
                "age_seconds": time.time() - float(parts[0]),
            }
    except Exception:
        return None


def check(name, max_age=3600):
    hb = read(name)
    if hb is None:
        return {"status": "missing", "name": name}
    stale = hb["age_seconds"] > max_age
    return {
        "status": "stale" if stale else hb.get("status", "ok"),
        "name": name,
        "age": hb["age_seconds"],
        "pid": hb["pid"],
    }


def report(jobs):
    results = []
    for name, max_age in jobs:
        results.append(check(name, max_age))
    return results
