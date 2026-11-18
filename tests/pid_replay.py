"""Offline replay of recorded identifier-service responses, for tests.

The recordings are in tests/fixtures/pid/ (see its README). Replay serves
them through an httpx MockTransport, redirect hops included, so pidcheck
runs exactly as it would against the live services.
"""

import json
from pathlib import Path

import httpx
import pytest

from pidcheck import config
from pidcheck.resolve import NoCache, Resolver

PID = Path(__file__).parent / "fixtures" / "pid"
MANIFEST = json.loads((PID / "manifest.json").read_text())["entries"]
TOKEN_SETTINGS = (
    "ROR_CLIENT_ID",
    "ORCID_CLIENT_ID",
    "ORCID_CLIENT_SECRET",
    "ORCID_ACCESS_TOKEN",
)


@pytest.fixture
def isolated_settings(monkeypatch):
    """Ignore any developer .env and token environment variables."""
    monkeypatch.setattr(config, "_dotenv", lambda path=None: {})
    for name in TOKEN_SETTINGS:
        monkeypatch.delenv(name, raising=False)


class Replay:
    """httpx transport serving the recorded exchanges, redirects included.
    Extra routes ({url: callable(request) -> Response}) take precedence;
    anything else gets status 599."""

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


def body(name: str) -> bytes:
    """A recorded response body, by recording name."""
    entry = next(e for e in MANIFEST if e["name"] == name)
    return (PID / entry["body"]).read_bytes()


def resolver(replay=None, cache=None) -> tuple[Resolver, Replay]:
    replay = replay or Replay()
    client = httpx.Client(
        transport=httpx.MockTransport(replay), follow_redirects=True
    )
    return Resolver(client, cache or NoCache(), retry_wait=0), replay
