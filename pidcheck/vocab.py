"""Check the vocabulary terms in a CDIF record against their vocabularies.

For each term URI (keywords, variables, units, other defined terms) this
fetches the term's RDF by content negotiation and checks that:

- it is described as a term with a label (a SKOS concept, a QUDT unit...);
- the record's name for it matches one of its labels;
- it is not deprecated;
- it belongs to the vocabulary the record names (informational: NVS, for
  one, groups terms in collections rather than schemes).

It also reports which vocabularies are hosted in Research Vocabularies
Australia (RVA), and whether the version a term resolves to is current.
RVA has no lookup by URI, so only hosting can be shown definitively (the
term resolves into RVA's linked-data API); a vocabulary served elsewhere
is reported as "not hosted in RVA", not "not registered".
"""

import datetime
import re
from concurrent.futures import ThreadPoolExecutor

import rdflib
from rdflib.namespace import OWL, RDF, RDFS, SKOS

from .identifiers import Identifier
from .resolve import WORKERS, Resolver

RDF_ACCEPT = (
    "text/turtle, application/rdf+xml;q=0.9, application/ld+json;q=0.8, "
    "application/xml;q=0.5, */*;q=0.1"
)
FORMATS = {
    "text/turtle": "turtle",
    "application/x-turtle": "turtle",
    "application/rdf+xml": "xml",
    "application/ld+json": "json-ld",
    "application/n-triples": "nt",
    "text/n3": "n3",
    # Generic XML: GCMD serves RDF/XML this way.
    "application/xml": "xml",
    "text/xml": "xml",
}
TERM_ROLES = ("keyword", "defined-term", "variable", "unit")
RVA_REGISTRY = "https://vocabs.ardc.edu.au/registry/api/resource/vocabularies"
_RVA_LDA = re.compile(
    r"^https?://vocabs\.ardc\.edu\.au/repository/api/lda/"
    r"(?P<path>[^?]+?)/resource(?:\.\w+)?(?:\?|$)"
)


# -- dereferencing ----------------------------------------------------------


def _variants(uri: str) -> list[str]:
    """The URI with and without a trailing slash, as http and https."""
    base = uri.rstrip("/")
    out = []
    for u in (uri, base, base + "/"):
        for scheme in ("http://", "https://"):
            out.append(re.sub(r"^https?://", scheme, u))
    return list(dict.fromkeys(out))


def _rva_path(urls: list[str]) -> str | None:
    for u in urls:
        if m := _RVA_LDA.match(u):
            return m.group("path")
    return None


def dereference(resolver: Resolver, uri: str) -> dict:
    """Fetch a term's RDF and describe it.

    status: ok | no-label | not-described (RDF without this URI) |
    not-rdf | not-found | broken | unreachable.
    """
    key = f"concept:{uri}"
    if (hit := resolver.cache.get(key)) is not None:
        return {**hit, "cached": True}
    result = _dereference(resolver, uri)
    if result["status"] != "unreachable":
        resolver.cache.put(key, result)
    return {**result, "cached": False}


