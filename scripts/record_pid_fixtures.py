"""Record identifier-service responses for offline tests.

Usage:  python scripts/record_pid_fixtures.py [--only NAME ...]

Each entry in RECORDINGS is requested once and saved under
tests/fixtures/pid/: the response body (trimmed where noted) and, in
manifest.json, the request, every redirect hop and the final status and
content type, so tests can replay the exchange through a fake transport.
"""

import argparse
import datetime
import json
import sys
from pathlib import Path

import httpx

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "pid"
MANIFEST = OUT / "manifest.json"
UA = "cdif-mcp/0.1"
HTML_LIMIT = 2000  # HTML pages are kept only to show what came back

TTL = "text/turtle"
RDF = "application/rdf+xml"
JSON = "application/json"
TERN_PARTY = (
    "https://w3id.org/tern/resources/7b838abc-8023-4a2a-ad2e-92a79be93104"
)
AODN_TERM = "http://vocab.aodn.org.au/def/discovery_parameter/entity/565"
RVA_LDA = (
    "https://vocabs.ardc.edu.au/repository/api/lda/aodn/"
    "aodn-discovery-parameter-vocabulary/version-1-7/resource"
)

# name, url, Accept, method, trim (keys to keep, for JSON), why
RECORDINGS = [
    # -- DOIs
    (
        "doi-handle-found",
        "https://doi.org/api/handles/10.25901/gg27-ck96",
        JSON,
        "GET",
        None,
        "DOI handle API: DOI exists (responseCode 1)",
    ),
    (
        "doi-handle-missing",
        "https://doi.org/api/handles/10.9999/does-not-exist",
        JSON,
        "GET",
        None,
        "DOI handle API: no such DOI (responseCode 100)",
    ),
    (
        "doi-handle-article",
        "https://doi.org/api/handles/10.3390/min11111195",
        JSON,
        "GET",
        None,
        "DOI handle API: the journal-article DOI exists",
    ),
    (
        "doi-ra-datacite",
        "https://doi.org/ra/10.25901/gg27-ck96",
        JSON,
        "GET",
        None,
        "Registration agency of a dataset DOI: DataCite",
    ),
    (
        "doi-ra-crossref",
        "https://doi.org/ra/10.3390/min11111195",
        JSON,
        "GET",
        None,
        "Registration agency of a journal-article DOI: Crossref",
    ),
    (
        "datacite-dataset",
        "https://api.datacite.org/dois/10.25901/gg27-ck96",
        JSON,
        "GET",
        {"data": {"attributes": ["types", "state", "url", "titles"]}},
        "DataCite DOI with resourceTypeGeneral Dataset",
    ),
    (
        "crossref-article",
        "https://api.crossref.org/works/10.3390/min11111195",
        JSON,
        "GET",
        {"message": ["type", "title", "DOI"]},
        "Crossref DOI for a journal article, used as a dataset @id",
    ),
    # -- ORCID, ROR
    (
        "orcid-found",
        "https://pub.orcid.org/v3.0/0000-0002-7591-0303/person",
        JSON,
        "GET",
        ["name"],
        "ORCID public API, anonymous (name only kept)",
    ),
    (
        "orcid-bad-checksum",
        "https://pub.orcid.org/v3.0/0000-0002-7591-0304/person",
        JSON,
        "GET",
        None,
        "ORCID iD with an invalid checksum: 404",
    ),
    (
        "ror-active",
        "https://api.ror.org/v2/organizations/01ej9dk98",
        JSON,
        "GET",
        ["id", "status", "names", "relationships"],
        "Active ROR organisation",
    ),
    (
        "ror-inactive",
        "https://api.ror.org/v2/organizations/00892tw58",
        JSON,
        "GET",
        ["id", "status", "names", "relationships"],
        "Inactive ROR organisation with a successor",
    ),
    (
        "ror-missing",
        "https://api.ror.org/v2/organizations/00000zz00",
        JSON,
        "GET",
        None,
        "Unknown ROR ID: 404",
    ),
    # -- vocabulary concepts
    (
        "nvs-p01",
        "http://vocab.nerc.ac.uk/collection/P01/current/TEMPPR01",
        TTL,
        "GET",
        None,
        "NVS concept; canonical URI has a trailing slash",
    ),
    (
        "nvs-l06",
        "http://vocab.nerc.ac.uk/collection/L06/current/39",
        TTL,
        "GET",
        None,
        "NVS concept with no skos:inScheme",
    ),
    (
        "gcmd-concept",
        "https://gcmd.earthdata.nasa.gov/kms/concept/"
        "9db9cb8c-7d18-4922-990c-b610d22356eb",
        TTL,
        "GET",
        None,
        "GCMD: RDF/XML served as application/xml",
    ),
    (
        "anzsrc-for",
        "https://linked.data.gov.au/def/anzsrc-for/2020/310302",
        TTL,
        "GET",
        None,
        "ANZSRC 2020 FoR, hosted in RVA's linked-data API",
    ),
    (
        "tern-cv",
        "http://linked.data.gov.au/def/tern-cv/"
        "903c614e-e036-4d50-ba50-9ec477c25654",
        TTL,
        "GET",
        None,
        "TERN CV: 3 redirects, application/x-turtle",
    ),
    (
        "aodn-html",
        AODN_TERM,
        TTL,
        "GET",
        None,
        "AODN term: redirect to RVA resource.html, HTML despite Accept",
    ),
    (
        "aodn-ttl",
        f"{RVA_LDA}?uri={AODN_TERM}&_format=ttl",
        TTL,
        "GET",
        None,
        "The same term from RVA with _format=ttl",
    ),
    (
        "qudt-unit",
        "http://qudt.org/vocab/unit/NUM",
        TTL,
        "GET",
        None,
        "QUDT unit: Turtle only, rdfs:label, not a skos:Concept",
    ),
    (
        "afd-taxon",
        "https://biodiversity.org.au/afd/taxa/CHIROPTERA",
        TTL,
        "GET",
        None,
        "AFD: gateway timeout (504) when recorded",
    ),
    (
        "tern-party",
        TERN_PARTY,
        TTL,
        "GET",
        None,
        "TERN w3id party: resolves to an HTML viewer only",
    ),
    # -- other URIs
    (
        "cf-standard-names",
        "http://cf-pcmdi.llnl.gov/documents/cf-standard-names",
        "*/*",
        "GET",
        None,
        "Dead codeSpace URL (404)",
    ),
    (
        "nci-persons-head",
        "https://geonetwork.nci.org.au/geonetwork/srv/resources/persons/",
        "*/*",
        "HEAD",
        None,
        "HEAD returns 400 where GET returns 200",
    ),
    (
        "nci-persons-get",
        "https://geonetwork.nci.org.au/geonetwork/srv/resources/persons/",
        "*/*",
        "GET",
        None,
        "A directory URL used as a party identifier",
    ),
    # -- Research Vocabularies Australia registry
    (
        "rva-vocabulary-aodn",
        "https://vocabs.ardc.edu.au/registry/api/resource/vocabularies/22"
        "?includeVersions=true&includeAccessPoints=true",
        JSON,
        "GET",
        None,
        "RVA-hosted vocabulary: versions, access points",
    ),
    (
        "rva-vocabulary-tern",
        "https://vocabs.ardc.edu.au/registry/api/resource/vocabularies/641"
        "?includeVersions=true&includeAccessPoints=true",
        JSON,
        "GET",
        None,
        "RVA-registered, served by TERN (web page AP)",
    ),
    (
        "rva-vocabulary-anzsrc",
        "https://vocabs.ardc.edu.au/registry/api/resource/vocabularies/316"
        "?includeVersions=true&includeAccessPoints=true",
        JSON,
        "GET",
        None,
        "RVA-hosted vocabulary with a single-segment linked-data path",
    ),
    (
        "rva-vocabularies",
        "https://vocabs.ardc.edu.au/registry/api/resource/vocabularies",
        JSON,
        "GET",
        lambda d: {
            "vocabulary": [
                {
                    k: v[k]
                    for k in ("id", "slug", "title", "owner", "status")
                    if k in v
                }
                for v in d["vocabulary"]
                if v.get("slug") in RVA_SLUGS
            ]
        },
        "RVA vocabulary listing (575 KB), cut to the vocabularies used here",
    ),
]
RVA_SLUGS = {
    "aodn-discovery-parameter-vocabulary",
    "aodn-geographic-extents-vocabulary",
    "aodn-platform-vocabulary",
    "anzsrc-2020-for",
    "tern-vocabularies",
}


