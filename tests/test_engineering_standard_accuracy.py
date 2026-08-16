"""docs/ENGINEERING_STANDARD.md is a promise made to people outside this repo.

CLAUDE.md is written for contributors and is pinned by
`test_claude_md_accuracy.py`. This is the outward-facing half — the page a
reader lands on when deciding whether the numbers on the site can be believed —
and it needs the same treatment for a sharper reason.

A contributor who hits a stale line in CLAUDE.md loses an afternoon. A visitor
who reads "this is enforced on every change" about a gate that was deleted last
month has been *lied to*, by a document whose entire subject is not lying. The
failure mode is self-refuting, which makes it the most expensive kind here:
every other honest claim on the page goes down with it.

So every checkable sentence is checked against the mechanism behind it. Two
things in particular:

  · a claim of enforcement must name a gate that RUNS ON EVERY CHANGE, verified
    against the preflight plan (which is parsed from ci.yml, so it cannot
    drift);
  · a practice that is NOT enforced must say so. The page lists one, and the
    line "Nothing enforces this" is load-bearing — moving mutation checking
    back into the machinery list would be exactly the substitution of intention
    for enforcement the page is about.
"""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOC = (ROOT / "docs" / "ENGINEERING_STANDARD.md").read_text(encoding="utf-8")

# The "You will never see" table quotes the shapes it forbids — `0.00%`,
# `12 (7W/4L)`. A scan for those shapes across the whole file therefore fails
# on the page's own examples, which is the comment-quotes-the-forbidden-string
# trap in a new costume (five false failures in this repo so far). Split once,
# here, and scan the prose.
_TABLE_START = DOC.index("| You will never see")
_TABLE_END = DOC.index("### Colour is a claim")
EXAMPLES = DOC[_TABLE_START:_TABLE_END]
PROSE = DOC[:_TABLE_START] + DOC[_TABLE_END:]

#: Markdown hard-wraps at 80 columns, so a sentence fragment is as likely to
#: straddle a newline as not. Phrase assertions run against this; the first
#: one written without it failed on "renders\nnothing at all" — a guard
#: reporting a defect that was only ever a line break.
FLAT = re.sub(r"\s+", " ", DOC)


def _plan():
    sys.path.insert(0, str(ROOT / "scripts"))
    import preflight
    return preflight.steps(fast=False)


# ── every mechanism it claims is enforced actually runs on every change ───

#: prose phrase → (file that implements it, substring of the CI command that
#: runs that file). Curated, because the page is written for a reader and names
#: no paths; the mapping is what a reader could not check for themselves.
ENFORCEMENT = [
    ("Structural honesty tests",
     "app/test/panel_failure_honesty.test.js", "npm test"),
    ("Planted red herrings",
     "app/test/engine_status_scenarios.test.js", "npm test"),
    ("Planted red herrings",
     "tests/test_surface_scenarios.py", "ci_test_gate.py"),
    ("Ratchets, not resolutions",
     "tests/known_failures.txt", "ci_test_gate.py"),
    ("Ratchets, not resolutions",
     "tests/unreachable_baseline.txt", "ci_test_gate.py"),
    ("Ratchets, not resolutions",
     "app/test/asset_versions.json", "npm test"),
    ("An adversarial pass on the risk engine",
     "scripts/red_team.py", "red_team.py"),
]


@pytest.mark.parametrize("phrase,rel,command", ENFORCEMENT)
def test_a_claimed_mechanism_exists_and_is_gated(phrase, rel, command):
    assert phrase in DOC, f"the page stopped claiming: {phrase}"
    assert (ROOT / rel).exists(), (
        f"the page claims {phrase!r} is enforced; {rel} is gone")
    assert any(command in cmd for _, cmd, _ in _plan()), (
        f"{rel} is no longer run by any CI step ({command!r} is not in the "
        f"preflight plan) — the page claims it runs on every change")


def test_the_structural_test_still_does_what_the_page_says_it_does():
    """"Every panel loader must either throw or omit missing sources
    individually. Neither fails the suite by construction."" A file that exists
    but no longer asserts that would satisfy the check above and still make the
    sentence false."""
    guard = (ROOT / "app" / "test" / "panel_failure_honesty.test.js").read_text(
        encoding="utf-8")
    assert "mustRead" in guard and "renderPanel" in guard
    assert "return null" in guard, "the throw-vs-null distinction is the claim"


