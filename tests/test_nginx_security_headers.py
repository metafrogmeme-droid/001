"""nginx drops every server-level `add_header` in a location that sets its own.

THE DEFECT THIS ENCODES

nginx inherits `add_header` into a `location` only if that location declares NO
`add_header` of its own. nginx.conf declared five security headers at server
level — HSTS, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, CSP —
and then every single location set one of its own:

    location /                       -> add_header Cache-Control "no-cache"
    location ~* \\.(js|css|png|...)$  -> add_header Cache-Control "public, ..."
    location /api/                   -> three CORS add_headers

So the security block applied to NOTHING the server actually served. Every HTML
page, every asset and every API response went out with no CSP, no HSTS and no
clickjacking protection, from a config file that read as though it had all
three. That is the worst kind of security control: one that is documented,
reviewed, and inert.

There is no inheritance escape in nginx — the `include` has to be repeated in
every location. Which means the failure mode is DRIFT: someone adds a location,
sets a Cache-Control on it, and silently opts that route out. This test is the
thing that notices.

WHY SOURCE-SCANNED

The property is "every location that sets add_header also includes the
snippet" — a structural fact about the config, not a behaviour any unit test
can drive without standing up nginx and issuing real requests. Where nginx IS
available, test_nginx_accepts_the_config below actually parses it.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CONF = ROOT / "nginx.conf"
SNIPPET = ROOT / "nginx" / "snippets" / "security-headers.conf"
SNIPPET_INCLUDE = "include /etc/nginx/snippets/security-headers.conf;"

# Every header the snippet is responsible for. Adding one here without adding
# it to the snippet fails, which is the point: the list and the file agree.
REQUIRED_HEADERS = [
    "Strict-Transport-Security",
    "X-Frame-Options",
    "X-Content-Type-Options",
    "Referrer-Policy",
    "Content-Security-Policy",
]


def _conf() -> str:
    return CONF.read_text(encoding="utf-8")


def _strip_comments(text: str) -> str:
    """nginx comments run to end of line. Strip them before scanning.

    Same trap CLAUDE.md names for Python source scans: this file's own header
    quotes `add_header Cache-Control`, and a raw scan cannot tell a warning
    about a directive from the directive.
    """
    return "\n".join(re.sub(r"#.*$", "", ln) for ln in text.splitlines())


def _locations(text: str):
    """Yield (header_line, body) for each `location ... { ... }` block.

    Brace-counted rather than regex-matched: `if ($request_method = OPTIONS)`
    nests inside /api/, and a non-greedy regex would end the block at the inner
    closing brace and miss everything after it — including, in the original
    file, the CORS headers that cause the whole problem.
    """
    for m in re.finditer(r"^\s*(location\s[^{]*)\{", text, re.M):
        depth, i = 1, m.end()
        while i < len(text) and depth:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        yield m.group(1).strip(), text[m.end():i - 1]


def test_the_snippet_defines_every_required_header():
    assert SNIPPET.exists(), f"{SNIPPET} is missing — the include targets nothing"
    body = _strip_comments(SNIPPET.read_text(encoding="utf-8"))
    for h in REQUIRED_HEADERS:
        assert re.search(rf"add_header\s+{re.escape(h)}\b", body), (
            f"{h} is not in {SNIPPET.name}. Every location includes this file "
            f"and nothing else supplies these headers.")


def test_every_location_that_sets_a_header_also_includes_the_snippet():
    """The drift guard. This is the whole file."""
    text = _strip_comments(_conf())
    offenders = []
    for header, body in _locations(text):
        if "add_header" not in body:
            continue          # sets none of its own, so it inherits correctly
        if SNIPPET_INCLUDE not in body:
            offenders.append(header)
    assert not offenders, (
        "these locations set their own add_header, which makes nginx drop "
        "EVERY server-level security header for them, and they do not include "
        "the snippet back:\n  " + "\n  ".join(offenders)
        + f"\n\nAdd `{SNIPPET_INCLUDE}` to each. nginx has no inheritance "
          f"escape; the repetition is required.")


def test_the_security_headers_are_not_only_declared_at_server_level():
    """The original shape, pinned directly.

    A future edit that 'tidies up the duplication' by pulling the include back
    to one server-level line reintroduces the exact bug, and every test above
    would still pass if the locations had also stopped setting Cache-Control.
    """
    text = _strip_comments(_conf())
    location_includes = sum(
        1 for _h, body in _locations(text) if SNIPPET_INCLUDE in body)
    assert location_includes >= 3, (
        f"only {location_includes} location(s) include the security-headers "
        f"snippet. A server-level include alone is inherited by no location "
        f"that sets an add_header of its own — which is all of them.")


def test_no_unsubstituted_placeholder_remains():
    """`YOUR_DOMAIN` in a certificate path is a stack that cannot start.

    docker-compose mounted this file directly as conf.d/default.conf with the
    literal placeholder still in ssl_certificate, so nginx failed on a missing
    file and the error pointed at certbot rather than at the config.
    """
    # Comments stripped first — the file's own header explains why the literal
    # placeholder was a bug, which means it NAMES the placeholder. Scanning raw
    # text failed on the fixed file for quoting the thing it forbids. Fourth
    # time this repo has hit that; CLAUDE.md keeps a note about it.
    text = _strip_comments(_conf())
    assert "YOUR_DOMAIN" not in text, (
        "nginx.conf still contains the literal YOUR_DOMAIN. It is an envsubst "
        "template now — use ${RUNECLAW_DOMAIN}, which docker-compose requires "
        "in .env and the nginx entrypoint substitutes at start.")
    assert "${RUNECLAW_DOMAIN}" in text, (
        "nginx.conf no longer references ${RUNECLAW_DOMAIN}; if the domain was "
        "hardcoded, this file stopped being deployable by anyone else")


def test_connection_upgrade_is_mapped_not_hardcoded():
    text = _strip_comments(_conf())
    assert re.search(r"map\s+\$http_upgrade\s+\$connection_upgrade", text), (
        "the $connection_upgrade map is gone")
    assert not re.search(r'proxy_set_header\s+Connection\s+"upgrade"', text), (
        'Connection is hardcoded to "upgrade" again, so every ordinary request '
        'announces an upgrade it is not making. Use $connection_upgrade.')


def test_json_is_not_gzipped_over_tls():
    """BREACH: compressing an authenticated response alongside attacker-chosen
    input leaks the secret a byte at a time. /api/ responses are JSON."""
    text = _strip_comments(_conf())
    m = re.search(r"gzip_types([^;]*);", text)
    assert m, "gzip_types is gone — check gzip is still configured deliberately"
    assert "application/json" not in m.group(1), (
        "application/json is back in gzip_types. These responses carry "
        "authenticated data over TLS; that is the BREACH precondition.")


def test_the_reverse_proxy_actually_rate_limits():
    """api_bridge.py:105-110 says this belongs here. It now is here."""
    text = _strip_comments(_conf())
    assert "limit_req_zone" in text, "no limit_req_zone declared"
    api = [b for h, b in _locations(text) if h.startswith("location /api/")]
    assert api, "no /api/ location found"
    assert "limit_req" in api[0], (
        "/api/ has no limit_req. The app's own limiter is in-process and "
        "per-worker, and its comment says the budget belongs at the proxy.")


@pytest.mark.skipif(shutil.which("nginx") is None,
                    reason="nginx not installed — structural checks still ran")
def test_nginx_accepts_the_config(tmp_path):
    """Parse it with the real thing where we can. A config that satisfies every
    assertion above and does not parse is still a stack that will not start."""
    conf_d = tmp_path / "conf.d"
    conf_d.mkdir()
    rendered = _conf().replace("${RUNECLAW_DOMAIN}", "example.test")
    # Certificate files nginx must be able to stat; contents are never read at
    # config-test time.
    certs = tmp_path / "certs"
    certs.mkdir()
    for name in ("fullchain.pem", "privkey.pem"):
        (certs / name).write_text("", encoding="utf-8")
    rendered = rendered.replace(
        "/etc/letsencrypt/live/example.test", str(certs))
    rendered = rendered.replace(
        "/etc/nginx/snippets/security-headers.conf", str(SNIPPET))
    (conf_d / "default.conf").write_text(rendered, encoding="utf-8")

    main = tmp_path / "nginx.conf"
    main.write_text(
        f"events {{}}\nhttp {{\n  include {conf_d}/*.conf;\n}}\n", encoding="utf-8")
    r = subprocess.run(["nginx", "-t", "-c", str(main), "-p", str(tmp_path)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"nginx rejected the config:\n{r.stderr}"
