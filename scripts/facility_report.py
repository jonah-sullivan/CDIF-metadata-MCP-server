"""Assess a random sample of GeoNetwork catalogue records against CDIF.

Usage:
    python scripts/facility_report.py \\
        --catalogue TERN=https://geonetwork.tern.org.au/geonetwork \\
        --catalogue IMOS/AODN=https://catalogue-imos.aodn.org.au/geonetwork \\
        --catalogue AuScope=https://portal.auscope.org.au/geonetwork \\
        [--sample 50] [--seed N] [--out-dir docs] [--name NAME]
        [--check-identifiers]

For each catalogue, lists its public records with resource type
'dataset', draws a random sample, converts each record's ISO 19115 XML
to CDIF Discovery with this repo's crosswalk, and validates it with the
MCP server's validator (CDIF JSON Schema and SHACL). Identifiers are
always checked offline (persistent identifiers, ORCIDs, RORs, check
digits); with --check-identifiers they are also resolved, and vocabulary
terms checked against their vocabularies (slower: many requests, cached
in .cache/pidcheck/). Writes NAME.md (a
summary) and NAME.csv (one row per record) to --out-dir. The report
records the seed, so a run can be repeated on the same records
(catalogue contents permitting), and SHA-256 hashes of the files that
determine the results, so runs can be compared.
"""

import argparse
import contextlib
import csv
import datetime
import hashlib
import io
import random
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from crosswalk import CrosswalkError, crosswalk  # noqa: E402
from crosswalk.geonetwork import (  # noqa: E402
    catalogue_base,
    fetch_xml,
    list_dataset_uuids,
    record_xml_url,
)
from pidcheck import check_record  # noqa: E402

CDIF = ROOT / "vendor" / "doc-corediscovery"
# Files whose content determines a record's result.
PROVENANCE_FILES = {
    "CDIF JSON Schema": CDIF / "CDIFDiscoveryDocStructuredSchema.json",
    "CDIF SHACL rules": CDIF / "discoveryDocRules.shacl",
    "CDIF JSON-LD frame": CDIF / "CDIFDiscoveryDoc-frame.jsonld",
    "CDIF framing script": CDIF / "FrameAndValidate.py",
    "Crosswalk": ROOT / "crosswalk" / "iso19115.py",
    "GeoNetwork access": ROOT / "crosswalk" / "geonetwork.py",
    "Validator (MCP server)": ROOT / "DemoMcpServer.py",
    "Identifier checks": ROOT / "pidcheck" / "identifiers.py",
    "Identifier resolution": ROOT / "pidcheck" / "resolve.py",
    "Vocabulary checks": ROOT / "pidcheck" / "vocab.py",
    "Identifier report": ROOT / "pidcheck" / "check.py",
}

FIELDS = [
    "facility",
    "catalogue",
    "uuid",
    "xml_url",
    "status",
    "error",
    "title",
    "dialect",
    "cdif_valid",
    "schema_valid",
    "shacl_conforms",
    "shacl_violations",
    "shacl_warnings",
    "shacl_info",
    "identifier",
    "licence",
    "creators",
    "variables",
    "variables_with_uri",
    "keywords",
    "keywords_with_uri",
    "missing_required",
    "required_from_fallback",
    "schema_problems",
    "shacl_violation_messages",
    "inferences",
    "unmapped",
    # identifier checks, offline (always)
    "dataset_has_pid",
    "people",
    "people_with_orcid",
    "organisations",
    "organisations_with_ror",
    "pid_problems",
    "pid_warnings",
    # identifier checks with --check-identifiers
    "identifiers_checked",
    "identifiers_resolving",
    "dataset_doi_type",
    "terms_checked",
    "terms_ok",
    "terms_label_matches",
    "terms_label_mismatches",
    "terms_deprecated",
    "terms_not_machine_readable",
    "rva_vocabularies",
]
PAUSE_SECONDS = 0.2  # between record fetches, to be gentle on catalogues


