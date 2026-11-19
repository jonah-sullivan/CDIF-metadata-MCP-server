"""Vocabulary checks (pidcheck.vocab), replaying recorded responses."""

import json

import httpx
import pytest
from pid_replay import Replay, body, isolated_settings, resolver  # noqa: F401

from pidcheck import extract
from pidcheck.resolve import Cache
from pidcheck.vocab import (
    check_vocabulary,
    dereference,
    label_matches,
    rva_vocabulary,
)

pytestmark = pytest.mark.usefixtures("isolated_settings")

NVS_P01 = "http://vocab.nerc.ac.uk/collection/P01/current/TEMPPR01"
GCMD = (
    "https://gcmd.earthdata.nasa.gov/kms/concept/"
    "9db9cb8c-7d18-4922-990c-b610d22356eb"
)
ANZSRC = "https://linked.data.gov.au/def/anzsrc-for/2020/310302"
TERN_CV = (
    "http://linked.data.gov.au/def/tern-cv/"
    "903c614e-e036-4d50-ba50-9ec477c25654"
)
AODN = "http://vocab.aodn.org.au/def/discovery_parameter/entity/565"
QUDT = "http://qudt.org/vocab/unit/NUM"
TERN_PARTY = (
    "https://w3id.org/tern/resources/7b838abc-8023-4a2a-ad2e-92a79be93104"
)
AFD = "https://biodiversity.org.au/afd/taxa/CHIROPTERA"
CF = "http://cf-pcmdi.llnl.gov/documents/cf-standard-names"


# -- dereferencing ------------------------------------------------------------


@pytest.mark.parametrize(
    ("uri", "label", "concept", "rva_path"),
    [
        (NVS_P01, "Temperature of the water body", True, None),
        (GCMD, "BATS", True, None),
        (
            ANZSRC,
            "Community ecology (excl. invasive species ecology)",
            True,
            "anzsrc-2020-for",
        ),
        (TERN_CV, "Climate and Bioclimate", True, None),
        (
            AODN,
            "Pressure (measured variable) in the water body exerted by "
            "overlying sea water and any medium above it",
            True,
            "aodn/aodn-discovery-parameter-vocabulary/version-1-7",
        ),
        (QUDT, "Number", False, None),
    ],
)
def test_dereference_terms(uri, label, concept, rva_path):
    r, _ = resolver()
    d = dereference(r, uri)
    assert d["status"] == "ok"
    assert label in d["labels"]
    assert d["concept"] is concept
    assert d["rva_path"] == rva_path
    assert d["deprecated"] is False


def test_nvs_slash_variant_and_no_scheme():
    r, _ = resolver()
    d = dereference(r, "http://vocab.nerc.ac.uk/collection/L06/current/39")
    assert d["labels"] == ["naval vessel"]
    assert d["schemes"] == []


def test_gcmd_xml_parsed_as_rdf():
    r, _ = resolver()
    d = dereference(r, GCMD)
    assert d["content_type"] == "application/xml"
    assert d["schemes"]  # skos:inScheme resolved against xml:base


def test_aodn_html_redirect_retried_as_turtle():
    r, replay = resolver()
    d = dereference(r, AODN)
    assert d["final_url"].endswith("&_format=ttl")
    assert any("resource.html" in str(q.url) for q in replay.requests)


@pytest.mark.parametrize(
    ("uri", "status"),
    [(TERN_PARTY, "not-rdf"), (AFD, "unreachable"), (CF, "not-found")],
)
def test_dereference_failures(uri, status):
    r, _ = resolver()
    assert dereference(r, uri)["status"] == status


def test_dereference_cache(tmp_path):
    r, replay = resolver(cache=Cache(tmp_path, days=30))
    first = dereference(r, NVS_P01)
    second = dereference(r, NVS_P01)
    assert (first["cached"], second["cached"]) == (False, True)
    n = len(replay.requests)
    dereference(r, AFD)
    dereference(r, AFD)  # unreachable: not cached, asked again
    assert len(replay.requests) == n + 4


# -- RVA ----------------------------------------------------------------------


def test_rva_vocabulary_versions():
    r, _ = resolver()
    anzsrc = rva_vocabulary(r, "anzsrc-2020-for")
    assert (
        anzsrc["vocabulary"],
        anzsrc["version"],
        anzsrc["version_status"],
    ) == ("anzsrc-2020-for", "2020", "current")
    aodn = rva_vocabulary(
        r, "aodn/aodn-discovery-parameter-vocabulary/version-1-7"
    )
    assert (aodn["owner"], aodn["version"], aodn["version_status"]) == (
        "AODN",
        "version-1-7",
        "current",
    )


# -- labels -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "labels", "ok"),
    [
        ("BATS", ["BATS"], True),
        ("bats ", ["BATS"], True),
        ("sea_water_temperature", ["Sea water temperature"], True),
        (
            "EARTH SCIENCE > OCEANS > OCEAN TEMPERATURE",
            ["OCEAN TEMPERATURE"],
            True,
        ),
        ("Oceans | Ocean Temperature", ["OCEAN TEMPERATURE"], True),
        ("Temperature", ["Temperature of the water body"], False),
    ],
)
def test_label_matches(name, labels, ok):
    assert label_matches(name, labels) is ok


# -- checking a record --------------------------------------------------------


