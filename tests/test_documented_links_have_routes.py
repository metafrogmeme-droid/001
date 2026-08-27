"""Every site URL our own docs hand a reader must resolve to a route we serve.

`docs/SUBMISSION.md` opened with **Dashboard: [Live Dashboard](…/hub.html)**.
There is no `hub.html` anywhere in `app/public` and no route claims `/hub`, so
that link was a 404 on both hosts — measured, not inferred. It is the first
line of a hackathon submission, directly under "Live Bot", which means the one
reader most likely to click it was a judge checking whether the thing is real.

This is `test_canonicals_have_routes.py` one surface over, and it exists for the
reason that file's own docstring gives: 27 of 28 canonicals were fine, the set
had nothing watching it, and the miss survived. The same was true here — eight
of the nine distinct paths our docs publish are served. A systemic bug announces
itself; a single stale link only gets found by checking the set.

ONLY LINK TARGETS, NOT EVERY URL IN THE TEXT
--------------------------------------------
A bare URL inside a fenced block or a deploy note is a QUOTATION — `dashboard_api`
quotes the origin in a comment about a hardcoded CORS entry, and
`docs/DEEP_AUDIT_2026-08-14.md` quotes that comment. Neither publishes a link.
Scanning every URL would make this test fail on prose describing a bug, which is
the "asserting a short string is ABSENT" trap CLAUDE.md records misfiring three
times in one sweep. So this reads `[text](url)` and `<a href="url">` — the things
a human actually clicks.

WHY THIS RUNS OFFLINE
---------------------
Same reason as the canonical guard: CI has no network to production, and a check
that skips when it cannot reach the site reports green for precisely the reason
it could not check. Routes come out of `app/server.js` — a claim about our own
configuration, checkable from our own source.

WHAT THIS DOES NOT CHECK
------------------------
Which HOST the docs name. `bot/utils/site_url.py` pins `DEFAULT_SITE_URL` to the
deployment host on purpose — it also routes the bot's sync POSTs, and
`test_site_url_single_source.py` fails if that flips — so "the docs should say
www.humanoid-traders.com" is a product call, not a broken link, and it is not
this file's business. Both hosts serve the same app; a path served on one is
served on the other. This asserts the PATH resolves.
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parents[1]
PUBLIC = REPO / "app" / "public"
SERVER = REPO / "app" / "server.js"

HOSTS = ("pmvc58g2.mule.page", "www.humanoid-traders.com")

_ROUTE = re.compile(r"""app\.get\(\s*['"]([^'"]+)['"]""")
# `[text](url)` and `<a href="url">` — link targets only, see the docstring.
_MD_LINK = re.compile(r"\]\(\s*(https?://[^\s)]+?)\s*\)")
_HREF = re.compile(r"""<a\s[^>]*href=["'](https?://[^"']+)["']""", re.I)


def _code_only(js: str) -> str:
    """Drop whole-line `//` comments, as the canonical guard does — a comment
    naming a path must not count as a route serving it."""
    return "\n".join(ln for ln in js.splitlines()
                     if not ln.lstrip().startswith("//"))


def _pattern_to_regex(pat: str) -> re.Pattern:
    """Express route pattern -> regex. `:name?` is an optional segment."""
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
    static = {f"/{f.name}" for f in PUBLIC.glob("*.html")}
    if path in static:
        return True
    if path == "/":
        return "/" in _registered_patterns()
    return any(_pattern_to_regex(p).match(path) for p in _registered_patterns())


def _doc_files() -> list:
    files = [REPO / "README.md", REPO / "README.zh-TW.md"]
    files += sorted((REPO / "docs").rglob("*.md"))
    return [f for f in files if f.is_file()]


def _documented_links() -> dict:
    """{path: ["relative/file.md:line", ...]} for every published link target."""
    found: dict[str, list] = {}
    for f in _doc_files():
        rel = f.relative_to(REPO)
        for i, line in enumerate(f.read_text(encoding="utf-8",
                                             errors="replace").splitlines(), 1):
            for m in list(_MD_LINK.finditer(line)) + list(_HREF.finditer(line)):
                url = m.group(1)
                parsed = urlparse(url)
                if parsed.hostname not in HOSTS:
                    continue
                path = parsed.path or "/"
                found.setdefault(path, []).append(f"{rel}:{i}")
    return found


def test_every_documented_link_has_a_route():
    links = _documented_links()
    missing = {p: where for p, where in links.items() if not _is_served(p)}
    assert not missing, (
        "our own docs publish these links, and this server serves none of "
        "them — every one is a 404 for whoever clicks it:\n  "
        + "\n  ".join(f"{p}\n      {', '.join(w)}"
                      for p, w in sorted(missing.items()))
        + "\n\nPoint the link at the URL that resolves, or add the route.")


def test_a_quoted_url_is_not_a_published_link():
    """Only link TARGETS count.

    Driven with synthetic text rather than by asserting some real doc's URL is
    present, so this cannot break when unrelated prose moves — the failure mode
    the canonical guard's second test was rewritten to avoid.
    """
    quoted = "_EXTRA_ORIGINS = {\"https://pmvc58g2.mule.page/nope.html\"}"
    assert not _MD_LINK.findall(quoted) and not _HREF.findall(quoted), (
        "a URL quoted in prose or code was read as a published link")

    published = "[Live Dashboard](https://pmvc58g2.mule.page/dashboard)"
    assert _MD_LINK.findall(published) == [
        "https://pmvc58g2.mule.page/dashboard"], (
        "stripping quotations must not also drop real links — a scan that "
        "found nothing would pass this file forever")


def test_the_link_set_is_actually_being_read():
    """A scan that silently found nothing would pass over an empty set —
    absent read as clean, the failure this repo names most often."""
    links = _documented_links()
    assert len(links) >= 6, (
        f"only {len(links)} distinct documented paths parsed across "
        f"{len(_doc_files())} files — the scan is broken, not the docs")
    assert "/" in links, "the site root is linked from README; the scan missed it"
    assert len(_registered_patterns()) >= 25, (
        f"only {len(_registered_patterns())} routes parsed from server.js — "
        "with no routes found, every documented link would look like a 404")
