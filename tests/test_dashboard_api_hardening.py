"""dashboard_api.py served files outside its roots and reported zeros it never read.

Driven end-to-end against a real ThreadingHTTPServer rather than source-scanned,
because both defects looked correct in the source:

  * `filepath.startswith(realpath(base_dir))` reads as a containment check and
    is a string prefix test — `<root>-backup/` passes it;
  * `do_HEAD` recomputed the routing with a guard that picks the base to match
    the path, so it asked "is this under EITHER root" and allowed a traversal
    that `do_GET` refuses;
  * `load_json(path, fallback)` returned the fallback on ANY exception, so
    /api/health answered `{"status":"ok","traders":0}` off a corrupt file —
    the repo's own cardinal rule (unreadable is never zero, absent is never a
    measurement) broken on the endpoint whose job is to report whether things
    work.
"""
from __future__ import annotations

import http.client
import importlib
import json
import os
import threading
from http.server import ThreadingHTTPServer

import pytest


@pytest.fixture()
def server(tmp_path, monkeypatch):
    """A live dashboard_api bound to a scratch tree.

    dashboard_api resolves its roots at import time, so the module is reloaded
    with the environment and paths pointed at tmp_path.
    """
    website = tmp_path / "website"
    website.mkdir()
    (website / "index.html").write_text("<h1>landing</h1>", encoding="utf-8")
    dash = tmp_path / "dashboard_static"
    dash.mkdir()
    (dash / "index.html").write_text("<h1>dash</h1>", encoding="utf-8")
    data = tmp_path / "data"
    data.mkdir()

    # The sibling whose name has the root as a PREFIX. This is the whole
    # traversal: it is not inside website/, and `startswith` says it is.
    sneaky = tmp_path / "website_backup"
    sneaky.mkdir()
    (sneaky / "secret.txt").write_text("SECRET", encoding="utf-8")

    monkeypatch.setenv("DASHBOARD_API_KEY", "test-key")
    monkeypatch.setenv("DASHBOARD_CORS_ORIGIN", "")
    monkeypatch.setenv("DASHBOARD_EXTRA_ORIGINS", "")

    mod = importlib.import_module("dashboard_api")
    mod = importlib.reload(mod)
    mod.WEBSITE_DIR = str(website)
    mod.DASHBOARD_DIR = str(dash)
    mod.DATA_FILE = str(data / "dashboard_snapshot.json")
    mod.FEED_FILE = str(data / "dashboard_feed.json")
    mod.API_KEY = "test-key"

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), mod.Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield httpd.server_address, mod, tmp_path
    finally:
        httpd.shutdown()
        httpd.server_close()


def _req(addr, method, path):
    conn = http.client.HTTPConnection(*addr, timeout=10)
    try:
        conn.request(method, path)
        r = conn.getresponse()
        return r.status, r.read(), dict(r.getheaders())
    finally:
        conn.close()


def _post(addr, path, payload, key="test-key"):
    conn = http.client.HTTPConnection(*addr, timeout=10)
    try:
        raw = json.dumps(payload).encode()
        conn.request(path and "POST", path, body=raw,
                     headers={"X-API-Key": key, "Content-Length": str(len(raw))})
        r = conn.getresponse()
        return r.status, json.loads(r.read() or b"{}")
    finally:
        conn.close()


# ── path containment ───────────────────────────────────────────────────────

@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_a_prefix_sibling_directory_is_not_inside_the_root(server, method):
    """The demonstrated exploit. `startswith` served this file."""
    addr, _mod, _tmp = server
    status, body, _h = _req(addr, method, "/../website_backup/secret.txt")
    assert status in (403, 404), (
        f"{method} served a file from website_backup/ (status {status}, "
        f"body {body[:60]!r}). It is a SIBLING of the root, not inside it — "
        f"`startswith` accepts it because the root is a string prefix of it.")
    assert b"SECRET" not in body


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_dot_dot_cannot_escape_the_dashboard_root(server, method):
    addr, _mod, _tmp = server
    status, body, _h = _req(addr, method, "/dashboard/../website/index.html")
    assert status in (403, 404), (
        f"{method} allowed a traversal out of dashboard_static/ into website/ "
        f"(status {status}). do_HEAD used to permit exactly this while do_GET "
        f"refused it, because it chose its base directory to match the path.")


def test_head_and_get_agree_on_every_route(server):
    """The two used to be separate routing implementations, and had diverged.

    Any route where HEAD and GET disagree is a route where one of them is
    enforcing something the other is not.
    """
    addr, _mod, _tmp = server
    for path in ("/", "/index.html", "/dashboard", "/dashboard/index.html",
                 "/dashboard/../website/index.html",
                 "/../website_backup/secret.txt",
                 "/nope.html", "/api/health", "/api/snapshot"):
        g, _gb, _gh = _req(addr, "GET", path)
        h, hb, _hh = _req(addr, "HEAD", path)
        assert g == h, (
            f"GET {path} -> {g} but HEAD {path} -> {h}; the two routing paths "
            f"disagree, which is how the traversal got in")
        assert hb == b"", f"HEAD {path} returned a body ({hb[:40]!r})"


def test_the_legitimate_files_are_still_served(server):
    """A containment fix that refuses everything is not a fix."""
    addr, _mod, _tmp = server
    for path, needle in (("/", b"landing"), ("/index.html", b"landing"),
                         ("/dashboard", b"dash"), ("/dashboard/index.html", b"dash")):
        status, body, _h = _req(addr, "GET", path)
        assert status == 200, f"GET {path} -> {status}, expected 200"
        assert needle in body


