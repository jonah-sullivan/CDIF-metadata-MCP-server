"""Find, classify and check (offline) the identifiers in a CDIF record.

Works on CDIF JSON-LD with prefixed property names (schema:name), as
produced by the ISO crosswalk or by the MCP server's _to_cdif_form.

Every identifier is collected with its role in the record (the dataset, a
person, a keyword term...), classified by scheme (DOI, ORCID, ROR...), and
normalised to its canonical URI where the scheme has one. Offline checks
cover syntax and check digits, and how identifiers are used: a dataset
with no persistent identifier, a party identified by an ordinary URL, one
identifier shared by differently named parties. Whether identifiers
resolve is checked elsewhere (pidcheck.resolve).
"""

import re
from collections import defaultdict
from dataclasses import asdict, dataclass

# -- classification ---------------------------------------------------------

# Kinds that identify persistently: a dataset with one of these has a PID.
PERSISTENT_KINDS = {
    "doi",
    "handle",
    "ark",
    "igsn",
    "raid",
    "persistent-uri",
}
# Hosts whose URIs are managed as persistent (redirect services and
# registries), as opposed to ordinary web addresses.
PERSISTENT_HOSTS = {
    "w3id.org",
    "purl.org",
    "purl.oclc.org",
    "purl.obolibrary.org",
    "linked.data.gov.au",
    "pid.geoscience.gov.au",
    "identifiers.org",
    "n2t.net",
}

_DOI = re.compile(
    r"^(?:doi:\s*|https?://(?:dx\.)?doi\.org/)?(10\.\d{4,9}/\S+)$",
    re.IGNORECASE,
)
_ORCID = re.compile(
    r"^(?:https?://(?:www\.)?orcid\.org/)?(\d{4}-\d{4}-\d{4}-\d{3}[\dX])$",
    re.IGNORECASE,
)
_ROR = re.compile(r"^(?:https?://ror\.org/)?(0[0-9a-z]{6}\d{2})$")
_ISNI = re.compile(
    r"^(?:https?://(?:www\.)?isni\.org/isni/|isni:?\s*)?"
    r"(\d{4}\s?\d{4}\s?\d{4}\s?\d{3}[\dX])$",
    re.IGNORECASE,
)
_RAID = re.compile(r"^https?://raid\.org/(10\.\d{4,9}/\S+)$", re.IGNORECASE)
_IGSN = re.compile(
    r"^(?:igsn:\s*|https?://(?:igsn\.org/|hdl\.handle\.net/10273/))(\S+)$",
    re.IGNORECASE,
)
_HANDLE = re.compile(
    r"^(?:hdl:\s*|https?://hdl\.handle\.net/)(\d[\d.]*/\S+)$", re.IGNORECASE
)
_ARK = re.compile(r"^(?:https?://n2t\.net/)?(ark:/?\d{5,}/\S+)$")
_URI = re.compile(r"^[a-z][a-z0-9+.\-]*:\S+$", re.IGNORECASE)
_CROCKFORD = "0123456789abcdefghjkmnpqrstvwxyz"


def _scheme_hint(hint: str | None) -> str:
    """'https://orcid.org', 'ORCID', 'doi'... -> 'orcid', 'doi'... or ''."""
    h = (hint or "").lower()
    for key in ("orcid", "ror", "isni", "doi", "raid", "igsn"):
        if re.search(rf"(^|[/.:]){key}([/.:]|$)", h) or h == key:
            return key
    return ""


def classify(value: str, hint: str | None = None) -> tuple[str, str]:
    """-> (kind, normalised). hint is a scheme name or URI (e.g. the
    propertyID of a schema:PropertyValue) that lets bare codes such as
    '01ej9dk98' be recognised."""
    v = value.strip()
    scheme = _scheme_hint(hint)
    if (hint or "").strip().lower() == "urn:uuid" and re.fullmatch(
        r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", v, re.IGNORECASE
    ):
        return "urn", f"urn:uuid:{v.lower()}"  # code space urn:uuid
    if m := _RAID.match(v):
        return "raid", f"https://raid.org/{m.group(1)}"
    if (m := _DOI.match(v)) and (not v[:3].isdigit() or scheme in ("doi", "")):
        return "doi", f"https://doi.org/{m.group(1)}"
    if (m := _ORCID.match(v)) and (
        v.startswith("http") or scheme in ("orcid", "")
    ):
        return "orcid", f"https://orcid.org/{m.group(1).upper()}"
    if (m := _ROR.match(v)) and (v.startswith("http") or scheme == "ror"):
        return "ror", f"https://ror.org/{m.group(1)}"
    if (m := _ISNI.match(v)) and (
        v.lower().startswith(("http", "isni")) or scheme == "isni"
    ):
        digits = re.sub(r"\s", "", m.group(1)).upper()
        return "isni", f"https://isni.org/isni/{digits}"
    if m := _IGSN.match(v):
        return "igsn", v
    if m := _HANDLE.match(v):
        return "handle", f"https://hdl.handle.net/{m.group(1)}"
    if m := _ARK.match(v):
        return "ark", f"https://n2t.net/{m.group(1)}"
    if re.match(r"https?://", v, re.IGNORECASE):
        host = re.sub(r"^https?://([^/:]+).*", r"\1", v).lower()
        if host in PERSISTENT_HOSTS:
            return "persistent-uri", v
        return "uri", v
    if v.lower().startswith("urn:"):
        return "urn", v
    if _URI.match(v) and not re.match(r"^\d", v):
        return "uri", v  # other schemes, e.g. mailto:
    return "not-uri", v


