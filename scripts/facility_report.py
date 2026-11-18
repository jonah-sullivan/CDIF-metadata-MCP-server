"""Assess a random sample of GeoNetwork catalogue records against CDIF.

Usage:
    python scripts/facility_report.py \\
        --catalogue TERN=https://geonetwork.tern.org.au/geonetwork \\
        --catalogue IMOS/AODN=https://catalogue-imos.aodn.org.au/geonetwork \\
        --catalogue AuScope=https://portal.auscope.org.au/geonetwork \\
        [--sample 50] [--seed N] [--out-dir docs] [--name NAME]

For each catalogue, lists its public records with resource type
'dataset', draws a random sample, converts each record's ISO 19115 XML
to CDIF Discovery with this repo's crosswalk, and validates it with the
MCP server's validator (CDIF JSON Schema and SHACL). Writes NAME.md (a
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


def assess(facility: str, base: str, uuid: str, validate) -> dict:
    """Fetch, convert and validate one record; return a CSV row."""
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
    return row


# -- summarising ------------------------------------------------------------


def _split(cell: str) -> list[str]:
    return [s for s in str(cell).split(SEP) if s]


def _pct(n: int, d: int) -> str:
    return f"{n} ({round(100 * n / d)}%)" if d else "-"


def _md(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


TRUE = {True, "True", "true"}


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
        "",
    ]

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
    }

    rows = []
    for facility, base in catalogues.items():
        print(f"{facility}: listing {base} ...", file=sys.stderr)
        version, uuids = list_dataset_uuids(base)
        meta["versions"][facility] = version
        meta["populations"][facility] = len(uuids)
        rng = random.Random(f"{seed}:{facility}")
        sample = sorted(rng.sample(uuids, min(args.sample, len(uuids))))
        for i, uuid in enumerate(sample, 1):
            row = assess(facility, base, uuid, srv._validate_one)
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
