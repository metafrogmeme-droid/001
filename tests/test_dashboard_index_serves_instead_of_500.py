"""The dashboard index route must serve the page, not a 500.

`handle_index` passed `content_type="text/html"` to `web.FileResponse`,
which does not accept that kwarg (it takes `headers=`) — every hit raised
`TypeError: FileResponse.__init__() got an unexpected keyword argument
'content_type'` and aiohttp turned it into a 500. The sibling route
`handle_performance_chart` had it right all along: FileResponse infers
text/html from the .html suffix. This test calls the handler the way the
route does, so the TypeError can never come back quietly.
"""

import asyncio
from types import SimpleNamespace

from bot.web import dashboard_server as ds


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_index_route_registered_and_not_api_prefixed():
    import inspect
    src = inspect.getsource(ds.create_app)
    assert 'add_get("/"' in src


def test_handler_serves_the_html_page():
    req = SimpleNamespace(app={"engine": SimpleNamespace()})
    resp = _run(ds.handle_index(req))
    assert resp.status == 200
    # Content-Type is set by FileResponse at prepare() time from the file
    # suffix, so an unprepared response object still reports the default.
    # Assert the guess aiohttp itself will make — and that it is the
    # dashboard file, not some other page in the directory.
    import mimetypes
    assert resp._path.name == "dashboard.html"
    assert mimetypes.guess_type(str(resp._path))[0] == "text/html"
