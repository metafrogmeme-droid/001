"""The roadmap paints rows 🟢. Nothing asked whether they were true.

`docs/ROADMAP.md` prints its own rule directly above the legend:

    Status is checked against the running product, not against intent. Rows
    are moved to 🟢 only when the described capability is reachable by a user
    today ... a green marker on a half-built feature is the fastest way for a
    roadmap to stop being worth reading.

Nine rows carried 🟢 and nothing verified one of them. The paths they cite —
`/agents`, `/track`, `/proof`, `/provable`, `/roots`, `/call`, `/leaderboard`,
`/trader`, `/wallet-link` — are written as backticked CODE, not as markdown
links, so `test_documented_links_have_routes.py` never saw a single one. That
test guards `[text](url)`; a roadmap cites paths the way engineers write them.
Two documents, one claim, and the check covered the half that happened to use
the other syntax.

This is the same defect the repo keeps rediscovering one surface over. The
question is always WHICH OTHER SURFACE MAKES THE SAME CLAIM, and a roadmap
promising nine live capabilities is a loud one — it is the document a reader
deciding whether to trust the product opens first.

WHY THIS IS NOT A LINK CHECKER
------------------------------
It checks two different things, because there are two ways a green row lies:

1. It cites a path that nobody serves. Checked exactly, offline, against
   `app/server.js` and every router mounted on it.
2. It cites NOTHING, and asserts "live today" in prose. A test cannot weigh
   prose, and pretending otherwise would be a confident verdict from no data.
   Those rows are recorded in `tests/roadmap_unverified_baseline.txt` as a
   ratchet, so the set of unverifiable green claims can shrink and cannot
   quietly grow. Recording a gap is not the same as clearing it.

WHY A BARE `/call` PASSES
-------------------------
The app serves `/call/:key`, not `/call`. A reader typing the bare path gets a
404, but the CLAIM is that per-call receipts are reachable, and they are — the
citation names the family. `express_routes._names_a_family` accepts that only
when every remaining segment is a parameter, so `/foo` never stands in for
`/foo/bar/:id`, which needs a literal nobody wrote down.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests import express_routes as er

REPO = Path(__file__).resolve().parents[1]
ROADMAP = REPO / "docs" / "ROADMAP.md"
BASELINE = REPO / "tests" / "roadmap_unverified_baseline.txt"

STATUSES = ("🟢", "🟡", "🔵", "◆")

# Backticked paths only. A bare `/api/*` in prose is a description, not a
# citation — CLAUDE.md records that scanning unquoted text for short strings
# has misfired four times, every one of them on prose quoting the thing.
_PATH = re.compile(r"`(/[A-Za-z0-9_/:.\-]*)`")
_NAME = re.compile(r"\*\*(.+?)\*\*")

# `app.use('/api/chat', express.json({...}))` is body-parser middleware, not a
# router, so there is nothing behind it to follow. Pinned rather than filtered
# by guesswork: a NEW unfollowable mount is a router whose every path would
# read as a 404, and that must fail here rather than quietly widen a blind spot.
KNOWN_UNFOLLOWED_MOUNTS = {"/api/chat"}


class Row:
    __slots__ = ("line", "name", "horizon", "status", "evidence", "paths")

    def __init__(self, line, cells):
        self.line = line
        self.horizon, self.status, self.evidence = cells[1], cells[2], cells[3]
        m = _NAME.match(cells[0])
        self.name = m.group(1) if m else cells[0]
        self.paths = _PATH.findall(cells[0] + " " + cells[3])

    def __repr__(self):
        return f"ROADMAP.md:{self.line} {self.status} {self.name}"


def _rows() -> list[Row]:
    out = []
    for i, ln in enumerate(ROADMAP.read_text(encoding="utf-8").splitlines(), 1):
        if not ln.startswith("|"):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) < 4 or cells[2] not in STATUSES:
            continue
        out.append(Row(i, cells))
    return out


def _cited_paths() -> dict[str, list[str]]:
    """{path: ["ROADMAP.md:12", ...]} for every backticked path in the file,
    table rows and prose bullets alike."""
    found: dict[str, list[str]] = {}
    for i, ln in enumerate(ROADMAP.read_text(encoding="utf-8").splitlines(), 1):
        for p in _PATH.findall(ln):
            found.setdefault(p, []).append(f"ROADMAP.md:{i}")
    return found


def _baseline() -> set[str]:
    return {ln.strip() for ln in BASELINE.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")}


def _unverified_green() -> set[str]:
    return {r.name for r in _rows() if r.status == "🟢" and not r.paths}


class TestEveryCitedPathResolves:
    def test_the_roadmap_cites_no_path_we_do_not_serve(self):
        pats = er.registered_patterns()
        cited = _cited_paths()
        dead = {p: where for p, where in cited.items()
                if not er.is_reachable(p, pats)}
        assert not dead, (
            "the roadmap tells a reader these are live and this server serves "
            "none of them:\n  "
            + "\n  ".join(f"{p}  ({', '.join(w)})" for p, w in sorted(dead.items()))
            + "\n\nAdd the route, or stop citing it.")

    def test_a_green_row_that_cites_a_path_cites_a_live_one(self):
        """Stated separately from the sweep above so the failure names the ROW.

        A dead path in a 🔵 row is a typo. A dead path in a 🟢 row is the
        roadmap breaking its own promise, and the message should say which
        capability is making the claim, not just which string failed.
        """
        pats = er.registered_patterns()
        broken = [(r, p) for r in _rows() if r.status == "🟢"
                  for p in r.paths if not er.is_reachable(p, pats)]
        assert not broken, "\n".join(
            f"{r} cites {p}, which nothing serves" for r, p in broken)


class TestUnverifiableGreenClaimsAreRecorded:
    """Rows that assert "live today" in prose and cite nothing checkable."""

    def test_no_new_unverified_green_claim(self):
        new = _unverified_green() - _baseline()
        assert not new, (
            "these rows are 🟢 with no path a test can resolve:\n  "
            + "\n  ".join(sorted(new))
            + "\n\nCite the endpoint that makes the claim true. Add it to "
              f"{BASELINE.name} only if there genuinely is no such path, and "
              "say in the file why.")

    def test_the_baseline_has_no_stale_entries(self):
        stale = _baseline() - _unverified_green()
        assert not stale, (
            "these are recorded as unverifiable and are not:\n  "
            + "\n  ".join(sorted(stale))
            + f"\n\nDelete them from {BASELINE.name} in the same commit that "
              "cured them — a baseline that keeps cured entries stops being a "
              "measurement of anything.")

    def test_every_baseline_entry_names_a_real_row(self):
        """A typo'd key would sit in the file forever satisfying nothing."""
        names = {r.name for r in _rows()}
        unknown = _baseline() - names
        assert not unknown, (
            f"{BASELINE.name} names rows that do not exist: {sorted(unknown)}")


