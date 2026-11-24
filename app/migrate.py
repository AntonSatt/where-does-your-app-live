"""Create or update the board's table. Safe to run any number of times.

Meant as a deploy step before the app starts:

    python -m app.migrate

The app also runs the same statements at startup, so this is optional.
"""

from __future__ import annotations

import asyncio
import os
import sys

import asyncpg

from .board import SCHEMA


async def migrate(dsn: str, attempts: int = 10) -> None:
    for attempt in range(1, attempts + 1):
        try:
            conn = await asyncpg.connect(dsn, timeout=10)
            break
        except Exception as e:  # noqa: BLE001 - the database may still be starting
            if attempt == attempts:
                raise
            print(f"waiting for Postgres ({type(e).__name__}), attempt {attempt} of {attempts}", flush=True)
            await asyncio.sleep(min(2 * attempt, 10))
    try:
        await conn.execute(SCHEMA)
    finally:
        await conn.close()


def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is not set, nothing to migrate")
        return 0
    asyncio.run(migrate(dsn))
    print("board schema is up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
