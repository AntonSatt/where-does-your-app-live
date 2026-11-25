"""RIPEstat Data API client: ASN, holder, registration country and location.

RIPEstat is free and needs no key. It asks automated clients to identify
themselves with `sourceapp`, and we keep concurrency low to be polite.
https://stat.ripe.net/docs/data-api/
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

from .cache import MISSING, TTLCache

BASE = "https://stat.ripe.net/data"
SOURCEAPP = "where-does-your-app-live"


class UpstreamError(Exception):
    """RIPEstat did not answer in a usable way."""


def registrable_domain(host: str) -> str | None:
    """support.aws.com -> aws.com, abuse.example.co.uk -> example.co.uk."""
    labels = [x for x in host.strip().strip(".").lower().split(".") if x]
    if len(labels) < 2:
        return None
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in {"co", "com", "net", "org", "ac", "gov", "or", "ne"}:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


@dataclass(frozen=True)
class Location:
    country: str | None
    city: str | None


class RipeStat:
    def __init__(self, client: httpx.AsyncClient, cache: TTLCache, concurrency: int = 4) -> None:
        self._client = client
        self._cache = cache
        self._sem = asyncio.Semaphore(concurrency)
        self._inflight: dict[str, asyncio.Task] = {}

    async def _call(self, call: str, resource: str, ttl: float) -> dict:
        key = f"ripestat:{call}:{resource}"
        cached = self._cache.get(key)
        if cached is not MISSING:
            return cached
        # Several hosts often share one network: ask RIPEstat once, not once per host.
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(self._fetch(call, resource, ttl, key))
            self._inflight[key] = task
            task.add_done_callback(lambda _: self._inflight.pop(key, None))
        return await asyncio.shield(task)

    async def _fetch(self, call: str, resource: str, ttl: float, key: str) -> dict:
        async with self._sem:
            try:
                r = await self._client.get(
                    f"{BASE}/{call}/data.json",
                    params={"resource": resource, "sourceapp": SOURCEAPP},
                )
                r.raise_for_status()
                data = r.json()["data"]
            except (httpx.HTTPError, ValueError, KeyError) as e:
                raise UpstreamError(f"RIPEstat {call} failed for {resource}") from e
        self._cache.set(key, data, ttl)
        return data

    async def asn_for_ip(self, ip: str) -> int | None:
        data = await self._call("network-info", ip, ttl=3600)
        asns = data.get("asns") or []
        return int(asns[0]) if asns else None

    async def holder(self, asn: int) -> str | None:
        data = await self._call("as-overview", f"AS{asn}", ttl=6 * 3600)
        return data.get("holder")

    async def abuse_domain(self, asn: int) -> str | None:
        """The domain of the network's abuse address, e.g. "akamai.com".

        Subsidiaries register networks under local names, but the abuse
        mailbox usually sits at the parent company's domain.
        """
        data = await self._call("abuse-contact-finder", f"AS{asn}", ttl=6 * 3600)
        for address in data.get("abuse_contacts") or []:
            if "@" in address:
                return registrable_domain(address.rsplit("@", 1)[1])
        return None

    async def registered_country(self, asn: int) -> str | None:
        data = await self._call("rir-stats-country", f"AS{asn}", ttl=6 * 3600)
        located = data.get("located_resources") or []
        return located[0].get("location") if located else None

    async def location(self, ip: str) -> Location:
        data = await self._call("maxmind-geo-lite", ip, ttl=6 * 3600)
        for resource in data.get("located_resources") or []:
            for loc in resource.get("locations") or []:
                return Location(loc.get("country") or None, loc.get("city") or None)
        return Location(None, None)
