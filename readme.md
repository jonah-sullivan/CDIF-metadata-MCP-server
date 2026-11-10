# cdif-mcp

An MCP server that lets an AI agent assess dataset metadata against the
[CDIF Discovery profile](https://github.com/Cross-Domain-Interoperability-Framework/doc-corediscovery)
(Cross-Domain Interoperability Framework, v1.1).

Ask an MCP client such as Claude Desktop: *"Is the metadata on this dataset
landing page CDIF-conformant, and what would fix it?"* The agent fetches the
page's embedded JSON-LD, validates it with CODATA's own schema and framing
script, and looks up the implementation guide to explain the gaps.

## Tools

| Tool | What it does |
| --- | --- |
| `fetch_metadata(url)` | Extracts the JSON-LD records embedded in a landing page |
| `validate_cdif(record)` | Frames a record and validates it against the CDIF Discovery JSON Schema |
| `search_cdif_guide(query)` | Returns the most relevant sections of the implementation guide |
| `list_cdif_examples()` / `get_cdif_example(name)` | Conforming records from real catalogues, as templates |

## Setup

```bash
git clone https://github.com/<you>/cdif-mcp && cd cdif-mcp
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
git clone --depth 1 https://github.com/Cross-Domain-Interoperability-Framework/doc-corediscovery vendor/doc-corediscovery
python test_server.py
```

## Connect to Claude Desktop

Settings > Developer > Edit Config, then add (absolute paths):

```json
{
  "mcpServers": {
    "cdif-metadata": {
      "command": "/path/to/cdif-mcp/.venv/bin/python",
      "args": ["/path/to/cdif-mcp/server.py"]
    }
  }
}
```

Restart Claude Desktop.

## How it works

- Validation reuses CODATA's normative `FrameAndValidate.py`: JSON-LD is
  expanded, framed into the tree shape the JSON Schema expects, then validated.
- Most catalogues publish `"@context": "https://schema.org/"` with bare
  property names. The server resolves that context locally and re-compacts
  the record with CDIF's prefixes (`schema:name`), so no network call is
  needed and the RDF meaning is unchanged.
- Validation is open-world (unknown properties pass) and covers the JSON
  Schema only, not the SHACL rules.

## Limitations and next steps

- Guide search is keyword-based. Replacing it with embedding retrieval over
  the full CDIF book would make it retrieval-augmented generation proper.
- Add SHACL validation (`discoveryDocRules.shacl`, via pyshacl).
- Add crosswalk tools (ISO 19115 or DCAT to CDIF).

CDIF artefacts are (c) CODATA, CC-BY-4.0.