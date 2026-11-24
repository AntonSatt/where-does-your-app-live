"""Published IP ranges from AWS and Google Cloud, to name the exact region.

Both providers publish which prefixes belong to which region and service.
An address in eu-north-1 is still on a US company's infrastructure, and the
region makes that point concrete.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import time
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)

AWS_URL = "https://ip-ranges.amazonaws.com/ip-ranges.json"
GCP_URL = "https://www.gstatic.com/ipranges/cloud.json"
REFRESH_SECONDS = 24 * 3600
RETRY_SECONDS = 300

# Region code to place, for the regions people are most likely to meet.
REGION_PLACES = {
    "eu-north-1": "Stockholm",
    "eu-west-1": "Ireland",
    "eu-west-2": "London",
    "eu-west-3": "Paris",
    "eu-central-1": "Frankfurt",
    "eu-central-2": "Zurich",
    "eu-south-1": "Milan",
    "eu-south-2": "Spain",
    "us-east-1": "N. Virginia",
    "us-east-2": "Ohio",
    "us-west-1": "N. California",
    "us-west-2": "Oregon",
    "europe-north1": "Finland",
    "europe-north2": "Stockholm",
    "europe-west1": "Belgium",
    "europe-west2": "London",
    "europe-west3": "Frankfurt",
    "europe-west4": "Netherlands",
    "europe-west6": "Zurich",
    "europe-west9": "Paris",
    "us-central1": "Iowa",
    "us-east4": "N. Virginia",
}


@dataclass(frozen=True)
class CloudRange:
    network: ipaddress.IPv4Network | ipaddress.IPv6Network
    provider: str
    region: str
    service: str


@dataclass(frozen=True)
class CloudMatch:
    provider: str
    region: str
    service: str

    @property
    def place(self) -> str | None:
        return REGION_PLACES.get(self.region)


def parse_aws(doc: dict) -> list[CloudRange]:
    out = []
    for p in doc.get("prefixes", []):
        out.append(CloudRange(ipaddress.ip_network(p["ip_prefix"]), "aws", p["region"], p["service"]))
    for p in doc.get("ipv6_prefixes", []):
        out.append(CloudRange(ipaddress.ip_network(p["ipv6_prefix"]), "aws", p["region"], p["service"]))
    return out


def parse_gcp(doc: dict) -> list[CloudRange]:
    out = []
    for p in doc.get("prefixes", []):
        prefix = p.get("ipv4Prefix") or p.get("ipv6Prefix")
        if prefix:
            out.append(CloudRange(ipaddress.ip_network(prefix), "gcp", p.get("scope", ""), p.get("service", "")))
    return out


def best_match(ranges: list[CloudRange], ip: str) -> CloudMatch | None:
    """Most specific prefix wins. AWS lists every prefix under the catch-all
    service AMAZON as well, so a named service beats it on a tie."""
    addr = ipaddress.ip_address(ip)
    best: CloudRange | None = None
    for r in ranges:
        if r.network.version != addr.version or addr not in r.network:
            continue
        if (
            best is None
            or r.network.prefixlen > best.network.prefixlen
            or (r.network.prefixlen == best.network.prefixlen and best.service == "AMAZON" and r.service != "AMAZON")
        ):
            best = r
    if best is None:
        return None
    return CloudMatch(best.provider, best.region, best.service)


class CloudRanges:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._ranges: list[CloudRange] = []
        self._loaded_at = 0.0
        self._attempted_at = float("-inf")
        self._lock = asyncio.Lock()

    async def refresh(self) -> None:
        async with self._lock:
            now = time.monotonic()
            if self._ranges and now - self._loaded_at < REFRESH_SECONDS:
                return
            if now - self._attempted_at < RETRY_SECONDS:
                return  # a recent attempt failed; do not stall every request on it
            self._attempted_at = now
            ranges: list[CloudRange] = []
            for url, parse in ((AWS_URL, parse_aws), (GCP_URL, parse_gcp)):
                try:
                    r = await self._client.get(url, timeout=20)
                    r.raise_for_status()
                    ranges.extend(parse(r.json()))
                except (httpx.HTTPError, ValueError, KeyError) as e:
                    log.warning("could not load %s: %s", url, e)
            if ranges:
                self._ranges = ranges
                self._loaded_at = time.monotonic()

    async def lookup(self, ip: str) -> CloudMatch | None:
        if not self._ranges or time.monotonic() - self._loaded_at > REFRESH_SECONDS:
            await self.refresh()
        return best_match(self._ranges, ip)
