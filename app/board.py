"""Tonight's board: every domain checked recently, with its verdict.

With DATABASE_URL set, the board lives in Postgres and every replica shares
it. Without it, the board lives in memory: fine on a laptop, but each replica
keeps its own list and a restart empties it.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import asyncpg

log = logging.getLogger(__name__)

WINDOW_HOURS = float(os.environ.get("BOARD_WINDOW_HOURS", "12"))
SHOWN = 50
COUNTED = 500

SCHEMA = """
CREATE TABLE IF NOT EXISTS lookups (
    id            bigserial PRIMARY KEY,
    domain        text        NOT NULL,
    verdict       text        NOT NULL,
    owner         text,
    owner_country text,
    looked_up_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS lookups_looked_up_at_idx ON lookups (looked_up_at DESC);
"""

VERDICT_ORDER = ["european", "us_front_door", "us_owned", "non_european", "unknown"]


@dataclass(frozen=True)
class Entry:
    domain: str
    verdict: str
    owner: str | None
    owner_country: str | None
    at: datetime

    def to_json(self) -> dict:
        return {
            "domain": self.domain,
            "verdict": self.verdict,
            "owner": self.owner,
            "owner_country": self.owner_country,
            "at": self.at.isoformat(),
        }


def entry_from_result(result: dict) -> Entry | None:
    """Only real, public domains go on the board. An IP address could be
    someone's home connection, and a name without servers says nothing."""
    domain = result.get("domain") or ""
    hosts = result.get("hosts") or []
    if not domain or not hosts:
        return None
    try:
        ipaddress.ip_address(domain)
        return None
    except ValueError:
        pass
    first = hosts[0]
    return Entry(
        domain=domain,
        verdict=result["verdict"]["code"],
        owner=first.get("owner"),
        owner_country=first.get("owner_country"),
        at=datetime.now(timezone.utc),
    )


def summarize(entries: list[Entry]) -> dict[str, int]:
    counts = {code: 0 for code in VERDICT_ORDER}
    for e in entries:
        counts[e.verdict] = counts.get(e.verdict, 0) + 1
    return {code: n for code, n in counts.items() if n}


def window_start() -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)


class MemoryBoard:
    storage = "memory"

    def __init__(self, max_items: int = 1000, note: str | None = None) -> None:
        self._items: OrderedDict[str, Entry] = OrderedDict()
        self._max = max_items
        self.note = note

    async def record(self, entry: Entry) -> None:
        self._items.pop(entry.domain, None)
        self._items[entry.domain] = entry
        while len(self._items) > self._max:
            self._items.popitem(last=False)

    async def recent(self, since: datetime, limit: int) -> list[Entry]:
        newest_first = [e for e in reversed(self._items.values()) if e.at >= since]
        return newest_first[:limit]

    async def close(self) -> None:
        pass


class PostgresBoard:
    storage = "postgres"
    note = None

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @classmethod
    async def connect(cls, dsn: str, search_path: str | None = None) -> PostgresBoard:
        settings = {"search_path": search_path} if search_path else None
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5, command_timeout=5, server_settings=settings)
        try:
            await pool.execute(SCHEMA)
        except BaseException:
            await pool.close()
            raise
        return cls(pool)

    async def record(self, entry: Entry) -> None:
        await self._pool.execute(
            "INSERT INTO lookups (domain, verdict, owner, owner_country, looked_up_at) VALUES ($1, $2, $3, $4, $5)",
            entry.domain,
            entry.verdict,
            entry.owner,
            entry.owner_country,
            entry.at,
        )

    async def recent(self, since: datetime, limit: int) -> list[Entry]:
        # Every lookup is kept; the board shows the latest one per domain.
        rows = await self._pool.fetch(
            """
            SELECT domain, verdict, owner, owner_country, looked_up_at FROM (
                SELECT DISTINCT ON (domain) domain, verdict, owner, owner_country, looked_up_at
                FROM lookups
                WHERE looked_up_at >= $1
                ORDER BY domain, looked_up_at DESC
            ) latest
            ORDER BY looked_up_at DESC
            LIMIT $2
            """,
            since,
            limit,
        )
        return [Entry(r["domain"], r["verdict"], r["owner"], r["owner_country"], r["looked_up_at"]) for r in rows]

    async def close(self) -> None:
        await self._pool.close()


async def open_board(dsn: str | None = None, attempts: int = 5) -> MemoryBoard | PostgresBoard:
    dsn = dsn if dsn is not None else os.environ.get("DATABASE_URL")
    if not dsn:
        log.info("DATABASE_URL is not set, tonight's board lives in memory")
        return MemoryBoard()
    for attempt in range(1, attempts + 1):
        try:
            board = await PostgresBoard.connect(dsn)
            log.info("tonight's board lives in Postgres")
            return board
        except Exception as e:  # noqa: BLE001 - any failure here means "no database yet"
            log.warning("Postgres not reachable (attempt %d of %d): %s", attempt, attempts, type(e).__name__)
            if attempt < attempts:
                await asyncio.sleep(min(2 * attempt, 8))
    log.error("could not reach Postgres, falling back to memory for tonight's board")
    return MemoryBoard(note="Postgres was configured but unreachable at startup.")
