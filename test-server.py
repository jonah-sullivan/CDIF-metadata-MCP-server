"""Smoke tests: talk to the server through a real MCP client, in memory."""
import asyncio

from fastmcp import Client

from DemoMcpServer import mcp

# What a typical catalogue landing page publishes: plain schema.org, no CDIF.
TYPICAL = {
    "@context": "https://schema.org/",
    "@type": "Dataset",
    "name": "Sea surface temperature, monthly",
    "description": "Gridded SST.",
    "url": "https://example.org/sst",
}


async def main():
    async with Client(mcp) as c:
        print("tools:", [t.name for t in await c.list_tools()])

        good = (await c.call_tool(
            "get_cdif_example", {"name": "copernicus-sea-ice.jsonld"})).data
        r = (await c.call_tool("validate_cdif", {"record": good})).data
        print("CDIF example ->", r["valid"], r["declared_conformance"])
        assert r["valid"]

        r = (await c.call_tool(
            "validate_cdif", {"example": "copernicus-sea-ice.jsonld"})).data
        print("validate by example name ->", r["valid"])
        assert r["valid"]

        for bad_args in ({}, {"example": "x.json", "url": "https://x"},
                         {"example": "../../../DemoMcpServer.py"},
                         {"url": "/etc/passwd"}):
            res = await c.call_tool("validate_cdif", bad_args,
                                    raise_on_error=False)
            assert res.is_error, bad_args
        print("bad validate_cdif arguments rejected")

        r = (await c.call_tool("validate_cdif", {"record": TYPICAL})).data
        print("plain schema.org ->", r["valid"], r["problem_count"], "problems")
        for p in r["problems"]:
            print("  ", p["path"], "|", p["message"][:110])
        assert not r["valid"]

        hits = (await c.call_tool(
            "search_cdif_guide", {"query": "subjectOf CatalogRecord"})).data
        print("guide search ->", [h["section"] for h in hits])
        assert hits

        # every example CODATA ships should pass
        names = (await c.call_tool("list_cdif_examples", {})).data
        bad = []
        for n in names:
            rec = (await c.call_tool("get_cdif_example", {"name": n})).data
            if not (await c.call_tool("validate_cdif", {"record": rec})).data["valid"]:
                bad.append(n)
        print(f"shipped examples: {len(names) - len(bad)}/{len(names)} valid", bad)

asyncio.run(main())