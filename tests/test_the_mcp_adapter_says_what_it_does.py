"""The MCP adapter over the skill registry: what it is, and what it is not.

`bot/mcp/server.py` wraps the bot's skills as typed MCP tools. It is NOT what
serves `POST /mcp` -- `app/routes/mcp.js` is, with its own registry, and it
names neither this module nor any `runeclaw_*` tool. The published integration
page said otherwise for as long as the adapter has existed, and the cost of
believing it is every call an integrator makes; that half is checked where it
can be measured, against the route, in
`app/test/the_published_mcp_tools_are_tools_the_route_answers.test.js`.

This file drives what is true of the adapter ITSELF, because a module nobody
reaches becomes defective in exactly the ways `market_cap`, `basis`,
`seasonality` and `quant_analyze` each did -- and this one had two:

  1. THE REDACTION WAS POINTED AT THE OTHER STRING. `_redact_string` scrubbed
     the traceback for the audit log and the caller's copy was a bare
     f-string of the exception, three lines below it. `quant_skill._safe_reason`
     is the same shape with a docstring promising the scrub, and the cure is
     the same one table.
  2. AN ACCEPTANCE IS A CLAIM. `_fullscan` advertised four modes over two
     behaviours: it branches on `mode == "quick"` and nothing else, so 'swing'
     and 'scalp' ran the identical whole-universe sweep and the reply echoed
     `"mode": "scalp"` back over it.

And it pins the three facts that make the door question a decision rather
than a wiring line, because a fix that lands only in prose is a fix a reader
can talk themselves out of.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import pathlib
from types import SimpleNamespace

import pytest

from tests.source_scan import code_only

ROOT = pathlib.Path(__file__).resolve().parent.parent
SERVER = ROOT / "bot" / "mcp" / "server.py"


@pytest.fixture
def adapter(monkeypatch):
    """A real server whose every skill raises, over a stub engine.

    The token is planted because the constructor is fail-closed by design and
    refuses to start without one -- which is the behaviour `live_e2e_test.py`
    reads the module constant to assert, and the only production import of
    this file.
    """
    monkeypatch.setenv("MCP_AUTH_TOKEN", "t" * 32)
    import importlib

    import bot.mcp.server as mod
    importlib.reload(mod)

    class _Raises:
        async def execute(self, engine, **kwargs):
            raise RuntimeError(
                "venue said no: https://api.bitget.com/api/v2/x?apiKey=LEAKED123"
                " Authorization: Bearer sk-ant-api03-Zm9vYmFyYmF6cXV4Cg")

    class _Registry:
        def get(self, name):
            return _Raises()

    srv = mod.RuneClawMCPServer(engine=SimpleNamespace(), registry=_Registry())
    return mod, srv, "t" * 32


def _call(srv, name, args, token):
    return asyncio.run(srv.call_tool(name, args, auth_token=token))


# ── 1. a failed read does not hand the caller the driver's message ──────────

class TestAFailedReadIsScrubbed:
    def test_the_callers_copy_carries_no_credential(self, adapter):
        _mod, srv, token = adapter
        r = _call(srv, "runeclaw_scan", {}, token)
        assert r["status"] == "error"
        blob = str(r)
        assert "LEAKED123" not in blob, (
            "the caller's error copy carries the query token the venue URL "
            "held. `_redact_string` was applied to the TRACEBACK for the log "
            "three lines above and never to this string.")
        assert "sk-ant-api03" not in blob, "a bare provider key reached the caller"
        assert "Bearer sk-" not in blob, (
            "the bearer credential reached the caller -- `app/lib/safe_error.js` "
            "redacted the word 'Bearer' and published the token after it, which "
            "is worse than a miss because the reader sees a marker")

    def test_it_still_says_something(self, adapter):
        """A scrub that erases the reason is a failure with no diagnosis."""
        _mod, srv, token = adapter
        r = _call(srv, "runeclaw_scan", {}, token)
        assert "Skill execution failed" in r["result"]
        assert "venue said no" in r["result"], (
            "the scrub removed the message as well as the secret; the caller "
            "now cannot tell a venue refusal from a bug in the adapter")

    def test_it_is_the_one_table_and_not_a_private_copy(self):
        """A second scrubber is a second answer about what a secret looks like.

        Driven rather than grepped for the name: patch the seam the module
        imported and read what comes out. A byte-identical private copy agrees
        with every fixture and diverges on the first edit to either.
        """
        import bot.mcp.server as mod
        assert mod._safe_exc_text is not None
        import bot.utils.exc_text as shared
        assert mod._safe_exc_text is shared._safe_exc_text, (
            "bot/mcp/server.py no longer scrubs through the shared table")


# ── 2. the advertised modes are the modes it runs ───────────────────────────

class TestTheModeVocabularyIsWhatItBranchesOn:
    def test_the_two_modes_the_function_does_not_run_are_refused(self, adapter):
        mod, srv, token = adapter
        for dead in ("swing", "scalp"):
            r = _call(srv, "runeclaw_fullscan", {"mode": dead}, token)
            assert r["status"] == "error", (
                f"mode={dead!r} is accepted and `_fullscan` branches only on "
                "'quick', so it ran the whole-universe sweep and the reply "
                f'echoed "mode": "{dead}" back over it')
            assert "Invalid mode" in r["result"]

    def test_the_refusal_names_the_modes_that_exist(self, adapter):
        mod, srv, token = adapter
        r = _call(srv, "runeclaw_fullscan", {"mode": "nope"}, token)
        for word in sorted(mod._FULLSCAN_MODES):
            assert word in r["result"], (
                "the refusal does not name the vocabulary, so a caller learns "
                "their word was wrong and not which words are right")

    def test_the_accepted_set_is_the_catalogue_description(self, adapter):
        """One vocabulary, two readers. A literal in the validator is the
        second answer that let two dead modes in."""
        mod, _srv, _token = adapter
        src = code_only(inspect.getsource(mod.RuneClawMCPServer.call_tool))
        assert "_FULLSCAN_MODES" in src, (
            "call_tool validates `mode` against something other than the "
            "vocabulary the description is written from")
        tree = ast.parse(src.strip())
        literals = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Set)
            and any(isinstance(e, ast.Constant) and e.value in ("quick", "swing")
                    for e in n.elts)
        ]
        assert literals == [], (
            "call_tool carries its own set literal of scan modes again")

    def test_the_branch_really_only_knows_quick(self, adapter):
        """THE CONTROL. If `_fullscan` ever grows a real 'swing' branch, the
        narrowing above is wrong and should be revisited rather than worked
        around."""
        mod, _srv, _token = adapter
        src = code_only(inspect.getsource(mod.RuneClawMCPServer._fullscan))
        for dead in ("swing", "scalp"):
            assert dead not in src, (
                f"`_fullscan` now knows {dead!r} -- it may be advertised "
                "again, and this file needs rewriting rather than working "
                "around")


# ── 3. the universe count is read, not typed ────────────────────────────────

class TestTheUniverseIsCounted:
    def test_the_description_states_the_list_it_sweeps(self, adapter):
        mod, _srv, _token = adapter
        from bot.skills.scan_skill import UNIVERSE
        row = next(t for t in mod.TOOL_CATALOGUE
                   if t.mcp_name == "runeclaw_fullscan")
        assert f"{len(UNIVERSE)}-symbol" in row.description, (
            "the fullscan description no longer states the size of the list it "
            "sweeps -- `DeepScanSkill.description` carried a stale '67+ "
            "symbols' against a universe of 115 for years")

    def test_it_is_NOT_the_deepscan_universe(self):
        """Two lists, two counts, and the difference is legitimate.

        `scan_skill.UNIVERSE` is what `_fullscan` sweeps;
        `deepscan_universe_size()` counts `DEEPSCAN_UNIVERSE + TRADFI_PERPETUALS`.
        Reading the second as a correction to the first would have replaced a
        true number with a wrong one -- check reachability before fixing.
        """
        from bot.skills.scan_skill import UNIVERSE
        from bot.skills.skill_registry import deepscan_universe_size
        assert len(UNIVERSE) != deepscan_universe_size(), (
            "the two universes have converged; the guard above no longer "
            "distinguishes them and the comment in server.py should be re-read")

    def test_the_description_is_DERIVED_and_not_a_typed_number(self, adapter):
        """A SOURCE read, and the narrow case where that is the honest one.

        The count is baked into the catalogue at import, so a driven check
        cannot tell a derived 67 from a typed 67 -- both render the same
        string. Only the source says where the number came from, which is why
        this reads it, and the whole point is that a number a list decides
        must not be typed beside the list.
        """
        mod, _srv, _token = adapter
        body = code_only(SERVER.read_text(encoding="utf-8"))
        start = body.index('mcp_name="runeclaw_fullscan"')
        block = body[start:body.index("MCPToolDef(", start + 1)]
        assert "_universe_phrase()" in block, (
            "the fullscan description no longer interpolates the counted "
            "universe -- a digit typed here is the `67+ symbols` shape, which "
            "sat stale in `DeepScanSkill.description` for years")
        import re as _re
        assert not _re.search(r"\b\d{2,3}[- ]symbol", block), (
            "a symbol count is typed into the fullscan catalogue row again")

    def test_a_universe_that_will_not_import_does_not_take_the_module_with_it(
            self, adapter, monkeypatch):
        """The branch the mutation round found nothing driving.

        `_scan_universe` imports `scan_skill` inside the function because that
        module pulls the engine in, and this file is imported at module scope
        by `live_e2e_test.py` to read `_MCP_AUTH_TOKEN` — the one production
        import there is. Its `except` is what keeps that import working when
        the skill tree cannot load, and with `scan_skill` importable in every
        fixture, swapping the `return ()` for a bare `raise` changed no verdict
        anywhere: the round reporting a coverage gap rather than a code one.
        Planted, because no ordinary input reaches it.
        """
        import sys
        mod, _srv, _token = adapter
        monkeypatch.setitem(sys.modules, "bot.skills.scan_skill", None)
        assert mod._scan_universe() == (), (
            "a skill tree that will not import takes bot/mcp/server.py's own "
            "import down with it, and the fail-closed token check that is the "
            "module's only production reader goes with it")

    def test_an_unreadable_universe_is_not_a_count_of_zero(self, adapter):
        mod, _srv, _token = adapter
        assert mod._universe_phrase.__doc__
        real = mod._FULLSCAN_UNIVERSE
        try:
            mod._FULLSCAN_UNIVERSE = ()
            phrase = mod._universe_phrase()
        finally:
            mod._FULLSCAN_UNIVERSE = real
        assert "0-symbol" not in phrase, (
            "a universe nobody could read renders as a sweep of zero symbols")
        assert "not readable" in phrase


# ── 4. the three facts that make the door a decision ────────────────────────

class TestWhatWouldHaveToBeDecided:
    def test_no_caller_identity_reaches_any_skill(self, adapter):
        """One shared bearer token, and nothing per-caller travels with it.

        Stated as a driven fact rather than as prose, because the doc and this
        module's docstring both rest on it: every read a wired adapter served
        would be the OPERATOR's book.
        """
        mod, _srv, _token = adapter
        sig = inspect.signature(mod.RuneClawMCPServer.call_tool)
        assert set(sig.parameters) == {"self", "name", "arguments", "auth_token"}, (
            "call_tool's signature changed -- if it now takes a caller, the "
            "'no identity' half of the door question is answered and the "
            "docstring should say so")
        src = code_only(inspect.getsource(mod.RuneClawMCPServer.call_tool))
        assert "user_id" not in src

    def test_the_two_account_tools_are_the_ones_that_would_leak(self, adapter):
        """Which rows the dollar rule lands on, named rather than remembered."""
        mod, _srv, _token = adapter
        skills = {t.mcp_name: t.skill_name for t in mod.TOOL_CATALOGUE}
        assert skills["runeclaw_portfolio"] == "get_portfolio"
        assert skills["runeclaw_risk"] == "check_risk"

    def test_no_surface_presents_MCP_ALLOW_EXECUTE_as_a_switch(self):
        """A name two surfaces gave an operator, that nothing reads.

        Two-way, like a baseline: the day something READS it, this fails and
        the wording becomes true rather than being kept false by a guard.
        """
        # BOTH RUNTIMES, and `tests/` is excluded for the reason
        # `honesty_gate.py` gives about its own scope: a test PLANTS the name
        # to prove something about it, and the first run of this guard
        # reported ITSELF as the reader -- the string above is code, so
        # `code_only` keeps it, which is the right answer to the wrong
        # question.
        readers = []
        for pat in ("bot/**/*.py", "scripts/**/*.py", "app/**/*.js"):
            for path in ROOT.glob(pat):
                if "__pycache__" in str(path) or "node_modules" in str(path):
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                    body = code_only(text) if path.suffix == ".py" else text
                except (OSError, UnicodeDecodeError, SyntaxError):
                    continue
                if "MCP_ALLOW_EXECUTE" in body:
                    readers.append(str(path.relative_to(ROOT)))
        if readers:
            pytest.fail(
                f"MCP_ALLOW_EXECUTE now has a reader ({readers}) -- it is a "
                "switch, and bot/mcp/server.py's comment plus "
                "mcp-integration.md may describe it as one again")

        # The doc's own voice, with what it QUOTES removed. This slice's
        # correction has to name what it corrected -- it says the paragraph
        # used to read "gated behind `MCP_ALLOW_EXECUTE=true` and caller
        # auth" -- so a naive scan matches the fix and reports it as the
        # defect, which the sibling guard's `_unquoted` exists for and which
        # this file's first draft avoided only by accident of line wrapping.
        import re as _re
        doc = (ROOT / "docs" / "gitbook" / "mcp-integration.md").read_text()
        own_voice = _re.sub(r'"[^"\n]*"', " ", doc)
        assert not _re.search(r"gated behind\s+`MCP_ALLOW_EXECUTE", own_voice), (
            "mcp-integration.md tells an operator to set MCP_ALLOW_EXECUTE and "
            "nothing in either runtime reads it")
        assert "no switch that re-enables it" in doc, (
            "the doc no longer says the flag is a name for a decision rather "
            "than a knob")

        # THE COMMENT IS A SURFACE TOO, and it is where the doc's sentence came
        # from. Read in the source's own voice -- this slice's retraction has
        # to quote what it retracted, so a bare token search matches the fix.
        server_voice = _re.sub(r'"[^"\n]*"', " ", SERVER.read_text(encoding="utf-8"))
        assert not _re.search(r"[Rr]e-enable\s+only\s+behind\s+MCP_ALLOW_EXECUTE",
                              server_voice), (
            "bot/mcp/server.py's catalogue comment tells the next developer to "
            "set MCP_ALLOW_EXECUTE, and nothing in either runtime reads it")


# ── 5. the module says what it is ───────────────────────────────────────────

def test_the_module_docstring_states_it_has_no_http_door():
    """The first thing a reader reads is what the doc got wrong.

    A docstring that opened "exposes the skill registry as MCP-callable tools
    for the Bitget Agent Hub" is where the published page's claim came from.
    """
    import bot.mcp.server as mod
    doc = mod.__doc__ or ""
    assert "no HTTP door" in doc
    assert "Unknown tool" in doc, (
        "the docstring no longer states what POST /mcp answers for these names")
