"""Fetching ISO 19115 XML from GeoNetwork catalogues."""

import re

import httpx

from .iso19115 import CrosswalkError

MAX_XML_BYTES = 20_000_000

_UUID = re.compile(r"[\w.\-]{1,200}")
_UUID_IN_TEXT = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)
# GeoNetwork UI and API URLs that identify a record:
#   .../srv/eng/catalog.search#/metadata/{uuid}
#   .../srv/api/records/{uuid}            (record page or API)
#   .../srv/api/records/{uuid}/formatters/...
_GN_RECORD = re.compile(
    r"^(?P<base>https?://.+?)/srv/(?:\w+/catalog\.search#/metadata/"
    r"|api/records/)(?P<uuid>[\w.\-]+)"
)


def record_xml_url(
    url: str | None = None,
    uuid: str | None = None,
    catalogue: str | None = None,
) -> str:
    """Resolve the arguments to the URL of a record's ISO XML.

    Give url, or uuid together with catalogue (the GeoNetwork base URL,
    e.g. https://geonetwork.tern.org.au/geonetwork). A url that is a
    GeoNetwork record page (catalog.search#/metadata/..., or
    /srv/api/records/...) is rewritten to the record's XML; any other url
    is used as given.
    """
    if url is not None:
        if not re.match(r"https?://", url):
            raise CrosswalkError(f"Not a web URL: {url!r}.")
        if m := _GN_RECORD.match(url):
            return _formatter(m["base"], m["uuid"])
        return url
    if not (uuid and catalogue):
        raise CrosswalkError("Give url, or both uuid and catalogue.")
    if not _UUID.fullmatch(uuid):
        raise CrosswalkError(f"Not a record identifier: {uuid!r}.")
    return _formatter(catalogue_base(catalogue), uuid)


def catalogue_base(catalogue: str) -> str:
    """'https://x/geonetwork/srv/eng/' -> 'https://x/geonetwork'."""
    if not re.match(r"https?://", catalogue):
        raise CrosswalkError(f"catalogue is not a web URL: {catalogue!r}.")
    return re.sub(r"/srv(/.*)?$", "", catalogue.rstrip("/"))


def _formatter(base: str, uuid: str) -> str:
    return f"{base}/srv/api/records/{uuid}/formatters/xml"


# -- Listing a catalogue's records -----------------------------------------

_HEADERS = {"Accept": "application/json", "User-Agent": "cdif-mcp/0.1"}
GN4_MAX_RESULTS = 10_000  # Elasticsearch's default result window
GN3_PAGE = 100  # GeoNetwork 3's q service returns at most 100 per page


def geonetwork_version(base: str) -> str:
    """The catalogue's GeoNetwork version, from /srv/api/site."""
    r = httpx.get(f"{base}/srv/api/site", headers=_HEADERS, timeout=30)
    r.raise_for_status()
    return str(r.json().get("system/platform/version", "unknown"))


def list_dataset_uuids(catalogue: str) -> tuple[str, list[str]]:
    """List the uuids of a catalogue's public records whose resource type
    is 'dataset'. Returns (GeoNetwork version, sorted uuids).

    GeoNetwork 4 is searched through its Elasticsearch API. GeoNetwork 3
    (e.g. AODN's 3.12) refuses that API, so its paged q service is used.
    """
    base = catalogue_base(catalogue)
    version = geonetwork_version(base)
    if version.split(".")[0] in ("3", "2"):
        uuids = _list_gn3(base)
    else:
        uuids = _list_gn4(base)
    return version, sorted(set(uuids))


def _list_gn4(base: str) -> list[str]:
    query = {
        "size": GN4_MAX_RESULTS,
        "_source": ["uuid"],
        "track_total_hits": True,
        "query": {
            "bool": {
                "must": [
                    {"term": {"isTemplate": "n"}},
                    {"term": {"resourceType": "dataset"}},
                ]
            }
        },
    }
    r = httpx.post(
        f"{base}/srv/api/search/records/_search",
        json=query,
        headers=_HEADERS,
        timeout=120,
    )
    r.raise_for_status()
    hits = r.json()["hits"]
    if hits["total"]["value"] > GN4_MAX_RESULTS:
        raise CrosswalkError(
            f"{base} has more than {GN4_MAX_RESULTS:,} dataset records; "
            f"listing them needs paging, which is not implemented."
        )
    return [h["_source"]["uuid"] for h in hits["hits"]]


def _list_gn3(base: str) -> list[str]:
    uuids: list[str] = []
    start = 1
    while True:
        r = httpx.get(
            f"{base}/srv/eng/q",
            params={
                "_content_type": "json",
                # 'true' returns only core fields: ~40 KB and ~5 s per page
                # on AODN, against ~1.5 MB and ~30 s for 'index'.
                "fast": "true",
                "type": "dataset",
                "buildSummary": "false",
                # Without an explicit sort, page order is not stable from
                # run to run, so paging repeats some records and skips
                # others. ('reverse' gives ascending uuids here.)
                "sortBy": "_uuid",
                "sortOrder": "reverse",
                "from": start,
                "to": start + GN3_PAGE - 1,
            },
            headers=_HEADERS,
            timeout=120,
        )
        r.raise_for_status()
        page = r.json().get("metadata") or []
        page = page if isinstance(page, list) else [page]  # one result
        # GeoNetwork 3's XML-to-JSON conversion sometimes wraps a record in
        # a one-item list.
        records = [
            r for m in page for r in (m if isinstance(m, list) else [m])
        ]
        uuids += [r.get("uuid") or r["geonet:info"]["uuid"] for r in records]
        if len(page) < GN3_PAGE:
            return uuids
        start += GN3_PAGE


def fetch_xml(url: str) -> bytes:
    """GET a record's XML, refusing HTML pages and oversized responses."""
    headers = {"Accept": "application/xml", "User-Agent": "cdif-mcp/0.1"}
    with httpx.stream(
        "GET", url, headers=headers, follow_redirects=True, timeout=60
    ) as r:
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        if "html" in ctype:
            hint = "pass its uuid and the catalogue's GeoNetwork base URL"
            if m := _UUID_IN_TEXT.search(url):
                hint = (
                    f"pass uuid={m.group(0)!r} with the catalogue's "
                    "GeoNetwork base URL"
                )
            raise CrosswalkError(
                f"{url} returned an HTML page, not ISO XML. If it is a "
                f"landing page for a GeoNetwork record, {hint} instead."
            )
        body = bytearray()
        for chunk in r.iter_bytes():
            body.extend(chunk)
            if len(body) > MAX_XML_BYTES:
                raise CrosswalkError(
                    f"{url} is larger than {MAX_XML_BYTES:,} bytes."
                )
    return bytes(body)
