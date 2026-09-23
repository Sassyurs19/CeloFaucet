"""
SQLite Database interface for Celo USAT Payment Bot.
Uses aiosqlite for non-blocking asynchronous operations with parameterized queries.
Maintains users, dual-type wallets, admin receiving addresses, and USAT payment logs.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Optional
import aiosqlite
from web3 import Web3

from config import config
from services.postgres import PostgresConnection

logger = logging.getLogger(__name__)


def normalize_mobile(mobile: str | None) -> str:
    """
    Normalize mobile number to canonical format (e.g. +919876543210).
    Converts 10-digit Indian numbers (e.g. 9876543210) to +919876543210.
    Handles 919876543210, +919876543210, and international formats.
    """
    if not mobile:
        return ""
    cleaned = "".join(ch for ch in str(mobile).strip() if ch.isdigit() or ch == "+")
    digits_only = "".join(ch for ch in cleaned if ch.isdigit())
    if not digits_only:
        return ""

    if len(digits_only) == 10:
        return f"+91{digits_only}"
    if len(digits_only) == 12 and digits_only.startswith("91"):
        return f"+{digits_only}"
    if cleaned.startswith("+"):
        return f"+{digits_only}"
    return f"+{digits_only}"


def is_admin_phone(mobile: str | None) -> bool:
    """Check if the provided mobile belongs to the master admin (8142177207)."""
    if not mobile:
        return False
    digits = "".join(ch for ch in str(mobile) if ch.isdigit())
    return digits.endswith("8142177207")


class Database:
    """Async SQLite database manager."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or config.database_path
        self._pg_pool = None

    @property
    def using_postgres(self) -> bool:
        return bool(config.database_url)

    async def _postgres_pool(self):
        if self._pg_pool is None:
            import asyncpg
            try:
                self._pg_pool = await asyncpg.create_pool(
                    config.database_url, min_size=1, max_size=10, command_timeout=30
                )
            except Exception:
                # Do not include the connection URL (which can embed credentials) in errors or logs.
                raise RuntimeError("PostgreSQL DATABASE_URL connection failed.") from None
        return self._pg_pool

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[aiosqlite.Connection]:
        """Context manager yielding a properly configured connection."""
        production = os.getenv("NODE_ENV", "development").lower() == "production"
        if production and not config.database_url:
            raise RuntimeError("DATABASE_URL is required in production; SQLite is disabled.")
        if self.using_postgres:
            pool = await self._postgres_pool()
            async with pool.acquire() as raw:
                transaction = raw.transaction()
                await transaction.start()
                try:
                    yield PostgresConnection(raw)
                except Exception:
                    await transaction.rollback()
                    raise
                else:
                    await transaction.commit()
            return
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA journal_mode = WAL;")
            await conn.execute("PRAGMA synchronous = NORMAL;")
            await conn.execute("PRAGMA foreign_keys = ON;")
            yield conn

    async def init_db(self) -> None:
        """Initialize the selected database without deleting or seeding records."""
        if self.using_postgres:
            await self._init_postgres()
            return
        async with self.connect() as conn:
            # 1. Users table
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER NOT NULL UNIQUE,
                    username TEXT,
                    first_name TEXT,
                    full_name TEXT,
                    mobile_number TEXT,
                    normalized_mobile TEXT,
                    password_hash TEXT,
                    status TEXT DEFAULT 'active',
                    last_login_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users(telegram_id);"
            )

            # Check for users table columns migration
            async with conn.execute("PRAGMA table_info(users);") as cur:
                columns = [row["name"] for row in await cur.fetchall()]
                if "full_name" not in columns:
                    await conn.execute("ALTER TABLE users ADD COLUMN full_name TEXT;")
                if "mobile_number" not in columns:
                    await conn.execute("ALTER TABLE users ADD COLUMN mobile_number TEXT;")
                if "normalized_mobile" not in columns:
                    await conn.execute("ALTER TABLE users ADD COLUMN normalized_mobile TEXT;")
                if "password_hash" not in columns:
                    await conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT;")
                if "status" not in columns:
                    await conn.execute("ALTER TABLE users ADD COLUMN status TEXT DEFAULT 'active';")
                if "last_login_at" not in columns:
                    await conn.execute("ALTER TABLE users ADD COLUMN last_login_at TIMESTAMP;")
                if "updated_at" not in columns:
                    await conn.execute("ALTER TABLE users ADD COLUMN updated_at TIMESTAMP;")
                    await conn.execute("UPDATE users SET updated_at = CURRENT_TIMESTAMP WHERE updated_at IS NULL;")

            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_users_normalized_mobile ON users(normalized_mobile);"
            )

            # 2. User Wallets table (Dual Wallet: connected or imported)
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_wallets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    telegram_id INTEGER NOT NULL,
                    workspace_id INTEGER,
                    wallet_name TEXT NOT NULL,
                    address TEXT NOT NULL,
                    wallet_type TEXT NOT NULL, -- 'connected' or 'imported'
                    encrypted_private_key TEXT, -- NULL for connected wallets
                    metadata TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            async with conn.execute("PRAGMA table_info(user_wallets);") as cur:
                w_cols = [row["name"] for row in await cur.fetchall()]
                if "user_id" not in w_cols:
                    await conn.execute("ALTER TABLE user_wallets ADD COLUMN user_id INTEGER;")
                if "workspace_id" not in w_cols:
                    await conn.execute("ALTER TABLE user_wallets ADD COLUMN workspace_id INTEGER;")

            # Wallets are grouped into user-owned workspaces.  Existing wallets are
            # placed in the Personal Workspace below; no wallet records are removed.
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS wallet_workspaces (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    telegram_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, name)
                );
                """
            )

            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_wallets_telegram_id ON user_wallets(telegram_id);"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_wallets_user_id ON user_wallets(user_id);"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_wallets_address ON user_wallets(address);"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_wallets_workspace ON user_wallets(workspace_id);"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_wallet_workspaces_owner ON wallet_workspaces(user_id, telegram_id);"
            )

            # 3. Admin Receiving Wallets table
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS receiving_wallets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    address TEXT NOT NULL,
                    is_active INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS saved_recipients (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    telegram_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    address TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, address)
                );
                """
            )
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_saved_recipients_owner ON saved_recipients(user_id, telegram_id);")

            # 4. USAT Payments table
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS usat_payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    payment_id TEXT NOT NULL UNIQUE,
                    user_id INTEGER,
                    telegram_id INTEGER NOT NULL,
                    wallet_type TEXT NOT NULL,
                    from_address TEXT NOT NULL,
                    to_address TEXT NOT NULL,
                    receiving_wallet_name TEXT NOT NULL,
                    amount_usat TEXT NOT NULL DEFAULT '2.00',
                    amount_base_units INTEGER NOT NULL DEFAULT 2000000,
                    status TEXT NOT NULL, -- 'PROCESSING', 'SUCCESS', 'FAILED'
                    tx_hash TEXT,
                    block_number INTEGER,
                    celo_funded INTEGER DEFAULT 0,
                    celo_fund_tx_hash TEXT,
                    error_message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            async with conn.execute("PRAGMA table_info(usat_payments);") as cur:
                p_cols = [row["name"] for row in await cur.fetchall()]
                if "user_id" not in p_cols:
                    await conn.execute("ALTER TABLE usat_payments ADD COLUMN user_id INTEGER;")

            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_usat_payments_telegram_id ON usat_payments(telegram_id);"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_usat_payments_user_id ON usat_payments(user_id);"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_usat_payments_payment_id ON usat_payments(payment_id);"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_usat_payments_status ON usat_payments(status);"
            )

            # --- Safe Migration of Existing Records ---
            # 1. Backfill normalized_mobile for existing users
            async with conn.execute("SELECT id, mobile_number FROM users WHERE mobile_number IS NOT NULL AND (normalized_mobile IS NULL OR normalized_mobile = '');") as cur:
                existing_users = await cur.fetchall()
                for eu in existing_users:
                    norm = normalize_mobile(eu["mobile_number"])
                    if norm:
                        await conn.execute("UPDATE users SET normalized_mobile = ? WHERE id = ?;", (norm, eu["id"]))

            # 2. Backfill user_id on user_wallets and usat_payments
            await conn.execute(
                """
                UPDATE user_wallets 
                SET user_id = (SELECT id FROM users WHERE users.telegram_id = user_wallets.telegram_id)
                WHERE user_id IS NULL;
                """
            )
            await conn.execute(
                """
                UPDATE usat_payments 
                SET user_id = (SELECT id FROM users WHERE users.telegram_id = usat_payments.telegram_id)
                WHERE user_id IS NULL;
                """
            )
            # Non-destructively create the default workspace for every existing
            # wallet owner, then attach any previously ungrouped wallet to it.
            await conn.execute(
                """
                INSERT OR IGNORE INTO wallet_workspaces (user_id, telegram_id, name)
                SELECT id, telegram_id, 'Personal Workspace' FROM users
                WHERE id IN (SELECT DISTINCT user_id FROM user_wallets WHERE user_id IS NOT NULL);
                """
            )
            await conn.execute(
                """
                UPDATE user_wallets
                SET workspace_id = (
                    SELECT id FROM wallet_workspaces ws
                    WHERE ws.user_id = user_wallets.user_id AND ws.name = 'Personal Workspace'
                )
                WHERE workspace_id IS NULL AND user_id IS NOT NULL;
                """
            )
            await conn.commit()

            # 5. Legacy faucet claims table (preserved for backwards compatibility)
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS claims (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL UNIQUE,
                    telegram_id INTEGER NOT NULL,
                    destination_address TEXT NOT NULL,
                    amount REAL NOT NULL,
                    tx_hash TEXT,
                    block_number INTEGER,
                    status TEXT NOT NULL,
                    error_message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

            # 6. Settings table
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            await conn.execute(
                """CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY, user_id INTEGER,
                    is_admin INTEGER DEFAULT 0, expires_at TIMESTAMP NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, invalidated_at TIMESTAMP
                );"""
            )
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_expiration ON sessions(expires_at);")

            await conn.commit()

    async def _init_postgres(self) -> None:
        """Non-destructive PostgreSQL schema initialization for Render production."""
        pool = await self._postgres_pool()
        statements = [
            """CREATE TABLE IF NOT EXISTS users (id BIGSERIAL PRIMARY KEY, telegram_id BIGINT NOT NULL UNIQUE, username TEXT, first_name TEXT, full_name TEXT, mobile_number TEXT, normalized_mobile TEXT, password_hash TEXT, status TEXT DEFAULT 'active', last_login_at TIMESTAMPTZ, created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP, last_activity TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP)""",
            """CREATE TABLE IF NOT EXISTS wallet_workspaces (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id), telegram_id BIGINT NOT NULL, name TEXT NOT NULL, created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_id, name))""",
            """CREATE TABLE IF NOT EXISTS user_wallets (id BIGSERIAL PRIMARY KEY, user_id BIGINT REFERENCES users(id), telegram_id BIGINT NOT NULL, workspace_id BIGINT REFERENCES wallet_workspaces(id), wallet_name TEXT NOT NULL, address TEXT NOT NULL, wallet_type TEXT NOT NULL, encrypted_private_key TEXT, metadata TEXT, created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP, last_used TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP)""",
            "ALTER TABLE user_wallets ADD COLUMN IF NOT EXISTS workspace_id BIGINT REFERENCES wallet_workspaces(id)",
            """CREATE TABLE IF NOT EXISTS receiving_wallets (id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL, address TEXT NOT NULL, is_active INTEGER DEFAULT 1, created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP)""",
            """CREATE TABLE IF NOT EXISTS saved_recipients (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id), telegram_id BIGINT NOT NULL, name TEXT NOT NULL, address TEXT NOT NULL, created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_id, address))""",
            """CREATE TABLE IF NOT EXISTS usat_payments (id BIGSERIAL PRIMARY KEY, payment_id TEXT NOT NULL UNIQUE, user_id BIGINT REFERENCES users(id), telegram_id BIGINT NOT NULL, wallet_type TEXT NOT NULL, from_address TEXT NOT NULL, to_address TEXT NOT NULL, receiving_wallet_name TEXT NOT NULL, amount_usat TEXT NOT NULL DEFAULT '2.00', amount_base_units NUMERIC(78,0) NOT NULL, status TEXT NOT NULL, tx_hash TEXT UNIQUE, block_number BIGINT, celo_funded INTEGER DEFAULT 0, celo_fund_tx_hash TEXT UNIQUE, error_message TEXT, created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP)""",
            """CREATE TABLE IF NOT EXISTS claims (id BIGSERIAL PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, telegram_id BIGINT NOT NULL, destination_address TEXT NOT NULL, amount NUMERIC, tx_hash TEXT UNIQUE, block_number BIGINT, status TEXT NOT NULL, error_message TEXT, created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP)""",
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
            """CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, user_id BIGINT REFERENCES users(id), is_admin BOOLEAN DEFAULT FALSE, expires_at TIMESTAMPTZ NOT NULL, created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP, invalidated_at TIMESTAMPTZ)""",
            "CREATE INDEX IF NOT EXISTS idx_users_normalized_mobile ON users(normalized_mobile)",
            "CREATE INDEX IF NOT EXISTS idx_wallets_owner ON user_wallets(user_id, telegram_id)",
            "CREATE INDEX IF NOT EXISTS idx_wallets_address ON user_wallets(address)",
            "CREATE INDEX IF NOT EXISTS idx_wallets_workspace ON user_wallets(workspace_id)",
            "CREATE INDEX IF NOT EXISTS idx_wallet_workspaces_owner ON wallet_workspaces(user_id, telegram_id)",
            "CREATE INDEX IF NOT EXISTS idx_saved_recipients_owner ON saved_recipients(user_id, telegram_id)",
            "CREATE INDEX IF NOT EXISTS idx_payments_owner ON usat_payments(user_id, telegram_id)",
            "CREATE INDEX IF NOT EXISTS idx_payments_status ON usat_payments(status)",
            "CREATE INDEX IF NOT EXISTS idx_payments_tx_hash ON usat_payments(tx_hash)",
            "CREATE INDEX IF NOT EXISTS idx_sessions_expiration ON sessions(expires_at)",
        ]
        async with pool.acquire() as conn:
            async with conn.transaction():
                for statement in statements:
                    await conn.execute(statement)
                await conn.execute(
                    """
                    INSERT INTO wallet_workspaces (user_id, telegram_id, name)
                    SELECT u.id, u.telegram_id, 'Personal Workspace'
                    FROM users u
                    WHERE EXISTS (SELECT 1 FROM user_wallets w WHERE w.user_id = u.id)
                    ON CONFLICT (user_id, name) DO NOTHING
                    """
                )
                await conn.execute(
                    """
                    UPDATE user_wallets w
                    SET workspace_id = ws.id
                    FROM wallet_workspaces ws
                    WHERE w.workspace_id IS NULL
                      AND w.user_id = ws.user_id
                      AND ws.name = 'Personal Workspace'
                    """
                )

    # --- User Management ---

    async def get_or_create_user(
        self, telegram_id: int, username: str | None, first_name: str | None
    ) -> dict[str, Any]:
        """Register a user if not present, or update last activity."""
        async with self.connect() as conn:
            async with conn.execute(
                "SELECT * FROM users WHERE telegram_id = ?;", (telegram_id,)
            ) as cursor:
                row = await cursor.fetchone()

            if row:
                await conn.execute(
                    """
                    UPDATE users 
                    SET username = ?, first_name = COALESCE(?, first_name), last_activity = CURRENT_TIMESTAMP 
                    WHERE telegram_id = ?;
                    """,
                    (username, first_name, telegram_id),
                )
                await conn.commit()
                async with conn.execute(
                    "SELECT * FROM users WHERE telegram_id = ?;", (telegram_id,)
                ) as cur:
                    updated_row = await cur.fetchone()
                    return dict(updated_row) if updated_row else dict(row)
            else:
                await conn.execute(
                    """
                    INSERT INTO users (telegram_id, username, first_name)
                    VALUES (?, ?, ?);
                    """,
                    (telegram_id, username, first_name),
                )
                await conn.commit()
                async with conn.execute(
                    "SELECT * FROM users WHERE telegram_id = ?;", (telegram_id,)
                ) as cursor:
                    new_row = await cursor.fetchone()
                    return dict(new_row) if new_row else {}

    async def get_user_profile(self, user_identifier: int) -> Optional[dict[str, Any]]:
        """Retrieve user profile row by user_id or telegram_id."""
        async with self.connect() as conn:
            async with conn.execute(
                "SELECT * FROM users WHERE id = ? OR telegram_id = ?;", (user_identifier, user_identifier)
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def get_user_by_normalized_mobile(self, normalized_mobile: str) -> Optional[dict[str, Any]]:
        """Retrieve user by canonical normalized mobile number."""
        async with self.connect() as conn:
            async with conn.execute(
                "SELECT * FROM users WHERE normalized_mobile = ? ORDER BY id DESC LIMIT 1;",
                (normalized_mobile,),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def get_user_by_id(self, user_id: int) -> Optional[dict[str, Any]]:
        """Retrieve user by primary key id."""
        async with self.connect() as conn:
            async with conn.execute(
                "SELECT * FROM users WHERE id = ?;", (user_id,)
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def get_user_by_telegram_id(self, telegram_id: int) -> Optional[dict[str, Any]]:
        """Retrieve user by telegram_id."""
        async with self.connect() as conn:
            async with conn.execute(
                "SELECT * FROM users WHERE telegram_id = ?;", (telegram_id,)
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def create_user_account(
        self,
        full_name: str,
        mobile_number: str,
        password_hash: str,
    ) -> dict[str, Any]:
        """Create a new user account with canonical mobile and password hash."""
        normalized = normalize_mobile(mobile_number)
        clean_name = full_name.strip()
        digits = "".join(ch for ch in normalized if ch.isdigit())
        pseudo_tg_id = int(digits[-9:]) if len(digits) >= 9 else (abs(hash(normalized)) % 1000000000 + 1000000000)

        async with self.connect() as conn:
            cur = await conn.execute(
                """
                INSERT INTO users (telegram_id, full_name, mobile_number, normalized_mobile, password_hash, status)
                VALUES (?, ?, ?, ?, ?, 'active');
                """,
                (pseudo_tg_id, clean_name, mobile_number.strip(), normalized, password_hash),
            )
            await conn.commit()
            new_id = cur.lastrowid
            async with conn.execute("SELECT * FROM users WHERE id = ?;", (new_id,)) as c:
                row = await c.fetchone()
                return dict(row) if row else {}

    async def update_user_password(self, user_id: int, new_password_hash: str) -> bool:
        """Update user password hash and updated_at."""
        async with self.connect() as conn:
            await conn.execute(
                "UPDATE users SET password_hash = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
                (new_password_hash, user_id),
            )
            await conn.commit()
            return True

    async def update_user_last_login(self, user_id: int) -> None:
        """Update user's last login timestamp."""
        async with self.connect() as conn:
            await conn.execute(
                "UPDATE users SET last_login_at = CURRENT_TIMESTAMP, last_activity = CURRENT_TIMESTAMP WHERE id = ?;",
                (user_id,),
            )
            await conn.commit()

    async def is_user_registered(self, telegram_id: int) -> bool:
        """Check if user has provided full name and mobile number."""
        user = await self.get_user_profile(telegram_id)
        if not user:
            return False
        return bool(user.get("full_name") and user.get("mobile_number"))

    async def update_user_name(self, telegram_id: int, full_name: str) -> None:
        """Update user's full name."""
        async with self.connect() as conn:
            await conn.execute(
                "UPDATE users SET full_name = ?, last_activity = CURRENT_TIMESTAMP WHERE telegram_id = ? OR id = ?;",
                (full_name.strip(), telegram_id, telegram_id),
            )
            await conn.commit()

    async def update_user_mobile(self, telegram_id: int, mobile_number: str) -> None:
        """Update user's mobile number."""
        norm = normalize_mobile(mobile_number)
        async with self.connect() as conn:
            await conn.execute(
                "UPDATE users SET mobile_number = ?, normalized_mobile = ?, last_activity = CURRENT_TIMESTAMP WHERE telegram_id = ? OR id = ?;",
                (mobile_number.strip(), norm, telegram_id, telegram_id),
            )
            await conn.commit()

    async def update_user_activity(self, telegram_id: int) -> None:
        """Update user's last activity timestamp."""
        async with self.connect() as conn:
            await conn.execute(
                "UPDATE users SET last_activity = CURRENT_TIMESTAMP WHERE telegram_id = ? OR id = ?;",
                (telegram_id, telegram_id),
            )
            await conn.commit()

    async def get_user_count(self, search: str = "") -> int:
        """Return total registered users count with optional search filter."""
        async with self.connect() as conn:
            if search:
                term = f"%{search.strip()}%"
                async with conn.execute(
                    "SELECT COUNT(*) AS count FROM users WHERE full_name LIKE ? OR mobile_number LIKE ? OR username LIKE ?;",
                    (term, term, term),
                ) as cursor:
                    row = await cursor.fetchone()
                    return row["count"] if row else 0
            else:
                async with conn.execute("SELECT COUNT(*) AS count FROM users;") as cursor:
                    row = await cursor.fetchone()
                    return row["count"] if row else 0

    async def get_all_users(self, limit: int = 15, offset: int = 0, search: str = "") -> list[dict[str, Any]]:
        """Get paginated users for admin with optional search."""
        async with self.connect() as conn:
            if search:
                term = f"%{search.strip()}%"
                async with conn.execute(
                    """
                    SELECT u.*,
                           (SELECT COUNT(*) FROM user_wallets w WHERE w.user_id = u.id OR w.telegram_id = u.telegram_id) AS wallet_count,
                           (SELECT COUNT(*) FROM usat_payments p WHERE (p.user_id = u.id OR p.telegram_id = u.telegram_id) AND p.status = 'SUCCESS') AS payment_count
                    FROM users u
                    WHERE u.full_name LIKE ? OR u.mobile_number LIKE ? OR u.username LIKE ?
                    ORDER BY u.id DESC
                    LIMIT ? OFFSET ?;
                    """,
                    (term, term, term, limit, offset),
                ) as cur:
                    rows = await cur.fetchall()
                    return [dict(r) for r in rows]
            else:
                async with conn.execute(
                    """
                    SELECT u.*,
                           (SELECT COUNT(*) FROM user_wallets w WHERE w.user_id = u.id OR w.telegram_id = u.telegram_id) AS wallet_count,
                           (SELECT COUNT(*) FROM usat_payments p WHERE (p.user_id = u.id OR p.telegram_id = u.telegram_id) AND p.status = 'SUCCESS') AS payment_count
                    FROM users u
                    ORDER BY u.id DESC
                    LIMIT ? OFFSET ?;
                    """,
                    (limit, offset),
                ) as cur:
                    rows = await cur.fetchall()
                    return [dict(r) for r in rows]

    async def get_user_details_admin(self, user_identifier: int) -> Optional[dict[str, Any]]:
        """Retrieve complete user details, linked wallets (safe, no keys), and payments for admin."""
        user = await self.get_user_profile(user_identifier)
        if not user:
            user = await self.get_user_by_id(user_identifier)
        if not user:
            return None

        # Get wallets (exclude encrypted_private_key)
        wallets = await self.get_user_wallets(user_identifier)
        safe_wallets = []
        for w in wallets:
            w_copy = dict(w)
            w_copy.pop("encrypted_private_key", None)
            safe_wallets.append(w_copy)

        # Get payments
        payments = await self.get_user_payments(user_identifier, limit=50)

        total_paid = await self.get_user_total_paid(user_identifier)
        return {
            "user": user,
            "wallets": safe_wallets,
            "payments": payments,
            "total_paid": round(total_paid, 2),
        }

    async def reset_all_users_and_wallets(self) -> dict[str, Any]:
        """Completely wipe all user accounts, user_wallets, payments, and claims to start fresh."""
        async with self.connect() as conn:
            await conn.execute("DELETE FROM usat_payments;")
            await conn.execute("DELETE FROM user_wallets;")
            await conn.execute("DELETE FROM claims;")
            await conn.execute("DELETE FROM sessions;")
            await conn.execute("DELETE FROM users;")
            if not self.using_postgres:
                await conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('users', 'user_wallets', 'usat_payments', 'claims');")
            await conn.commit()
            try:
                await conn.execute("VACUUM;")
            except Exception:
                pass
        return {"success": True, "message": "All user accounts, wallets, and payments successfully cleared."}

    # --- Wallet Management (Dual Wallet: connected or imported) ---

    async def add_user_wallet(
        self,
        telegram_id: int,
        wallet_name: str,
        address: str,
        wallet_type: str,
        encrypted_private_key: Optional[str] = None,
        metadata: Optional[str] = None,
        user_id: Optional[int] = None,
        workspace_id: Optional[int] = None,
    ) -> int:
        """Add a wallet for a user. Address is checksummed."""
        chk_address = Web3.to_checksum_address(address)
        async with self.connect() as conn:
            if user_id is None:
                async with conn.execute("SELECT id FROM users WHERE telegram_id = ? OR id = ?;", (telegram_id, telegram_id)) as cur:
                    u_row = await cur.fetchone()
                    user_id = u_row["id"] if u_row else telegram_id

            if workspace_id is None:
                workspace_id = await self.ensure_personal_workspace(user_id)

            cur = await conn.execute(
                """
                INSERT INTO user_wallets (user_id, telegram_id, workspace_id, wallet_name, address, wallet_type, encrypted_private_key, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    user_id,
                    telegram_id,
                    workspace_id,
                    wallet_name.strip(),
                    chk_address,
                    wallet_type,
                    encrypted_private_key,
                    metadata,
                ),
            )
            await conn.commit()
            return cur.lastrowid or 0

    async def ensure_personal_workspace(self, user_identifier: int) -> int:
        """Return the owner's default workspace and backfill their older wallets safely."""
        async with self.connect() as conn:
            async with conn.execute(
                "SELECT id, telegram_id FROM users WHERE id = ? OR telegram_id = ? ORDER BY id ASC LIMIT 1;",
                (user_identifier, user_identifier),
            ) as cur:
                user = await cur.fetchone()
            if not user:
                raise ValueError("User not found for workspace")
            user_id, telegram_id = user["id"], user["telegram_id"]
            await conn.execute(
                """
                INSERT INTO wallet_workspaces (user_id, telegram_id, name)
                VALUES (?, ?, 'Personal Workspace')
                ON CONFLICT(user_id, name) DO NOTHING;
                """,
                (user_id, telegram_id),
            )
            async with conn.execute(
                "SELECT id FROM wallet_workspaces WHERE user_id = ? AND name = 'Personal Workspace';",
                (user_id,),
            ) as cur:
                workspace = await cur.fetchone()
            if not workspace:
                raise RuntimeError("Personal workspace could not be created")
            workspace_id = workspace["id"]
            await conn.execute(
                """
                UPDATE user_wallets SET workspace_id = ?
                WHERE workspace_id IS NULL AND (user_id = ? OR telegram_id = ?);
                """,
                (workspace_id, user_id, telegram_id),
            )
            await conn.commit()
            return workspace_id

    async def get_user_workspaces(self, user_identifier: int) -> list[dict[str, Any]]:
        """List only the authenticated user's workspaces, including their wallet count."""
        await self.ensure_personal_workspace(user_identifier)
        async with self.connect() as conn:
            async with conn.execute(
                """
                SELECT ws.id, ws.name, ws.created_at, COUNT(w.id) AS wallet_count
                FROM wallet_workspaces ws
                LEFT JOIN user_wallets w ON w.workspace_id = ws.id
                WHERE ws.user_id = ? OR ws.telegram_id = ?
                GROUP BY ws.id, ws.name, ws.created_at
                ORDER BY ws.id ASC;
                """,
                (user_identifier, user_identifier),
            ) as cur:
                return [dict(row) for row in await cur.fetchall()]

    async def create_user_workspace(self, user_identifier: int, name: str) -> dict[str, Any]:
        """Create a named workspace for the current owner only."""
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Workspace name is required")
        async with self.connect() as conn:
            async with conn.execute(
                "SELECT id, telegram_id FROM users WHERE id = ? OR telegram_id = ? ORDER BY id ASC LIMIT 1;",
                (user_identifier, user_identifier),
            ) as cur:
                user = await cur.fetchone()
            if not user:
                raise ValueError("User not found for workspace")
            try:
                await conn.execute(
                    "INSERT INTO wallet_workspaces (user_id, telegram_id, name) VALUES (?, ?, ?);",
                    (user["id"], user["telegram_id"], clean_name),
                )
            except Exception as exc:
                if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                    raise ValueError("A workspace with this name already exists") from None
                raise
            async with conn.execute(
                "SELECT id, name, created_at FROM wallet_workspaces WHERE user_id = ? AND name = ?;",
                (user["id"], clean_name),
            ) as cur:
                workspace = await cur.fetchone()
            await conn.commit()
            return dict(workspace) if workspace else {"name": clean_name}

    async def get_user_workspace_by_id(self, workspace_id: int, user_identifier: int) -> Optional[dict[str, Any]]:
        async with self.connect() as conn:
            async with conn.execute(
                """
                SELECT * FROM wallet_workspaces
                WHERE id = ? AND (user_id = ? OR telegram_id = ?);
                """,
                (workspace_id, user_identifier, user_identifier),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def update_wallet_private_key(
        self,
        wallet_id: int,
        encrypted_private_key: str,
        wallet_name: Optional[str] = None,
    ) -> bool:
        """Upgrade/link an existing wallet with an encrypted private key and set type to 'imported'."""
        async with self.connect() as conn:
            if wallet_name:
                await conn.execute(
                    """
                    UPDATE user_wallets 
                    SET encrypted_private_key = ?, wallet_type = 'imported', wallet_name = ?
                    WHERE id = ?;
                    """,
                    (encrypted_private_key, wallet_name.strip(), wallet_id),
                )
            else:
                await conn.execute(
                    """
                    UPDATE user_wallets 
                    SET encrypted_private_key = ?, wallet_type = 'imported'
                    WHERE id = ?;
                    """,
                    (encrypted_private_key, wallet_id),
                )
            await conn.commit()
            return True

    async def get_user_wallets(self, user_identifier: int) -> list[dict[str, Any]]:
        """Retrieve all wallets owned by a specific user (matching user_id or telegram_id)."""
        async with self.connect() as conn:
            async with conn.execute(
                """
                SELECT * FROM user_wallets 
                WHERE user_id = ? OR telegram_id = ? 
                ORDER BY id ASC;
                """,
                (user_identifier, user_identifier),
            ) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]

    async def get_user_wallet_by_id(self, wallet_id: int, user_identifier: int) -> Optional[dict[str, Any]]:
        """Retrieve a specific wallet ensuring ownership by user_id or telegram_id."""
        async with self.connect() as conn:
            async with conn.execute(
                "SELECT * FROM user_wallets WHERE id = ? AND (user_id = ? OR telegram_id = ?);",
                (wallet_id, user_identifier, user_identifier),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def get_wallet_by_id_admin(self, wallet_id: int) -> Optional[dict[str, Any]]:
        """Retrieve any wallet by ID for admin."""
        async with self.connect() as conn:
            async with conn.execute(
                """
                SELECT w.*, u.full_name, u.first_name, u.username
                FROM user_wallets w
                LEFT JOIN users u ON w.telegram_id = u.telegram_id
                WHERE w.id = ?;
                """,
                (wallet_id,),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def get_wallets_by_user_id_admin(self, telegram_id: int) -> list[dict[str, Any]]:
        """Retrieve all wallets for a specific user (admin inspection)."""
        async with self.connect() as conn:
            async with conn.execute(
                "SELECT * FROM user_wallets WHERE telegram_id = ? ORDER BY id ASC;",
                (telegram_id,),
            ) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]

    async def get_all_wallets_admin(self, limit: int = 20, offset: int = 0, search: str = "") -> list[dict[str, Any]]:
        """Retrieve paginated wallets for admin without exposing private keys."""
        async with self.connect() as conn:
            if search:
                term = f"%{search.strip()}%"
                async with conn.execute(
                    """
                    SELECT w.id, w.user_id, w.telegram_id, w.wallet_name, w.address, w.wallet_type,
                           w.created_at, w.last_used, u.full_name, u.mobile_number, u.username
                    FROM user_wallets w
                    LEFT JOIN users u ON (w.user_id = u.id OR w.telegram_id = u.telegram_id)
                    WHERE w.wallet_name LIKE ? OR w.address LIKE ? OR u.full_name LIKE ?
                    ORDER BY w.id DESC
                    LIMIT ? OFFSET ?;
                    """,
                    (term, term, term, limit, offset),
                ) as cur:
                    rows = await cur.fetchall()
                    return [dict(r) for r in rows]
            else:
                async with conn.execute(
                    """
                    SELECT w.id, w.user_id, w.telegram_id, w.wallet_name, w.address, w.wallet_type,
                           w.created_at, w.last_used, u.full_name, u.mobile_number, u.username
                    FROM user_wallets w
                    LEFT JOIN users u ON (w.user_id = u.id OR w.telegram_id = u.telegram_id)
                    ORDER BY w.id DESC
                    LIMIT ? OFFSET ?;
                    """,
                    (limit, offset),
                ) as cur:
                    rows = await cur.fetchall()
                    return [dict(r) for r in rows]

    async def get_all_wallets_count_admin(self, search: str = "") -> int:
        """Return total wallet count for admin."""
        async with self.connect() as conn:
            if search:
                term = f"%{search.strip()}%"
                async with conn.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM user_wallets w
                    LEFT JOIN users u ON (w.user_id = u.id OR w.telegram_id = u.telegram_id)
                    WHERE w.wallet_name LIKE ? OR w.address LIKE ? OR u.full_name LIKE ?;
                    """,
                    (term, term, term),
                ) as cur:
                    row = await cur.fetchone()
                    return row["cnt"] if row else 0
            else:
                async with conn.execute("SELECT COUNT(*) AS cnt FROM user_wallets;") as cur:
                    row = await cur.fetchone()
                    return row["cnt"] if row else 0

    async def get_admin_funding_history(self, limit: int = 20) -> list[dict[str, Any]]:
        """Retrieve recent successful CELO fee sends with their receiving wallet details."""
        async with self.connect() as conn:
            async with conn.execute(
                """
                SELECT p.id AS record_id, p.payment_id, p.user_id,
                       p.from_address AS wallet_address,
                       COALESCE((
                           SELECT w.wallet_name
                           FROM user_wallets w
                           WHERE LOWER(w.address) = LOWER(p.from_address)
                             AND (w.user_id = p.user_id OR w.telegram_id = p.telegram_id)
                           ORDER BY w.id DESC LIMIT 1
                       ), 'Wallet') AS wallet_name,
                       u.full_name, p.celo_fund_tx_hash AS funding_tx_hash,
                       p.created_at, 'Automatic payment fee' AS fee_type
                FROM usat_payments p
                LEFT JOIN users u ON (p.user_id = u.id OR p.telegram_id = u.telegram_id)
                WHERE p.celo_funded = 1 AND p.celo_fund_tx_hash IS NOT NULL

                UNION ALL

                SELECT c.id AS record_id, c.request_id AS payment_id, NULL AS user_id,
                       c.destination_address AS wallet_address,
                       COALESCE((
                           SELECT w.wallet_name
                           FROM user_wallets w
                           WHERE LOWER(w.address) = LOWER(c.destination_address)
                             AND w.telegram_id = c.telegram_id
                           ORDER BY w.id DESC LIMIT 1
                       ), 'Wallet') AS wallet_name,
                       u.full_name, c.tx_hash AS funding_tx_hash,
                       c.created_at, 'Manual fee refill' AS fee_type
                FROM claims c
                LEFT JOIN users u ON u.telegram_id = c.telegram_id
                WHERE c.status = 'SUCCESS' AND c.tx_hash IS NOT NULL

                ORDER BY created_at DESC
                LIMIT ?;
                """,
                (limit,),
            ) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]

    async def rename_user_wallet(self, wallet_id: int, user_identifier: int, new_name: str) -> None:
        """Rename a user's wallet."""
        async with self.connect() as conn:
            await conn.execute(
                "UPDATE user_wallets SET wallet_name = ? WHERE id = ? AND (user_id = ? OR telegram_id = ?);",
                (new_name.strip(), wallet_id, user_identifier, user_identifier),
            )
            await conn.commit()

    async def rename_wallet_admin(self, wallet_id: int, new_name: str) -> None:
        """Rename wallet by admin."""
        async with self.connect() as conn:
            await conn.execute(
                "UPDATE user_wallets SET wallet_name = ? WHERE id = ?;",
                (new_name.strip(), wallet_id),
            )
            await conn.commit()

    async def delete_user_wallet(self, wallet_id: int, user_identifier: int) -> None:
        """Delete wallet owned by user."""
        async with self.connect() as conn:
            await conn.execute(
                "DELETE FROM user_wallets WHERE id = ? AND (user_id = ? OR telegram_id = ?);",
                (wallet_id, user_identifier, user_identifier),
            )
            await conn.commit()

    async def delete_wallet_admin(self, wallet_id: int) -> None:
        """Delete wallet by admin."""
        async with self.connect() as conn:
            await conn.execute("DELETE FROM user_wallets WHERE id = ?;", (wallet_id,))
            await conn.commit()

    async def get_user_wallet_count(self, user_identifier: int) -> int:
        """Return total wallets owned by user."""
        async with self.connect() as conn:
            async with conn.execute(
                "SELECT COUNT(*) AS cnt FROM user_wallets WHERE user_id = ? OR telegram_id = ?;", (user_identifier, user_identifier)
            ) as cur:
                row = await cur.fetchone()
                return row["cnt"] if row else 0

    async def update_wallet_last_used(self, wallet_id: int) -> None:
        """Touch last_used timestamp."""
        async with self.connect() as conn:
            await conn.execute(
                "UPDATE user_wallets SET last_used = CURRENT_TIMESTAMP WHERE id = ?;", (wallet_id,)
            )
            await conn.commit()

    # --- Personal Saved Recipients ---

    async def get_saved_recipients(self, user_identifier: int) -> list[dict[str, Any]]:
        """List recipient addresses saved by the current user only."""
        user_ids = await self._resolve_user_identifiers(user_identifier)
        placeholders = ",".join("?" * len(user_ids))
        async with self.connect() as conn:
            async with conn.execute(
                f"""
                SELECT id, name, address, created_at FROM saved_recipients
                WHERE user_id IN ({placeholders}) OR telegram_id IN ({placeholders})
                ORDER BY LOWER(name) ASC, id ASC;
                """,
                user_ids + user_ids,
            ) as cur:
                rows = await cur.fetchall()
                return [dict(row) for row in rows]

    async def save_recipient(self, user_id: int, name: str, address: str) -> int:
        """Save or rename one recipient address for its owner."""
        async with self.connect() as conn:
            async with conn.execute(
                """
                INSERT INTO saved_recipients (user_id, telegram_id, name, address)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, address) DO UPDATE SET name = excluded.name;
                """,
                (user_id, user_id, name.strip(), address),
            ) as cur:
                await conn.commit()
                return cur.lastrowid or 0

    async def delete_saved_recipient(self, recipient_id: int, user_identifier: int) -> bool:
        """Remove only a recipient owned by the current user."""
        user_ids = await self._resolve_user_identifiers(user_identifier)
        placeholders = ",".join("?" * len(user_ids))
        async with self.connect() as conn:
            cur = await conn.execute(
                f"DELETE FROM saved_recipients WHERE id = ? AND (user_id IN ({placeholders}) OR telegram_id IN ({placeholders}));",
                [recipient_id] + user_ids + user_ids,
            )
            await conn.commit()
            return cur.rowcount > 0

    # --- Receiving Wallets (Admin Configured) ---

    async def get_receiving_wallets(self, only_active: bool = False) -> list[dict[str, Any]]:
        """Retrieve all or only active receiving wallets."""
        query = "SELECT * FROM receiving_wallets"
        params: tuple = ()
        if only_active:
            query += " WHERE is_active = 1"
        query += " ORDER BY id ASC;"
        async with self.connect() as conn:
            async with conn.execute(query, params) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]

    async def get_receiving_wallet_by_id(self, wallet_id: int) -> Optional[dict[str, Any]]:
        """Get receiving wallet by ID."""
        async with self.connect() as conn:
            async with conn.execute(
                "SELECT * FROM receiving_wallets WHERE id = ?;", (wallet_id,)
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def add_receiving_wallet(self, name: str, address: str) -> int:
        """Add a new receiving wallet."""
        chk_address = Web3.to_checksum_address(address)
        async with self.connect() as conn:
            cur = await conn.execute(
                "INSERT INTO receiving_wallets (name, address, is_active) VALUES (?, ?, 1);",
                (name.strip(), chk_address),
            )
            await conn.commit()
            return cur.lastrowid or 0

    async def update_receiving_wallet_name(self, wallet_id: int, new_name: str) -> None:
        """Rename a receiving wallet."""
        async with self.connect() as conn:
            await conn.execute(
                "UPDATE receiving_wallets SET name = ? WHERE id = ?;",
                (new_name.strip(), wallet_id),
            )
            await conn.commit()

    async def toggle_receiving_wallet(self, wallet_id: int, is_active: bool) -> None:
        """Enable or disable a receiving wallet."""
        async with self.connect() as conn:
            await conn.execute(
                "UPDATE receiving_wallets SET is_active = ? WHERE id = ?;",
                (1 if is_active else 0, wallet_id),
            )
            await conn.commit()

    async def delete_receiving_wallet(self, wallet_id: int) -> None:
        """Remove a receiving wallet."""
        async with self.connect() as conn:
            await conn.execute("DELETE FROM receiving_wallets WHERE id = ?;", (wallet_id,))
            await conn.commit()

    async def get_recently_used_receiving_wallet(self, user_identifier: int) -> Optional[dict[str, Any]]:
        """Retrieve the most recently used active receiving wallet for a user."""
        async with self.connect() as conn:
            async with conn.execute(
                """
                SELECT r.* FROM usat_payments p
                JOIN receiving_wallets r ON p.to_address = r.address
                WHERE (p.user_id = ? OR p.telegram_id = ?) AND r.is_active = 1
                ORDER BY p.id DESC LIMIT 1;
                """,
                (user_identifier, user_identifier),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    # --- USAT Payment Management ---

    async def create_usat_payment(
        self,
        payment_id: str,
        telegram_id: int,
        wallet_type: str,
        from_address: str,
        to_address: str,
        receiving_wallet_name: str,
        amount_usat: str = "2.00",
        amount_base_units: int = 2000000,
        user_id: Optional[int] = None,
    ) -> int:
        """Create a new USAT payment record with PROCESSING status."""
        chk_from = Web3.to_checksum_address(from_address)
        chk_to = Web3.to_checksum_address(to_address)
        async with self.connect() as conn:
            if user_id is None:
                async with conn.execute("SELECT id FROM users WHERE telegram_id = ? OR id = ?;", (telegram_id, telegram_id)) as cur:
                    u_row = await cur.fetchone()
                    user_id = u_row["id"] if u_row else telegram_id

            cur = await conn.execute(
                """
                INSERT INTO usat_payments (
                    payment_id, user_id, telegram_id, wallet_type, from_address, to_address,
                    receiving_wallet_name, amount_usat, amount_base_units, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PROCESSING');
                """,
                (
                    payment_id,
                    user_id,
                    telegram_id,
                    wallet_type,
                    chk_from,
                    chk_to,
                    receiving_wallet_name,
                    amount_usat,
                    amount_base_units,
                ),
            )
            await conn.commit()
            return cur.lastrowid or 0

    async def update_usat_payment_status(
        self,
        payment_id: str,
        status: str,
        tx_hash: Optional[str] = None,
        block_number: Optional[int] = None,
        celo_funded: int = 0,
        celo_fund_tx_hash: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """Update payment lifecycle state."""
        async with self.connect() as conn:
            await conn.execute(
                """
                UPDATE usat_payments
                SET status = ?,
                    tx_hash = COALESCE(?, tx_hash),
                    block_number = COALESCE(?, block_number),
                    celo_funded = CASE WHEN ? = 1 THEN 1 ELSE celo_funded END,
                    celo_fund_tx_hash = COALESCE(?, celo_fund_tx_hash),
                    error_message = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE payment_id = ?;
                """,
                (
                    status,
                    tx_hash,
                    block_number,
                    celo_funded,
                    celo_fund_tx_hash,
                    error_message,
                    payment_id,
                ),
            )
            await conn.commit()

    async def _resolve_user_identifiers(self, user_identifier: int) -> list[int]:
        """Resolve all potential IDs (users.id, users.telegram_id) for a given identifier."""
        try:
            uid_int = int(user_identifier)
        except (ValueError, TypeError):
            return []

        ids = {uid_int}
        user = await self.get_user_by_id(uid_int)
        if user and user.get("telegram_id"):
            try:
                ids.add(int(user["telegram_id"]))
            except (ValueError, TypeError):
                pass
        user_tg = await self.get_user_by_telegram_id(uid_int)
        if user_tg and user_tg.get("id"):
            try:
                ids.add(int(user_tg["id"]))
            except (ValueError, TypeError):
                pass
        return list(ids)

    async def get_active_usat_payment(self, user_identifier: int) -> Optional[dict[str, Any]]:
        """Check for active PROCESSING payment to prevent concurrent races."""
        user_ids = await self._resolve_user_identifiers(user_identifier)
        if not user_ids:
            return None
        placeholders = ",".join("?" * len(user_ids))
        async with self.connect() as conn:
            # Auto-expire stale unmined payments older than 15 minutes to prevent permanent locks
            await conn.execute(
                f"""
                UPDATE usat_payments
                SET status = 'CANCELLED',
                    error_message = 'Auto-expired due to inactivity',
                    updated_at = CURRENT_TIMESTAMP
                WHERE (user_id IN ({placeholders}) OR telegram_id IN ({placeholders}))
                  AND UPPER(status) IN ('PROCESSING', 'PENDING', 'AWAITING_USER_SIGNATURE')
                  AND tx_hash IS NULL
                  AND created_at < datetime('now', '-15 minutes');
                """,
                user_ids + user_ids,
            )
            await conn.commit()

            async with conn.execute(
                f"""
                SELECT * FROM usat_payments 
                WHERE (user_id IN ({placeholders}) OR telegram_id IN ({placeholders}))
                  AND UPPER(status) IN ('PROCESSING', 'PENDING', 'AWAITING_USER_SIGNATURE', 'CONFIRMING')
                ORDER BY created_at DESC LIMIT 1;
                """,
                user_ids + user_ids,
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def cancel_pending_payment(
        self,
        payment_id: str | int,
        user_identifier: int | None = None,
        is_admin: bool = False
    ) -> bool:
        """Cancel a pending/processing payment that has not been debited."""
        pid_str = str(payment_id).strip()
        user_ids = []
        if user_identifier is not None and not is_admin:
            user_ids = await self._resolve_user_identifiers(user_identifier)

        async with self.connect() as conn:
            if is_admin or not user_ids:
                cur = await conn.execute(
                    """
                    UPDATE usat_payments
                    SET status = 'CANCELLED',
                        error_message = 'Cancelled by user',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE (payment_id = ? OR CAST(id AS TEXT) = ?)
                      AND UPPER(status) NOT IN ('SUCCESS', 'CONFIRMED');
                    """,
                    (pid_str, pid_str),
                )
            else:
                placeholders = ",".join("?" * len(user_ids))
                cur = await conn.execute(
                    f"""
                    UPDATE usat_payments
                    SET status = 'CANCELLED',
                        error_message = 'Cancelled by user',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE (payment_id = ? OR CAST(id AS TEXT) = ?)
                      AND (user_id IN ({placeholders}) OR telegram_id IN ({placeholders}))
                      AND UPPER(status) NOT IN ('SUCCESS', 'CONFIRMED');
                    """,
                    [pid_str, pid_str] + user_ids + user_ids,
                )
            await conn.commit()
            return cur.rowcount > 0

    async def cancel_user_active_payment(self, user_identifier: int) -> Optional[str]:
        """Cancel whatever active/processing/pending payment the user currently has. Returns cancelled payment_id if found."""
        user_ids = await self._resolve_user_identifiers(user_identifier)
        if not user_ids:
            return None
        placeholders = ",".join("?" * len(user_ids))
        async with self.connect() as conn:
            async with conn.execute(
                f"""
                SELECT payment_id, id FROM usat_payments
                WHERE (user_id IN ({placeholders}) OR telegram_id IN ({placeholders}))
                  AND UPPER(status) NOT IN ('SUCCESS', 'CONFIRMED', 'CANCELLED')
                ORDER BY created_at DESC LIMIT 1;
                """,
                user_ids + user_ids,
            ) as cur:
                row = await cur.fetchone()
                if not row:
                    return None
                pid = row["payment_id"]

            await conn.execute(
                """
                UPDATE usat_payments
                SET status = 'CANCELLED',
                    error_message = 'Cancelled by user',
                    updated_at = CURRENT_TIMESTAMP
                WHERE payment_id = ?;
                """,
                (pid,),
            )
            await conn.commit()
            return pid

    async def get_usat_payment_by_id(self, payment_id: str | int) -> Optional[dict[str, Any]]:
        """Retrieve a specific USAT payment record by payment_id (UUID) or numeric id."""
        async with self.connect() as conn:
            pid_str = str(payment_id).strip()
            async with conn.execute(
                """
                SELECT * FROM usat_payments 
                WHERE payment_id = ? OR CAST(id AS TEXT) = ?;
                """,
                (pid_str, pid_str),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def get_user_payments(
        self, user_identifier: int, limit: int = 5, offset: int = 0
    ) -> list[dict[str, Any]]:
        """Paginated USAT payments for a specific user."""
        user_ids = await self._resolve_user_identifiers(user_identifier)
        placeholders = ",".join("?" * len(user_ids))
        async with self.connect() as conn:
            async with conn.execute(
                f"""
                SELECT * FROM usat_payments
                WHERE user_id IN ({placeholders}) OR telegram_id IN ({placeholders})
                ORDER BY id DESC
                LIMIT ? OFFSET ?;
                """,
                user_ids + user_ids + [limit, offset],
            ) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]

    async def get_user_payments_count(self, user_identifier: int) -> int:
        """Return total USAT payments made by user."""
        user_ids = await self._resolve_user_identifiers(user_identifier)
        placeholders = ",".join("?" * len(user_ids))
        async with self.connect() as conn:
            async with conn.execute(
                f"SELECT COUNT(*) AS cnt FROM usat_payments WHERE user_id IN ({placeholders}) OR telegram_id IN ({placeholders});",
                user_ids + user_ids,
            ) as cur:
                row = await cur.fetchone()
                return row["cnt"] if row else 0

    async def get_wallet_payment_history(self, user_identifier: int, wallet_address: str) -> list[dict[str, Any]]:
        """Return recorded sends and CELO rewards for one owned wallet, oldest first."""
        user_ids = await self._resolve_user_identifiers(user_identifier)
        placeholders = ",".join("?" * len(user_ids))
        async with self.connect() as conn:
            async with conn.execute(
                f"""
                SELECT p.id AS record_id,
                       'outgoing' AS direction,
                       'payment' AS activity_type,
                       CAST(p.amount_usat AS TEXT) AS amount,
                       'USDT' AS currency,
                       p.to_address AS counterparty_address,
                       p.status AS status,
                       p.tx_hash AS tx_hash,
                       p.created_at AS created_at
                FROM usat_payments p
                WHERE (user_id IN ({placeholders}) OR telegram_id IN ({placeholders}))
                  AND LOWER(p.from_address) = LOWER(?)

                UNION ALL

                SELECT p.id AS record_id,
                       'incoming' AS direction,
                       'gas_reward' AS activity_type,
                       CAST(? AS TEXT) AS amount,
                       'CELO' AS currency,
                       '' AS counterparty_address,
                       'SUCCESS' AS status,
                       p.celo_fund_tx_hash AS tx_hash,
                       p.created_at AS created_at
                FROM usat_payments p
                WHERE (p.user_id IN ({placeholders}) OR p.telegram_id IN ({placeholders}))
                  AND LOWER(p.from_address) = LOWER(?)
                  AND p.celo_funded = 1
                  AND p.celo_fund_tx_hash IS NOT NULL

                UNION ALL

                SELECT c.id AS record_id,
                       'incoming' AS direction,
                       'fee_reward' AS activity_type,
                       CAST(c.amount AS TEXT) AS amount,
                       'CELO' AS currency,
                       '' AS counterparty_address,
                       c.status AS status,
                       c.tx_hash AS tx_hash,
                       c.created_at AS created_at
                FROM claims c
                WHERE c.telegram_id IN ({placeholders})
                  AND LOWER(c.destination_address) = LOWER(?)
                  AND c.status = 'SUCCESS'
                  AND c.tx_hash IS NOT NULL
                ORDER BY created_at ASC, record_id ASC;
                """,
                user_ids + user_ids + [wallet_address]
                + [str(config.celo_funding_amount)]
                + user_ids + user_ids + [wallet_address]
                + user_ids + [wallet_address],
            ) as cur:
                rows = await cur.fetchall()
                return [dict(row) for row in rows]

    async def get_user_total_paid(self, user_identifier: int) -> float:
        """Calculate total USAT paid by user in successful transactions."""
        async with self.connect() as conn:
            async with conn.execute(
                """
                SELECT SUM(CAST(amount_usat AS REAL)) AS total 
                FROM usat_payments 
                WHERE (user_id = ? OR telegram_id = ?) AND status = 'SUCCESS';
                """,
                (user_identifier, user_identifier),
            ) as cur:
                row = await cur.fetchone()
                return float(row["total"]) if row and row["total"] else 0.0

    async def get_wallet_total_sent(self, address: str) -> float:
        """Calculate total USAT sent from a specific wallet address."""
        chk_addr = Web3.to_checksum_address(address)
        async with self.connect() as conn:
            async with conn.execute(
                """
                SELECT SUM(CAST(amount_usat AS REAL)) AS total 
                FROM usat_payments 
                WHERE from_address = ? AND status = 'SUCCESS';
                """,
                (chk_addr,),
            ) as cur:
                row = await cur.fetchone()
                return float(row["total"]) if row and row["total"] else 0.0

    async def get_all_payments(
        self,
        limit: int = 20,
        offset: int = 0,
        search: str = "",
        status_filter: str = "",
        date_filter: str = "",
        time_from: str = "",
        time_to: str = "",
    ) -> list[dict[str, Any]]:
        """Paginated all payments for admin with search (name, mobile, addresses, tx_hash), date, and time range filters."""
        conditions = []
        params = []
        if status_filter:
            conditions.append("p.status = ?")
            params.append(status_filter.strip())
        if search:
            term = f"%{search.strip()}%"
            conditions.append("(p.from_address LIKE ? OR p.to_address LIKE ? OR p.tx_hash LIKE ? OR u.full_name LIKE ? OR u.mobile_number LIKE ? OR p.payment_id LIKE ?)")
            params.extend([term, term, term, term, term, term])
        if date_filter:
            conditions.append("date(p.created_at, 'localtime') = date(?)")
            params.append(date_filter.strip())
        if time_from:
            tf = time_from.strip()
            if len(tf) == 5:
                tf = f"{tf}:00"
            conditions.append("time(p.created_at, 'localtime') >= time(?)")
            params.append(tf)
        if time_to:
            tt = time_to.strip()
            if len(tt) == 5:
                tt = f"{tt}:59"
            conditions.append("time(p.created_at, 'localtime') <= time(?)")
            params.append(tt)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"""
            SELECT p.*, u.full_name, u.username, u.mobile_number,
                   datetime(p.created_at, 'localtime') AS local_created_at
            FROM usat_payments p
            LEFT JOIN users u ON (p.user_id = u.id OR p.telegram_id = u.telegram_id)
            {where_clause}
            ORDER BY p.id DESC
            LIMIT ? OFFSET ?;
        """
        params.extend([limit, offset])
        async with self.connect() as conn:
            async with conn.execute(query, tuple(params)) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]

    async def get_all_payments_count(
        self,
        search: str = "",
        status_filter: str = "",
        date_filter: str = "",
        time_from: str = "",
        time_to: str = "",
    ) -> int:
        """Return total payments count with optional search, status, and time range filters."""
        summary = await self.get_all_payments_summary(
            search=search,
            status_filter=status_filter,
            date_filter=date_filter,
            time_from=time_from,
            time_to=time_to,
        )
        return summary["total_count"]

    async def get_all_payments_summary(
        self,
        search: str = "",
        status_filter: str = "",
        date_filter: str = "",
        time_from: str = "",
        time_to: str = "",
    ) -> dict[str, Any]:
        """Calculate total payments count, total USDT sum, and unique members for a filtered window."""
        conditions = []
        params = []
        if status_filter:
            conditions.append("p.status = ?")
            params.append(status_filter.strip())
        if search:
            term = f"%{search.strip()}%"
            conditions.append("(p.from_address LIKE ? OR p.to_address LIKE ? OR p.tx_hash LIKE ? OR u.full_name LIKE ? OR u.mobile_number LIKE ? OR p.payment_id LIKE ?)")
            params.extend([term, term, term, term, term, term])
        if date_filter:
            conditions.append("date(p.created_at, 'localtime') = date(?)")
            params.append(date_filter.strip())
        if time_from:
            tf = time_from.strip()
            if len(tf) == 5:
                tf = f"{tf}:00"
            conditions.append("time(p.created_at, 'localtime') >= time(?)")
            params.append(tf)
        if time_to:
            tt = time_to.strip()
            if len(tt) == 5:
                tt = f"{tt}:59"
            conditions.append("time(p.created_at, 'localtime') <= time(?)")
            params.append(tt)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"""
            SELECT 
                COUNT(p.id) AS total_count,
                COALESCE(SUM(CAST(p.amount_usat AS REAL)), 0.0) AS total_amount,
                COUNT(DISTINCT COALESCE(p.user_id, p.telegram_id)) AS unique_users
            FROM usat_payments p
            LEFT JOIN users u ON (p.user_id = u.id OR p.telegram_id = u.telegram_id)
            {where_clause};
        """
        async with self.connect() as conn:
            async with conn.execute(query, tuple(params)) as cur:
                row = await cur.fetchone()
                return {
                    "total_count": row["total_count"] if row else 0,
                    "total_amount": round(float(row["total_amount"]), 2) if row else 0.0,
                    "unique_users": row["unique_users"] if row else 0,
                }

    async def get_usat_statistics(self) -> dict[str, Any]:
        """Aggregate platform metrics for admin reporting."""
        async with self.connect() as conn:
            async with conn.execute("SELECT COUNT(*) AS count FROM users;") as c:
                row = await c.fetchone()
                total_users = row["count"] if row else 0

            async with conn.execute(
                """
                SELECT 
                    COUNT(*) AS total_payments,
                    SUM(CASE WHEN status = 'SUCCESS' THEN 1 ELSE 0 END) AS successful,
                    SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) AS failed,
                    SUM(CASE WHEN status = 'PROCESSING' THEN 1 ELSE 0 END) AS processing,
                    SUM(CASE WHEN status = 'SUCCESS' THEN CAST(amount_usat AS REAL) ELSE 0 END) AS total_usat_sent,
                    SUM(CASE WHEN celo_funded = 1 THEN 1 ELSE 0 END) AS celo_subsidies_count
                FROM usat_payments;
                """
            ) as c:
                row = await c.fetchone()
                total_payments = row["total_payments"] or 0
                successful = row["successful"] or 0
                failed = row["failed"] or 0
                processing = row["processing"] or 0
                total_usat_sent = row["total_usat_sent"] or 0.0
                celo_subsidies_count = row["celo_subsidies_count"] or 0

            # Today's metrics
            async with conn.execute(
                """
                SELECT 
                    COUNT(*) AS today_payments,
                    SUM(CASE WHEN status = 'SUCCESS' THEN CAST(amount_usat AS REAL) ELSE 0 END) AS today_usat_sent
                FROM usat_payments
                WHERE date(created_at) = date('now');
                """
            ) as c:
                row = await c.fetchone()
                today_payments = row["today_payments"] or 0
                today_usat_sent = row["today_usat_sent"] or 0.0

            # Total wallets connected vs imported
            async with conn.execute(
                """
                SELECT 
                    COUNT(*) as total_wallets,
                    SUM(CASE WHEN wallet_type = 'connected' THEN 1 ELSE 0 END) as connected_wallets,
                    SUM(CASE WHEN wallet_type = 'imported' THEN 1 ELSE 0 END) as imported_wallets
                FROM user_wallets;
                """
            ) as c:
                row = await c.fetchone()
                total_wallets = row["total_wallets"] or 0
                connected_wallets = row["connected_wallets"] or 0
                imported_wallets = row["imported_wallets"] or 0

        return {
            "total_users": total_users,
            "total_payments": total_payments,
            "successful": successful,
            "failed": failed,
            "processing": processing,
            "total_usat_sent": round(float(total_usat_sent), 2),
            "today_payments": today_payments,
            "today_usat_sent": round(float(today_usat_sent), 2),
            "celo_subsidies_count": celo_subsidies_count,
            "total_wallets": total_wallets,
            "connected_wallets": connected_wallets,
            "imported_wallets": imported_wallets,
        }

    async def get_user_totals(self, limit: int = 15, offset: int = 0) -> list[dict[str, Any]]:
        """Breakdown of users and their total USAT paid."""
        async with self.connect() as conn:
            async with conn.execute(
                """
                SELECT u.telegram_id, u.full_name, u.first_name, u.username,
                       COUNT(p.id) AS successful_payments,
                       COALESCE(SUM(CAST(p.amount_usat AS REAL)), 0.0) AS total_usat_paid
                FROM users u
                LEFT JOIN usat_payments p ON u.telegram_id = p.telegram_id AND p.status = 'SUCCESS'
                GROUP BY u.telegram_id
                ORDER BY total_usat_paid DESC, successful_payments DESC
                LIMIT ? OFFSET ?;
                """,
                (limit, offset),
            ) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]

    # --- Legacy Faucet Claims (Preserved for compatibility) ---

    async def create_claim(
        self, request_id: str, telegram_id: int, destination_address: str, amount: float
    ) -> int:
        """Create a new legacy claim record with PROCESSING status."""
        async with self.connect() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO claims (request_id, telegram_id, destination_address, amount, status)
                VALUES (?, ?, ?, ?, 'PROCESSING');
                """,
                (request_id, telegram_id, destination_address, amount),
            )
            await conn.commit()
            return cursor.lastrowid or 0

    async def update_claim_status(
        self,
        request_id: str,
        status: str,
        tx_hash: str | None = None,
        block_number: int | None = None,
        error_message: str | None = None,
    ) -> None:
        """Update legacy claim state upon broadcast or error."""
        async with self.connect() as conn:
            await conn.execute(
                """
                UPDATE claims
                SET status = ?, tx_hash = ?, block_number = ?, error_message = ?, updated_at = CURRENT_TIMESTAMP
                WHERE request_id = ?;
                """,
                (status, tx_hash, block_number, error_message, request_id),
            )
            await conn.commit()

    async def get_active_claim_for_user(self, telegram_id: int) -> Optional[dict[str, Any]]:
        """Check if user has an ongoing legacy transaction."""
        async with self.connect() as conn:
            async with conn.execute(
                """
                SELECT * FROM claims 
                WHERE telegram_id = ? AND status = 'PROCESSING'
                ORDER BY created_at DESC LIMIT 1;
                """,
                (telegram_id,),
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def get_user_claims(
        self, telegram_id: int, limit: int = 5, offset: int = 0
    ) -> list[dict[str, Any]]:
        """Retrieve paginated legacy claims for a specific user."""
        async with self.connect() as conn:
            async with conn.execute(
                """
                SELECT * FROM claims 
                WHERE telegram_id = ? 
                ORDER BY id DESC 
                LIMIT ? OFFSET ?;
                """,
                (telegram_id, limit, offset),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def get_user_claims_count(self, telegram_id: int) -> int:
        """Return total legacy claims submitted by a user."""
        async with self.connect() as conn:
            async with conn.execute(
                "SELECT COUNT(*) AS count FROM claims WHERE telegram_id = ?;", (telegram_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return row["count"] if row else 0

    async def get_all_claims(
        self, limit: int = 10, offset: int = 0
    ) -> list[dict[str, Any]]:
        """Retrieve paginated list of all legacy claims."""
        async with self.connect() as conn:
            async with conn.execute(
                """
                SELECT * FROM claims 
                ORDER BY id DESC 
                LIMIT ? OFFSET ?;
                """,
                (limit, offset),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def get_all_claims_count(self) -> int:
        """Return total legacy claim requests count."""
        async with self.connect() as conn:
            async with conn.execute("SELECT COUNT(*) AS count FROM claims;") as cursor:
                row = await cursor.fetchone()
                return row["count"] if row else 0

    async def get_statistics(self) -> dict[str, Any]:
        """Aggregate platform metrics for legacy test compatibility."""
        async with self.connect() as conn:
            async with conn.execute("SELECT COUNT(*) AS count FROM users;") as c:
                row = await c.fetchone()
                total_users = row["count"] if row else 0

            async with conn.execute(
                """
                SELECT 
                    COUNT(*) AS total_requests,
                    SUM(CASE WHEN status = 'SUCCESS' THEN 1 ELSE 0 END) AS successful,
                    SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) AS failed,
                    SUM(CASE WHEN status = 'PROCESSING' THEN 1 ELSE 0 END) AS processing,
                    SUM(CASE WHEN status = 'SUCCESS' THEN amount ELSE 0 END) AS total_celo_sent
                FROM claims;
                """
            ) as c:
                row = await c.fetchone()
                total_requests = row["total_requests"] or 0
                successful = row["successful"] or 0
                failed = row["failed"] or 0
                processing = row["processing"] or 0
                total_celo_sent = row["total_celo_sent"] or 0.0

            async with conn.execute(
                """
                SELECT 
                    COUNT(*) AS today_requests,
                    SUM(CASE WHEN status = 'SUCCESS' THEN amount ELSE 0 END) AS today_celo_sent
                FROM claims
                WHERE date(created_at) = date('now');
                """
            ) as c:
                row = await c.fetchone()
                today_requests = row["today_requests"] or 0
                today_celo_sent = row["today_celo_sent"] or 0.0

        return {
            "total_users": total_users,
            "total_requests": total_requests,
            "successful": successful,
            "failed": failed,
            "processing": processing,
            "total_celo_sent": round(float(total_celo_sent), 4),
            "today_requests": today_requests,
            "today_celo_sent": round(float(today_celo_sent), 4),
        }

    # --- System Settings (Pause / Resume) ---

    async def get_setting(self, key: str, default: str = "") -> str:
        """Retrieve key-value configuration from SQLite."""
        async with self.connect() as conn:
            async with conn.execute(
                "SELECT value FROM settings WHERE key = ?;", (key,)
            ) as cursor:
                row = await cursor.fetchone()
                return row["value"] if row else default

    async def set_setting(self, key: str, value: str) -> None:
        """Insert or replace key-value configuration."""
        async with self.connect() as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?);",
                (key, value),
            )
            await conn.commit()

    # --- Durable browser sessions ---

    async def create_session(self, session_id: str, user_id: int | None, is_admin: bool, expires_at: datetime | str) -> None:
        try:
            async with self.connect() as conn:
                await conn.execute(
                    "INSERT INTO sessions (session_id, user_id, is_admin, expires_at) VALUES (?, ?, ?, ?);",
                    (session_id, user_id, bool(is_admin) if self.using_postgres else (1 if is_admin else 0), expires_at),
                )
                await conn.commit()
        except Exception:
            # Deliberately omit the opaque session ID and all credential material.
            logger.exception("Unable to persist a session for user ID %s.", user_id)
            raise

    async def get_session(self, session_id: str) -> Optional[dict[str, Any]]:
        async with self.connect() as conn:
            async with conn.execute(
                "SELECT * FROM sessions WHERE session_id = ? AND invalidated_at IS NULL AND expires_at > CURRENT_TIMESTAMP;",
                (session_id,),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def invalidate_session(self, session_id: str) -> None:
        async with self.connect() as conn:
            await conn.execute("UPDATE sessions SET invalidated_at = CURRENT_TIMESTAMP WHERE session_id = ?;", (session_id,))
            await conn.commit()

    async def is_bot_paused(self) -> bool:
        """Check whether payments are paused by admin."""
        val = await self.get_setting("payments_paused", "false")
        return val.lower() in ("true", "1", "yes")

    async def set_bot_paused(self, paused: bool) -> None:
        """Pause or resume payments."""
        await self.set_setting("payments_paused", "true" if paused else "false")

    # Legacy alias
    async def is_faucet_paused(self) -> bool:
        return await self.is_bot_paused()

    async def reset_all_users_and_wallets(self) -> dict[str, Any]:
        """Admin reset: wipe all users, connected/imported wallets, claims, and payment records for a clean slate."""
        async with self.connect() as conn:
            await conn.execute("DELETE FROM usat_payments;")
            await conn.execute("DELETE FROM user_wallets;")
            await conn.execute("DELETE FROM claims;")
            await conn.execute("DELETE FROM sessions;")
            await conn.execute("DELETE FROM users;")
            if not self.using_postgres:
                await conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('users', 'user_wallets', 'usat_payments', 'claims');")
            await conn.commit()
        return {"success": True, "message": "All user accounts, wallets, claims, and payments have been completely reset."}


# Global database instance
db = Database()
