"""cdif-mcp: an MCP server that lets AI agents check dataset metadata
against the CDIF Discovery profile (https://w3id.org/cdif/discovery/1.1).

Run:  python server.py        (stdio transport, for Claude Desktop etc.)
"""
import contextlib
import io
import json
import re
import sys
import tempfile
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from fastmcp import FastMCP
from jsonschema.exceptions import best_match

# CODATA's composite CDIF Discovery spec (Core + Discovery), cloned into
# vendor/ (see README).
PROFILE = Path(__file__).parent / "vendor" / "doc-corediscovery"
SCHEMA = PROFILE / "CDIFDiscoveryDocStructuredSchema.json"
FRAME = PROFILE / "CDIFDiscoveryDoc-frame.jsonld"
GUIDE = PROFILE / "CDIFDiscoveryDocImplementationGuide.md"

# CODATA's own framing + validation script. It print()s progress, and on a
# stdio MCP server stdout IS the protocol channel, so every call into it is
# wrapped in redirect_stdout below.
sys.path.insert(0, str(PROFILE))
with contextlib.redirect_stdout(io.StringIO()):
    import FrameAndValidate as cdif

mcp = FastMCP(
    "cdif-metadata",
    instructions=(
        "Tools for assessing dataset metadata against the CDIF Discovery "
        "profile. Typical flow: validate_cdif(url=...) or "
        "validate_cdif(example=...) -> search_cdif_guide(query) to explain "
        "how to fix what failed."
    ),
)

SCHEMA_ORG = {"http://schema.org", "http://schema.org/",
              "https://schema.org", "https://schema.org/"}
PREFIXES = {k: v for k, v in json.loads(FRAME.read_text("utf-8"))["@context"]
            .items() if isinstance(v, str)}


def _to_cdif_form(doc: dict) -> dict:
    """Most catalogues publish '@context': 'https://schema.org/' with bare
    property names; CDIF's schema expects prefixed ones (schema:name). Resolve
    the schema.org context locally (no network call), then re-compact the
    record with the CDIF prefixes. Same RDF, different spelling."""
    ctx = doc.get("@context")
    local = {}
    for item in ctx if isinstance(ctx, list) else [ctx]:
        if isinstance(item, str) and item in SCHEMA_ORG:
            local["@vocab"] = "http://schema.org/"
        elif isinstance(item, dict):
            local.update(item)
        elif item is not None:
            return doc  # another remote context: leave it for pyld to fetch
    if str(local.get("@vocab", "")).startswith("https://schema.org"):
        local["@vocab"] = "http://schema.org/"
    # Some catalogues (e.g. GeoNetwork) mix bare names with "schema:X" under a
    # plain schema.org context, which leaves "schema:" undefined. Define it so
    # both spellings mean the same thing.
    local.setdefault("schema", "http://schema.org/")
    expanded = cdif.jsonld.expand({**doc, "@context": local})
    return cdif.jsonld.compact(expanded, PREFIXES)


@mcp.tool
def fetch_metadata(url: str) -> list[dict]:
    """Fetch a dataset landing page and return every JSON-LD record embedded
    in it (<script type="application/ld+json">). If the URL itself returns
    JSON-LD, that document is returned. An empty list means the page carries
    no machine-readable metadata, which is itself a finding."""
    return _fetch(url)


def _fetch(url: str) -> list[dict]:
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"Not a web URL: {url!r}. For a bundled example "
                         f"file, pass its name as example= instead.")
    r = httpx.get(url, follow_redirects=True, timeout=30,
                  headers={"Accept": "text/html, application/ld+json;q=0.9",
                           "User-Agent": "cdif-mcp/0.1"})
    r.raise_for_status()
    if "json" in r.headers.get("content-type", ""):
        data = r.json()
        return data if isinstance(data, list) else [data]
    records = []
    for tag in BeautifulSoup(r.text, "html.parser").find_all(
            "script", type="application/ld+json"):
        try:
            raw = re.sub(r"<!--.*?-->", "", tag.string or "", flags=re.DOTALL)
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        records.extend(data if isinstance(data, list) else [data])
    return records


@mcp.tool
def validate_cdif(record: dict | list[dict] | None = None,
                  example: str | None = None,
                  url: str | None = None) -> dict:
    """Validate JSON-LD metadata against the CDIF Discovery profile v1.1.
    Give exactly ONE of:
      example: name of a bundled example from list_cdif_examples(), e.g.
               "CDIF-aloha-dataset.json" (a name, not a path or URL)
      url:     a dataset landing page; its embedded JSON-LD is fetched and
               validated, so there is no need to call fetch_metadata first
      record:  a JSON-LD record, or the list returned by fetch_metadata
    Prefer example or url: they avoid copying a whole record into the call.
    Returns whether it is valid, which CDIF profiles it declares in
    dcterms:conformsTo, and a list of problems (JSON path + message). When
    there are several records, returns {"records": [...]}, one result each."""
    given = [k for k, v in (("record", record), ("example", example),
                            ("url", url)) if v is not None]
    if len(given) != 1:
        raise ValueError("Give exactly one of record, example or url "
                         f"(got: {', '.join(given) or 'none'}).")
    if example is not None:
        record = _load_example(example)
    elif url is not None:
        record = _fetch(url)
    if isinstance(record, dict):
        return _validate_one(record)
    if not record:
        return {"valid": False, "problem_count": 1, "problems": [
            {"path": "$", "message": "Empty list: no JSON-LD records to "
             "validate (the page carries no machine-readable metadata)."}]}
    if len(record) == 1:
        return _validate_one(record[0])
    return {"records": [{"index": i, **_validate_one(r)}
                        for i, r in enumerate(record)]}


