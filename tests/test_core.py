"""Offline tests: input handling, owner mapping, cloud ranges and verdicts."""

import pytest

from app.cloud import CloudMatch, best_match, parse_aws, parse_gcp
from app.lookup import InputError, normalize
from app.owners import ORGS, clean_holder, match_cname, owner_for_asn, owner_for_holder, zone
from app.ripestat import Location
from app.verdict import Host, judge

NOWHERE = Location(None, None)


# --- input -------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("example.com", "example.com"),
        ("  Example.COM.  ", "example.com"),
        ("https://www.example.com/path?q=1", "www.example.com"),
        ("http://user@example.com:8443/", "example.com"),
        ("räksmörgås.se", "xn--rksmrgs-5wao1o.se"),
        ("1.1.1.1", "1.1.1.1"),
        ("[2606:4700:4700::1111]", "2606:4700:4700::1111"),
        ("2a01:4f8::1", "2a01:4f8::1"),
    ],
)
def test_normalize_accepts(raw, expected):
    assert normalize(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "localhost", "foo", "a..b.com", "-bad.com", "bad-.com", "1.2.3", "x" * 300 + ".com"])
def test_normalize_rejects(raw):
    with pytest.raises(InputError):
        normalize(raw)


# --- owners ------------------------------------------------------------------

def test_parent_company_beats_registration_country():
    # Akamai registers this network in the Netherlands, but Akamai is a US company.
    assert owner_for_asn(20940).country == "US"
    assert owner_for_asn(202053).name == "UpCloud"
    assert owner_for_asn(999999) is None


def test_known_company_by_network_name():
    # AS33905 isn't in the table, but its name gives Akamai away (sr.se, 2026-11-25).
    assert owner_for_holder("AKAMAI-AMS Akamai International B.V..").key == "akamai"
    assert owner_for_holder("LINODE-AP Linode, LLC").key == "akamai-cloud"
    assert owner_for_holder("AMAZON-02 - Amazon.com, Inc.").key == "aws"
    assert owner_for_holder("Amazonas Telecom") is None  # a Brazilian state, not Amazon
    assert owner_for_holder("Swedish_Tax_Agency Swedish Tax Agency") is None
    assert owner_for_holder(None) is None


def test_akamai_subsidiary_is_a_us_front_door():
    akamai_nl = Host("192.0.2.1", 33905, "AKAMAI-AMS Akamai International B.V.",
                     owner_for_holder("AKAMAI-AMS Akamai International B.V."), None, NOWHERE)
    assert judge([akamai_nl], []).code == "us_front_door"


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("Swedish_Tax_Agency Swedish Tax Agency", "Swedish Tax Agency"),
        ("AKAMAI-AMS Akamai International B.V..", "Akamai International B.V."),
        ("SUNET - Vetenskapsradet / SUNET", "Vetenskapsradet / SUNET"),
        ("BASEFARM-SE-ASN Orange Business Digital Sweden AB", "Orange Business Digital Sweden AB"),
        ("EPISERVER_AS Optimizely AB", "Optimizely AB"),
        ("PROTON Proton AG", "Proton AG"),
        ("radware Radware Ltd", "Radware Ltd"),
        ("UPCLOUD UpCloud Ltd", "UpCloud Ltd"),
        # Not handles: these stay whole.
        ("IBM Corporation", "IBM Corporation"),
        ("OVH SAS", "OVH SAS"),
        ("T-Mobile Netherlands B.V.", "T-Mobile Netherlands B.V."),
        ("Telia Company AB", "Telia Company AB"),
        ("Foo Bar - Baz", "Foo Bar - Baz"),
    ],
)
def test_clean_holder(raw, clean):
    assert clean_holder(raw) == clean


def test_cname_rules():
    m = match_cname("my-alb-123.eu-north-1.elb.amazonaws.com.")
    assert m.org.key == "aws" and m.region == "eu-north-1"
    assert match_cname("d111111abcdef8.cloudfront.net").service == "CloudFront CDN"
    assert match_cname("cname.vercel-dns.com").org.key == "vercel"
    assert match_cname("example.com") is None
    # Suffix match only, not substring.
    assert match_cname("cloudfront.net.example.com") is None


