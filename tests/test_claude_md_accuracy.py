"""CLAUDE.md is read before anyone looks at the code, so it must be true.

It exists because scripts/preflight.py was undiscoverable: nothing in the
repo pointed at it, so the next session would have rebuilt the same wrong
habit — running a subset of CI and reporting it as the whole.

A document that is read FIRST is the worst place for a stale claim, because
everything after it is interpreted through it. The runbook demonstrated the
failure mode a day earlier: it described `build` as the answer to "did my fix
land?", which stopped being true when a fix shipped entirely in public/js.
Correct when written, wrong by the time it mattered.

So the checkable claims are pinned. Every command it gives runs, every file it
points at exists, every rule it states is one the suite actually enforces.
"""

import json
import pathlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOC = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")


# ── every path it names exists ────────────────────────────────────────────

@pytest.mark.parametrize("rel", [
    "scripts/preflight.py",
    "scripts/ci_test_gate.py",
    "tests/known_failures.txt",
    "app/test/panel_failure_honesty.test.js",
    "app/test/mcp_public_records.test.js",
    "app/test/dashboard_social.test.js",
    "tests/test_preflight_matches_ci.py",
    "docs/LIVE_HARDENING_RUNBOOK.md",
    "scripts/cloudflared/",
    ".github/workflows/ci.yml",
    "app/lib/version.js",
])
def test_a_referenced_path_exists(rel):
    assert rel in DOC, f"{rel} dropped out of CLAUDE.md"
    assert (ROOT / rel).exists(), f"CLAUDE.md points at a missing path: {rel}"


def test_no_path_it_names_is_missing():
    """The parametrised list above is curated; this catches anything added to
    the doc later without a test."""
    referenced = set(re.findall(r"`((?:app|bot|docs|scripts|tests)/[\w./-]+)`", DOC))
    missing = sorted(p for p in referenced if not (ROOT / p).exists())
    assert missing == [], f"CLAUDE.md points at missing paths: {missing}"


# ── every command it gives actually works ─────────────────────────────────

