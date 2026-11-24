"""HTTP API and the single page."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse

from .cache import TTLCache
from .cloud import CloudRanges
from .lookup import DnsError, InputError, Lookup, NotFound
from .ripestat import RipeStat, UpstreamError

STATIC = Path(__file__).parent / "static"
REQUEST_TIMEOUT = 25

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    client = httpx.AsyncClient(timeout=10, headers={"User-Agent": "where-does-your-app-live"})
    cache = TTLCache()
    cloud = CloudRanges(client)
    app.state.lookup = Lookup(RipeStat(client, cache), cloud, cache)
    preload = asyncio.create_task(cloud.refresh())
    try:
        yield
    finally:
        preload.cancel()
        await client.aclose()


app = FastAPI(title="Where does your app live?", lifespan=lifespan, docs_url="/api/docs", redoc_url=None)


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


@app.get("/api/lookup")
async def lookup(domain: str = Query(..., max_length=2048, description="Domain, URL or IP address")):
    try:
        return await asyncio.wait_for(app.state.lookup.run(domain), REQUEST_TIMEOUT)
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


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(STATIC / "index.html")
