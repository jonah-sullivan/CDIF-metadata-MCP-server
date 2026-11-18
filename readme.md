# cdif-mcp

An MCP server that lets an AI agent assess dataset metadata against the
[CDIF Discovery profile](https://github.com/Cross-Domain-Interoperability-Framework/doc-corediscovery)
(Cross-Domain Interoperability Framework, v1.1). It does two jobs:

- **Validate JSON-LD.** Check the schema.org JSON-LD embedded in a dataset
  landing page, or any JSON-LD record, against CODATA's CDIF JSON Schema and
  SHACL rules, and explain how to fix what fails.
- **Convert ISO 19115.** Convert an ISO 19115 record (ISO 19115-3 or ISO
  19139 XML), such as those published by the GeoNetwork catalogues of TERN,
  IMOS/AODN and AuScope, to CDIF JSON-LD, validate it, and report what was
  mapped, inferred, missing and ignored.

Ask an MCP client such as Claude Desktop, for example:

- *"Is the metadata on this dataset landing page CDIF-conformant, and what
  would fix it?"*
- *"Convert TERN record 3567cff4-4a73-439e-a1b4-d30caf3c8959 from
  https://geonetwork.tern.org.au/geonetwork to CDIF. Does it validate, and
  what had to be inferred?"*

## Tools

| Tool | What it does |
| --- | --- |
| `fetch_metadata(url)` | Extracts the JSON-LD records embedded in a landing page |
| `validate_cdif(example \| url \| record)` | Validates a bundled example (by name), a landing page (by URL) or a record against the CDIF Discovery JSON Schema and SHACL rules |
| `search_cdif_guide(query)` | Returns the most relevant sections of the implementation guide |
| `list_cdif_examples()` / `get_cdif_example(name)` | Conforming records from real catalogues, as templates |
| `crosswalk_iso19115(url \| example)` | Converts an ISO 19115 record (by XML URL or GeoNetwork page URL, or a bundled example) to CDIF and validates it |
| `crosswalk_geonetwork_record(catalogue, uuid)` | The same, for a record identified by its uuid in a GeoNetwork catalogue |
| `list_iso_examples()` | Bundled ISO 19115 records from TERN, IMOS/AODN and AuScope |

## Setup

```bash
git clone https://github.com/<you>/cdif-mcp && cd cdif-mcp
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt     # or requirements.txt to run only
git clone --depth 1 https://github.com/Cross-Domain-Interoperability-Framework/doc-corediscovery vendor/doc-corediscovery
python -m pytest
```

The tests need no network access. `tests/test_mcp_server.py` drives the
server through a real MCP client in memory.

## Connect to Claude Desktop

Settings > Developer > Edit Config, then add (absolute paths):

```json
{
  "mcpServers": {
    "cdif-metadata": {
      "command": "/path/to/cdif-mcp/.venv/bin/python",
      "args": ["/path/to/cdif-mcp/DemoMcpServer.py"]
    }
  }
}
```

Restart Claude Desktop.

## Run with a local model (Lemonade + qwen)

The tools also work with a model running on your own machine. This setup
uses [Lemonade](https://github.com/lemonade-sdk/lemonade) to serve
`qwen3.5-9b-FLM` through an OpenAI-compatible API. Lemonade only serves the
model: it is not an MCP client, so something has to sit between it and this
server. Use either `qwen_agent.py` (terminal) or Open WebUI (browser).

### 1. Get qwen running in Lemonade

Install Lemonade (on Ubuntu, the `lemonade-server` package runs it as a
systemd service on `http://localhost:13305`), then load the model once with a
larger context window and save that setting:

```bash
lemonade pull qwen3.5-9b-FLM          # first time only
lemonade load qwen3.5-9b-FLM --ctx-size 16384 --save-options
lemonade status                       # should list qwen3.5-9b-FLM
```

The default 4096-token context is too small: one guide search can return
more than that. With the options saved, Lemonade reloads the model with
them whenever a request needs it, so you do not have to load it by hand
again. Unload it with `lemonade unload` to free its memory (about 16 GB).

### 2a. Terminal: `qwen_agent.py`

`qwen_agent.py` is a small MCP client. It loads this server in-process (no
need to start `DemoMcpServer.py` separately), passes its tools to qwen, runs
the tool calls qwen makes and returns the results.

```bash
.venv/bin/python qwen_agent.py "Is the metadata at <url> CDIF-conformant?"
.venv/bin/python qwen_agent.py      # interactive
```

Environment variables: `LEMONADE_URL` (default
`http://localhost:13305/api/v1`), `LEMONADE_MODEL` (default
`qwen3.5-9b-FLM`) and `MAX_TOOL_CHARS` (default 6000, the cap on tool output
sent back to the model).

### 2b. Browser: Open WebUI

[Open WebUI](https://github.com/open-webui/open-webui) is a chat UI that can
use Lemonade as a model provider and this server as an MCP tool server.

**Start the MCP server over HTTP** (leave it running):

```bash
.venv/bin/fastmcp run DemoMcpServer.py --transport http --host 127.0.0.1 --port 8765
```

**Run Open WebUI in Docker on the host network**, so that `localhost` inside
the container reaches Lemonade (which listens on 127.0.0.1 only) and the MCP
server. On Docker's default bridge network the container cannot reach them.

```bash
docker run -d --network=host -v open-webui:/app/backend/data \
  -e WEBUI_AUTH=False \
  --name open-webui --restart unless-stopped ghcr.io/open-webui/open-webui:main
```

Open WebUI is then at <http://localhost:8080>. `WEBUI_AUTH=False` turns off
the login for single-user use. It only works on a fresh install with no
accounts. On the host network, anyone who can reach port 8080 gets admin
access, so firewall that port, or omit `WEBUI_AUTH=False` and create an
account. Environment variables belong to the container, not the data volume:
when you recreate the container, pass them again.

**Configure it** in the Admin Panel (menu names may differ between versions):

1. **Settings > Connections > OpenAI API > +**: URL
   `http://127.0.0.1:13305/api/v1`, API key any non-empty text (Lemonade
   does not check it). `qwen3.5-9b-FLM` then appears in the model picker.
2. **Settings > External Tools > +**: type MCP (Streamable HTTP), URL
   `http://127.0.0.1:8765/mcp`, no auth.
3. **Settings > Models > qwen3.5-9b-FLM > Advanced Params**: set
   **Function Calling** to **Native**, and enable the CDIF tools for the
   model (or per chat, from the tools button next to the message box).

Start a new chat with qwen and ask, for example, *"Validate the example
CDIF-aloha-dataset.json"*. After you change the server's tools, restart the
MCP server and start a new chat, so the model sees the new tool descriptions.

### Tips for small models

What we learned testing the tools with qwen3.5-9b, and how the tools were
designed as a result:

- **Pass names and URLs, not records.** A 9B model generates about 9
  tokens/s on an NPU and cannot reliably copy a multi-KB JSON-LD record into
  a tool call. Ask it to validate by name or URL, for example
  `validate_cdif(example=...)` or `validate_cdif(url=...)`, so the call is a
  short string and the server loads the record itself.
- **Make arguments that belong together required.** With `catalogue` as an
  optional argument alongside `uuid`, qwen left it out on every attempt, even
  when the error message spelled out the exact call to make. As required
  parameters of their own tool (`crosswalk_geonetwork_record`), it supplied
  both first time.
- **Keep easily misread output out of the main lists.** CDIF types the
  metadata record in `schema:subjectOf` as `schema:Dataset`, so Dataset
  recommendations also fire on it. Labelling those results was not enough:
  qwen still reported them as gaps in the dataset until they moved to a
  separate `catalog_record_results` list. Likewise, the crosswalk report
  names its list of inferred values `inferences_and_fallbacks`, not
  `notes`.
- **Keep replies short.** Pass `include_record=False` to the crosswalk
  tools unless the converted record is needed: `qwen_agent.py` truncates
  tool output to `MAX_TOOL_CHARS`.

## ISO 19115 crosswalk

The crosswalk converts ISO 19115 records to CDIF Discovery JSON-LD. It reads
ISO 19115-3 (the `mdb` 2.0 encoding of ISO 19115-1, used by nearly all
TERN, IMOS/AODN and AuScope records) and ISO 19139 (the `gmd` encoding of
ISO 19115:2003). Records that do not describe data, such as service
records, are reported as out of scope.

**From an MCP client**, give one of:

- `crosswalk_iso19115(url=...)`: the record's XML URL, or a GeoNetwork record
  page such as `.../srv/eng/catalog.search#/metadata/<uuid>`, which is
  converted to its XML URL;
- `crosswalk_geonetwork_record(catalogue=..., uuid=...)`: the catalogue's
  base URL (ending in `/geonetwork`) and the record's uuid;
- `crosswalk_iso19115(example=...)`: one of the bundled records.

Each call converts and validates in one step. The reply has `crosswalk` (the
report below), `validation` (the `validate_cdif` result) and, unless
`include_record=False`, `record` (the CDIF JSON-LD).

**From Python**: `crosswalk.crosswalk(xml_bytes)` returns
`{"record": ..., "report": ...}`.

The report says what was done, so that nothing is hidden:

- `mapped`: the CDIF properties produced, with the ISO elements they were
  copied from;
- `inferences_and_fallbacks`: every value that was inferred or taken from a
  fallback source (for example, the metadata record's identifier when the
  dataset has none);
- `missing_required`: CDIF required properties the ISO record cannot supply;
- `unmapped`: ISO sections the crosswalk ignored;
- `out_of_scope`: why a record was not converted (only when it was not).

The mapping follows CODATA's crosswalk spreadsheet.
[docs/iso19115-cdif-crosswalk.md](docs/iso19115-cdif-crosswalk.md) gives each
rule, the spreadsheet rows it is based on and where it departs from them,
the rows not mapped yet, results on the bundled records, and problems found
in CDIF itself. It is generated from the code: after changing a rule, run
`python scripts/gen_crosswalk_docs.py` (a test fails while it is out of
date; `--check` exits 1 for CI).

XML from catalogues is parsed with entity expansion and network access
turned off, and record fetches refuse HTML pages and responses over 20 MB.

## Facility report

`scripts/facility_report.py` assesses how ready a facility's catalogue is
for CDIF. For each GeoNetwork catalogue given, it lists the public records
with resource type `dataset`, draws a random sample, converts and validates
each record, and writes a Markdown summary and a CSV with one row per
record:

```bash
.venv/bin/python scripts/facility_report.py \
  --catalogue TERN=https://geonetwork.tern.org.au/geonetwork \
  --catalogue IMOS/AODN=https://catalogue-imos.aodn.org.au/geonetwork \
  --catalogue AuScope=https://portal.auscope.org.au/geonetwork \
  --sample 50        # optional: --seed N, --out-dir docs, --name NAME
```

It works with GeoNetwork 4 (search API) and GeoNetwork 3 (the `q` service).
It fetches the records one at a time from the live catalogues; 50 records
from each of these three take about 15 minutes.

- **Reproducible:** the report records the random seed, so the same seed
  draws the same records while a catalogue's contents are unchanged. It also
  records SHA-256 hashes of the CDIF schema, SHACL rules, frame and framing
  script, and of this repository's crosswalk and validator, so two reports
  can be compared.
- **Two measures of validity:** *CDIF valid* counts records that pass after
  conversion. *Valid from source values alone* counts only those that needed
  no fallback for a required property (identifier, `dateModified` or
  licence), and is the better measure of how ready the source metadata is.
- **Other signals:** DOIs, licences given as URIs, named creators, and
  variables and keywords with vocabulary URIs.

Example: [docs/facility-report-2026-11-19.md](docs/facility-report-2026-11-19.md),
50 records each from TERN, IMOS/AODN and AuScope. All converted, and 100%,
100% and 96% were CDIF valid. But only 44%, 0% and 0% were valid from
source values alone, mostly because the records lack a resource
identifier.

## How it works

- Validation reuses CODATA's normative `FrameAndValidate.py`: JSON-LD is
  expanded, framed into the tree shape the JSON Schema expects, then validated.
- Most catalogues publish `"@context": "https://schema.org/"` with bare
  property names. The server resolves that context locally and re-compacts
  the record with CDIF's prefixes (`schema:name`), so no network call is
  needed and the RDF meaning is unchanged.
- Validation is open-world (unknown properties pass) for the JSON Schema.
- SHACL validation (`discoveryDocRules.shacl`, via pyshacl) runs on the
  record's whole RDF graph, so nested nodes are checked too: a node typed
  `schema:Dataset` anywhere in the record must meet the Dataset rules.
  pyshacl runs with `advanced=True`, which is required because the Dataset
  shapes select their targets with `sh:SPARQLTarget`. Results are grouped
  by severity, node, path and message, with a count, because a record with
  many similar nodes can produce thousands of identical results.
- Each SHACL result's `node` says which part of the record it is about:
  `dataset`, or a node type such as `schema:PropertyValue node`. Results
  about the metadata record in `schema:subjectOf` are listed separately, in
  `catalog_record_results`. CDIF types that record as `schema:Dataset`, so
  Dataset rules (creator, description, keywords...) are also checked on it,
  and these results do not mean the dataset lacks those properties.
- `valid` is true only if the JSON Schema passes and SHACL reports no
  Violations. SHACL Warnings and Info are recommendations. `schema_valid`
  gives the JSON Schema result alone; pass `shacl=False` to skip SHACL.
- Of the 47 bundled examples, 46 pass the JSON Schema and 36 meet the SHACL
  rules (`python -m pytest tests/test_mcp_server.py -rx` lists the
  others, as expected failures). A very large record such as
  `ncei-ghrsst-mur-sst.jsonld` (30k triples) takes about 15 s to check
  against the SHACL rules.

## Project layout

| Path | Contents |
| --- | --- |
| `DemoMcpServer.py` | The MCP server and its tools, including the validator |
| `crosswalk/` | ISO 19115 crosswalk (`iso19115.py`), GeoNetwork access (`geonetwork.py`), and its relation to CODATA's spreadsheet (`codata.py`) |
| `qwen_agent.py` | A terminal MCP client for a model served by Lemonade |
| `scripts/` | `facility_report.py`, `gen_crosswalk_docs.py`, and `fetch_iso_fixtures.py` (refreshes the bundled ISO records) |
| `tests/` | pytest suite; `tests/fixtures/iso/` holds the bundled ISO records, with their sources in `manifest.json` |
| `docs/` | Generated crosswalk documentation and the example facility report |
| `vendor/doc-corediscovery/` | CODATA's CDIF Discovery profile (cloned during setup, not committed) |

## Limitations and next steps

- Guide search is keyword-based. Replacing it with embedding retrieval over
  the full CDIF book would make it retrieval-augmented generation proper.
- Only ISO 19115 is crosswalked. DCAT and DataCite crosswalks would cover
  other common catalogue formats.
- Some ISO elements are not mapped yet, among them associated resources,
  lineage sources, distribution formats and sizes, and data quality reports.
  See "Not mapped yet" in the crosswalk documentation.
- The facility report lists GeoNetwork 4 catalogues in a single search,
  which is limited to 10,000 records. Larger catalogues would need paging.
- Building the crosswalk exposed three problems in CDIF's own artefacts,
  which should be raised upstream with CODATA (see "Problems found in CDIF
  itself" in the crosswalk documentation):
  1. CDIF's complete example uses invalid JSON-LD for WKT geometry.
  2. The validation frame drops funders that have no identifier.
  3. Dataset rules also apply to the catalog record.

CDIF artefacts are (c) CODATA, CC-BY-4.0.
