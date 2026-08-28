from __future__ import annotations

import aiosqlite
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database not initialized")
        return self._conn

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path.as_posix())
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(
            """
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT NOT NULL,
                balance INTEGER NOT NULL DEFAULT 0,
                plan TEXT NOT NULL DEFAULT 'Free',
                plan_limit INTEGER NOT NULL DEFAULT 1,
                plan_expires_at TEXT,
                banned INTEGER NOT NULL DEFAULT 0,
                referred_by INTEGER,
                referrals_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                last_seen TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS owners (
                user_id INTEGER PRIMARY KEY,
                label TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS required_chats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                invite_link TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'channel'
            );

            CREATE TABLE IF NOT EXISTS redeem_codes (
                code TEXT PRIMARY KEY,
                amount INTEGER NOT NULL,
                max_uses INTEGER NOT NULL DEFAULT 1,
                used_count INTEGER NOT NULL DEFAULT 0,
                created_by INTEGER,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                source_path TEXT NOT NULL,
                entry_point TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'stopped',
                process_pid INTEGER,
                started_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS user_actions (
                user_id INTEGER PRIMARY KEY,
                action TEXT NOT NULL,
                payload TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS plans (
                plan_name TEXT PRIMARY KEY,
                price INTEGER NOT NULL,
                max_bots INTEGER NOT NULL,
                description TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        await self.conn.commit()
        columns = await self.conn.execute_fetchall("PRAGMA table_info(users)")
        if not any(row["name"] == "plan_expires_at" for row in columns):
            await self.conn.execute("ALTER TABLE users ADD COLUMN plan_expires_at TEXT")
            await self.conn.commit()
        await self.seed_defaults()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def seed_defaults(self) -> None:
        await self.conn.executemany(
            """
            INSERT OR IGNORE INTO plans (plan_name, price, max_bots, description)
            VALUES (?, ?, ?, ?)
            """,
            [
                ("Free", 0, 1, "1 bot aktif"),
                ("Pro", 200, 2, "Hosting 2 bot"),
                ("VIP", 1000, 5, "Hosting 5 bot"),
            ],
        )
        await self.conn.commit()

    async def ensure_user(self, user_id: int, username: str | None, full_name: str) -> None:
        now = utc_now()
        await self.conn.execute(
            """
            INSERT INTO users (user_id, username, full_name, created_at, last_seen)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                full_name=excluded.full_name,
                last_seen=excluded.last_seen
            """,
            (user_id, username, full_name, now, now),
        )
        await self.conn.commit()

    async def update_last_seen(self, user_id: int) -> None:
        await self.conn.execute("UPDATE users SET last_seen=? WHERE user_id=?", (utc_now(), user_id))
        await self.conn.commit()

    async def get_user(self, user_id: int) -> aiosqlite.Row | None:
        cur = await self.conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        if row and row["plan_expires_at"]:
            try:
                expired = datetime.fromisoformat(row["plan_expires_at"]) <= datetime.now(timezone.utc)
            except ValueError:
                expired = True
            if expired:
                await self.conn.execute(
                    "UPDATE users SET plan='Free', plan_limit=1, plan_expires_at=NULL WHERE user_id=?",
                    (user_id,),
                )
                await self.conn.commit()
                cur = await self.conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
                row = await cur.fetchone()
        return row

    async def get_users(self, limit: int = 20) -> list[aiosqlite.Row]:
        cur = await self.conn.execute(
            "SELECT * FROM users ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return await cur.fetchall()

    async def count_users(self) -> int:
        cur = await self.conn.execute("SELECT COUNT(*) AS count FROM users")
        row = await cur.fetchone()
        return int(row["count"]) if row else 0

    async def set_action(self, user_id: int, action: str, payload: str | None = None) -> None:
        await self.conn.execute(
            """
            INSERT INTO user_actions (user_id, action, payload, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                action=excluded.action,
                payload=excluded.payload,
                updated_at=excluded.updated_at
            """,
            (user_id, action, payload, utc_now()),
        )
        await self.conn.commit()

    async def get_action(self, user_id: int) -> dict[str, Any] | None:
        cur = await self.conn.execute("SELECT * FROM user_actions WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def clear_action(self, user_id: int) -> None:
        await self.conn.execute("DELETE FROM user_actions WHERE user_id=?", (user_id,))
        await self.conn.commit()

    async def get_plan(self, plan_name: str) -> aiosqlite.Row | None:
        cur = await self.conn.execute("SELECT * FROM plans WHERE plan_name=?", (plan_name,))
        return await cur.fetchone()

    async def list_plans(self) -> list[aiosqlite.Row]:
        cur = await self.conn.execute("SELECT * FROM plans ORDER BY price ASC")
        return await cur.fetchall()

    async def set_plan(self, user_id: int, plan: str, plan_limit: int) -> None:
        await self.conn.execute(
            "UPDATE users SET plan=?, plan_limit=? WHERE user_id=?",
            (plan, plan_limit, user_id),
        )
        await self.conn.commit()

    async def purchase_plan(self, user_id: int, plan_name: str, quantity: int) -> tuple[bool, int, int, int]:
        plan = await self.get_plan(plan_name)
        user = await self.get_user(user_id)
        if plan is None or user is None or quantity < 1:
            return False, 0, 0, 0

        price = int(plan["price"]) * quantity
        balance = int(user["balance"])
        if balance < price:
            return False, balance, price, int(user["plan_limit"])

        old_limit = int(user["plan_limit"])
        new_limit = int(plan["max_bots"]) * quantity if user["plan"] == "Free" else old_limit + int(plan["max_bots"]) * quantity
        now = datetime.now(timezone.utc)
        current_expiry = user["plan_expires_at"]
        if current_expiry:
            try:
                base = max(now, datetime.fromisoformat(current_expiry))
            except ValueError:
                base = now
        else:
            base = now
        expires_at = (base + timedelta(days=30)).isoformat()
        await self.conn.execute(
            "UPDATE users SET balance=?, plan=?, plan_limit=?, plan_expires_at=? WHERE user_id=?",
            (balance - price, plan_name, new_limit, expires_at, user_id),
        )
        await self.conn.commit()
        return True, balance - price, price, new_limit

    async def add_balance(self, user_id: int, amount: int) -> None:
        await self.conn.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id=?",
            (amount, user_id),
        )
        await self.conn.commit()

    async def set_balance(self, user_id: int, amount: int) -> None:
        await self.conn.execute("UPDATE users SET balance=? WHERE user_id=?", (amount, user_id))
        await self.conn.commit()

    async def ban_user(self, user_id: int, banned: bool = True) -> None:
        await self.conn.execute(
            "UPDATE users SET banned=? WHERE user_id=?",
            (1 if banned else 0, user_id),
        )
        await self.conn.commit()

    async def count_user_bots(self, user_id: int) -> int:
        cur = await self.conn.execute(
            "SELECT COUNT(*) AS count FROM bots WHERE owner_id=? AND status IN ('running', 'starting')",
            (user_id,),
        )
        row = await cur.fetchone()
        return int(row["count"]) if row else 0

    async def list_user_bots(self, user_id: int) -> list[aiosqlite.Row]:
        cur = await self.conn.execute(
            "SELECT * FROM bots WHERE owner_id=? ORDER BY created_at DESC",
            (user_id,),
        )
        return await cur.fetchall()

    async def list_all_bots(self) -> list[aiosqlite.Row]:
        cur = await self.conn.execute("SELECT * FROM bots ORDER BY created_at DESC")
        return await cur.fetchall()

    async def get_bot(self, bot_id: int) -> aiosqlite.Row | None:
        cur = await self.conn.execute("SELECT * FROM bots WHERE id=?", (bot_id,))
        return await cur.fetchone()

    async def create_bot(
        self,
        owner_id: int,
        name: str,
        kind: str,
        source_path: str,
        entry_point: str,
    ) -> int:
        now = utc_now()
        cur = await self.conn.execute(
            """
            INSERT INTO bots (owner_id, name, kind, source_path, entry_point, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'stopped', ?, ?)
            """,
            (owner_id, name, kind, source_path, entry_point, now, now),
        )
        await self.conn.commit()
        return int(cur.lastrowid)

    async def update_bot_status(
        self,
        bot_id: int,
        status: str,
        process_pid: int | None = None,
        started_at: str | None = None,
    ) -> None:
        await self.conn.execute(
            """
            UPDATE bots
            SET status=?, process_pid=?, started_at=?, updated_at=?
            WHERE id=?
            """,
            (status, process_pid, started_at, utc_now(), bot_id),
        )
        await self.conn.commit()

    async def delete_bot(self, bot_id: int) -> None:
        await self.conn.execute("DELETE FROM bots WHERE id=?", (bot_id,))
        await self.conn.commit()

    async def update_bot_entrypoint(self, bot_id: int, entry_point: str) -> None:
        await self.conn.execute(
            "UPDATE bots SET entry_point=?, updated_at=? WHERE id=?",
            (entry_point, utc_now(), bot_id),
        )
        await self.conn.commit()

    async def get_app_setting(self, key: str) -> str | None:
        cur = await self.conn.execute("SELECT value FROM app_settings WHERE key=?", (key,))
        row = await cur.fetchone()
        return row["value"] if row else None

    async def set_app_setting(self, key: str, value: str) -> None:
        await self.conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        await self.conn.commit()

    async def stop_all_bots(self) -> None:
        await self.conn.execute(
            "UPDATE bots SET status='stopped', process_pid=NULL, started_at=NULL, updated_at=?",
            (utc_now(),),
        )
        await self.conn.commit()

    async def reset_data(self) -> None:
        await self.conn.executescript(
            """
            DELETE FROM users;
            DELETE FROM user_actions;
            DELETE FROM redeem_codes;
            DELETE FROM bots;
            DELETE FROM owners;
            DELETE FROM required_chats;
            DELETE FROM plans;
            DELETE FROM app_settings;
            """
        )
        await self.conn.commit()
        await self.seed_defaults()

    async def add_required_chat(self, chat_id: str, title: str, invite_link: str, kind: str) -> None:
        await self.conn.execute(
            """
            INSERT INTO required_chats (chat_id, title, invite_link, kind)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                title=excluded.title,
                invite_link=excluded.invite_link,
                kind=excluded.kind
            """,
            (chat_id, title, invite_link, kind),
        )
        await self.conn.commit()

    async def list_required_chats(self) -> list[aiosqlite.Row]:
        cur = await self.conn.execute("SELECT * FROM required_chats ORDER BY id ASC")
        return await cur.fetchall()

    async def delete_required_chat(self, chat_id: str) -> None:
        await self.conn.execute("DELETE FROM required_chats WHERE chat_id=?", (chat_id,))
        await self.conn.commit()

    async def create_redeem_code(self, code: str, amount: int, max_uses: int, created_by: int | None) -> None:
        await self.conn.execute(
            """
            INSERT INTO redeem_codes (code, amount, max_uses, used_count, created_by, created_at)
            VALUES (?, ?, ?, 0, ?, ?)
            """,
            (code, amount, max_uses, created_by, utc_now()),
        )
        await self.conn.commit()

    async def use_redeem_code(self, code: str) -> int | None:
        cur = await self.conn.execute("SELECT * FROM redeem_codes WHERE code=?", (code,))
        row = await cur.fetchone()
        if row is None:
            return None
        if int(row["used_count"]) >= int(row["max_uses"]):
            return -1
        await self.conn.execute(
            "UPDATE redeem_codes SET used_count = used_count + 1 WHERE code=?",
            (code,),
        )
        await self.conn.commit()
        return int(row["amount"])

    async def add_owner(self, user_id: int, label: str) -> None:
        await self.conn.execute(
            """
            INSERT INTO owners (user_id, label)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET label=excluded.label
            """,
            (user_id, label),
        )
        await self.conn.commit()

    async def delete_owner(self, user_id: int) -> None:
        await self.conn.execute("DELETE FROM owners WHERE user_id=?", (user_id,))
        await self.conn.commit()

    async def list_owners(self) -> list[aiosqlite.Row]:
        cur = await self.conn.execute("SELECT * FROM owners ORDER BY user_id ASC")
        return await cur.fetchall()
