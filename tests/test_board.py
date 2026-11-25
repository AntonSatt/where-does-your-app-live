"""Tonight's board: what goes on it, memory storage and the HTTP API."""

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import main
from app.board import Entry, MemoryBoard, configured_dsn, connect_later, entry_from_result, summarize


def test_connect_later_moves_the_board_once_postgres_answers():
    fallback = MemoryBoard()
    target = MemoryBoard()  # stands in for Postgres
    attempts = []
    installed = []

    async def connect(dsn):
        attempts.append(dsn)
        if len(attempts) < 3:
            raise ConnectionRefusedError
        return target

    async def run():
        await fallback.record(Entry("a.com", "european", "Hetzner", "DE", at(2)))
        await fallback.record(Entry("b.com", "us_owned", "Amazon Web Services", "US", at(1)))
        await connect_later("postgresql://db/app", fallback, installed.append, connect=connect, delay=0)
        return await target.recent(at(60), 10)

    entries = asyncio.run(run())
    assert len(attempts) == 3 and installed == [target]
    assert [e.domain for e in entries] == ["b.com", "a.com"]  # carried over, order kept


def test_configured_dsn(monkeypatch):
    for name in ("DATABASE_URL", "PGHOST"):
        monkeypatch.delenv(name, raising=False)
    assert configured_dsn() is None  # nothing set: memory
    monkeypatch.setenv("DATABASE_URL", " ")
    assert configured_dsn() is None
    monkeypatch.setenv("PGHOST", "db")
    assert configured_dsn() == ""  # asyncpg reads the PG* variables itself
    monkeypatch.setenv("DATABASE_URL", "postgresql://db/app")
    assert configured_dsn() == "postgresql://db/app"  # DATABASE_URL wins


def result(domain, code="european", hosts=None):
    if hosts is None:
        hosts = [{"owner": "Hetzner", "owner_country": "DE"}]
    return {"domain": domain, "verdict": {"code": code}, "hosts": hosts}


def at(minutes_ago):
    return datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)


def test_entry_from_result_keeps_real_domains():
    e = entry_from_result(result("hetzner.com"))
    assert (e.domain, e.verdict, e.owner, e.owner_country) == ("hetzner.com", "european", "Hetzner", "DE")


def test_entry_from_result_skips_ips_and_empty_answers():
    # An IP address could be someone's home connection.
    assert entry_from_result(result("203.0.113.7")) is None
    assert entry_from_result(result("2001:db8::1")) is None
    assert entry_from_result(result("example.com", code="unknown", hosts=[])) is None


def test_memory_board_keeps_the_latest_per_domain_newest_first():
    board = MemoryBoard()

    async def run():
        await board.record(Entry("a.com", "european", "Hetzner", "DE", at(30)))
        await board.record(Entry("b.com", "us_owned", "Amazon Web Services", "US", at(20)))
        await board.record(Entry("a.com", "us_front_door", "Cloudflare", "US", at(10)))
        return await board.recent(at(60), 10)

    entries = asyncio.run(run())
    assert [(e.domain, e.verdict) for e in entries] == [("a.com", "us_front_door"), ("b.com", "us_owned")]


def test_memory_board_window_and_limit():
    board = MemoryBoard()

    async def run():
        await board.record(Entry("old.com", "european", None, None, at(24 * 60)))
        for i in range(5):
            await board.record(Entry(f"n{i}.com", "european", None, None, at(5 - i)))
        return await board.recent(at(60), 3)

    entries = asyncio.run(run())
    assert [e.domain for e in entries] == ["n4.com", "n3.com", "n2.com"]


def test_summarize_counts_in_a_fixed_order():
    entries = [Entry(f"{i}.com", code, None, None, at(1)) for i, code in enumerate(["us_owned", "european", "european"])]
    assert list(summarize(entries).items()) == [("european", 2), ("us_owned", 1)]


class FakeLookup:
    async def run(self, domain):
        return {**result(domain), "cached": False}


def test_api_records_lookups_on_the_board():
    main.app.state.board = MemoryBoard()
    main.app.state.lookup = FakeLookup()
    client = TestClient(main.app)  # no lifespan: no network, state set above

    assert client.get("/api/lookup", params={"domain": "hetzner.com"}).status_code == 200
    body = client.get("/api/board").json()
    assert body["storage"] == "memory"
    assert body["total"] == 1 and body["counts"] == {"european": 1}
    assert body["entries"][0]["domain"] == "hetzner.com"

    # board=false answers as usual but keeps the domain off the public board.
    assert client.get("/api/lookup", params={"domain": "intranet.example.com", "board": "false"}).status_code == 200
    assert [e["domain"] for e in client.get("/api/board").json()["entries"]] == ["hetzner.com"]
