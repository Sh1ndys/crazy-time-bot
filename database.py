"""
Работа с базой данных SQLite
"""

import sqlite3
import logging
from datetime import datetime, timezone
from config import DB_PATH, DEFAULT_THRESHOLDS

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, path: str = DB_PATH):
        self.path = path

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # ── ИНИЦИАЛИЗАЦИЯ ─────────────────────────────────────────────────────────

    def init(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS spins (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    spin_id     TEXT UNIQUE,        -- уникальный ID спина от tracksino
                    result      TEXT NOT NULL,       -- CrazyTime / CoinFlip / 1 / 2 / 5 / 10 ...
                    timestamp   DATETIME NOT NULL,   -- UTC время спина
                    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_spins_result    ON spins(result);
                CREATE INDEX IF NOT EXISTS idx_spins_timestamp ON spins(timestamp);
                CREATE INDEX IF NOT EXISTS idx_spins_spin_id   ON spins(spin_id);

                CREATE TABLE IF NOT EXISTS thresholds (
                    event     TEXT PRIMARY KEY,
                    threshold INTEGER NOT NULL
                );

                -- Таблица для отслеживания уже отправленных уведомлений
                -- чтобы не спамить одно и то же
                CREATE TABLE IF NOT EXISTS sent_alerts (
                    event         TEXT NOT NULL,
                    absence_count INTEGER NOT NULL,
                    sent_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (event, absence_count)
                );

                CREATE TABLE IF NOT EXISTS subscribers (
                    chat_id    INTEGER PRIMARY KEY,
                    username   TEXT,
                    joined_at  DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS bonus_gaps (
                    id       INTEGER PRIMARY KEY AUTOINCREMENT,
                    gap      INTEGER NOT NULL,
                    ended_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS bonus_multipliers (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    event      TEXT NOT NULL,
                    multiplier REAL NOT NULL,
                    timestamp  DATETIME NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_bm_event     ON bonus_multipliers(event);
                CREATE INDEX IF NOT EXISTS idx_bm_timestamp ON bonus_multipliers(timestamp);

                CREATE TABLE IF NOT EXISTS after_series_bonuses (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    event          TEXT NOT NULL,
                    series_length  INTEGER NOT NULL,
                    multiplier     REAL,
                    timestamp      DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS sim_settings (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sim_active_bets (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    event           TEXT NOT NULL,
                    current_stake   REAL NOT NULL,
                    bets_remaining  INTEGER NOT NULL,
                    martingale_step INTEGER NOT NULL DEFAULT 0,
                    triggered_at    DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS sim_history (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    event      TEXT NOT NULL,
                    stake      REAL NOT NULL,
                    outcome    TEXT NOT NULL,
                    profit     REAL NOT NULL,
                    multiplier REAL,
                    timestamp  DATETIME DEFAULT CURRENT_TIMESTAMP
                );
            """)
        logger.info(f"База данных инициализирована: {self.path}")

    def init_default_thresholds(self):
        with self._conn() as conn:
            for event, threshold in DEFAULT_THRESHOLDS.items():
                conn.execute(
                    "INSERT OR IGNORE INTO thresholds (event, threshold) VALUES (?, ?)",
                    (event, threshold)
                )

    # ── СПИНЫ ─────────────────────────────────────────────────────────────────

    def save_spin(self, spin_id: str, result: str, timestamp: datetime) -> bool:
        """Сохранить спин. Возвращает True если новый, False если уже есть."""
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO spins (spin_id, result, timestamp) VALUES (?, ?, ?)",
                    (spin_id, result, timestamp.isoformat())
                )
            return True
        except sqlite3.IntegrityError:
            return False  # уже есть

    def get_last_spin_id(self) -> str | None:
        """Получить spin_id последнего сохранённого спина (для инкрементального парсинга)"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT spin_id FROM spins ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
        return row['spin_id'] if row else None

    def get_last_spin(self) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT result, timestamp FROM spins ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def get_total_spins(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM spins").fetchone()[0]

    def get_recent_spins(self, limit: int = 5000) -> list[dict]:
        """Последние N спинов по убыванию времени"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT result, timestamp FROM spins ORDER BY timestamp DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_spins_since(self, since: datetime) -> list[dict]:
        """Все спины начиная с указанной даты"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT result, timestamp FROM spins WHERE timestamp >= ? ORDER BY timestamp ASC",
                (since.isoformat(),)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_all_spins_ordered(self) -> list[str]:
        """Все результаты по возрастанию времени (для вычисления промежутков)"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT result FROM spins ORDER BY timestamp ASC"
            ).fetchall()
        return [r['result'] for r in rows]

    def get_spins_in_month(self, year: int, month: int) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT result FROM spins
                   WHERE strftime('%Y', timestamp) = ? AND strftime('%m', timestamp) = ?
                   ORDER BY timestamp ASC""",
                (str(year), f"{month:02d}")
            ).fetchall()
        return [r['result'] for r in rows]

    # ── ПОРОГИ ────────────────────────────────────────────────────────────────

    def get_all_thresholds(self) -> dict[str, int]:
        with self._conn() as conn:
            rows = conn.execute("SELECT event, threshold FROM thresholds").fetchall()
        return {r['event']: r['threshold'] for r in rows}

    def get_threshold(self, event: str) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT threshold FROM thresholds WHERE event = ?", (event,)
            ).fetchone()
        return row['threshold'] if row else DEFAULT_THRESHOLDS.get(event, 300)

    def set_threshold(self, event: str, threshold: int):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO thresholds (event, threshold) VALUES (?, ?)",
                (event, threshold)
            )

    # ── УВЕДОМЛЕНИЯ ───────────────────────────────────────────────────────────

    def was_alert_sent(self, event: str, absence_count: int) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM sent_alerts WHERE event = ? AND absence_count = ?",
                (event, absence_count)
            ).fetchone()
        return row is not None

    def mark_alert_sent(self, event: str, absence_count: int):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO sent_alerts (event, absence_count) VALUES (?, ?)",
                (event, absence_count)
            )

    def clear_alert_history(self, event: str):
        """Очищает историю уведомлений (например, после выпадения события)"""
        with self._conn() as conn:
            conn.execute("DELETE FROM sent_alerts WHERE event = ?", (event,))

    # ── ПОДПИСЧИКИ ────────────────────────────────────────────────────────────

    def add_subscriber(self, chat_id: int, username: str = None):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO subscribers (chat_id, username) VALUES (?, ?)",
                (chat_id, username)
            )

    def remove_subscriber(self, chat_id: int):
        with self._conn() as conn:
            conn.execute("DELETE FROM subscribers WHERE chat_id = ?", (chat_id,))

    def get_all_subscribers(self) -> list[int]:
        with self._conn() as conn:
            rows = conn.execute("SELECT chat_id FROM subscribers").fetchall()
        return [r["chat_id"] for r in rows]

    def is_subscriber(self, chat_id: int) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM subscribers WHERE chat_id = ?", (chat_id,)
            ).fetchone()
        return row is not None

    def get_subscribers_count(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM subscribers").fetchone()[0]

    # ── СЕРИИ БЕЗ БОНУСОВ ────────────────────────────────────────────────────

    def save_bonus_gap(self, gap: int):
        with self._conn() as conn:
            conn.execute("INSERT INTO bonus_gaps (gap) VALUES (?)", (gap,))

    # ── МНОЖИТЕЛИ БОНУСОВ ────────────────────────────────────────────────────

    def save_bonus_multiplier(self, event: str, multiplier: float, timestamp: datetime):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO bonus_multipliers (event, multiplier, timestamp) VALUES (?, ?, ?)",
                (event, multiplier, timestamp.isoformat())
            )

    def get_multiplier_stats(self) -> dict:
        """Статистика множителей за всё время и за последние 24 часа"""
        from datetime import timedelta
        since_24h = (datetime.now(tz=timezone.utc) - timedelta(hours=24)).isoformat()
        result = {}
        with self._conn() as conn:
            for event in ["CoinFlip", "Pachinko", "CashHunt", "CrazyTime"]:
                row_all = conn.execute(
                    """SELECT COUNT(*) as cnt, MAX(multiplier) as mx, AVG(multiplier) as av
                       FROM bonus_multipliers WHERE event = ?""",
                    (event,)
                ).fetchone()
                row_24h = conn.execute(
                    """SELECT MAX(multiplier) as mx
                       FROM bonus_multipliers WHERE event = ? AND timestamp >= ?""",
                    (event, since_24h)
                ).fetchone()
                result[event] = {
                    "count":    row_all["cnt"] or 0,
                    "max_all":  row_all["mx"] or 0,
                    "avg_all":  row_all["av"] or 0.0,
                    "max_24h":  row_24h["mx"] or 0,
                }
        return result

    def get_bonus_gap_stats(self) -> dict:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt, MAX(gap) as mx, MIN(gap) as mn, AVG(gap) as av FROM bonus_gaps"
            ).fetchone()
            top5 = conn.execute(
                "SELECT gap FROM bonus_gaps ORDER BY gap DESC LIMIT 5"
            ).fetchall()
        return {
            "count": row["cnt"] or 0,
            "max":   row["mx"] or 0,
            "min":   row["mn"] or 0,
            "avg":   row["av"] or 0.0,
            "top5":  [r["gap"] for r in top5],
        }

    # ── AFTER SERIES BONUSES ──────────────────────────────────────────────────

    def save_after_series_bonus(self, event: str, series_length: int, multiplier: float = None):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO after_series_bonuses (event, series_length, multiplier) VALUES (?, ?, ?)",
                (event, series_length, multiplier)
            )

    def get_after_series_stats(self, min_series: int = 30) -> dict:
        from datetime import timedelta
        since_24h = (datetime.now(tz=timezone.utc) - timedelta(hours=24)).isoformat()
        with self._conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM after_series_bonuses WHERE series_length >= ?",
                (min_series,)
            ).fetchone()[0]
            rows_all = conn.execute(
                """SELECT event, COUNT(*) as cnt, AVG(multiplier) as avg_mult, MAX(multiplier) as max_mult
                   FROM after_series_bonuses WHERE series_length >= ?
                   GROUP BY event ORDER BY cnt DESC""",
                (min_series,)
            ).fetchall()
            rows_24h = conn.execute(
                """SELECT event, COUNT(*) as cnt, MAX(multiplier) as max_mult
                   FROM after_series_bonuses WHERE series_length >= ? AND timestamp >= ?
                   GROUP BY event ORDER BY cnt DESC""",
                (min_series, since_24h)
            ).fetchall()
        return {
            "total": total,
            "all":   [dict(r) for r in rows_all],
            "24h":   [dict(r) for r in rows_24h],
        }

    # ── СИМУЛЯТОР ─────────────────────────────────────────────────────────────

    SIM_DEFAULTS = {
        "balance":          "100000",
        "stake":            "100",
        "num_bets":         "10",
        "strategy":         "flat",
        "martingale_mult":  "2.0",
        "martingale_steps": "3",
        "enabled":          "1",
    }

    def sim_get(self, key: str) -> str:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM sim_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else self.SIM_DEFAULTS.get(key, "")

    def sim_set(self, key: str, value: str):
        with self._conn() as conn:
            conn.execute("INSERT OR REPLACE INTO sim_settings (key, value) VALUES (?, ?)", (key, value))

    def sim_init_defaults(self):
        for k, v in self.SIM_DEFAULTS.items():
            with self._conn() as conn:
                conn.execute("INSERT OR IGNORE INTO sim_settings (key, value) VALUES (?, ?)", (k, v))

    def sim_get_balance(self) -> float:
        return float(self.sim_get("balance"))

    def sim_set_balance(self, balance: float):
        self.sim_set("balance", str(round(balance, 2)))

    def sim_get_active_bets(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM sim_active_bets").fetchall()
        return [dict(r) for r in rows]

    def sim_add_active_bet(self, event: str, stake: float, bets_remaining: int, martingale_step: int = 0):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO sim_active_bets (event, current_stake, bets_remaining, martingale_step) VALUES (?, ?, ?, ?)",
                (event, stake, bets_remaining, martingale_step)
            )

    def sim_update_active_bet(self, bet_id: int, bets_remaining: int, current_stake: float, martingale_step: int):
        with self._conn() as conn:
            conn.execute(
                "UPDATE sim_active_bets SET bets_remaining=?, current_stake=?, martingale_step=? WHERE id=?",
                (bets_remaining, current_stake, martingale_step, bet_id)
            )

    def sim_remove_active_bet(self, bet_id: int):
        with self._conn() as conn:
            conn.execute("DELETE FROM sim_active_bets WHERE id=?", (bet_id,))

    def sim_has_active_bet(self, event: str) -> bool:
        with self._conn() as conn:
            row = conn.execute("SELECT 1 FROM sim_active_bets WHERE event=?", (event,)).fetchone()
        return row is not None

    def sim_add_history(self, event: str, stake: float, outcome: str, profit: float, multiplier: float = None):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO sim_history (event, stake, outcome, profit, multiplier) VALUES (?, ?, ?, ?, ?)",
                (event, stake, outcome, profit, multiplier)
            )

    def sim_get_history_stats(self) -> dict:
        from datetime import timedelta
        since_24h = (datetime.now(tz=timezone.utc) - timedelta(hours=24)).isoformat()
        with self._conn() as conn:
            row_all = conn.execute(
                """SELECT COUNT(*) as total,
                          SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) as wins,
                          SUM(profit) as total_profit,
                          MAX(profit) as best_win,
                          MIN(profit) as worst_loss
                   FROM sim_history"""
            ).fetchone()
            row_24h = conn.execute(
                """SELECT COUNT(*) as total,
                          SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) as wins,
                          SUM(profit) as total_profit
                   FROM sim_history WHERE timestamp >= ?""",
                (since_24h,)
            ).fetchone()
        return {
            "all": dict(row_all),
            "24h": dict(row_24h),
        }

    def sim_reset(self):
        with self._conn() as conn:
            conn.execute("DELETE FROM sim_active_bets")
            conn.execute("DELETE FROM sim_history")