def _term(name, uri, term_set=None):
    t = {
        "@type": ["schema:DefinedTerm"],
        "schema:name": name,
        "schema:identifier": uri,
    }
    if term_set:
        t["schema:inDefinedTermSet"] = term_set
    return t


DEPRECATED = "https://vocab.example.org/old-term"
WEB_PAGE_SET = "https://www.example.org/our-keywords"


def _routes():
    old = (
        "@prefix skos: <http://www.w3.org/2004/02/skos/core#> .\n"
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
        f'<{DEPRECATED}> a skos:Concept ; skos:prefLabel "Old" ;\n'
        "  owl:deprecated true .\n"
    )
    return {
        DEPRECATED: lambda q: httpx.Response(
            200, text=old, headers={"content-type": "text/turtle"}
        ),
        WEB_PAGE_SET: lambda q: httpx.Response(
            200, text="<html></html>", headers={"content-type": "text/html"}
        ),
    }


def test_check_vocabulary_findings():
    party2 = TERN_PARTY.replace("7b838abc", "00000000")
    record = {
        "schema:keywords": [
            _term("BATS", GCMD),
            _term("Ecology", ANZSRC),  # wrong label
            _term("Party A", TERN_PARTY),  # HTML only
            _term("Party B", party2),  # HTML only, same host
            _term("Chiroptera", AFD),  # service down
            _term("Old", DEPRECATED),
            _term(
                "Temperature of the water body",
                NVS_P01,
                "http://vocab.nerc.ac.uk/collection/P02/current/",
            ),
            _term("Climate and Bioclimate", TERN_CV, CF),  # dead term set
            _term("Number", QUDT, WEB_PAGE_SET),
        ]
    }
    routes = _routes()
    tern_html = body("tern-party")
    routes[party2] = lambda q: httpx.Response(
        200, content=tern_html, headers={"content-type": "text/html"}
    )
    r, _ = resolver(Replay(routes))
    result = check_vocabulary(extract(record), r)
    s = result["summary"]
    assert s["terms"] == 9
    assert (s["label_matches"], s["label_mismatches"]) == (5, 1)
    assert (s["deprecated"], s["not_machine_readable"], s["unreachable"]) == (
        1,
        2,
        1,
    )
    assert s["rva_vocabularies"] == 1  # ANZSRC

    by_code = {f["code"]: f for f in result["findings"]}
    assert "Ecology" in by_code["label-mismatch"]["message"]
    grouped = by_code["term-not-machine-readable"]
    assert grouped["count"] == 2
    assert grouped["examples"] == ["Party A", "Party B"]
    assert "2 terms from w3id.org" in grouped["message"]
    assert by_code["term-unchecked"]["severity"] == "info"
    assert by_code["deprecated-term"]["severity"] == "warning"
    assert by_code["scheme-mismatch"]["severity"] == "info"
    assert by_code["term-set-not-found"]["value"] == CF
    assert by_code["term-set-web-page"]["value"] == WEB_PAGE_SET
    json.dumps(result)  # serialisable


def test_superseded_rva_version():
    detail = json.loads(body("rva-vocabulary-anzsrc"))
    for v in detail["version"]:
        v["status"] = "superseded"
    url = (
        "https://vocabs.ardc.edu.au/registry/api/resource/vocabularies/"
        "316?includeVersions=true&includeAccessPoints=true"
    )
    r, _ = resolver(Replay({url: lambda q: httpx.Response(200, json=detail)}))
    record = {
        "schema:keywords": [
            _term("Community ecology (excl. invasive species ecology)", ANZSRC)
        ]
    }
    codes = [
        f["code"] for f in check_vocabulary(extract(record), r)["findings"]
    ]
    assert codes == ["superseded-vocabulary-version"]


def test_shared_uri_reported_once_not_as_terms():
    """AODN uses the (dead) CF standard names page as every variable's
    propertyID: it identifies a code space, not each variable."""
    record = {
        "schema:variableMeasured": [
            {
                "@type": ["schema:PropertyValue"],
                "schema:name": name,
                "schema:propertyID": CF,
            }
            for name in ("sea_water_temperature", "wind_from_direction")
        ]
    }
    r, _ = resolver()
    result = check_vocabulary(extract(record), r)
    codes = [f["code"] for f in result["findings"]]
    assert codes == ["term-uri-shared", "term-set-not-found"]
    assert result["summary"]["shared_uris"] == 1
    assert result["summary"]["not_found"] == 0
    assert (
        "2 terms ('sea_water_temperature', 'wind_from_direction')"
        in (result["findings"][0]["message"])
    )


def test_all_labels_kept_english_first():
    """QUDT has labels in dozens of languages; the English one must be
    found however many there are, and shown first."""
    other = "".join(
        f'  rdfs:label "Label {n}"@x-{n:02d} ;\n' for n in range(15)
    )
    ttl = (
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
        "<https://units.example.org/W-PER-M2>\n"
        + other
        + '  rdfs:label "Watt per Square Metre"@en .\n'
    )
    uri = "https://units.example.org/W-PER-M2"
    r, _ = resolver(
        Replay(
            {
                uri: lambda q: httpx.Response(
                    200, text=ttl, headers={"content-type": "text/turtle"}
                )
            }
        )
    )
    d = dereference(r, uri)
    assert d["labels"][0] == "Watt per Square Metre"
    assert len(d["labels"]) == 16