def test_zones():
    assert zone("SE") == "eu" and zone("NO") == "eu" and zone("EU") == "eu"
    assert zone("CH") == "europe" and zone("GB") == "europe"
    assert zone("US") == "us" and zone("CA") == "other" and zone(None) == "unknown"


# --- cloud ranges ------------------------------------------------------------

AWS_DOC = {
    "prefixes": [
        {"ip_prefix": "13.48.0.0/15", "region": "eu-north-1", "service": "AMAZON"},
        {"ip_prefix": "13.48.0.0/15", "region": "eu-north-1", "service": "EC2"},
        {"ip_prefix": "13.32.0.0/12", "region": "GLOBAL", "service": "AMAZON"},
    ],
    "ipv6_prefixes": [{"ipv6_prefix": "2a05:d016::/36", "region": "eu-north-1", "service": "EC2"}],
}
GCP_DOC = {"prefixes": [{"ipv4Prefix": "34.88.0.0/16", "service": "Google Cloud", "scope": "europe-north1"}]}


def test_cloud_best_match():
    ranges = parse_aws(AWS_DOC) + parse_gcp(GCP_DOC)
    m = best_match(ranges, "13.48.10.1")
    assert (m.provider, m.region, m.service, m.place) == ("aws", "eu-north-1", "EC2", "Stockholm")
    assert best_match(ranges, "2a05:d016::1").region == "eu-north-1"
    assert best_match(ranges, "34.88.1.1").region == "europe-north1"
    assert best_match(ranges, "8.8.8.8") is None


# --- verdicts ----------------------------------------------------------------

def host(asn, org=None, registered=None, cloud=None):
    return Host("192.0.2.1", asn, f"NET-{asn}", ORGS.get(org), registered, NOWHERE, cloud)


def test_european():
    v = judge([host(24940, "hetzner")], [])
    assert v.code == "european" and v.confidence == "high"


def test_us_owned_in_eu_region_mentions_region():
    v = judge([host(16509, "aws", cloud=CloudMatch("aws", "eu-north-1", "EC2"))], [])
    assert v.code == "us_owned"
    assert "Stockholm (eu-north-1)" in v.detail


def test_us_cdn_is_a_front_door():
    v = judge([host(13335, "cloudflare")], [])
    assert v.code == "us_front_door" and "Cloudflare" in v.detail


def test_cloudfront_is_a_front_door():
    v = judge([host(16509, "aws", cloud=CloudMatch("aws", "GLOBAL", "CLOUDFRONT"))], [])
    assert v.code == "us_front_door" and "Amazon CloudFront" in v.detail


def test_global_accelerator_is_aws_not_a_front_door():
    v = judge([host(16509, "aws", cloud=CloudMatch("aws", "GLOBAL", "GLOBALACCELERATOR"))], [])
    assert v.code == "us_owned"
    assert any("Global Accelerator" in n for n in v.notes)


def test_european_cdn_in_front_stays_european():
    v = judge([host(200325, "bunny")], [])
    assert v.code == "european"


def test_us_platform_in_cname_chain_counts():
    v = judge([host(24940, "hetzner")], [match_cname("cname.vercel-dns.com")])
    assert v.code == "us_owned" and v.headline.startswith("Partly")


def test_registration_country_lowers_confidence():
    v = judge([host(64512, registered="SE")], [])
    assert v.code == "european" and v.confidence == "medium" and v.notes
    # Two addresses on the same network give one note, not two.
    assert len(judge([host(64512, registered="SE"), host(64512, registered="SE")], []).notes) == 1


def test_outside_europe():
    v = judge([host(62679, "shopify")], [])
    assert v.code == "non_european" and "Shopify (CA)" in v.detail


def test_no_hosts():
    assert judge([], []).code == "unknown"
