"""The Postgres board against a real database.

Skipped unless TEST_DATABASE_URL is set. Runs in a throwaway schema that is
dropped afterwards, so it never touches existing tables. For a local run:

    docker run -d --rm --name wdyal-pg -p 127.0.0.1:55432:5432 \
        -e POSTGRES_HOST_AUTH_METHOD=trust postgres:17-alpine
    TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55432/postgres .venv/bin/pytest
"""

import asyncio
import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import asyncpg
import pytest

from app.board import Entry, PostgresBoard

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is not set")


def at(minutes_ago):
    return datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)


def test_postgres_board_round_trip():
    schema = f"test_board_{secrets.token_hex(4)}"

    async def run():
        admin = await asyncpg.connect(DSN)
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        try:
            board = await PostgresBoard.connect(DSN, search_path=schema)
            try:
                # Connecting twice must be harmless: the schema statements are idempotent.
                again = await PostgresBoard.connect(DSN, search_path=schema)
                await again.close()

                await board.record(Entry("a.com", "european", "Hetzner", "DE", at(30)))
                await board.record(Entry("b.com", "us_owned", "Amazon Web Services", "US", at(20)))
                await board.record(Entry("a.com", "us_front_door", "Cloudflare", "US", at(10)))
                await board.record(Entry("old.com", "european", None, None, at(24 * 60)))
                entries = await board.recent(at(60), 10)
                stored = await admin.fetchval(f'SELECT count(*) FROM "{schema}".lookups')
            finally:
                await board.close()
        finally:
            await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
            await admin.close()
        return entries, stored

    entries, stored = asyncio.run(run())
    assert [(e.domain, e.verdict) for e in entries] == [("a.com", "us_front_door"), ("b.com", "us_owned")]
    assert entries[0].owner == "Cloudflare" and entries[0].at.tzinfo is not None
    assert stored == 4  # every lookup is kept; the board shows the latest per domain


def test_replicas_starting_together_and_pg_variables(monkeypatch):
    """Three replicas create the table at once, all through the PG* variables."""
    url = urlsplit(DSN)
    for name, value in {"PGHOST": url.hostname, "PGPORT": str(url.port or 5432), "PGUSER": url.username,
                        "PGPASSWORD": url.password, "PGDATABASE": url.path.lstrip("/")}.items():
        if value:
            monkeypatch.setenv(name, value)
    schema = f"test_board_{secrets.token_hex(4)}"

    async def run():
        admin = await asyncpg.connect(DSN)
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        try:
            boards = await asyncio.gather(*(PostgresBoard.connect("", search_path=schema) for _ in range(3)))
            await boards[0].record(Entry("a.com", "european", "Hetzner", "DE", at(1)))
            seen = [len(await b.recent(at(60), 10)) for b in boards]
            for b in boards:
                await b.close()
        finally:
            await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
            await admin.close()
        return seen

    assert asyncio.run(run()) == [1, 1, 1]  # one shared table, every replica sees the lookup
