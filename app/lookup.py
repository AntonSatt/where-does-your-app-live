"""Domain in, owners and verdict out.

DNS (with the CNAME chain) -> public IPs -> RIPEstat (ASN, holder,
registration country, location) + published cloud ranges -> curated owner
table -> verdict.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import time
from dataclasses import asdict
from urllib.parse import urlsplit

import dns.asyncresolver
import dns.exception
import dns.rdatatype
import dns.resolver

from .cache import MISSING, TTLCache
from .cloud import CloudRanges
from .owners import CnameMatch, clean_holder, match_cname, owner_for_asn, owner_for_domain, owner_for_holder, zone
from .ripestat import Location, RipeStat, UpstreamError
from .verdict import Host, judge

MAX_V4 = 4
MAX_V6 = 2
RESULT_TTL = 300

_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")


class InputError(ValueError):
    """The input is not a domain or address we can look up."""


class NotFound(Exception):
    """The domain does not exist."""


class DnsError(Exception):
    """DNS did not answer."""


def normalize(raw: str) -> str:
    """Accept a domain, a URL or an IP address; return a bare lowercase name."""
    text = (raw or "").strip()
    if not text:
        raise InputError("Enter a domain, for example example.com.")
    if len(text) > 2048:
        raise InputError("That is too long to be a domain.")
    try:
        return str(ipaddress.ip_address(text.strip("[]")))
    except ValueError:
        pass
    if "://" not in text:
        text = "http://" + text
    try:
        host = urlsplit(text).hostname
    except ValueError as e:
        raise InputError("That doesn't look like a domain.") from e
    if not host:
        raise InputError("That doesn't look like a domain.")
    host = host.rstrip(".")
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    try:
        host = host.encode("idna").decode("ascii").lower()
    except UnicodeError as e:
        raise InputError("That domain has characters we can't handle.") from e
    labels = host.split(".")
    if len(host) > 253 or len(labels) < 2 or not all(_LABEL.match(label) for label in labels):
        raise InputError("That doesn't look like a domain.")
    if labels[-1].isdigit():
        raise InputError("That doesn't look like a domain.")
    return host


class Resolver:
    def __init__(self) -> None:
        self._resolver = dns.asyncresolver.Resolver()
        self._resolver.lifetime = 5.0

    async def _query(self, name: str, rdtype: str, attempts: int = 2) -> tuple[list[str], list[str]]:
        for attempt in range(1, attempts + 1):
            try:
                answer = await self._resolver.resolve(name, rdtype)
                break
            except dns.resolver.NXDOMAIN as e:
                raise NotFound(name) from e
            except dns.resolver.NoAnswer:
                return [], []
            except (dns.resolver.NoNameservers, dns.exception.Timeout) as e:
                # A single lost answer is common; one more try usually gets through.
                if attempt == attempts:
                    raise DnsError(name) from e
        cnames = [
            str(rr.target).rstrip(".")
            for rrset in answer.response.answer
            if rrset.rdtype == dns.rdatatype.CNAME
            for rr in rrset
        ]
        return cnames, [rr.address for rr in answer]

    async def resolve(self, name: str) -> tuple[list[str], list[str]]:
        (c4, v4), (c6, v6) = await asyncio.gather(self._query(name, "A"), self._query(name, "AAAA"))
        cnames = list(dict.fromkeys(c4 + c6))
        return cnames, v4[:MAX_V4] + v6[:MAX_V6]


class Lookup:
    def __init__(self, ripe: RipeStat, cloud: CloudRanges, cache: TTLCache, resolver: Resolver | None = None) -> None:
        self._ripe = ripe
        self._cloud = cloud
        self._cache = cache
        self._dns = resolver or Resolver()

    async def run(self, raw: str) -> dict:
        domain = normalize(raw)
        key = f"result:{domain}"
        cached = self._cache.get(key)
        if cached is not MISSING:
            return {**cached, "cached": True}

        started = time.monotonic()
        checked, cnames, ips, notes = await self._addresses(domain)
        public = [ip for ip in ips if ipaddress.ip_address(ip).is_global]
        if len(public) < len(ips):
            notes.append("Private or reserved addresses were skipped. They aren't on the public internet.")

        layers = [m for m in (match_cname(c) for c in cnames) if m]
        hosts = await asyncio.gather(*(self._host(ip) for ip in public))
        verdict = judge(list(hosts), layers)
        verdict.notes = notes + verdict.notes

        result = {
            "domain": domain,
            "checked": checked,
            "verdict": asdict(verdict),
            "cname_chain": cnames,
            "layers": [_layer(m) for m in layers],
            "hosts": [_host_json(h) for h in hosts],
            "took_ms": round((time.monotonic() - started) * 1000),
        }
        self._cache.set(key, result, RESULT_TTL)
        return {**result, "cached": False}

    async def _addresses(self, domain: str) -> tuple[str, list[str], list[str], list[str]]:
        try:
            ipaddress.ip_address(domain)
            return domain, [], [domain], []
        except ValueError:
            pass
        cnames, ips = await self._dns.resolve(domain)
        if ips or domain.startswith("www."):
            return domain, cnames, ips, []
        www = f"www.{domain}"
        try:
            cnames, ips = await self._dns.resolve(www)
        except NotFound:
            return domain, [], [], []
        note = f"{domain} has no address of its own, so we checked {www}."
        return www, cnames, ips, ([note] if ips else [])

    async def _host(self, ip: str) -> Host:
        asn, location, cloud = await asyncio.gather(
            self._ripe.asn_for_ip(ip),
            _soft(self._ripe.location(ip), Location(None, None)),
            self._cloud.lookup(ip),
        )
        owner = owner_for_asn(asn) if asn else None
        holder, registered = None, None
        if asn and owner:
            holder = await _soft(self._ripe.holder(asn), None)
        elif asn:
            # A network the table doesn't list may still give its parent away,
            # by its name or by the domain of its abuse address.
            holder, abuse = await asyncio.gather(
                _soft(self._ripe.holder(asn), None),
                _soft(self._ripe.abuse_domain(asn), None),
            )
            owner = owner_for_holder(holder) or owner_for_domain(abuse)
            if not owner:
                registered = await _soft(self._ripe.registered_country(asn), None)
        return Host(ip, asn, holder, owner, registered, location, cloud)


async def _soft(coro, default):
    try:
        return await coro
    except UpstreamError:
        return default


def _layer(m: CnameMatch) -> dict:
    return {
        "via": m.name,
        "service": m.service,
        "owner": m.org.name,
        "country": m.org.country,
        "zone": zone(m.org.country),
        "region": m.region,
    }


def _host_json(h: Host) -> dict:
    return {
        "ip": h.ip,
        "asn": h.asn,
        "network": clean_holder(h.network),
        "owner": h.name,
        "owner_country": h.country,
        "owner_source": "curated" if h.owner else ("registration" if h.registered_country else None),
        "kind": h.owner.kind if h.owner else None,
        "zone": zone(h.country),
        "anycast": h.anycast,
        "location": None if h.anycast else {"country": h.location.country, "city": h.location.city},
        "cloud": (
            {"provider": h.cloud.provider, "region": h.cloud.region, "place": h.cloud.place, "service": h.cloud.service}
            if h.cloud
            else None
        ),
    }
