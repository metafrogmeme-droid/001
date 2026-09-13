"""A symbol with a slash in it cannot travel as a path segment.

MEASURED LIVE, 2026-09-13, against the V308 tunnels, same host, same minute:

    GET https://api.humanoid-traders.com/patterns/BTCUSDT      -> 200, real read
    GET https://api.humanoid-traders.com/patterns/BTC%2FUSDT   -> 404, HTML

The 404 body was the WEBSITE'S "Not found" page, not the bridge's JSON — so
the bridge never saw the request. Every symbol this product names has a slash
in it (`BTC/USDT`), and `app/routes/insight.js` and `app/routes/patterns.js`
both built `${BOT_API_URL}/<route>/${encodeURIComponent(sym)}`. The Insight
and Patterns panels therefore sent, on every single call, the one URL shape
that never arrives — while the bridge behind them answered everything else
correctly, so the panel's honest 502 pointed at a healthy service.

THE LESSON WAS ALREADY WRITTEN DOWN ONE HOP AWAY. `insight.js` carries a
comment saying the symbol travels as a query param "because several hosting
proxies (including the live deployment's) reject the %2F an encoded-slash
path segment needs, 404ing at the edge before Express ever sees the request".
That is this defect, diagnosed, for the INBOUND edge. Nobody asked whether
the call the same file makes OUTBOUND had the same shape. It did.

AND IT IS NOT THE EDGE. That was the first guess and driving it disproved it:
the ASGI server percent-decodes the path into `scope["path"]` BEFORE Starlette
matches, so `/insight/BTC%2FUSDT` has never been able to reach the handler
over HTTP at all, tunnel or no tunnel. It does not 404 cleanly either —
`api_bridge` mounts `StaticFiles` at `''`, which matches everything, so the
caller is handed the website. That is the HTML body measured above, and it is
why the panel's failure was an unparseable response rather than a readable
error.

NO TEST COULD SEE IT because no fixture did routing. `deepscan.test.js` keyed
its stub on `decodeURIComponent(url.pathname)` — deliberately, with a comment
saying "the real bridge (Starlette) decodes the path param, so match on the
decoded path here too". Half right, and the missing half is the whole defect:
it decodes, and THEN it matches, and the match fails. `insight_route.test.js`
matched `startsWith('/insight/')`, which no router does. Both stubs decode and
then re-match now, and answer HTML on a two-segment path, exactly as the
deployed stack does.
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def bridge_app(tmp_path_factory):
    import os
    import secrets
    os.environ.setdefault("JWT_SECRET", secrets.token_hex(32))
    import api_bridge
    return api_bridge.app


def _match(app, path: str):
    """The FIRST route a GET of `path` reaches, or None. Starlette's own
    matcher, not a string comparison: the question is what the router does.

    It returns the ROUTE, not `getattr(route, "endpoint", None)`. The first
    draft returned the endpoint and the "reaches nothing" assertion passed —
    not because nothing matched, but because what matched was a `Mount`,
    which has no `endpoint` attribute. A guard that acquits on a missing
    ATTRIBUTE where it meant to acquit on a missing MATCH is the quiet kind
    of wrong, and it was hiding the most useful half of this diagnosis.
    """
    from starlette.routing import Match

    scope = {"type": "http", "method": "GET", "path": path,
             "root_path": "", "headers": []}
    for route in app.routes:
        if route.matches(scope)[0] is Match.FULL:
            return route
    return None


def _api_endpoint(app, path: str):
    """The API handler a GET of `path` reaches, or None — mounts excluded."""
    from fastapi.routing import APIRoute
    from starlette.routing import Match

    scope = {"type": "http", "method": "GET", "path": path,
             "root_path": "", "headers": []}
    for route in app.routes:
        if isinstance(route, APIRoute) and route.matches(scope)[0] is Match.FULL:
            return route.endpoint
    return None


@pytest.mark.parametrize("route", ["insight", "patterns"])
def test_the_query_form_reaches_the_handler(bridge_app, route):
    """The shape the web sends now. No path segment, so nothing for an edge
    to decode and re-match."""
    ep = _api_endpoint(bridge_app, f"/{route}")
    assert ep is not None, f"/{route} reaches no handler"
    assert ep.__name__ == route


@pytest.mark.parametrize("route", ["insight", "patterns"])
def test_the_path_form_still_works_for_callers_inside_the_network(
        bridge_app, route):
    """RED HERRING for the fix: the path form is not the defect and is not
    removed. Inside the compose network there is no edge and it works — and
    an old client pinned to it must not be broken by this change."""
    ep = _api_endpoint(bridge_app, f"/{route}/BTCUSDT")
    assert ep is not None and ep.__name__ == route


@pytest.mark.parametrize("route", ["insight", "patterns"])
def test_what_a_decoded_slash_delivers_is_the_website_and_not_the_api(
        bridge_app, route):
    """THE DEFECT ITSELF, and the mechanism is worth stating exactly.

    `%2F` is percent-decoded into `scope["path"]` before Starlette matches,
    so a symbol carried in a path segment arrives as TWO segments and no
    `/{route}/{symbol}` route can match it. It does not 404 cleanly, though:
    `api_bridge` mounts `StaticFiles` at `''`, which matches everything, so
    the caller is served the WEBSITE — an HTML page where JSON was expected,
    which `fetchJSON` then fails to parse into a 502 that reads as "the
    bridge is down" about a bridge answering every other request correctly.

    Both halves are asserted. "No API route matches" is the defect; "the
    mount matches anyway" is why the symptom was an unparseable body rather
    than a readable error, and it is the half a first draft missed.
    """
    from starlette.routing import Mount

    decoded = f"/{route}/BTC/USDT"
    assert _api_endpoint(bridge_app, decoded) is None
    assert isinstance(_match(bridge_app, decoded), Mount)


@pytest.mark.parametrize("route", ["insight", "patterns"])
def test_an_absent_symbol_is_refused_and_never_defaulted(bridge_app, route):
    """A query param can be missing where a path segment cannot, so the
    handler gains a case it never had. It must refuse, not pick a symbol:
    a default here would answer a question about BTC to someone who asked
    about nothing, and they would have no way to tell."""
    import api_bridge

    fn = getattr(api_bridge, route)
    assert fn.__defaults__ is not None
    sig = __import__("inspect").signature(fn)
    assert sig.parameters["symbol"].default == "", (
        "the absent symbol must fall to the validator, not to a ticker")
    assert not api_bridge._SYMBOL_RE.match(""), (
        "the empty symbol has to be rejected by the same regex that rejects "
        "every other junk value")


def test_no_web_route_sends_a_slash_bearing_symbol_as_a_path_segment():
    """The structural half, over BOTH surfaces at once — because this defect
    was two copies of one line and fixing one would have left the other.

    It reads the JS as text deliberately: the property is about the URL a
    template builds, and there is no seam to call. Comments are stripped, so
    the notes above each call site (which quote the broken shape on purpose)
    cannot satisfy or trip it.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    offenders = []
    for rel in ("app/routes/insight.js", "app/routes/patterns.js"):
        src = (root / rel).read_text(encoding="utf-8")
        code = re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", src, flags=re.S))
        for m in re.finditer(r"\$\{BOT_API_URL\}([^`]*)", code):
            url = m.group(1)
            seg = url.split("?")[0]
            if "${" in seg and "encodeURIComponent" in url:
                offenders.append(f"{rel}: {url[:70]}")
    assert offenders == [], (
        "a symbol interpolated into a PATH segment upstream is the shape the "
        "edge drops: " + "; ".join(offenders))