# -- assessing one record ---------------------------------------------------


def _is_uri(v) -> bool:
    return isinstance(v, str) and v.startswith(("http://", "https://"))


# Joins multi-value CSV cells. Not "; ": crosswalk messages contain it.
SEP = " | "


def _join(items) -> str:
    return SEP.join(dict.fromkeys(str(i) for i in items))


# Required CDIF properties, and the crosswalk messages that say the value
# did not come straight from the source record.
REQUIRED_FALLBACKS = {
    "schema:identifier": "The resource has no identifier",
    "schema:dateModified": "No resource revision date",
    "schema:license": "Licence inferred",
}


def offline_identifier_check(record: dict) -> dict:
    return check_record(record, resolve=False, max_findings=1000)


def assess(
    facility: str,
    base: str,
    uuid: str,
    validate,
    check_ids=offline_identifier_check,
) -> dict:
    """Fetch, convert and validate one record; return a CSV row.
    check_ids(record) gives the identifier report (offline by default)."""
    row = dict.fromkeys(FIELDS, "")
    url = record_xml_url(uuid=uuid, catalogue=base)
    row.update(facility=facility, catalogue=base, uuid=uuid, xml_url=url)
    try:
        xml = fetch_xml(url)
    except Exception as e:  # noqa: BLE001 - any failure is a finding
        row.update(status="fetch_error", error=str(e)[:300])
        return row
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            out = crosswalk(xml, source_url=url)
    except CrosswalkError as e:
        row.update(status="not_iso", error=str(e)[:300])
        return row
    rep, rec = out["report"], out["record"]
    row["dialect"] = rep["dialect"]
    if rec is None:
        row.update(status="out_of_scope", error=rep["out_of_scope"][:300])
        return row

    v = validate(rec)
    shacl = v.get("shacl", {})
    counts = shacl.get("counts", {})
    violations = [
        r["message"]
        for r in shacl.get("results", [])
        + shacl.get("catalog_record_results", [])
        if r["severity"] == "Violation"
    ]
    inferences = rep["inferences_and_fallbacks"]
    if any("no identifier" in i for i in inferences):
        identifier = "metadata id only"
    elif rec["@id"].startswith("https://doi.org/"):
        identifier = "DOI"
    else:
        identifier = "other"
    if "schema:license" in rec:
        inferred = any(i.startswith("Licence inferred") for i in inferences)
        licence = "inferred" if inferred else "URI"
    elif "schema:conditionsOfAccess" in rec:
        licence = "conditions only"
    else:
        licence = "none"
    variables = rec.get("schema:variableMeasured", [])
    keywords = rec.get("schema:keywords", [])
    row.update(
        status="converted",
        title=rec.get("schema:name", "")[:200],
        cdif_valid=v["valid"],
        schema_valid=v.get("schema_valid", False),
        shacl_conforms=shacl.get("conforms", ""),
        shacl_violations=counts.get("Violation", ""),
        shacl_warnings=counts.get("Warning", ""),
        shacl_info=counts.get("Info", ""),
        identifier=identifier,
        licence=licence,
        creators=len(rec.get("schema:creator", {}).get("@list", [])),
        variables=len(variables),
        variables_with_uri=sum(
            _is_uri(x.get("schema:propertyID")) for x in variables
        ),
        keywords=len(keywords),
        keywords_with_uri=sum(
            isinstance(k, dict) and _is_uri(k.get("schema:identifier"))
            for k in keywords
        ),
        missing_required=_join(rep["missing_required"]),
        required_from_fallback=_join(
            prop
            for prop, msg in REQUIRED_FALLBACKS.items()
            if any(i.startswith(msg) for i in inferences)
        ),
        schema_problems=_join(p["message"][:200] for p in v["problems"]),
        shacl_violation_messages=_join(m[:200] for m in violations),
        inferences=_join(inferences),
        unmapped=_join(u["element"] for u in rep["unmapped"]),
    )
    row.update(_identifier_columns(check_ids(rec)))
    return row


