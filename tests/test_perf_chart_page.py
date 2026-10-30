"""
Performance charting page (/chart).

The page itself carries no data — it is served unauthenticated like the
dashboard index, and fetches /api/performance + /api/equitycurve client-side
with the operator's Bearer token (those endpoints stay gated). These tests pin
the route, the served HTML, and that the page is NOT under the /api/ prefix
(so auth_middleware doesn't block the page load itself).
"""

import asyncio
import inspect
from types import SimpleNamespace

from bot.web import dashboard_server as ds


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_chart_route_registered_and_not_api_prefixed():
    src = inspect.getsource(ds.create_app)
    assert 'add_get("/chart"' in src
    assert not "/chart".startswith("/api/")  # page load must not be auth-gated


def test_handler_serves_html():
    req = SimpleNamespace(app={"engine": SimpleNamespace()})
    resp = _run(ds.handle_performance_chart(req))
    assert resp.status == 200


def test_page_references_both_endpoints_and_auth():
    import pathlib
    html = (pathlib.Path(ds.__file__).parent / "performance_chart.html").read_text()
    # Consumes both analytics endpoints...
    assert "/api/performance" in html
    assert "/api/equitycurve" in html
    # The operator secret is posted once to /session and comes back as a
    # cookie. The page must not keep a copy a script can read.
    assert "/session" in html
    assert "credentials: 'same-origin'" in html
    assert "localStorage.setItem('dashboard_token'" not in html
    assert "cdn.jsdelivr.net" not in html
    assert "/vendor/chart.umd.min.js" in html
    # Uses the merged field names so it doesn't render blanks.
    for field in ("current_equity", "initial_balance", "drawdown_pct",
                  "by_symbol", "win_rate", "sharpe_ratio"):
        assert field in html, f"page missing field reference: {field}"


def test_page_degrades_without_chart_library():
    # If the vendored Chart.js file did not load, the stats/table must still
    # render — the page guards on `typeof Chart === 'undefined'` instead of
    # throwing.
    import pathlib
    html = (pathlib.Path(ds.__file__).parent / "performance_chart.html").read_text()
    assert "typeof Chart === 'undefined'" in html
    assert "chartNote" in html


def test_a_cookie_is_a_session_and_the_header_still_works(monkeypatch):
    monkeypatch.setattr(ds, "_DASHBOARD_TOKEN", "secret")

    async def _passthrough(request):
        return ds.web.json_response({"ok": True})

    via_cookie = SimpleNamespace(path="/api/performance", headers={}, method="GET",
                                 cookies={"dashboard_auth": "secret"})
    assert _run(ds.auth_middleware(via_cookie, _passthrough)).status == 200

    via_header = SimpleNamespace(
        path="/api/performance",
        headers={"Authorization": "Bearer secret"}, method="GET", cookies={})
    assert _run(ds.auth_middleware(via_header, _passthrough)).status == 200

    rejected = SimpleNamespace(path="/api/performance", headers={}, method="GET",
                               cookies={"dashboard_auth": "nope"})
    assert _run(ds.auth_middleware(rejected, _passthrough)).status == 401


def test_session_exchanges_the_token_for_an_httponly_cookie(monkeypatch):
    monkeypatch.setattr(ds, "_DASHBOARD_TOKEN", "secret")

    class Req:
        scheme = "https"
        headers = {}

        def __init__(self, token):
            self._token = token

        async def json(self):
            return {"token": self._token}

    ok = _run(ds.handle_session(Req("secret")))
    # aiohttp keeps the cookie on the response until it is prepared; the
    # header appears when the response is sent. The morsel is the fact.
    morsel = ok.cookies["dashboard_auth"]
    assert ok.status == 200
    assert morsel.value == "secret"
    assert morsel["httponly"]
    assert morsel["samesite"].lower() == "lax"
    assert morsel["secure"]
    assert "secret" not in (ok.text or "")

    plain = Req("secret")
    plain.scheme = "http"
    insecure = _run(ds.handle_session(plain))
    assert not insecure.cookies["dashboard_auth"]["secure"]

    bad = _run(ds.handle_session(Req("nope")))
    assert bad.status == 401
    assert "dashboard_auth" not in bad.cookies


def test_csp_is_explicit_and_does_not_name_a_cdn():
    async def _passthrough(request):
        return ds.web.Response(text="ok")

    resp = _run(ds.security_middleware(SimpleNamespace(), _passthrough))
    csp = resp.headers["Content-Security-Policy"]
    connect = next(d.strip() for d in csp.split(";") if d.strip().startswith("connect-src"))
    script = next(d.strip() for d in csp.split(";") if d.strip().startswith("script-src"))
    assert connect == "connect-src 'self'"
    assert "https://" not in script
    assert "'unsafe-inline'" not in script
    assert "sha256-" in script


def test_auth_middleware_does_not_gate_the_page():
    # /chart is not under /api/, so the middleware passes it straight through.
    async def _passthrough(request):
        return ds.web.json_response({"ok": True})
    req = SimpleNamespace(path="/chart", headers={}, method="GET")
    # Even with NO token configured, the page must still be reachable.
    import unittest.mock as mock
    with mock.patch.object(ds, "_DASHBOARD_TOKEN", ""):
        resp = _run(ds.auth_middleware(req, _passthrough))
    assert resp.status == 200