def _dereference(resolver: Resolver, uri: str) -> dict:
    today = datetime.date.today().isoformat()
    base = {"uri": uri, "checked": today}
    r, err = resolver.request(uri, RDF_ACCEPT)
    if r is None:
        return {**base, "status": "unreachable", "error": err}
    status = resolver._status_of(r)
    urls = [str(h.url) for h in r.history] + [str(r.url)]
    rva = _rva_path(urls)
    ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
    # RVA's linked-data API redirects some terms to resource.html, which
    # is HTML whatever Accept says; ask for Turtle explicitly.
    if status == "ok" and rva and "html" in ctype:
        ttl = re.sub(r"/resource\.html\?", "/resource?", str(r.url))
        r2, err2 = resolver.request(f"{ttl}&_format=ttl", RDF_ACCEPT)
        if r2 is None:
            return {
                **base,
                "status": "unreachable",
                "error": err2,
                "rva_path": rva,
            }
        r = r2
        status = resolver._status_of(r)
        ctype = r.headers.get("content-type", "").split(";")[0].lower()
    info = {
        **base,
        "final_url": str(r.url),
        "content_type": ctype,
        "http_status": r.status_code,
        "rva_path": rva,
    }
    if status != "ok":
        return {**info, "status": status}
    fmt = FORMATS.get(ctype) or ("xml" if "xml" in ctype else None)
    if fmt is None:
        return {**info, "status": "not-rdf"}
    try:
        g = rdflib.Graph().parse(
            data=r.content, format=fmt, publicID=str(r.url)
        )
    # Deliberately broad: rdflib raises many types on bad input, and any
    # of them means the response is not usable RDF.
    except Exception as e:  # noqa: BLE001
        return {
            **info,
            "status": "not-rdf",
            "error": f"{type(e).__name__}: {e}"[:200],
        }
    subject = next(
        (
            rdflib.URIRef(v)
            for v in _variants(uri)
            if (rdflib.URIRef(v), None, None) in g
        ),
        None,
    )
    if subject is None:
        return {**info, "status": "not-described"}
    labels = list(
        dict.fromkeys(
            str(o).strip()
            for p in (SKOS.prefLabel, SKOS.altLabel, RDFS.label)
            for o in g.objects(subject, p)
            if str(o).strip()  # NVS has empty altLabels
        )
    )
    types = [str(t) for t in g.objects(subject, RDF.type)]
    deprecated = any(
        str(o).lower() == "true" for o in g.objects(subject, OWL.deprecated)
    )
    return {
        **info,
        "status": "ok" if labels else "no-label",
        "labels": labels[:10],
        "types": types[:5],
        "concept": str(SKOS.Concept) in types,
        "schemes": [str(o) for o in g.objects(subject, SKOS.inScheme)][:5],
        "deprecated": deprecated,
    }


# -- RVA --------------------------------------------------------------------


def _rva_index(resolver: Resolver) -> dict | None:
    """RVA's vocabularies by slug, cached. None if RVA is unreachable."""
    if (hit := resolver.cache.get("rva:index")) is not None:
        return hit
    r, _ = resolver.request(RVA_REGISTRY, "application/json")
    if r is None or r.status_code != 200:
        return None
    index = {
        v["slug"]: {
            "id": v["id"],
            "title": v.get("title"),
            "owner": v.get("owner"),
        }
        for v in r.json().get("vocabulary", [])
        if v.get("slug")
    }
    resolver.cache.put("rva:index", index)
    return index


def _rva_versions(resolver: Resolver, vid: int) -> list[dict] | None:
    key = f"rva:vocabulary:{vid}"
    if (hit := resolver.cache.get(key)) is not None:
        return hit
    r, _ = resolver.request(
        f"{RVA_REGISTRY}/{vid}?includeVersions=true&includeAccessPoints=true",
        "application/json",
    )
    if r is None or r.status_code != 200:
        return None
    versions = []
    for v in r.json().get("version", []):
        prefixes = [
            ap["ap-sissvoc"]["url-prefix"]
            for ap in v.get("access-point", [])
            if ap.get("discriminator") == "sissvoc"
        ]
        versions.append(
            {
                "slug": v.get("slug"),
                "status": v.get("status"),
                "lda": [p.split("/lda/", 1)[-1] for p in prefixes],
            }
        )
    resolver.cache.put(key, versions)
    return versions


def rva_vocabulary(resolver: Resolver, path: str) -> dict:
    """Describe the RVA vocabulary behind a linked-data API path such as
    'aodn/aodn-discovery-parameter-vocabulary/version-1-7'."""
    out = {"hosted": True, "path": path}
    index = _rva_index(resolver)
    if index is None:
        return out
    slug = next((s for s in path.split("/") if s in index), None)
    if slug is None:
        return out
    vocab = index[slug]
    out.update(vocabulary=slug, title=vocab["title"], owner=vocab["owner"])
    versions = _rva_versions(resolver, vocab["id"]) or []
    match = max(
        (
            v
            for v in versions
            for p in v["lda"]
            if path == p or path.startswith(p + "/")
        ),
        key=lambda v: len(v["lda"][0]) if v["lda"] else 0,
        default=None,
    )
    if match:
        out.update(version=match["slug"], version_status=match["status"])
    return out


# -- checking a record's terms ------------------------------------------------


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("_", " ")).strip(" .").casefold()


