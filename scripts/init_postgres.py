"""Initialize the non-destructive PostgreSQL schema used in production.

Run only with NODE_ENV=production and DATABASE_URL set by the shell/Render.
This creates missing tables and indexes; it never drops or truncates data.
"""
from __future__ import annotations

import asyncio
import os

from database import db


async def main() -> None:
    if os.getenv("NODE_ENV", "").lower() != "production":
        raise RuntimeError("Set NODE_ENV=production before initializing PostgreSQL.")
    if not os.getenv("DATABASE_URL"):
        raise RuntimeError("DATABASE_URL is required.")
    await db.init_db()


if __name__ == "__main__":
    asyncio.run(main())
