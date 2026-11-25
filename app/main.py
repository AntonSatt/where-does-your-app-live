"""HTTP API and the single page."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse

from .board import (
    COUNTED,
    SHOWN,
    WINDOW_HOURS,
    configured_dsn,
    connect_later,
    entry_from_result,
    open_board,
    summarize,
    window_start,
)
from .cache import TTLCache
from .cloud import CloudRanges
from .lookup import DnsError, InputError, Lookup, NotFound
from .ripestat import RipeStat, UpstreamError

STATIC = Path(__file__).parent / "static"
REQUEST_TIMEOUT = 25

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    client = httpx.AsyncClient(timeout=10, headers={"User-Agent": "where-does-your-app-live"})
    cache = TTLCache()
    cloud = CloudRanges(client)
    app.state.lookup = Lookup(RipeStat(client, cache), cloud, cache)
    dsn = configured_dsn()
    app.state.board = await open_board(dsn)
    tasks = [asyncio.create_task(cloud.refresh())]
    if dsn is not None and app.state.board.storage == "memory":
        # The database often starts after the app on Kubernetes; switch over when it answers.
        tasks.append(asyncio.create_task(
            connect_later(dsn, app.state.board, lambda board: setattr(app.state, "board", board))))
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await app.state.board.close()
        await client.aclose()


app = FastAPI(title="Where does your app live?", lifespan=lifespan, docs_url="/api/docs", redoc_url=None)


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


@app.get("/api/lookup")
async def lookup(
    domain: str = Query(..., max_length=2048, description="Domain, URL or IP address"),
    board: bool = Query(True, description="Put the domain on the public board. false keeps it off."),
):
    try:
        result = await asyncio.wait_for(app.state.lookup.run(domain), REQUEST_TIMEOUT)
    except InputError as e:
        return _error(400, str(e))
    except NotFound:
        return _error(404, "That domain doesn't exist.")
    except DnsError:
        return _error(502, "DNS didn't answer for that domain. Try again in a moment.")
    except UpstreamError:
        return _error(502, "The network registry (RIPEstat) isn't answering right now. Try again in a moment.")
    except TimeoutError:
        return _error(504, "That took too long. Try again in a moment.")

    entry = entry_from_result(result) if board else None
    if entry:
        try:
            await app.state.board.record(entry)
        except Exception:  # noqa: BLE001 - the board is a bonus; the answer still goes out
            log.exception("could not record %s on the board", entry.domain)
    return result


@app.get("/api/board")
async def board():
    """The domains checked in the last BOARD_WINDOW_HOURS hours, newest first."""
    store = app.state.board
    try:
        entries = await store.recent(window_start(), COUNTED)
    except Exception:  # noqa: BLE001
        log.exception("could not read the board")
        return _error(503, "The board isn't available right now.")
    return {
        "storage": store.storage,
        "note": store.note,
        "window_hours": WINDOW_HOURS,
        "total": len(entries),
        "counts": summarize(entries),
        "entries": [e.to_json() for e in entries[:SHOWN]],
    }


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(STATIC / "index.html")