# -- check digits ------------------------------------------------------------


def mod11_2(base: str) -> str:
    """ISO 7064 Mod 11-2 check character (ORCID, ISNI)."""
    total = 0
    for d in base:
        total = (total + int(d)) * 2
    r = (12 - total % 11) % 11
    return "X" if r == 10 else str(r)


def orcid_ok(orcid_uri: str) -> bool:
    digits = orcid_uri.rsplit("/", 1)[-1].replace("-", "")
    return mod11_2(digits[:-1]) == digits[-1].upper()


def isni_ok(isni_uri: str) -> bool:
    digits = isni_uri.rsplit("/", 1)[-1]
    return mod11_2(digits[:-1]) == digits[-1].upper()


def ror_ok(ror_uri: str) -> bool:
    """ROR: Crockford base32 number, ISO 7064 Mod 97-10 check digits."""
    rid = ror_uri.rsplit("/", 1)[-1]
    n = 0
    for ch in rid[:7]:
        if ch not in _CROCKFORD:
            return False
        n = n * 32 + _CROCKFORD.index(ch)
    return f"{98 - (n * 100) % 97:02d}" == rid[7:]


# -- extraction --------------------------------------------------------------


@dataclass(frozen=True)
class Identifier:
    value: str  # as written in the record
    role: str  # see ROLES
    path: str  # where in the record, e.g. schema:creator/@list[0]
    kind: str  # see classify()
    normalised: str  # canonical URI where the scheme has one
    label: str | None = None  # the name of the thing identified


ROLES = {
    "dataset": "identifier of the described dataset",
    "alternate": "alternate identifier of the dataset (schema:sameAs)",
    "person": "identifier of a person",
    "organisation": "identifier of an organisation",
    "licence": "licence",
    "keyword": "vocabulary term used as a keyword",
    "defined-term": "other vocabulary term (e.g. a role or service type)",
    "term-set": "vocabulary a term belongs to (schema:inDefinedTermSet)",
    "variable": "measured variable (schema:propertyID)",
    "unit": "unit of measure (schema:unitCode)",
}
_PERSON = {"schema:Person"}
_ORG = {
    "schema:Organization",
    "schema:FundingAgency",
    "schema:Consortium",
    "schema:Corporation",
    "schema:EducationalOrganization",
    "schema:GovernmentOrganization",
    "schema:NGO",
    "schema:ResearchOrganization",
}


def _list(x) -> list:
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def _types(node: dict) -> set[str]:
    return set(_list(node.get("@type")))


def _values(x, path: str):
    """Identifier values in a string, {@id}, PropertyValue or a list of
    these -> [(value, hint, path)]. hint is a PropertyValue's propertyID."""
    out = []
    for i, item in enumerate(_list(x)):
        p = f"{path}[{i}]" if isinstance(x, list) else path
        if isinstance(item, str):
            out.append((item, None, p))
        elif isinstance(item, dict):
            hint = item.get("schema:propertyID")
            hint = hint.get("@id") if isinstance(hint, dict) else hint
            if url := item.get("schema:url"):
                out.append((url, hint, f"{p}/schema:url"))
            elif (val := item.get("schema:value")) is not None:
                out.append((str(val), hint, f"{p}/schema:value"))
            elif "@id" in item:
                out.append((item["@id"], None, f"{p}/@id"))
    return out


