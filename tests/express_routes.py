"""What paths does `app/server.js` actually serve?

Two tests ask this question, and until now only one of them could answer it.
`test_documented_links_have_routes.py` read `app.get(...)` out of `server.js`
and nothing else, which is enough for the pages our docs link and wrong for
anything mounted: 40-odd routers are attached with `app.use('/api/auth',
authRouter)`, and every path inside them was invisible.

That matters the moment a second caller appears. A resolver that cannot see
`/api/auth/referrals` does not report "I did not look" — it reports **404**,
which is the accusation this repo spends most of its guard tests preventing.
So the resolution lives here once, both callers share it, and the mount half
is covered rather than silently absent.

WHAT IT RESOLVES
----------------
* `app.get|post|put|delete|all('/path', ...)` in `server.js`
* `app.use('/prefix', require('./routes/x'))` — inline mount
* `app.use('/prefix', name)` where `name` came from `const name = require(...)`
  or `const { router: name } = require(...)` — the shape `app/auth.js` uses
* static pages: every `app/public/*.html` is served at `/<name>.html`

WHAT IT DOES NOT
----------------
Routers mounted on other routers, and any mount whose second argument is
computed rather than named. Both are reported through `unresolved_mounts()`
rather than dropped, because a mount we could not follow is **not** a mount
with no routes in it, and a caller that treats it as one is back to answering
"could not read" with "404".
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP = REPO / "app"
PUBLIC = APP / "public"
SERVER = APP / "server.js"

_VERBS = "get|post|put|patch|delete|all|head|options"
_APP_ROUTE = re.compile(rf"""\bapp\.(?:{_VERBS})\(\s*['"]([^'"]+)['"]""")
_ROUTER_ROUTE = re.compile(rf"""\brouter\.(?:{_VERBS})\(\s*['"]([^'"]+)['"]""")
_USE = re.compile(r"""\bapp\.use\(\s*['"](/[^'"]*)['"]\s*,\s*([^\n]+)""")
_REQUIRE = re.compile(r"""require\(\s*['"]([^'"]+)['"]\s*\)""")


def code_only(js: str) -> str:
    """Strip comments without stripping code, which is harder than it reads.

    The first draft did `re.sub(r"/\\*.*?\\*/", "", js, flags=re.S)` and then
    dropped `//` lines. `server.js:227` is a line comment containing the words
    `/api/*`, so the block pass opened a comment there and closed it 295 lines
    later on a real `/* ... */` — swallowing every route and mount in between.
    The resolver then reported `/leaderboard`, `/proof`, `/track` and `/roots`
    as UNSERVED. All four are served on the line the comment ate.

    That is the accusation-from-a-blind-spot this repo keeps re-learning, and
    it arrived inside the helper written to prevent it. So this walks the
    source once, tracking string and template literals, and only treats `//`
    and `/*` as comment openers when it is not inside one.
    """
    out, i, n = [], 0, len(js)
    while i < n:
        c = js[i]
        if c in "'\"`":
            quote, j = c, i + 1
            while j < n:
                if js[j] == "\\":
                    j += 2
                    continue
                if js[j] == quote:
                    break
                j += 1
            out.append(js[i:j + 1])
            i = j + 1
        elif js.startswith("//", i):
            j = js.find("\n", i)
            i = n if j < 0 else j
        elif js.startswith("/*", i):
            j = js.find("*/", i + 2)
            # Keep the newlines so line numbers in failure messages survive.
            out.append("\n" * js.count("\n", i, n if j < 0 else j + 2))
            i = n if j < 0 else j + 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _resolve_module(spec: str, base: Path) -> Path | None:
    if not spec.startswith("."):
        return None
    p = (base / spec).resolve()
    for cand in (p, p.with_suffix(".js"), p / "index.js"):
        if cand.is_file():
            return cand
    return None


def _mount_expr_module(expr: str, src: str) -> tuple[Path | None, str]:
    """`app.use(prefix, EXPR)` -> the file EXPR's router lives in.

    Returns `(path_or_None, why)`; `why` names the reason when it is None so
    the caller can report an unfollowed mount instead of an empty one.
    """
    m = _REQUIRE.search(expr)
    if m:
        f = _resolve_module(m.group(1), APP)
        return (f, "" if f else f"unresolvable require({m.group(1)!r})")

    name = expr.strip().rstrip(");").strip()
    if not re.fullmatch(r"[A-Za-z_$][\w$]*", name):
        return (None, f"computed mount argument: {expr.strip()[:60]}")

    for pat in (rf"""\b(?:const|let|var)\s+{re.escape(name)}\s*=\s*require\(\s*['"]([^'"]+)['"]""",
                rf"""\b(?:const|let|var)\s*\{{[^}}]*\brouter\s*:\s*{re.escape(name)}\b[^}}]*\}}\s*=\s*require\(\s*['"]([^'"]+)['"]""",
                rf"""\b(?:const|let|var)\s*\{{[^}}]*\b{re.escape(name)}\b[^}}]*\}}\s*=\s*require\(\s*['"]([^'"]+)['"]"""):
        m = re.search(pat, src)
        if m:
            f = _resolve_module(m.group(1), APP)
            return (f, "" if f else f"{name} = require({m.group(1)!r}) not on disk")
    return (None, f"no require() found for {name}")


def _join(prefix: str, path: str) -> str:
    return ("/" + prefix.strip("/") + "/" + path.strip("/")).replace("//", "/").rstrip("/") or "/"


def _server_src() -> str:
    return code_only(SERVER.read_text(encoding="utf-8"))


def unresolved_mounts() -> list[str]:
    """Mounts this resolver could not follow. Never silently empty."""
    src = _server_src()
    out = []
    for prefix, expr in _USE.findall(src):
        f, why = _mount_expr_module(expr, src)
        if f is None:
            out.append(f"{prefix}: {why}")
    return out


def registered_patterns() -> list[str]:
    """Every express path pattern the app registers, prefixes applied."""
    src = _server_src()
    pats = list(_APP_ROUTE.findall(src))
    for prefix, expr in _USE.findall(src):
        f, _ = _mount_expr_module(expr, src)
        if f is None:
            continue
        try:
            body = code_only(f.read_text(encoding="utf-8"))
        except OSError:
            continue
        pats += [_join(prefix, p) for p in _ROUTER_ROUTE.findall(body)]
    return pats


def static_pages() -> set[str]:
    return {f"/{f.name}" for f in PUBLIC.glob("*.html")}


def pattern_to_regex(pat: str) -> re.Pattern:
    """Express route pattern -> regex. `:name?` is an optional segment."""
    out = ["^"]
    for seg in pat.strip("/").split("/") if pat.strip("/") else []:
        if seg.startswith(":"):
            out.append("(?:/[^/]+)?" if seg.endswith("?") else "/[^/]+")
        else:
            out.append("/" + re.escape(seg))
    out.append("/?$")
    return re.compile("".join(out) or "^/$")


def _names_a_family(path: str, pat: str) -> bool:
    """Does `path` name a route family whose remaining segments are all params?

    `/call` is cited by the roadmap; the app serves `/call/:key`. Typing the
    bare path 404s, but the CLAIM being made is that per-call receipts are
    reachable, and they are — the citation names the family. Accepted only
    when EVERY remaining segment is a parameter: `/foo` does not name
    `/foo/bar/:id`, because reaching that needs a literal nobody wrote down.
    """
    want = [s for s in path.strip("/").split("/") if s]
    have = [s for s in pat.strip("/").split("/") if s]
    if len(have) <= len(want) or have[:len(want)] != want:
        return False
    return all(s.startswith(":") for s in have[len(want):])


def is_served(path: str, patterns: list[str] | None = None) -> bool:
    """Exact resolution only — what a reader typing this path would get."""
    pats = registered_patterns() if patterns is None else patterns
    if path in static_pages():
        return True
    if path == "/":
        return "/" in pats
    return any(pattern_to_regex(p).match(path) for p in pats)


def is_reachable(path: str, patterns: list[str] | None = None) -> bool:
    """Served exactly, or naming a family that is. See `_names_a_family`."""
    pats = registered_patterns() if patterns is None else patterns
    return is_served(path, pats) or any(_names_a_family(path, p) for p in pats)
