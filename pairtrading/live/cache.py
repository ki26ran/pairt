"""
PairTradingCache — isolated DuckDB for PairTrading strategy.
Separate from market_data.duckdb to avoid Windows file-locking conflicts.
"""
import os, sys, duckdb, json
from datetime import datetime
from typing import Dict, List, Optional, Any
from pairtrading.configs.symbols import get_nifty200

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(BASE_DIR)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DB_PATH = os.path.join(BASE_DIR, "pairtrading.duckdb")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pair_positions (
    pair_key VARCHAR PRIMARY KEY,
    s1 VARCHAR NOT NULL,
    s2 VARCHAR NOT NULL,
    direction VARCHAR NOT NULL,
    entry_date TIMESTAMP NOT NULL,
    entry_z DOUBLE,
    entry_p1 DOUBLE,
    entry_p2 DOUBLE,
    entry_z_threshold DOUBLE,
    exit_z_threshold DOUBLE,
    hr DOUBLE,
    last_z DOUBLE,
    last_p1 DOUBLE,
    last_p2 DOUBLE,
    last_updated TIMESTAMP,
    broker_order_id VARCHAR
);

CREATE TABLE IF NOT EXISTS pair_signals (
    id BIGINT PRIMARY KEY,
    timestamp TIMESTAMP NOT NULL,
    s1 VARCHAR NOT NULL,
    s2 VARCHAR NOT NULL,
    s1_price DOUBLE,
    s2_price DOUBLE,
    z_score DOUBLE,
    entry_z DOUBLE,
    exit_z DOUBLE,
    signal VARCHAR NOT NULL,
    hr DOUBLE
);

CREATE INDEX IF NOT EXISTS idx_pair_signals_ts ON pair_signals(timestamp);
CREATE INDEX IF NOT EXISTS idx_pair_signals_pair ON pair_signals(s1, s2);

CREATE TABLE IF NOT EXISTS pair_signals_today (
    s1 VARCHAR NOT NULL,
    s2 VARCHAR NOT NULL,
    s1_price DOUBLE,
    s2_price DOUBLE,
    z_score DOUBLE,
    entry_z DOUBLE,
    exit_z DOUBLE,
    signal VARCHAR NOT NULL,
    hr DOUBLE,
    PRIMARY KEY (s1, s2)
);

CREATE TABLE IF NOT EXISTS pair_scanner_results (
    pair_key VARCHAR PRIMARY KEY,
    timestamp TIMESTAMP NOT NULL,
    s1 VARCHAR NOT NULL,
    s2 VARCHAR NOT NULL,
    s1_price DOUBLE,
    s2_price DOUBLE,
    z_score DOUBLE,
    entry_z DOUBLE,
    exit_z DOUBLE,
    signal VARCHAR NOT NULL,
    hr DOUBLE
);

