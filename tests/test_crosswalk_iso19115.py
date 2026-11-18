"""Tests for the ISO 19115 -> CDIF Discovery crosswalk.

Fixture assertions check values that were verified by hand against the
source XML in tests/fixtures/iso/. If a fixture is refetched and a test
fails, check the new XML before changing the expected value.
"""

import json
from pathlib import Path

import pytest

from crosswalk import CrosswalkError, crosswalk
from crosswalk.iso19115 import (
    _Builder,
    _doi_url,
    _iso_date,
    _plain,
    _scheme,
    parse,
)

FIXTURES = Path(__file__).parent / "fixtures" / "iso"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text("utf-8"))
DATASETS = [
    r["file"] for r in MANIFEST["records"] if "service" not in r["file"]
]


def split(xml: bytes | str) -> tuple[dict, dict]:
    out = crosswalk(xml)
    return out["record"], out["report"]


def run(name: str) -> tuple[dict, dict]:
    return split((FIXTURES / name).read_bytes())


# -- every fixture ---------------------------------------------------------


@pytest.mark.parametrize(
    "rec", MANIFEST["records"], ids=[r["file"] for r in MANIFEST["records"]]
)
def test_dialect_detected(rec):
    _, report = run(rec["file"])
    assert report["dialect"] == rec["standard"]


@pytest.mark.parametrize("name", DATASETS)
def test_dataset_record_shape(name):
    record, report = run(name)
    assert record["@type"] == ["schema:Dataset"]
    sub = record["schema:subjectOf"]
    assert sub["schema:about"] == {"@id": record["@id"]}
    assert sub["@id"] != record["@id"]
    assert {"@id": "https://w3id.org/cdif/discovery/1.1"} in sub[
        "dcterms:conformsTo"
    ]
    for prop in ("schema:name", "schema:identifier", "schema:dateModified"):
        assert prop in record or prop in report["missing_required"]


@pytest.mark.parametrize("name", DATASETS)
def test_output_is_valid_jsonld(name):
    from pyld import jsonld

    record, _ = run(name)
    assert jsonld.expand(record)  # raises on invalid JSON-LD


@pytest.mark.parametrize("name", DATASETS)
def test_dates_match_cdif_pattern(name):
    import re

    pattern = re.compile(
        r"^[1-2][0-9]{3}-(0[1-9]|1[0-2])(-([0-2][0-9]|3[0-1])"
        r"(T([0-1][0-9]|2[0-3])(:[0-5][0-9])?(:[0-5][0-9]"
        r"(Z|[+-][0-2][0-9]:[0-5][0-9])?)?)?)?$"
    )
    record, _ = run(name)
    assert pattern.match(record["schema:dateModified"])


# Expected end-to-end validity (JSON Schema + SHACL via the MCP server's
# validator). The two failures are real findings, not crosswalk bugs.
EXPECTED_VALID = {
    "tern-awra-canopy-conductance.xml": True,
    "tern-cmrset-evapotranspiration.xml": True,
    "tern-bat-acoustic-usa.xml": True,
    "aodn-glider-yamba.xml": True,
    "aodn-ran-ctd-profiles.xml": True,
    "aodn-sst-l3s.xml": True,
    "auscope-rosebery-3d-model.xml": True,
    # Funder has no identifier, and CDIF's frame drops such funders.
    "auscope-calperum-czo.xml": False,
    # The source record states no licence or access conditions.
    "auscope-gsv-mineral-occurrences.xml": False,
}


@pytest.mark.parametrize("name", sorted(EXPECTED_VALID))
def test_end_to_end_validation(name):
    import DemoMcpServer as srv

    record, _ = run(name)
    assert srv._validate_one(record)["valid"] is EXPECTED_VALID[name]


# -- specific fixtures -----------------------------------------------------


def test_service_record_out_of_scope():
    record, report = run("auscope-gsnsw-wms-service.xml")
    assert record is None
    assert "service" in report["out_of_scope"]


def test_tern_cmrset():
    record, report = run("tern-cmrset-evapotranspiration.xml")
    assert record["@id"] == "https://doi.org/10.25901/gg27-ck96"
    assert record["schema:identifier"]["schema:propertyID"] == (
        "https://doi.org"
    )
    assert record["schema:url"].startswith("https://portal.tern.org.au/")
    assert {"@id": "https://creativecommons.org/licenses/by/4.0/"} in record[
        "schema:license"
    ]
    assert not record["schema:description"].startswith("<p>")
    apis = [
        d
        for d in record["schema:distribution"]
        if d["@type"] == ["schema:WebAPI"]
    ]
    assert any(
        a["schema:serviceType"]["schema:termCode"].startswith("OGC:WMS")
        for a in apis
    )
    assert all("schema:termsOfService" in a for a in apis)
    gcmd = [
        k
        for k in record["schema:keywords"]
        if isinstance(k, dict)
        and str(k.get("schema:identifier", "")).startswith(
            "https://gcmd.earthdata.nasa.gov/"
        )
    ]
    assert gcmd
    assert {"element": "mri:purpose", "count": 1} in report["unmapped"]


