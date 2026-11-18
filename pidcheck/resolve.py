"""Resolve identifiers: does each one lead somewhere, and to what?

Each identifier is checked the way its scheme allows:

- DOI: the doi.org handle API (no landing page is fetched), its
  registration agency, and its resource type from DataCite or Crossref.
- ORCID: the ORCID public API (the person's name).
- ROR: the ROR API (name, active or inactive, successor).
- Any other http(s) URI: a GET request, following redirects, without
  reading the body. (HEAD is not used: some servers answer it with 400.)

Statuses:
  ok           the identifier resolves
  not-found    the service says it does not exist (404, 410, unknown DOI)
  broken       another client error (e.g. 400, 403)
  unreachable  timeout, connection failure or server error: a problem with
               the service, not necessarily with the identifier
  unsupported  no way to resolve it (a URN, a plain string...)

Results with status ok, not-found or broken are cached on disk
(.cache/pidcheck/ by default) for PIDCHECK_CACHE_DAYS days, so repeated
checks do not repeat requests. Requests are limited to two at a time per
host. Optional tokens (ROR_CLIENT_ID; ORCID_CLIENT_ID and
ORCID_CLIENT_SECRET, or ORCID_ACCESS_TOKEN) are read from the environment
or .env and are sent only to their own service.
"""

import datetime
import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

from . import config

USER_AGENT = "cdif-mcp/0.1 (pidcheck)"
TIMEOUT = 30
PER_HOST = 2  # concurrent requests per host
WORKERS = 8
RETRY_STATUSES = {429, 502, 503, 504}
CACHED_STATUSES = {"ok", "not-found", "broken"}
ORCID_TOKEN_URL = "https://orcid.org/oauth/token"


# -- cache ------------------------------------------------------------------


class Cache:
    """One JSON file per key, with an expiry date."""

    def __init__(
        self, directory: Path | None = None, days: float | None = None
    ):
        self.dir = directory or config.cache_dir()
        self.days = config.cache_days() if days is None else days

    def _path(self, key: str) -> Path:
        h = hashlib.sha256(key.encode()).hexdigest()
        return self.dir / h[:2] / f"{h}.json"

    def get(self, key: str):
        path = self._path(key)
        try:
            entry = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            return None
        if entry.get("expires", 0) < time.time() or entry.get("key") != key:
            return None
        return entry["value"]

    def put(self, key: str, value) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "key": key,
            "expires": time.time() + self.days * 86400,
            "value": value,
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(entry), "utf-8")
        tmp.replace(path)


class NoCache(Cache):
    def __init__(self):
        pass

    def get(self, key):
        return None

    def put(self, key, value):
        pass


# -- resolver -----------------------------------------------------------------


