"""Tests for resolving and fetching GeoNetwork record XML (no network)."""

import contextlib

import pytest

from crosswalk import CrosswalkError, geonetwork
from crosswalk.geonetwork import fetch_xml, record_xml_url

XML = "https://geonetwork.tern.org.au/geonetwork/srv/api/records/{}/formatters/xml"
UUID = "9fefa68b-dbed-4c20-88db-a9429fb4ba97"


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "uuid": UUID,
            "catalogue": "https://geonetwork.tern.org.au/geonetwork",
        },
        {
            "uuid": UUID,
            "catalogue": "https://geonetwork.tern.org.au/geonetwork/",
        },
        {
            "uuid": UUID,
            "catalogue": "https://geonetwork.tern.org.au/geonetwork/srv/eng",
        },
        {
            "url": "https://geonetwork.tern.org.au/geonetwork/srv/eng/"
            f"catalog.search#/metadata/{UUID}"
        },
        {
            "url": "https://geonetwork.tern.org.au/geonetwork/srv/api/"
            f"records/{UUID}"
        },
        {
            "url": "https://geonetwork.tern.org.au/geonetwork/srv/api/"
            f"records/{UUID}/formatters/xml"
        },
    ],
)
def test_record_xml_url_geonetwork(kwargs):
    assert record_xml_url(**kwargs) == XML.format(UUID)


def test_other_urls_used_as_given():
    url = "https://example.org/metadata/record.xml"
    assert record_xml_url(url=url) == url


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"uuid": UUID},
        {"catalogue": "https://x.org"},
        {"url": "file:///etc/passwd"},
        {"uuid": "../../etc", "catalogue": "https://x.org"},
        {"uuid": "a/b", "catalogue": "https://x.org"},
        {"uuid": UUID, "catalogue": "ftp://x.org"},
    ],
)
def test_record_xml_url_rejects(kwargs):
    with pytest.raises(CrosswalkError):
        record_xml_url(**kwargs)


class _FakeResponse:
    def __init__(self, ctype: str, chunks: list[bytes]):
        self.headers = {"content-type": ctype}
        self._chunks = chunks

    def raise_for_status(self):
        pass

    def iter_bytes(self):
        yield from self._chunks


def _fake_stream(response):
    @contextlib.contextmanager
    def stream(*args, **kwargs):
        yield response

    return stream


def test_fetch_xml(monkeypatch):
    monkeypatch.setattr(
        geonetwork.httpx,
        "stream",
        _fake_stream(_FakeResponse("application/xml", [b"<a/>", b""])),
    )
    assert fetch_xml("https://x.org/r.xml") == b"<a/>"


def test_fetch_xml_refuses_html(monkeypatch):
    monkeypatch.setattr(
        geonetwork.httpx,
        "stream",
        _fake_stream(_FakeResponse("text/html; charset=utf-8", [b"<html>"])),
    )
    with pytest.raises(CrosswalkError, match="HTML page"):
        fetch_xml("https://x.org/page")


def test_fetch_xml_size_limit(monkeypatch):
    monkeypatch.setattr(geonetwork, "MAX_XML_BYTES", 10)
    monkeypatch.setattr(
        geonetwork.httpx,
        "stream",
        _fake_stream(_FakeResponse("application/xml", [b"x" * 8] * 3)),
    )
    with pytest.raises(CrosswalkError, match="larger than"):
        fetch_xml("https://x.org/big.xml")


def test_fetch_xml_html_hint_names_uuid(monkeypatch):
    monkeypatch.setattr(
        geonetwork.httpx,
        "stream",
        _fake_stream(_FakeResponse("text/html", [b"<html>"])),
    )
    with pytest.raises(CrosswalkError, match=f"uuid='{UUID}'"):
        fetch_xml(f"https://portal.tern.org.au/metadata/{UUID}")