def test_the_preflight_invocations_are_real():
    for flag in ("--fast", "--list"):
        assert f"scripts/preflight.py {flag}" in DOC
    r = subprocess.run([sys.executable, "scripts/preflight.py", "--help"],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0
    for flag in ("--fast", "--list"):
        assert flag in r.stdout, f"{flag} is documented but not accepted"


def test_the_fingerprint_command_prints_both_values():
    m = re.search(r'node -e "(.+?)"', DOC)
    assert m, "the deploy-verification command is gone"
    r = subprocess.run(["node", "-e", m.group(1)], cwd=ROOT,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    parts = r.stdout.split()
    assert len(parts) == 2, f"expected build and assets, got {r.stdout!r}"
    for p in parts:
        assert re.fullmatch(r"[0-9a-f]{12}\+\d+", p), p


def test_the_gate_count_it_quotes_is_the_real_one():
    """"Eight gates" is a number someone will trust rather than count."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import preflight
    # `[\w-]+`, not `\w+`: a hyphen is not a word character, so "Twenty-one
    # gates:" matched the group as "one" and the count silently compared 1
    # against 21. The parser has to reach the whole numeral it is checking.
    m = re.search(r"([\w-]+) gates:", DOC)
    assert m, "the gate count sentence is gone"
    words = {"six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
             "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
             "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
             "nineteen": 19, "twenty": 20, "twenty-one": 21, "twenty-two": 22,
             "twenty-three": 23, "twenty-four": 24, "twenty-five": 25}
    claimed = words.get(m.group(1).lower())
    # Refusing an unknown word rather than skipping is the point: `.get()`
    # returning None and the test passing anyway would mean the doc could say
    # "Eleven" while the table stopped at ten, and the count would go unchecked
    # exactly when it changed. The red-team gate made it eleven and this line
    # is what said so.
    assert claimed is not None, f"unparsed count word: {m.group(1)}"
    assert claimed == len(preflight.steps(fast=False)), (
        f"CLAUDE.md says {claimed} gates, preflight plans "
        f"{len(preflight.steps(fast=False))}")


def test_the_jobs_it_says_are_uncovered_really_are():
    sys.path.insert(0, str(ROOT / "scripts"))
    import preflight
    uncovered = " ".join(preflight.uncovered()).lower()
    for word in ("cargo", "solidity", "gitleaks", "token tooling"):
        assert word in uncovered, f"CLAUDE.md claims {word} is uncovered; it is not"


# ── every rule it states is one the suite enforces ────────────────────────

def test_the_honesty_rule_has_a_guard_behind_it():
    assert "Unreadable is never zero" in DOC
    guard = (ROOT / "app" / "test" / "panel_failure_honesty.test.js").read_text(
        encoding="utf-8")
    assert "renderPanel" in guard and "mustRead" in guard, (
        "the doc cites this file as the structural enforcement — it must "
        "still be that")


def test_the_honesty_backlog_it_quotes_is_the_real_one():
    """A number in prose is the part that rots first.

    Same rule as the gate count above and the unreachable-module count: the
    doc says how big the baselined backlog is, and a reader will trust that
    rather than open the file. It moves whenever the ratchet moves.
    """
    m = re.search(r"two-way\s+ratchet on ([\d,]+) hits", DOC)
    assert m, "the honesty-ratchet backlog sentence is gone"
    baseline = json.loads((ROOT / "tests" / "honesty_baseline.json")
                          .read_text(encoding="utf-8"))
    assert int(m.group(1).replace(",", "")) == baseline["total"], (
        f"CLAUDE.md says {m.group(1)} baselined hits; the baseline records "
        f"{baseline['total']}")


def test_the_shapes_it_says_are_uncovered_really_are():
    """The doc states the gate's coverage. An overstated one is the failure
    the gate exists to prevent, so the two halves have to agree."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import honesty_gate
    assert "Python only" in DOC and "skips `tests/`" in DOC
    assert "tests" not in honesty_gate.ROOTS
    assert "Five of the eight shapes" in honesty_gate.__doc__


def test_the_three_look_alikes_it_clears_are_still_clear():
    """The doc names three sites that LOOK like the absent-is-zero bug and
    are not, so a future reader can skip them. If one ever becomes the bug,
    that sentence stops being a time-saver and starts steering someone past
    a real defect -- the most expensive kind of wrong documentation.

    The third (the paper Trade's atomic close) has its own file. These two
    do not, so they are pinned here.
    """
    assert "None of them are." in DOC

    track = (ROOT / "app" / "routes" / "track.js").read_text(encoding="utf-8")
    assert "isFinite(parseFloat(t.pnl))" in track, (
        "the doc says track.js filters on isFinite upstream, which is why "
        "its `|| 0` is unreachable — if that filter is gone the `|| 0` is "
        "now live and the doc is telling people to ignore it"
    )

    db = (ROOT / "app" / "db.js").read_text(encoding="utf-8")
    i = db.index("CREATE TABLE IF NOT EXISTS arena_trades")
    schema = db[i:i + 1200]
    assert "pnl DOUBLE NOT NULL" in schema, (
        "the doc says arena_trades.pnl is NOT NULL, which is why the public "
        "Arena win rate cannot meet an unpriced close — if it became "
        "nullable that surface needs the same treatment the others got"
    )


def test_the_baseline_gate_behaviour_it_describes_is_real():
    assert "starts *passing* is a hard failure" in DOC
    gate = (ROOT / "scripts" / "ci_test_gate.py").read_text(encoding="utf-8")
    assert "known_failures" in gate
    assert "HARD failure" in gate or "hard failure" in gate


def test_the_comment_stripping_helper_it_recommends_exists():
    assert "code_only()" in DOC
    src = (ROOT / "tests" / "test_preflight_matches_ci.py").read_text(encoding="utf-8")
    assert "def code_only(" in src and "tokenize" in src


def test_it_does_not_tell_anyone_to_run_a_bare_pytest():
    """The trap it exists to close: `pytest` alone skips the baseline gate,
    both ruff passes, mypy, bandit, pip-audit and the web suite."""
    assert "Do not" in DOC and "bare `pytest`" in DOC
    # and it must not contradict itself elsewhere
    assert not re.search(r"^\s*(?:\$ )?python3? -m pytest", DOC, re.M)


def test_the_recorded_call_sites_are_the_number_it_claims():
    """A number in prose is the part that rots first. Counted from the source
    the claim is about, not from memory."""
    import ast
    import inspect
    import textwrap

    m = re.search(r"Fifty-one call sites across the two entry points", DOC)
    assert m, "the claim was reworded; recount it"
    import bot.skills.telegram_handler as th
    from bot.web import user_gateway as ug
    tg = textwrap.dedent(inspect.getsource(th.TelegramHandler._handle_message))
    web = inspect.getsource(ug._chat_turn)
    n = sum(ast.unparse(c.func).endswith(("_remember_routed", "record_routed_turn"))
            for src in (tg, web)
            for c in ast.walk(ast.parse(src))
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute | ast.Name))
    assert n == 51, f"CLAUDE.md says fifty-one; the two entry points have {n}"


def test_the_catalogue_numbers_are_the_numbers_a_drive_returns():
    """The paragraph's own confession: the first draft wrote 79/10/5 from an
    earlier walk and could not reproduce it. Read the claim OUT of the prose
    and compare it to the drive, so a reworded sentence fails rather than
    quietly carrying a stale number."""
    from tests.test_the_bot_can_say_what_it_does import catalogue_on_the_web

    m = re.search(r"`_cmd_help` names (\d+) slash\n?commands for a non-admin",
                  DOC)
    assert m, "the catalogue claim was reworded; recount it"
    named, nothing, hits = catalogue_on_the_web()
    assert int(m.group(1)) == named

    # The total is read here too, never restated: a literal `90` in this
    # pattern is a second copy of the number the line above just measured,
    # and the second copy is what goes stale. It did — the sentence said
    # "79 of the 91" while this regex still demanded "of the 90", so the
    # guard failed on its own staleness rather than on the prose's.
    m2 = re.search(r"(\d+) of the (\d+) reach the tool-less chat model and (\d+)\n?"
                   r"reach a skill", DOC)
    assert m2, "the fall-through claim was reworded; recount it"
    assert int(m2.group(2)) == named, "the sentence disagrees with itself"
    assert (int(m2.group(1)), int(m2.group(3))) == (nothing, len(hits))
    assert "`/scan`, whose whole job is the" in DOC
    assert hits.get("scan") == "analyze_asset", hits


def test_the_capability_answer_is_derived_from_the_permission_table():
    """"a COLUMN on the permission table rather than a map in the renderer".

    The claim is the EQUALITY, and it is the thing that makes the derivation
    safe: a skill added later fails the table's own guard instead of vanishing
    from the answer to "what can you do?".
    """
    from bot.skills.skill_permissions import SKILL_PERMISSION, SKILL_SAYS

    assert "a COLUMN on the permission table rather than a map in the renderer" in DOC
    assert set(SKILL_SAYS) == set(SKILL_PERMISSION)


def test_the_seam_it_names_has_the_callers_it_claims():
    """"the free-text, vision and public paths all read it".

    `defang_if_flagged` having ONE caller is the defect this paragraph is
    about, so a claim that a seam is shared must be counted, not remembered.
    """
    import ast
    import inspect

    from bot.web import user_gateway as ug
    from tests.source_scan import code_only

    # Whitespace-NORMALISED, because the claim wraps across two lines in the
    # markdown and a wrapped phrase is not one substring — the "asserting a
    # short string" misfire, in the present direction: the first draft of
    # this pin failed on prose that was there.
    flat = re.sub(r"\s+", " ", DOC)
    assert "the free-text, vision and public paths all read it" in flat
    names = {"hardened_prompt", "_harden_v", "_harden_pub"}
    calls = sum(
        1 for n in ast.walk(ast.parse(code_only(inspect.getsource(ug))))
        if isinstance(n, ast.Call)
        and (n.func.id if isinstance(n.func, ast.Name)
             else getattr(n.func, "attr", "")) in names)
    assert calls >= 3, f"the web has {calls} caller(s) of the shared seam"


def test_the_firewall_defaults_are_the_way_round_it_says():
    """The paragraph corrects a comment that named the wrong half as off. If
    the defaults ever flip, the correction becomes the new false statement."""
    from bot.config import CONFIG

    flat = re.sub(r"\s+", " ", DOC)
    assert "`guardian_firewall_enabled` defaults to **True**" in flat
    assert getattr(CONFIG.risk, "guardian_firewall_enabled", None) is True
    assert getattr(CONFIG.risk, "guardian_firewall_block_high", None) is False


def test_the_outbound_seam_is_at_a_boundary_not_a_call_site_list():
    """"a middleware and the `_sse_frame` builder — one place for every JSON
    route and one for every streamed frame".

    The claim is that new code inherits the scrub. That is only true while
    the seam sits at the boundary, so it is counted rather than trusted: one
    middleware, installed, and one scrub inside the frame builder.
    """
    import inspect

    from bot.web import user_gateway as ug
    from tests.source_scan import code_only

    flat = re.sub(r"\s+", " ", DOC)
    assert "one place for every JSON route and one for every streamed frame" in flat
    src = code_only(inspect.getsource(ug))
    assert "middlewares=[outbound_redaction_middleware, secret_middleware]" in \
        re.sub(r"\s+", " ", src), "the redactor must be installed, outermost"
    frame = code_only(inspect.getsource(ug._sse_frame))
    assert "reply_safe(" in frame, "every streamed frame is built here"
    # And no chat reply may be scrubbed at its own call site instead: that is
    # the list-of-seventeen this paragraph argues against.
    turn = code_only(inspect.getsource(ug._chat_turn))
    assert "reply_safe(" not in turn, (
        "a per-return scrub is the shape the boundary replaced")


def test_the_seam_and_the_old_scrub_read_one_vocabulary_now():
    """The paragraph's sharpest claim was that `reply_safe` knew a shape
    `_redact_string` did not, and the paragraph after it records how that gap
    closed: one table, every reader. So the assertion this test used to make
    — that the shared scrub MISSES the bot token — is the one that must fail
    now. The walk is proved one in `test_one_secret_vocabulary.py` by planting
    a shape in the table rather than by comparing outputs, because a
    byte-identical copy agrees on every fixture."""
    from bot.utils.exc_text import _safe_exc_text
    from bot.utils.logger import _redact_string
    from bot.utils.outbound import reply_safe

    flat = re.sub(r"\s+", " ", DOC)
    assert "the shared key=value redactor does not know it" in flat
    assert "`bot/utils/secret_shapes.py`" in flat, "the paragraph names the one table"
    tok = "bot1234567890:AAFvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvv"
    assert "1234567890:AAF" not in _redact_string(tok), "one vocabulary: the old scrub reads it too"
    assert "1234567890:AAF" not in reply_safe(tok)
    assert "1234567890:AAF" not in _safe_exc_text(RuntimeError(tok))


def test_the_six_records_it_names_all_exist_and_differ():
    from bot.nlp import skill_memory as sm

    assert "Six records now" in DOC
    heads = {sm.skill_result_memory("s", "x")[:30],
             sm.routed_answer_memory("s", "x")[:30],
             sm.card_shown_memory("s")[:30],
             sm.not_run_memory("s", "x")[:30],
             sm.web_answer_memory("s", "x")[:30],
             sm.command_reply_memory("s", ["x"])[:30]}
    assert len(heads) == 6, heads
    for name in ("skill_result_memory", "routed_answer_memory",
                 "card_shown_memory", "not_run_memory", "web_answer_memory",
                 "command_reply_memory", "record_routed_turn"):
        assert name in DOC and hasattr(sm, name)


def test_the_two_valued_mode_shape_it_describes_is_really_gone():
    """The claim is checkable, so it is checked: every site the paragraph
    names reads `mode_label`, and the guard reads the whole tree."""
    assert "Wrong file AND wrong literal" in DOC
    from tests.test_live_readiness import MODE_SHAPE_ALLOWED, _two_valued_mode_sites

    hits = _two_valued_mode_sites(ROOT)
    assert [h for h in hits if h[0] not in MODE_SHAPE_ALLOWED] == []
    guard = (ROOT / "tests" / "test_live_readiness.py").read_text(encoding="utf-8")
    assert "root.rglob" in guard and "ast.IfExp" in guard


def test_the_status_seam_it_names_exists_and_both_surfaces_read_it():
    import inspect

    from bot.skills.start_commands import StartCommands
    from bot.web import user_gateway as ug

    assert "status_card_text" in DOC
    assert hasattr(StartCommands, "status_card_text")
    assert "status_card_text" in inspect.getsource(StartCommands._cmd_status)
    assert "status_card_text" in inspect.getsource(ug._chat_turn)
    # ...and the alias it replaced is gone. ASKED, not parsed: this used to
    # `src.index("_INTENT_ALIASES = {")` and read the literal between the
    # braces, so the day that map became a derivation from `skill_doors` —
    # which is the fix for three copies that answered three ways — the guard
    # failed on its own scanning rather than on anything about `status`.
    from bot.nlp.skill_doors import web_scan_aliases

    assert "status" not in web_scan_aliases()



def test_the_door_table_paragraph_names_numbers_a_drive_returns():
    """Every countable claim in the capability-door section, driven.

    The section above it exists because this repo wrote 79/10/5 from memory;
    these are the numbers that would rot the same way, so each is read off the
    code rather than off the prose.
    """
    from bot.nlp.chat_tools import CHAT_TOOLS
    from bot.nlp.intent_router import routed_skill_names
    from bot.nlp.skill_doors import SURFACES, UnknownSurface, dispatches_to, words_reach

    flat = re.sub(r"\s+", " ", DOC)

    # "36 names including `halt`, `close_position` and `emergency_stop`" — the
    # fail-open answer, which is now unreachable because the surface is
    # validated. It is measured by REMOVING the validation, not by trusting
    # the sentence: the whole claim is about what the old branch returned.
    assert "54 names including `halt`" in flat
    old = set(routed_skill_names()) | {t.name for t in CHAT_TOOLS}
    assert len(old) == 54, len(old)
    assert {"halt", "close_position", "emergency_stop"} <= old
    # "...than the one it modelled best (telegram, 33)"
    assert "(telegram, 49)" in flat
    tg = {dispatches_to(n) for n in routed_skill_names()}
    tg |= {t.name for t in CHAT_TOOLS}
    tg.discard("")
    assert len(tg) == 49, len(tg)
    # ...and the unmeasured surface now refuses rather than answering either.
    for bad in ("", "nonsense"):
        with pytest.raises(UnknownSurface):
            words_reach(bad)
    for good in SURFACES:
        words_reach(good)

    # "Two rows have no router rule at all today — `proposals` and
    # `rejected_trades`; it was four until `check_event_risk` and
    # `macro_brief` gained rules of their own" — both halves driven: the two
    # whose ONLY door is a chat tool are named by no router rule, and the two
    # the sentence says left that set really did.
    assert "Two rows have no router rule at all today" in flat
    two = {"proposals", "rejected_trades"}
    left = {"check_event_risk", "macro_brief"}
    for name in two | left:
        assert f"`{name}`" in DOC, name
    assert not (two & routed_skill_names()), two & routed_skill_names()
    assert left <= routed_skill_names(), left - routed_skill_names()
    assert (two | left) <= {t.name for t in CHAT_TOOLS}

    # "ten catalogue commands carry one" — the underscore commands the
    # slash extractor used to truncate (seven until the website's venue
    # router and meme radar became commands under their intent names, nine
    # until the price alert did).
    from bot.skills.command_catalog import all_entries
    under = sorted(c for c in all_entries() if "_" in c)
    assert "ten catalogue commands carry one" in flat
    assert len(under) == 10, under
    for name in under:
        assert f"`{name}`" in DOC, name

    # "all fifteen rows of that table" — the web client's intercepts, counted
    # in the JS file rather than restated here.
    assert "fifteen rows of that table" in flat
    js = (pathlib.Path(__file__).resolve().parent.parent
          / "app" / "routes" / "chat.js").read_text()
    block = js[js.index("const INTERCEPTS = ["):]
    block = block[:block.index("\n];")]
    rows = re.findall(r"^\s*\['([a-z]+)',", block, re.M)
    assert len(rows) == 15, rows


def test_the_url_shape_it_names_is_the_shape_both_routes_send():
    """"The bridge takes the symbol both ways" and the web sends the query
    form — both halves driven, because this defect was one line copied twice
    and a guard reading only one file would have acquitted the other."""
    import os
    import re as _re
    import secrets

    flat = re.sub(r"\s+", " ", DOC)
    assert "a slash in a path segment does not survive a hop" in flat
    assert "The bridge takes the symbol **both** ways" in flat

    os.environ.setdefault("JWT_SECRET", secrets.token_hex(32))
    from fastapi.routing import APIRoute
    from starlette.routing import Match

    import api_bridge

    def _reaches(path, api_only=False):
        scope = {"type": "http", "method": "GET", "path": path,
                 "root_path": "", "headers": []}
        return any(r.matches(scope)[0] is Match.FULL
                   for r in api_bridge.app.routes
                   if not api_only or isinstance(r, APIRoute))

    for route in ("insight", "patterns"):
        assert _reaches(f"/{route}"), f"the query form of /{route} is missing"
        assert _reaches(f"/{route}/BTCUSDT"), "the path form was removed"
        # What the decoded slash actually delivers, and why the query form
        # exists. NOT "matches nothing" — a StaticFiles mount at '' matches
        # everything, which is WHY the caller got HTML instead of a 404 body
        # it could read. The first draft of this assertion asked whether the
        # resolved route had an `.endpoint`, which the Mount does not, so it
        # passed while the Mount was matching happily: a guard acquitting on
        # a missing attribute rather than on a missing match.
        assert not _reaches(f"/{route}/BTC/USDT", api_only=True)
        assert _reaches(f"/{route}/BTC/USDT"), (
            "something still matches it — the static mount, and that is the "
            "point: the caller gets a web page where JSON was expected")

    # And no web route puts a slash-bearing symbol back in a path segment.
    for rel in ("app/routes/insight.js", "app/routes/patterns.js"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        code = _re.sub(r"//[^\n]*", "",
                       _re.sub(r"/\*.*?\*/", "", src, flags=_re.S))
        for m in _re.finditer(r"\$\{BOT_API_URL\}([^`]*)", code):
            seg = m.group(1).split("?")[0]
            assert "${" not in seg, f"{rel} interpolates into a path segment"

def test_the_four_volume_controls_it_names_all_exist_and_bound_volume():
    """"four volume knobs over a feed whose input is `active_alerts`".

    A count in prose is the part that rots first, and this one is an
    argument: the paragraph's whole case is that every EXISTING control is
    about how many, so it is wrong the moment a fifth one appears or one of
    the four turns out to bound something else. Each is read from the module
    that holds it rather than from a list here.
    """
    import inspect

    from bot.core import proactive_monitor as pm
    from tests.source_scan import code_only

    flat = re.sub(r"\s+", " ", DOC)
    for name in ("_SEVERE_CARDS_PER_TICK", "_SEVERE_CARDS_PER_HOUR",
                 "BLACK_SWAN_SEVERE_REPEAT"):
        assert name in flat, name
    src = code_only(inspect.getsource(pm))
    assert "_SEVERE_CARDS_PER_TICK" in src and "_SEVERE_CARDS_PER_HOUR" in src
    assert "BLACK_SWAN_SEVERE_REPEAT" in src
    # ...and the input really is the whole scanned universe.
    assert "active_alerts" in src and "active_alerts" in flat


def test_the_two_dials_it_names_have_the_defaults_it_quotes():
    from bot.core import anomaly_scope as sc

    flat = re.sub(r"\s+", " ", DOC)
    assert "`scope` (`held` by default, `all` a choice somebody types)" in flat
    assert "`interval` (3600s)" in flat
    assert sc.SCOPE_HELD == "held" and sc.SCOPE_ALL == "all"
    assert sc.DEFAULT_INTERVAL_SEC == 3600
    # The default is the one a store with nothing recorded hands back.
    assert sc.normalise_scope(None) is None, "junk is not a default"


def test_the_three_valued_book_read_it_describes_is_really_three_valued():
    """"answers None when the book cannot be read, never an empty set".

    Driven, not scanned: the two absences have to be distinguishable from
    outside, and a scan cannot tell `None` from `set()` at the call site.
    """
    from bot.core.anomaly_scope import SCOPE_HELD, held_symbols, scoped

    flat = re.sub(r"\s+", " ", DOC)
    assert "answers **None** when the book cannot be read, never an empty set" in flat

    class _Raises:
        @property
        def live_executor(self):
            raise RuntimeError("venue down")

    assert held_symbols(_Raises()) is None

    class _Alert:
        symbol = "WLFI/USDT"

    alerts = [_Alert()]
    kept_unreadable, dropped_unreadable, note_unreadable = scoped(
        alerts, None, SCOPE_HELD)
    kept_empty, dropped_empty, note_empty = scoped(alerts, set(), SCOPE_HELD)
    assert kept_unreadable == alerts and dropped_unreadable == []
    assert kept_empty == [] and dropped_empty == alerts
    # Not merely DIFFERENT — a first draft asserted that and passed against a
    # mutation that gave the unreadable book the empty book's sentence with a
    # count of zero in front of it. Each note has to name its own case, and
    # the unreadable one must not assert the thing nobody read.
    assert "unreadable" in note_unreadable.lower()
    assert "no open positions" not in note_unreadable
    assert "no open positions" in note_empty
    assert "unreadable" not in note_empty.lower()


def test_the_interval_is_a_floor_and_not_a_schedule():
    """"`is_due()` is True for a never-sent channel"."""
    from bot.core.anomaly_scope import DEFAULT_INTERVAL_SEC, is_due

    flat = re.sub(r"\s+", " ", DOC)
    assert "`is_due()` is True for a never-sent channel" in flat
    assert is_due(None, 1_000_000.0, DEFAULT_INTERVAL_SEC) is True
    assert is_due(1_000_000.0, 1_000_001.0, DEFAULT_INTERVAL_SEC) is False
    assert is_due(1_000_000.0, 1_000_000.0 + DEFAULT_INTERVAL_SEC,
               DEFAULT_INTERVAL_SEC) is True


def test_the_neutralised_dial_it_describes_is_actually_neutralised():
    """"It neutralises the dial explicitly now and says why."

    The paragraph's claim is about a NAMED test, so the named test is read.
    A test whose subject has become unreachable has quietly become a test of
    something else, and the only durable record of that is in the test.
    """
    flat = re.sub(r"\s+", " ", DOC)
    assert "test_a_new_condition_still_pages_behind_a_standing_one" in flat
    src = (ROOT / "tests" / "test_anomaly_alert_volume.py").read_text(
        encoding="utf-8")
    i = src.index("def test_a_new_condition_still_pages_behind_a_standing_one")
    body = src[i:i + 2500]
    assert "interval" in body, "the dial has to be named to be neutralised"



def test_the_audience_it_says_it_did_not_change_really_did_not():
    """The paragraph's claim is a NEGATIVE — the one kind that rots silently,
    because nothing fails when somebody later makes the change it says was
    not made. So it is driven: the titles carry no symbol, and BLACK_SWAN
    still reaches every watching chat."""
    import ast
    import inspect

    from bot.core import proactive_monitor as pm

    flat = re.sub(r"\s+", " ", DOC)
    assert "this slice does NOT deliver it" in flat
    assert "No symbol has ever reached that feed" in flat

    auds, titles = [], []
    for node in ast.walk(ast.parse(inspect.getsource(pm))):
        if not (isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "Alert"):
            continue
        kw = {k.arg: k.value for k in node.keywords}
        t = kw.get("alert_type")
        if not (isinstance(t, ast.Constant) and t.value == "BLACK_SWAN"):
            continue
        a = kw.get("audience")
        auds.append(a.value if isinstance(a, ast.Constant) else "all")
        titles.append(ast.unparse(kw["title"]))

    assert auds and set(auds) == {"all"}, auds
    # The titles the public feed would receive: a type, a phrase, a count.
    # None of them interpolates a symbol, which is the paragraph's evidence.
    assert titles, "no BLACK_SWAN title found"
    for t in titles:
        assert "symbol" not in t.lower() or "len(" in t, t


# ── F-15 ──────────────────────────────────────────────────────────────────

def test_no_secret_or_address_is_committed_in_it():
    assert re.search(r"0x[a-fA-F0-9]{40}", DOC) is None
    for pat in (r"\bsk-[A-Za-z0-9]{16,}", r"SECRET\s*=\s*\S{8,}",
                r"\b[a-z0-9-]+\.trycloudflare\.com"):
        assert not re.search(pat, DOC), f"secret-shaped text matched {pat}"


# ── the scan-dispatch section ─────────────────────────────────────────────

def test_the_scan_section_names_numbers_a_drive_returns():
    """Every countable claim in the one-column section, read off the code.

    The section's own subject is a count that was stale in five places, so a
    count in THIS prose is the part most likely to rot next — the `<n> of the
    <m>` rule one section up, applied to the paragraph that describes it.
    """
    import ast
    import inspect

    import bot.token.tier_gate as tg
    from bot.nlp.skill_doors import SCAN_DISPATCH, dispatch_kwargs, dispatches_to
    from bot.skills.skill_registry import DeepScanSkill, deepscan_universe_size

    flat = re.sub(r"\s+", " ", DOC)

    # "The universe is 115." / "115 symbols through a `Semaphore(10)`"
    n = deepscan_universe_size()
    assert f"The universe is {n}." in flat, n
    assert f"{n} symbols through a `Semaphore(10)`" in flat, n

    # "twelve sequential batches" — ceil(universe / semaphore), in words.
    import textwrap

    src = textwrap.dedent(inspect.getsource(DeepScanSkill.execute))
    sem = next(int(c.value)
               for node in ast.walk(ast.parse(src))
               if isinstance(node, ast.Call)
               and ast.unparse(node.func).endswith("Semaphore")
               for c in node.args if isinstance(c, ast.Constant))
    assert sem == 10, sem
    assert -(-n // sem) == 12, -(-n // sem)
    assert "twelve sequential batches" in flat

    # "Eight of the nine paid skills are gated by COINCIDENCE ... and
    # `pro_scan` is sold as `premium_scan`" — measured over the SKILLS (the
    # domain `feature_for` is called on), not over the features, which map to
    # themselves by construction and would make the claim vacuous.
    from bot.skills.skill_permissions import SKILL_PERMISSION

    sold = set(tg.FEATURE_MIN_TIER)
    paid = {sk for sk in SKILL_PERMISSION if tg.feature_for(sk) in sold}
    same = {sk for sk in paid if tg.feature_for(sk) == sk}
    assert len(paid) == 9 and len(same) == 8, (sorted(paid), sorted(same))
    assert sorted(paid - same) == ["pro_scan"], sorted(paid - same)
    assert tg.feature_for("pro_scan") == "premium_scan"
    assert "Eight of the nine paid skills" in flat

    # "all seven scan rules" / "all three scan skills". Five until the sweep's
    # own timeframes got rows of their own (`scan_deep_1h`, `scan_deep_1d`):
    # three skills still, because both run `deepscan`.
    assert len(SCAN_DISPATCH) == 7
    assert len({dispatches_to(i) for i in SCAN_DISPATCH} | {"scan_market"}) == 3
    assert "all seven scan rules" in flat and "all three" in flat

    # "swapping `scan_swing`'s mode to `intraday`" — both are real modes.
    assert dispatch_kwargs("scan_swing") == {"mode": "swing"}
    from bot.skills.skill_registry import ProScanSkill
    assert {"swing", "intraday"} <= set(ProScanSkill.MODE_CFG)

    # EVERY ILLUSTRATIVE COUNT, not only the two stated as facts. The section
    # is about a universe size that was stale in five places, and a worked
    # example teaches a stale denominator as confidently as a claim does —
    # `Scanned 40/115` with `Not reached 75` beside it is arithmetic, and it
    # stops being arithmetic the day a symbol is added.
    section = DOC[DOC.index("**ONE COLUMN, and five of the six"):
                  DOC.index("**A second copy of a gate decided")]
    pairs = re.findall(r"Scanned (\d+)/(\d+)", section)
    assert pairs, "the section stopped showing a Scanned row"
    for read, total in pairs:
        assert int(total) == n, (read, total, n)
    # ...and each complement in the same section adds back up to the universe.
    read_40 = int(pairs[0][0])
    assert f"Not reached {n - read_40} (time budget)" in section, n - read_40
    assert f"as {n - read_40} errors" in section, n - read_40
    words = {23: "twenty-three", 24: "twenty-four", 22: "twenty-two"}
    short = n - int(pairs[1][0])
    assert words.get(short, str(short)) in section, (short, words.get(short))


def test_the_income_map_derivation_it_describes_is_the_real_one():
    """"`ROLE_PERMISSIONS["admin"]` is the literal `{"*"}`".

    The paragraph's whole argument for the complement is that the obvious
    derivation answers the wildcard. If the role table ever stops being
    shaped that way, the argument is stale and this says so.
    """
    from bot.utils.user_store import ROLE_PERMISSIONS
    from tests.test_the_income_map_says_who_may_run_a_command import (
        _admin_only_permissions,
    )

    assert ROLE_PERMISSIONS["admin"] == {"*"}, ROLE_PERMISSIONS["admin"]
    admin_only = _admin_only_permissions()
    assert "admin" in admin_only and "stake" not in admin_only
    assert "stake" in ROLE_PERMISSIONS["trader"], (
        "the doc says trader holds it; that is why /stake is not admin-only")


def test_the_two_stale_citations_it_names_are_where_it_says():
    """"line 199 is empty and the handler is at 297" / "six lines short".

    Those two readings are the whole argument for refusing a resolvability
    ratchet — a citation rots by pointing at the wrong line, not an impossible
    one — so they are driven rather than remembered. Both are also the fix
    this slice shipped, so a later edit that shifts either handler fails HERE
    and sends the reader to re-measure the sentence rather than trust it.
    """
    def _lines(rel):
        return (ROOT / rel).read_text(encoding="utf-8").split("\n")

    def _defs(lines, name):
        return [i for i, ln in enumerate(lines, 1)
                if ln.lstrip().startswith((f"def {name}(", f"async def {name}("))]

    stake = _lines("bot/skills/yield_commands.py")
    assert len(stake) >= 199, "the stale citation stopped being in range"
    assert not stake[198].strip(), repr(stake[198])
    assert _defs(stake, "_cmd_stake") == [297], _defs(stake, "_cmd_stake")

    trading = _lines("bot/skills/trading_commands.py")
    assert len(trading) >= 178 and not trading[177].strip(), repr(trading[177])
    assert _defs(trading, "_cmd_mystrategy") == [184], (
        _defs(trading, "_cmd_mystrategy"))
    assert trading[182].strip() == '@guard("mystrategy")', repr(trading[182])

    # ...and the map cites the corrected lines, not the blank ones.
    income = (ROOT / "docs" / "INCOME_MAP.md").read_text(encoding="utf-8")
    assert "trading_commands.py:178" not in income
    assert income.count("trading_commands.py:184") == 2
    assert "yield_commands.py:199" not in income
    assert "yield_commands.py:297" in income


def _source_scan_adoption() -> tuple[int, int, int]:
    """(importers, private tokenize copies, copies whose file imports it anyway).

    DERIVED, never restated — the rule this file already applies to the
    catalogue's 91/79/12. The three counts CLAUDE.md's source-scanning section
    quotes are read back out of the prose and compared to this walk, because a
    number in prose is the part that rots first: the paragraph said "47 test
    files" and named ONE remaining private copy, and on the day this was
    written the tree held 199 and 18.

    A copy is a function named like the shared one that TOKENIZES for itself.
    An earlier draft also required that it not mention `source_scan` in its own
    body; the mutation round showed no such function exists, so that clause was
    a claim about a check rather than a check. BOTH spellings count: four of the
    eighteen use the bytes-based `tokenize.tokenize` rather than
    `tokenize.generate_tokens`, and the first draft of this walk knew only the
    second and answered 14 — a guard one spelling short of the thing it counts,
    which is `_SLASH_COMMAND` stopping at the underscore in a new place. Asking
    whether the FILE mentions `source_scan` is a different wrong question and
    answers 11: eight files import `handler_sources` from it and still carry
    their own stripper, which is the sharpest half of the finding.
    """
    import ast

    names = {"code_only", "_code_only", "strip_comments", "_strip_comments"}
    importers: set[str] = set()
    copies: list[str] = []
    both = 0
    for path in sorted(list((ROOT / "tests").rglob("*.py"))
                       + list((ROOT / "scripts").rglob("*.py"))):
        if path.name == "source_scan.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:                      # pragma: no cover - parse gate covers it
            continue
        imports_it = any(
            isinstance(n, ast.ImportFrom) and (n.module or "").endswith("source_scan")
            for n in ast.walk(tree))
        if imports_it:
            importers.add(str(path))
        owns = [n for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name in names
                and ("tokenize.generate_tokens" in ast.unparse(n)
                     or "tokenize.tokenize" in ast.unparse(n))]
        copies.extend(f"{path}:{n.lineno}" for n in owns)
        if owns and imports_it:
            both += 1
    return len(importers), len(copies), both


def test_the_source_scan_counts_it_quotes_are_the_ones_a_walk_returns():
    """"as 199 test files already do" / "The backlog is 18" / "8 of those".

    All three are read out of the prose, so a drift fails HERE rather than
    teaching the next reader that the backlog is smaller than it is.
    """
    importers, copies, both = _source_scan_adoption()
    section = DOC[DOC.index("## Writing tests that scan source"):
                  DOC.index("Prefer exercising a property over matching text")]

    assert f"as {importers} test files already do" in section, importers
    assert f"The backlog is {copies}," in section, copies
    assert f"{copies} files\ndefine their own" in section, copies
    assert f"**{both} of those already have the module open**" in section, both


def _source_scanning_files() -> tuple[int, int]:
    """(files reaching for source text, all test files) -- the prose's own rule.

    It is a FLOOR and the paragraph says so: a hand-rolled
    `Path("bot/x.py").read_text()` is a source scan this rule cannot see. A
    wider proxy (any `read_text` beside a production path literal) answers 470
    on the same tree, so the two rules disagree by seventy -- which is why the
    number travels with the rule that produced it rather than alone, the same
    reason the walk above counts BOTH tokenize spellings.
    """
    files = sorted((ROOT / "tests").rglob("test_*.py"))
    reach = [p for p in files
             if any(tok in p.read_text(encoding="utf-8", errors="replace")
                    for tok in ("source_scan", "code_only", "inspect.getsource"))]
    return len(reach), len(files)


def test_the_do_not_convert_wholesale_count_is_the_one_a_walk_returns():
    """"47 of 532" was the SAME 47, seven hundred lines down, on another question.

    The importer sentence said 47 and this one said 47, and only one of them
    could have been measured -- which is how the first came to be wrong. Both
    are derived now.
    """
    reach, total = _source_scanning_files()
    section = DOC[DOC.index("**Do not convert wholesale"):]
    section = section[:section.index("Rank candidates by what a wrong claim")]

    assert f"**{reach} of {total}**" in section, (reach, total)
    assert f"{reach} is a FLOOR" in section, reach
    assert '*"47 of 532 test files' in section, (
        "the stale figure is quoted as the thing being corrected, so a reader "
        "who remembers it lands on the correction rather than on silence")


def test_the_fifth_false_failure_names_its_own_referent():
    """"The fifth one" sat two paragraphs from "five false failures"; the
    backlog paragraphs above put forty lines and a "Four counts" between them,
    and the nearest antecedent stopped being the right one.
    """
    head = DOC.index("Strip comments first.")
    ref = DOC.index("is the argument for importing rather than copying.")
    assert "**The fifth false failure " in DOC[head:ref + 60], (
        "it names what it is the fifth OF")
    assert "**The fifth one is" not in DOC, "the ambiguous spelling is gone"
    # The referent is HAND-WRAPPED ("five false\nfailures"), so the search is
    # whitespace-normalised: the first draft of this line looked for the
    # unwrapped form, failed, and was one keystroke from being "fixed" by
    # re-wrapping the prose. Check the assertion before the code.
    flat = " ".join(DOC.split())
    assert flat.count("five false failures") == 1, "and its referent is still there"


def test_the_latent_hazard_it_names_is_still_the_shape_it_says():
    """"hand-scans quote state ... and never blanks docstrings".

    The paragraph declines to consolidate on the grounds that this is a hazard
    rather than a defect. If the copy stops being narrow, the grounds change.
    """
    import ast

    src = (ROOT / "tests" / "test_black_swan_is_reached.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "_strip_comments")
    body = ast.unparse(fn)
    assert "tokenize" not in body, "it is the hand-written scanner the doc describes"
    assert "'\\\"'" in body or '"\'"' in body or "in '\"" in body, "it tracks quote state"

    g: dict = {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "x", "exec"), g)
    planted = '"""MARKER_IN_A_DOCSTRING."""\n# MARKER_IN_A_COMMENT\nx = 1\n'
    out = g["_strip_comments"](planted)

    # TWO facts, not one count. The first draft asserted `out.count(...) == 1`
    # over one marker planted twice, and the mutation that relaxed it to `<= 1`
    # SURVIVED: zero satisfies it too, so the assertion stopped separating a
    # narrow copy from a wide one -- which is the whole claim. Distinct markers
    # make each half its own observable fact, and neither direction has a
    # comparison to loosen.
    assert "MARKER_IN_A_COMMENT" not in out, (
        "the comment really is stripped -- that much the copy does")
    assert "MARKER_IN_A_DOCSTRING" in out, (
        "and the DOCSTRING survives, which is the hazard the paragraph "
        "declines to consolidate on")
