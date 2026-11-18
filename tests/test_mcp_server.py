"""The MCP server, exercised through a real MCP client in memory."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastmcp import Client
from pid_replay import isolated_settings  # noqa: F401
from pid_replay import resolver as replay_resolver

import DemoMcpServer
from DemoMcpServer import mcp

# What a typical catalogue landing page publishes: plain schema.org, no CDIF.
TYPICAL = {
    "@context": "https://schema.org/",
    "@type": "Dataset",
    "name": "Sea surface temperature, monthly",
    "description": "Gridded SST.",
    "url": "https://example.org/sst",
}


def call(tool: str, args: dict | None = None, raise_on_error: bool = True):
    """Call one tool through an in-memory MCP client."""

    async def run():
        async with Client(mcp) as c:
            return await c.call_tool(
                tool, args or {}, raise_on_error=raise_on_error
            )

    return asyncio.run(run())


def data(tool: str, args: dict | None = None):
    return call(tool, args).data


def test_tools_listed():
    async def names():
        async with Client(mcp) as c:
            return {t.name for t in await c.list_tools()}

    assert asyncio.run(names()) == {
        "fetch_metadata",
        "validate_cdif",
        "search_cdif_guide",
        "list_cdif_examples",
        "get_cdif_example",
        "crosswalk_iso19115",
        "crosswalk_geonetwork_record",
        "list_iso_examples",
        "check_identifiers",
        "check_geonetwork_identifiers",
    }


# -- validate_cdif -----------------------------------------------------------


def test_validate_record():
    good = data("get_cdif_example", {"name": "copernicus-sea-ice.jsonld"})
    r = data("validate_cdif", {"record": good})
    assert r["valid"]
    assert "https://w3id.org/cdif/discovery/1.1" in r["declared_conformance"]


def test_validate_by_example_name():
    r = data("validate_cdif", {"example": "copernicus-sea-ice.jsonld"})
    assert r["valid"]


@pytest.mark.parametrize(
    "args",
    [
        {},
        {"example": "x.json", "url": "https://x"},
        {"example": "../../../DemoMcpServer.py"},
        {"url": "/etc/passwd"},
    ],
)
def test_validate_rejects_bad_arguments(args):
    assert call("validate_cdif", args, raise_on_error=False).is_error


def test_plain_schema_org_fails():
    r = data("validate_cdif", {"record": TYPICAL})
    assert not r["valid"]
    assert r["problem_count"] > 0
    assert not r["shacl"]["conforms"]
    assert r["shacl"]["counts"]["Violation"] > 0


def test_shacl_can_be_skipped():
    r = data("validate_cdif", {"record": TYPICAL, "shacl": False})
    assert "shacl" not in r


def test_guide_search():
    hits = data("search_cdif_guide", {"query": "subjectOf CatalogRecord"})
    assert hits
    assert all({"section", "text"} <= set(h) for h in hits)


# -- ISO 19115 crosswalk (bundled NCRIS records, no network) ---------------


def test_list_iso_examples():
    assert len(data("list_iso_examples")) == 10


def test_crosswalk_valid_record():
    r = data(
        "crosswalk_iso19115",
        {"example": "tern-cmrset-evapotranspiration.xml"},
    )
    assert r["validation"]["valid"]
    assert r["record"]["@id"] == "https://doi.org/10.25901/gg27-ck96"
    assert r["crosswalk"]["missing_required"] == []


def test_crosswalk_without_record():
    r = data(
        "crosswalk_iso19115",
        {
            "example": "auscope-gsv-mineral-occurrences.xml",
            "include_record": False,
        },
    )
    assert "record" not in r
    assert not r["validation"]["valid"]
    assert r["crosswalk"]["missing_required"] == [
        "schema:license or schema:conditionsOfAccess"
    ]


def test_crosswalk_service_record_out_of_scope():
    r = data(
        "crosswalk_iso19115", {"example": "auscope-gsnsw-wms-service.xml"}
    )
    assert "service" in r["crosswalk"]["out_of_scope"]
    assert "record" not in r
    assert "validation" not in r


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("crosswalk_iso19115", {}),
        ("crosswalk_iso19115", {"example": "../../DemoMcpServer.py"}),
        ("crosswalk_iso19115", {"example": "manifest.json"}),
        ("crosswalk_iso19115", {"url": "file:///etc/passwd"}),
        (
            "crosswalk_iso19115",
            {"example": "aodn-sst-l3s.xml", "url": "https://x.org"},
        ),
        ("crosswalk_geonetwork_record", {"uuid": "abc"}),
        (
            "crosswalk_geonetwork_record",
            {"uuid": "../x", "catalogue": "https://x.org"},
        ),
        (
            "crosswalk_geonetwork_record",
            {"uuid": "abc", "catalogue": "file:///etc"},
        ),
    ],
)
def test_crosswalk_rejects_bad_arguments(tool, args):
    assert call(tool, args, raise_on_error=False).is_error


# -- CODATA's bundled CDIF examples ----------------------------------------

# Examples that fail in CODATA's own repository. Marked as expected
# failures (not strict), so an upstream fix shows as XPASS rather than an
# error, while a new failure of any other example fails the run.
SCHEMA_FAILURES = {
    "exampleCDIFDiscoveryComplete.json": (
        "array @type on the geosparql:wktLiteral value object is invalid "
        "JSON-LD"
    ),
}
SHACL_FAILURES = {
    name: "fails CDIF's SHACL rules upstream"
    for name in (
        "GeoCodes-usap-dataset.jsonld",
        "ODIS-protectedAreaData.json",
        "dataverse-borealis-tern-lake-ndvi.jsonld",
        "exampleCDIFDiscoveryComplete.json",
        "ncei-ghrsst-mur-sst.jsonld",
        "ncei-world-ocean-atlas.jsonld",
        "pangaea-chlorophyll-fluorescence.jsonld",
        "pangaea-ctd-salinity.jsonld",
        "pangaea-epimeria-species.jsonld",
        "pangaea-nutrients.jsonld",
        "pangaea-seawater-isotope.jsonld",
    )
}
EXAMPLES = data("list_cdif_examples")


def _cases(known: dict[str, str]):
    return [
        pytest.param(
            n,
            marks=pytest.mark.xfail(reason=known[n], strict=False)
            if n in known
            else (),
        )
        for n in EXAMPLES
    ]


@pytest.fixture(scope="module")
def example_results():
    """Validate each bundled example once (SHACL on the largest takes
    ~15 s), shared by the schema and SHACL tests."""
    cache: dict[str, dict] = {}

    def get(name: str) -> dict:
        if name not in cache:
            cache[name] = data("validate_cdif", {"example": name})
        return cache[name]

    return get


@pytest.mark.parametrize("name", _cases(SCHEMA_FAILURES))
def test_bundled_example_schema_valid(name, example_results):
    assert example_results(name)["schema_valid"]


@pytest.mark.parametrize("name", _cases(SHACL_FAILURES))
def test_bundled_example_shacl_conforms(name, example_results):
    assert example_results(name)["shacl"]["conforms"]


# -- identifier checks (recorded responses, no network) ---------------------


@pytest.fixture
def replayed_identifiers(monkeypatch, request):
    """Identifier checks use recorded responses instead of the network."""
    request.getfixturevalue("isolated_settings")
    monkeypatch.setattr(
        DemoMcpServer, "_pid_resolver", lambda: replay_resolver()[0]
    )


@pytest.mark.usefixtures("replayed_identifiers")
def test_crosswalk_with_identifier_check():
    r = data(
        "crosswalk_iso19115",
        {
            "example": "tern-cmrset-evapotranspiration.xml",
            "include_record": False,
            "check_identifiers": True,
        },
    )
    ids = r["identifiers"]
    assert ids["summary"]["dataset_doi_type"] == "Dataset"
    assert ids["summary"]["dataset_doi_agency"] == "DataCite"
    assert {"problems", "warnings", "notes"} <= set(ids)
    assert r["validation"]["valid"]  # identifier checks do not change it


@pytest.mark.usefixtures("replayed_identifiers")
def test_validate_with_identifier_check():
    r = data(
        "validate_cdif",
        {"example": "copernicus-sea-ice.jsonld", "check_identifiers": True},
    )
    assert "summary" in r["identifiers"]


def test_identifier_check_off_by_default():
    r = data(
        "crosswalk_iso19115",
        {"example": "aodn-sst-l3s.xml", "include_record": False},
    )
    assert "identifiers" not in r
    assert "identifiers" not in data(
        "validate_cdif", {"example": "copernicus-sea-ice.jsonld"}
    )


ISO = Path(__file__).parent / "fixtures" / "iso"


@pytest.mark.usefixtures("replayed_identifiers")
def test_check_identifiers_iso_example():
    r = data("check_identifiers", {"source": "auscope-rosebery-3d-model.xml"})
    assert r["source"] == "auscope-rosebery-3d-model.xml"
    assert "dataset-doi-is-publication" in [f["code"] for f in r["warnings"]]


@pytest.mark.usefixtures("replayed_identifiers")
def test_check_identifiers_cdif_example():
    r = data("check_identifiers", {"source": "copernicus-sea-ice.jsonld"})
    assert {"summary", "problems", "warnings", "notes"} <= set(r)


@pytest.mark.parametrize(
    "source",
    [
        "no-such-example.xml",
        "../../DemoMcpServer.py",
        "auscope-gsnsw-wms-service.xml",
    ],  # a service: out of scope
)
def test_check_identifiers_rejects(source):
    assert call(
        "check_identifiers", {"source": source}, raise_on_error=False
    ).is_error


def _fake_get(monkeypatch, content, ctype):
    def get(url, **kw):
        return httpx.Response(
            200,
            content=content,
            headers={"content-type": ctype},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(DemoMcpServer.httpx, "get", get)


@pytest.mark.usefixtures("replayed_identifiers")
def test_check_identifiers_iso_url(monkeypatch):
    xml = (ISO / "tern-cmrset-evapotranspiration.xml").read_bytes()
    _fake_get(monkeypatch, xml, "application/xml")
    r = data("check_identifiers", {"source": "https://example.org/record.xml"})
    assert r["summary"]["dataset_doi_type"] == "Dataset"


@pytest.mark.usefixtures("replayed_identifiers")
def test_check_identifiers_landing_page(monkeypatch):
    record = {
        "@context": "https://schema.org/",
        "@type": "Dataset",
        "@id": "https://doi.org/10.25901/gg27-ck96",
        "name": "x",
    }
    page = (
        '<html><script type="application/ld+json">'
        + json.dumps(record)
        + "</script></html>"
    )
    _fake_get(monkeypatch, page.encode(), "text/html")
    r = data("check_identifiers", {"source": "https://example.org/page"})
    assert r["summary"]["dataset_doi_agency"] == "DataCite"


@pytest.mark.usefixtures("replayed_identifiers")
def test_check_geonetwork_identifiers(monkeypatch):
    xml = (ISO / "auscope-rosebery-3d-model.xml").read_bytes()
    monkeypatch.setattr(DemoMcpServer.xwalk, "fetch_xml", lambda url: xml)
    r = data(
        "check_geonetwork_identifiers",
        {
            "catalogue": "https://portal.auscope.org.au/geonetwork",
            "uuid": "c20396fc-6cee-437d-bec3-1b3608641574",
        },
    )
    assert r["source"].endswith("/formatters/xml")
    assert "dataset-doi-is-publication" in [f["code"] for f in r["warnings"]]
