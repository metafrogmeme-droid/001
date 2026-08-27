"""`scripts/verify_deploy.sh` — a hash nobody sent is not a hash that differed.

WHAT WAS WRONG

The web half parses the two content hashes out of `/api/version` with sed:

    live_build="$(printf '%s' "$live" | sed -n 's/.*"build":"\\([^"]*\\)".*/\\1/p')"

`app/lib/version.js` **omits** `build`/`assets` rather than sending null, and
says why in its own comment: "an absent field reads as not available here,
where a null invites being mistaken for a value". The sed then yields "", ""
never equals the expected hash, and the comparison reported

    FAIL  serving DIFFERENT code than this checkout
    build   live=  expected=1c9436fa27f6+216

— a confident verdict about a hash the server never claimed, printed as though
the live build were empty rather than unreported. An HTML error page from a
proxy reaches the same place: both fields parse to empty, same false FAIL.

WHY THAT DIRECTION IS THE EXPENSIVE ONE

This script's own header explains it: "reporting an unreachable endpoint as a
failed deploy sends an operator to roll back a deploy that landed perfectly."
It already separates verdicts from non-verdicts, and `unk()` exists for exactly
this — it simply was not reached on the parse path.

These drive the real script against a real socket. The three-outcome contract
(0 verdict / 1 verdict / 3 NOT a verdict) is the thing being pinned.
"""
from __future__ import annotations

import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "verify_deploy.sh"


def _expected():
    """The hashes this checkout computes — what the script compares against."""
    out = subprocess.run(
        ["node", "-e",
         'const v=require("./app/lib/version").buildInfo();'
         'console.log(v.build+" "+v.assets)'],
        capture_output=True, text=True, cwd=REPO)
    if out.returncode != 0 or not out.stdout.strip():
        pytest.skip("node/app unavailable — the script itself reports UNKNOWN here")
    return out.stdout.strip().split(" ")


class _Handler(BaseHTTPRequestHandler):
    payload = b"{}"
    content_type = "application/json"

    def do_GET(self):
        if self.path != "/api/version":
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", self.content_type)
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        self.wfile.write(self.payload)

    def log_message(self, *a):
        pass


@pytest.fixture
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd
    httpd.shutdown()


def _serve(httpd, body, content_type="application/json"):
    # COMPACT separators, because that is what the server actually sends.
    # Express's res.json() uses JSON.stringify with no `json spaces` set, so
    # the wire format is {"build":"..."} — and the script's sed matches that
    # exact shape. json.dumps' default {"build": "..."} is a payload no real
    # server here produces, and testing against it would have "proved" a bug
    # that does not exist while hiding the one that does.
    _Handler.payload = (body if isinstance(body, bytes)
                        else json.dumps(body, separators=(",", ":")).encode())
    _Handler.content_type = content_type
    return f"http://127.0.0.1:{httpd.server_address[1]}"


def _run(url):
    """--web-only: the bot box is a different target with its own probes."""
    return subprocess.run(
        ["bash", str(SCRIPT), "--web-only"],
        capture_output=True, text=True, cwd="/",
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "WEB_URL": url, "HOME": "/root"})


# ── the defect: absent must be UNKNOWN (3), never FAIL (1) ─────────────

def test_an_omitted_build_field_is_unknown_not_a_mismatch(server):
    want_build, want_assets = _expected()
    url = _serve(server, {"sha": "c4d3baa", "assets": want_assets})   # no "build"
    r = _run(url)
    assert r.returncode == 3, (r.returncode, r.stdout, r.stderr)
    assert "did not send this field" in r.stdout
    assert "serving DIFFERENT code" not in r.stdout


def test_an_omitted_assets_field_is_unknown_not_a_mismatch(server):
    want_build, _ = _expected()
    url = _serve(server, {"sha": "c4d3baa", "build": want_build})     # no "assets"
    r = _run(url)
    assert r.returncode == 3
    assert "nothing was compared" in r.stdout


def test_an_html_error_page_is_unknown_not_a_mismatch(server):
    """A proxy 502 parses to empty for both fields. That is not a deploy."""
    url = _serve(server, b"<html><body>502 Bad Gateway</body></html>", "text/html")
    r = _run(url)
    assert r.returncode == 3, (r.returncode, r.stdout, r.stderr)
    assert "serving DIFFERENT code" not in r.stdout


def test_incomplete_is_reported_as_not_a_clean_bill_of_health(server):
    url = _serve(server, {"sha": "c4d3baa"})    # neither hash
    r = _run(url)
    assert r.returncode == 3
    assert "INCOMPLETE" in r.stdout
    assert "NOT a clean bill of" in r.stdout


# ── the verdicts still work ────────────────────────────────────────────

def test_matching_hashes_verify(server):
    want_build, want_assets = _expected()
    url = _serve(server, {"sha": "c4d3baa", "build": want_build, "assets": want_assets})
    r = _run(url)
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    assert "DEPLOY VERIFIED" in r.stdout


def test_a_genuinely_different_build_is_still_a_fail(server):
    """The fix must not soften a REAL mismatch into 'could not tell'."""
    _, want_assets = _expected()
    url = _serve(server, {"sha": "old", "build": "770ad5c415bd+211",
                          "assets": want_assets})
    r = _run(url)
    assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
    assert "serving DIFFERENT code" in r.stdout
    # and it still names the shape that cost a day of broken sign-in
    assert "SIGN-IN SHAPE" in r.stdout


def test_both_hashes_stale_says_the_container_never_took_the_deploy(server):
    url = _serve(server, {"sha": "old", "build": "770ad5c415bd+211",
                          "assets": "f0275bef2cd0+92"})
    r = _run(url)
    assert r.returncode == 1
    assert "has not taken the deploy at all" in r.stdout


def test_an_unreachable_site_is_unknown_not_failed():
    r = subprocess.run(
        ["bash", str(SCRIPT), "--web-only"],
        capture_output=True, text=True, cwd="/",
        env={"PATH": "/usr/local/bin:/usr/bin:/bin",
             "WEB_URL": "http://127.0.0.1:9", "HOME": "/root"})
    assert r.returncode == 3
    assert "could not read" in r.stdout


def test_an_unknown_argument_checks_nothing_and_says_so():
    r = subprocess.run(["bash", str(SCRIPT), "--nonsense"],
                       capture_output=True, text=True, cwd="/")
    assert r.returncode == 2
    assert "Nothing was checked" in r.stderr