def test_the_red_herring_practice_is_really_in_those_suites():
    """Both directions, plus the herring. The two suites spell the vocabulary
    differently (`MUST_SAY` / `MUST include`), so match the shape rather than
    one file's wording — pinning a name would fail on a rename and pass on a
    suite that quietly dropped the negative half, which is backwards."""
    for rel in ("tests/test_surface_scenarios.py",
                "app/test/engine_status_scenarios.test.js"):
        src = (ROOT / rel).read_text(encoding="utf-8").lower()
        assert "red herring" in src, f"{rel} no longer plants one"
        assert re.search(r"must[ _](say|include)", src), (
            f"{rel} lost its MUST-say assertions")
        assert re.search(r"must[ _]not[ _](say|include)", src), (
            f"{rel} asserts only what a card says, never what it must not — "
            f"the half that catches an over-read")


def test_the_sharpest_example_is_true_of_the_receipt_page():
    """The page singles this one out — "a receipt that cannot be fully loaded
    renders nothing at all" — and gives the reason: a visitor who copies a hash
    that does not verify writes off the whole publisher, true parts included.

    So it is the row on that table most worth checking, and it is checkable:
    both ends of the receipt path must refuse rather than serve a shape.
    """
    assert "renders nothing at all" in FLAT

    # The server half already has a guard of its own, so point at it rather
    # than re-deriving one here. The first attempt did re-derive it, as
    # "'Verify feed unavailable' appears in call.js" — which survived a
    # mutation that gutted a failure branch, because two other branches still
    # carried the string. A guard that passes while the thing it guards is
    # broken is the exact shape this page is about, and it is worth noting
    # that it took a mutation check rather than a reading to find it.
    guard = (ROOT / "app" / "test" / "latest_call_route.test.js").read_text(
        encoding="utf-8")
    assert "an unreadable database is a 503, and never an empty success" in guard, (
        "the receipt route's own honesty guard is gone; the page's sharpest "
        "example is now unbacked")

    page = (ROOT / "app" / "public" / "call.html").read_text(encoding="utf-8")
    assert re.search(r"if \(res\.status !== 200 \|\| !d \|\| !d\.seal\)", page), (
        "call.html must refuse to render a receipt with no seal — a "
        "seal-shaped page without a seal is the failure this row names")


def test_the_ratchets_really_fail_in_both_directions():
    """"a stale entry must be removed in the same commit that fixes it" is the
    half people drop, and it is the half that stops a baseline from hiding a
    real bug."""
    gate = (ROOT / "scripts" / "ci_test_gate.py").read_text(encoding="utf-8")
    assert "known_failures" in gate
    assert "hard failure" in gate.lower(), (
        "the page claims a baseline entry that starts passing is a failure")

    unreach = (ROOT / "tests" / "test_no_new_unreachable_modules.py").read_text(
        encoding="utf-8")
    assert "baseline" in unreach and "stale" in unreach.lower()

    ratchet = (ROOT / "app" / "test" / "cache_buster_ratchet.test.js").read_text(
        encoding="utf-8")
    assert "nobody references" in ratchet, (
        "the manifest no longer rejects a stale entry, so it is a one-way "
        "ratchet and the page overstates it")


def test_the_scenario_count_it_quotes_is_the_real_one():
    """A number in prose is the part that rots first — CLAUDE.md's own gate
    count changed under it twice. This one is cheap to check: run the thing."""
    m = re.search(r"\*\*An adversarial pass on the risk engine\.\*\* (\w+) scenarios",
                  DOC)
    assert m, "the adversarial-pass bullet is gone or reworded"
    words = {"twenty": 20, "twenty-five": 25, "thirty": 30, "forty": 40,
             "fifty": 50, "sixty": 60}
    claimed = words.get(m.group(1).lower())
    # Refusing an unknown word rather than skipping: a .get() returning None
    # and the test passing anyway means the count silently stops being checked
    # exactly when somebody changes it.
    assert claimed is not None, f"unparsed count word: {m.group(1)}"

    sys.path.insert(0, str(ROOT / "scripts"))
    import red_team
    report = red_team.run()
    assert claimed == report.total_scenarios, (
        f"the page says {claimed} scenarios, the red team runs "
        f"{report.total_scenarios}")
    assert report.failed == 0, (
        f"{report.failed} scenarios got past the risk engine — the page says "
        f"this is gated at zero")


# ── and the one thing that is NOT enforced says so ────────────────────────