def extract(record: dict) -> list[Identifier]:
    """Every identifier and vocabulary term in a CDIF record."""
    found: list[Identifier] = []

    def add(value, role, path, hint=None, label=None):
        if not isinstance(value, str) or not value.strip():
            return
        kind, norm = classify(value, hint)
        found.append(Identifier(value, role, path, kind, norm, label))

    if "@id" in record:
        add(record["@id"], "dataset", "@id")
    for v, h, p in _values(
        record.get("schema:identifier"), "schema:identifier"
    ):
        add(v, "dataset", p, h)
    for v, h, p in _values(record.get("schema:sameAs"), "schema:sameAs"):
        add(v, "alternate", p, h)
    for v, h, p in _values(record.get("schema:license"), "schema:license"):
        add(v, "licence", p, h)

    def walk(node, path: str, in_keywords: bool):
        if isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{path}[{i}]", in_keywords)
            return
        if not isinstance(node, dict):
            return
        types = _types(node)
        name = node.get("schema:name")
        name = name if isinstance(name, str) else None
        if types & (_PERSON | _ORG):
            role = "person" if types & _PERSON else "organisation"
            if "@id" in node:
                add(node["@id"], role, f"{path}/@id", label=name)
            for key in ("schema:identifier", "schema:sameAs"):
                for v, h, p in _values(node.get(key), f"{path}/{key}"):
                    add(v, role, p, h, name)
        if "schema:DefinedTerm" in types:
            role = "keyword" if in_keywords else "defined-term"
            for v, h, p in _values(
                node.get("schema:identifier"), f"{path}/schema:identifier"
            ):
                add(v, role, p, h, name)
            for v, h, p in _values(
                node.get("schema:inDefinedTermSet"),
                f"{path}/schema:inDefinedTermSet",
            ):
                add(v, "term-set", p, h)
        if path.startswith("schema:variableMeasured"):
            prop = node.get("schema:propertyID")
            for v, h, p in _values(prop, f"{path}/schema:propertyID"):
                add(v, "variable", p, h, name)
            for v, h, p in _values(
                node.get("schema:unitCode"), f"{path}/schema:unitCode"
            ):
                add(v, "unit", p, h, node.get("schema:unitText"))
        for key, child in node.items():
            if key in ("@context", "schema:identifier", "schema:sameAs"):
                continue
            walk(
                child,
                f"{path}/{key}" if path else key,
                in_keywords or key == "schema:keywords",
            )

    for key, child in record.items():
        if key not in (
            "@context",
            "@id",
            "schema:identifier",
            "schema:sameAs",
            "schema:license",
        ):
            walk(child, key, key == "schema:keywords")
    return found


# -- offline checks ----------------------------------------------------------


def _finding(severity, code, message, ident: Identifier) -> dict:
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "value": ident.value,
        "role": ident.role,
        "path": ident.path,
    }


def _agents(record: dict) -> list[dict]:
    """Distinct people and organisations: type, name, identifiers."""
    agents: dict[tuple, dict] = {}

    def walk(node):
        if isinstance(node, list):
            for x in node:
                walk(x)
        elif isinstance(node, dict):
            types = _types(node)
            if types & (_PERSON | _ORG):
                kind = "person" if types & _PERSON else "organisation"
                ids = [node["@id"]] if "@id" in node else []
                for key in ("schema:identifier", "schema:sameAs"):
                    ids += [v for v, _, _ in _values(node.get(key), "")]
                key = (kind, node.get("schema:name"), tuple(ids))
                agents.setdefault(
                    key,
                    {
                        "kind": kind,
                        "name": node.get("schema:name"),
                        "identifiers": ids,
                    },
                )
            for k, v in node.items():
                if k != "@context":
                    walk(v)

    walk(record)
    return list(agents.values())