def trim(data, keep):
    """Keep only the given keys (nested dicts give keys per level), or
    apply keep if it is a function."""
    if keep is None:
        return data
    if callable(keep):
        return keep(data)
    if isinstance(keep, list):
        return {k: data[k] for k in keep if k in data}
    return {k: trim(data.get(k, {}), sub) for k, sub in keep.items()}


def record(name, url, accept, method, keep):
    with httpx.Client(follow_redirects=True, timeout=60) as client:
        r = client.request(
            method, url, headers={"Accept": accept, "User-Agent": UA}
        )
    hops = [
        {
            "url": str(h.url),
            "status": h.status_code,
            "location": h.headers.get("location"),
        }
        for h in r.history
    ]
    ctype = r.headers.get("content-type", "")
    body = r.content
    if keep is not None and "json" in ctype:
        body = json.dumps(trim(r.json(), keep), indent=1).encode()
    elif "html" in ctype:
        body = body[:HTML_LIMIT]
    ext = (
        "json"
        if "json" in ctype
        else "ttl"
        if "turtle" in ctype
        else "xml"
        if "xml" in ctype
        else "html"
        if "html" in ctype
        else "txt"
    )
    body_file = f"{name}.{ext}" if body else None
    if body_file:
        (OUT / body_file).write_bytes(body)
    return {
        "name": name,
        "request": {"url": url, "accept": accept, "method": method},
        "hops": hops,
        "final_url": str(r.url),
        "status": r.status_code,
        "content_type": ctype,
        "body": body_file,
        "trimmed": keep is not None or "html" in ctype,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    old = {
        e["name"]: e
        for e in (
            json.loads(MANIFEST.read_text())["entries"]
            if MANIFEST.exists()
            else []
        )
    }
    for name, url, accept, method, keep, why in RECORDINGS:
        if args.only and name not in args.only:
            continue
        try:
            entry = record(name, url, accept, method, keep)
        except httpx.HTTPError as e:
            entry = {
                "name": name,
                "request": {"url": url, "accept": accept, "method": method},
                "error": f"{type(e).__name__}: {e}",
            }
        entry["why"] = why
        old[name] = entry
        print(
            f"{entry.get('status', 'ERR')!s:4} {name:22} "
            f"{entry.get('content_type', entry.get('error', ''))[:40]:40} "
            f"hops={len(entry.get('hops', []))}"
        )
    MANIFEST.write_text(
        json.dumps(
            {
                "recorded": datetime.date.today().isoformat(),
                "entries": [old[n] for n, *_ in RECORDINGS if n in old],
            },
            indent=2,
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