def test_tern_landing_page_not_home_page():
    record, _ = run("tern-awra-canopy-conductance.xml")
    assert record["schema:url"] == (
        "https://portal.tern.org.au/metadata/"
        "089c56eb-af02-42eb-9bce-c3bdc17f01c1"
    )
    links = [
        link["schema:target"]["schema:url"]
        for link in record["schema:relatedLink"]
    ]
    assert "https://www.tern.org.au/" in links


def test_tern_creators_are_people_affiliated_with_org():
    record, _ = run("tern-cmrset-evapotranspiration.xml")
    creators = record["schema:creator"]["@list"]
    names = [c["schema:name"] for c in creators]
    assert "McVicar, Tim R" in names
    tim = creators[names.index("McVicar, Tim R")]
    assert tim["@type"] == ["schema:Person"]
    assert tim["schema:affiliation"]["schema:name"].startswith("CSIRO")


def test_aodn_polygons_and_variables():
    record, _ = run("aodn-ran-ctd-profiles.xml")
    places = record["schema:spatialCoverage"]
    geom = [p for p in places if "geosparql:hasGeometry" in p]
    assert len(geom) == 1
    g = geom[0]["geosparql:hasGeometry"]
    assert g["@type"] == ["sf:MultiPolygon"]
    assert g["geosparql:asWKT"]["@value"].startswith("MULTIPOLYGON(((150 -24")
    s, w, n, e = map(float, geom[0]["schema:geo"]["schema:box"].split())
    assert s < n and w < e and 100 < w < 160
    assert record["schema:temporalCoverage"] == [
        "2004-01-25T00:38:00/2016-03-05T07:08:00"
    ]
    temp = next(
        v
        for v in record["schema:variableMeasured"]
        if v["schema:name"] == "Temperature of the water body"
    )
    assert temp["schema:propertyID"].endswith("/P01/current/TEMPPR01")
    assert temp["schema:unitCode"].endswith("/P06/current/UPAA")


def test_aodn_no_identifier_is_reported():
    record, report = run("aodn-glider-yamba.xml")
    assert record["schema:identifier"]["schema:value"] == (
        "334592c9-991a-4e55-aba1-0b3247387a03"
    )
    assert any("no identifier" in n for n in report["notes"])


def test_aodn_principal_investigator_as_creator():
    _, report = run("aodn-sst-l3s.xml")
    assert any("principal investigators" in n for n in report["notes"])


def test_auscope_doi_from_online_resource():
    record, report = run("auscope-rosebery-3d-model.xml")
    assert record["@id"].startswith("https://doi.org/10.")
    assert any(
        "DOI taken from an online resource" in n for n in report["notes"]
    )
    assert any("No resource scope" in n for n in report["notes"])


def test_auscope_licence_in_generic_constraints():
    record, _ = run("auscope-calperum-czo.xml")
    assert record["schema:license"] == [
        {"@id": "https://creativecommons.org/licenses/by/4.0/"}
    ]
    boxes = [p for p in record["schema:spatialCoverage"] if "schema:geo" in p]
    assert len(boxes) == 3


def test_iso19139_missing_licence():
    record, report = run("auscope-gsv-mineral-occurrences.xml")
    assert (
        "schema:license or schema:conditionsOfAccess"
        in (report["missing_required"])
    )
    assert record["schema:distribution"][0]["@type"] == ["schema:WebAPI"]
    assert record["schema:spatialCoverage"][0]["schema:geo"]["schema:box"] == (
        "-39 140 -35 150"
    )


# -- helpers ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("2021-02-28", "2021-02-28"),
        ("2024-06-20 12:12:17", "2024-06-20T12:12:17"),
        ("2025-06-19T02:58:43.152852Z", "2025-06-19T02:58:43Z"),
        (
            "2021-02-28T00:00:00.000000+00:00",
            "2021-02-28T00:00:00+00:00",
        ),
        ("2021-02-28T10:00:00+1000", "2021-02-28T10:00:00+10:00"),
    ],
)
def test_iso_date(given, expected):
    assert _iso_date(given) == expected


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("doi:10.25901/gg27-ck96", "https://doi.org/10.25901/gg27-ck96"),
        (
            "https://dx.doi.org/10.25901/gg27-ck96",
            "https://doi.org/10.25901/gg27-ck96",
        ),
        ("10.1234/abc", "https://doi.org/10.1234/abc"),
        ("https://example.org/10.1234/abc", None),
        ("not a doi", None),
    ],
)
def test_doi_url(given, expected):
    assert _doi_url(given) == expected


