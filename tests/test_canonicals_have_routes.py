"""Every declared canonical URL must resolve to a route this server registers.

`swap.html` shipped with `<link rel="canonical" href=".../swap">` and no route
ever claimed `/swap`. The page lived at `/swap.html` and told search engines its
preferred address was a 404 — so the canonical, whose entire job is to say
"index THIS url", pointed at nothing. 27 of the other 28 were fine, which is
why it survived: it is not a systemic bug, it is one page that was missed, and
nothing was watching the set.

WHY THIS RUNS OFFLINE

The obvious version fetches each canonical and asserts 200. CI has no network
to the production host, and a test that silently skips when it cannot reach the
site is worse than no test — it would report green for exactly the reason it
could not check. So this reads the routes out of `server.js` instead: a claim
about our own configuration, checkable from our own source.

STRIP COMMENTS BEFORE SCANNING SOURCE

The comment added beside the new `/swap` route explains the fix and therefore
contains the string `/swap`. Scanning raw text would let that comment satisfy
the check after somebody deleted the route — prose standing in for code, which
is the trap CLAUDE.md records five times and which the function-reachability
ratchet hit two commits ago.
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parents[1]
PUBLIC = REPO / "app" / "public"
SERVER = REPO / "app" / "server.js"

_CANON = re.compile(r'rel="canonical"[^>]*href="([^"]+)"')
_ROUTE = re.compile(r"""app\.get\(\s*['"]([^'"]+)['"]""")


def _code_only(js: str) -> str:
    """Drop whole-line `//` comments. Deliberately not a general JS parser:
    it only has to stop a comment mentioning a path from counting as a route,
    and every comment in the block this protects is a whole-line one."""
    return "\n".join(ln for ln in js.splitlines()
                     if not ln.lstrip().startswith("//"))


def _pattern_to_regex(pat: str) -> re.Pattern:
    """Express route pattern -> regex.

    A literal comparison is not enough and the first draft of this file proved
    it by accusing `letter.html`, whose canonical `/letter` is served by
    `app.get('/letter/:week?')`. The `?` makes the segment optional, so the
    route does match — the check did not. That page returns 200 in production;
    the test was wrong, not the code.

    `:name?` -> an optional segment, `:name` -> a required one.
    """
    out = ["^"]
    for seg in pat.strip("/").split("/") if pat.strip("/") else []:
        if seg.startswith(":"):
            out.append("(?:/[^/]+)?" if seg.endswith("?") else "/[^/]+")
        else:
            out.append("/" + re.escape(seg))
    out.append("/?$")
    return re.compile("".join(out) or "^/$")


def _registered_patterns() -> list:
    return _ROUTE.findall(_code_only(SERVER.read_text(encoding="utf-8")))


def _is_served(path: str) -> bool:
    if path == "/":
        return "/" in _registered_patterns()
    return any(_pattern_to_regex(p).match(path) for p in _registered_patterns())


def _canonicals() -> dict:
    out = {}
    for f in sorted(PUBLIC.glob("*.html")):
        m = _CANON.search(f.read_text(encoding="utf-8", errors="replace"))
        if m:
            out[f.name] = urlparse(m.group(1)).path or "/"
    return out


def test_every_canonical_has_a_route():
    static = {f"/{f.name}" for f in PUBLIC.glob("*.html")}
    missing = {name: path for name, path in _canonicals().items()
               if not _is_served(path) and path not in static}
    assert not missing, (
        "these pages declare a canonical URL this server does not serve, so "
        "each one tells search engines to index a 404:\n  "
        + "\n  ".join(f"{n}  ->  {p}" for n, p in sorted(missing.items()))
        + "\n\nAdd the route in app/server.js, or point the canonical at the "
          "URL that actually resolves.")


def test_a_comment_mentioning_a_path_is_not_a_route():
    """The scan must read code, not prose.

    Driven with synthetic source rather than by asserting some real route's
    string is present in `server.js`. The first draft did the latter — it
    pinned `/swap`, which was true only while a `/swap` route existed, so
    removing that route broke this test for a reason having nothing to do with
    the property under test. A guard that fails when unrelated code moves is a
    guard people delete.
    """
    commented = _code_only("// app.get('/totally-invented-path', handler)\n")
    assert not _ROUTE.findall(commented), (
        "a commented-out route was counted as registered")

    real = _code_only("app.get('/totally-invented-path', handler)\n")
    assert _ROUTE.findall(real) == ["/totally-invented-path"], (
        "stripping comments must not also strip the code — a scan that finds "
        "nothing would pass this file forever")


def test_the_canonical_set_is_actually_being_read():
    """A scan that silently found nothing would pass forever.

    If the regex or the glob breaks, `_canonicals()` returns {} and the check
    above passes over an empty set — absent read as clean, which is the failure
    this repo names most often.
    """
    canon = _canonicals()
    assert len(canon) >= 25, f"only {len(canon)} canonicals parsed — the scan is broken"
    assert "index.html" in canon and canon["index.html"] == "/"
