"""Chat with a local Lemonade-served model that can call this MCP server's tools.

Usage:
    python qwen_agent.py "Is the metadata at <url> CDIF-conformant?"
    python qwen_agent.py            # interactive

Lemonade serves an OpenAI-compatible API; this script is the MCP client that
sits between it and DemoMcpServer: it lists the server's tools, passes them to
the model, runs the tool calls the model makes, and feeds back the results.
"""
import asyncio
import json
import os
import sys

import httpx
from fastmcp import Client

from DemoMcpServer import mcp

BASE_URL = os.environ.get("LEMONADE_URL", "http://localhost:13305/api/v1")
MODEL = os.environ.get("LEMONADE_MODEL", "qwen3.5-9b-FLM")
# Tool output is truncated to this many characters before it goes back to the
# model, so a big guide section or record doesn't overflow a small context.
MAX_TOOL_CHARS = int(os.environ.get("MAX_TOOL_CHARS", "6000"))
MAX_STEPS = 8

SYSTEM = (
    "You assess dataset metadata against the CDIF Discovery profile using the "
    "tools provided. Validate with validate_cdif(url=...) for a web page or "
    "validate_cdif(example=...) for a bundled example; never copy a whole "
    "record into a tool call. Then search_cdif_guide(query) to explain fixes. "
    "Base every claim on tool "
    "results. Be concise."
)


def to_openai_tools(tools) -> list[dict]:
    return [{"type": "function", "function": {
        "name": t.name,
        "description": t.description or "",
        "parameters": t.input_schema,
    }} for t in tools]


async def chat(http: httpx.AsyncClient, messages: list[dict], tools: list[dict]) -> dict:
    r = await http.post(f"{BASE_URL}/chat/completions", json={
        "model": MODEL, "messages": messages, "tools": tools,
    })
    r.raise_for_status()
    return r.json()["choices"][0]["message"]


async def run_turn(mcp_client, http, messages, tools) -> str:
    for _ in range(MAX_STEPS):
        msg = await chat(http, messages, tools)
        messages.append({k: v for k, v in msg.items() if v is not None})
        calls = msg.get("tool_calls") or []
        if not calls:
            return msg.get("content") or ""
        for call in calls:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"]["arguments"] or "{}")
                print(f"  -> {name}({json.dumps(args)[:120]})", file=sys.stderr)
                result = await mcp_client.call_tool(name, args)
                text = json.dumps(result.structured_content or result.data,
                                  default=str)
            except Exception as e:  # bad arguments, tool error: let the model retry
                text = f"Error: {e}"
            if len(text) > MAX_TOOL_CHARS:
                text = text[:MAX_TOOL_CHARS] + " ...[truncated]"
            messages.append({"role": "tool", "tool_call_id": call["id"],
                             "content": text})
    return "(stopped: too many tool steps)"


async def main():
    async with Client(mcp) as mcp_client, httpx.AsyncClient(timeout=600) as http:
        tools = to_openai_tools(await mcp_client.list_tools())
        messages = [{"role": "system", "content": SYSTEM}]
        prompts = sys.argv[1:] and [" ".join(sys.argv[1:])]
        while True:
            if prompts:
                prompt = prompts.pop()
            elif len(sys.argv) > 1:
                break
            else:
                try:
                    prompt = input("\nyou> ").strip()
                except EOFError:
                    break
                if not prompt:
                    continue
            messages.append({"role": "user", "content": prompt})
            print("\n" + await run_turn(mcp_client, http, messages, tools))


if __name__ == "__main__":
    asyncio.run(main())
