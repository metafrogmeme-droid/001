"""The heartbeat pings only when it CHECKED, and the deploy gate asks both targets.

TWO FAILURES FROM 2026-08-25, ONE WEEK APART IN CAUSE.

1. Nothing outside the box was watching. Every alert path in RUNECLAW runs
   INSIDE the thing being monitored — system_health, proactive_monitor, the
   Telegram degraded alerts — and a bot that has died cannot tell you it died.
   So every recovery that week began with a human noticing, and the gateway
   tunnel spent eighteen days giving up after five restart attempts unnoticed.

2. A deploy landed the right commit on the bot box, passed every check, and
   left sign-in broken all day — because the fix was in `app/lib/siwf.js`,
   which ships with the WEB CONTAINER. Nothing asked about the other half.

THE HAZARD IN THE FIX IS WORSE THAN THE ORIGINAL GAP.

The naive dead-man's switch is one cron line:

    */5 * * * * curl -fsS https://hc-ping.com/<uuid>

It pings whenever CRON is alive — which it is while the bot is dead, the
bridge is dead and the gateway is unreachable. That shows green through an
entire outage, and an operator learns to trust it. A heartbeat that fires
regardless of health is a confident all-clear manufactured from no evidence:
the exact defect this repository spends most of its guard tests preventing,
rebuilt inside the tool bought to prevent it.

These tests pin the properties that make it honest, because every one of them
is a single edit away from the naive version.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HEARTBEAT = ROOT / "scripts" / "monitoring" / "heartbeat.sh"
VERIFY = ROOT / "scripts" / "verify_deploy.sh"


def _code_only(text: str) -> str:
    """Comment lines blanked, line count preserved.

    Both scripts describe the naive version they must not be, quoting the very
    `curl <ping-url>` line that would be the bug. A scan matching the prose
    would fail on the file explaining itself and pass on the one doing it.
    """
    return "\n".join(
        "" if line.lstrip().startswith("#") else line
        for line in text.splitlines()
    )


@pytest.fixture(scope="module")
def hb() -> str:
    assert HEARTBEAT.exists(), f"{HEARTBEAT} is gone"
    return _code_only(HEARTBEAT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def vd() -> str:
    assert VERIFY.exists(), f"{VERIFY} is gone"
    return _code_only(VERIFY.read_text(encoding="utf-8"))


# ── the heartbeat checks before it reports ─────────────────────────────────

def test_the_success_ping_is_reachable_only_when_healthy(hb: str) -> None:
    """The whole design in one assertion, and the version that actually holds.

    THE FIRST DRAFT ASSERTED THE PING CAME *AFTER* THE CHECKS, AND A MUTATION
    WALKED STRAIGHT THROUGH IT. Inserting

        curl -fsS -m 10 "$PING_URL" >/dev/null 2>&1
        if [ -n "$failures" ]; then ...

    passed all thirteen tests: it IS after the checks. It is also completely
    ungated — the naive cron line, rebuilt inside the file written to prevent
    it, reporting green through an outage.

    "After the checks" was never the property. The property is UNREACHABLE WHEN
    ANYTHING FAILED, and in a shell script the thing that makes it unreachable
    is the failure branch's `exit`. So that is what gets asserted: every
    success ping must sit after the exit that ends the unhealthy path.
    """
    fail_branch = hb.index('if [ -n "$failures" ]')
    # The exit that ends the failure branch — nothing after it runs when
    # anything was wrong.
    exit_pos = hb.index("exit 1", fail_branch)

    # Success pings: those NOT ending in /fail.
    success = [m.start() for m in re.finditer(r'curl -[^\n]*"\$PING_URL"[^\n]*', hb)
               if "/fail" not in m.group(0)]
    assert success, "no success ping found — the scan has drifted"
    for pos in success:
        assert pos > exit_pos, (
            "a success ping is reachable while `failures` is non-empty — this "
            "reports a healthy system during an outage, which is the exact "
            "defect the script exists to avoid"
        )


def test_no_ping_happens_before_the_checks_have_run(hb: str) -> None:
    """Belt to the previous test's braces: nothing pings anything before the
    gateway and bridge have been probed."""
    first_check = hb.index("/gateway/health")
    for m in re.finditer(r"\bcurl -[^\n]*PING_URL[^\n]*", hb):
        assert m.start() > first_check, (
            f"a ping is sent before any check has run: {m.group(0)}"
        )


def test_an_unhealthy_system_pings_fail_not_success(hb: str) -> None:
    """Silence would eventually alert, but only after the grace period.

    An explicit /fail says so immediately, which is the difference between
    noticing an outage in one minute and in eleven.
    """
    assert re.search(r'"\$\{PING_URL%/\}/fail"', hb), "there is no failure ping"
    # and it is reached only on the failure branch
    fail_branch = hb.index('if [ -n "$failures" ]')
    assert hb.index("/fail") > fail_branch


def test_a_refusing_gateway_counts_as_healthy(hb: str) -> None:
    """403 is the CORRECT answer from the gateway — it requires a secret.

    Reading it as an outage would page the operator every five minutes for a
    working system, and an alert that cries wolf is how a real one gets
    ignored. This is the assertion most likely to be "fixed" into a bug.
    """
    assert re.search(r"200\|401\|403", hb), (
        "the gateway's authenticated refusal is no longer treated as healthy"
    )


def test_it_sends_nothing_when_it_cannot_check(hb: str) -> None:
    """The third outcome, and the one a two-state design gets wrong.

    No curl, or no configured URL, means the question went unanswered. Pinging
    /fail would report the bot as down on the strength of a broken harness;
    pinging success would be a lie. Sending nothing lets the dead-man's switch
    alert on its timer — silence is the honest output when you do not know.
    """
    for guard in ("command -v curl", 'if [ -z "$PING_URL" ]'):
        assert guard in hb, f"the {guard!r} guard is gone"
    assert hb.count("exit 3") >= 2, (
        "could-not-check no longer has its own exit code, so it is being "
        "reported as one of the two verdicts"
    )


def test_the_ping_url_is_never_logged(hb: str) -> None:
    """It is a secret: anyone holding it can silence the alerting (F-15).

    A URL echoed into a cron log that gets pasted into an issue is a disclosed
    credential.
    """
    for line in hb.splitlines():
        if re.match(r"\s*log ", line):
            assert "PING_URL" not in line, f"the ping URL is logged: {line.strip()}"


def test_monitoring_cannot_hang_the_box(hb: str) -> None:
    """Every curl is bounded. A missed ping is recovered five minutes later;
    a wedged cron job is not.

    Anchored on `curl -`, the invocation form, because two earlier spellings of
    this assertion both failed on things that are not invocations at all:

        command -v curl >/dev/null     asks whether curl EXISTS; opens no socket
        log "curl is missing ..."      PROSE, inside a string

    The second is the shape CLAUDE.md records: text that quotes the thing is
    indistinguishable from code doing it, and `_code_only` blanks comment lines
    but cannot blank a string. Both times the assertion was wrong and the
    script was right — bolting `--max-time` onto a lookup, or rewording an
    error message, to satisfy a scan would have been the worse repair.
    """
    invocations = [m.group(0) for m in re.finditer(r"\bcurl -[^\n]*", hb)]
    assert len(invocations) >= 3, (
        f"only {len(invocations)} curl invocation(s) found; the scan has drifted "
        "and is asserting nothing"
    )
    for inv in invocations:
        assert "-m " in inv or "--max-time" in inv, f"unbounded curl: {inv}"


# ── the deploy gate asks BOTH targets ──────────────────────────────────────

def test_it_checks_the_web_container_and_the_bot_box(vd: str) -> None:
    """The failure it exists for: one target verified, the other never asked.

    A deploy to the box cannot fix anything under app/, and nothing said so.
    """
    assert "/api/version" in vd, "the web container is never asked what it serves"
    assert "/gateway/health" in vd, "the gateway is never probed"
    assert "8000/health" in vd or "$BRIDGE_URL/health" in vd, "the bridge is never probed"


def test_the_web_check_compares_content_not_a_deploy_log(vd: str) -> None:
    """Logs say what was ATTEMPTED. The hashes say what is being served."""
    assert "version" in vd and "buildInfo" in vd, (
        "the expected hashes are no longer computed from the local checkout"
    )
    assert "live_build" in vd and "live_assets" in vd


def test_both_hashes_are_compared_because_the_pair_is_the_diagnosis(vd: str) -> None:
    """build alone cannot distinguish a server-only deploy from a stuck client,
    and assets alone cannot see a change under app/lib — which is precisely
    where the sign-in fix lived.

    ANCHORED ON THE LINE THAT DECIDES "OK", not on the two comparisons
    appearing anywhere. The first draft asserted both regexes were present, and
    a mutation dropping `assets` from the success condition passed — because
    the DIAGNOSTIC branch below it still mentions both, and the scan matched a
    neighbour and credited it. Same shape as the season-delete test that read
    the next route's `adminOnly` and called the route guarded.
    """
    at = vd.index('ok "serving this checkout')
    # the condition immediately governing that success line
    decider = vd.rindex("if [", 0, at)
    condition = vd[decider:at]
    assert '"$live_build" = "$want_build"' in condition, (
        "the success condition does not compare the server-side hash"
    )
    assert '"$live_assets" = "$want_assets"' in condition, (
        "the success condition does not compare the asset hash — a client-only "
        "change could be reported as fully deployed"
    )


def test_a_refusing_gateway_is_healthy_here_too(vd: str) -> None:
    """Same claim as the heartbeat, and it must not drift between them."""
    assert re.search(r"200\|401\|403", vd)


def test_unreachable_is_not_the_same_as_wrong(vd: str) -> None:
    """Reporting an unreachable endpoint as a failed deploy sends an operator
    to roll back a deploy that landed perfectly."""
    assert "exit 3" in vd or 'worst=3' in vd
    assert "UNKNOWN" in vd
    assert re.search(r"INCOMPLETE", vd), "there is no could-not-check summary"


def test_a_failure_is_never_softened_into_could_not_tell(vd: str) -> None:
    """`unk` must only downgrade a CLEAN run. If it could overwrite a FAIL,
    a single unreachable check would launder a real failure into a shrug."""
    unk_line = next(ln for ln in vd.splitlines() if ln.startswith("unk()"))
    assert '[ "$worst" -eq 0 ]' in unk_line, (
        "unk() can overwrite a failure verdict"
    )


def test_both_scripts_are_executable() -> None:
    """cron and an operator both invoke these directly; a lost +x is a
    monitoring system that silently never runs."""
    import os
    for p in (HEARTBEAT, VERIFY):
        assert os.access(p, os.X_OK), f"{p.name} is not executable"
