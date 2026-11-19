"""Tests for catalogue listing and the facility report (no network)."""

import importlib.util
from pathlib import Path

import pytest
from pid_replay import isolated_settings  # noqa: F401

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


def test_parse_arcgis_groups():
    rpt = _load_report()
    gid = "40a78e66db6944c0aca00fad63a93056"
    assert rpt.parse_arcgis_groups(
        [f"ACS=https://www.acs.gov.au/search?groupIds={gid}", f"B={gid}"]
    ) == {"ACS": gid, "B": gid}
    for bad in [gid, "ACS=", "ACS=https://www.acs.gov.au/search"]:
        with pytest.raises(SystemExit):
            rpt.parse_arcgis_groups([bad])


def test_list_records(monkeypatch):
    """ArcGIS items come with their ISO 19139 URL; GeoNetwork records with
    none (assess() builds it); meta gets versions, populations, types."""
    rpt = _load_report()
    item = "52cb2b4d300d4bc59d31555ad9e5dc8c"
    monkeypatch.setattr(
        rpt, "list_group_items", lambda g: ([item], {"PDF": 2})
    )
    monkeypatch.setattr(
        rpt, "list_dataset_uuids", lambda base: ("4.4.12", ["u1", "u2"])
    )
    meta = {"versions": {}, "populations": {}}
    assert rpt.list_records("arcgis", "g", meta, "ACS") == [
        (item, rpt.item_xml_url(item))
    ]
    assert rpt.list_records("geonetwork", "https://x", meta, "T") == [
        ("u1", None),
        ("u2", None),
    ]
    assert meta == {
        "versions": {"ACS": "ArcGIS Online group", "T": "GeoNetwork 4.4.12"},
        "populations": {"ACS": 1, "T": 2},
        "item_types": {"ACS": {"PDF": 2}},
    }


def test_assess_arcgis_record(monkeypatch):
    """An ArcGIS ISO 19139 export, fetched from the URL given to assess()."""
    import DemoMcpServer as srv

    rpt = _load_report()
    item = "52cb2b4d300d4bc59d31555ad9e5dc8c"
    url = rpt.item_xml_url(item)
    xml = (
        ROOT / "tests/fixtures/arcgis/acs-ocean-chemistry-variables.xml"
    ).read_bytes()
    fetched = []
    monkeypatch.setattr(
        rpt, "fetch_xml", lambda u: fetched.append(u) or xml
    )
    group = rpt.arcgis_group_url("40a78e66db6944c0aca00fad63a93056")
    row = rpt.assess("ACS", group, item, srv._validate_one, xml_url=url)
    assert fetched == [url]
    assert (row["catalogue"], row["xml_url"]) == (group, url)
    assert row["status"] == "converted"
    assert row["dialect"] == "iso19139"
    assert row["title"] == "Ocean chemistry variables"
    assert row["licence"] == "inferred"
    assert "schema:dateModified" in row["missing_required"]

    md = rpt.summarise(
        [row],
        {
            **META,
            "versions": {"ACS": "ArcGIS Online group"},
            "populations": {"ACS": 41},
            "item_types": {"ACS": {"File Geodatabase": 15, "PDF": 7}},
        },
    )
    assert "| Catalogue | ArcGIS Online group |" in md
    assert "| Dataset records in catalogue | 41 |" in md
    assert (
        "- **ACS** (ArcGIS group): items by type: File Geodatabase 15, "
        "PDF 7." in md
    )


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


# -- identifier columns ------------------------------------------------------

META = {
    "date": "2026-01-01",
    "sample": 2,
    "seed": 7,
    "csv": "r.csv",
    "hashes": {"Crosswalk": "abc"},
    "versions": {"F": "4.4"},
    "populations": {"F": 10},
}


def _assess(rpt, monkeypatch, files, check_ids=None):
    import DemoMcpServer as srv

    monkeypatch.setattr(
        rpt,
        "fetch_xml",
        lambda url: (FIXTURES / files[url.split("/")[-3]]).read_bytes(),
    )
    kw = {"check_ids": check_ids} if check_ids else {}
    return [
        rpt.assess("F", "https://x.org/gn", u, srv._validate_one, **kw)
        for u in files
    ]


def test_offline_identifier_columns(monkeypatch):
    rpt = _load_report()
    rows = _assess(
        rpt,
        monkeypatch,
        {
            "u1": "auscope-calperum-czo.xml",
            "u2": "auscope-gsv-mineral-occurrences.xml",
        },
    )
    calperum, gsv = rows
    assert calperum["dataset_has_pid"] is True
    assert (calperum["people"], calperum["people_with_orcid"]) == (3, 3)
    assert calperum["identifiers_checked"] == ""  # not resolved
    assert gsv["dataset_has_pid"] is False
    assert "dataset-no-pid" in gsv["pid_warnings"]

    md = rpt.summarise(rows, META)
    assert "| Dataset has a persistent identifier | 1 (50%) |" in md
    assert "| People with an ORCID | 3 of 4 (75%) |" in md
    assert "Dataset has no persistent identifier |" in md
    assert "They were not resolved" in md
    assert "All checked identifiers resolve" not in md


@pytest.mark.usefixtures("isolated_settings")
def test_resolved_identifier_columns(monkeypatch):
    from pid_replay import resolver

    from pidcheck import check_record

    rpt = _load_report()
    r, _ = resolver()
    rows = _assess(
        rpt,
        monkeypatch,
        {"u1": "auscope-rosebery-3d-model.xml"},
        check_ids=lambda rec: check_record(rec, r, max_findings=1000),
    )
    row = rows[0]
    assert row["dataset_doi_type"] == "journal-article"
    assert "dataset-doi-is-publication" in row["pid_warnings"]
    assert row["identifiers_checked"] >= 1

    md = rpt.summarise(rows, {**META, "identifiers_resolved": True})
    assert "| Dataset DOI identifies a publication | 1 (100%) |" in md
    assert "Dataset DOI that identifies a publication |" in md
    assert "## Vocabularies hosted in RVA" in md
    assert "They were also resolved" in md


def test_rva_list_drops_unversioned_duplicates():
    rpt = _load_report()
    row = {k: "" for k in rpt.FIELDS}
    rows = [
        {
            **row,
            "facility": "F",
            "status": "converted",
            "rva_vocabularies": "AODN Extents",
        },
        {
            **row,
            "facility": "F",
            "status": "converted",
            "rva_vocabularies": "AODN Extents (version-4-0, current) | Other",
        },
    ]
    md = rpt.summarise(rows, {**META, "identifiers_resolved": True})
    assert "- **F:** AODN Extents (version-4-0, current); Other" in md