class TestTheScanIsActuallyReadingSomething:
    """A scan that silently found nothing passes every assertion above."""

    def test_the_rows_parse(self):
        rows = _rows()
        assert len(rows) >= 25, f"only {len(rows)} roadmap rows parsed"
        greens = [r for r in rows if r.status == "🟢"]
        assert len(greens) >= 8, (
            f"only {len(greens)} green rows parsed — if the table's shape "
            "changed, this file is checking nothing")

    def test_paths_are_being_found(self):
        cited = _cited_paths()
        assert len(cited) >= 10, (
            f"only {len(cited)} distinct paths found in the roadmap — with an "
            "empty set, every assertion above passes vacuously")
        for expect in ("/provable", "/agents", "/api/auth/referrals"):
            assert expect in cited, f"{expect} is cited and the scan missed it"

    def test_routes_are_being_found(self):
        pats = er.registered_patterns()
        assert len(pats) >= 200, (
            f"only {len(pats)} routes resolved from server.js and its mounts "
            "— with few routes found, every cited path looks like a 404")

    def test_every_mount_is_followed_or_named(self):
        unfollowed = {m.split(":", 1)[0] for m in er.unresolved_mounts()}
        assert unfollowed == KNOWN_UNFOLLOWED_MOUNTS, (
            f"mounts this resolver cannot follow changed: {sorted(unfollowed)}\n"
            "Every path inside an unfollowed router reads as UNSERVED here. "
            "Teach express_routes to follow it, or pin it above with a reason.")


class TestTheFamilyRule:
    """`/call` may stand for `/call/:key`. It may not stand for anything else."""

    @pytest.mark.parametrize("path,pattern,ok", [
        ("/call", "/call/:key", True),
        ("/trader", "/trader/:handle", True),
        ("/a", "/a/:x/:y", True),
        ("/a", "/a", False),          # exact match is is_served's job, not this
        ("/a", "/a/b", False),        # a literal segment nobody documented
        ("/a", "/a/b/:id", False),
        ("/a", "/ab/:id", False),     # prefix of the STRING, not of the path
        ("/api", "/api/auth/:x", False),
    ])
    def test_only_a_parameter_suffix_counts(self, path, pattern, ok):
        assert er._names_a_family(path, pattern) is ok

    def test_a_family_citation_never_reports_the_bare_path_as_served(self):
        """`is_served` answers what a reader typing the path would get.

        Keeping the two questions apart is the point: this file accepts a
        family citation deliberately, and a caller asking whether a URL
        resolves must not inherit that leniency by accident.
        """
        assert er.is_reachable("/call") and not er.is_served("/call")


class TestTheResolverDoesNotManufactureA404:
    """Both halves of this were live bugs in the first draft of the resolver."""

    def test_a_line_comment_quoting_a_path_does_not_eat_the_routes_after_it(self):
        """server.js:227 is a `//` comment containing the words `/api/*`.

        The first stripper ran the block-comment pass first, so that `/*`
        opened a comment which closed 295 lines later on a real `*/`. Every
        route in between vanished and `/leaderboard`, `/proof`, `/track` and
        `/roots` were all reported UNSERVED — four false 404s against pages
        that have shipped for months, produced by the helper written to
        prevent exactly that.
        """
        js = """
// a note mentioning /api/* and nothing else
app.get('/real', h);
try { x(); } catch (e) { /* swallowed */ }
app.get('/after-the-block', h);
"""
        assert er._APP_ROUTE.findall(er.code_only(js)) == [
            "/real", "/after-the-block"]

    def test_a_commented_out_route_is_not_a_route(self):
        js = "// app.get('/ghost', h);\n/* app.get('/spectre', h); */\napp.get('/real', h);"
        assert er._APP_ROUTE.findall(er.code_only(js)) == ["/real"]

    def test_a_path_inside_a_string_survives_stripping(self):
        js = "const note = 'see /*not a comment*/ here';\napp.get('/real', h);"
        assert "/real" in er._APP_ROUTE.findall(er.code_only(js))

    def test_mounted_router_paths_resolve(self):
        """`/api/auth/referrals` lives in app/auth.js, mounted by name.

        The resolver that only read `app.get` in server.js could not see it,
        and would have called the roadmap a liar for citing it.
        """
        assert er.is_served("/api/auth/referrals"), (
            "the mount half of the resolver stopped working")
