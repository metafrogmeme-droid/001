"""The MCP doc described a product that had not shipped, and one that had.

`docs/gitbook/mcp-integration.md` is published on GitBook and is what an agent
developer reads before integrating. It said three things that were false, all in
the same direction until the last one:

  1. "Skill registry (internal) | Implemented -- 12 skills registered"
     The default registry builds THIRTY skills.

  2. "MCP tool adapter layer | Planned -- architecture ready, adapter not yet
     written" and "The MCP adapter is not yet implemented as production code."
     `bot/mcp/server.py` builds JSON Schema tool definitions from
     `TOOL_CATALOGUE` and dispatches `call_tool` into the registry;
     `app/routes/mcp.js` mounts it at POST /mcp. It shipped in #69.

  3. "Expose all 12 skills as MCP tools", listed under Future.
     Nine are exposed today. The target was already wrong twice over: wrong
     count, and not actually future.

  4. "Require human confirmation for any trade execution (even via MCP)."
     `auto_confirm_live_enabled` defaults True, so a signal clearing the bar
     places a live order with nobody pressing anything. This was the FIFTH
     surface carrying that claim after the homepage, the meta description, the
     JSON-LD and llms.txt.

The first three UNDERSTATE the product, which is the unusual direction and the
reason it survived: nobody re-reads a doc to check whether it is being too
modest. The fourth overstates a safety property, which is the direction this
repo is organised around catching.

WHY NO NEW COUNT REPLACES THE OLD ONE. `_TOTAL_RISK_CHECKS = 23` drifted against
an engine emitting thirty-six labels and was asserted on eleven surfaces at
once. Both the registry and the catalogue are enumerable at runtime; a number
typed into a document is a second, staler copy of something already knowable.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "gitbook" / "mcp-integration.md"


def _unquoted(text: str) -> str:
    """The doc's own voice, with anything it QUOTES removed.

    The correction has to name what it corrected — it says the row used to read
    "12 skills registered" — so a naive scan matches the fix and reports it as
    the defect. Four false failures in this repo have come from exactly that,
    and `site/test/privacy_truth.test.js` strips quoted spans for the same
    reason.

    Blockquote lines go too: the explanatory note lives in one, and its whole
    job is to state the old numbers.
    """
    out = re.sub(r'"[^"\n]*"', " ", text)
    out = re.sub(r"`[^`\n]*`", " ", out)
    out = "\n".join(l for l in out.split("\n") if not l.lstrip().startswith(">"))
    return out


def _doc() -> str:
    assert DOC.exists(), f"{DOC} is gone — the guard is measuring nothing"
    return DOC.read_text(encoding="utf-8")


def test_the_doc_states_no_skill_or_tool_count():
    """A digit in front of "skills" or "tools" is a hand-maintained total."""
    hits = re.findall(r"(?<![.\d])\b\d{1,3}\s+(?:skills?|tools?)\b",
                      _unquoted(_doc()), re.IGNORECASE)
    assert hits == [], (
        f"mcp-integration.md states a skill/tool count ({hits[:3]}). The "
        "registry and TOOL_CATALOGUE are both enumerable at runtime; a number "
        "here is a second, staler copy that drifts in one direction — this row "
        "said 12 while the registry held 30.")


def test_the_doc_does_not_call_the_shipped_adapter_planned():
    """It shipped. Saying otherwise costs an integrator the integration."""
    doc = _unquoted(_doc()).lower()
    for phrase in ("adapter not yet written",
                   "adapter is not yet implemented",
                   "mcp adapter is not yet"):
        assert phrase not in doc, (
            f"the doc says {phrase!r}, and bot/mcp/server.py plus "
            "app/routes/mcp.js mount a live JSON-RPC surface at POST /mcp")


def test_the_adapter_really_is_there_before_the_doc_claims_it():
    """THE CONTROL, and it runs the other way round.

    The two tests above would both pass if the adapter were deleted and the doc
    left claiming it — which would be the worse error. This asserts the code the
    doc now points at actually exists, so the claim and the thing move together.
    """
    server = ROOT / "bot" / "mcp" / "server.py"
    assert server.exists(), "bot/mcp/server.py is gone but the doc claims it"
    src = server.read_text(encoding="utf-8")
    assert "TOOL_CATALOGUE" in src and "async def list_tools" in src, (
        "the MCP server no longer builds a tool catalogue")
    mount = (ROOT / "app" / "server.js").read_text(encoding="utf-8")
    assert "app.use('/mcp'" in mount, (
        "POST /mcp is no longer mounted, so the doc promises a surface that is "
        "not served")


def test_the_doc_does_not_promise_human_confirmation():
    """Fifth surface. `auto_confirm_live_enabled` defaults True."""
    doc = _unquoted(_doc())
    hits = re.findall(r"human[- ]confirm\w*|human-in-the-loop", doc, re.IGNORECASE)
    assert hits == [], (
        f"mcp-integration.md promises {hits[:2]} — bot/config.py defaults "
        "auto_confirm_live_enabled to True, so a signal clearing the confidence "
        "bar places a live order with nobody pressing anything")


def test_the_default_really_is_auto_confirm_so_this_guard_is_not_stale():
    """If the default ever flips, the claim becomes true and this guard should
    be revisited rather than silently keeping a true sentence out of the doc."""
    cfg = (ROOT / "bot" / "config.py").read_text(encoding="utf-8")
    assert re.search(r"auto_confirm_live_enabled[^\n]*_env_bool\([^\n]*True", cfg), (
        "auto_confirm_live_enabled no longer defaults True — human confirmation "
        "may now be accurate, and the doc guard above should be reconsidered "
        "instead of assumed")


def test_the_tool_map_is_the_catalogue_row_for_row():
    """The table listed `runeclaw_execute`, which the catalogue deliberately
    EXCLUDES — the one tool whose absence is a security decision, advertised
    as present — and omitted two tools the catalogue carries. Names are
    matched exactly, both ways, and no count is involved."""
    from bot.mcp.server import TOOL_CATALOGUE
    in_table = re.findall(r"^\|\s*`(runeclaw_\w+)`\s*\|", _doc(), re.MULTILINE)
    assert in_table, "the tool map table is gone from mcp-integration.md"
    assert sorted(in_table) == sorted(t.mcp_name for t in TOOL_CATALOGUE), (
        "docs/gitbook/mcp-integration.md and TOOL_CATALOGUE disagree about "
        "which tools exist — fix whichever is wrong, not the test")
    assert "runeclaw_execute" not in in_table


@pytest.mark.parametrize("must_say", [
    "fail-closed",
    "risk gate",
])
def test_the_guarantees_that_ARE_true_are_still_stated(must_say):
    """Removing a false claim must not become removing the true ones beside it.

    The risk gate genuinely runs on every interface, and an agent developer
    deciding whether to trust this surface needs to read that.
    """
    assert must_say.lower() in _doc().lower(), (
        f"mcp-integration.md no longer mentions {must_say!r} — the false "
        "confirmation claim had to go; the real guarantees did not")
