"""The generated crosswalk documentation must match the code."""

import importlib.util
from pathlib import Path

from crosswalk import MAPPINGS
from crosswalk.codata import REFERENCES

ROOT = Path(__file__).resolve().parent.parent


def test_every_mapping_has_a_codata_reference():
    assert {m.cdif for m in MAPPINGS} == set(REFERENCES)


def test_crosswalk_docs_up_to_date():
    spec = importlib.util.spec_from_file_location(
        "gen_crosswalk_docs", ROOT / "scripts" / "gen_crosswalk_docs.py"
    )
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    assert gen.OUT.read_text("utf-8") == gen.render(), (
        "docs/iso19115-cdif-crosswalk.md is stale: run "
        "python scripts/gen_crosswalk_docs.py"
    )
