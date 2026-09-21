"""Small asyncpg compatibility layer for the existing aiosqlite data access API.

It deliberately accepts only application-owned SQL. Values remain parameterized;
the adapter converts SQLite positional placeholders to PostgreSQL placeholders.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncpg


_ID_TABLES = {"users", "user_wallets", "receiving_wallets", "usat_payments", "claims"}


def _sql(sql: str) -> str:
    statement = sql.strip()
    statement = re.sub(r"\bdatetime\('now',\s*'(-?\d+) minutes'\)", r"NOW() + INTERVAL '\1 minutes'", statement)
    statement = statement.replace("datetime(p.created_at, 'localtime')", "p.created_at")
    statement = statement.replace("date(p.created_at, 'localtime')", "DATE(p.created_at)")
    statement = statement.replace("time(p.created_at, 'localtime')", "CAST(p.created_at AS TIME)")
    statement = statement.replace("date(created_at)", "DATE(created_at)")
    statement = statement.replace("date('now')", "CURRENT_DATE")
    statement = statement.replace("CURRENT_TIMESTAMP", "CURRENT_TIMESTAMP")
    statement = re.sub(r"INSERT\s+OR\s+REPLACE\s+INTO\s+settings\s*\(key,\s*value\)\s*VALUES\s*\(\?,\s*\?\)",
                       "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", statement,
                       flags=re.IGNORECASE)
    statement = re.sub(r"CAST\(([^)]+)\s+AS\s+TEXT\)", r"CAST(\1 AS TEXT)", statement, flags=re.IGNORECASE)
    index = 0
    def marker(_: re.Match[str]) -> str:
        nonlocal index
        index += 1
        return f"${index}"
    statement = re.sub(r"\?", marker, statement)
    statement = re.sub(r"\bdate\(\$(\d+)\)", r"$\1::date", statement, flags=re.IGNORECASE)
    statement = re.sub(r"\btime\(\$(\d+)\)", r"$\1::time", statement, flags=re.IGNORECASE)
    return statement


def _value(value: Any) -> Any:
    """Keep the existing SQLite API's JSON-friendly scalar values."""
    if isinstance(value, (datetime, date, time)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else str(value)
    return value


class PostgresCursor:
    def __init__(self, connection: "PostgresConnection", sql: str, params: Any = ()) -> None:
        self.connection = connection
        self.sql = sql
        self.params = tuple(params or ())
        self.rows: list[dict[str, Any]] | None = None
        self.rowcount = 0
        self.lastrowid = 0

    async def _run(self) -> "PostgresCursor":
        if self.rows is not None:
            return self
        query = _sql(self.sql)
        is_select = query.upper().startswith("SELECT") or " RETURNING " in query.upper()
        insert = re.match(r"INSERT\s+INTO\s+([a-z_]+)", query, re.IGNORECASE)
        if insert and insert.group(1).lower() in _ID_TABLES and " RETURNING " not in query.upper():
            query = query.rstrip(";") + " RETURNING id"
            is_select = True
        if is_select:
            records = await self.connection.raw.fetch(query, *self.params)
            self.rows = [{key: _value(value) for key, value in dict(record).items()} for record in records]
            if self.rows and "id" in self.rows[0]:
                self.lastrowid = int(self.rows[0]["id"])
            self.rowcount = len(self.rows)
        else:
            result = await self.connection.raw.execute(query, *self.params)
            self.rows = []
            match = re.search(r"(\d+)$", result)
            self.rowcount = int(match.group(1)) if match else 0
        return self

    def __await__(self):
        return self._run().__await__()

    async def __aenter__(self) -> "PostgresCursor":
        return await self._run()

    async def __aexit__(self, *_: object) -> None:
        return None

    async def fetchone(self) -> dict[str, Any] | None:
        await self._run()
        return self.rows[0] if self.rows else None

    async def fetchall(self) -> list[dict[str, Any]]:
        await self._run()
        return self.rows or []


class PostgresConnection:
    def __init__(self, raw: Any) -> None:
        self.raw = raw

    def execute(self, sql: str, params: Any = ()) -> PostgresCursor:
        return PostgresCursor(self, sql, params)

    async def commit(self) -> None:
        # Transactions are managed by the Database context manager.
        return None
