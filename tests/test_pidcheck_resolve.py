"""Identifier resolution (pidcheck.resolve), replaying recorded responses."""

import json
from pathlib import Path

import httpx
import pytest

from pidcheck import config
from pidcheck.resolve import Cache, NoCache, Resolver

PID = Path(__file__).parent / "fixtures" / "pid"
MANIFEST = json.loads((PID / "manifest.json").read_text())["entries"]


@pytest.fixture(autouse=True)
def no_dotenv(monkeypatch):
    """Ignore any developer .env and token variables."""
    monkeypatch.setattr(config, "_dotenv", lambda path=None: {})
    for name in (
        "ROR_CLIENT_ID",
        "ORCID_CLIENT_ID",
        "ORCID_CLIENT_SECRET",
        "ORCID_ACCESS_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)


class Replay:
    """httpx transport serving the recorded exchanges, redirects included.
    Extra routes ({url: callable(request) -> Response}) take precedence."""

    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.requests: list[httpx.Request] = []
        self.served = {}
        for e in MANIFEST:
            if "status" not in e:
                continue
            for hop in e["hops"]:
                self.served[hop["url"]] = ("redirect", hop)
            self.served[e["final_url"]] = ("final", e)
            if not e["hops"]:
                self.served[e["request"]["url"]] = ("final", e)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if url in self.routes:
            return self.routes[url](request)
        kind, e = self.served.get(url, (None, None))
        if kind == "redirect":
            return httpx.Response(
                e["status"], headers={"location": e["location"]}
            )
        if kind == "final":
            body = (PID / e["body"]).read_bytes() if e["body"] else b""
            return httpx.Response(
                e["status"],
                content=body,
                headers={"content-type": e["content_type"]},
            )
        return httpx.Response(599, text=f"no recording for {url}")


def resolver(replay=None, cache=None) -> tuple[Resolver, Replay]:
    replay = replay or Replay()
    client = httpx.Client(
        transport=httpx.MockTransport(replay), follow_redirects=True
    )
    return Resolver(client, cache or NoCache(), retry_wait=0), replay


# -- schemes ------------------------------------------------------------------


def test_doi_dataset():
    r, _ = resolver()
    res = r.resolve("https://doi.org/10.25901/gg27-ck96", "doi")
    assert res["status"] == "ok"
    assert res["detail"] == {
        "target": "https://portal.tern.org.au/metadata/TERN/"
        "9fefa68b-dbed-4c20-88db-a9429fb4ba97",
        "registration_agency": "DataCite",
        "resource_type": "Dataset",
        "state": "findable",
    }


def test_doi_journal_article():
    r, _ = resolver()
    res = r.resolve("https://doi.org/10.3390/min11111195", "doi")
    assert res["detail"]["registration_agency"] == "Crossref"
    assert res["detail"]["resource_type"] == "journal-article"


def test_doi_not_found():
    r, _ = resolver()
    res = r.resolve("https://doi.org/10.9999/does-not-exist", "doi")
    assert res["status"] == "not-found"


def test_orcid():
    r, _ = resolver()
    ok = r.resolve("https://orcid.org/0000-0002-7591-0303", "orcid")
    assert (ok["status"], ok["detail"]["name"]) == ("ok", "David Baker")
    bad = r.resolve("https://orcid.org/0000-0002-7591-0304", "orcid")
    assert bad["status"] == "not-found"


def test_ror():
    r, _ = resolver()
    inactive = r.resolve("https://ror.org/00892tw58", "ror")
    assert inactive["detail"] == {
        "name": "The University of Adelaide",
        "organisation_status": "inactive",
        "successors": ["https://ror.org/028g18b61"],
    }
    active = r.resolve("https://ror.org/01ej9dk98", "ror")
    assert active["detail"]["organisation_status"] == "active"
    assert "successors" not in active["detail"]
    assert r.resolve("https://ror.org/00000zz00", "ror")["status"] == (
        "not-found"
    )


def test_generic_uris():
    r, _ = resolver()
    party = r.resolve(
        "https://w3id.org/tern/resources/7b838abc-8023-4a2a-ad2e-92a79be93104",
        "persistent-uri",
    )
    assert party["status"] == "ok"
    assert party["detail"]["content_type"] == "text/html"
    assert party["final_url"].startswith("https://linkeddata.tern.org.au/")
    dead = r.resolve(
        "http://cf-pcmdi.llnl.gov/documents/cf-standard-names", "uri"
    )
    assert (dead["status"], dead["http_status"]) == ("not-found", 404)
    persons = r.resolve(
        "https://geonetwork.nci.org.au/geonetwork/srv/resources/persons/",
        "uri",
    )
    assert persons["status"] == "ok"  # GET, not HEAD (which gives 400)


def test_unsupported():
    r, replay = resolver()
    res = r.resolve("urn:uuid:334592c9-991a-4e55-aba1-0b3247387a03", "urn")
    assert res["status"] == "unsupported"
    assert not replay.requests


# -- failures and retries ----------------------------------------------------


def test_server_error_is_unreachable_after_one_retry():
    r, replay = resolver()
    res = r.resolve("https://biodiversity.org.au/afd/taxa/CHIROPTERA", "uri")
    assert (res["status"], res["http_status"]) == ("unreachable", 504)
    assert len(replay.requests) == 2


def test_retry_recovers():
    calls = []

    def flaky(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503)
        return httpx.Response(200, headers={"content-type": "text/html"})

    url = "https://example.org/flaky"
    r, _ = resolver(Replay({url: flaky}))
    assert r.resolve(url, "uri")["status"] == "ok"
    assert len(calls) == 2


def test_timeout_is_unreachable():
    def timeout(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    url = "https://example.org/slow"
    r, _ = resolver(Replay({url: timeout}))
    res = r.resolve(url, "uri")
    assert res["status"] == "unreachable"
    assert "ConnectTimeout" in res["error"]


# -- cache --------------------------------------------------------------------


def test_cache(tmp_path):
    r, replay = resolver(cache=Cache(tmp_path, days=30))
    uri = "https://ror.org/01ej9dk98"
    first = r.resolve(uri, "ror")
    second = r.resolve(uri, "ror")
    assert (first["cached"], second["cached"]) == (False, True)
    assert len(replay.requests) == 1
    assert {k: v for k, v in first.items() if k != "cached"} == {
        k: v for k, v in second.items() if k != "cached"
    }


def test_unreachable_not_cached(tmp_path):
    r, replay = resolver(cache=Cache(tmp_path, days=30))
    uri = "https://biodiversity.org.au/afd/taxa/CHIROPTERA"
    r.resolve(uri, "uri")
    r.resolve(uri, "uri")
    assert len(replay.requests) == 4  # two attempts, twice


def test_expired_cache_entry_ignored(tmp_path):
    r, replay = resolver(cache=Cache(tmp_path, days=-1))
    r.resolve("https://ror.org/01ej9dk98", "ror")
    r.resolve("https://ror.org/01ej9dk98", "ror")
    assert len(replay.requests) == 2


def test_resolve_many_deduplicates():
    r, replay = resolver()
    items = [("https://ror.org/01ej9dk98", "ror")] * 3 + [
        ("https://orcid.org/0000-0002-7591-0303", "orcid")
    ]
    results = r.resolve_many(items)
    assert set(results) == {
        "https://ror.org/01ej9dk98",
        "https://orcid.org/0000-0002-7591-0303",
    }
    assert len(replay.requests) == 2


# -- tokens -------------------------------------------------------------------


def test_ror_client_id_sent_only_to_ror(monkeypatch):
    monkeypatch.setenv("ROR_CLIENT_ID", "my-ror-client")
    r, replay = resolver()
    r.resolve("https://ror.org/01ej9dk98", "ror")
    res = r.resolve("https://orcid.org/0000-0002-7591-0303", "orcid")
    sent = {
        str(q.url).split("/")[2]: q.headers.get("client-id")
        for q in replay.requests
    }
    assert sent == {"api.ror.org": "my-ror-client", "pub.orcid.org": None}
    assert "my-ror-client" not in json.dumps(res)


def test_orcid_access_token(monkeypatch):
    monkeypatch.setenv("ORCID_ACCESS_TOKEN", "secret-token")
    r, replay = resolver()
    res = r.resolve("https://orcid.org/0000-0002-7591-0303", "orcid")
    r.resolve("https://ror.org/01ej9dk98", "ror")
    auth = {
        str(q.url).split("/")[2]: q.headers.get("authorization")
        for q in replay.requests
    }
    assert auth == {
        "pub.orcid.org": "Bearer secret-token",
        "api.ror.org": None,
    }
    assert "secret-token" not in json.dumps(res)


def test_orcid_client_credentials(monkeypatch):
    monkeypatch.setenv("ORCID_CLIENT_ID", "APP-123")
    monkeypatch.setenv("ORCID_CLIENT_SECRET", "s3cret")
    exchanges = []

    def token(request):
        exchanges.append(request.content.decode())
        return httpx.Response(200, json={"access_token": "from-exchange"})

    r, replay = resolver(Replay({"https://orcid.org/oauth/token": token}))
    r.resolve("https://orcid.org/0000-0002-7591-0303", "orcid")
    r.resolve("https://orcid.org/0000-0002-7591-0304", "orcid")
    assert len(exchanges) == 1  # obtained once, then reused
    assert "grant_type=client_credentials" in exchanges[0]
    assert "scope=%2Fread-public" in exchanges[0]
    orcid_calls = [q for q in replay.requests if q.url.host == "pub.orcid.org"]
    assert all(
        q.headers["authorization"] == "Bearer from-exchange"
        for q in orcid_calls
    )


# -- configuration ------------------------------------------------------------


def test_dotenv_parsing(tmp_path, monkeypatch):
    monkeypatch.undo()  # use the real parser
    env = tmp_path / ".env"
    env.write_text(
        "# comment\n"
        "ROR_CLIENT_ID=abc\n"
        "export ORCID_CLIENT_ID = 'APP-1'\n"
        'ORCID_CLIENT_SECRET="x y"\n'
        "EMPTY=\n"
        "not a setting\n"
    )
    assert config._dotenv(env) == {
        "ROR_CLIENT_ID": "abc",
        "ORCID_CLIENT_ID": "APP-1",
        "ORCID_CLIENT_SECRET": "x y",
        "EMPTY": "",
    }


def test_environment_overrides_dotenv(monkeypatch):
    monkeypatch.setattr(
        config, "_dotenv", lambda path=None: {"ROR_CLIENT_ID": "from-file"}
    )
    assert config.setting("ROR_CLIENT_ID") == "from-file"
    monkeypatch.setenv("ROR_CLIENT_ID", "from-env")
    assert config.setting("ROR_CLIENT_ID") == "from-env"