def label_matches(name: str, labels: list[str]) -> bool:
    """Case- and space-insensitive; for hierarchical keywords such as
    'EARTH SCIENCE > OCEANS > OCEAN TEMPERATURE' the last part counts."""
    candidates = {_norm(name)}
    for sep in (">", "|"):
        if sep in name:
            candidates.add(_norm(name.rsplit(sep, 1)[-1]))
    return bool(candidates & {_norm(label) for label in labels})


def _same_scheme(a: str, b: str) -> bool:
    def key(u):
        return re.sub(r"^https?://", "", u).rstrip("/").casefold()

    def last(u):
        return key(u).rsplit("/", 1)[-1]

    return key(a) == key(b) or last(a) == last(b)


def _finding(
    severity, code, message, ident: Identifier, group=None, many=None
) -> dict:
    """A finding. Findings with the same code and group are merged into
    one, using the template many ({n} and {examples} are filled in)."""
    f = {
        "severity": severity,
        "code": code,
        "message": message,
        "value": ident.value,
        "role": ident.role,
        "path": ident.path,
    }
    if group is not None:
        f["_group"], f["_many"] = group, many
        f["_label"] = ident.label or ident.normalised
    return f


def _aggregate(findings: list[dict]) -> list[dict]:
    """Report each finding once per identifier, and merge grouped findings
    (e.g. 15 terms from one host that are not machine-readable) into one
    entry with a count and examples."""
    unique = list({(f["code"], f["value"]): f for f in findings}.values())
    out, groups = [], {}
    for f in unique:
        group = f.pop("_group", None)
        many, label = f.pop("_many", None), f.pop("_label", None)
        if group is None:
            out.append(f)
            continue
        key = (f["code"], group)
        if key not in groups:
            groups[key] = {"finding": f, "many": many, "labels": []}
            out.append(f)
        groups[key]["labels"].append(label)
    for g in groups.values():
        labels = g["labels"]
        if len(labels) > 1:
            examples = ", ".join(f"'{x}'" for x in labels[:3])
            g["finding"].update(
                message=g["many"].format(n=len(labels), examples=examples),
                count=len(labels),
                examples=labels[:3],
            )
    return out


def _host(uri: str) -> str:
    return re.sub(r"^https?://([^/]+).*", r"\1", uri)


