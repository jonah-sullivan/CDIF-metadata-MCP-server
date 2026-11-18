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
    if not re.match(r"https?://", catalogue):
        raise CrosswalkError(f"catalogue is not a web URL: {catalogue!r}.")
    if not _UUID.fullmatch(uuid):
        raise CrosswalkError(f"Not a record identifier: {uuid!r}.")
    base = re.sub(r"/srv(/.*)?$", "", catalogue.rstrip("/"))
    return _formatter(base, uuid)


def _formatter(base: str, uuid: str) -> str:
    return f"{base}/srv/api/records/{uuid}/formatters/xml"


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
                hint = f"pass uuid={m.group(0)!r} with the catalogue's " + (
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
