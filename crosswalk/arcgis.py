"""Listing an ArcGIS Online group's items and their ISO 19139 metadata.

ArcGIS Hub sites (such as the Australian Climate Service's acs.gov.au) are
front ends to ArcGIS Online groups: a Hub search URL like
https://www.acs.gov.au/search?groupIds=40a78e66db6944c0aca00fad63a93056
shows the public items shared to that group. ArcGIS Online exports an
item's metadata as ISO 19139 at
{portal}/sharing/rest/content/items/{id}/info/metadata/metadata.xml
?format=iso19139.
"""

import re
from urllib.parse import parse_qs, urlparse

import httpx

from .iso19115 import CrosswalkError

ARCGIS_ONLINE = "https://www.arcgis.com"
PAGE = 100  # the most the sharing API returns per page
_HEADERS = {"Accept": "application/json", "User-Agent": "cdif-mcp/0.1"}
_ID = re.compile(r"[0-9a-f]{32}")

# Item types that hold data, as opposed to documents (PDF), maps and apps.
# https://developers.arcgis.com/rest/users-groups-and-items/items-and-item-types/
DATA_TYPES = frozenset(
    {
        "CSV",
        "Feature Collection",
        "Feature Service",
        "File Geodatabase",
        "GeoJson",
        "GeoPackage",
        "Image Collection",
        "Image Service",
        "KML",
        "KML Collection",
        "Map Service",
        "Microsoft Excel",
        "OGCFeatureServer",
        "Scene Service",
        "Shapefile",
        "Vector Tile Service",
        "WCS",
        "WFS",
        "WMS",
        "WMTS",
    }
)


def group_id(value: str) -> str:
    """A group id, given as the id itself or as a Hub or ArcGIS Online URL
    that names it (?groupIds=..., ?id=..., or /groups/{id})."""
    value = value.strip()
    if _ID.fullmatch(value):
        return value
    if re.match(r"https?://", value):
        u = urlparse(value)
        q = parse_qs(u.query)
        for v in q.get("groupIds", []) + q.get("id", []):
            for part in v.split(","):
                if _ID.fullmatch(part.strip()):
                    return part.strip()
        if m := re.search(r"/groups?/([0-9a-f]{32})(?:/|$)", u.path):
            return m.group(1)
    raise CrosswalkError(
        f"Not an ArcGIS group id or a URL that names one: {value!r}."
    )


def list_group_items(
    group: str, portal: str = ARCGIS_ONLINE
) -> tuple[list[str], dict[str, int]]:
    """List a group's public data items. Returns (sorted item ids, counts
    of every item type in the group, data or not)."""
    gid = group_id(group)
    ids: list[str] = []
    types: dict[str, int] = {}
    start = 1
    while start > 0:
        r = httpx.get(
            f"{portal}/sharing/rest/content/groups/{gid}/search",
            params={
                "q": "*",
                "num": PAGE,
                "start": start,
                "sortField": "id",  # a stable order for paging
                "f": "json",
            },
            headers=_HEADERS,
            timeout=60,
        )
        r.raise_for_status()
        page = r.json()
        # The sharing API reports errors in a 200 response.
        if "error" in page:
            msg = page["error"].get("message", page["error"])
            raise CrosswalkError(f"ArcGIS group {gid}: {msg}")
        for item in page.get("results", []):
            types[item["type"]] = types.get(item["type"], 0) + 1
            if item["type"] in DATA_TYPES:
                ids.append(item["id"])
        start = page.get("nextStart", -1)
    return sorted(set(ids)), dict(sorted(types.items()))


def item_xml_url(item_id: str, portal: str = ARCGIS_ONLINE) -> str:
    """URL of an item's metadata exported as ISO 19139."""
    if not _ID.fullmatch(item_id):
        raise CrosswalkError(f"Not an ArcGIS item id: {item_id!r}.")
    return (
        f"{portal}/sharing/rest/content/items/{item_id}"
        "/info/metadata/metadata.xml?format=iso19139"
    )