def check_offline(record: dict) -> dict:
    """Identifiers in a CDIF record, a summary, and offline findings.

    Findings have severity 'problem' (an identifier is wrong), 'warning'
    (it works poorly for machines or is not persistent) or 'info'.
    """
    ids = extract(record)
    findings: list[dict] = []

    for i in ids:
        if i.kind == "orcid" and not orcid_ok(i.normalised):
            findings.append(
                _finding(
                    "problem",
                    "orcid-check-digit",
                    f"ORCID iD {i.value} has an invalid check digit.",
                    i,
                )
            )
        if i.kind == "isni" and not isni_ok(i.normalised):
            findings.append(
                _finding(
                    "problem",
                    "isni-check-digit",
                    f"ISNI {i.value} has an invalid check digit.",
                    i,
                )
            )
        if i.kind == "ror" and not ror_ok(i.normalised):
            findings.append(
                _finding(
                    "problem",
                    "ror-check-digit",
                    f"ROR ID {i.value} has invalid check digits.",
                    i,
                )
            )
        if i.kind == "not-uri" and i.role != "term-set":
            findings.append(
                _finding(
                    "warning",
                    "not-a-uri",
                    f"'{i.value}' is not a URI, so machines cannot "
                    f"resolve it.",
                    i,
                )
            )
        if i.kind == "not-uri" and i.role == "term-set":
            findings.append(
                _finding(
                    "warning",
                    "term-set-not-a-uri",
                    f"Vocabulary '{i.value}' is named but not identified by a "
                    f"URI.",
                    i,
                )
            )
        # A bare code with its scheme in propertyID ("0000-0002-...",
        # propertyID ORCID) is the recommended PropertyValue form; only a
        # prefixed or URL form that differs from the canonical URI
        # (doi:..., http://dx.doi.org/...) is worth noting.
        if (
            i.kind in ("doi", "orcid", "ror", "isni")
            and ":" in i.value
            and i.value.strip() != i.normalised
        ):
            findings.append(
                _finding(
                    "info",
                    "non-canonical-form",
                    f"{i.kind.upper()} written as '{i.value}'; the canonical "
                    f"form is {i.normalised}.",
                    i,
                )
            )
        if i.role in ("person", "organisation") and i.kind == "uri":
            findings.append(
                _finding(
                    "warning",
                    "party-local-url",
                    f"{i.label or 'A ' + i.role} is identified by "
                    f"{i.value}, an ordinary URL rather than a persistent "
                    f"identifier "
                    f"(ORCID, ROR...).",
                    i,
                )
            )

    # The dataset should have at least one persistent identifier.
    dataset_ids = [i for i in ids if i.role in ("dataset", "alternate")]
    if dataset_ids and not any(
        i.kind in PERSISTENT_KINDS for i in dataset_ids
    ):
        first = dataset_ids[0]
        findings.append(
            _finding(
                "warning",
                "dataset-no-pid",
                "The dataset has no persistent identifier (DOI, handle, ARK, "
                "IGSN, RAiD or persistent URI); its identifiers are "
                + ", ".join(sorted({i.value for i in dataset_ids}))
                + ".",
                first,
            )
        )

    # One identifier for differently named parties identifies none of them.
    names = defaultdict(set)
    first_use: dict[str, Identifier] = {}
    for i in ids:
        if i.role in ("person", "organisation") and i.label:
            names[i.normalised].add(i.label)
            first_use.setdefault(i.normalised, i)
    for norm, labels in names.items():
        if len(labels) > 1:
            findings.append(
                _finding(
                    "problem",
                    "shared-party-identifier",
                    f"{norm} identifies {len(labels)} differently named "
                    f"parties ({', '.join(sorted(labels)[:4])}"
                    f"{'...' if len(labels) > 4 else ''}).",
                    first_use[norm],
                )
            )

    # Report each problem once per identifier.
    unique = list({(f["code"], f["value"]): f for f in findings}.values())
    return {
        "summary": summarise(record, ids),
        "findings": unique,
        "identifiers": [asdict(i) for i in ids],
    }


def summarise(record: dict, ids: list[Identifier]) -> dict:
    agents = _agents(record)
    people = [a for a in agents if a["kind"] == "person"]
    orgs = [a for a in agents if a["kind"] == "organisation"]

    def has(agent, kind):
        return any(classify(v)[0] == kind for v in agent["identifiers"])

    keywords = [k for k in _list(record.get("schema:keywords"))]
    keyword_uris = sum(
        1
        for k in keywords
        if isinstance(k, dict)
        and any(
            classify(v, h)[0] not in ("not-uri",)
            for v, h, _ in _values(k.get("schema:identifier"), "")
        )
    )
    variables = _list(record.get("schema:variableMeasured"))
    dataset = [i for i in ids if i.role in ("dataset", "alternate")]
    return {
        "dataset_identifiers": sorted({i.normalised for i in dataset}),
        "dataset_has_pid": any(i.kind in PERSISTENT_KINDS for i in dataset),
        "dataset_doi": next(
            (i.normalised for i in dataset if i.kind == "doi"), None
        ),
        "people": len(people),
        "people_with_orcid": sum(has(p, "orcid") for p in people),
        "organisations": len(orgs),
        "organisations_with_ror": sum(has(o, "ror") for o in orgs),
        "keywords": len(keywords),
        "keywords_with_uri": keyword_uris,
        "variables": len(variables),
        "variables_with_uri": sum(
            1 for i in ids if i.role == "variable" and i.kind != "not-uri"
        ),
        "licences": len([i for i in ids if i.role == "licence"]),
    }
