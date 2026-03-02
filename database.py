"""
База даних (SQLite) — підписки, платежі, реферали, промокоди, статистика.
"""

import sqlite3
from datetime import datetime
from contextlib import contextmanager


class Database:
    def __init__(self, db_path: str = "subscriptions.db"):
        self.db_path = db_path

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self):
        """Створити таблиці при першому запуску."""
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id     INTEGER PRIMARY KEY,
                    username    TEXT,
                    referrer_id INTEGER,
                    created_at  TEXT
                );

                CREATE TABLE IF NOT EXISTS subscriptions (
                    user_id     INTEGER PRIMARY KEY,
                    payment_id  TEXT,
                    plan_key    TEXT DEFAULT '1m',
                    created_at  TEXT,
                    expires_at  TEXT
                );

                CREATE TABLE IF NOT EXISTS pending_payments (
                    payment_id  TEXT PRIMARY KEY,
                    user_id     INTEGER,
                    plan_key    TEXT DEFAULT '1m',
                    created_at  TEXT
                );

                CREATE TABLE IF NOT EXISTS promo_codes (
                    code        TEXT PRIMARY KEY,
                    discount    INTEGER,
                    used_count  INTEGER DEFAULT 0,
                    max_uses    INTEGER DEFAULT 100,
                    created_at  TEXT
                );

                CREATE TABLE IF NOT EXISTS user_promos (
                    user_id     INTEGER PRIMARY KEY,
                    code        TEXT,
                    discount    INTEGER
                );
            """)

    # ── Користувачі ───────────────────────────────────────────────────────────

    def register_user(self, user_id: int, username: str):
        with self._conn() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO users (user_id, username, created_at)
                VALUES (?, ?, ?)
            """, (user_id, username, datetime.utcnow().isoformat()))

    def get_user(self, user_id: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return dict(row) if row else None

    def set_referrer(self, user_id: int, referrer_id: int):
        with self._conn() as conn:
            conn.execute("""
                UPDATE users SET referrer_id = ?
                WHERE user_id = ? AND referrer_id IS NULL
            """, (referrer_id, user_id))

    def get_referrer(self, user_id: int) -> int | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT referrer_id FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
        return row["referrer_id"] if row else None

    def get_referral_count(self, user_id: int) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM users WHERE referrer_id = ?", (user_id,)
            ).fetchone()
        return row["cnt"] if row else 0

    # ── Підписки ──────────────────────────────────────────────────────────────

    def save_subscription(self, user_id: int, payment_id: str, expires_at: datetime, plan_key: str = "1m"):
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO subscriptions (user_id, payment_id, plan_key, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    payment_id = excluded.payment_id,
                    plan_key   = excluded.plan_key,
                    created_at = excluded.created_at,
                    expires_at = excluded.expires_at
            """, (user_id, payment_id, plan_key, datetime.utcnow().isoformat(), expires_at.isoformat()))

    def get_subscription(self, user_id: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM subscriptions WHERE user_id = ?", (user_id,)
            ).fetchone()
        if not row:
            return None
        return {
            "user_id":    row["user_id"],
            "payment_id": row["payment_id"],
            "plan_key":   row["plan_key"],
            "expires_at": datetime.fromisoformat(row["expires_at"]),
        }

    def get_active_subscriptions(self) -> list[dict]:
        now = datetime.utcnow().isoformat()
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM subscriptions WHERE expires_at > ?", (now,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_expired_subscriptions(self) -> list[dict]:
        now = datetime.utcnow().isoformat()
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM subscriptions WHERE expires_at <= ?", (now,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_expiring_soon(self, days: int = 3) -> list[dict]:
        """Підписки що закінчуються протягом N днів."""
        now = datetime.utcnow()
        soon = (now + __import__('datetime').timedelta(days=days)).isoformat()
        now_str = now.isoformat()
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM subscriptions WHERE expires_at > ? AND expires_at <= ?",
                (now_str, soon)
            ).fetchall()
        return [dict(r) for r in rows]

    def remove_subscription(self, user_id: int):
        with self._conn() as conn:
            conn.execute("DELETE FROM subscriptions WHERE user_id = ?", (user_id,))

    def update_subscription_expiry(self, user_id: int, new_expires: datetime):
        with self._conn() as conn:
            conn.execute(
                "UPDATE subscriptions SET expires_at = ? WHERE user_id = ?",
                (new_expires.isoformat(), user_id)
            )

    # ── Очікувані платежі ─────────────────────────────────────────────────────

    def save_pending_payment(self, user_id: int, payment_id: str, plan_key: str = "1m"):
        with self._conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO pending_payments (payment_id, user_id, plan_key, created_at)
                VALUES (?, ?, ?, ?)
            """, (payment_id, user_id, plan_key, datetime.utcnow().isoformat()))

    def get_pending_payment(self, payment_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM pending_payments WHERE payment_id = ?", (payment_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_user_by_payment(self, payment_id: str) -> int | None:
        row = self.get_pending_payment(payment_id)
        return row["user_id"] if row else None

    def delete_pending_payment(self, payment_id: str):
        with self._conn() as conn:
            conn.execute("DELETE FROM pending_payments WHERE payment_id = ?", (payment_id,))

    # ── Промокоди ─────────────────────────────────────────────────────────────

    def create_promo(self, code: str, discount: int, max_uses: int = 100):
        with self._conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO promo_codes (code, discount, max_uses, created_at)
                VALUES (?, ?, ?, ?)
            """, (code, discount, max_uses, datetime.utcnow().isoformat()))

    def check_promo(self, code: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("""
                SELECT * FROM promo_codes
                WHERE code = ? AND used_count < max_uses
            """, (code,)).fetchone()
        return dict(row) if row else None

    def save_user_promo(self, user_id: int, code: str, discount: int):
        with self._conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO user_promos (user_id, code, discount)
                VALUES (?, ?, ?)
            """, (user_id, code, discount))

    def get_user_promo(self, user_id: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM user_promos WHERE user_id = ?", (user_id,)
            ).fetchone()
        return dict(row) if row else None

    def use_promo(self, user_id: int):
        promo = self.get_user_promo(user_id)
        if promo:
            with self._conn() as conn:
                conn.execute(
                    "UPDATE promo_codes SET used_count = used_count + 1 WHERE code = ?",
                    (promo["code"],)
                )
                conn.execute("DELETE FROM user_promos WHERE user_id = ?", (user_id,))

    # ── Статистика ────────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        with self._conn() as conn:
            total_users = conn.execute("SELECT COUNT(*) as cnt FROM users").fetchone()["cnt"]
            now = datetime.utcnow().isoformat()
            active_subs = conn.execute(
                "SELECT COUNT(*) as cnt FROM subscriptions WHERE expires_at > ?", (now,)
            ).fetchone()["cnt"]

            plan_1m = conn.execute(
                "SELECT COUNT(*) as cnt FROM subscriptions WHERE plan_key = '1m'"
            ).fetchone()["cnt"]
            plan_3m = conn.execute(
                "SELECT COUNT(*) as cnt FROM subscriptions WHERE plan_key = '3m'"
            ).fetchone()["cnt"]
            plan_1y = conn.execute(
                "SELECT COUNT(*) as cnt FROM subscriptions WHERE plan_key = '1y'"
            ).fetchone()["cnt"]

        revenue = plan_1m * 10 + plan_3m * 25 + plan_1y * 90

        return {
            "total_users":   total_users,
            "active_subs":   active_subs,
            "total_revenue": revenue,
            "plan_1m":       plan_1m,
            "plan_3m":       plan_3m,
            "plan_1y":       plan_1y,
        }
