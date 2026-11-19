"""Tests for listing ArcGIS Online groups (no network)."""

import pytest

from crosswalk import CrosswalkError, arcgis

GID = "40a78e66db6944c0aca00fad63a93056"
ITEM = "52cb2b4d300d4bc59d31555ad9e5dc8c"


class _Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


@pytest.mark.parametrize(
    "value",
    [
        GID,
        f" {GID} ",
        f"https://www.acs.gov.au/search?groupIds={GID}",
        f"https://hub.example.org/search?q=x&groupIds={GID}&sort=name",
        f"https://www.acs.gov.au/search?groupIds=notanid,{GID}",
        f"https://www.arcgis.com/home/group.html?id={GID}",
        f"https://www.arcgis.com/sharing/rest/community/groups/{GID}",
        f"https://www.arcgis.com/sharing/rest/content/groups/{GID}/search",
    ],
)
def test_group_id(value):
    assert arcgis.group_id(value) == GID


@pytest.mark.parametrize(
    "value",
    [
        "",
        "ACS",
        GID.upper(),
        GID + "0",
        "https://www.acs.gov.au/search",
        f"ftp://x.org/groups/{GID}",
        f"https://x.org/items/{GID}x",
    ],
)
def test_group_id_rejects(value):
    with pytest.raises(CrosswalkError):
        arcgis.group_id(value)


def test_list_group_items_pages_and_filters(monkeypatch):
    """Two pages; only data item types kept; types counted across all."""
    pages = {
        1: {
            "nextStart": 101,
            "results": [
                {"id": "b" * 32, "type": "Feature Service"},
                {"id": "c" * 32, "type": "PDF"},
            ],
        },
        101: {
            "nextStart": -1,
            "results": [
                {"id": "a" * 32, "type": "File Geodatabase"},
                {"id": "d" * 32, "type": "Web Map"},
            ],
        },
    }
    calls = []

    def fake_get(url, params=None, **kw):
        calls.append((url, params))
        return _Resp(pages[params["start"]])

    monkeypatch.setattr(arcgis.httpx, "get", fake_get)
    ids, types = arcgis.list_group_items(
        f"https://www.acs.gov.au/search?groupIds={GID}"
    )
    assert ids == ["a" * 32, "b" * 32]
    assert types == {
        "Feature Service": 1,
        "File Geodatabase": 1,
        "PDF": 1,
        "Web Map": 1,
    }
    assert [p["start"] for _, p in calls] == [1, 101]
    assert calls[0][0] == (
        f"https://www.arcgis.com/sharing/rest/content/groups/{GID}/search"
    )
    assert calls[0][1]["sortField"] == "id"


def test_list_group_items_error_in_200(monkeypatch):
    monkeypatch.setattr(
        arcgis.httpx,
        "get",
        lambda *a, **k: _Resp(
            {"error": {"code": 400, "message": "Invalid group id"}}
        ),
    )
    with pytest.raises(CrosswalkError, match="Invalid group id"):
        arcgis.list_group_items(GID)


def test_item_xml_url():
    assert arcgis.item_xml_url(ITEM) == (
        f"https://www.arcgis.com/sharing/rest/content/items/{ITEM}"
        "/info/metadata/metadata.xml?format=iso19139"
    )
    with pytest.raises(CrosswalkError):
        arcgis.item_xml_url("../../x")
