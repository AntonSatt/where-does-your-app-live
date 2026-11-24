"""Who really owns a network, and which platforms show up in CNAME chains.

The registration country of an ASN is not the same as the country of the
company behind it: Amazon registers networks in Ireland and Akamai in the
Netherlands, but both are US companies. This curated table maps the networks
that matter most to their parent company. Anything not listed falls back to
the registration country, with lower confidence.

Run scripts/check_owners.py to verify the ASNs against RIPEstat.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# EU and EEA members. CLOUD Act exposure is about the US, so "European" here
# means EU/EEA plus the UK and Switzerland, with the latter two marked as such.
EU_EEA = frozenset(
    "AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE "
    "IS LI NO".split()
)
EUROPE_NON_EU = frozenset({"GB", "CH"})

CDN = "cdn"
CLOUD = "cloud"
HOSTING = "hosting"
PLATFORM = "platform"
NETWORK = "network"


@dataclass(frozen=True)
class Org:
    key: str
    name: str
    country: str  # ISO 3166-1 alpha-2 of the parent company's headquarters
    kind: str


_ORGS = [
    # United States
    Org("cloudflare", "Cloudflare", "US", CDN),
    Org("aws", "Amazon Web Services", "US", CLOUD),
    Org("azure", "Microsoft", "US", CLOUD),
    Org("google", "Google", "US", CLOUD),
    Org("akamai", "Akamai", "US", CDN),
    Org("akamai-cloud", "Akamai Cloud (Linode)", "US", CLOUD),
    Org("fastly", "Fastly", "US", CDN),
    Org("oracle", "Oracle Cloud", "US", CLOUD),
    Org("digitalocean", "DigitalOcean", "US", CLOUD),
    Org("vultr", "Vultr", "US", CLOUD),
    Org("github", "GitHub (Microsoft)", "US", PLATFORM),
    Org("fly", "Fly.io", "US", PLATFORM),
    Org("vercel", "Vercel", "US", PLATFORM),
    Org("netlify", "Netlify", "US", PLATFORM),
    Org("heroku", "Heroku (Salesforce)", "US", PLATFORM),
    Org("meta", "Meta", "US", PLATFORM),
    Org("automattic", "Automattic (WordPress.com)", "US", PLATFORM),
    Org("squarespace", "Squarespace", "US", PLATFORM),
    # Elsewhere outside Europe
    Org("shopify", "Shopify", "CA", PLATFORM),
    Org("wix", "Wix", "IL", PLATFORM),
    # Europe
    Org("hetzner", "Hetzner", "DE", HOSTING),
    Org("ovh", "OVHcloud", "FR", CLOUD),
    Org("scaleway", "Scaleway", "FR", CLOUD),
    Org("upcloud", "UpCloud", "FI", CLOUD),
    Org("ionos", "IONOS", "DE", HOSTING),
    Org("contabo", "Contabo", "DE", HOSTING),
    Org("leaseweb", "Leaseweb", "NL", HOSTING),
    Org("hostinger", "Hostinger", "LT", HOSTING),
    Org("one-com", "one.com", "DK", HOSTING),
    Org("bunny", "bunny.net", "SI", CDN),
    Org("glesys", "GleSYS", "SE", HOSTING),
    Org("bahnhof", "Bahnhof", "SE", HOSTING),
    Org("telia", "Telia", "SE", NETWORK),
    Org("arelion", "Arelion", "SE", NETWORK),
    Org("exoscale", "Exoscale", "CH", CLOUD),
    Org("infomaniak", "Infomaniak", "CH", HOSTING),
]
ORGS: dict[str, Org] = {o.key: o for o in _ORGS}

ASN_OWNERS: dict[int, str] = {
    13335: "cloudflare",
    209242: "cloudflare",
    16509: "aws",
    14618: "aws",
    8987: "aws",
    8075: "azure",
    8068: "azure",
    15169: "google",
    396982: "google",
    19527: "google",
    20940: "akamai",
    16625: "akamai",
    32787: "akamai",
    63949: "akamai-cloud",
    54113: "fastly",
    31898: "oracle",
    14061: "digitalocean",
    20473: "vultr",
    36459: "github",
    40509: "fly",
    32934: "meta",
    2635: "automattic",
    53831: "squarespace",
    62679: "shopify",
    58182: "wix",
    24940: "hetzner",
    213230: "hetzner",
    16276: "ovh",
    12876: "scaleway",
    202053: "upcloud",
    8560: "ionos",
    51167: "contabo",
    60781: "leaseweb",
    28753: "leaseweb",
    47583: "hostinger",
    51468: "one-com",
    200325: "bunny",
    42708: "glesys",
    8473: "bahnhof",
    3301: "telia",
    1299: "arelion",
    61098: "exoscale",
    29222: "infomaniak",
}


@dataclass(frozen=True)
class CnameRule:
    pattern: re.Pattern[str]
    org: str
    service: str


def _rule(suffix_regex: str, org: str, service: str) -> CnameRule:
    return CnameRule(re.compile(rf"(^|\.){suffix_regex}$"), org, service)


CNAME_RULES = [
    _rule(r"cloudfront\.net", "aws", "CloudFront CDN"),
    _rule(r"(?P<region>[a-z]{2}(-gov)?-[a-z]+-\d)\.elb\.amazonaws\.com", "aws", "Elastic Load Balancing"),
    _rule(r"awsglobalaccelerator\.com", "aws", "Global Accelerator"),
    _rule(r"amplifyapp\.com", "aws", "Amplify"),
    _rule(r"s3[.-]([a-z0-9-]+\.)?amazonaws\.com", "aws", "S3"),
    _rule(r"azurefd\.net", "azure", "Azure Front Door"),
    _rule(r"azureedge\.net", "azure", "Azure CDN"),
    _rule(r"trafficmanager\.net", "azure", "Traffic Manager"),
    _rule(r"azurewebsites\.net", "azure", "App Service"),
    _rule(r"(?P<region>[a-z0-9]+)\.cloudapp\.azure\.com", "azure", "Azure VM"),
    _rule(r"cdn\.cloudflare\.net", "cloudflare", "Cloudflare CDN"),
    _rule(r"(akamaiedge|edgekey|edgesuite|akamaized)\.net", "akamai", "Akamai CDN"),
    _rule(r"(fastly|fastlylb)\.net", "fastly", "Fastly CDN"),
    _rule(r"(vercel-dns\.com|vercel\.app)", "vercel", "Vercel"),
    _rule(r"(netlify\.app|netlify\.com|netlifyglobalcdn\.com)", "netlify", "Netlify"),
    _rule(r"github\.io", "github", "GitHub Pages"),
    _rule(r"(herokudns\.com|herokuapp\.com)", "heroku", "Heroku"),
    _rule(r"googlehosted\.com", "google", "Google hosting"),
    _rule(r"myshopify\.com", "shopify", "Shopify"),
    _rule(r"squarespace\.com", "squarespace", "Squarespace"),
    _rule(r"wixdns\.net", "wix", "Wix"),
    _rule(r"(b-cdn\.net|bunny\.net)", "bunny", "bunny.net CDN"),
]


@dataclass(frozen=True)
class CnameMatch:
    name: str
    org: Org
    service: str
    region: str | None


def match_cname(name: str) -> CnameMatch | None:
    name = name.rstrip(".").lower()
    for rule in CNAME_RULES:
        m = rule.pattern.search(name)
        if m:
            region = m.groupdict().get("region")
            return CnameMatch(name, ORGS[rule.org], rule.service, region)
    return None


def owner_for_asn(asn: int) -> Org | None:
    key = ASN_OWNERS.get(asn)
    return ORGS[key] if key else None


def zone(country: str | None) -> str:
    """Return "eu", "europe", "us", "other" or "unknown" for a country code."""
    if not country:
        return "unknown"
    country = country.upper()
    if country in EU_EEA:
        return "eu"
    if country in EUROPE_NON_EU:
        return "europe"
    if country == "US":
        return "us"
    return "other"
