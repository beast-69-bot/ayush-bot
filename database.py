from __future__ import annotations

import os
import time
from typing import Any, Optional
import aiosqlite
import config

def _now() -> int:
    return int(time.time())

class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None
        self._settings_cache: dict[str, str | None] = {}
        self._admins_cache: set[int] | None = None

    async def init(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.execute("PRAGMA synchronous=NORMAL;")
        await self._conn.execute("PRAGMA foreign_keys=ON;")
        await self._ensure_schema()
        await self._seed_default_settings()

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None
        return self._conn

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def _ensure_schema(self) -> None:
        await self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
              user_id       INTEGER PRIMARY KEY,
              first_name    TEXT,
              username      TEXT,
              premium_until INTEGER NOT NULL DEFAULT 0,
              premium_daily_limit INTEGER NOT NULL DEFAULT 7,
              created_at    INTEGER NOT NULL,
              last_seen     INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS admins (
              user_id  INTEGER PRIMARY KEY,
              added_by INTEGER NOT NULL,
              added_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settings (
              key   TEXT PRIMARY KEY,
              value TEXT
            );

            CREATE TABLE IF NOT EXISTS bot_bans (
              user_id INTEGER PRIMARY KEY,
              banned_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reports (
              user_id INTEGER PRIMARY KEY,
              last_report REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS payment_requests (
              id            INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id       INTEGER NOT NULL,
              plan_key      TEXT NOT NULL,
              plan_days     INTEGER NOT NULL,
              amount_rs     INTEGER NOT NULL,
              projected_premium_until INTEGER,
              status        TEXT NOT NULL DEFAULT 'pending', -- pending|submitted|processed|rejected|expired
              utr_text      TEXT,
              user_chat_id  INTEGER,
              details_msg_id INTEGER,
              qr_msg_id     INTEGER,
              expires_at    INTEGER,
              created_at    INTEGER NOT NULL,
              updated_at    INTEGER NOT NULL,
              processed_by  INTEGER,
              processed_at  INTEGER,
              gateway_extra TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_payment_requests_user ON payment_requests(user_id, status);

            CREATE TABLE IF NOT EXISTS withdrawals (
              id            INTEGER PRIMARY KEY AUTOINCREMENT,
              requester_id  INTEGER NOT NULL,
              amount        INTEGER NOT NULL,
              status        TEXT NOT NULL DEFAULT 'pending',
              created_at    INTEGER NOT NULL,
              updated_at    INTEGER NOT NULL,
              processed_by  INTEGER
            );

            CREATE TABLE IF NOT EXISTS apk_keys (
              key           TEXT PRIMARY KEY,
              user_id       INTEGER,
              expiry_date   TEXT,
              is_active     INTEGER,
              devices       TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS auth_tokens (
              token         TEXT PRIMARY KEY,
              key           TEXT,
              expiry        REAL
            );

            CREATE TABLE IF NOT EXISTS admin_notifications (
              id            INTEGER PRIMARY KEY AUTOINCREMENT,
              request_id    INTEGER NOT NULL,
              admin_id      INTEGER NOT NULL,
              message_id    INTEGER NOT NULL,
              created_at    INTEGER NOT NULL
            );
            """
        )
        await self.conn.commit()

    async def _seed_default_settings(self) -> None:
        # Seed Owner Admin
        now = _now()
        if config.OWNER_ID:
            await self.conn.execute(
                "INSERT OR IGNORE INTO admins(user_id, added_by, added_at) VALUES(?, 0, ?)",
                (config.OWNER_ID, now)
            )
        
        # Seed default payment settings
        default_settings = {
            "payment_gateway": "manual",
            "pay_upi": config.UPI_ID,
            "pay_name": "Elite Premium Store",
            "pay_text": "Please pay the amount using the QR code above or UPI ID. After payment, click 'Submit UTR' and enter your 12-digit UTR/Transaction ID for verification.",
            "razorpay_key_id": config.RZP_KEY_ID,
            "razorpay_key_secret": config.RZP_KEY_SECRET,
        }
        for k, v in default_settings.items():
            if v is not None:
                await self.conn.execute(
                    "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
                    (k, str(v))
                )
        await self.conn.commit()

    # Users
    async def upsert_user(self, user_id: int, first_name: str | None, username: str | None) -> None:
        now = _now()
        await self.conn.execute(
            """
            INSERT INTO users(user_id, first_name, username, premium_until, created_at, last_seen)
            VALUES(?, ?, ?, 0, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
              first_name=excluded.first_name,
              username=excluded.username,
              last_seen=excluded.last_seen
            """,
            (int(user_id), first_name, username, now, now),
        )
        await self.conn.commit()

    async def is_premium_active(self, user_id: int) -> bool:
        now = _now()
        cur = await self.conn.execute("SELECT premium_until FROM users WHERE user_id=?", (int(user_id),))
        row = await cur.fetchone()
        await cur.close()
        return bool(row and int(row[0]) >= now)

    async def get_premium_until(self, user_id: int) -> int:
        cur = await self.conn.execute("SELECT premium_until FROM users WHERE user_id=?", (int(user_id),))
        row = await cur.fetchone()
        await cur.close()
        return int(row[0]) if row and row[0] is not None else 0

    async def get_user(self, user_id: int) -> Optional[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT user_id, first_name, username, premium_until, created_at, last_seen FROM users WHERE user_id=?",
            (int(user_id),),
        )
        row = await cur.fetchone()
        await cur.close()
        if not row:
            return None
        return {
            "user_id": int(row[0]),
            "first_name": row[1] or "",
            "username": row[2] or "",
            "premium_until": int(row[3]) if row[3] is not None else 0,
            "created_at": int(row[4]) if row[4] is not None else 0,
            "last_seen": int(row[5]) if row[5] is not None else 0,
        }

    async def add_premium_seconds(self, user_id: int, seconds: int) -> int:
        now = _now()
        cur = await self.conn.execute("SELECT premium_until FROM users WHERE user_id=?", (int(user_id),))
        row = await cur.fetchone()
        await cur.close()
        current = int(row[0]) if row else 0
        new_until = max(current, now) + int(seconds)
        await self.conn.execute(
            """
            INSERT INTO users(user_id, premium_until, created_at, last_seen)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET premium_until=excluded.premium_until, last_seen=excluded.last_seen
            """,
            (int(user_id), int(new_until), now, now),
        )
        await self.conn.commit()
        return int(new_until)

    async def set_premium_until(self, user_id: int, premium_until: int) -> None:
        now = _now()
        await self.conn.execute(
            """
            INSERT INTO users(user_id, premium_until, created_at, last_seen)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET premium_until=excluded.premium_until, last_seen=excluded.last_seen
            """,
            (int(user_id), int(premium_until), now, now),
        )
        await self.conn.commit()

    async def set_premium_daily_limit(self, user_id: int, daily_limit: int) -> None:
        now = _now()
        await self.conn.execute(
            """
            INSERT INTO users(user_id, premium_until, premium_daily_limit, created_at, last_seen)
            VALUES(?, 0, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET premium_daily_limit=excluded.premium_daily_limit, last_seen=excluded.last_seen
            """,
            (int(user_id), int(daily_limit), now, now),
        )
        await self.conn.commit()

    # Admins
    async def is_admin(self, user_id: int) -> bool:
        if self._admins_cache is None:
            self._admins_cache = set(await self.list_admin_ids())
        return int(user_id) in self._admins_cache

    async def add_admin(self, user_id: int, added_by: int = 0) -> None:
        if self._admins_cache is not None:
            self._admins_cache.add(int(user_id))
        now = _now()
        await self.conn.execute(
            "INSERT OR REPLACE INTO admins(user_id, added_by, added_at) VALUES(?, ?, ?)",
            (int(user_id), int(added_by), now),
        )
        await self.conn.commit()

    async def remove_admin(self, user_id: int) -> None:
        if self._admins_cache is not None:
            self._admins_cache.discard(int(user_id))
        await self.conn.execute("DELETE FROM admins WHERE user_id=?", (int(user_id),))
        await self.conn.commit()

    async def list_admin_ids(self) -> list[int]:
        cur = await self.conn.execute("SELECT user_id FROM admins")
        rows = await cur.fetchall()
        await cur.close()
        return [int(r[0]) for r in rows]

    # Settings
    async def set_setting(self, key: str, value: str | None) -> None:
        self._settings_cache[key] = value
        if value is None:
            await self.conn.execute("DELETE FROM settings WHERE key=?", (key,))
        else:
            await self.conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
        await self.conn.commit()

    async def get_setting(self, key: str) -> Optional[str]:
        if key in self._settings_cache:
            return self._settings_cache[key]
        cur = await self.conn.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = await cur.fetchone()
        await cur.close()
        val = row[0] if row else None
        self._settings_cache[key] = val
        return val

    # Bot Bans
    async def is_bot_banned(self, user_id: int) -> bool:
        cur = await self.conn.execute("SELECT 1 FROM bot_bans WHERE user_id=?", (int(user_id),))
        row = await cur.fetchone()
        await cur.close()
        return row is not None

    async def ban_from_bot(self, user_id: int) -> None:
        now = _now()
        await self.conn.execute(
            "INSERT OR IGNORE INTO bot_bans(user_id, banned_at) VALUES(?, ?)",
            (int(user_id), now)
        )
        await self.conn.execute(
            "UPDATE apk_keys SET is_active=0 WHERE user_id=?",
            (int(user_id),)
        )
        await self.conn.commit()

    async def unban_from_bot(self, user_id: int) -> None:
        await self.conn.execute("DELETE FROM bot_bans WHERE user_id=?", (int(user_id),))
        await self.conn.commit()

    # Faphouse License Keys
    async def generate_faphouse_key(self, user_id: int, plan_days: int) -> str:
        import random
        import string
        from datetime import datetime, timedelta
        
        chars = string.ascii_uppercase + string.digits
        key = "FAPH-" + '-'.join(''.join(random.choices(chars, k=4)) for _ in range(3))
        expiry = (datetime.now() + timedelta(days=plan_days)).strftime('%Y-%m-%d %H:%M:%S')
        
        await self.conn.execute(
            "INSERT INTO apk_keys (key, user_id, expiry_date, is_active, devices) VALUES (?, ?, ?, 1, '')",
            (key, int(user_id), expiry)
        )
        await self.conn.commit()
        return key

    async def get_active_user_keys(self, user_id: int) -> list[dict[str, Any]]:
        from datetime import datetime
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        async with self.conn.execute(
            "SELECT key, expiry_date, is_active FROM apk_keys WHERE user_id=? AND is_active=1 AND expiry_date > ?",
            (int(user_id), now_str)
        ) as cursor:
            rows = await cursor.fetchall()
            return [{"key": r[0], "expiry_date": r[1], "is_active": r[2]} for r in rows]

    # Reports
    async def add_report(self, user_id: int, timestamp: float) -> None:
        await self.conn.execute(
            "INSERT OR REPLACE INTO reports(user_id, last_report) VALUES(?, ?)",
            (int(user_id), timestamp)
        )
        await self.conn.commit()

    async def get_last_report(self, user_id: int) -> Optional[float]:
        cur = await self.conn.execute("SELECT last_report FROM reports WHERE user_id=?", (int(user_id),))
        row = await cur.fetchone()
        await cur.close()
        return row[0] if row else None

    # Payments
    async def create_payment_request(self, user_id: int, plan_key: str, plan_days: int, amount_rs: int) -> int:
        now = _now()
        expires_at = now + 300  # 5 minutes expiry
        current_until = await self.get_premium_until(int(user_id))
        projected_until = max(int(current_until), now) + (int(plan_days) * 24 * 60 * 60)
        cur = await self.conn.execute(
            """
            INSERT INTO payment_requests(
              user_id, plan_key, plan_days, amount_rs, projected_premium_until,
              status, expires_at, created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, 'pending', ?, ?, ?)
            """,
            (
                int(user_id),
                plan_key,
                int(plan_days),
                int(amount_rs),
                int(projected_until),
                int(expires_at),
                now,
                now,
            ),
        )
        await self.conn.commit()
        return int(cur.lastrowid)

    async def set_payment_utr(self, request_id: int, utr_text: str) -> bool:
        now = _now()
        cur = await self.conn.execute(
            """
            UPDATE payment_requests
            SET utr_text=?, status='submitted', updated_at=?
            WHERE id=? AND status IN ('pending', 'submitted')
            """,
            (utr_text, now, int(request_id)),
        )
        await self.conn.commit()
        return bool(cur.rowcount and cur.rowcount > 0)

    async def get_payment_request(self, request_id: int) -> Optional[dict[str, Any]]:
        cur = await self.conn.execute(
            """
            SELECT id, user_id, plan_key, plan_days, amount_rs, projected_premium_until, status, utr_text, user_chat_id, details_msg_id, qr_msg_id, expires_at, created_at, updated_at, processed_by, processed_at, gateway_extra
            FROM payment_requests
            WHERE id=?
            """,
            (int(request_id),),
        )
        row = await cur.fetchone()
        await cur.close()
        if not row:
            return None
        return {
            "id": int(row[0]),
            "user_id": int(row[1]),
            "plan_key": row[2],
            "plan_days": int(row[3]),
            "amount_rs": int(row[4]),
            "projected_premium_until": int(row[5]) if row[5] is not None else 0,
            "status": row[6],
            "utr_text": row[7],
            "user_chat_id": int(row[8]) if row[8] is not None else None,
            "details_msg_id": int(row[9]) if row[9] is not None else None,
            "qr_msg_id": int(row[10]) if row[10] is not None else None,
            "expires_at": int(row[11]) if row[11] is not None else 0,
            "created_at": int(row[12]),
            "updated_at": int(row[13]),
            "processed_by": row[14],
            "processed_at": row[15],
            "gateway_extra": row[16] if len(row) > 16 else None,
        }

    async def get_latest_open_payment_request(self, user_id: int) -> Optional[dict[str, Any]]:
        cur = await self.conn.execute(
            """
            SELECT id
            FROM payment_requests
            WHERE user_id=? AND status IN ('pending', 'submitted')
            ORDER BY id DESC
            LIMIT 1
            """,
            (int(user_id),),
        )
        row = await cur.fetchone()
        await cur.close()
        if not row:
            return None
        return await self.get_payment_request(int(row[0]))

    async def set_payment_ui_messages(self, request_id: int, user_chat_id: int, details_msg_id: int, qr_msg_id: int | None) -> None:
        now = _now()
        await self.conn.execute(
            """
            UPDATE payment_requests
            SET user_chat_id=?, details_msg_id=?, qr_msg_id=?, updated_at=?
            WHERE id=?
            """,
            (int(user_chat_id), int(details_msg_id), int(qr_msg_id) if qr_msg_id is not None else None, now, int(request_id)),
        )
        await self.conn.commit()

    async def clear_payment_ui_messages(self, request_id: int) -> None:
        now = _now()
        await self.conn.execute(
            """
            UPDATE payment_requests
            SET user_chat_id=NULL, details_msg_id=NULL, qr_msg_id=NULL, updated_at=?
            WHERE id=?
            """,
            (now, int(request_id)),
        )
        await self.conn.commit()

    async def set_payment_gateway_extra(self, request_id: int, gateway_extra: str) -> None:
        now = _now()
        await self.conn.execute(
            """
            UPDATE payment_requests
            SET gateway_extra=?, updated_at=?
            WHERE id=?
            """,
            (gateway_extra, now, int(request_id)),
        )
        await self.conn.commit()

    async def expire_payment_request_if_pending(self, request_id: int) -> bool:
        now = _now()
        cur = await self.conn.execute(
            """
            UPDATE payment_requests
            SET status='expired', updated_at=?
            WHERE id=? AND status='pending' AND (utr_text IS NULL OR TRIM(utr_text)='')
            """,
            (now, int(request_id)),
        )
        await self.conn.commit()
        return bool(cur.rowcount and cur.rowcount > 0)

    async def approve_payment_request(self, request_id: int, admin_id: int) -> bool:
        now = _now()
        cur = await self.conn.execute(
            """
            UPDATE payment_requests
            SET status='processed', processed_by=?, processed_at=?, updated_at=?
            WHERE id=? AND status IN ('submitted', 'pending')
            """,
            (int(admin_id), now, now, int(request_id)),
        )
        await self.conn.commit()
        return bool(cur.rowcount and cur.rowcount > 0)

    async def reject_payment_request(self, request_id: int, admin_id: int) -> bool:
        now = _now()
        cur = await self.conn.execute(
            """
            UPDATE payment_requests
            SET status='rejected', processed_by=?, processed_at=?, updated_at=?
            WHERE id=? AND status IN ('submitted', 'pending')
            """,
            (int(admin_id), now, now, int(request_id)),
        )
        await self.conn.commit()
        return bool(cur.rowcount and cur.rowcount > 0)

    async def list_pending_payment_requests(self) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            """
            SELECT id, user_id, plan_key, plan_days, amount_rs, projected_premium_until, status, utr_text, user_chat_id, details_msg_id, qr_msg_id, expires_at, created_at, updated_at, processed_by, processed_at, gateway_extra
            FROM payment_requests
            WHERE status='pending'
            """
        )
        rows = await cur.fetchall()
        await cur.close()
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append({
                "id": int(row[0]),
                "user_id": int(row[1]),
                "plan_key": row[2],
                "plan_days": int(row[3]),
                "amount_rs": int(row[4]),
                "projected_premium_until": int(row[5]) if row[5] is not None else 0,
                "status": row[6],
                "utr_text": row[7],
                "user_chat_id": int(row[8]) if row[8] is not None else None,
                "details_msg_id": int(row[9]) if row[9] is not None else None,
                "qr_msg_id": int(row[10]) if row[10] is not None else None,
                "expires_at": int(row[11]) if row[11] is not None else 0,
                "created_at": int(row[12]),
                "updated_at": int(row[13]),
                "processed_by": row[14],
                "processed_at": row[15],
                "gateway_extra": row[16] if len(row) > 16 else None,
            })
        return out

    async def list_processed_payment_requests(self, since_ts: int, until_ts: int) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            """
            SELECT id, user_id, plan_key, plan_days, amount_rs, processed_at
            FROM payment_requests
            WHERE status='processed'
              AND processed_at IS NOT NULL
              AND processed_at>=?
              AND processed_at<=?
            ORDER BY processed_at ASC, id ASC
            """,
            (int(since_ts), int(until_ts)),
        )
        rows = await cur.fetchall()
        await cur.close()
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "id": int(r[0]),
                    "user_id": int(r[1]),
                    "plan_key": str(r[2] or ""),
                    "plan_days": int(r[3]) if r[3] is not None else 0,
                    "amount_rs": int(r[4]) if r[4] is not None else 0,
                    "processed_at": int(r[5]) if r[5] is not None else 0,
                }
            )
        return out

    async def list_all_user_ids(self) -> list[int]:
        cur = await self.conn.execute("SELECT user_id FROM users")
        rows = await cur.fetchall()
        await cur.close()
        return [row[0] for row in rows]

    async def list_recent_payment_requests(self, limit: int = 10) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            """
            SELECT id, user_id, plan_key, plan_days, amount_rs, status, created_at
            FROM payment_requests
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cur.fetchall()
        await cur.close()
        out = []
        for r in rows:
            out.append({
                "id": int(r[0]),
                "user_id": int(r[1]),
                "plan_key": r[2],
                "plan_days": int(r[3]),
                "amount_rs": int(r[4]),
                "status": r[5],
                "created_at": int(r[6]),
            })
        return out

    async def get_payment_stats(self) -> dict[str, int]:
        cur = await self.conn.execute(
            """
            SELECT status, COUNT(*)
            FROM payment_requests
            GROUP BY status
            """
        )
        rows = await cur.fetchall()
        await cur.close()
        stats = {"pending": 0, "submitted": 0, "processed": 0, "rejected": 0, "expired": 0}
        for row in rows:
            status = row[0]
            if status in stats:
                stats[status] = row[1]
        return stats

    async def list_all_processed_payments(self) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            """
            SELECT id, user_id, plan_key, amount_rs, processed_by, processed_at, gateway_extra
            FROM payment_requests
            WHERE status='processed'
            """
        )
        rows = await cur.fetchall()
        await cur.close()
        out = []
        for r in rows:
            out.append({
                "id": int(r[0]),
                "user_id": int(r[1]),
                "plan_key": r[2],
                "amount_rs": int(r[3]),
                "processed_by": r[4],
                "processed_at": r[5],
                "gateway_extra": r[6],
            })
        return out

    async def create_withdrawal_request(self, requester_id: int, amount: int) -> int:
        now = _now()
        cur = await self.conn.execute(
            """
            INSERT INTO withdrawals(requester_id, amount, status, created_at, updated_at)
            VALUES(?, ?, 'pending', ?, ?)
            """,
            (int(requester_id), int(amount), now, now)
        )
        await self.conn.commit()
        return int(cur.lastrowid)

    async def get_withdrawal_request(self, wid: int) -> Optional[dict[str, Any]]:
        cur = await self.conn.execute(
            """
            SELECT id, requester_id, amount, status, created_at, updated_at, processed_by
            FROM withdrawals
            WHERE id=?
            """,
            (int(wid),)
        )
        row = await cur.fetchone()
        await cur.close()
        if not row:
            return None
        return {
            "id": int(row[0]),
            "requester_id": int(row[1]),
            "amount": int(row[2]),
            "status": row[3],
            "created_at": int(row[4]),
            "updated_at": int(row[5]),
            "processed_by": int(row[6]) if row[6] is not None else None
        }

    async def approve_withdrawal_request(self, wid: int, processed_by: int) -> bool:
        now = _now()
        cur = await self.conn.execute(
            """
            UPDATE withdrawals
            SET status='approved', processed_by=?, updated_at=?
            WHERE id=? AND status='pending'
            """,
            (int(processed_by), now, int(wid))
        )
        await self.conn.commit()
        return bool(cur.rowcount and cur.rowcount > 0)

    async def reject_withdrawal_request(self, wid: int, processed_by: int) -> bool:
        now = _now()
        cur = await self.conn.execute(
            """
            UPDATE withdrawals
            SET status='rejected', processed_by=?, updated_at=?
            WHERE id=? AND status='pending'
            """,
            (int(processed_by), now, int(wid))
        )
        await self.conn.commit()
        return bool(cur.rowcount and cur.rowcount > 0)

    async def get_total_withdrawn(self) -> int:
        cur = await self.conn.execute(
            """
            SELECT SUM(amount)
            FROM withdrawals
            WHERE status='approved'
            """
        )
        row = await cur.fetchone()
        await cur.close()
        return int(row[0]) if row and row[0] is not None else 0

    async def get_daily_revenue_breakdown(self) -> dict[str, dict[str, Any]]:
        import json
        import datetime
        cur = await self.conn.execute(
            """
            SELECT amount_rs, processed_at, gateway_extra
            FROM payment_requests
            WHERE status='processed'
            ORDER BY processed_at DESC
            """
        )
        rows = await cur.fetchall()
        await cur.close()
        
        daily: dict[str, dict[str, Any]] = {}
        for row in rows:
            amount_rs = int(row[0])
            processed_at = row[1]
            gateway_extra_str = row[2]
            
            gw = "manual"
            if gateway_extra_str:
                try:
                    gw_extra = json.loads(gateway_extra_str)
                    gw = gw_extra.get("gateway", "manual")
                except Exception:
                    if "razorpay" in str(gateway_extra_str).lower():
                        gw = "razorpay"
                    elif "stars" in str(gateway_extra_str).lower():
                        gw = "stars"
                        
            date_str = datetime.datetime.utcfromtimestamp(processed_at).strftime("%Y-%m-%d")
            
            if date_str not in daily:
                daily[date_str] = {
                    "date": date_str,
                    "total_rs": 0,
                    "manual": 0,
                    "razorpay": 0,
                    "stars": 0,
                    "count": 0
                }
                
            daily[date_str]["count"] += 1
            if gw == "stars":
                daily[date_str]["stars"] += amount_rs
            elif gw == "razorpay":
                daily[date_str]["razorpay"] += amount_rs
                daily[date_str]["total_rs"] += amount_rs
            else:
                daily[date_str]["manual"] += amount_rs
                daily[date_str]["total_rs"] += amount_rs
                
        return daily

    async def get_active_plans(self) -> dict[str, Any]:
        import config
        import copy
        plans = {}
        for plan_key, plan_val in config.PAY_PLANS.items():
            plan_copy = copy.deepcopy(plan_val)
            
            # Check price override
            price_override = await self.get_setting(f"plan_price:{plan_key}")
            if price_override is not None:
                try:
                    plan_copy["amount"] = int(price_override)
                except ValueError:
                    pass
                    
            # Check stars override
            stars_override = await self.get_setting(f"plan_stars:{plan_key}")
            if stars_override is not None:
                try:
                    plan_copy["stars"] = int(stars_override)
                except ValueError:
                    pass

            # Check status override
            status_override = await self.get_setting(f"plan_status:{plan_key}")
            plan_copy["status"] = status_override or "active"

            # Check limit override
            limit_override = await self.get_setting(f"plan_limit:{plan_key}")
            if limit_override is not None:
                try:
                    plan_copy["limit"] = int(limit_override)
                except ValueError:
                    plan_copy["limit"] = None
            else:
                plan_copy["limit"] = None
                
            # Populate sold count
            plan_copy["sold_count"] = await self.get_plan_sales_count(plan_key)
            
            plans[plan_key] = plan_copy
        return plans

    async def add_admin_notification(self, request_id: int, admin_id: int, message_id: int) -> None:
        now = _now()
        await self.conn.execute(
            "INSERT INTO admin_notifications(request_id, admin_id, message_id, created_at) VALUES(?, ?, ?, ?)",
            (int(request_id), int(admin_id), int(message_id), now)
        )
        await self.conn.commit()

    async def list_admin_notifications(self, request_id: int) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT admin_id, message_id FROM admin_notifications WHERE request_id=?",
            (int(request_id),)
        )
        rows = await cur.fetchall()
        await cur.close()
        return [{"admin_id": r[0], "message_id": r[1]} for r in rows]

    async def get_plan_sales_count(self, plan_key: str) -> int:
        cur = await self.conn.execute(
            "SELECT COUNT(*) FROM payment_requests WHERE plan_key=? AND status='processed'",
            (plan_key,)
        )
        row = await cur.fetchone()
        await cur.close()
        return row[0] if row else 0