def _identifier_columns(report: dict) -> dict:
    s = report["summary"]
    cols = {
        "dataset_has_pid": s["dataset_has_pid"],
        "people": s["people"],
        "people_with_orcid": s["people_with_orcid"],
        "organisations": s["organisations"],
        "organisations_with_ror": s["organisations_with_ror"],
        "pid_problems": _join(f["code"] for f in report["problems"]),
        "pid_warnings": _join(f["code"] for f in report["warnings"]),
    }
    if "identifiers_checked" in s:
        t = s["terms"]
        cols.update(
            identifiers_checked=s["identifiers_checked"],
            identifiers_resolving=s["identifiers_resolving"],
            dataset_doi_type=s.get("dataset_doi_type") or "",
            terms_checked=t["checked"],
            terms_ok=t["ok"],
            terms_label_matches=t["label_matches"],
            terms_label_mismatches=t["label_mismatches"],
            terms_deprecated=t["deprecated"],
            terms_not_machine_readable=t["not_machine_readable"],
            rva_vocabularies=_join(s["rva_vocabularies"]),
        )
    return cols


# -- summarising ------------------------------------------------------------


def _split(cell: str) -> list[str]:
    return [s for s in str(cell).split(SEP) if s]


def _pct(n: int, d: int) -> str:
    return f"{n} ({round(100 * n / d)}%)" if d else "-"


