"""The runbook must not name things that do not exist.

A runbook is an instrument like any other, and it fails the same way: by
claiming more than it can support. Today's version of that was the deploy
check — the doc said `build` answers "did my fix land?", which stopped being
true the moment a fix shipped entirely in public/js and `build` did not move.
The doc was not wrong when written; the code moved and the doc did not.

An operator reading a stale runbook mid-incident is in a worse position than
one reading nothing, because they will act on it. So the factual, checkable
claims are pinned here:

  - every env var it tells you to set exists in the code that reads it
  - every default it quotes is the default actually compiled in
  - the two fingerprint fields it describes are the two actually served
  - it contains no chain address at all — nothing in an ops runbook is
    on-chain, so the only way one arrives is a mid-incident paste

What is NOT pinned is the prose. Judgement calls, thresholds-with-reasons and
incident narratives are the point of the document and are meant to be edited.
"""

import re
from pathlib import Path

import pytest

RUNBOOK = Path("docs/LIVE_HARDENING_RUNBOOK.md").read_text(encoding="utf-8")
CONFIG = Path("bot/config.py").read_text(encoding="utf-8")


# ── env names ─────────────────────────────────────────────────────────────

#: (env name, where it is read). Every one is quoted in the runbook's caps
#: table or its prose as something the operator can set.
DOCUMENTED_ENV = [
    ("ANALYSIS_TIMEOUT_SEC", "bot/config.py"),
    ("MARKET_DATA_TIMEOUT_MS", "bot/config.py"),
    ("TICK_PHASE_TIMEOUT_SEC", "bot/config.py"),
    ("TOP_MOVERS_COUNT", "bot/config.py"),
    ("PUBLIC_GATEWAY_URL", "bot/config.py"),
    ("GATEWAY_CONNECT_TIMEOUT_MS", "app/lib/gateway.js"),
]


@pytest.mark.parametrize("name,where", DOCUMENTED_ENV)
def test_a_documented_env_var_is_actually_read(name, where):
    assert name in RUNBOOK, f"{name} dropped out of the runbook"
    src = Path(where).read_text(encoding="utf-8")
    assert name in src, (
        f"the runbook tells an operator to set {name}, but {where} never "
        f"reads it — a rename left the doc pointing at nothing")


@pytest.mark.parametrize("name,default", [
    ("ANALYSIS_TIMEOUT_SEC", "90"),
    ("MARKET_DATA_TIMEOUT_MS", "15000"),
    ("TICK_PHASE_TIMEOUT_SEC", "300"),
])
def test_a_quoted_default_is_the_real_default(name, default):
    """A wrong default is worse than none: it gets used to reason about
    headroom that does not exist."""
    m = re.search(rf'"{name}",\s*([0-9_.]+)', CONFIG)
    assert m, f"{name} not found with a literal default in bot/config.py"
    actual = float(m.group(1).replace("_", ""))
    row = re.search(rf"\|\s*`{name}`\s*\|\s*([0-9]+)\s*\|", RUNBOOK)
    assert row, f"{name} has no default column in the runbook caps table"
    assert float(row.group(1)) == actual, (
        f"runbook says {name} defaults to {row.group(1)}, code says {actual:g}")
    assert float(default) == actual, "the test's own expectation drifted"


# ── the deploy check ──────────────────────────────────────────────────────

def test_both_fingerprint_fields_are_documented_and_served():
    for field in ("`build`", "`assets`"):
        assert field in RUNBOOK, f"{field} missing from the deploy section"
    version = Path("app/lib/version.js").read_text(encoding="utf-8")
    assert "out.build = BUILD.build" in version
    assert "out.assets = BUILD.assets" in version


def test_the_runbook_no_longer_calls_build_the_whole_answer():
    """It said `build` answers "did my fix land?". For a client-only change it
    does not, and saying so is the entire reason `assets` exists."""
    assert "client-only change" in RUNBOOK
    assert "nothing deployed" in RUNBOOK
    # The cache-buster caveat matters too: a moved digest is not a fetched file.
    assert "?v=" in RUNBOOK


def test_the_command_it_gives_prints_both_values():
    assert "v.build, v.assets" in RUNBOOK, (
        "the copy-paste command must produce both, or an operator compares one "
        "and concludes about the other")


# ── engine triage ─────────────────────────────────────────────────────────

