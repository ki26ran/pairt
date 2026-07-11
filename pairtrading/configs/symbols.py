"""
Single source of truth: DuckDB universe table via DataCache.
All stock symbols, lot sizes, tick sizes come from the shared DuckDB.
"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _get_cache():
    from common.market_data.cache import get_cache
    return get_cache()


def get_nifty200():
    """Return list of dicts: [{Symbol, LotSize, TickSize, Sector}] — Nifty 200."""
    c = _get_cache()
    return [
        {"Symbol": s["symbol"] + ".NS", "LotSize": s["lot_size"],
         "TickSize": s["tick_size"], "Sector": s.get("sector", "")}
        for s in c.get_all_stocks()
        if s["nifty200"]
    ]


def get_nifty100():
    """Return list of dicts: [{Symbol, LotSize, TickSize, Sector}] — Nifty 100 only.

    Same-sector pairs from Nifty 100 have higher structural cointegration
    than cross-sector pairs from larger universes.
    """
    c = _get_cache()
    return [
        {"Symbol": s["symbol"] + ".NS", "LotSize": s["lot_size"],
         "TickSize": s["tick_size"], "Sector": s.get("sector", "")}
        for s in c.get_all_stocks()
        if s["nifty100"]
    ]


# Maintain backward compatibility: module-level NIFTY200 attribute
def __getattr__(name):
    if name == 'NIFTY200':
        return get_nifty200()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
