"""The combined, compact identifier report (pidcheck.check_record)."""

import json

import httpx
import pytest
from pid_replay import Replay, isolated_settings, resolver  # noqa: F401

from pidcheck import check_record

pytestmark = pytest.mark.usefixtures("isolated_settings")

CC_BY = "https://creativecommons.org/licenses/by/4.0/"


def _person(name, orcid):
    return {
        "@type": ["schema:Person"],
        "schema:name": name,
        "schema:identifier": orcid,
    }


RECORD = {
    "@id": "https://doi.org/10.3390/min11111195",  # a journal article
    "@type": ["schema:Dataset"],
    "schema:license": [{"@id": CC_BY}],
    "schema:creator": {
        "@list": [
            _person("Baker, David", "https://orcid.org/0000-0002-7591-0303"),
            _person("Smith, Jane", "https://orcid.org/0000-0002-7591-0303"),
            _person("Nobody, A.", "https://orcid.org/0000-0002-7591-0304"),
        ]
    },
    "schema:publisher": {
        "@type": ["schema:Organization"],
        "schema:name": "The University of Adelaide",
        "schema:identifier": "https://ror.org/00892tw58",
    },
}


def run(record=RECORD, **kw):
    cc = {
        CC_BY: lambda q: httpx.Response(
            200, text="<html/>", headers={"content-type": "text/html"}
        )
    }
    r, replay = resolver(Replay(cc))
    return check_record(record, r, **kw), replay


def codes(report, key):
    return [f["code"] for f in report[key]]


def test_findings_by_severity():
    report, _ = run()
    assert set(codes(report, "problems")) == {
        "orcid-check-digit",  # offline
        "identifier-not-found",  # ORCID 404s too
        # one ORCID iD named 'Baker, David' and 'Smith, Jane'
        "shared-party-identifier",
    }
    assert set(codes(report, "warnings")) == {
        "dataset-doi-is-publication",
        "inactive-organisation",
    }
    assert codes(report, "notes") == ["orcid-name-mismatch"]
    msg = {f["code"]: f["message"] for f in report["warnings"]}
    assert "Crossref as a journal-article" in msg["dataset-doi-is-publication"]
    assert (
        "successor is https://ror.org/028g18b61"
        in (msg["inactive-organisation"])
    )
    assert "Smith, Jane" in report["notes"][0]["message"]


def test_summary():
    s = run()[0]["summary"]
    assert s["dataset_doi"] == "https://doi.org/10.3390/min11111195"
    assert (s["dataset_doi_agency"], s["dataset_doi_type"]) == (
        "Crossref",
        "journal-article",
    )
    assert s["identifiers_checked"] == 5  # DOI, 2 ORCIDs, ROR, licence
    assert s["identifiers_resolving"] == 4
    assert s["terms"]["checked"] == 0
    assert s["rva_vocabularies"] == []


def test_entries_are_compact_and_serialisable():
    report, _ = run()
    for key in ("problems", "warnings", "notes"):
        for f in report[key]:
            assert set(f) <= {"code", "message", "count", "examples"}
    json.dumps(report)


def test_lists_are_capped():
    report, _ = run(max_findings=1)
    assert len(report["problems"]) == 1
    assert report["problems_omitted"] == 2
    assert "warnings_omitted" in report
    assert "notes_omitted" not in report


def test_offline_only():
    report, replay = run(resolve=False)
    assert not replay.requests
    assert "identifiers_checked" not in report["summary"]
    assert "orcid-check-digit" in codes(report, "problems")
    assert "dataset-doi-is-publication" not in codes(report, "warnings")