def test_mutation_checking_is_not_presented_as_machinery():
    """The page's own rule, applied to the page: a habit described as a gate is
    the same substitution as a guess described as a measurement.

    Mutation checking is the practice that has caught the most here, so it
    belongs on the page — but nothing runs it, and the section header saying
    "How it is enforced" must not swallow it.
    """
    assert "Mutation checking" in DOC
    habit = DOC.index("### The one that is a habit, not a gate")
    assert DOC.index("Mutation checking") > habit, (
        "mutation checking moved into the enforced list — nothing enforces it")
    assert "Nothing enforces this" in FLAT
    tail = DOC[habit:]
    assert "discipline is weaker than" in re.sub(r"\s+", " ", tail), (
        "the page must rank the habit below the gates, not beside them")


# ── the limits it publishes ───────────────────────────────────────────────

@pytest.mark.parametrize("limit", [
    "Not that the numbers are good",
    "Not that nothing will break",
    "Not that every call is sealed",
    "Not investment advice",
])
def test_it_keeps_stating_what_it_does_not_promise(limit):
    """A trust page that drops its limits section reads as a guarantee, which
    is the misreading it exists to prevent."""
    assert limit in FLAT


def test_it_never_makes_a_performance_claim():
    """The page is about honesty, so it is the single worst place on the site
    for a number that flatters. Nothing outside the "you will never see" table
    may carry a figure at all."""
    assert not re.search(r"\$\s?\d", PROSE), "a dollar figure reached the page"
    assert not re.search(r"\d+\s?%", PROSE), (
        "a percentage outside the counter-examples reads as a track record")
    assert not re.search(r"\d+W/\d+L", PROSE)
    # And the examples block must still BE counter-examples, not a showcase.
    assert "You will never see" in EXAMPLES


# ── the reader is told to check things that exist ─────────────────────────

def test_every_endpoint_it_points_a_reader_at_is_mounted():
    server = (ROOT / "app" / "server.js").read_text(encoding="utf-8")
    for route in re.findall(r"`GET (/api/[\w:/.-]+)`", DOC):
        mount = "/".join(route.split("/")[:3])          # /api/call/:key → /api/call
        assert f"'{mount}'" in server, (
            f"the page tells readers to fetch {route}; {mount} is not mounted")


def test_the_documents_it_links_exist():
    for rel in re.findall(r"\]\(\./([\w./-]+)\)", DOC):
        assert (ROOT / "docs" / rel).exists(), f"broken link: docs/{rel}"
    for rel in re.findall(r"\]\(\.\./([\w./-]+)\)", DOC):
        assert (ROOT / rel).exists(), f"broken link: {rel}"


def test_no_path_it_names_is_missing():
    """The curated lists above are curated; this catches a path added to the
    page later without anyone pinning it. Directories count — the page sends
    readers to `app/test/` and `tests/` to read the guards for themselves."""
    named = set(re.findall(r"`((?:app|bot|docs|scripts|tests)/[\w./-]*)`", DOC))
    missing = sorted(p for p in named if not (ROOT / p).exists())
    assert missing == [], f"the page points at missing paths: {missing}"


def test_it_points_at_claude_md_and_claude_md_still_holds_the_rule():
    assert "CLAUDE.md" in DOC
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "Unreadable is never zero" in claude, (
        "the page sends readers to CLAUDE.md for the same rule in blunter "
        "form — it must still be there")


# ── a page nobody can reach is a page that does not exist ─────────────────

def test_both_published_documents_are_linked_from_a_public_surface():
    """The lesson from `token_dossier` and from #999, one level up: a module
    nothing calls and a card nothing renders are indistinguishable from broken,
    and so is a specification no visitor can find.

    PROVABLE_CALLS_SPEC.md shipped one commit before this file with no link to
    it from anywhere on the site — correct, complete, and unreachable. Both are
    linked from /provable now, and this keeps them linked.
    """
    pv = (ROOT / "app" / "public" / "provable.html").read_text(encoding="utf-8")
    for name in ("PROVABLE_CALLS_SPEC.md", "ENGINEERING_STANDARD.md"):
        assert name in pv, f"{name} is published but reachable from nowhere"


# ── F-15 ──────────────────────────────────────────────────────────────────

def test_no_secret_or_internal_config_is_published_in_it():
    assert re.search(r"0x[a-fA-F0-9]{40}", DOC) is None
    for pat in (r"\bsk-[A-Za-z0-9]{16,}", r"SECRET\s*=\s*\S{8,}",
                r"\b[a-z0-9-]+\.trycloudflare\.com", r"mysql://", r"\bBearer\s+\S{8,}"):
        assert not re.search(pat, DOC), f"secret-shaped text matched {pat}"