# ── unreadable is never zero ───────────────────────────────────────────────

def test_health_does_not_report_ok_for_an_unreadable_snapshot(server):
    addr, mod, _tmp = server
    with open(mod.DATA_FILE, "w") as f:
        f.write("{ this is not json")
    status, body, _h = _req(addr, "GET", "/api/health")
    payload = json.loads(body)
    assert status == 503, f"unreadable snapshot answered {status}"
    assert payload.get("status") != "ok", (
        f"/api/health said {payload!r} for a corrupt snapshot file. That is a "
        f"confident all-clear assembled from a failed read.")
    assert payload.get("traders") != 0, "an unreadable trader count is not zero"


def test_snapshot_does_not_report_an_empty_book_for_an_unreadable_file(server):
    addr, mod, _tmp = server
    with open(mod.DATA_FILE, "w") as f:
        f.write("}{")
    status, body, _h = _req(addr, "GET", "/api/snapshot")
    assert status == 503, (
        f"/api/snapshot answered {status} with {body[:80]!r} for a corrupt "
        f"file. `{{'traders': [], 'total_traders': 0}}` reads as a measured "
        f"'no traders', which is a different claim from 'could not read'.")


def test_absent_and_unreadable_are_told_apart(server):
    """Three outcomes. A snapshot that has not arrived yet is not a failure."""
    addr, mod, _tmp = server
    if os.path.exists(mod.DATA_FILE):
        os.unlink(mod.DATA_FILE)
    status, body, _h = _req(addr, "GET", "/api/health")
    assert status == 200 and json.loads(body)["status"] == "starting", (
        f"a not-yet-written snapshot reported as {body[:80]!r}; absent is a "
        f"real state, distinct from both ok and unreadable")


def test_a_genuine_zero_is_still_reported_as_zero(server):
    """0 is a real, measured value. The fix must not hide it."""
    addr, mod, _tmp = server
    with open(mod.DATA_FILE, "w") as f:
        json.dump({"traders": [], "total_traders": 0, "received_at": "now"}, f)
    status, body, _h = _req(addr, "GET", "/api/health")
    payload = json.loads(body)
    assert status == 200 and payload["status"] == "ok"
    assert payload["traders"] == 0, (
        "a real, measured zero must survive — testing falsiness instead of "
        "`is None` is the other half of the same rule")


# ── writes ─────────────────────────────────────────────────────────────────

def test_a_failed_write_is_not_reported_as_ok(server, monkeypatch):
    addr, mod, _tmp = server
    monkeypatch.setattr(mod, "save_json", lambda *a, **k: False)
    status, payload = _post(addr, "/api/snapshot", {"traders": [], "total_traders": 0})
    assert status == 500 and "error" in payload, (
        f"a failed snapshot write answered {status} {payload!r}; the pusher "
        f"records that as a delivered snapshot")


def test_the_snapshot_write_is_atomic(server):
    """A crash mid-write must not leave a truncated file the next read chokes on."""
    addr, mod, tmp = server
    status, payload = _post(addr, "/api/snapshot",
                            {"traders": [{"id": 1}], "total_traders": 1})
    assert status == 200 and payload["ok"] is True
    with open(mod.DATA_FILE) as f:
        assert json.load(f)["total_traders"] == 1
    leftovers = [p for p in os.listdir(os.path.dirname(mod.DATA_FILE))
                 if ".tmp." in p]
    assert not leftovers, f"temporary files left behind: {leftovers}"


def test_a_malformed_content_length_is_a_400_not_a_traceback(server):
    addr, _mod, _tmp = server
    conn = http.client.HTTPConnection(*addr, timeout=10)
    try:
        conn.putrequest("POST", "/api/snapshot", skip_accept_encoding=True)
        conn.putheader("X-API-Key", "test-key")
        conn.putheader("Content-Length", "not-a-number")
        conn.endheaders()
        r = conn.getresponse()
        assert r.status == 400, (
            f"a malformed Content-Length answered {r.status}; int() used to "
            f"raise straight out of the handler")
    finally:
        conn.close()


def test_the_feed_survives_concurrent_writers(server):
    """Read-modify-write under ThreadingHTTPServer loses entries without a lock."""
    addr, mod, _tmp = server
    errors = []

    def push(i):
        try:
            s, _ = _post(addr, "/api/snapshot", {"traders": [], "total_traders": i})
            if s != 200:
                errors.append(s)
        except Exception as exc:      # pragma: no cover - surfaced by the assert
            errors.append(repr(exc))

    threads = [threading.Thread(target=push, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, f"concurrent pushes failed: {errors}"
    with open(mod.FEED_FILE) as f:
        feed = json.load(f)
    assert len(feed) == 12, (
        f"{len(feed)} of 12 feed entries survived — concurrent read-modify-write "
        f"without a lock is how the others were erased")


# ── configuration ──────────────────────────────────────────────────────────

def test_no_third_party_origin_is_compiled_into_the_cors_allow_list():
    """An allow-list is a security decision and belongs in the deployment's
    environment, not in the source of every deployment."""
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "dashboard_api.py"), encoding="utf-8").read()
    from tests.test_preflight_matches_ci import code_only
    code = code_only(src)
    assert "mule.page" not in code, (
        "a specific third-party origin is hardcoded into the CORS allow-list "
        "again; use DASHBOARD_EXTRA_ORIGINS")
    assert "DASHBOARD_EXTRA_ORIGINS" in code
