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

    m = re.search(r"Thirty-one call sites across the two entry points", DOC)
    assert m, "the claim was reworded; recount it"
    import bot.skills.telegram_handler as th
    from bot.web import user_gateway as ug
    tg = textwrap.dedent(inspect.getsource(th.TelegramHandler._handle_message))
    web = inspect.getsource(ug._chat_turn)
    n = sum(ast.unparse(c.func).endswith(("_remember_routed", "record_routed_turn"))
            for src in (tg, web)
            for c in ast.walk(ast.parse(src))
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute | ast.Name))
    assert n == 31, f"CLAUDE.md says thirty-one; the two entry points have {n}"


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

    m2 = re.search(r"(\d+) of the 90 reach the tool-less chat model and (\d+)\n?"
                   r"reach a skill", DOC)
    assert m2, "the fall-through claim was reworded; recount it"
    assert (int(m2.group(1)), int(m2.group(2))) == (nothing, len(hits))
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


def test_the_four_records_it_names_all_exist_and_differ():
    from bot.nlp import skill_memory as sm

    assert "Four records now" in DOC
    heads = {sm.skill_result_memory("s", "x")[:30],
             sm.routed_answer_memory("s", "x")[:30],
             sm.card_shown_memory("s")[:30],
             sm.not_run_memory("s", "x")[:30]}
    assert len(heads) == 4, heads
    for name in ("skill_result_memory", "routed_answer_memory",
                 "card_shown_memory", "not_run_memory", "record_routed_turn"):
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
    # ...and the alias it replaced is gone.
    src = inspect.getsource(ug._chat_turn)
    i = src.index("_INTENT_ALIASES = {")
    assert '"status"' not in src[i:src.index("}", i)]


# ── F-15 ──────────────────────────────────────────────────────────────────

def test_no_secret_or_address_is_committed_in_it():
    assert re.search(r"0x[a-fA-F0-9]{40}", DOC) is None
    for pat in (r"\bsk-[A-Za-z0-9]{16,}", r"SECRET\s*=\s*\S{8,}",
                r"\b[a-z0-9-]+\.trycloudflare\.com"):
        assert not re.search(pat, DOC), f"secret-shaped text matched {pat}"
