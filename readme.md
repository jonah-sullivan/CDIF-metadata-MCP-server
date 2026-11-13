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
| `validate_cdif(example \| url \| record)` | Validates a bundled example (by name), a landing page (by URL) or a record against the CDIF Discovery JSON Schema and SHACL rules |
| `search_cdif_guide(query)` | Returns the most relevant sections of the implementation guide |
| `list_cdif_examples()` / `get_cdif_example(name)` | Conforming records from real catalogues, as templates |

## Setup

```bash
git clone https://github.com/<you>/cdif-mcp && cd cdif-mcp
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
git clone --depth 1 https://github.com/Cross-Domain-Interoperability-Framework/doc-corediscovery vendor/doc-corediscovery
python test-server.py
```

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

A 9B model generates about 9 tokens/s on an NPU and cannot reliably copy a
multi-KB JSON-LD record into a tool call. Ask it to validate by name or URL,
for example `validate_cdif(example=...)` or `validate_cdif(url=...)`, so the
call is a short string and the server loads the record itself.

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
  by severity, path and message, with a count, because a record with many
  similar nodes can produce thousands of identical results.
- `valid` is true only if the JSON Schema passes and SHACL reports no
  Violations. SHACL Warnings and Info are recommendations. `schema_valid`
  gives the JSON Schema result alone; pass `shacl=False` to skip SHACL.
- Of the 47 bundled examples, 46 pass the JSON Schema and 36 meet the SHACL
  rules (run `test-server.py` for the list). A very large record such as
  `ncei-ghrsst-mur-sst.jsonld` (30k triples) takes about 15 s to check
  against the SHACL rules.

## Limitations and next steps

- Guide search is keyword-based. Replacing it with embedding retrieval over
  the full CDIF book would make it retrieval-augmented generation proper.
- Add crosswalk tools (ISO 19115 or DCAT to CDIF).

CDIF artefacts are (c) CODATA, CC-BY-4.0.