"""Verify the curated ASN table in app/owners.py against RIPEstat.

Prints each ASN with the holder name RIPEstat reports, so a wrong or
reassigned ASN stands out. Run from the repository root:

    python scripts/check_owners.py
"""

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.owners import ASN_OWNERS, ORGS  # noqa: E402

URL = "https://stat.ripe.net/data/as-overview/data.json"


def main() -> None:
    with httpx.Client(timeout=15) as client:
        for asn, key in sorted(ASN_OWNERS.items(), key=lambda kv: kv[1]):
            r = client.get(URL, params={"resource": f"AS{asn}", "sourceapp": "where-does-your-app-live"})
            holder = r.json()["data"].get("holder") or "(no holder)"
            print(f"{ORGS[key].name:28} AS{asn:<7} {holder}")


if __name__ == "__main__":
    main()