def _md(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


TRUE = {True, "True", "true"}

# Readable descriptions of identifier finding codes (pidcheck).
CODE_DESCRIPTIONS = {
    "orcid-check-digit": "ORCID iD with an invalid check digit",
    "ror-check-digit": "ROR ID with invalid check digits",
    "isni-check-digit": "ISNI with an invalid check digit",
    "shared-party-identifier": (
        "One identifier used for differently named people or organisations"
    ),
    "party-local-url": (
        "Person or organisation identified by an ordinary URL, not a PID"
    ),
    "dataset-no-pid": "Dataset has no persistent identifier",
    "not-a-uri": "Identifier that is not a URI",
    "term-set-not-a-uri": "Vocabulary named but not identified by a URI",
    "identifier-not-found": "Identifier that does not exist",
    "identifier-broken": "Identifier that returns an error",
    "dataset-doi-is-publication": "Dataset DOI that identifies a publication",
    "doi-not-findable": "DOI that is not findable",
    "inactive-organisation": "Organisation that is inactive in ROR",
    "term-not-found": "Vocabulary term that does not resolve",
    "term-not-machine-readable": (
        "Vocabulary terms with no machine-readable description"
    ),
    "label-mismatch": "Keyword name that differs from its vocabulary label",
    "deprecated-term": "Deprecated vocabulary term",
    "term-set-not-found": "Vocabulary URI that does not resolve",
    "term-uri-shared": (
        "One URI used as the identifier of several different terms"
    ),
    "superseded-vocabulary-version": (
        "Terms from a superseded RVA vocabulary version"
    ),
}


def _int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _ratio(rows: list[dict], num: str, *den: str) -> str:
    """Totals across records: 'n of d (p%)'."""
    n = sum(_int(r[num]) for r in rows)
    d = sum(_int(r[k]) for r in rows for k in den)
    return f"{n} of {d} ({round(100 * n / d)}%)" if d else "-"


def summarise(rows: list[dict], meta: dict) -> str:
    """Markdown report from assessed rows and run metadata."""
    facilities = list(dict.fromkeys(r["facility"] for r in rows))
    by = {f: [r for r in rows if r["facility"] == f] for f in facilities}
    conv = {f: [r for r in by[f] if r["status"] == "converted"] for f in by}

    def metric(label, pred=None, of_sample=False, value=None) -> str:
        cells = []
        for f in facilities:
            if value:
                cells.append(value(f))
                continue
            pool = by[f] if of_sample else conv[f]
            cells.append(_pct(sum(1 for r in pool if pred(r)), len(pool)))
        return f"| {label} | " + " | ".join(cells) + " |"

    resolved = meta.get("identifiers_resolved", False)

    def rva_used(f: str) -> list[str]:
        """Distinct RVA vocabularies. An entry without a version (when
        RVA's registry did not answer for that record) is dropped if the
        same vocabulary appears with one."""
        used = {v for r in conv[f] for v in _split(r["rva_vocabularies"])}
        versioned = {v.split(" (", 1)[0] for v in used if " (" in v}
        return sorted(v for v in used if " (" in v or v not in versioned)

    def top(field: str, f: str, n: int = 5) -> list[str]:
        c = Counter(s for r in conv[f] for s in _split(r[field]))
        ranked = sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))
        return [f"| {k} | {_md(msg)} |" for msg, k in ranked[:n]]

    lines = [
        f"# CDIF readiness of NCRIS facility metadata ({meta['date']})",
        "",
        (
            "How well a random sample of public dataset records from each "
            "facility's GeoNetwork catalogue converts to the [CDIF Discovery "
            "profile](https://w3id.org/cdif/discovery/1.1), and what is "
            "missing. Each record's ISO 19115 XML was converted with this "
            "repository's crosswalk (see "
            "[iso19115-cdif-crosswalk.md](iso19115-cdif-crosswalk.md)) and "
            "validated against the CDIF JSON Schema and SHACL rules."
        ),
        "",
        "## Method",
        "",
        (
            f"- **Sample:** {meta['sample']} records per catalogue, drawn at "
            f"random from its public records with resource type `dataset`, "
            f"using seed {meta['seed']} (each catalogue's seed is derived "
            f"from it)."
        ),
        f"- **Per-record results:** [{meta['csv']}]({meta['csv']}).",
        (
            "- **Identifiers:** checked offline for every record "
            "(persistent identifiers, ORCIDs, RORs, check digits). "
            + (
                "They were also resolved (DOIs through DataCite and "
                "Crossref, ORCID, ROR, other URIs by GET), and vocabulary "
                "terms dereferenced and checked against their "
                "vocabularies."
                if resolved
                else "They were not resolved: run with "
                "--check-identifiers for resolution and vocabulary checks."
            )
        ),
        (
            "- **Files that determined the results** (first 12 hex digits of "
            "their SHA-256; a different hash means results may differ):"
        ),
        "",
        "| File | SHA-256 |",
        "| --- | --- |",
        *(f"| {k} | `{h}` |" for k, h in meta["hashes"].items()),
        "",
        (
            "Percentages are of converted records unless stated. A "
            "record's result depends on both the source metadata and the "
            "crosswalk: a failure can come from a gap in the source, a "
            "limit of the crosswalk, or a problem in CDIF's own artefacts "
            "(listed in the crosswalk documentation)."
        ),
        "",
        (
            "**CDIF valid** counts records that pass after conversion, "
            "including where the crosswalk filled a required property from "
            "a fallback: the metadata record's identifier for "
            "`schema:identifier`, the metadata record's date for "
            "`schema:dateModified`, or a licence recognised from its name. "
            "**Valid from source values alone** counts only records that "
            "needed none of these fallbacks, and is the better measure of "
            "how ready the source metadata is."
        ),
        "",
        "## Summary",
        "",
        "| | " + " | ".join(facilities) + " |",
        "| --- |" + " --- |" * len(facilities),
        metric("GeoNetwork", value=lambda f: meta["versions"][f]),
        metric(
            "Dataset records in catalogue",
            value=lambda f: f"{meta['populations'][f]:,}",
        ),
        metric("Sampled", value=lambda f: str(len(by[f]))),
        metric(
            "Converted (of sample)",
            lambda r: r["status"] == "converted",
            of_sample=True,
        ),
        metric("**CDIF valid**", lambda r: r["cdif_valid"] in TRUE),
        metric(
            "**Valid from source values alone**",
            lambda r: (
                r["cdif_valid"] in TRUE and not r["required_from_fallback"]
            ),
        ),
        metric(
            "Identifier from fallback",
            lambda r: "schema:identifier" in r["required_from_fallback"],
        ),
        metric(
            "dateModified from fallback",
            lambda r: "schema:dateModified" in r["required_from_fallback"],
        ),
        metric("JSON Schema valid", lambda r: r["schema_valid"] in TRUE),
        metric(
            "SHACL conforms (no violations)",
            lambda r: r["shacl_conforms"] in TRUE,
        ),
        metric("DOI as identifier", lambda r: r["identifier"] == "DOI"),
        metric("Licence given as a URI", lambda r: r["licence"] == "URI"),
        metric(
            "Licence recognised only from its name or badge",
            lambda r: r["licence"] == "inferred",
        ),
        metric("No licence", lambda r: r["licence"] == "none"),
        metric("Names a creator", lambda r: int(r["creators"] or 0) > 0),
        metric(
            "Variables with vocabulary URIs",
            lambda r: int(r["variables_with_uri"] or 0) > 0,
        ),
        metric(
            "Keywords with vocabulary URIs",
            lambda r: int(r["keywords_with_uri"] or 0) > 0,
        ),
        metric(
            "Dataset has a persistent identifier",
            lambda r: r["dataset_has_pid"] in TRUE,
        ),
        metric(
            "People with an ORCID",
            value=lambda f: _ratio(conv[f], "people_with_orcid", "people"),
        ),
        metric(
            "Organisations with a ROR",
            value=lambda f: _ratio(
                conv[f], "organisations_with_ror", "organisations"
            ),
        ),
    ]
    if resolved:
        lines += [
            metric(
                "All checked identifiers resolve",
                lambda r: (
                    r["identifiers_checked"] != ""
                    and _int(r["identifiers_resolving"])
                    == _int(r["identifiers_checked"])
                ),
            ),
            metric(
                "Dataset DOI identifies a publication",
                lambda r: (
                    "dataset-doi-is-publication" in _split(r["pid_warnings"])
                ),
            ),
            metric(
                "Vocabulary terms with a machine-readable description",
                value=lambda f: _ratio(conv[f], "terms_ok", "terms_checked"),
            ),
            metric(
                "Term names matching their vocabulary label",
                value=lambda f: _ratio(
                    conv[f],
                    "terms_label_matches",
                    "terms_label_matches",
                    "terms_label_mismatches",
                ),
            ),
            metric(
                "Deprecated vocabulary terms",
                value=lambda f: str(
                    sum(_int(r["terms_deprecated"]) for r in conv[f])
                ),
            ),
            metric(
                "RVA-hosted vocabularies used",
                value=lambda f: str(len(rva_used(f))),
            ),
        ]
    lines.append("")

    sections = [
        (
            "Why records fail validation",
            (
                "Most common JSON Schema problems and SHACL violations, by "
                "number of records."
            ),
            ["schema_problems", "shacl_violation_messages"],
        ),
        (
            "Missing required properties",
            "CDIF required properties the source records cannot supply.",
            ["missing_required"],
        ),
        (
            "Inferences and fallbacks",
            (
                "Values the crosswalk had to infer or take from a fallback "
                "source, most common first."
            ),
            ["inferences"],
        ),
    ]
    for title, blurb, fields in sections:
        lines += [f"## {title}", "", blurb, ""]
        for f in facilities:
            found = [x for field in fields for x in top(field, f)]
            lines += [f"### {f}", ""]
            if found:
                lines += ["| Records | Message |", "| --- | --- |", *found]
            else:
                lines.append("None.")
            lines.append("")

    lines += [
        "## Identifier and vocabulary findings",
        "",
        "Problems and warnings from the identifier checks, by number of "
        "records.",
        "",
    ]
    for f in facilities:
        c = Counter(
            code
            for r in conv[f]
            for code in _split(r["pid_problems"]) + _split(r["pid_warnings"])
        )
        ranked = sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
        lines += [f"### {f}", ""]
        if ranked:
            lines += [
                "| Records | Finding |",
                "| --- | --- |",
                *(
                    f"| {n} | {CODE_DESCRIPTIONS.get(code, code)} |"
                    for code, n in ranked
                ),
            ]
        else:
            lines.append("None.")
        lines.append("")
    if resolved:
        lines += ["## Vocabularies hosted in RVA", ""]
        for f in facilities:
            used = rva_used(f)
            lines.append(f"- **{f}:** " + ("; ".join(used) or "none"))
        lines.append("")

    not_converted = [r for r in rows if r["status"] != "converted"]
    lines += ["## Records not converted", ""]
    if not_converted:
        lines += [
            "| Facility | Record | Status | Reason |",
            "| --- | --- | --- | --- |",
            *(
                f"| {r['facility']} | `{r['uuid']}` | {r['status']} "
                f"| {_md(r['error'])[:160]} |"
                for r in not_converted
            ),
        ]
    else:
        lines.append("None.")
    return "\n".join(lines).rstrip() + "\n"