class Resolver:
    def __init__(
        self,
        client: httpx.Client | None = None,
        cache: Cache | None = None,
        retry_wait: float = 1.0,
    ):
        self.client = client or httpx.Client(
            timeout=TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
        self.cache = cache if cache is not None else Cache()
        self.retry_wait = retry_wait
        self._host_locks: dict[str, threading.Semaphore] = {}
        self._lock = threading.Lock()
        self._orcid_token: str | None = None

    # -- HTTP --------------------------------------------------------------

    def _host_gate(self, url: str) -> threading.Semaphore:
        host = urlsplit(url).hostname or ""
        with self._lock:
            return self._host_locks.setdefault(
                host, threading.Semaphore(PER_HOST)
            )

    def request(
        self,
        url: str,
        accept: str = "*/*",
        headers: dict | None = None,
        read: bool = True,
    ):
        """GET url -> (response or None, error text or None). Retries once
        on timeouts, connection errors, 429 and 5xx gateway errors. With
        read=False the body is not downloaded."""
        hdrs = {"Accept": accept, **(headers or {})}
        error = None
        for attempt in range(2):
            try:
                with self._host_gate(url):
                    if read:
                        r = self.client.get(url, headers=hdrs)
                    else:
                        with self.client.stream("GET", url, headers=hdrs) as r:
                            pass
                if r.status_code not in RETRY_STATUSES or attempt:
                    return r, None
                wait = r.headers.get("retry-after", "")
                time.sleep(
                    min(float(wait), 10) if wait.isdigit() else self.retry_wait
                )
            except httpx.HTTPError as e:
                error = f"{type(e).__name__}: {e}"[:200]
                if attempt:
                    break
                time.sleep(self.retry_wait)
        return None, error

    # -- results -------------------------------------------------------------

    @staticmethod
    def _result(uri, kind, status, response=None, error=None, **detail):
        return {
            "uri": uri,
            "kind": kind,
            "status": status,
            "http_status": response.status_code
            if response is not None
            else None,
            "final_url": str(response.url) if response is not None else None,
            "detail": {k: v for k, v in detail.items() if v is not None},
            **({"error": error} if error else {}),
            "checked": datetime.date.today().isoformat(),
        }

    @staticmethod
    def _status_of(response) -> str:
        code = response.status_code
        if code < 400:
            return "ok"
        if code in (404, 410):
            return "not-found"
        if code < 500 and code != 429:
            return "broken"
        return "unreachable"

    def resolve(self, uri: str, kind: str) -> dict:
        """Resolve one normalised identifier (see identifiers.classify)."""
        key = f"resolve:{kind}:{uri}"
        if (hit := self.cache.get(key)) is not None:
            return {**hit, "cached": True}
        method = {
            "doi": self._doi,
            "orcid": self._orcid,
            "ror": self._ror,
        }.get(kind)
        if method is None:
            method = (
                self._generic
                if uri.startswith(("http://", "https://"))
                else self._unsupported
            )
        result = method(uri, kind)
        if result["status"] in CACHED_STATUSES:
            self.cache.put(key, result)
        return {**result, "cached": False}

    def resolve_many(self, items) -> dict[str, dict]:
        """Resolve (uri, kind) pairs, in parallel, each distinct uri once.
        -> {uri: result}."""
        unique = list(dict.fromkeys(items))
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            results = pool.map(lambda uk: self.resolve(*uk), unique)
        return {u: r for (u, _), r in zip(unique, results, strict=True)}

    # -- schemes -------------------------------------------------------------

    def _unsupported(self, uri, kind):
        return self._result(uri, kind, "unsupported")

    def _generic(self, uri, kind):
        r, err = self.request(uri, read=False)
        if r is None:
            return self._result(uri, kind, "unreachable", error=err)
        return self._result(
            uri,
            kind,
            self._status_of(r),
            r,
            content_type=r.headers.get("content-type", "").split(";")[0],
        )

    def _doi(self, uri, kind):
        doi = uri.split("doi.org/", 1)[1]
        path = quote(doi, safe="/:;()-._~")
        r, err = self.request(
            f"https://doi.org/api/handles/{path}", "application/json"
        )
        if r is None:
            return self._result(uri, kind, "unreachable", error=err)
        try:
            code = r.json().get("responseCode")
        except ValueError:
            code = None
        if code == 100 or r.status_code == 404:
            return self._result(uri, kind, "not-found", r)
        if code != 1:
            return self._result(
                uri,
                kind,
                self._status_of(r) if r.status_code >= 400 else "broken",
                r,
            )
        target = next(
            (
                v["data"]["value"]
                for v in r.json().get("values", [])
                if v.get("type") == "URL"
            ),
            None,
        )

        ra = rtype = state = None
        r2, _ = self.request(f"https://doi.org/ra/{path}", "application/json")
        if r2 is not None and r2.status_code == 200:
            try:
                ra = r2.json()[0].get("RA")
            except (ValueError, IndexError, AttributeError):
                ra = None
        if ra == "DataCite":
            r3, _ = self.request(
                f"https://api.datacite.org/dois/{path}", "application/json"
            )
            if r3 is not None and r3.status_code == 200:
                attrs = r3.json().get("data", {}).get("attributes", {})
                rtype = (attrs.get("types") or {}).get("resourceTypeGeneral")
                state = attrs.get("state")
        elif ra == "Crossref":
            r3, _ = self.request(
                f"https://api.crossref.org/works/{path}", "application/json"
            )
            if r3 is not None and r3.status_code == 200:
                rtype = r3.json().get("message", {}).get("type")
        return self._result(
            uri,
            kind,
            "ok",
            r,
            target=target,
            registration_agency=ra,
            resource_type=rtype,
            state=state,
        )

    def _orcid(self, uri, kind):
        orcid = uri.rsplit("/", 1)[1]
        headers = {}
        if token := self._orcid_bearer():
            headers["Authorization"] = f"Bearer {token}"
        r, err = self.request(
            f"https://pub.orcid.org/v3.0/{orcid}/person",
            "application/json",
            headers,
        )
        if r is None:
            return self._result(uri, kind, "unreachable", error=err)
        status = self._status_of(r)
        name = None
        if status == "ok":
            n = r.json().get("name") or {}
            name = (
                " ".join(
                    (n.get(k) or {}).get("value", "")
                    for k in ("given-names", "family-name")
                ).strip()
                or None
            )
        return self._result(uri, kind, status, r, name=name)

    def _orcid_bearer(self) -> str | None:
        """A /read-public token: ORCID_ACCESS_TOKEN, or obtained once per
        process from ORCID_CLIENT_ID and ORCID_CLIENT_SECRET. Anonymous
        access is used when neither is configured or the exchange fails."""
        if self._orcid_token is not None:
            return self._orcid_token or None
        token = config.setting("ORCID_ACCESS_TOKEN") or ""
        cid = config.setting("ORCID_CLIENT_ID")
        secret = config.setting("ORCID_CLIENT_SECRET")
        if not token and cid and secret:
            try:
                r = self.client.post(
                    ORCID_TOKEN_URL,
                    headers={"Accept": "application/json"},
                    data={
                        "client_id": cid,
                        "client_secret": secret,
                        "grant_type": "client_credentials",
                        "scope": "/read-public",
                    },
                )
                if r.status_code == 200:
                    token = r.json().get("access_token", "")
            except (httpx.HTTPError, ValueError):
                token = ""
        self._orcid_token = token
        return token or None

    def _ror(self, uri, kind):
        rid = uri.rsplit("/", 1)[1]
        headers = {}
        if client_id := config.setting("ROR_CLIENT_ID"):
            headers["Client-Id"] = client_id
        r, err = self.request(
            f"https://api.ror.org/v2/organizations/{rid}",
            "application/json",
            headers,
        )
        if r is None:
            return self._result(uri, kind, "unreachable", error=err)
        status = self._status_of(r)
        if status != "ok":
            return self._result(uri, kind, status, r)
        d = r.json()
        name = next(
            (
                n["value"]
                for n in d.get("names", [])
                if "ror_display" in n.get("types", [])
            ),
            None,
        )
        successors = [
            rel["id"]
            for rel in d.get("relationships", [])
            if rel.get("type") == "successor"
        ]
        return self._result(
            uri,
            kind,
            "ok",
            r,
            name=name,
            organisation_status=d.get("status"),
            successors=successors or None,
        )
