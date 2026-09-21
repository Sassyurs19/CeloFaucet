"""Copy an existing SQLite database into PostgreSQL without overwriting records.

This is an explicit one-time migration tool. It never runs during application
startup and uses INSERT ... ON CONFLICT DO NOTHING for every copied row.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sqlite3
from pathlib import Path
from typing import Any

import asyncpg


TABLES = ("users", "user_wallets", "receiving_wallets", "usat_payments", "claims", "settings", "sessions")


def read_rows(path: Path, table: str) -> list[dict[str, Any]]:
    with sqlite3.connect(path) as source:
        source.row_factory = sqlite3.Row
        exists = source.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
        if not exists:
            return []
        return [dict(row) for row in source.execute(f"SELECT * FROM {table}")]


async def copy_table(conn: asyncpg.Connection, path: Path, table: str) -> int:
    rows = read_rows(path, table)
    if not rows:
        return 0
    columns = list(rows[0])
    placeholders = ", ".join(f"${index}" for index in range(1, len(columns) + 1))
    query = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
    values: list[tuple[Any, ...]] = []
    for row in rows:
        if table == "sessions" and row.get("user_id") == 0:
            row["user_id"] = None
        values.append(tuple(row.get(column) for column in columns))
    await conn.executemany(query, values)
    return len(rows)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Safely import existing SQLite data into PostgreSQL.")
    parser.add_argument("--sqlite", required=True, type=Path, help="Path to the existing SQLite database")
    args = parser.parse_args()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required.")
    if not args.sqlite.is_file():
        raise RuntimeError("SQLite database file was not found.")

    connection = await asyncpg.connect(database_url)
    try:
        async with connection.transaction():
            for table in TABLES:
                await copy_table(connection, args.sqlite, table)
            for table in ("users", "user_wallets", "receiving_wallets", "usat_payments", "claims"):
                await connection.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), COALESCE((SELECT MAX(id) FROM {table}), 1), true)")
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(main())
