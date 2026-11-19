"""Check every identifier and vocabulary term in a CDIF record.

check_record() combines the offline checks (pidcheck.identifiers), the
resolution of dataset, party and licence identifiers (pidcheck.resolve)
and the vocabulary checks (pidcheck.vocab) into one compact report:

    {"summary": {...},
     "problems": [...], "warnings": [...], "notes": [...]}

Each list holds {"code", "message"} entries (grouped findings add
"count" and "examples"), is capped at max_findings, and reports how many
were left out. Results that are simply fine are counted in the summary,
not listed.
"""

import re
import threading

from .identifiers import Identifier, check_offline, extract
from .resolve import Resolver
from .vocab import _aggregate, _finding, check_vocabulary

# Roles whose identifiers are resolved here (terms and term sets are
# dereferenced by pidcheck.vocab instead).
RESOLVE_ROLES = ("dataset", "alternate", "person", "organisation", "licence")
# DOI resource types that identify publications rather than data: Crossref
# registers publications, and these are DataCite's publication types.
PUBLICATION_TYPES = {
    "text",
    "journalarticle",
    "book",
    "bookchapter",
    "conferencepaper",
    "conferenceproceeding",
    "dissertation",
    "journal",
    "preprint",
    "report",
    "peerreview",
    "journal-article",
}
SEVERITIES = (
    ("problem", "problems"),
    ("warning", "warnings"),
    ("info", "notes"),
)

_default: Resolver | None = None
_default_lock = threading.Lock()


def default_resolver() -> Resolver:
    """One Resolver per process, sharing its HTTP connections and cache."""
    global _default
    with _default_lock:
        if _default is None:
            _default = Resolver()
        return _default


def _resolvable(i: Identifier) -> bool:
    return i.role in RESOLVE_ROLES and (
        i.kind in ("doi", "orcid", "ror")
        or i.normalised.startswith(("http://", "https://"))
    )


def _family_name_matches(record_name: str, orcid_name: str) -> bool:
    """Loose: the ORCID family name appears in the record's name."""
    family = orcid_name.split()[-1].casefold() if orcid_name.split() else ""
    words = re.findall(r"\w+", record_name.casefold())
    return not family or family in words


def resolution_findings(
    ids: list[Identifier], results: dict[str, dict]
) -> list[dict]:
    findings = []
    for i in ids:
        res = results.get(i.normalised)
        if res is None:
            continue
        name = i.label or f"The {i.role}"
        st, detail = res["status"], res.get("detail", {})
        if st == "not-found":
            findings.append(
                _finding(
                    "problem",
                    "identifier-not-found",
                    f"{name}'s identifier {i.normalised} does not exist.",
                    i,
                )
            )
        elif st == "broken":
            findings.append(
                _finding(
                    "warning",
                    "identifier-broken",
                    f"{name}'s identifier {i.normalised} returns HTTP "
                    f"{res.get('http_status')}.",
                    i,
                )
            )
        elif st == "unreachable":
            host = re.sub(r"^https?://([^/]+).*", r"\1", i.normalised)
            findings.append(
                _finding(
                    "info",
                    "identifier-unchecked",
                    f"Could not check {i.normalised}: {host} did not respond.",
                    i,
                    group=host,
                    many=f"Could not check {{n}} identifiers: {host} did not "
                    f"respond (e.g. {{examples}}).",
                )
            )
        if st != "ok":
            continue
        if i.kind == "doi" and i.role in ("dataset", "alternate"):
            ra = detail.get("registration_agency")
            rtype = detail.get("resource_type") or ""
            if ra == "Crossref" or rtype.casefold() in PUBLICATION_TYPES:
                findings.append(
                    _finding(
                        "warning",
                        "dataset-doi-is-publication",
                        f"The dataset's DOI {i.normalised} is registered "
                        f"{'with ' + ra + ' ' if ra else ''}as a "
                        f"{rtype or 'publication'}: it identifies a "
                        f"publication, not the dataset.",
                        i,
                    )
                )
            if detail.get("state") not in (None, "findable"):
                findings.append(
                    _finding(
                        "warning",
                        "doi-not-findable",
                        f"DOI {i.normalised} is in state "
                        f"'{detail['state']}', not 'findable'.",
                        i,
                    )
                )
        if i.kind == "ror" and detail.get("organisation_status") not in (
            None,
            "active",
        ):
            succ = detail.get("successors") or []
            findings.append(
                _finding(
                    "warning",
                    "inactive-organisation",
                    f"{detail.get('name') or name} ({i.normalised}) is "
                    f"{detail['organisation_status']} in ROR"
                    + (
                        f"; its successor is {', '.join(succ)}."
                        if succ
                        else "."
                    ),
                    i,
                )
            )
        if (
            i.kind == "orcid"
            and i.label
            and detail.get("name")
            and not _family_name_matches(i.label, detail["name"])
        ):
            findings.append(
                _finding(
                    "info",
                    "orcid-name-mismatch",
                    f"The record names {i.normalised} '{i.label}'; ORCID "
                    f"names it '{detail['name']}'.",
                    i,
                )
            )
    return findings


def _compact(findings: list[dict], limit: int) -> dict:
    out = {}
    for severity, key in SEVERITIES:
        items = [
            {
                k: f[k]
                for k in ("code", "message", "count", "examples")
                if k in f
            }
            for f in findings
            if f["severity"] == severity
        ]
        out[key] = items[:limit]
        if len(items) > limit:
            out[f"{key}_omitted"] = len(items) - limit
    return out


def check_record(
    record: dict,
    resolver: Resolver | None = None,
    resolve: bool = True,
    max_findings: int = 10,
) -> dict:
    """Check a CDIF record's identifiers and vocabulary terms.

    With resolve=False only the offline checks run (no network)."""
    offline = check_offline(record)
    ids = extract(record)
    summary = dict(offline["summary"])
    findings = list(offline["findings"])
    if resolve:
        resolver = resolver or default_resolver()
        targets = [(i.normalised, i.kind) for i in ids if _resolvable(i)]
        results = resolver.resolve_many(targets)
        findings += resolution_findings(ids, results)
        summary["identifiers_checked"] = len(results)
        summary["identifiers_resolving"] = sum(
            r["status"] == "ok" for r in results.values()
        )
        if doi := summary.get("dataset_doi"):
            detail = results.get(doi, {}).get("detail", {})
            summary["dataset_doi_agency"] = detail.get("registration_agency")
            summary["dataset_doi_type"] = detail.get("resource_type")
        vocab = check_vocabulary(ids, resolver)
        findings += vocab["findings"]
        summary["terms"] = {
            "checked": vocab["summary"]["terms"],
            **{
                k: v
                for k, v in vocab["summary"].items()
                if k not in ("terms", "vocabularies", "rva_vocabularies")
            },
        }
        summary["vocabularies_named"] = vocab["summary"]["vocabularies"]
        summary["rva_vocabularies"] = [
            _rva_label(v) for v in vocab["vocabularies"]["rva"]
        ]
    return {"summary": summary, **_compact(_aggregate(findings), max_findings)}


def _rva_label(v: dict) -> str:
    """'AODN Platform Vocabulary (version-7-0, current)'."""
    name = v.get("title") or v["path"]
    version = v.get("version")
    if not version:
        return name
    if not version.lower().startswith("version"):
        version = f"version {version}"
    return f"{name} ({version}, {v.get('version_status')})"