# -- running ----------------------------------------------------------------


def file_hashes() -> dict[str, str]:
    return {
        label: hashlib.sha256(path.read_bytes()).hexdigest()[:12]
        if path.is_file()
        else "missing"
        for label, path in PROVENANCE_FILES.items()
    }


def parse_catalogues(values: list[str]) -> dict[str, str]:
    cats = {}
    for v in values:
        name, sep, url = v.partition("=")
        if not sep or not name or not url:
            raise SystemExit(f"--catalogue must be NAME=URL, got {v!r}")
        cats[name] = catalogue_base(url)
    return cats


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n".join(__doc__.splitlines()[1:]),
    )
    ap.add_argument(
        "--catalogue",
        action="append",
        required=True,
        metavar="NAME=URL",
        help="facility name and GeoNetwork base URL (repeatable)",
    )
    ap.add_argument("--sample", type=int, default=50)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--out-dir", type=Path, default=ROOT / "docs")
    ap.add_argument("--name", default=None)
    ap.add_argument(
        "--check-identifiers",
        action="store_true",
        help="resolve identifiers and check vocabulary terms (slower)",
    )
    args = ap.parse_args()

    import DemoMcpServer as srv

    catalogues = parse_catalogues(args.catalogue)
    seed = args.seed if args.seed is not None else random.randrange(10**6)
    date = datetime.date.today().isoformat()
    name = args.name or f"facility-report-{date}"
    meta = {
        "date": date,
        "sample": args.sample,
        "seed": seed,
        "csv": f"{name}.csv",
        "hashes": file_hashes(),
        "versions": {},
        "populations": {},
        "identifiers_resolved": args.check_identifiers,
    }
    check_ids = offline_identifier_check
    if args.check_identifiers:
        from pidcheck import default_resolver

        resolver = default_resolver()

        def check_ids(record):
            return check_record(record, resolver, max_findings=1000)

    rows = []
    for facility, base in catalogues.items():
        print(f"{facility}: listing {base} ...", file=sys.stderr)
        version, uuids = list_dataset_uuids(base)
        meta["versions"][facility] = version
        meta["populations"][facility] = len(uuids)
        rng = random.Random(f"{seed}:{facility}")
        sample = sorted(rng.sample(uuids, min(args.sample, len(uuids))))
        for i, uuid in enumerate(sample, 1):
            row = assess(facility, base, uuid, srv._validate_one, check_ids)
            rows.append(row)
            print(
                f"  [{i}/{len(sample)}] {row['status']:12} "
                f"valid={row['cdif_valid']!s:5} {uuid}",
                file=sys.stderr,
            )
            time.sleep(PAUSE_SECONDS)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.out_dir / f"{name}.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    md_path = args.out_dir / f"{name}.md"
    md_path.write_text(summarise(rows, meta), "utf-8")
    print(f"wrote {md_path} and {csv_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