def check_vocabulary(
    identifiers: list[Identifier], resolver: Resolver
) -> dict:
    """Dereference every term URI in identifiers (from extract()) and the
    vocabularies they name, and check them.
    -> {"summary", "findings", "terms", "vocabularies"}."""
    terms = [
        i
        for i in identifiers
        if i.role in TERM_ROLES
        and i.normalised.startswith(("http://", "https://"))
    ]
    term_sets = {
        i.path.rsplit("/", 1)[0]: i
        for i in identifiers
        if i.role == "term-set"
    }
    set_uris = list(
        dict.fromkeys(
            i.normalised
            for i in term_sets.values()
            if i.normalised.startswith(("http://", "https://"))
        )
    )
    uris = list(dict.fromkeys(i.normalised for i in terms))
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        fetched = list(
            pool.map(lambda u: dereference(resolver, u), uris + set_uris)
        )
    described = dict(zip(uris, fetched[: len(uris)], strict=True))
    vocabularies = dict(zip(set_uris, fetched[len(uris) :], strict=True))

    findings: list[dict] = []
    counts = dict.fromkeys(
        (
            "ok",
            "label_matches",
            "label_mismatches",
            "deprecated",
            "not_machine_readable",
            "not_found",
            "unreachable",
        ),
        0,
    )
    seen: set[str] = set()
    for i in terms:
        d = described[i.normalised]
        first = i.normalised not in seen
        seen.add(i.normalised)
        name = i.label or i.normalised
        st = d["status"]
        if st in ("not-found", "broken"):
            counts["not_found"] += first
            findings.append(
                _finding(
                    "problem",
                    "term-not-found",
                    f"Term '{name}' ({i.normalised}) does not resolve "
                    f"(HTTP {d.get('http_status')}).",
                    i,
                )
            )
        elif st == "unreachable":
            counts["unreachable"] += first
            host = _host(i.normalised)
            findings.append(
                _finding(
                    "info",
                    "term-unchecked",
                    f"Could not check '{name}': {host} did not respond.",
                    i,
                    group=host,
                    many=f"Could not check {{n}} terms: {host} did not "
                    f"respond (e.g. {{examples}}).",
                )
            )
        elif st in ("not-rdf", "not-described", "no-label"):
            counts["not_machine_readable"] += first
            ctype = d.get("content_type") or "no content"
            one = {
                "not-rdf": f"asked for RDF, its URI returns {ctype}",
                "not-described": "its URI returns RDF that does not "
                "describe it",
                "no-label": "it is described without a label",
            }[st]
            many = {
                "not-rdf": f"return {ctype} when asked for RDF",
                "not-described": "return RDF that does not describe them",
                "no-label": "are described without a label",
            }[st]
            host = _host(i.normalised)
            findings.append(
                _finding(
                    "warning",
                    "term-not-machine-readable",
                    f"Term '{name}' ({i.normalised}) has no machine-readable "
                    f"description: {one}.",
                    i,
                    group=(host, st),
                    many=f"{{n}} terms from {host} {many} "
                    f"(e.g. {{examples}}).",
                )
            )
        else:
            counts["ok"] += first
            if i.label:
                if label_matches(i.label, d["labels"]):
                    counts["label_matches"] += first
                else:
                    counts["label_mismatches"] += first
                    findings.append(
                        _finding(
                            "warning",
                            "label-mismatch",
                            f"The record calls {i.normalised} '{i.label}'; "
                            f"its vocabulary calls it "
                            f"'{' / '.join(d['labels'][:2])}'.",
                            i,
                        )
                    )
            if d.get("deprecated"):
                counts["deprecated"] += first
                findings.append(
                    _finding(
                        "warning",
                        "deprecated-term",
                        f"Term '{name}' ({i.normalised}) is deprecated in "
                        f"its vocabulary.",
                        i,
                    )
                )
            ts = term_sets.get(i.path.rsplit("/", 1)[0])
            if (
                ts
                and d.get("schemes")
                and ts.normalised.startswith("http")
                and not any(
                    _same_scheme(ts.normalised, s) for s in d["schemes"]
                )
            ):
                schemes = ", ".join(d["schemes"][:2])
                findings.append(
                    _finding(
                        "info",
                        "scheme-mismatch",
                        f"The record puts '{name}' in {ts.normalised}; its "
                        f"vocabulary puts it in {schemes}.",
                        i,
                        group=ts.normalised,
                        many=f"{{n}} terms are placed in {ts.normalised}, but "
                        f"their vocabularies put them elsewhere (e.g. "
                        f"{{examples}}).",
                    )
                )

    # Vocabularies named by the record: dead links, and web pages with no
    # machine-readable description.
    for ts in dict.fromkeys(term_sets.values()):
        v = vocabularies.get(ts.normalised)
        if v is None:
            continue
        if v["status"] in ("not-found", "broken"):
            findings.append(
                _finding(
                    "warning",
                    "term-set-not-found",
                    f"Vocabulary URI {ts.normalised} does not resolve "
                    f"(HTTP {v.get('http_status')}).",
                    ts,
                )
            )
        elif v["status"] == "not-rdf" and "html" in v.get("content_type", ""):
            findings.append(
                _finding(
                    "info",
                    "term-set-web-page",
                    f"Vocabulary {ts.normalised} is a web page: asked for "
                    f"RDF, it returns {v['content_type']}.",
                    ts,
                )
            )

    # RVA: which vocabularies are hosted there, and whether the version
    # terms resolve to is current.
    rva: dict[str, dict] = {}
    for i in terms:
        path = described[i.normalised].get("rva_path")
        if path and path not in rva:
            rva[path] = rva_vocabulary(resolver, path)
    for v in rva.values():
        if v.get("version_status") not in (None, "current"):
            ident = next(
                i
                for i in terms
                if described[i.normalised].get("rva_path") == v["path"]
            )
            findings.append(
                _finding(
                    "warning",
                    "superseded-vocabulary-version",
                    f"Terms resolve to version {v.get('version')} of RVA "
                    f"vocabulary '{v.get('title')}', which is "
                    f"{v['version_status']}.",
                    ident,
                )
            )

    return {
        "summary": {
            "terms": len(uris),
            **counts,
            "vocabularies": len(set_uris),
            "rva_vocabularies": len(rva),
        },
        "findings": _aggregate(findings),
        "terms": described,
        "vocabularies": {"named": vocabularies, "rva": list(rva.values())},
    }
