"""
DataCache — DuckDB-backed market data store.
Singleton, cache-first with live fallback. Shared by all projects.
"""
import os, sys, json, time, importlib, re
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd
import numpy as np

_duckdb_available = False
try:
    import duckdb
    _duckdb_available = True
except ImportError:
    pass

from .provider import get_provider

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE, "config.json")


def _load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {}


def _parse_retention(ret_str: str) -> timedelta:
    parts = ret_str.split()
    val = int(parts[0])
    unit = parts[1].lower()
    if "year" in unit:
        return timedelta(days=val * 365)
    elif "day" in unit:
        return timedelta(days=val)
    return timedelta(days=365)


class DataCache:
    _instances = {}

    @classmethod
    def instance(cls, portfolio="__default__") -> "DataCache":
        if portfolio not in cls._instances:
            cls._instances[portfolio] = DataCache(portfolio)
        return cls._instances[portfolio]

    def __init__(self, portfolio="__default__"):
        if not _duckdb_available:
            raise ImportError("duckdb is required. Install with: pip install duckdb")
        self.portfolio = portfolio
        self.config = _load_config()
        db_name = self.config.get("db_path", "market_data.duckdb")
        self.db_path = os.path.join(BASE, db_name)
        self.provider = get_provider(portfolio)
        self._ensure_db()
        self._ensure_universe()

    def _db_read(self):
        """Read-only connection — retries on lock conflict or stale write connections."""
        for attempt in range(20):
            try:
                return duckdb.connect(self.db_path, read_only=True)
            except Exception as e:
                msg = str(e).lower()
                if attempt < 19 and ("lock" in msg or "cannot open" in msg or "used by another process" in msg or "different configuration" in msg):
                    time.sleep(0.3 * (attempt + 1))
                    continue
                raise

    def _db_write(self):
        """Read-write connection — retries on lock conflict."""
        for attempt in range(20):
            try:
                return duckdb.connect(self.db_path)
            except Exception as e:
                msg = str(e).lower()
                if attempt < 19 and ("lock" in msg or "cannot open" in msg or "used by another process" in msg):
                    time.sleep(0.3 * (attempt + 1))
                    continue
                raise

    def _ensure_db(self):
        con = None
        try:
            con = self._db_write()
            con.execute("""
                CREATE TABLE IF NOT EXISTS daily_bars (
                    ticker VARCHAR NOT NULL,
                    date DATE NOT NULL,
                    open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume BIGINT,
                    PRIMARY KEY (ticker, date)
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS hourly_bars (
                    ticker VARCHAR NOT NULL,
                    datetime_ist TIMESTAMP NOT NULL,
                    open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume BIGINT,
                    PRIMARY KEY (ticker, datetime_ist)
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS bars_5min (
                    ticker VARCHAR NOT NULL,
                    datetime_ist TIMESTAMP NOT NULL,
                    open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume BIGINT,
                    PRIMARY KEY (ticker, datetime_ist)
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS bars_15min (
                    ticker VARCHAR NOT NULL,
                    datetime_ist TIMESTAMP NOT NULL,
                    open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume BIGINT,
                    PRIMARY KEY (ticker, datetime_ist)
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS bars_1min (
                    ticker VARCHAR NOT NULL,
                    datetime_ist TIMESTAMP NOT NULL,
                    open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume BIGINT,
                    PRIMARY KEY (ticker, datetime_ist)
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS sync_log (
                    ticker VARCHAR NOT NULL,
                    timeframe VARCHAR NOT NULL,
                    last_sync TIMESTAMP,
                    bar_count INTEGER,
                    first_date DATE,
                    last_date DATE,
                    status VARCHAR,
                    PRIMARY KEY (ticker, timeframe)
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS universe (
                    symbol VARCHAR PRIMARY KEY,
                    lot_size INTEGER,
                    tick_size DOUBLE,
                    nifty50 BOOLEAN,
                    nifty100 BOOLEAN,
                    nifty200 BOOLEAN,
                    margin_lt_200k BOOLEAN,
                    fno BOOLEAN,
                    sector VARCHAR,
                    source VARCHAR
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS nfo_symbols (
                    exchange VARCHAR,
                    token BIGINT,
                    lot_size INTEGER,
                    symbol VARCHAR,
                    trading_symbol VARCHAR,
                    expiry DATE,
                    instrument VARCHAR,
                    option_type VARCHAR,
                    strike_price DOUBLE,
                    tick_size DOUBLE,
                    PRIMARY KEY (trading_symbol)
                )
            """)
            try:
                con.execute("CREATE INDEX IF NOT EXISTS idx_nfo_options ON nfo_symbols(symbol, expiry, option_type)")
            except Exception:
                pass
            for col, col_type in [("nifty100", "BOOLEAN"), ("sector", "VARCHAR")]:
                try:
                    con.execute(f"ALTER TABLE universe ADD COLUMN IF NOT EXISTS {col} {col_type}")
                except Exception:
                    pass
        except Exception:
            return
        finally:
            if con:
                con.close()

    def _ensure_universe(self):
        con = None
        try:
            con = self._db_write()
            count = con.execute("SELECT COUNT(*) FROM universe").fetchone()[0]
            if count == 0:
                self._load_universe()
                return

            # Auto-repair: if nifty100 is all NULL, repopulate from source
            try:
                has_n100 = con.execute(
                    "SELECT COUNT(*) FROM universe WHERE nifty100 IS NOT NULL"
                ).fetchone()[0]
            except Exception:
                has_n100 = 0
            if has_n100 == 0 and count > 0:
                self._load_universe()
                return

            # Auto-repair: if sector is all NULL/empty, repopulate
            try:
                has_sector = con.execute(
                    "SELECT COUNT(*) FROM universe WHERE sector IS NOT NULL AND sector != ''"
                ).fetchone()[0]
            except Exception:
                has_sector = 0
            if has_sector == 0 and count > 0:
                self._load_universe()
                return
        except Exception:
            return
        finally:
            if con:
                con.close()

    def _load_universe(self):
        try:
            uni_path = self.config.get("universes", {}).get("nifty200", "")
            if not uni_path:
                return
            if not os.path.isabs(uni_path):
                uni_path = os.path.normpath(os.path.join(BASE, uni_path))
            if not os.path.exists(uni_path):
                return
            uni_dir = os.path.dirname(uni_path)
            sys.path.insert(0, uni_dir)
            mod_name = os.path.basename(uni_path).replace(".py", "")
            mod = importlib.import_module(mod_name)

            con = self._db_write()
            nifty50_set = set()
            nifty100_set = set()
            sector_map = getattr(mod, "SECTOR_MAP", None) or {}

            # Support both naming conventions
            n50_data = getattr(mod, "NIFTY50_DICT", None) or getattr(mod, "NIFTY50_SYMBOLS", None) or []
            n200_data = getattr(mod, "NIFTY200_DICT", None) or getattr(mod, "SYMBOLS_DATA", None) or {}
            n50_list = getattr(mod, "NIFTY50", None) or getattr(mod, "NIFTY50_SYMBOLS", None) or []
            n100_data = getattr(mod, "NIFTY100_SYMBOLS", None) or set()

            # Build nifty50 set
            if isinstance(n50_data, list):
                for entry in n50_data:
                    if isinstance(entry, dict):
                        sym = entry.get("Symbol", entry.get("symbol", "")).replace(".NS", "")
                    else:
                        sym = str(entry).replace(".NS", "")
                    if sym:
                        nifty50_set.add(sym)
            elif isinstance(n50_data, set):
                nifty50_set = {s.replace(".NS", "") for s in n50_data}

            for item in n50_list:
                if isinstance(item, str):
                    nifty50_set.add(item.replace(".NS", ""))

            # Build nifty100 set
            if isinstance(n100_data, set):
                nifty100_set = n100_data
            nifty100_set.update(nifty50_set)

            # Process nifty200 data
            if isinstance(n200_data, dict):
                entries = [{"Symbol": k, **v} for k, v in n200_data.items()]
            elif isinstance(n200_data, list):
                entries = n200_data
            else:
                n200_plain = getattr(mod, "NIFTY200", None) or getattr(mod, "NIFTY200_SYMBOLS", None) or []
                entries = [{"Symbol": s} for s in n200_plain if isinstance(s, str)]

            for entry in entries:
                if isinstance(entry, dict):
                    sym = entry.get("Symbol", entry.get("symbol", "")).replace(".NS", "")
                    lot = entry.get("LotSize", entry.get("lot", entry.get("lot_size", 0)))
                    tick = entry.get("TickSize", entry.get("tick", entry.get("tick_size", 0.1)))
                else:
                    sym = str(entry).replace(".NS", "")
                    lot = 0
                    tick = 0.1
                if not sym:
                    continue
                is_n50 = sym in nifty50_set
                is_n100 = sym in nifty100_set
                sector = sector_map.get(sym, "")
                con.execute("""
                    INSERT OR REPLACE INTO universe
                        (symbol, lot_size, tick_size, nifty50, nifty100, nifty200,
                         margin_lt_200k, fno, sector, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (sym, lot, tick, is_n50, is_n100, True, False, True, sector, "nifty200"))
        except Exception as e:
            import traceback
            traceback.print_exc()
        finally:
            if con:
                con.close()

    # ── Universe ──────────────────────────────────────────────

    def get_universe(self, name: str) -> List[str]:
        con = self._db_read()
        col_map = {
            "nifty50": "nifty50", "nifty100": "nifty100",
            "nifty200": "nifty200",
            "margin_lt_200k": "margin_lt_200k", "fno": "fno",
        }
        col = col_map.get(name, "nifty200")
        rows = con.execute(f"SELECT symbol FROM universe WHERE {col} = true").fetchall()
        con.close()
        return [r[0] + ".NS" for r in rows]

    def get_all_stocks(self) -> list:
        """Return all stocks with full metadata from universe table."""
        # Schema migration (one-time, write connection)
        try:
            con = self._db_write()
            for col, col_type in [("nifty100", "BOOLEAN"), ("sector", "VARCHAR")]:
                try:
                    con.execute(f"ALTER TABLE universe ADD COLUMN IF NOT EXISTS {col} {col_type}")
                except Exception:
                    pass
            con.close()
        except Exception:
            pass
        # Read (read-only for concurrent access)
        con = self._db_read()
        cols = ["symbol", "lot_size", "tick_size", "nifty50", "nifty100",
                "nifty200", "margin_lt_200k", "fno", "sector"]
        rows = con.execute(
            f"SELECT {', '.join(cols)} FROM universe ORDER BY symbol"
        ).fetchall()
        con.close()
        return [dict(zip(cols, row)) for row in rows]

    def get_symbols_data(self):
        """Return {SYMBOL: {lot: int, tick: float}} — matches common/universes.py SYMBOLS_DATA format."""
        data = {}
        for s in self.get_all_stocks():
            data[s["symbol"]] = {"lot": s["lot_size"], "tick": s["tick_size"]}
        return data

    def get_lot_size(self, symbol):
        """Return lot size for a symbol, or None if not found."""
        clean = symbol.replace(".NS", "").replace(".BO", "")
        s = self.get_stock(clean)
        return s["lot_size"] if s else None

    def get_tick_size(self, symbol):
        """Return tick size for a symbol, or 0.05 default."""
        clean = symbol.replace(".NS", "").replace(".BO", "")
        s = self.get_stock(clean)
        return s["tick_size"] if s else 0.05

    def get_universe_map(self):
        """Return {name: set_of_symbols_without_ns} matching UNIVERSE_MAP format."""
        stocks = self.get_all_stocks()
        nifty50 = set()
        nifty100 = set()
        nifty200 = set()
        fno = set()
        margin = set()
        for s in stocks:
            sym = s["symbol"]
            if s["nifty50"]: nifty50.add(sym)
            if s["nifty100"]: nifty100.add(sym)
            if s["nifty200"]: nifty200.add(sym)
            if s["fno"]: fno.add(sym)
            if s["margin_lt_200k"]: margin.add(sym)
        full_fo = nifty200 | fno
        return {
            "Nifty 50": nifty50,
            "Nifty 100": nifty100,
            "Nifty 200": full_fo,
            "Full F&O": full_fo,
            "Margin < 200K": margin,
        }

    def get_universe_names(self):
        """Return list of universe name strings."""
        return list(self.get_universe_map().keys())

    def get_nifty200_symbols(self):
        """Return list of .NS-suffixed ticker strings (NIFTY200 format)."""
        return [s["symbol"] + ".NS" for s in self.get_all_stocks() if s["nifty200"]]

    def get_nifty50_symbols(self):
        """Return set of Nifty 50 symbols without .NS."""
        return {s["symbol"] for s in self.get_all_stocks() if s["nifty50"]}

    def get_nifty100_symbols(self):
        """Return set of Nifty 100 symbols without .NS."""
        return {s["symbol"] for s in self.get_all_stocks() if s["nifty100"]}

    def add_stock(self, symbol: str, lot_size: int = 0, tick_size: float = 0.1,
                  nifty50: bool = False, nifty100: bool = False, nifty200: bool = False,
                  margin_lt_200k: bool = False, fno: bool = False, sector: str = ""):
        con = self._db_write()
        con.execute("""
            INSERT OR REPLACE INTO universe
                (symbol, lot_size, tick_size, nifty50, nifty100, nifty200,
                 margin_lt_200k, fno, sector, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual')
        """, (symbol.upper(), lot_size, tick_size, nifty50, nifty100, nifty200,
              margin_lt_200k, fno, sector))
        con.close()

    def update_stock(self, symbol: str, **kwargs):
        sets = []
        vals = []
        allowed = ["lot_size", "tick_size", "nifty50", "nifty100", "nifty200",
                   "margin_lt_200k", "fno", "sector"]
        for k, v in kwargs.items():
            if k in allowed:
                sets.append(f"{k} = ?")
                vals.append(v)
        if not sets:
            return
        vals.append(symbol.upper())
        con = self._db_write()
        con.execute(f"UPDATE universe SET {', '.join(sets)} WHERE symbol = ?", vals)
        con.close()

    def delete_stock(self, symbol: str):
        con = self._db_write()
        con.execute("DELETE FROM universe WHERE symbol = ?", (symbol.upper(),))
        con.close()

    def get_stock(self, symbol: str) -> dict:
        con = self._db_read()
        row = con.execute(
            "SELECT symbol, lot_size, tick_size, nifty50, nifty100, nifty200, "
            "margin_lt_200k, fno, sector FROM universe WHERE symbol = ?",
            (symbol.upper(),)
        ).fetchone()
        con.close()
        if row:
            return dict(zip(
                ["symbol", "lot_size", "tick_size", "nifty50", "nifty100",
                 "nifty200", "margin_lt_200k", "fno", "sector"], row
            ))
        return None

    def rebuild_universe(self):
        """Drop and rebuild universe from common/universes.py."""
        con = self._db_write()
        con.execute("DELETE FROM universe")
        con.close()
        self._ensure_universe()

    # ── Reading (cache-first, fallback to provider) ──────────

    def _table_for_timeframe(self, timeframe: str) -> str:
        return {
            "daily": "daily_bars", "1d": "daily_bars",
            "hourly": "hourly_bars", "1h": "hourly_bars",
            "5m": "bars_5min", "15m": "bars_15min",
            "1m": "bars_1min",
        }.get(timeframe, "daily_bars")

    def _read_from_db(self, table: str, ticker: str, start: str,
                      end: str, tz_convert: str = None) -> Optional[pd.DataFrame]:
        col = "date" if table == "daily_bars" else "datetime_ist"
        for attempt in range(10):
            con = self._db_read()
            try:
                df = con.execute(f"""
                    SELECT * FROM "{table}"
                    WHERE ticker = ? AND "{col}" >= ? AND "{col}" <= ?
                    ORDER BY "{col}"
                """, (ticker, start, end)).df()
                if df.empty:
                    return None
                if tz_convert and col == "datetime_ist":
                    df[col] = pd.to_datetime(df[col])
                    if df[col].dt.tz is None:
                        df[col] = df[col].dt.tz_localize(tz_convert)
                return df
            except Exception as e:
                msg = str(e).lower()
                if attempt < 9 and ("lock" in msg or "cannot open" in msg or "used by another process" in msg):
                    time.sleep(0.3 * (attempt + 1))
                    continue
                return None
            finally:
                try:
                    con.close()
                except Exception:
                    pass
        return None

    def get_daily(self, symbol: str, start: str, end: str) -> Optional[pd.DataFrame]:
        ticker = symbol if not symbol.endswith((".NS", ".BO")) else symbol.replace(".NS", "").replace(".BO", "")
        df = self._read_from_db("daily_bars", ticker, start, end)
        if df is not None and not df.empty:
            return df
        return self._fetch_daily(ticker, start, end)

    def get_bulk_daily(self, symbols: List[str], start: str, end: str) -> Dict[str, pd.DataFrame]:
        result = {}
        missing = []
        for sym in symbols:
            ticker = sym.replace(".NS", "").replace(".BO", "")
            df = self._read_from_db("daily_bars", ticker, start, end)
            if df is not None and not df.empty:
                result[ticker] = df
            else:
                missing.append(ticker)
        if missing:
            fetched = self._fetch_bulk_daily(missing, start, end)
            result.update(fetched)
        return result

    def get_intraday(self, symbol: str, date_str: str, interval: str = "5m") -> Optional[pd.DataFrame]:
        ticker = symbol.replace(".NS", "").replace(".BO", "")
        table = self._table_for_timeframe(interval)
        date_dt = pd.Timestamp(date_str)
        start = date_dt.strftime("%Y-%m-%d")
        end = (date_dt + timedelta(days=1)).strftime("%Y-%m-%d")
        df = self._read_from_db(table, ticker, start, end, tz_convert="Asia/Kolkata")
        if df is not None and not df.empty:
            # Gap detection: re-sync if latest candle is too far behind
            if interval in ("1m", "5m", "15m", "hourly"):
                gap_thresholds = {"1m": 5, "5m": 3, "15m": 2, "hourly": 1}
                max_gap = gap_thresholds.get(interval, 5)
                ts_col = 'datetime_ist' if 'datetime_ist' in df.columns else 'date'
                last_ts = pd.Timestamp(df[ts_col].iloc[-1])
                now_ts = pd.Timestamp.now(tz=last_ts.tz if last_ts.tz else None)
                gap = (now_ts - last_ts).total_seconds() / 60
                expected_interval = {"1m": 1, "5m": 5, "15m": 15, "hourly": 60}.get(interval, 1)
                if gap > max_gap * expected_interval:
                    return self._fetch_intraday(ticker, date_str, interval)
            return df
        # Fallback for 15m: aggregate from 5m data
        if interval == "15m":
            df5 = self.get_intraday(symbol, date_str, "5m")
            if df5 is not None and not df5.empty:
                agg = df5.set_index("datetime_ist").resample("15min").agg({
                    "open": "first", "high": "max", "low": "min",
                    "close": "last", "volume": "sum"
                }).dropna().reset_index()
                return agg
        return self._fetch_intraday(ticker, date_str, interval)

    def get_bulk_intraday(self, symbols: List[str], date_str: str,
                          interval: str = "5m") -> Dict[str, pd.DataFrame]:
        result = {}
        table = self._table_for_timeframe(interval)
        missing = []
        for sym in symbols:
            ticker = sym.replace(".NS", "").replace(".BO", "")
            date_dt = pd.Timestamp(date_str)
            start = date_dt.strftime("%Y-%m-%d")
            end = (date_dt + timedelta(days=1)).strftime("%Y-%m-%d")
            df = self._read_from_db(table, ticker, start, end, tz_convert="Asia/Kolkata")
            if df is not None and not df.empty:
                result[ticker] = df
            else:
                missing.append(ticker)
        # Fallback for 15m: aggregate from 5m for missing symbols
        if interval == "15m" and missing:
            from_5m = self.get_bulk_intraday(missing, date_str, "5m")
            for ticker, df5 in from_5m.items():
                if df5 is not None and not df5.empty:
                    agg = df5.set_index("datetime_ist").resample("15min").agg({
                        "open": "first", "high": "max", "low": "min",
                        "close": "last", "volume": "sum"
                    }).dropna().reset_index()
                    result[ticker] = agg
                    missing.remove(ticker)
        if missing:
            fetched = self._fetch_bulk_intraday(missing, date_str, interval)
            result.update(fetched)
        return result

    def get_prev_close(self, symbol: str, date_str: str) -> Optional[float]:
        df = self.get_daily(symbol, (pd.Timestamp(date_str) - timedelta(days=30)).strftime("%Y-%m-%d"),
                            date_str)
        if df is None or df.empty:
            return None
        ref = pd.Timestamp(date_str)
        prev = df[pd.to_datetime(df["date"]) < ref]
        prev = prev.dropna(subset=["close"])
        if prev.empty:
            return None
        return float(prev["close"].iloc[-1])

    def get_last_price(self, symbol: str) -> Optional[float]:
        return self.provider.get_last_price(symbol)

    # ── Fetch from provider + store ──────────────────────────

    def _fetch_daily(self, ticker: str, start: str, end: str) -> Optional[pd.DataFrame]:
        ticker_ns = ticker + ".NS" if not ticker.endswith((".NS", ".BO")) and not ticker.startswith("^") else ticker
        df = self.provider.download_daily([ticker_ns], start, end)
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            try:
                sdf = df.xs(ticker_ns, level='Ticker', axis=1).copy()
            except (KeyError, ValueError):
                return None
        else:
            sdf = df.copy()
        if sdf.empty:
            return None
        sdf = sdf.reset_index()
        sdf.columns = [c.lower() if isinstance(c, str) else str(c).lower() for c in sdf.columns]
        required = {'date': 'date', 'open': 'open', 'high': 'high', 'low': 'low', 'close': 'close', 'volume': 'volume'}
        col_map = {'date': ['date', 'timestamp', 'time', 'datetime'],
                    'open': ['open', 'opening'],
                    'high': ['high'],
                    'low': ['low'],
                    'close': ['close', 'closing', 'adj close', 'adjusted close'],
                    'volume': ['volume', 'vol']}
        for k, v in required.items():
            if k in sdf.columns:
                continue
            # Try exact match on known aliases
            found = False
            for col in sdf.columns:
                cl = str(col).lower().strip()
                if cl in col_map.get(k, [k]):
                    sdf = sdf.rename(columns={col: v})
                    found = True
                    break
        sdf["ticker"] = ticker
        cols = ["ticker", "date", "open", "high", "low", "close", "volume"]
        sdf = sdf[[c for c in cols if c in sdf.columns]]
        sdf["date"] = pd.to_datetime(sdf["date"]).dt.date
        try:
            self._write_db("daily_bars", sdf)
        except Exception:
            pass
        try:
            self._update_sync_log({ticker}, "daily", end, bar_count=len(sdf))
        except Exception:
            pass
        return sdf

    def _fetch_bulk_daily(self, tickers: List[str], start: str, end: str) -> Dict[str, pd.DataFrame]:
        ns_tickers = [t + ".NS" if not t.endswith((".NS", ".BO")) and not t.startswith("^") else t for t in tickers]
        df = self.provider.download_daily(ns_tickers, start, end)
        if df is None or df.empty:
            return {}
        result = {}
        stored = set()
        for ticker in tickers:
            ns_t = ticker + ".NS" if not ticker.endswith((".NS", ".BO")) else ticker
            try:
                if isinstance(df.columns, pd.MultiIndex):
                    sdf = df.xs(ns_t, level='Ticker', axis=1).copy()
                else:
                    sdf = df.copy()
                if sdf.empty or len(sdf) < 2:
                    continue
                sdf = sdf.reset_index()
                sdf.columns = [c.lower() if isinstance(c, str) else str(c).lower() for c in sdf.columns]
                sdf["ticker"] = ticker
                cols = ["ticker", "date", "open", "high", "low", "close", "volume"]
                col_map = {}
                for c in cols:
                    for col in sdf.columns:
                        if c in str(col).lower():
                            col_map[col] = c
                            break
                if col_map:
                    sdf = sdf.rename(columns=col_map)
                sdf["date"] = pd.to_datetime(sdf["date"]).dt.date
                store = sdf[[c for c in cols if c in sdf.columns]]
                self._write_db("daily_bars", store)
                result[ticker] = store
                stored.add(ticker)
            except Exception:
                continue
        if stored:
            try:
                self._update_sync_log(stored, "daily", end)
            except Exception:
                pass
        return result

    def _fetch_intraday(self, ticker: str, date_str: str, interval: str) -> Optional[pd.DataFrame]:
        ticker_ns = ticker + ".NS" if not ticker.endswith((".NS", ".BO")) and not ticker.startswith("^") else ticker
        dt = pd.Timestamp(date_str)
        start = dt.strftime("%Y-%m-%d")
        end = (dt + timedelta(days=1)).strftime("%Y-%m-%d")
        df = self.provider.download_intraday([ticker_ns], start, end, interval)
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            try:
                sdf = df.xs(ticker_ns, level='Ticker', axis=1).copy()
            except (KeyError, ValueError):
                return None
        else:
            sdf = df.copy()
        if sdf.empty:
            return None
        sdf = sdf.reset_index()
        sdf.columns = [c.lower() if isinstance(c, str) else str(c).lower() for c in sdf.columns]
        sdf["ticker"] = ticker
        for ts_col in ['datetime', 'date', 'timestamp', 'index']:
            if ts_col in sdf.columns:
                sdf = sdf.rename(columns={ts_col: "datetime_ist"})
                break
        cols = ["ticker", "datetime_ist", "open", "high", "low", "close", "volume"]
        sdf = sdf[[c for c in cols if c in sdf.columns]]
        table = self._table_for_timeframe(interval)
        try:
            self._write_db(table, sdf)
        except Exception:
            pass  # best-effort cache, data still returned
        return sdf

    def _fetch_bulk_intraday(self, tickers: List[str], date_str: str,
                             interval: str) -> Dict[str, pd.DataFrame]:
        ns_tickers = [t + ".NS" if not t.endswith((".NS", ".BO")) and not t.startswith("^") else t for t in tickers]
        dt = pd.Timestamp(date_str)
        start = dt.strftime("%Y-%m-%d")
        end = (dt + timedelta(days=1)).strftime("%Y-%m-%d")
        df = self.provider.download_intraday(ns_tickers, start, end, interval)
        if df is None or df.empty:
            return {}
        table = self._table_for_timeframe(interval)
        result = {}
        for ticker in tickers:
            ns_t = ticker + ".NS" if not ticker.endswith((".NS", ".BO")) else ticker
            try:
                if isinstance(df.columns, pd.MultiIndex):
                    sdf = df.xs(ns_t, level='Ticker', axis=1).copy()
                else:
                    sdf = df.copy()
                if sdf.empty:
                    continue
                sdf = sdf.reset_index()
                sdf.columns = [c.lower() if isinstance(c, str) else str(c).lower() for c in sdf.columns]
                sdf["ticker"] = ticker
                for ts_col in ['datetime', 'date', 'timestamp', 'index']:
                    if ts_col in sdf.columns:
                        sdf = sdf.rename(columns={ts_col: "datetime_ist"})
                        break
                cols = ["ticker", "datetime_ist", "open", "high", "low", "close", "volume"]
                if all(c in sdf.columns for c in ["ticker", "open", "high", "low", "close", "volume"]):
                    self._write_db(table, sdf[[c for c in cols if c in sdf.columns]])
                    result[ticker] = sdf
            except Exception:
                continue
        return result

    # ── Write to DB ──────────────────────────────────────────

    def _write_db(self, table: str, df: pd.DataFrame):
        if df is None or df.empty:
            return
        try:
            con = self._db_write()
        except Exception:
            return
        try:
            pk_col = "date" if table == "daily_bars" else "datetime_ist"
            con.register("_tmp", df)
            cols = ", ".join(df.columns)
            con.execute(f"""
                INSERT INTO "{table}" ({cols})
                SELECT {cols} FROM _tmp
                ON CONFLICT (ticker, {pk_col}) DO UPDATE SET
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    volume = EXCLUDED.volume
            """)
        except Exception:
            pass
        finally:
            con.close()

    # ── Sync ─────────────────────────────────────────────────

    def _sync_timeframe(self, tickers: List[str], timeframe: str):
        table = self._table_for_timeframe(timeframe)
        con = self._db_read()
        existing = set(r[0] for r in con.execute(
            f"SELECT DISTINCT ticker FROM sync_log WHERE timeframe = ?", (timeframe,)).fetchall())
        con.close()

        for ticker in tickers:
            clean = ticker.replace(".NS", "").replace(".BO", "")
            if clean in existing:
                continue
            try:
                con = self._db_write()
                try:
                    con.execute("""
                        INSERT OR IGNORE INTO sync_log
                            (ticker, timeframe, last_sync, bar_count, first_date, last_date, status)
                        VALUES (?, ?, NULL, 0, NULL, NULL, 'pending')
                    """, (clean, timeframe))
                finally:
                    con.close()
            except Exception:
                pass

    def _delta_start(self, timeframe: str, max_lookback: int) -> str:
        """Return the start date for delta sync.
        Goes back 3 days from last sync for candle correction, but never
        requests older than max_lookback (Yahoo's API retention limit).
        Falls back to full max_lookback if no prior sync.
        Always capped to not exceed today's date (prevents future-date corruption)."""
        today = datetime.now()
        yahoo_min = today - timedelta(days=max_lookback)
        con = self._db_read()
        row = con.execute(
            "SELECT MAX(last_date) FROM sync_log WHERE timeframe = ? AND last_date IS NOT NULL",
            (timeframe,)
        ).fetchone()
        con.close()
        if row and row[0]:
            last = pd.Timestamp(row[0]).to_pydatetime()
            # Cap last to today to prevent future-date issues
            last = min(last, today)
            candidate = last - timedelta(days=3)
            start = max(candidate, yahoo_min)
            return start.strftime("%Y-%m-%d")
        return yahoo_min.strftime("%Y-%m-%d")

    def sync_daily(self, date_str: str = None) -> dict:
        if date_str is None:
            date_str = datetime.now().strftime("%Y-%m-%d")
        tickers = self.get_universe("nifty200")
        start = self._delta_start("daily", 1095)
        end = (pd.Timestamp(date_str) + timedelta(days=1)).strftime("%Y-%m-%d")
        if start > end:
            start = end
        return self._bulk_fetch_daily(tickers, start, end)

    def sync_hourly(self, date_str: str = None) -> dict:
        if date_str is None:
            date_str = datetime.now().strftime("%Y-%m-%d")
        tickers = self.get_universe("nifty200")
        start = self._delta_start("hourly", 60)
        end = (pd.Timestamp(date_str) + timedelta(days=1)).strftime("%Y-%m-%d")
        if start > end:
            start = end
        return self._bulk_fetch_intraday(tickers, "hourly", start, end)

    def sync_5min(self, date_str: str = None) -> dict:
        if date_str is None:
            date_str = datetime.now().strftime("%Y-%m-%d")
        tickers = self.get_universe("nifty200")
        start = self._delta_start("5m", 60)
        end = (pd.Timestamp(date_str) + timedelta(days=1)).strftime("%Y-%m-%d")
        if start > end:
            start = end
        return self._bulk_fetch_intraday(tickers, "5m", start, end)

    def sync_1min(self, date_str: str = None) -> dict:
        if date_str is None:
            date_str = datetime.now().strftime("%Y-%m-%d")
        tickers = self.get_universe("nifty200")
        start = self._delta_start("1m", 30)
        end = (pd.Timestamp(date_str) + timedelta(days=1)).strftime("%Y-%m-%d")
        if start > end:
            start = end
        return self._bulk_fetch_intraday(tickers, "1m", start, end)

    def _bulk_fetch_daily(self, tickers: List[str], start: str, end: str) -> dict:
        if start > end:
            start, end = end, start
        ns_tickers = [t + ".NS" if not t.endswith((".NS", ".BO")) and not t.startswith("^") else t for t in tickers]
        count = 0
        stored = set()
        for i in range(0, len(ns_tickers), 30):
            batch = ns_tickers[i:i+30]
            try:
                df = self.provider.download_daily(batch, start, end)
                if df is not None and not df.empty:
                    self._store_daily_dataframe(df)
                    count += 1
                    if isinstance(df.columns, pd.MultiIndex) and 'Ticker' in df.columns.names:
                        stored.update(t.replace(".NS", "").replace(".BO", "")
                                      for t in df.columns.get_level_values('Ticker'))
            except Exception:
                pass
            if i + 30 < len(ns_tickers):
                time.sleep(2)
        self._update_sync_log(stored, "daily", end)
        if count == 0 and stored:
            print(f"  [WARN] _bulk_fetch_daily: {len(stored)} tickers had stored data but 0 batches fetched")
        return {"timeframe": "daily", "start": start, "end": end, "batches_fetched": count,
                "tickers": len(tickers)}

    def _fetch_delta_daily(self, tickers: List[str], start: str, end: str) -> dict:
        """Fetch daily data only for specific tickers in a narrow date range.
        Uses INSERT OR REPLACE to update existing cache rows without
        re-downloading the full universe."""
        ns_tickers = [t + ".NS" if not t.endswith((".NS", ".BO")) and not t.startswith("^") else t for t in tickers]
        stored = set()
        for i in range(0, len(ns_tickers), 30):
            batch = ns_tickers[i:i+30]
            try:
                df = self.provider.download_daily(batch, start, end)
                if df is not None and not df.empty:
                    self._store_daily_dataframe(df)
                    if isinstance(df.columns, pd.MultiIndex) and 'Ticker' in df.columns.names:
                        stored.update(t.replace(".NS", "").replace(".BO", "")
                                      for t in df.columns.get_level_values('Ticker'))
            except Exception:
                pass
            if i + 30 < len(ns_tickers):
                time.sleep(2)
        if stored:
            self._update_sync_log(stored, "daily", end)
        return {"timeframe": "daily", "start": start, "end": end, "tickers": len(tickers)}

    def _bulk_fetch_intraday(self, tickers: List[str], interval: str,
                             start: str, end: str) -> dict:
        if start > end:
            start, end = end, start
        ns_tickers = [t + ".NS" if not t.endswith((".NS", ".BO")) and not t.startswith("^") else t for t in tickers]
        table = self._table_for_timeframe(interval)
        count = 0
        stored = set()
        for i in range(0, len(ns_tickers), 30):
            batch = ns_tickers[i:i+30]
            try:
                df = self.provider.download_intraday(batch, start, end, interval)
                if df is not None and not df.empty:
                    self._store_intraday_dataframe(df, table)
                    count += 1
                    if isinstance(df.columns, pd.MultiIndex) and 'Ticker' in df.columns.names:
                        stored.update(t.replace(".NS", "").replace(".BO", "")
                                      for t in df.columns.get_level_values('Ticker'))
            except Exception:
                pass
            if i + 30 < len(ns_tickers):
                time.sleep(2)
        self._update_sync_log(stored, interval, end)
        return {"timeframe": interval, "start": start, "end": end, "batches_fetched": count,
                "tickers": len(tickers)}

    def _update_sync_log(self, tickers: set, timeframe: str, date_str: str, bar_count: int = 0):
        if not tickers:
            return
        try:
            con = self._db_write()
        except Exception:
            return
        now = datetime.now()
        dt = pd.Timestamp(date_str).date()
        for ticker in tickers:
            con.execute("""
                INSERT INTO sync_log (ticker, timeframe, last_sync, bar_count, first_date, last_date, status)
                VALUES (?, ?, ?, ?, NULL, ?, 'ok')
                ON CONFLICT (ticker, timeframe) DO UPDATE SET
                    last_sync = EXCLUDED.last_sync,
                    bar_count = EXCLUDED.bar_count,
                    last_date = EXCLUDED.last_date,
                    status = 'ok'
            """, (ticker, timeframe, now, bar_count, dt))
        con.close()

    def _store_daily_dataframe(self, df: pd.DataFrame):
        if df is None or df.empty:
            return
        try:
            con = self._db_write()
        except Exception:
            return
        ticker_level = 'Ticker' if isinstance(df.columns, pd.MultiIndex) and 'Ticker' in df.columns.names else None
        if ticker_level is None:
            con.close()
            return
        tickers_in = set(df.columns.get_level_values(ticker_level))
        for t in tickers_in:
            try:
                sdf = df.xs(t, level=ticker_level, axis=1).copy()
                if sdf.empty:
                    continue
                sdf = sdf.reset_index()
                sdf.columns = [c.lower() if isinstance(c, str) else str(c).lower() for c in sdf.columns]
                sdf["ticker"] = t.replace(".NS", "").replace(".BO", "")
                if "date" not in sdf.columns:
                    # Find date-like column
                    for c in sdf.columns:
                        if "date" in str(c).lower() or "index" in str(c).lower():
                            sdf = sdf.rename(columns={c: "date"})
                            break
                cols = ["ticker", "date", "open", "high", "low", "close", "volume"]
                present = [c for c in cols if c in sdf.columns]
                if len(present) >= 5:
                    sdf["date"] = pd.to_datetime(sdf["date"]).dt.date
                    con.register("_daily_tmp", sdf[present])
                    update_set = ", ".join(
                        f"{c} = EXCLUDED.{c}" for c in present if c not in ("ticker", "date")
                    )
                    con.execute(f"""
                        INSERT INTO daily_bars ({', '.join(present)})
                        SELECT {', '.join(present)} FROM _daily_tmp
                        ON CONFLICT (ticker, date) DO UPDATE SET {update_set}
                    """)
            except Exception:
                continue
        con.close()

    def _store_intraday_dataframe(self, df: pd.DataFrame, table: str):
        if df is None or df.empty:
            return
        if not isinstance(df.columns, pd.MultiIndex):
            print(f"  [WARN] _store_intraday_dataframe: expected MultiIndex columns, got {type(df.columns)}")
            return
        try:
            con = self._db_write()
        except Exception:
            return
        ticker_level = 'Ticker' if 'Ticker' in df.columns.names else None
        if ticker_level is None:
            con.close()
            print(f"  [WARN] _store_intraday_dataframe: 'Ticker' not in column level names: {df.columns.names}")
            return
        tickers_in = set(df.columns.get_level_values(ticker_level))
        for t in tickers_in:
            try:
                sdf = df.xs(t, level=ticker_level, axis=1).copy()
                if sdf.empty:
                    continue
                sdf = sdf.reset_index()
                sdf.columns = [c.lower() if isinstance(c, str) else str(c).lower() for c in sdf.columns]
                sdf["ticker"] = t.replace(".NS", "").replace(".BO", "")
                for ts_col in ['datetime', 'date', 'timestamp', 'index']:
                    if ts_col in sdf.columns:
                        sdf = sdf.rename(columns={ts_col: "datetime_ist"})
                        break
                cols = ["ticker", "datetime_ist", "open", "high", "low", "close", "volume"]
                present = [c for c in cols if c in sdf.columns]
                if len(present) >= 5:
                    con.register("_store_tmp", sdf[present])
                    pk = "ticker, datetime_ist"
                    col_list = ", ".join(present)
                    con.execute(f"""
                        INSERT INTO "{table}" ({col_list})
                        SELECT {col_list} FROM _store_tmp
                        ON CONFLICT (ticker, datetime_ist) DO UPDATE SET
                            open = EXCLUDED.open,
                            high = EXCLUDED.high,
                            low = EXCLUDED.low,
                            close = EXCLUDED.close,
                            volume = EXCLUDED.volume
                    """)
            except Exception:
                continue
        con.close()

    # ── Status ───────────────────────────────────────────────

    def is_stale(self, symbol: str, timeframe: str) -> bool:
        clean = symbol.replace(".NS", "").replace(".BO", "")
        con = self._db_read()
        row = con.execute(
            "SELECT last_sync, last_date FROM sync_log WHERE ticker = ? AND timeframe = ?",
            (clean, timeframe)).fetchone()
        con.close()
        if row is None or row[0] is None:
            return True
        retention = _parse_retention(self.config.get("retention", {}).get(timeframe, "7 days"))
        if datetime.now() - pd.Timestamp(row[0]).to_pydatetime() > retention / 2:
            return True
        if timeframe == "daily" and row[1] is not None:
            # Last complete trading day (skip weekends)
            last_trading = datetime.now() - timedelta(days=1)
            while last_trading.weekday() >= 5:
                last_trading -= timedelta(days=1)
            if row[1] < last_trading.date():
                return True
        # For intraday timeframes: stale if last sync was not today
        if timeframe in ("1m", "5m", "hourly", "15m") and row[0] is not None:
            if pd.Timestamp(row[0]).date() < datetime.now().date():
                return True
        return False

    def get_cache_stats(self) -> dict:
        con = self._db_read()
        tables = ["daily_bars", "hourly_bars", "bars_15min", "bars_5min", "bars_1min"]
        stats = {}
        total = 0
        for t in tables:
            cnt = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            stats[t] = cnt
            total += cnt
        # File size
        file_size_mb = round(os.path.getsize(self.db_path) / (1024 * 1024), 2) if os.path.exists(self.db_path) else 0
        con.close()
        return {
            "total_bars": total,
            "by_table": stats,
            "file_size_mb": file_size_mb,
            "db_path": self.db_path,
        }

    def clear_all(self):
        """Drop all data tables and sync_log. Universe is preserved."""
        con = self._db_write()
        tables = ["daily_bars", "hourly_bars", "bars_15min", "bars_5min", "bars_1min", "sync_log"]
        for t in tables:
            con.execute(f'DROP TABLE IF EXISTS "{t}"')
        con.close()
        self._ensure_db()

    def update_bar_counts(self):
        """Recalculate bar counts and backfill last_date from actual table data."""
        con = self._db_write()
        for tf, table, date_col in [
            ("daily", "daily_bars", "date"),
            ("hourly", "hourly_bars", "datetime_ist"),
            ("15m", "bars_15min", "datetime_ist"),
            ("5m", "bars_5min", "datetime_ist"),
            ("1m", "bars_1min", "datetime_ist"),
        ]:
            counts = con.execute(
                f"SELECT ticker, COUNT(1), MAX(\"{date_col}\") FROM \"{table}\" GROUP BY ticker"
            ).fetchall()
            for ticker, cnt, last_dt in counts:
                con.execute("""
                    INSERT INTO sync_log (ticker, timeframe, last_sync, bar_count, first_date, last_date, status)
                    VALUES (?, ?, CURRENT_TIMESTAMP, ?, CAST(? AS DATE), CAST(? AS DATE), 'ok')
                    ON CONFLICT (ticker, timeframe) DO UPDATE SET
                        bar_count = EXCLUDED.bar_count,
                        last_date = COALESCE(EXCLUDED.last_date, sync_log.last_date),
                        status = 'ok'
                """, (ticker, tf, cnt, last_dt, last_dt))
        con.close()

    def clear_timeframe(self, timeframe: str):
        """Clear data for a specific timeframe."""
        table = self._table_for_timeframe(timeframe)
        con = self._db_write()
        con.execute(f'DELETE FROM "{table}"')
        con.execute(f'DELETE FROM sync_log WHERE timeframe = ?', (timeframe,))
        con.close()

    # ── Backtest-compatible format adapters ─────────────────

    def get_daily_for_backtest(self, symbols, start, end, min_bars=25):
        """Return {SYMBOL.NS: DataFrame} with datetime index + OHLCV columns
        matching the format backtest engines expect (capitalised from yfinance).
        Tickers with fewer than min_bars rows trigger a full re-fetch from provider."""
        clean = [s.replace('.NS', '').replace('.BO', '') for s in symbols]
        try:
            raw = self.get_bulk_daily(clean, start, end)
        except Exception as e:
            print(f"  [WARN] get_bulk_daily failed: {e}")
            return {}
        result = {}
        need_refetch = []
        end_dt = pd.Timestamp(end)
        # Don't use future date for recency check — cap at today
        ref_dt = min(end_dt, pd.Timestamp(datetime.now().date()))
        for sym, df in raw.items():
            if df is None or df.empty:
                continue
            if min_bars > 0 and len(df) < min_bars:
                need_refetch.append(sym)
                continue
            last = pd.Timestamp(df['date'].max()) if 'date' in df.columns else ref_dt
            if last < ref_dt - timedelta(days=7):
                need_refetch.append(sym)
                continue
            try:
                df = df.copy()
                if 'date' in df.columns:
                    df['date'] = pd.to_datetime(df['date'])
                    df = df.set_index('date')
                df.index.name = None
                rename = {}
                for col in df.columns:
                    low = col.lower()
                    if low == 'open': rename[col] = 'Open'
                    elif low == 'high': rename[col] = 'High'
                    elif low == 'low': rename[col] = 'Low'
                    elif low == 'close': rename[col] = 'Close'
                    elif low == 'volume': rename[col] = 'Volume'
                if rename:
                    df = df.rename(columns=rename)
                keep = [c for c in ['Open', 'High', 'Low', 'Close', 'Volume'] if c in df.columns]
                if len(keep) >= 4:
                    result[f"{sym}.NS"] = df[keep].dropna(subset=['Close'])
            except Exception as e:
                print(f"  [WARN] Skipping {sym}: format error ({e})")
        if need_refetch:
            print(f"  [DATA] {len(need_refetch)} symbols need refetch (insufficient bars), fetching from provider...")
            try:
                fetched = self._fetch_bulk_daily(need_refetch, start, end)
            except Exception as e:
                print(f"  [WARN] Refetch failed: {e}")
                fetched = {}
            for sym, df in fetched.items():
                if df is None or df.empty or len(df) < 2:
                    continue
                try:
                    df = df.copy()
                    if 'date' in df.columns:
                        df['date'] = pd.to_datetime(df['date'])
                        df = df.set_index('date')
                    df.index.name = None
                    rename = {}
                    for col in df.columns:
                        low = col.lower()
                        if low == 'open': rename[col] = 'Open'
                        elif low == 'high': rename[col] = 'High'
                        elif low == 'low': rename[col] = 'Low'
                        elif low == 'close': rename[col] = 'Close'
                        elif low == 'volume': rename[col] = 'Volume'
                    if rename:
                        df = df.rename(columns=rename)
                    keep = [c for c in ['Open', 'High', 'Low', 'Close', 'Volume'] if c in df.columns]
                    if len(keep) >= 4:
                        result[f"{sym}.NS"] = df[keep].dropna(subset=['Close'])
                except Exception as e:
                    print(f"  [WARN] Skipping refetched {sym}: format error ({e})")
        return result

    def get_intraday_for_backtest(self, symbol, date_str, interval="5m"):
        """Single-symbol intraday DataFrame with datetime index + Asia/Kolkata tz.
        Reads from cache only — no provider fallback (historical data may not be available from API).
        If cached data has a candle gap > threshold, triggers a provider sync first."""
        gap_thresholds = {"1m": 5, "5m": 3, "15m": 2, "hourly": 1}
        max_gap = gap_thresholds.get(interval, 5)
        try:
            ticker = symbol.replace(".NS", "").replace(".BO", "")
            table = self._table_for_timeframe(interval)
            date_dt = pd.Timestamp(date_str)
            start = date_dt.strftime("%Y-%m-%d")
            end = (date_dt + timedelta(days=1)).strftime("%Y-%m-%d")
            df = self._read_from_db(table, ticker, start, end, tz_convert="Asia/Kolkata")
            # Gap detection: only sync for today's date (backtest on historical
            # dates will always have a large gap — don't trigger wasteful Yahoo syncs)
            is_today = date_dt.date() == datetime.now().date()
            if is_today and df is not None and not df.empty and interval in gap_thresholds:
                ts_col = 'datetime_ist' if 'datetime_ist' in df.columns else 'date'
                last_ts = pd.Timestamp(df[ts_col].iloc[-1])
                now_ts = pd.Timestamp.now(tz=last_ts.tz if last_ts.tz else None)
                gap = (now_ts - last_ts).total_seconds() / 60
                expected_interval = {"1m": 1, "5m": 5, "15m": 15, "hourly": 60}.get(interval, 1)
                if gap > max_gap * expected_interval:
                    sync_methods = {"1m": self.sync_1min, "5m": self.sync_5min,
                                    "15m": self.sync_5min, "hourly": self.sync_hourly}
                    if interval in sync_methods:
                        try:
                            sync_methods[interval](date_str)
                        except Exception:
                            pass
                        df = self._read_from_db(table, ticker, start, end, tz_convert="Asia/Kolkata")
        except Exception as e:
            print(f"  [WARN] get_intraday({symbol}) failed: {e}")
            return None
        if df is None or df.empty:
            return None
        try:
            df = df.copy()
            ts_col = 'datetime_ist' if 'datetime_ist' in df.columns else 'date'
            if ts_col in df.columns:
                df[ts_col] = pd.to_datetime(df[ts_col])
                df = df.set_index(ts_col)
            df.index.name = None
            if df.index.tz is None:
                try:
                    df.index = df.index.tz_localize('Asia/Kolkata')
                except Exception:
                    pass
            rename = {}
            for col in df.columns:
                low = col.lower()
                if low == 'open': rename[col] = 'Open'
                elif low == 'high': rename[col] = 'High'
                elif low == 'low': rename[col] = 'Low'
                elif low == 'close': rename[col] = 'Close'
                elif low == 'volume': rename[col] = 'Volume'
            if rename:
                df = df.rename(columns=rename)
            keep = [c for c in ['Open', 'High', 'Low', 'Close', 'Volume'] if c in df.columns]
            return df[keep].dropna(subset=['Close']) if keep else df
        except Exception as e:
            print(f"  [WARN] get_intraday_for_backtest({symbol}) format error: {e}")
            return None

    def get_bulk_intraday_for_backtest(self, symbols: List[str], date_str: str,
                                        interval: str = "5m") -> Dict[str, pd.DataFrame]:
        """Load intraday for many symbols in one query. Returns {clean_symbol: DataFrame}.
        Faster than calling get_intraday_for_backtest per symbol."""
        clean = [s.replace(".NS", "").replace(".BO", "") for s in symbols]
        table = self._table_for_timeframe(interval)
        date_dt = pd.Timestamp(date_str)
        start = date_dt.strftime("%Y-%m-%d")
        end = (date_dt + timedelta(days=1)).strftime("%Y-%m-%d")
        col = "datetime_ist"
        try:
            con = self._db_read()
            raw = con.execute(f"""
                SELECT * FROM "{table}"
                WHERE "{col}" >= ? AND "{col}" < ?
                ORDER BY ticker, "{col}"
            """, (start, end)).df()
            con.close()
        except Exception:
            return {}
        if raw.empty:
            return {}
        raw[col] = pd.to_datetime(raw[col])
        if raw[col].dt.tz is None:
            raw[col] = raw[col].dt.tz_localize("Asia/Kolkata")
        result = {}
        for ticker in clean:
            sub = raw[raw['ticker'] == ticker].copy()
            if sub.empty:
                continue
            sub = sub.set_index(col)
            sub.index.name = None
            rename = {}
            for c in sub.columns:
                low = c.lower()
                if low == 'open': rename[c] = 'Open'
                elif low == 'high': rename[c] = 'High'
                elif low == 'low': rename[c] = 'Low'
                elif low == 'close': rename[c] = 'Close'
                elif low == 'volume': rename[c] = 'Volume'
            if rename:
                sub = sub.rename(columns=rename)
            keep = [c for c in ['Open', 'High', 'Low', 'Close', 'Volume'] if c in sub.columns]
            if keep:
                sub = sub[keep].dropna(subset=['Close'])
            if not sub.empty:
                result[ticker if ticker.endswith((".NS", ".BO")) else ticker] = sub
        return result

    def ensure_fresh(self, symbols, timeframe):
        """Sync a timeframe if any symbol is stale. Returns True if synced.
        Uses per-symbol delta sync for daily (only stale symbols, narrow range).
        For intraday timeframes syncs entire universe (legacy behaviour).
        Errors are caught and logged — never raises."""
        mapping = {'daily': 'daily', '1d': 'daily',
                   'hourly': 'hourly', '1h': 'hourly',
                   '5m': '5m', '15m': '5m', '1m': '1m'}
        tf = mapping.get(timeframe, timeframe)
        try:
            clean = [s.replace('.NS', '').replace('.BO', '') for s in symbols]
            stale = [s for s in clean if self.is_stale(s, tf)]
            if stale:
                if tf == "daily":
                    self._fetch_daily_for_stale(stale)
                else:
                    sync_methods = {'hourly': self.sync_hourly,
                                    '5m': self.sync_5min, '1m': self.sync_1min}
                    if tf in sync_methods:
                        sync_methods[tf]()
                return True
        except Exception as e:
            print(f"  [WARN] ensure_fresh({tf}) failed: {e} — continuing with cached data")
        return False

    def _fetch_daily_for_stale(self, stale: List[str]):
        """Fetch daily data for stale symbols, computing per-ticker start
        from sync_log so that a symbol missing Jan–May 2026 doesn't get
        skipped just because another symbol has recent data."""
        today = datetime.now()
        yahoo_min = today - timedelta(days=1095)
        end = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        con = self._db_read()
        placeholders = ", ".join("?" for _ in stale)
        rows = con.execute(
            f"SELECT ticker, last_date FROM sync_log WHERE timeframe='daily' AND ticker IN ({placeholders})",
            stale
        ).fetchall()
        con.close()
        ld_map = {r[0]: r[1] for r in rows}
        dates = [ld_map.get(s) for s in stale if ld_map.get(s) is not None]
        if dates:
            earliest = min(dates)
            candidate = pd.Timestamp(earliest).to_pydatetime() - timedelta(days=3)
            start = max(candidate, yahoo_min).strftime("%Y-%m-%d")
        else:
            start = yahoo_min.strftime("%Y-%m-%d")
        self._fetch_delta_daily(stale, start, end)

    def get_bulk_multiindex(self, symbols, start, end, interval="1d"):
        """Returns a MultiIndex DataFrame matching yf.download(group_by='ticker')
        output format. Compatible with PairTrading backtest engines."""
        clean = [s.replace('.NS', '').replace('.BO', '') for s in symbols]
        tf_map = {'1d': 'daily', '1h': 'hourly', '5m': '5m', '1m': '1m', 'hourly': 'hourly', '15m': '5m'}
        tf = tf_map.get(interval, 'daily')
        self.ensure_fresh(symbols, interval)
        pieces = {}
        for sym in clean:
            if interval == '1d':
                df = self.get_daily(sym, start, end)
                if df is not None and not df.empty:
                    ts_col = 'date'
                    df = df.copy()
                    df[ts_col] = pd.to_datetime(df[ts_col])
                    df = df.set_index(ts_col)
                    for col in df.columns:
                        if col.lower() in ('open', 'high', 'low', 'close', 'volume'):
                            pieces[(col.capitalize(), sym + '.NS')] = df[col]
            else:
                # Intraday: iterate over each day in the range
                sd = pd.Timestamp(start)
                ed = pd.Timestamp(end)
                day = sd
                while day <= ed:
                    day_str = day.strftime('%Y-%m-%d')
                    try:
                        idf = self.get_intraday(sym, day_str, interval)
                        if idf is not None and not idf.empty:
                            idf = idf.copy()
                            ts_col = 'datetime_ist' if 'datetime_ist' in idf.columns else 'date'
                            if ts_col in idf.columns:
                                idf[ts_col] = pd.to_datetime(idf[ts_col])
                                idf = idf.set_index(ts_col)
                            for col in idf.columns:
                                if col.lower() in ('open', 'high', 'low', 'close', 'volume'):
                                    key = (col.capitalize(), sym + '.NS')
                                    if key in pieces:
                                        pieces[key] = pd.concat([pieces[key], idf[col]])
                                    else:
                                        pieces[key] = idf[col]
                    except Exception:
                        pass
                    day += timedelta(days=1)
        if not pieces:
            return pd.DataFrame()
        all_data = pd.DataFrame(pieces)
        all_data.columns = pd.MultiIndex.from_tuples(all_data.columns, names=['Price', 'Ticker'])
        all_data = all_data.sort_index()
        return all_data

    def _period_to_range(self, period_str):
        """Convert yfinance-style period string to (start, end) date strings."""
        now = datetime.now()
        period_str = period_str.lower().strip()
        m = re.match(r'(\d+)\s*(d|w|mo|y)', period_str)
        if m:
            num, unit = int(m.group(1)), m.group(2)
            if unit == 'd':
                start = (now - timedelta(days=num)).strftime('%Y-%m-%d')
            elif unit == 'w':
                start = (now - timedelta(weeks=num)).strftime('%Y-%m-%d')
            elif unit == 'mo':
                start = (now - timedelta(days=num * 30)).strftime('%Y-%m-%d')
            elif unit == 'y':
                start = (now - timedelta(days=num * 365)).strftime('%Y-%m-%d')
            else:
                start = (now - timedelta(days=1)).strftime('%Y-%m-%d')
        else:
            start = (now - timedelta(days=1)).strftime('%Y-%m-%d')
        end = (now + timedelta(days=1)).strftime('%Y-%m-%d')
        return start, end

    def get_intraday_by_period(self, symbol, period, interval="5m"):
        """Fetch intraday data using period string (like yfinance).
        Converts to date range internally, routes through DuckDB cache.
        Concatenates all days in the range into a single DataFrame."""
        start, end = self._period_to_range(period)
        pieces = []
        day = pd.Timestamp(start)
        end_dt = pd.Timestamp(end)
        while day < end_dt:
            try:
                df = self.get_intraday_for_backtest(symbol, day.strftime("%Y-%m-%d"), interval)
                if df is not None and not df.empty:
                    pieces.append(df)
            except Exception:
                pass
            day += timedelta(days=1)
        if not pieces:
            return None
        result = pd.concat(pieces)
        result = result[~result.index.duplicated(keep='last')]
        return result.sort_index()

    def get_last_price_bulk(self, symbols):
        """Bulk fetch last prices for multiple symbols via the cache.
        Routes through get_intraday for today's 5m data and picks last close."""
        today = datetime.now().strftime('%Y-%m-%d')
        result = {}
        for sym in symbols:
            clean = sym.replace('.NS', '').replace('.BO', '')
            try:
                df = self.get_intraday(clean, today, '5m')
                if df is not None and not df.empty:
                    close_col = 'close' if 'close' in df.columns else 'Close'
                    if close_col in df.columns:
                        result[sym] = float(df[close_col].iloc[-1])
            except Exception:
                pass
        return result

    # ── NFO Symbols (Options/Futures) ─────────────────────────

    def import_nfo_symbols(self, csv_path: str = None, url: str = None):
        """Import NFO symbols from a CSV file or download from Shoonya URL.
        Clears old data and repopulates the nfo_symbols table.

        Args:
            csv_path: Path to NFO_symbols.txt (CSV format)
            url: URL to download NFO_symbols.txt.zip from (default: Shoonya API)
        """
        if not csv_path and not url:
            url = "https://api.shoonya.com/NFO_symbols.txt.zip"

        try:
            if url:
                import urllib.request
                import zipfile, io
                with urllib.request.urlopen(url, timeout=30) as resp:
                    z = zipfile.ZipFile(io.BytesIO(resp.read()))
                    csv_data = z.read("NFO_symbols.txt").decode("utf-8")
                lines = [l.strip() for l in csv_data.split("\n") if l.strip()]
            else:
                with open(csv_path, "r", encoding="utf-8") as f:
                    lines = [l.strip() for l in f.readlines() if l.strip()]

            header = lines[0].split(",")
            col_map = {}
            for c in ["Exchange","Token","LotSize","Symbol","TradingSymbol","Expiry","Instrument","OptionType","StrikePrice","TickSize"]:
                try:
                    col_map[c] = header.index(c)
                except ValueError:
                    col_map[c] = None

            records = []
            for line in lines[1:]:
                parts = line.split(",")
                try:
                    token = int(parts[col_map["Token"]]) if col_map["Token"] is not None else 0
                    lot_size = int(parts[col_map["LotSize"]]) if col_map["LotSize"] is not None else 0
                    expiry_str = parts[col_map["Expiry"]].strip()
                    expiry = pd.Timestamp(expiry_str).date() if expiry_str else None
                    strike = float(parts[col_map["StrikePrice"]]) if col_map["StrikePrice"] is not None else 0.0
                    tick = float(parts[col_map["TickSize"]]) if col_map["TickSize"] is not None else 0.05
                    records.append((
                        parts[col_map["Exchange"]].strip() if col_map["Exchange"] is not None else "NFO",
                        token, lot_size,
                        parts[col_map["Symbol"]].strip(),
                        parts[col_map["TradingSymbol"]].strip(),
                        expiry,
                        parts[col_map["Instrument"]].strip(),
                        parts[col_map["OptionType"]].strip(),
                        strike, tick
                    ))
                except (ValueError, IndexError):
                    continue

            if not records:
                return 0

            con = self._db_write()
            con.execute("DELETE FROM nfo_symbols")
            con.executemany("""
                INSERT INTO nfo_symbols
                    (exchange, token, lot_size, symbol, trading_symbol, expiry,
                     instrument, option_type, strike_price, tick_size)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, records)
            con.close()
            return len(records)
        except Exception as e:
            print(f"  [NFO] Import failed: {e}")
            return 0

    def get_option_symbol(self, symbol: str, strike: float, expiry_date, option_type: str) -> dict:
        """Look up option details from nfo_symbols table.
        Args:
            symbol: Stock symbol (e.g., 'RELIANCE')
            strike: Strike price
            expiry_date: Expiry date (datetime or str like '2026-07-28')
            option_type: 'CE' or 'PE'
        Returns:
            dict with trading_symbol, lot_size, token, tick_size, strike_price, expiry
            or None if not found
        """
        sym = symbol.replace(".NS", "").upper()
        if isinstance(expiry_date, str):
            expiry = pd.Timestamp(expiry_date).date()
        else:
            expiry = expiry_date.date() if hasattr(expiry_date, 'date') else expiry_date
        opt = "CE" if option_type in ("C", "CE", "CALL") else "PE"

        try:
            con = self._db_read()
            row = con.execute("""
                SELECT trading_symbol, lot_size, token, tick_size, strike_price, expiry
                FROM nfo_symbols
                WHERE symbol = ? AND expiry = ? AND option_type = ? AND strike_price = ?
                LIMIT 1
            """, (sym, expiry, opt, strike)).fetchone()
            con.close()
            if row:
                return {
                    "trading_symbol": row[0],
                    "lot_size": row[1],
                    "token": row[2],
                    "tick_size": row[3],
                    "strike_price": row[4],
                    "expiry": row[5],
                }
            return None
        except Exception:
            return None

    def find_option_strike(self, symbol: str, underlying_price: float, expiry_date,
                           option_type: str) -> dict:
        """Find the nearest option strike from nfo_symbols table.
        Args:
            symbol: Stock symbol
            underlying_price: Current stock price
            expiry_date: Expiry date
            option_type: 'CE' or 'PE'
        Returns:
            dict with trading_symbol, strike_price, lot_size, etc, or None
        """
        sym = symbol.replace(".NS", "").upper()
        if isinstance(expiry_date, str):
            expiry = pd.Timestamp(expiry_date).date()
        else:
            expiry = expiry_date.date() if hasattr(expiry_date, 'date') else expiry_date
        opt = "CE" if option_type in ("C", "CE", "CALL") else "PE"

        try:
            con = self._db_read()
            rows = con.execute("""
                SELECT trading_symbol, lot_size, token, tick_size, strike_price, expiry
                FROM nfo_symbols
                WHERE symbol = ? AND expiry = ? AND option_type = ?
                ORDER BY ABS(strike_price - ?)
                LIMIT 1
            """, (sym, expiry, opt, underlying_price)).fetchone()
            con.close()
            if rows:
                return {
                    "trading_symbol": rows[0],
                    "lot_size": rows[1],
                    "token": rows[2],
                    "tick_size": rows[3],
                    "strike_price": rows[4],
                    "expiry": rows[5],
                }
            return None
        except Exception:
            return None

    def resolve_option_contract(self, symbol: str, underlying_price: float,
                                 direction: str, expiry_date=None,
                                 strike_mode: str = "ATM",
                                 min_dte: int = 0) -> Optional[dict]:
        """Resolve option contract from nfo_symbols table.
        Single source of truth — no formula-based expiry or strike calculation.
        All portfolios (Swing, PairTrading, etc.) should use this method.

        Args:
            symbol: Stock symbol (e.g. 'RELIANCE', 'LODHA')
            underlying_price: Current market / fill price
            direction: 'LONG' (CE) or 'SHORT' (PE)
            expiry_date: Optional specific expiry (date/str). If None,
                         picks the nearest future expiry from NFO table.
            strike_mode: 'ATM' — nearest absolute strike (default)
                         'ITM1' — one tick toward in-the-money
            min_dte: Minimum days to expiry. If auto-selecting expiry, skip
                     contracts with fewer than min_dte days remaining.
                     Use 10 for entry (avoid theta decay), 7 for rollover check.

        Returns:
            dict with trading_symbol, strike_price, expiry, lot_size,
            tick_size, token, option_type, or None if no contract found.

        Usage:
            contract = cache.resolve_option_contract("LODHA", 1205.50, "LONG")
            # -> {'trading_symbol': 'LODHA28JUL26C1200', 'strike_price': 1200.0, ...}
        """
        sym = symbol.replace(".NS", "").upper()
        opt_type = "CE" if direction == "LONG" else "PE"

        try:
            # 1. Resolve expiry (skip those within min_dte)
            if expiry_date is None:
                con = self._db_read()
                rows = con.execute("""
                    SELECT DISTINCT expiry FROM nfo_symbols
                    WHERE symbol = ? AND option_type = ? AND expiry > CURRENT_DATE
                    ORDER BY expiry
                """, (sym, opt_type)).fetchall()
                con.close()
                if not rows:
                    return None
                from datetime import date as _dt_date
                today = _dt_date.today()
                expiry = None
                for r in rows:
                    dte = (r[0] - today).days if hasattr(r[0], '__sub__') else 30
                    if dte >= min_dte:
                        expiry = r[0]
                        break
                if expiry is None:
                    # All available expiries are within min_dte — use the furthest one
                    expiry = rows[-1][0]
            else:
                if isinstance(expiry_date, str):
                    expiry = pd.Timestamp(expiry_date).date()
                elif hasattr(expiry_date, 'date'):
                    expiry = expiry_date.date()
                else:
                    expiry = expiry_date

            # 2. Resolve strike
            if strike_mode == "ITM1":
                # ITM1: prefer strike toward in-the-money
                con = self._db_read()
                if direction == "LONG":
                    row = con.execute("""
                        SELECT trading_symbol, lot_size, token, tick_size,
                               strike_price, expiry
                        FROM nfo_symbols
                        WHERE symbol = ? AND expiry = ? AND option_type = ?
                          AND strike_price <= ?
                        ORDER BY strike_price DESC LIMIT 1
                    """, (sym, expiry, opt_type, underlying_price)).fetchone()
                else:
                    row = con.execute("""
                        SELECT trading_symbol, lot_size, token, tick_size,
                               strike_price, expiry
                        FROM nfo_symbols
                        WHERE symbol = ? AND expiry = ? AND option_type = ?
                          AND strike_price >= ?
                        ORDER BY strike_price ASC LIMIT 1
                    """, (sym, expiry, opt_type, underlying_price)).fetchone()
                con.close()
                if not row:
                    # Fallback: nearest absolute
                    nfo = self.find_option_strike(sym, underlying_price, expiry, opt_type)
                    if nfo:
                        nfo["option_type"] = opt_type
                        nfo["dte"] = (expiry - datetime.now().date()).days if hasattr(expiry, '__sub__') else 30
                    return nfo
                return {
                    "trading_symbol": row[0], "lot_size": row[1], "token": row[2],
                    "tick_size": row[3], "strike_price": row[4], "expiry": row[5],
                    "option_type": opt_type,
                    "dte": (row[5] - datetime.now().date()).days if hasattr(row[5], '__sub__') else 30,
                }

            # ATM: nearest absolute strike
            nfo = self.find_option_strike(sym, underlying_price, expiry, opt_type)
            if nfo:
                nfo["option_type"] = opt_type
                nfo["dte"] = (expiry - datetime.now().date()).days if hasattr(expiry, '__sub__') else 30
            return nfo

        except Exception:
            return None

    def get_nfo_lot_size(self, trading_symbol: str) -> int:
        """Get lot size for a specific NFO trading symbol."""
        try:
            con = self._db_read()
            row = con.execute(
                "SELECT lot_size FROM nfo_symbols WHERE trading_symbol = ?",
                (trading_symbol,)
            ).fetchone()
            con.close()
            return row[0] if row else None
        except Exception:
            return None


def get_cache(portfolio="__default__") -> DataCache:
    return DataCache.instance(portfolio)
