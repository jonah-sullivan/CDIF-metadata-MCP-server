"""Offline identifier checks (pidcheck.identifiers)."""

from pathlib import Path

import pytest

from crosswalk import crosswalk
from pidcheck import check_offline, classify, extract
from pidcheck.identifiers import isni_ok, orcid_ok, ror_ok

ISO = Path(__file__).parent / "fixtures" / "iso"


# -- classification ---------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "hint", "kind", "normalised"),
    [
        (
            "https://doi.org/10.25901/gg27-ck96",
            None,
            "doi",
            "https://doi.org/10.25901/gg27-ck96",
        ),
        (
            "doi:10.25901/gg27-ck96",
            None,
            "doi",
            "https://doi.org/10.25901/gg27-ck96",
        ),
        (
            "http://dx.doi.org/10.1000/X",
            None,
            "doi",
            "https://doi.org/10.1000/X",
        ),
        (
            "10.60623/3159pc3b",
            "https://doi.org",
            "doi",
            "https://doi.org/10.60623/3159pc3b",
        ),
        (
            "https://orcid.org/0000-0002-1825-0097",
            None,
            "orcid",
            "https://orcid.org/0000-0002-1825-0097",
        ),
        (
            "0000-0002-1825-0097",
            "ORCID",
            "orcid",
            "https://orcid.org/0000-0002-1825-0097",
        ),
        (
            "https://ror.org/01ej9dk98",
            None,
            "ror",
            "https://ror.org/01ej9dk98",
        ),
        ("01ej9dk98", "https://ror.org", "ror", "https://ror.org/01ej9dk98"),
        ("01ej9dk98", None, "not-uri", "01ej9dk98"),  # no scheme given
        (
            "https://isni.org/isni/0000000121032683",
            None,
            "isni",
            "https://isni.org/isni/0000000121032683",
        ),
        (
            "hdl:20.500.12345/abc",
            None,
            "handle",
            "https://hdl.handle.net/20.500.12345/abc",
        ),
        ("ark:/12345/x9", None, "ark", "https://n2t.net/ark:/12345/x9"),
        (
            "https://raid.org/10.80368/b1adfb3a",
            None,
            "raid",
            "https://raid.org/10.80368/b1adfb3a",
        ),
        ("igsn:AU1234", None, "igsn", "igsn:AU1234"),
        (
            "https://w3id.org/tern/resources/x",
            None,
            "persistent-uri",
            "https://w3id.org/tern/resources/x",
        ),
        (
            "http://linked.data.gov.au/def/tern-cv/x",
            None,
            "persistent-uri",
            "http://linked.data.gov.au/def/tern-cv/x",
        ),
        (
            "https://example.org/people/1",
            None,
            "uri",
            "https://example.org/people/1",
        ),
        (
            "mailto:someone@example.org",
            None,
            "uri",
            "mailto:someone@example.org",
        ),
        (
            "urn:uuid:334592c9-991a-4e55-aba1-0b3247387a03",
            None,
            "urn",
            "urn:uuid:334592c9-991a-4e55-aba1-0b3247387a03",
        ),
        (
            "334592C9-991A-4E55-ABA1-0B3247387A03",
            "urn:uuid",
            "urn",
            "urn:uuid:334592c9-991a-4e55-aba1-0b3247387a03",
        ),
        (
            "Terrestrial Ecosystem Research Network",
            None,
            "not-uri",
            "Terrestrial Ecosystem Research Network",
        ),
    ],
)
def test_classify(value, hint, kind, normalised):
    assert classify(value, hint) == (kind, normalised)


def test_check_digits():
    assert orcid_ok("https://orcid.org/0000-0002-1825-0097")
    assert orcid_ok("https://orcid.org/0009-0000-4544-8528")
    assert not orcid_ok("https://orcid.org/0000-0002-7591-0304")
    assert ror_ok("https://ror.org/01ej9dk98")
    assert ror_ok("https://ror.org/028g18b61")
    assert not ror_ok("https://ror.org/00892tw59")
    assert isni_ok("https://isni.org/isni/0000000121032683")
    assert not isni_ok("https://isni.org/isni/0000000121032684")


# -- extraction ---------------------------------------------------------------

