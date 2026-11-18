"""Download the ISO 19115 fixture records listed in tests/fixtures/iso/manifest.json.

Usage:  python scripts/fetch_iso_fixtures.py [--only FILE ...]

Each record is fetched from its GeoNetwork catalogue
({catalogue}/srv/api/records/{uuid}/formatters/xml), checked to be the ISO
dialect the manifest expects, and saved next to the manifest. The manifest
is updated with the source URL, fetch date and SHA-256 of each file.
"""
import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path

import httpx
from lxml import etree

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "iso"
MANIFEST = FIXTURES / "manifest.json"

# Root element namespace -> dialect.
DIALECTS = {
    "http://standards.iso.org/iso/19115/-3/mdb/2.0": "iso19115-3",
    "http://www.isotc211.org/2005/gmd": "iso19139",
}

# Catalogue XML is untrusted: no entity expansion, no network access.
PARSER = etree.XMLParser(resolve_entities=False, no_network=True,
                         huge_tree=False, remove_blank_text=False)


def fetch(base: str, uuid: str) -> tuple[str, bytes]:
    url = f"{base}/srv/api/records/{uuid}/formatters/xml"
    r = httpx.get(url, follow_redirects=True, timeout=60,
                  headers={"Accept": "application/xml",
                           "User-Agent": "cdif-mcp/0.1"})
    r.raise_for_status()
    return url, r.content


def dialect(xml: bytes) -> str:
    root = etree.fromstring(xml, PARSER)
    return DIALECTS.get(etree.QName(root).namespace, f"unknown ({root.tag})")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", nargs="*", help="fixture file names to refetch")
    args = ap.parse_args()

    manifest = json.loads(MANIFEST.read_text("utf-8"))
    failed = 0
    for rec in manifest["records"]:
        if args.only and rec["file"] not in args.only:
            continue
        try:
            url, xml = fetch(manifest["catalogues"][rec["catalogue"]], rec["uuid"])
            found = dialect(xml)
        except Exception as e:
            print(f"FAIL {rec['file']}: {e}", file=sys.stderr)
            failed += 1
            continue
        if found != rec["standard"]:
            print(f"FAIL {rec['file']}: expected {rec['standard']}, got {found}",
                  file=sys.stderr)
            failed += 1
            continue
        (FIXTURES / rec["file"]).write_bytes(xml)
        rec.update(source=url, fetched=datetime.date.today().isoformat(),
                   sha256=hashlib.sha256(xml).hexdigest())
        print(f"ok   {rec['file']} ({len(xml):,} bytes, {found})")

    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                        "utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