def test_the_status_lines_it_quotes_are_the_ones_actually_emitted():
    cards = Path("bot/formatters/rich_cards.py").read_text(encoding="utf-8")
    i18n = Path("bot/utils/i18n.py").read_text(encoding="utf-8")
    assert "lbl_phase_headroom" in cards and "lbl_phase_headroom" in i18n
    assert "lbl_last_phase_timeout" in cards and "lbl_last_phase_timeout" in i18n
    # The runbook quotes the English rendering of both.
    assert "Slowest tick phase" in RUNBOOK
    assert "Tick phase timed out" in RUNBOOK
    assert '"Slowest tick phase"' in i18n or "Slowest tick phase" in i18n


def test_the_sltp_monitor_vocabulary_is_what_the_card_renders():
    """The runbook's SL/TP table is read by someone who has just been told
    their positions "could be unmonitored". Every row is quoted from i18n
    rather than restated, so rewording a label fails here until the runbook
    is updated with it — the failure mode being a table that explains a line
    the operator will never see."""
    from bot.utils.i18n import t

    cards = Path("bot/formatters/rich_cards.py").read_text(encoding="utf-8")
    i18n = Path("bot/utils/i18n.py").read_text(encoding="utf-8")
    for key in ("lbl_sltp_monitor", "val_sltp_ran", "val_sltp_backstop",
                "val_sltp_unwatched", "val_sltp_backstop_failed",
                "val_sltp_unknown"):
        assert key in i18n, f"{key} is not defined"
        assert key in cards, f"{key} is defined but nothing renders it"
        english = t(key, "en")
        assert english != key, f"{key} has no English rendering"
        # "not recorded — the engine has not completed a tick" is quoted short
        # in the table; match on the leading clause.
        quoted = english.split(" \u2014 ")[0]
        assert quoted in RUNBOOK, (
            f"the runbook does not quote {key} ({quoted!r}) — an operator "
            f"looking up a line they can see would not find it")


def test_the_backstop_audit_action_it_tells_you_to_grep_for_exists():
    engine = Path("bot/core/engine.py").read_text(encoding="utf-8")
    assert 'action="positions_backstop"' in engine
    assert "positions_backstop" in RUNBOOK
    # Both verdicts, because grepping for only the reassuring one is how the
    # bad news gets missed.
    assert "RAN" in RUNBOOK and "INCOMPLETE" in RUNBOOK


def test_the_audit_action_it_tells_you_to_grep_for_exists():
    engine = Path("bot/core/engine.py").read_text(encoding="utf-8")
    assert 'action="analysis_timeout"' in engine
    assert "analysis_timeout" in RUNBOOK


def test_the_breaker_key_it_quotes_is_the_real_one():
    assert "engine_tick_failure" in RUNBOOK
    hits = [p for p in Path("bot").rglob("*.py")
            if "engine_tick_failure" in p.read_text(encoding="utf-8")]
    assert hits, "the runbook quotes a breaker key nothing emits"


# ── dashboard vocabulary ──────────────────────────────────────────────────

@pytest.mark.parametrize("phrase", [
    "ENGINE STATUS UNKNOWN", "NO ENGINE DATA", "ENGINE LIVE",
    "Live updates disconnected",
])
def test_documented_dashboard_states_are_strings_the_app_can_show(phrase):
    # Search every script the dashboard PAGE loads, not one file. The chip's
    # verdict moved to engine-status-model.js so it could be scenario-tested,
    # and a single-file grep reported that the app had stopped rendering
    # states it renders identically — the third source-scanning test in one
    # evening to fail on a behaviour-preserving extraction.
    #
    # The invariant is unchanged: a phrase the runbook teaches an operator to
    # recognise must exist SOMEWHERE the page can show it.
    sources = [
        Path("app/public/js/dashboard.js"),
        Path("app/public/js/engine-status-model.js"),
    ]
    shown = "\n".join(p.read_text(encoding="utf-8")
                      for p in sources if p.exists())
    assert phrase in RUNBOOK, f"{phrase} dropped out of the runbook"
    assert phrase in shown, (
        f"the runbook teaches an operator to recognise {phrase!r}, which the "
        f"dashboard never renders")


# ── F-15 / address hygiene ────────────────────────────────────────────────

def test_this_runbook_carries_no_chain_address_at_all():
    """Nothing here is on-chain, so nothing here needs an address.

    The deployed contract addresses live in the on-chain docs, which have
    their own pinning (app/test/golive_runbook.test.js). THIS file is an ops
    runbook — boot probes, caps, deploy checks — and the only reason an
    address would ever appear in it is somebody pasting one from a terminal
    mid-incident. Zero is the invariant that catches that; "one expected
    address" would quietly permit a second.
    """
    addrs = set(re.findall(r"0x[a-fA-F0-9]{40}", RUNBOOK))
    assert addrs == set(), f"chain address(es) pasted into the ops runbook: {addrs}"


