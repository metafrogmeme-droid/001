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


def _local_nginx_version():
    """(major, minor, patch) of the nginx on PATH, or None. `nginx -v` -> stderr."""
    if shutil.which("nginx") is None:
        return None
    r = subprocess.run(["nginx", "-v"], capture_output=True, text=True, timeout=30)
    m = re.search(r"nginx/(\d+)\.(\d+)\.(\d+)", (r.stderr or "") + (r.stdout or ""))
    return tuple(int(g) for g in m.groups()) if m else None


def test_the_pinned_nginx_image_supports_the_directives_used():
    """The config targets the version docker-compose pins, not the one on PATH.

    `http2 on;` is nginx >= 1.25.1. Compose pins nginx:1.27-alpine, so it is
    the correct modern spelling there — but a downgrade of that pin would make
    this config fail to parse in production, and nothing else would say so.
    """
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    m = re.search(r"image:\s*nginx:(\d+)\.(\d+)(?:\.(\d+))?", compose)
    assert m, "docker-compose no longer pins an nginx image version"
    pinned = (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))
    if "http2 on;" in _strip_comments(_conf()):
        assert pinned >= (1, 25, 1), (
            f"nginx.conf uses `http2 on;` (nginx >= 1.25.1) but docker-compose "
            f"pins nginx:{'.'.join(str(p) for p in pinned)}. The deployed "
            f"container would fail to start with 'unknown directive \"http2\"'.")


@pytest.mark.skipif(shutil.which("nginx") is None,
                    reason="nginx not installed — structural checks still ran")
def test_nginx_accepts_the_config(tmp_path):
    """Parse it with the real thing where we can. A config that satisfies every
    assertion above and does not parse is still a stack that will not start.

    THE VERSION SKEW THIS HAS TO BRIDGE, AND WHY IT IS NOT A FUDGE

    The config targets nginx:1.27-alpine, which is what docker-compose runs.
    This test invokes whatever `nginx` is on PATH, which on ubuntu-latest
    runners (and most dev boxes) is 1.24 — and 1.24 does not know the
    `http2 on;` directive introduced in 1.25.1. So the first version of this
    test failed CI on a config that is correct for the nginx that will actually
    serve it.

    Downgrading the config to the legacy `listen 443 ssl http2;` to satisfy an
    nginx nobody deploys would be the wrong way round. Instead the ONE
    version-specific pair is rewritten to its legacy equivalent when the local
    nginx is too old. Everything this test exists to check — the per-location
    includes, the map, the limit_req zones, the location structure — is
    version-independent and is still parsed for real. The pin itself is
    asserted separately, in test_the_pinned_nginx_image_supports_the_directives_used.
    """
    conf_d = tmp_path / "conf.d"
    conf_d.mkdir()
    rendered = _conf().replace("${RUNECLAW_DOMAIN}", "example.test")

    version = _local_nginx_version()
    if version is not None and version < (1, 25, 1):
        legacy = re.sub(r"listen\s+443\s+ssl;\s*\n\s*http2\s+on;",
                        "listen 443 ssl http2;", rendered)
        assert legacy != rendered, (
            f"local nginx {version} predates `http2 on;` and the legacy "
            f"substitution matched nothing — the listen block changed shape, so "
            f"this test is about to validate something other than what it thinks")
        rendered = legacy

    # nginx resolves upstream hostnames at CONFIG-TEST time, and `api_bridge`
    # and `runeclaw-bot` are docker-compose service names that exist only on
    # the compose network. Point them at loopback so the parse can proceed:
    # like the http2 substitution above this is environmental, not structural,
    # and adding a `resolver` to defer lookup would change how the real
    # deployment behaves purely to satisfy a test.
    rendered = rendered.replace("http://api_bridge:8000", "http://127.0.0.1:8000")
    rendered = rendered.replace("http://runeclaw-bot:8080", "http://127.0.0.1:8080")

    # `nginx -t` BINDS the listen sockets, and 80/443 are privileged. CI runs
    # unprivileged, so the real ports fail with "bind() to 0.0.0.0:80 failed
    # (13: Permission denied)". Move them above 1024; the port number is not
    # something this test is about.
    ports = re.subn(r"listen\s+80;", "listen 18080;", rendered)
    rendered, n80 = ports
    rendered, n443 = re.subn(r"listen\s+443\s+ssl", "listen 18443 ssl", rendered)
    assert n80 and n443, (
        f"expected to rewrite both listen ports for an unprivileged nginx -t, "
        f"rewrote {n80} x 80 and {n443} x 443 — the listen lines changed shape "
        f"and this test is about to bind something it did not mean to")

    # A throwaway self-signed pair. Empty files are not enough: nginx -t
    # actually LOADS the certificate and fails with "PEM routines::no start
    # line", so a placeholder would leave this test red for a reason that has
    # nothing to do with the config.
    certs = tmp_path / "certs"
    certs.mkdir()
    gen = subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", str(certs / "privkey.pem"),
         "-out", str(certs / "fullchain.pem"),
         "-days", "1", "-subj", "/CN=example.test"],
        capture_output=True, text=True, timeout=120)
    if gen.returncode != 0:
        pytest.skip(f"could not generate a test certificate: {gen.stderr[:200]}")
    rendered = rendered.replace(
        "/etc/letsencrypt/live/example.test", str(certs))
    rendered = rendered.replace(
        "/etc/nginx/snippets/security-headers.conf", str(SNIPPET))
    (conf_d / "default.conf").write_text(rendered, encoding="utf-8")

    # Every runtime path redirected into tmp_path. `nginx -t` does not just
    # parse — it OPENS the pid file, the logs and the scratch directories, so
    # under a non-root user the defaults fail with
    #
    #   [emerg] open() "/run/nginx.pid" failed (13: Permission denied)
    #
    # *after* reporting "syntax is ok". CI runs unprivileged; the first version
    # of this was written and verified as root, so it passed here and failed
    # there on a config nginx had already accepted.
    main = tmp_path / "nginx.conf"
    main.write_text(
        f"pid {tmp_path}/nginx.pid;\n"
        f"error_log {tmp_path}/error.log;\n"
        f"events {{}}\n"
        f"http {{\n"
        f"  access_log {tmp_path}/access.log;\n"
        f"  client_body_temp_path {tmp_path}/client_body;\n"
        f"  proxy_temp_path {tmp_path}/proxy;\n"
        f"  fastcgi_temp_path {tmp_path}/fastcgi;\n"
        f"  uwsgi_temp_path {tmp_path}/uwsgi;\n"
        f"  scgi_temp_path {tmp_path}/scgi;\n"
        f"  include {conf_d}/*.conf;\n"
        f"}}\n", encoding="utf-8")
    r = subprocess.run(["nginx", "-t", "-c", str(main), "-p", str(tmp_path)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"nginx rejected the config:\n{r.stderr}"
    # "syntax is ok" alone is not success — it is printed before the paths are
    # opened, and the run above failed in exactly that gap.
    assert "test is successful" in r.stderr, (
        f"nginx did not confirm the config: {r.stderr}")