CREATE TABLE IF NOT EXISTS pair_thresholds (
    pair_key VARCHAR PRIMARY KEY,
    entry_z DOUBLE NOT NULL,
    exit_z DOUBLE NOT NULL,
    hr DOUBLE NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pair_discovered (
    pair_key VARCHAR PRIMARY KEY,
    stock1 VARCHAR NOT NULL,
    stock2 VARCHAR NOT NULL,
    sector VARCHAR,
    correlation DOUBLE,
    coint_pvalue DOUBLE,
    hedge_ratio DOUBLE,
    half_life DOUBLE,
    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pair_config (
    key VARCHAR PRIMARY KEY,
    value VARCHAR,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pair_trades (
    trade_id BIGINT PRIMARY KEY,
    pair_key VARCHAR NOT NULL,
    s1 VARCHAR NOT NULL,
    s2 VARCHAR NOT NULL,
    direction VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    entry_date TIMESTAMP,
    exit_date TIMESTAMP,
    entry_price_s1 DOUBLE,
    entry_price_s2 DOUBLE,
    exit_price_s1 DOUBLE,
    exit_price_s2 DOUBLE,
    entry_z DOUBLE,
    exit_z DOUBLE,
    z_at_exit DOUBLE,
    hr DOUBLE,
    lot_size_s1 INT,
    lot_size_s2 INT,
    pnl_s1 DOUBLE,
    pnl_s2 DOUBLE,
    total_pnl DOUBLE,
    exit_reason VARCHAR,
    broker_order_id VARCHAR,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


class PairTradingCache:
    _instance = None
    _initialized = False
    _read_con = None
    _write_con = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def _migrate_open_positions(self, con):
        """One-time migration: seed existing open positions into pair_trades."""
        try:
            migrated = con.execute("SELECT value FROM pair_config WHERE key = 'pair_trades_migrated'").fetchone()
            if migrated:
                return
            rows = con.execute("SELECT * FROM pair_positions").fetchall()
            if not rows:
                con.execute("INSERT OR REPLACE INTO pair_config (key, value) VALUES ('pair_trades_migrated', '1')")
                con.commit()
                return
            lot_cache = {e["Symbol"]: e["LotSize"] for e in get_nifty200()}
            next_id = con.execute("SELECT COALESCE(MAX(trade_id), 0) + 1 FROM pair_trades").fetchone()[0]
            for r in rows:
                pk = r[0]
                s1, s2 = r[1], r[2]
                direction = r[3]
                entry_date = r[4]
                entry_p1, entry_p2 = r[6], r[7]
                entry_z_th = r[8]   # entry_z_threshold
                exit_z_th = r[9]    # exit_z_threshold
                hr = r[10]
                last_z = r[11]
                lot1 = lot_cache.get(s1, 1)
                lot2 = lot_cache.get(s2, 1)
                # Calculate unrealized P&L from last_p1/last_p2
                last_p1, last_p2 = r[12], r[13]
                if last_p1 and last_p2 and direction == "LONG":
                    pnl_s1 = (last_p1 - entry_p1) * lot1
                    pnl_s2 = (entry_p2 - last_p2) * lot2
                elif last_p1 and last_p2 and direction == "SHORT":
                    pnl_s1 = (entry_p1 - last_p1) * lot1
                    pnl_s2 = (last_p2 - entry_p2) * lot2
                else:
                    pnl_s1 = pnl_s2 = None
                con.execute("""
                    INSERT INTO pair_trades (trade_id, pair_key, s1, s2, direction, status,
                        entry_date, entry_price_s1, entry_price_s2,
                        entry_z, exit_z, z_at_exit, hr, lot_size_s1, lot_size_s2,
                        pnl_s1, pnl_s2, total_pnl, created_at)
                    VALUES (?, ?, ?, ?, ?, 'OPEN',
                        ?, ?, ?,
                        ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?)
                """, (next_id, pk, s1, s2, direction,
                      entry_date, entry_p1, entry_p2,
                      entry_z_th, exit_z_th, last_z, hr, lot1, lot2,
                      round(pnl_s1, 2) if pnl_s1 is not None else None,
                      round(pnl_s2, 2) if pnl_s2 is not None else None,
                      round(pnl_s1 + pnl_s2, 2) if pnl_s1 is not None else None,
                      entry_date))
                next_id += 1
            con.execute("INSERT OR REPLACE INTO pair_config (key, value) VALUES ('pair_trades_migrated', '1')")
            con.commit()
            print(f"[INFO] Migrated {len(rows)} open positions to pair_trades")
        except Exception as e:
            print(f"[WARN] pair_trades migration failed: {e}")

    def _init_db(self):
        """Ensure all tables exist. Safe to call repeatedly - no-op after first call."""
        if PairTradingCache._initialized:
            return
        con = None
        try:
            con = duckdb.connect(DB_PATH)
            for stmt in _SCHEMA.strip().split(";"):
                stmt = stmt.strip()
                if stmt:
                    con.execute(stmt)
            PairTradingCache._initialized = True
            # Migrate existing open positions into pair_trades
            self._migrate_open_positions(con)
        except Exception as e:
            print(f"[WARN] PairTradingCache init failed: {e}")
        finally:
            if con:
                con.close()

    def _db_read(self):
        self._init_db()
        if PairTradingCache._read_con is None:
            PairTradingCache._read_con = duckdb.connect(DB_PATH)
        return PairTradingCache._read_con

    def _db_write(self):
        self._init_db()
        if PairTradingCache._write_con is None:
            PairTradingCache._write_con = duckdb.connect(DB_PATH)
        return PairTradingCache._write_con

    # --- pair_positions ---
    def load_positions(self) -> Dict[str, dict]:
        """Load all open positions from DuckDB."""
        con = None
        try:
            con = self._db_write()  # use write connection to avoid stale snapshots
            rows = con.execute("SELECT * FROM pair_positions ORDER BY pair_key").fetchall()
            cols = ["pair_key", "s1", "s2", "direction", "entry_date", "entry_z",
                    "entry_p1", "entry_p2", "entry_z_threshold", "exit_z_threshold",
                    "hr", "last_z", "last_p1", "last_p2", "last_updated",
                    "broker_order_id", "expiry_date"]
            positions = {}
            for r in rows:
                pk = r[0]
                pos = {cols[i]: r[i] for i in range(1, len(cols))}
                for k in ("entry_z", "entry_p1", "entry_p2", "entry_z_threshold",
                          "exit_z_threshold", "hr", "last_z", "last_p1", "last_p2"):
                    if pos.get(k) is None:
                        pos[k] = 0.0
                positions[pk] = pos
            return positions
        except Exception:
            return {}
        finally:
            if con:
                pass  # Don't close singleton _read_con

    def save_positions(self, positions: Dict[str, dict]) -> bool:
        """Replace all positions (upsert each)."""
        if not positions:
            return self.clear_positions()
        con = None
        try:
            con = self._db_write()
            for pk, p in positions.items():
                con.execute("""
                    INSERT OR REPLACE INTO pair_positions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    pk,
                    p.get("s1", ""),
                    p.get("s2", ""),
                    p.get("direction", ""),
                    p.get("entry_date", ""),
                    float(p.get("entry_z", 0)),
                    float(p.get("entry_p1", 0)),
                    float(p.get("entry_p2", 0)),
                    float(p.get("entry_z_threshold", 0)),
                    float(p.get("exit_z_threshold", 0)),
                    float(p.get("hr", 1)),
                    float(p.get("last_z", 0)),
                    float(p.get("last_p1", 0)),
                    float(p.get("last_p2", 0)),
                    p.get("last_updated", datetime.now().isoformat()),
                    p.get("broker_order_id"),
                    p.get("expiry_date", "")
                ))
            con.commit()
            return True
        except Exception as e:
            print(f"[WARN] save_positions failed: {e}")
            return False
        finally:
            if con:
                pass

    def open_position(self, pair_key: str, s1: str, s2: str, direction: str,
                      entry_z: float, entry_p1: float, entry_p2: float,
                      entry_z_threshold: float, exit_z_threshold: float,
                      hr: float, broker_order_id: str = None,
                      expiry_date: str = None) -> bool:
        """Open a new position. Skips if position already exists for this pair_key."""
        con = self._db_write()
        # Ensure expiry_date column exists
        try:
            con.execute("ALTER TABLE pair_positions ADD COLUMN expiry_date VARCHAR")
        except Exception:
            pass
        con.close()

        con = None
        try:
            con = self._db_write()
            existing = con.execute(
                "SELECT 1 FROM pair_positions WHERE pair_key = ?", (pair_key,)
            ).fetchone()
            if existing:
                return False
            now = datetime.now()
            con.execute("""
                INSERT OR REPLACE INTO pair_positions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                pair_key, s1, s2, direction, now.isoformat(),
                float(entry_z), float(entry_p1), float(entry_p2),
                float(entry_z_threshold), float(exit_z_threshold),
                float(hr), float(entry_z), float(entry_p1), float(entry_p2), now.isoformat(),
                broker_order_id, expiry_date
            ))
            # Look up lot sizes
            lot_cache = {e["Symbol"]: e["LotSize"] for e in get_nifty200()}
            lot1 = lot_cache.get(s1, 1)
            lot2 = lot_cache.get(s2, 1)
            # INSERT into pair_trades
            next_id = con.execute("SELECT COALESCE(MAX(trade_id), 0) + 1 FROM pair_trades").fetchone()[0]
            con.execute("""
                INSERT INTO pair_trades (trade_id, pair_key, s1, s2, direction, status,
                    entry_date, exit_date, entry_price_s1, entry_price_s2,
                    exit_price_s1, exit_price_s2, entry_z, exit_z, z_at_exit,
                    hr, lot_size_s1, lot_size_s2, pnl_s1, pnl_s2, total_pnl,
                    exit_reason, broker_order_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?,
                        NULL, NULL, ?, ?, ?,
                        ?, ?, ?, NULL, NULL, NULL,
                        NULL, ?, ?)
            """, (next_id, pair_key, s1, s2, direction, "OPEN",
                  now, entry_p1, entry_p2,
                  entry_z_threshold, exit_z_threshold, entry_z,
                  hr, lot1, lot2,
                  broker_order_id, now))
            con.commit()
            return True
        except Exception as e:
            print(f"[WARN] open_position failed: {e}")
            return False
        finally:
            if con:
                pass

    def close_position(self, pair_key: str,
                       exit_p1: float = None, exit_p2: float = None,
                       exit_z: float = None, exit_reason: str = None,
                       lot_scale: float = 1.0) -> bool:
        """Close a position: UPDATE most recent OPEN trade, then DELETE from pair_positions."""
        con = None
        try:
            con = self._db_write()
            row = con.execute("SELECT * FROM pair_positions WHERE pair_key = ?", (pair_key,)).fetchone()
            if not row:
                return False
            s1, s2, direction = row[1], row[2], row[3]
            entry_p1, entry_p2 = float(row[6]), float(row[7])
            entry_z_val = float(row[5])
            lot_cache = {e["Symbol"]: e["LotSize"] for e in get_nifty200()}
            lot1 = lot_cache.get(s1, 1)
            lot2 = lot_cache.get(s2, 1)
            if exit_p1 is not None and exit_p2 is not None:
                if direction == "LONG":
                    pnl_s1 = (exit_p1 - entry_p1) * lot1 * lot_scale
                    pnl_s2 = (entry_p2 - exit_p2) * lot2 * lot_scale
                else:
                    pnl_s1 = (entry_p1 - exit_p1) * lot1 * lot_scale
                    pnl_s2 = (exit_p2 - entry_p2) * lot2 * lot_scale
            else:
                pnl_s1, pnl_s2 = None, None
            total_pnl = round(pnl_s1 + pnl_s2, 2) if pnl_s1 is not None else None
            # Only close the most recent OPEN trade for this pair_key
            con.execute("""
                UPDATE pair_trades SET
                    status = 'CLOSED',
                    exit_date = ?,
                    exit_price_s1 = ?,
                    exit_price_s2 = ?,
                    z_at_exit = ?,
                    exit_z = ?,
                    pnl_s1 = ?,
                    pnl_s2 = ?,
                    total_pnl = ?,
                    exit_reason = ?
                WHERE trade_id = (
                    SELECT trade_id FROM pair_trades
                    WHERE pair_key = ? AND status = 'OPEN'
                    ORDER BY entry_date DESC LIMIT 1
                )
            """, (datetime.now(), exit_p1, exit_p2, exit_z, exit_z,
                  round(pnl_s1, 2) if pnl_s1 is not None else None,
                  round(pnl_s2, 2) if pnl_s2 is not None else None,
                  total_pnl, exit_reason, pair_key))
            con.execute("DELETE FROM pair_positions WHERE pair_key = ?", (pair_key,))
            con.commit()
            return True
        except Exception as e:
            print(f"[WARN] close_position failed: {e}")
            return False
        finally:
            if con:
                pass

    def update_position(self, pair_key: str, last_z: float, last_p1: float, last_p2: float) -> bool:
        """Update position with latest market data."""
        con = None
        try:
            con = self._db_write()
            con.execute("""
                UPDATE pair_positions
                SET last_z = ?, last_p1 = ?, last_p2 = ?, last_updated = ?
                WHERE pair_key = ?
            """, (float(last_z), float(last_p1), float(last_p2), datetime.now().isoformat(), pair_key))
            con.commit()
            return True
        except Exception as e:
            print(f"[WARN] update_position failed: {e}")
            return False
        finally:
            if con:
                pass

    def clear_positions(self) -> bool:
        con = None
        try:
            con = self._db_write()
            con.execute("DELETE FROM pair_positions")
            con.commit()
            return True
        except Exception:
            return False
        finally:
            if con:
                pass

    # --- pair_signals (history) ---
    def append_signal(self, s1: str, s2: str, s1_price: float, s2_price: float,
                      z_score: float, entry_z: float, exit_z: float,
                      signal: str, hr: float) -> bool:
        con = None
        try:
            con = self._db_write()
            # Dedup: skip if same pair+signal already logged today
            today = datetime.now().strftime('%Y-%m-%d')
            existing = con.execute("""
                SELECT COUNT(*) FROM pair_signals
                WHERE s1 = ? AND s2 = ? AND signal = ? AND CAST(timestamp AS DATE) = ?
            """, (s1, s2, signal, today)).fetchone()[0]
            if existing > 0:
                return False
            con.execute("BEGIN TRANSACTION")
            next_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM pair_signals").fetchone()[0]
            con.execute("""
                INSERT INTO pair_signals (id, timestamp, s1, s2, s1_price, s2_price,
                                          z_score, entry_z, exit_z, signal, hr)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (next_id, datetime.now(), s1, s2, s1_price, s2_price,
                  z_score, entry_z, exit_z, signal, hr))
            con.execute("COMMIT")
            return True
        except Exception as e:
            print(f"[WARN] append_signal failed: {e}")
            return False
        finally:
            if con:
                pass

    def log_signal(self, s1: str, s2: str, s1_price: float, s2_price: float,
                   z_score: float, entry_z: float, exit_z: float,
                   signal: str, hr: float) -> bool:
        """Log a signal to history."""
        return self.append_signal(s1, s2, s1_price, s2_price, z_score, entry_z, exit_z, signal, hr)

    def get_signal_history(self, limit: int = 100) -> List[dict]:
        """Return completed and open trades from pair_trades."""
        con = None
        try:
            con = self._db_read()
            rows = con.execute("""
                SELECT trade_id, pair_key, s1, s2, direction, status,
                       entry_date, exit_date, entry_price_s1, entry_price_s2,
                       exit_price_s1, exit_price_s2, entry_z, exit_z, z_at_exit, hr,
                       lot_size_s1, lot_size_s2, pnl_s1, pnl_s2, total_pnl, exit_reason
                FROM pair_trades
                ORDER BY trade_id DESC
                LIMIT ?
            """, (limit,)).fetchall()
            cols = ["trade_id", "pair_key", "s1", "s2", "direction", "status",
                    "entry_date", "exit_date", "entry_price_s1", "entry_price_s2",
                    "exit_price_s1", "exit_price_s2", "entry_z", "exit_z", "z_at_exit", "hr",
                    "lot_size_s1", "lot_size_s2", "pnl_s1", "pnl_s2", "total_pnl", "exit_reason"]
            return [dict(zip(cols, r)) for r in rows]
        except Exception:
            return []
        finally:
            if con:
                pass

    # --- pair_signals_today (active signals) ---
    def save_today_signals(self, signals: List[dict]) -> bool:
        if not signals:
            return self.clear_today_signals()
        con = None
        try:
            con = self._db_write()
            con.execute("DELETE FROM pair_signals_today")
            for s in signals:
                con.execute("""
                    INSERT OR REPLACE INTO pair_signals_today
                    (s1, s2, s1_price, s2_price, z_score, entry_z, exit_z, signal, hr)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (s.get("s1", ""), s.get("s2", ""),
                      s.get("s1_price", 0), s.get("s2_price", 0),
                      s.get("z_score", 0), s.get("entry_z", 0),
                      s.get("exit_z", 0), s.get("signal", ""),
                      s.get("hr", 1)))
            con.commit()
            return True
        except Exception as e:
            print(f"[WARN] save_today_signals failed: {e}")
            return False
        finally:
            if con:
                pass

    def load_today_signals(self) -> List[dict]:
        con = None
        try:
            con = self._db_read()
            rows = con.execute("SELECT s1, s2, s1_price, s2_price, z_score AS \"Z-score\", entry_z AS \"Entry Z\", exit_z AS \"Exit Z\", signal AS \"Signal\", hr FROM pair_signals_today").fetchall()
            cols = ["s1", "s2", "s1_price", "s2_price", "Z-score", "Entry Z", "Exit Z", "Signal", "hr"]
            return [dict(zip(cols, r)) for r in rows]
        except Exception:
            return []
        finally:
            if con:
                pass

    def clear_today_signals(self) -> bool:
        con = None
        try:
            con = self._db_write()
            con.execute("DELETE FROM pair_signals_today")
            con.commit()
            return True
        except Exception:
            return False
        finally:
            if con:
                pass

    # --- pair_scanner_results (full snapshot) ---
    def save_scanner_results(self, data: dict) -> bool:
        """Save full scan results snapshot (pairs + active_signals + config)."""
        con = None
        try:
            con = self._db_write()
            con.execute("DELETE FROM pair_scanner_results")
            pairs = data.get("pairs", [])
            for p in pairs:
                con.execute("""
                    INSERT INTO pair_scanner_results
                    (pair_key, timestamp, s1, s2, s1_price, s2_price,
                     z_score, entry_z, exit_z, signal, hr)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (f"{p.get('s1','')}|{p.get('s2','')}",
                      data.get("last_updated", datetime.now().isoformat()),
                      p.get("s1", ""), p.get("s2", ""),
                      p.get("s1_price", 0), p.get("s2_price", 0),
                      p.get("z_score", 0), p.get("entry_z", 0),
                      p.get("exit_z", 0), p.get("signal", "NONE"),
                      p.get("hr", 1)))
            con.commit()
            return True
        except Exception as e:
            print(f"[WARN] save_scanner_results failed: {e}")
            return False
        finally:
            if con:
                pass

    def load_scanner_results(self) -> dict:
        con = None
        try:
            con = self._db_read()
            rows = con.execute("""
                SELECT pair_key, timestamp, s1, s2, s1_price, s2_price,
                       z_score AS "Z-score", entry_z AS "Entry Z", 
                       exit_z AS "Exit Z", signal AS "Signal", hr
                FROM pair_scanner_results
            """).fetchall()
            if not rows:
                return {}
            cols = ["pair_key", "timestamp", "s1", "s2", "s1_price", "s2_price",
                    "Z-score", "Entry Z", "Exit Z", "Signal", "hr"]
            pairs = [dict(zip(cols, r)) for r in rows]
            active = [p for p in pairs if p.get("Signal", "NONE") != "NONE"]
            return {
                "last_updated": rows[0][1] if rows else "",
                "pairs": pairs,
                "active_signals": active,
                "config": {"max_positions": 3, "interval": "1h", "roll_window": 63}
            }
        except Exception:
            return {}
        finally:
            if con:
                pass

    # --- pair_thresholds ---
    def save_thresholds(self, thresholds: dict) -> bool:
        con = None
        try:
            con = self._db_write()
            con.execute("DELETE FROM pair_thresholds")
            for pk, v in thresholds.items():
                con.execute("""
                    INSERT INTO pair_thresholds (pair_key, entry_z, exit_z, hr)
                    VALUES (?, ?, ?, ?)
                """, (pk, v.get("entry_z", 2.0), v.get("exit_z", 0.5), v.get("hr", 1.0)))
            con.commit()
            return True
        except Exception as e:
            print(f"[WARN] save_thresholds failed: {e}")
            return False
        finally:
            if con:
                pass

    def load_thresholds(self) -> dict:
        con = None
        try:
            con = self._db_read()
            rows = con.execute("SELECT pair_key, entry_z, exit_z, hr FROM pair_thresholds").fetchall()
            return {r[0]: {"entry_z": r[1], "exit_z": r[2], "hr": r[3]} for r in rows}
        except Exception:
            return {}
        finally:
            if con:
                pass

    # --- pair_discovered ---
    def save_discovered_pairs(self, df) -> bool:
        """Replace all discovered pairs with a DataFrame."""
        con = None
        try:
            con = self._db_write()
            con.execute("DELETE FROM pair_discovered")
            for _, r in df.iterrows():
                pk = f"{r['Stock1']}|{r['Stock2']}"
                con.execute("""
                    INSERT INTO pair_discovered (pair_key, stock1, stock2, sector, correlation, coint_pvalue, hedge_ratio, half_life)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (pk, r['Stock1'], r['Stock2'], r.get('Sector', ''),
                      float(r.get('Correlation', 0)), float(r.get('Coint_PValue', 1)),
                      float(r.get('Hedge_Ratio', 1)), float(r.get('Half_Life', 0))))
            con.commit()
            return True
        except Exception as e:
            print(f"[WARN] save_discovered_pairs failed: {e}")
            return False
        finally:
            if con:
                pass

    def load_discovered_pairs(self) -> List[dict]:
        """Return list of dicts matching pairs.csv columns."""
        con = None
        try:
            con = self._db_read()
            rows = con.execute("""
                SELECT stock1, stock2, sector, correlation, coint_pvalue, hedge_ratio, half_life
                FROM pair_discovered ORDER BY coint_pvalue
            """).fetchall()
            cols = ["Stock1", "Stock2", "Sector", "Correlation", "Coint_PValue", "Hedge_Ratio", "Half_Life"]
            return [dict(zip(cols, r)) for r in rows]
        except Exception:
            return []
        finally:
            if con:
                pass

    def clear_discovered_pairs(self) -> bool:
        con = None
        try:
            con = self._db_write()
            con.execute("DELETE FROM pair_discovered")
            con.commit()
            return True
        except Exception:
            return False
        finally:
            if con:
                pass

    # --- pair_config ---
    def get_config(self, key: str, default: Any = None) -> Any:
        con = None
        try:
            con = self._db_read()
            r = con.execute("SELECT value FROM pair_config WHERE key = ?", (key,)).fetchone()
            if r:
                val = r[0]
                try:
                    return json.loads(val)
                except Exception:
                    return val
            return default
        except Exception:
            return default
        finally:
            if con:
                pass

    def set_config(self, key: str, value: Any) -> bool:
        con = None
        try:
            con = self._db_write()
            val = json.dumps(value) if not isinstance(value, str) else value
            con.execute("""
                INSERT OR REPLACE INTO pair_config (key, value) VALUES (?, ?)
            """, (key, val))
            con.commit()
            return True
        except Exception as e:
            print(f"[WARN] set_config failed: {e}")
            return False
        finally:
            if con:
                pass

    def delete_config(self, key: str) -> bool:
        con = None
        try:
            con = self._db_write()
            con.execute("DELETE FROM pair_config WHERE key = ?", (key,))
            con.commit()
            return True
        except Exception:
            return False
        finally:
            if con:
                pass

    # --- utilities ---
    def get_db_path(self) -> str:
        return DB_PATH

    def get_stats(self) -> dict:
        con = None
        try:
            con = self._db_read()
            stats = {}
            for table in ["pair_positions", "pair_signals", "pair_signals_today",
                          "pair_scanner_results", "pair_thresholds",
                          "pair_discovered", "pair_config"]:
                try:
                    count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    stats[table] = count
                except Exception:
                    stats[table] = 0
            return stats
        except Exception:
            return {}
        finally:
            if con:
                pass


def get_pair_cache() -> PairTradingCache:
    return PairTradingCache()