RECORD = {
    "@context": {"schema": "http://schema.org/"},
    "@id": "https://doi.org/10.25901/gg27-ck96",
    "@type": ["schema:Dataset"],
    "schema:identifier": {
        "@type": ["schema:PropertyValue"],
        "schema:propertyID": "https://doi.org",
        "schema:value": "10.25901/gg27-ck96",
        "schema:url": "https://doi.org/10.25901/gg27-ck96",
    },
    "schema:sameAs": ["https://example.org/datasets/42"],
    "schema:license": [
        {"@id": "https://creativecommons.org/licenses/by/4.0/"}
    ],
    "schema:creator": {
        "@list": [
            {
                "@type": ["schema:Person"],
                "schema:name": "Carberry, Josiah",
                "schema:identifier": {
                    "@type": ["schema:PropertyValue"],
                    "schema:propertyID": "https://orcid.org",
                    "schema:value": "0000-0002-1825-0097",
                },
                "schema:affiliation": {
                    "@type": ["schema:Organization"],
                    "schema:name": "The University of Melbourne",
                    "schema:identifier": "https://ror.org/01ej9dk98",
                },
            }
        ]
    },
    "schema:contributor": [
        {
            "@type": ["schema:Role"],
            "schema:roleName": {
                "@type": ["schema:DefinedTerm"],
                "schema:name": "pointOfContact",
                "schema:inDefinedTermSet": "https://standards.iso.org/x",
            },
            "schema:contributor": {
                "@type": ["schema:Organization"],
                "schema:name": "TERN",
            },
        }
    ],
    "schema:keywords": [
        "plain keyword",
        {
            "@type": ["schema:DefinedTerm"],
            "schema:name": "BATS",
            "schema:identifier": "https://gcmd.earthdata.nasa.gov/kms/"
            "concept/9db9cb8c-7d18-4922-990c-b610d22356eb",
            "schema:inDefinedTermSet": "GCMD Science Keywords",
        },
    ],
    "schema:variableMeasured": [
        {
            "@type": ["schema:PropertyValue"],
            "schema:name": "Temperature of the water body",
            "schema:propertyID": "http://vocab.nerc.ac.uk/collection/P01/"
            "current/TEMPPR01",
            "schema:unitText": "Degrees Celsius",
            "schema:unitCode": "http://vocab.nerc.ac.uk/collection/P06/"
            "current/UPAA",
        }
    ],
}


def test_extract_roles():
    by_role = {}
    for i in extract(RECORD):
        by_role.setdefault(i.role, []).append(i)
    assert {i.kind for i in by_role["dataset"]} == {"doi"}
    assert by_role["alternate"][0].kind == "uri"
    assert by_role["licence"][0].value.startswith("https://creativecommons")
    person = by_role["person"][0]
    assert (person.kind, person.label) == ("orcid", "Carberry, Josiah")
    assert by_role["organisation"][0].kind == "ror"
    keyword = by_role["keyword"][0]
    assert keyword.label == "BATS"
    assert keyword.path.startswith("schema:keywords[1]")
    assert {i.value for i in by_role["term-set"]} == {
        "https://standards.iso.org/x",
        "GCMD Science Keywords",
    }
    assert by_role["variable"][0].label == "Temperature of the water body"
    assert by_role["unit"][0].label == "Degrees Celsius"
    assert "defined-term" not in by_role  # roleName has no identifier


def test_clean_record_summary():
    result = check_offline(RECORD)
    s = result["summary"]
    assert s["dataset_has_pid"]
    assert s["dataset_doi"] == "https://doi.org/10.25901/gg27-ck96"
    assert (s["people"], s["people_with_orcid"]) == (1, 1)
    assert (s["organisations"], s["organisations_with_ror"]) == (2, 1)
    assert (s["keywords"], s["keywords_with_uri"]) == (2, 1)
    assert (s["variables"], s["variables_with_uri"]) == (1, 1)
    # Only the named-but-unidentified vocabulary is worth reporting.
    assert [f["code"] for f in result["findings"]] == ["term-set-not-a-uri"]


# -- findings -----------------------------------------------------------------


def _person(name, ident):
    return {
        "@type": ["schema:Person"],
        "schema:name": name,
        "schema:identifier": ident,
    }


def test_findings():
    record = {
        "@id": "https://catalogue.example.org/records/1",
        "schema:identifier": "1",
        "schema:contributor": [
            _person("A", "https://orcid.org/0000-0002-7591-0304"),
            _person("B", "https://example.org/persons/"),
            _person("C", "https://example.org/persons/"),
            _person("D", "doi:10.1000/x"),
            {
                "@type": ["schema:Organization"],
                "schema:name": "E",
                "schema:identifier": "https://ror.org/00892tw59",
            },
        ],
    }
    codes = {
        (f["code"], f["severity"]) for f in check_offline(record)["findings"]
    }
    assert ("orcid-check-digit", "problem") in codes
    assert ("ror-check-digit", "problem") in codes
    assert ("shared-party-identifier", "problem") in codes
    assert ("party-local-url", "warning") in codes
    assert ("not-a-uri", "warning") in codes  # schema:identifier "1"
    assert ("dataset-no-pid", "warning") in codes
    assert ("non-canonical-form", "info") in codes  # doi:10.1000/x


def test_findings_reported_once():
    record = {
        "schema:contributor": [
            _person("B", "https://example.org/p/"),
            _person("B", "https://example.org/p/"),
        ]
    }
    findings = check_offline(record)["findings"]
    assert [f["code"] for f in findings] == ["party-local-url"]


# -- the bundled NCRIS records ----------------------------------------------


def run(name):
    return check_offline(crosswalk((ISO / name).read_bytes())["record"])


def test_nci_shared_person_identifier():
    findings = run("tern-awra-canopy-conductance.xml")["findings"]
    shared = [f for f in findings if f["code"] == "shared-party-identifier"]
    assert shared
    assert shared[0]["value"].endswith("/srv/resources/persons/")


def test_metadata_uuid_is_not_reported_twice():
    codes = [f["code"] for f in run("aodn-glider-yamba.xml")["findings"]]
    assert "dataset-no-pid" in codes
    assert "not-a-uri" not in codes  # the urn:uuid code space is known


def test_auscope_orcids_and_rors():
    s = run("auscope-calperum-czo.xml")["summary"]
    assert (s["people"], s["people_with_orcid"]) == (3, 3)
    assert s["organisations_with_ror"] == 2
    assert s["dataset_doi"] == "https://doi.org/10.60623/3159pc3b"
