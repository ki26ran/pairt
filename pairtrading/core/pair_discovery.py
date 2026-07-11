import sys, os
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from statsmodels.tsa.stattools import coint
from statsmodels.api import OLS, add_constant


def _get_pt_cache():
    ROOT = os.path.dirname(BASE_DIR)
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from common.market_data.cache import get_cache
    return get_cache()


def discover_pairs(symbols, corr_threshold=0.70, pvalue_threshold=0.05, years=2,
                   require_same_sector=True):
    """Discover cointegrated pairs.

    When *require_same_sector* is True (default), only stocks from the same
    sector are paired — eliminating spurious cross-sector statistical artifacts.
    """
    end = datetime.now()
    start = end - timedelta(days=years * 365)
    tickers = [s["Symbol"] for s in symbols]

    # Build sector lookup
    sector_of = {s["Symbol"]: s.get("Sector", "") for s in symbols}

    cache = _get_pt_cache()
    df = cache.get_bulk_multiindex(tickers, start.strftime("%Y-%m-%d"),
                                    end.strftime("%Y-%m-%d"), interval="1d")

    if df.empty or not isinstance(df.columns, pd.MultiIndex):
        return pd.DataFrame()

    closes = df["Close"]
    closes = closes.dropna(axis=1, thresh=int(len(closes) * 0.7))
    closes = closes.ffill().dropna()

    if closes.shape[1] < 2:
        return pd.DataFrame()

    n = len(symbols)
    results = []
    cols = closes.columns.tolist()

    for i in range(n):
        for j in range(i + 1, n):
            s1 = symbols[i]["Symbol"]
            s2 = symbols[j]["Symbol"]

            # Sector constraint
            if require_same_sector:
                sec1 = sector_of.get(s1, "")
                sec2 = sector_of.get(s2, "")
                if sec1 != sec2 or not sec1:
                    continue

            if s1 not in cols or s2 not in cols:
                continue

            pair_data = closes[[s1, s2]].dropna()
            if len(pair_data) < 60:
                continue

            p1 = pair_data[s1]
            p2 = pair_data[s2]

            corr = p1.corr(p2)
            if corr < corr_threshold:
                continue

            try:
                coint_result = coint(p1, p2)
                coint_pvalue = coint_result[1]
            except Exception:
                continue

            if coint_pvalue >= pvalue_threshold:
                continue

            try:
                X = add_constant(p2)
                model = OLS(p1, X).fit()
                hr = model.params.iloc[1] if hasattr(model.params, 'iloc') else model.params[1]
            except Exception:
                hr = 1.0

            if hr <= 0.01 or hr > 25:
                continue

            try:
                spread = p1 - hr * p2
                lag_spread = spread.shift(1)
                valid = pd.concat([spread, lag_spread], axis=1).dropna()
                Y = valid.iloc[:, 0]
                X_hl = add_constant(valid.iloc[:, 1])
                ar_model = OLS(Y, X_hl).fit()
                beta = ar_model.params.iloc[1] if hasattr(ar_model.params, 'iloc') else ar_model.params[1]
                if 0 < beta < 1:
                    half_life = round(-np.log(2) / np.log(beta), 1)
                else:
                    half_life = 999
            except Exception:
                half_life = 999

            results.append({
                "Stock1": s1,
                "Stock2": s2,
                "Sector": sector_of.get(s1, ""),
                "Correlation": round(corr, 4),
                "Coint_PValue": round(coint_pvalue, 6),
                "Hedge_Ratio": round(hr, 4),
                "Half_Life": half_life,
            })

    if not results:
        return pd.DataFrame()

    result_df = pd.DataFrame(results)
    result_df = result_df.sort_values("Coint_PValue")
    return result_df.reset_index(drop=True)


def reestimate_hedge_ratio(s1, s2, lookback_days=180):
    """Re-estimate the hedge ratio using a recent rolling window of daily data.

    Returns (hr, corr, coint_pvalue) or raises on failure.
    """
    cache = _get_pt_cache()
    end = datetime.now()
    start = end - timedelta(days=lookback_days)
    df = cache.get_bulk_multiindex([s1, s2], start.strftime("%Y-%m-%d"),
                                    end.strftime("%Y-%m-%d"), interval="1d")
    if df.empty or not isinstance(df.columns, pd.MultiIndex):
        raise ValueError("No data")
    p1 = df["Close"][s1].dropna()
    p2 = df["Close"][s2].dropna()
    pair = pd.concat([p1, p2], axis=1).dropna()
    if len(pair) < 30:
        raise ValueError(f"Only {len(pair)} data points")
    s1c, s2c = pair.iloc[:, 0], pair.iloc[:, 1]
    corr_val = s1c.corr(s2c)
    X = add_constant(s2c)
    model = OLS(s1c, X).fit()
    hr = model.params.iloc[1] if hasattr(model.params, 'iloc') else model.params[1]
    try:
        coint_result = coint(s1c, s2c)
        coint_p = float(coint_result[1])
    except Exception:
        coint_p = 1.0
    return round(hr, 4), round(corr_val, 4), round(coint_p, 6)