def test_scheme():
    assert _scheme("ROR", None, None) == "https://ror.org"
    assert _scheme(None, None, "https://orcid.org/0000-0002-1825-0097") == (
        "https://orcid.org"
    )
    assert _scheme("urn:uuid", None, None) == "urn:uuid"
    assert _scheme(None, None, "https://w3id.org/tern/x") == "https://w3id.org"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "http://creativecommons.org/licenses/by/4.0",
            "https://creativecommons.org/licenses/by/4.0/",
        ),
        (
            "https://licensebuttons.net/l/by-nc/4.0/88x31.png",
            "https://creativecommons.org/licenses/by-nc/4.0/",
        ),
        (
            "CC-BY-4.0 International Attribution",
            "https://creativecommons.org/licenses/by/4.0/",
        ),
        (
            "The Creative Commons Attribution 4.0 International (CC BY 4.0)",
            "https://creativecommons.org/licenses/by/4.0/",
        ),
        (
            "Creative Commons Attribution-NonCommercial-ShareAlike 3.0",
            "https://creativecommons.org/licenses/by-nc-sa/3.0/",
        ),
        (
            "Released under CC0",
            "https://creativecommons.org/publicdomain/zero/1.0/",
        ),
    ],
)
def test_licence_detection(text, expected):
    explicit, inferred = _Builder._licences_in_text(text)
    assert expected in explicit + inferred


def test_plain_strips_html():
    assert _plain("<p>One &amp; two</p><p>Three</p>") == "One & two\n\nThree"
    assert _plain("No markup  here") == "No markup here"


# -- input handling and safety --------------------------------------------

MINIMAL = """<?xml version="1.0"?>
{doctype}
<mdb:MD_Metadata xmlns:mdb="http://standards.iso.org/iso/19115/-3/mdb/2.0"
    xmlns:mri="http://standards.iso.org/iso/19115/-3/mri/1.0"
    xmlns:cit="http://standards.iso.org/iso/19115/-3/cit/2.0"
    xmlns:gco="http://standards.iso.org/iso/19115/-3/gco/1.0"
    xmlns:gex="http://standards.iso.org/iso/19115/-3/gex/1.0"
    xmlns:gml="http://www.opengis.net/gml/3.2">
  <mdb:identificationInfo><mri:MD_DataIdentification>
    <mri:citation><cit:CI_Citation>
      <cit:title><gco:CharacterString>{title}</gco:CharacterString></cit:title>
    </cit:CI_Citation></mri:citation>
    {extent}
  </mri:MD_DataIdentification></mdb:identificationInfo>
</mdb:MD_Metadata>"""


def minimal(title="T", doctype="", extent=""):
    return MINIMAL.format(title=title, doctype=doctype, extent=extent)


def test_external_entity_not_resolved(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET")
    xml = minimal(
        title="&xxe;",
        doctype=f'<!DOCTYPE x [<!ENTITY xxe SYSTEM "file://{secret}">]>',
    )
    record = crosswalk(xml)["record"]
    assert "TOP-SECRET" not in json.dumps(record)


def test_entity_expansion_not_performed():
    laughs = (
        '<!DOCTYPE x [<!ENTITY a "lol"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;">'
        '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;">]>'
    )
    record = crosswalk(minimal(title="&c;", doctype=laughs))["record"]
    assert "lollol" not in json.dumps(record)


def test_rejects_non_iso():
    with pytest.raises(CrosswalkError):
        crosswalk("<html><body>not metadata</body></html>")
    with pytest.raises(CrosswalkError):
        crosswalk("this is not xml <")


def test_csw_wrapper_is_unwrapped():
    inner = minimal(title="Wrapped").split("?>", 1)[1]
    xml = (
        '<csw:GetRecordByIdResponse xmlns:csw="http://www.opengis.net/cat/'
        f'csw/2.0.2">{inner}</csw:GetRecordByIdResponse>'
    )
    assert crosswalk(xml)["record"]["schema:name"] == "Wrapped"


def test_missing_scope_and_required_reported():
    record, report = split(minimal())
    assert report["scope"] == "dataset"
    assert set(report["missing_required"]) == {
        "schema:identifier",
        "schema:dateModified",
        "schema:license or schema:conditionsOfAccess",
        "schema:url or schema:distribution",
    }
    assert record["@id"] == "#resource"


def test_polygon_axis_order_latitude_first():
    extent = """<mri:extent><gex:EX_Extent><gex:geographicElement>
      <gex:EX_BoundingPolygon><gex:polygon>
        <gml:Polygon srsName="urn:ogc:def:crs:EPSG::4326"><gml:exterior>
          <gml:LinearRing>
            <gml:posList>-10 20 -10 30 -20 30 -20 20</gml:posList>
          </gml:LinearRing></gml:exterior></gml:Polygon>
      </gex:polygon></gex:EX_BoundingPolygon>
    </gex:geographicElement></gex:EX_Extent></mri:extent>"""
    record, report = split(minimal(extent=extent))
    place = record["schema:spatialCoverage"][0]
    wkt = place["geosparql:hasGeometry"]["geosparql:asWKT"]["@value"]
    assert wkt == "POLYGON((20 -10, 30 -10, 30 -20, 20 -20, 20 -10))"
    assert place["schema:geo"]["schema:box"] == "-20 20 -10 30"
    assert any("axis order assumed lat/lon" in n for n in report["notes"])


def test_parse_returns_md_metadata():
    root = parse(minimal())
    assert root.tag.endswith("MD_Metadata")
