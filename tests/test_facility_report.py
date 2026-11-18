"""Tests for catalogue listing and the facility report (no network)."""

import importlib.util
from pathlib import Path

import pytest

from crosswalk import CrosswalkError, geonetwork

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "iso"


class _Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def _load_report():
    spec = importlib.util.spec_from_file_location(
        "facility_report", ROOT / "scripts" / "facility_report.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# -- listing ----------------------------------------------------------------


def test_list_gn3_pages_and_unwraps(monkeypatch):
    """GeoNetwork 3: pages of 100, a single result as a bare object, and
    records wrapped in one-item lists."""
    pages = {
        1: {
            "metadata": [{"uuid": f"a{i}"} for i in range(99)]
            + [[{"uuid": "wrapped"}]]
        },
        101: {"metadata": {"geonet:info": {"uuid": "last"}}},
    }

    def fake_get(url, params=None, **kw):
        if url.endswith("/srv/api/site"):
            return _Resp({"system/platform/version": "3.12.7"})
        return _Resp(pages[params["from"]])

    monkeypatch.setattr(geonetwork.httpx, "get", fake_get)
    version, uuids = geonetwork.list_dataset_uuids("https://x.org/gn/")
    assert version == "3.12.7"
    assert len(uuids) == 101
    assert {"wrapped", "last"} <= set(uuids)


def test_list_gn4(monkeypatch):
    monkeypatch.setattr(
        geonetwork.httpx,
        "get",
        lambda *a, **k: _Resp({"system/platform/version": "4.4.12"}),
    )
    hits = {
        "hits": {
            "total": {"value": 2},
            "hits": [{"_source": {"uuid": "b"}}, {"_source": {"uuid": "a"}}],
        }
    }
    monkeypatch.setattr(geonetwork.httpx, "post", lambda *a, **k: _Resp(hits))
    assert geonetwork.list_dataset_uuids("https://x.org/gn") == (
        "4.4.12",
        ["a", "b"],
    )


def test_list_gn4_too_many(monkeypatch):
    monkeypatch.setattr(
        geonetwork.httpx,
        "get",
        lambda *a, **k: _Resp({"system/platform/version": "4.4.12"}),
    )
    hits = {"hits": {"total": {"value": 20_000}, "hits": []}}
    monkeypatch.setattr(geonetwork.httpx, "post", lambda *a, **k: _Resp(hits))
    with pytest.raises(CrosswalkError, match="paging"):
        geonetwork.list_dataset_uuids("https://x.org/gn")


# -- the report -------------------------------------------------------------


def test_parse_catalogues():
    rpt = _load_report()
    assert rpt.parse_catalogues(["T=https://x.org/gn/srv/eng/"]) == {
        "T": "https://x.org/gn"
    }
    with pytest.raises(SystemExit):
        rpt.parse_catalogues(["https://x.org/gn"])


def test_assess_and_summarise(monkeypatch):
    """assess() on bundled records (fetch replaced), then summarise()."""
    import DemoMcpServer as srv

    rpt = _load_report()
    files = {
        "u1": "tern-cmrset-evapotranspiration.xml",
        "u2": "auscope-gsv-mineral-occurrences.xml",
        "u3": "auscope-gsnsw-wms-service.xml",
        "u4": "aodn-glider-yamba.xml",
    }
    monkeypatch.setattr(
        rpt,
        "fetch_xml",
        lambda url: (FIXTURES / files[url.split("/")[-3]]).read_bytes(),
    )
    rows = [
        rpt.assess("F", "https://x.org/gn", u, srv._validate_one)
        for u in files
    ]
    by = {r["uuid"]: r for r in rows}
    assert by["u1"]["cdif_valid"] is True
    assert by["u1"]["identifier"] == "DOI"
    assert by["u1"]["licence"] == "URI"
    assert by["u2"]["cdif_valid"] is False
    assert by["u2"]["licence"] == "none"
    assert "schema:license" in by["u2"]["missing_required"]
    assert by["u3"]["status"] == "out_of_scope"
    # Valid, but only because required values came from fallbacks.
    assert by["u4"]["cdif_valid"] is True
    assert by["u4"]["required_from_fallback"] == (
        "schema:identifier | schema:dateModified"
    )
    assert by["u1"]["required_from_fallback"] == ""
    assert set(rows[0]) == set(rpt.FIELDS)

    md = rpt.summarise(
        rows,
        {
            "date": "2026-01-01",
            "sample": 3,
            "seed": 7,
            "csv": "r.csv",
            "hashes": {"Crosswalk": "abc"},
            "versions": {"F": "4.4"},
            "populations": {"F": 10},
        },
    )
    assert "| Converted (of sample) | 3 (75%) |" in md
    assert "| **CDIF valid** | 2 (67%) |" in md
    assert "| **Valid from source values alone** | 1 (33%) |" in md
    assert "| Identifier from fallback | 2 (67%) |" in md
    assert "| No licence | 1 (33%) |" in md
    assert "`u3` | out_of_scope" in md
    # Messages containing "; " are not split apart.
    assert (
        "The resource has no identifier; schema:identifier is the "
        "metadata record identifier." in md
    )


def test_fetch_failure_is_recorded(monkeypatch):
    rpt = _load_report()

    def boom(url):
        raise geonetwork.httpx.ConnectError("refused")

    monkeypatch.setattr(rpt, "fetch_xml", boom)
    row = rpt.assess("F", "https://x.org/gn", "u", lambda r: {})
    assert row["status"] == "fetch_error"
    assert "refused" in row["error"]
