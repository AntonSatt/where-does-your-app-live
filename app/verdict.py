"""Turn what we found into a verdict a person can act on.

Ownership decides the verdict, not location. A US company is subject to the
CLOUD Act wherever its servers stand, and a CDN on anycast addresses has no
single location anyway.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .cloud import CloudMatch
from .owners import CDN, CnameMatch, Org, clean_holder, zone
from .ripestat import Location

EUROPEAN = "european"
US_FRONT_DOOR = "us_front_door"
US_OWNED = "us_owned"
NON_EUROPEAN = "non_european"
UNKNOWN = "unknown"


@dataclass
class Host:
    ip: str
    asn: int | None
    network: str | None  # holder name as registered, e.g. "AMAZON-02 - Amazon.com, Inc."
    owner: Org | None  # curated parent company, if we know it
    registered_country: str | None
    location: Location
    cloud: CloudMatch | None = None

    @property
    def country(self) -> str | None:
        return self.owner.country if self.owner else self.registered_country

    @property
    def name(self) -> str:
        if self.owner:
            return self.owner.name
        return clean_holder(self.network) or (f"AS{self.asn}" if self.asn else "unknown network")

    @property
    def front_door(self) -> bool:
        """A CDN that terminates HTTPS and hides the origin server."""
        return bool(self.owner and self.owner.kind == CDN) or bool(self.cloud and self.cloud.service == "CLOUDFRONT")

    @property
    def anycast(self) -> bool:
        """Edge addresses with no single location worth showing."""
        return self.front_door or bool(self.cloud and self.cloud.service == "GLOBALACCELERATOR")

    @property
    def front_door_name(self) -> str:
        if self.cloud and self.cloud.service == "CLOUDFRONT":
            return "Amazon CloudFront"
        return self.name


@dataclass
class Verdict:
    code: str
    headline: str
    detail: str
    confidence: str
    notes: list[str] = field(default_factory=list)


def _names(items: list[str]) -> str:
    unique = list(dict.fromkeys(items))
    if len(unique) <= 1:
        return "".join(unique)
    return ", ".join(unique[:-1]) + " and " + unique[-1]


def _region_text(hosts: list[Host]) -> str | None:
    for h in hosts:
        if h.cloud and h.cloud.region and h.cloud.region != "GLOBAL":
            place = h.cloud.place
            return f"{place} ({h.cloud.region})" if place else h.cloud.region
    return None


def judge(hosts: list[Host], layers: list[CnameMatch]) -> Verdict:
    if not hosts:
        return Verdict(UNKNOWN, "No public servers found", "The name does not point to any public address.", "high")

    notes: list[str] = []
    for h in hosts:
        if not h.owner and h.registered_country:
            note = (
                f"{h.name}: the country is where the network is registered. "
                "The company behind it can be based somewhere else."
            )
            if note not in notes:  # one per network, not one per address
                notes.append(note)
    confidence = "medium" if notes or any(h.country is None for h in hosts) else "high"
    if any(h.cloud and h.cloud.service == "GLOBALACCELERATOR" for h in hosts):
        notes.append(
            "Traffic enters through AWS Global Accelerator, which can only route to resources inside AWS."
        )

    # Everyone in the path: platforms seen in the CNAME chain, then the networks.
    parties: list[tuple[str, str | None]] = [(m.org.name, m.org.country) for m in layers]
    parties += [(h.name, h.country) for h in hosts]
    zones = {zone(country) for _, country in parties}

    us = [name for name, country in parties if zone(country) == "us"]
    if us:
        all_front = all(h.front_door for h in hosts)
        us_cdns = [h.front_door_name for h in hosts if h.front_door and zone(h.country) == "us"]
        if all_front and us_cdns:
            return Verdict(
                US_FRONT_DOOR,
                "Behind a US-owned CDN",
                f"{_names(us_cdns)} sits in front and terminates HTTPS, so traffic is decrypted on "
                "infrastructure owned by a US company. The origin server is hidden behind it, so "
                "where the app itself runs can't be seen from outside.",
                confidence,
                notes,
            )
        region = _region_text(hosts)
        partly = bool(zones & {"eu", "europe"})
        detail = (
            f"{_names(us)} is a US company. Under the US CLOUD Act, US authorities can demand "
            "the data no matter which country the servers are in."
        )
        if region:
            detail = f"The servers are in {region}, but that doesn't change who owns them. " + detail
        return Verdict(
            US_OWNED,
            "Partly on US-owned infrastructure" if partly else "Runs on US-owned infrastructure",
            detail,
            confidence,
            notes,
        )

    if "unknown" in zones and len(zones) == 1:
        return Verdict(UNKNOWN, "Owner unknown", "We couldn't tell who owns these networks.", "low", notes)

    if zones <= {"eu", "europe", "unknown"}:
        names = _names([name for name, _ in parties])
        if "europe" in zones:
            notes.append("The UK and Switzerland are outside the EU but covered by EU adequacy decisions.")
        if "unknown" in zones:
            confidence = "low"
        return Verdict(
            EUROPEAN,
            "European all the way" if confidence == "high" else "Looks European",
            f"Served by {names}. No US-owned company in the path we can see.",
            confidence,
            notes,
        )

    outside = [f"{name} ({country})" for name, country in parties if zone(country) == "other"]
    return Verdict(
        NON_EUROPEAN,
        "Owned outside Europe",
        f"{_names(outside)} is based outside Europe.",
        confidence,
        notes,
    )
