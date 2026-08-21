"""Numbers written into prose, pinned against the thing they describe.

`tests/test_claude_md_accuracy.py` already does this for CLAUDE.md's gate
count, and its reason generalises: a number in prose is the part that rots
first, and a document that confidently states a stale one is the same defect as
a panel printing a stale figure.

Two had rotted, found 2026-08-21 while auditing the agent-facing surface:

    docs/INTEROP.md         "POST /mcp (17 read-only tools)"
                            -> the registry holds 30 read-only + 3 Arena
    .github/workflows/ci.yml "carries 1 critical and 15 high advisories today"
                            -> token/.audit-baseline.json records 0 critical,
                               9 high, and the gate prints that on every run

Neither was load-bearing on its own. Together they are the reason the discovery
document this sweep added computes every count from the live registry instead
of writing one down: `/.well-known/mcp.json` is read by MACHINES that will act
on it, so it is the last place a hand-maintained literal belongs.

Deliberately NOT a general "find every number in every doc" scan. That would
drown in false positives — version strings, dates, port numbers, prices in
examples. These are specific claims about specific machine-readable sources,
each pinned to its own source of truth.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
INTEROP = REPO / "docs" / "INTEROP.md"
CI = REPO / ".github" / "workflows" / "ci.yml"
MCP_JS = REPO / "app" / "routes" / "mcp.js"
AUDIT_BASELINE = REPO / "token" / ".audit-baseline.json"


def _registry_counts() -> tuple:
    """(read_only, write) counted from mcp.js's own two registries.

    Parsed from source rather than imported: this is a pytest suite and the
    registry is JavaScript. Extract the object literal by brace-matching, then
    count its top-level `  name: {` entries.

    The first draft counted entries while walking, testing `depth == 1` AFTER
    folding in the current line's braces — so `  arena_open: {` was seen at
    depth 2 and never counted, and WRITE_TOOLS came back 0. Every count
    assertion below would then have compared a document against zero and
    passed only when the document was also wrong.
    `TestTheRegistryParseIsNotVacuous` is what caught it, which is the whole
    reason a parser gets a test before the things it parses do.
    """
    src = MCP_JS.read_text(encoding="utf-8")

    def block(decl: str) -> str:
        start = src.index(decl) + len(decl) - 1   # position of the opening `{`
        depth = 0
        for i in range(start, len(src)):
            if src[i] == "{":
                depth += 1
            elif src[i] == "}":
                depth -= 1
                if depth == 0:
                    return src[start:i + 1]
        raise AssertionError(f"unterminated object literal for {decl!r}")

    def count(decl: str) -> int:
        return len(re.findall(r"^  [a-z][a-z0-9_]*:\s*\{", block(decl), re.M))

    return count("const TOOLS = {"), count("const WRITE_TOOLS = {")


class TestTheRegistryParseIsNotVacuous:
    """A parser that returns 0 would make every count assertion below pass
    against any document at all. Prove it finds real tools first."""

    def test_it_finds_a_plausible_number_of_tools(self):
        read_only, write = _registry_counts()
        assert read_only >= 20, f"only parsed {read_only} read-only tools"
        assert write >= 1, f"only parsed {write} key-gated tools"

    def test_it_finds_the_arena_tools_as_key_gated(self):
        src = MCP_JS.read_text(encoding="utf-8")
        write_block = src[src.index("const WRITE_TOOLS = {"):]
        for name in ("arena_open", "arena_close", "arena_my_positions"):
            assert re.search(rf"^  {name}:", write_block, re.M), name


class TestInteropStatesTheRealToolCount:
    def test_the_read_only_count_matches_the_registry(self):
        read_only, _ = _registry_counts()
        text = INTEROP.read_text(encoding="utf-8")
        m = re.search(r"\((\d+) read-only tools", text)
        assert m, "INTEROP.md no longer states a read-only tool count"
        assert int(m.group(1)) == read_only, (
            f"INTEROP.md says {m.group(1)} read-only tools; the registry has "
            f"{read_only}. Update the doc, not this test."
        )

    def test_the_key_gated_count_matches_the_registry(self):
        _, write = _registry_counts()
        text = INTEROP.read_text(encoding="utf-8")
        m = re.search(r"(\d+) key-gated Arena tools", text)
        assert m, "INTEROP.md no longer states a key-gated tool count"
        assert int(m.group(1)) == write, (
            f"INTEROP.md says {m.group(1)} key-gated tools; the registry has "
            f"{write}."
        )

    def test_it_points_agents_at_the_discovery_document(self):
        """The surface was undiscoverable until this sweep; the doc that
        describes it should say where the front door is."""
        assert "/.well-known/mcp.json" in INTEROP.read_text(encoding="utf-8")


class TestCiCommentMatchesTheRecordedBaseline:
    """The npm advisory numbers in ci.yml's comment, against the baseline file
    the gate actually reads. No network: both sources are in the repo."""

    @pytest.fixture
    def counts(self):
        if not AUDIT_BASELINE.exists():
            pytest.skip("token/.audit-baseline.json not present")
        return json.loads(AUDIT_BASELINE.read_text(encoding="utf-8")).get("counts", {})

    def test_the_critical_and_high_counts_are_current(self, counts):
        text = CI.read_text(encoding="utf-8")
        m = re.search(r"carries (\d+) critical and (\d+) high advisories", text)
        assert m, "ci.yml no longer states the advisory counts"
        assert int(m.group(1)) == counts.get("critical"), (
            f"ci.yml says {m.group(1)} critical; the baseline records "
            f"{counts.get('critical')}")
        assert int(m.group(2)) == counts.get("high"), (
            f"ci.yml says {m.group(2)} high; the baseline records "
            f"{counts.get('high')}")

    def test_the_total_is_current(self, counts):
        text = CI.read_text(encoding="utf-8")
        m = re.search(r"\((\d+) in total\)", text)
        assert m, "ci.yml no longer states a total"
        assert int(m.group(1)) == sum(counts.values()), (
            f"ci.yml says {m.group(1)} total; the baseline sums to "
            f"{sum(counts.values())}")


class TestTheDiscoveryDocumentDerivesItsCounts:
    """The point of the whole file, asserted structurally.

    A discovery document is machine-read and acted on, so a hand-written count
    in it is worse than one in prose. This pins that the counts come from the
    registry rather than from literals — a future edit that "simplifies" them
    into constants reintroduces exactly the rot being fixed here.
    """

    def test_it_counts_the_live_registries(self):
        src = (REPO / "app" / "routes" / "discovery.js").read_text(encoding="utf-8")
        assert "Object.keys(m.TOOLS || {}).length" in src
        assert "Object.keys(m.WRITE_TOOLS || {}).length" in src

    def test_it_hardcodes_no_tool_total(self):
        """Comments and the prose in `description` are stripped first: this
        file explains the 33/30/3 problem in its own header, and a scan that
        cannot tell that from code is the trap CLAUDE.md opens with."""
        src = (REPO / "app" / "routes" / "discovery.js").read_text(encoding="utf-8")
        code = re.sub(r"/\*[\s\S]*?\*/", "", src)
        code = "\n".join(re.sub(r"//.*$", "", ln) for ln in code.splitlines())
        code = re.sub(r"'[^'\n]*'|\"[^\"\n]*\"|`[^`]*`", "''", code)
        for literal in ("33", "30"):
            assert literal not in code, (
                f"discovery.js hardcodes {literal}; counts must derive from "
                "the registry")