def _validate_one(record: dict) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".jsonld", delete=False,
                                     encoding="utf-8") as f:
        pass
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            doc = _to_cdif_form(record)
            Path(f.name).write_text(json.dumps(doc), "utf-8")
            framed = cdif.frame_cdif_document(f.name, str(FRAME))
            result = cdif.validate_against_schema(framed, str(SCHEMA))
    except Exception as e:  # malformed JSON-LD, unreachable remote context...
        return {"valid": False, "problems": [
            {"path": "$", "message": f"Could not process as JSON-LD: {e}"}]}
    finally:
        Path(f.name).unlink(missing_ok=True)

    # The CDIF frame selects the schema:Dataset node. If nothing matched, the
    # schema would report every required property as missing, which is
    # misleading; report the actual cause instead.
    if "@type" not in framed:
        types = doc.get("@type", "none") if isinstance(doc, dict) else "unknown"
        return {"valid": False, "declared_conformance": [], "problem_count": 1,
                "problems": [{"path": "$['@type']", "message": (
                    f"No schema:Dataset node found to validate (record @type: "
                    f"{types}). CDIF requires the described resource to be "
                    f"typed schema:Dataset; other types may be listed "
                    f"alongside it.")}]}

    declared = [c.get("@id", c) if isinstance(c, dict) else c
                for c in (framed.get("schema:subjectOf") or {}).get(
                    "dcterms:conformsTo", [])]
    problems = [{"path": e.json_path, "message": _explain(e)}
                for e in sorted(result["errors"], key=lambda e: e.json_path)]
    return {"valid": result["valid"], "declared_conformance": declared,
            "problem_count": len(problems), "problems": problems[:40]}


def _explain(error) -> str:
    """jsonschema reports a failed anyOf by dumping the whole value. Report
    the most relevant underlying failure instead."""
    if error.context:
        sub = best_match(error.context)
        return f"{sub.json_path}: {sub.message}"[:300]
    return error.message[:300]


def _guide_sections() -> list[tuple[str, str]]:
    """Split the implementation guide into (heading, text) sections."""
    sections, heading, lines = [], "Preamble", []
    for line in GUIDE.read_text("utf-8").splitlines():
        if re.match(r"#{1,3} ", line):
            sections.append((heading, "\n".join(lines)))
            heading, lines = line.lstrip("# ").strip(), []
        lines.append(line)
    sections.append((heading, "\n".join(lines)))
    return sections


@mcp.tool
def search_cdif_guide(query: str, max_sections: int = 3) -> list[dict]:
    """Search the CDIF Discovery implementation guide and return the most
    relevant sections (Markdown). Use it to explain a validation problem or
    look up how a property should be encoded, e.g. 'schema:subjectOf
    CatalogRecord' or 'spatial coverage bounding box'."""
    terms = set(re.findall(r"[a-z0-9]+", query.lower()))
    scored = []
    for heading, text in _guide_sections():
        words = re.findall(r"[a-z0-9]+", text.lower())
        hits = sum(words.count(t) for t in terms)
        hits += 5 * sum(t in heading.lower() for t in terms)
        if hits:
            scored.append((hits / (len(words) + 50) ** 0.5, heading, text))
    scored.sort(reverse=True)
    return [{"section": h, "text": t[:6000]} for _, h, t in scored[:max_sections]]


@mcp.tool
def list_cdif_examples() -> list[str]:
    """List the conforming example records shipped with the CDIF Discovery
    profile (drawn from real catalogues such as Copernicus, PANGAEA, ODIS)."""
    return sorted(p.name for p in (PROFILE / "examples").glob("*.json*"))


@mcp.tool
def get_cdif_example(name: str) -> dict:
    """Return one conforming CDIF Discovery example record, to use as a
    template or comparison. name comes from list_cdif_examples(). To check
    whether it is valid, call validate_cdif(example=name) instead."""
    return _load_example(name)


def _load_example(name: str) -> dict:
    # Path(name).name drops any directory part, so only files in examples/
    # can be read (no "../" or absolute paths).
    path = PROFILE / "examples" / Path(name).name
    if not path.is_file():
        raise ValueError(f"No such example: {name}. Call list_cdif_examples().")
    return json.loads(path.read_text("utf-8"))


if __name__ == "__main__":
    mcp.run()