def test_no_ephemeral_tunnel_url_is_committed():
    """It changes on every cloudflared restart, so a committed one is wrong
    almost immediately — and documenting a moving target as fact is the same
    failure the rest of this file guards."""
    assert "trycloudflare.com" not in RUNBOOK.replace(
        "*.trycloudflare.com", ""), "a specific quick-tunnel host was committed"
    assert "named" in RUNBOOK and "tunnel" in RUNBOOK, (
        "the standing hazard must still be written down")


def test_no_secret_shaped_value_crept_in():
    for pat in (r"sk-[A-Za-z0-9]{16,}", r"LLM_API_KEY\s*=\s*\S+",
                r"WEB_GATEWAY_SECRET\s*=\s*[A-Za-z0-9]{8,}"):
        assert not re.search(pat, RUNBOOK), f"secret-shaped text matched {pat}"


# ── the progress signal ───────────────────────────────────────────────────

def test_the_progress_line_it_documents_is_one_the_code_emits():
    from tests.source_scan import code_only
    engine = code_only(Path("bot/core/engine.py").read_text(encoding="utf-8"))
    assert "finished {_done} of {_of} signals" in engine
    assert "needed at that rate" in engine
    assert "signals attempted before it was cancelled" in RUNBOOK or \
        "val_signals_done" in Path("bot/utils/i18n.py").read_text(encoding="utf-8")
    assert "How far did the batch get" in RUNBOOK or "how far did the batch get" in RUNBOOK


def test_the_triage_order_is_stated():
    """The fraction comes FIRST. Two fixes shipped ahead of it, against a
    cause that was inferred rather than measured, and neither worked."""
    assert "Read the fraction first" in RUNBOOK
    i = RUNBOOK.index("how far did the batch get")
    j = RUNBOOK.index("The analysis-timeout audit line")
    assert i < j, "the measured signal must be documented before the narrower one"


def test_the_knobs_it_names_for_a_sizing_problem_all_exist():
    for env in ("TOP_MOVERS_COUNT", "SCAN_ANALYSIS_CONCURRENCY",
                "TICK_PHASE_TIMEOUT_SEC"):
        assert env in RUNBOOK
        assert env in CONFIG, f"the runbook offers {env} as a fix; config never reads it"


# ── the bot's build provenance ────────────────────────────────────────────

class TestTheBuildSectionDescribesWhatTheCodeActuallyDoes:
    """Added after 2026-08-20, when a deploy landed 255 commits stale and the
    bot could not be asked what it was running. The section is only useful if
    every command in it works, so each is pinned to the code that serves it —
    a runbook read mid-incident is acted on."""

    def test_the_port_it_tells_you_to_curl_is_the_port_bound(self):
        from tests.source_scan import code_only
        main = code_only(Path("bot/main.py").read_text(encoding="utf-8"))
        assert "curl -s localhost:8080/health" in RUNBOOK
        assert "8080)" in main, "the dashboard no longer binds the documented port"

    def test_the_health_payload_really_carries_the_field(self):
        from tests.source_scan import code_only
        src = code_only(
            Path("bot/web/dashboard_server.py").read_text(encoding="utf-8"))
        assert '"build": build_short()' in src, (
            "the runbook tells an operator to read /health for the build")

    def test_every_label_in_the_table_is_one_the_renderer_can_emit(self):
        """The table is a decode key. A row the code cannot produce is a
        reader deciding their deploy is fine on a shape that never appears —
        and a shape the code produces that is missing from the table is worse,
        because `-dirty` is the row that means 'this sha is not the code'."""
        from bot.utils import build_info as bi
        sha = "0" * 40
        produced = {
            bi.short(bi.BuildInfo(sha, "git", False)),
            bi.short(bi.BuildInfo(sha, "git", True)),
            bi.short(bi.BuildInfo(sha, "image", None)),
            bi.short(bi.BuildInfo(None, "none", None)),
        }
        for label in produced:
            shape = label.replace("0000000", "0449bc7")
            assert shape in RUNBOOK, (
                f"the renderer emits {shape!r} and the decode table omits it")

    def test_the_pre_launch_gate_it_names_exists_and_still_exits_three(self):
        guard = Path("scripts/verify_deploy_source.sh")
        assert "scripts/verify_deploy_source.sh" in RUNBOOK
        src = guard.read_text(encoding="utf-8")
        assert "exit 3" in src, (
            "the runbook promises 'never 0 when it could not check'")

    def test_it_does_not_promise_dirty_covers_untracked_files(self):
        """`-dirty` is tracked-only, deliberately. An operator told otherwise
        would read a clean label over an untracked stray and mistrust the
        instrument, or worse, trust it about the wrong thing."""
        assert "tracked modifications only" in RUNBOOK
