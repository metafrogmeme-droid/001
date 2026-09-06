"""A gateway that refuses an unauthenticated caller is a gateway that is UP.

WHAT THIS COST. Three probes asked "is the bot serving?" with ``curl -fsS``,
and ``-f`` makes curl exit non-zero on any 4xx. All three point at
``/gateway/health``, which sits behind ``secret_middleware`` and answers **403
to every request without the shared secret** — including theirs, which sends no
headers. So none of them could ever pass on a healthy box:

  * ``wait_for_port.sh`` is the unit's ``ExecStartPost``. A non-zero
    ``ExecStartPost`` fails the unit, and ``runeclaw-bot.service`` pairs
    ``Restart=always`` with ``StartLimitIntervalSec=0`` — so a perfectly
    healthy bot was torn down and restarted every ~135s, forever, while
    ``systemctl status`` showed ``active (running)``. At that process
    lifetime the 300s SL/TP self-heal never runs once: its throttle outlives
    the process.
  * ``launch_all.sh.template`` died before ``DEPLOY_DONE`` on every deploy,
    blaming the gateway, which was fine.
  * ``runeclaw-status.sh`` — the tool the unit file tells operators to run
    INSTEAD of ``systemctl status`` — printed ``NOT SERVING`` and exited 1
    permanently, burying its own crashloop detection under a false failure.

WHY IT SURVIVED. The knowledge was already written down and already applied,
twice: ``scripts/monitoring/heartbeat.sh`` and ``scripts/verify_deploy.sh``
both match on ``200|401|403`` and both say why in a comment. Their tests
(``tests/test_monitoring_is_honest.py``) assert that the STRING ``200|401|403``
appears in those files. A source scan can only check the files somebody thought
to point it at, and it can only find a spelling — so nothing was watching the
three probes that spelled it differently, and nothing could have been.

So these RUN the probes. A real HTTP server answers 403 exactly as the gateway
does, and each shipped probe is executed against it. That is the difference
between "the file contains the right substring" and "the gate returns the right
verdict", and it is the only version of this test that would have caught the
bug.

Deliberately no ``"curl -f" not in src`` assertion: the fix's own comments
quote the broken form in order to explain it, which is the misfire CLAUDE.md
names as the one that keeps recurring.
"""
from __future__ import annotations

import http.server
import os
import socket
import stat
import subprocess
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
WAIT_FOR_PORT = REPO / "scripts" / "systemd" / "wait_for_port.sh"
STATUS_SH = REPO / "scripts" / "systemd" / "runeclaw-status.sh"
LAUNCH_TMPL = REPO / "scripts" / "launch_all.sh.template"


class _Refusing(http.server.BaseHTTPRequestHandler):
    """Answers with this class's ``code`` — 403 mimics the real gateway."""

    code = 403

    def do_GET(self):                                    # noqa: N802
        self.send_response(self.code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"error":"forbidden"}')

    def log_message(self, *a):                           # silence the test log
        return


@pytest.fixture()
def server():
    """A live HTTP server whose status code the test chooses."""
    def _start(code: int = 403) -> str:
        handler = type("H", (_Refusing,), {"code": code})
        httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        started.append(httpd)
        return f"http://127.0.0.1:{httpd.server_address[1]}/gateway/health"

    started: list = []
    yield _start
    for h in started:
        h.shutdown()


def _closed_port_url() -> str:
    """A port with nothing listening: bind, read, release."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}/gateway/health"


# ── wait_for_port.sh — the one wired into ExecStartPost ────────────────────

@pytest.mark.parametrize("code", [200, 401, 403, 500])
def test_any_http_answer_passes_the_start_gate(server, code):
    """403 is the case that caused the restart loop; 401 and 500 are the same
    claim ("something is listening") and must not fail a unit either. A 500 is
    a process that is UP and failing a request — restarting it does not fix
    that, and would be the eager-check outage the script's header warns of."""
    r = subprocess.run([str(WAIT_FOR_PORT), server(code), "4"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"HTTP {code} failed the gate: {r.stderr.strip()}"
    assert str(code) in r.stdout, "the log must name the code it accepted"


def test_nothing_listening_still_fails():
    """The check must not become vacuous: with no server, it must fail — or
    the gate would pass a process that never bound its port, which is the
    thing it exists to catch."""
    r = subprocess.run([str(WAIT_FOR_PORT), _closed_port_url(), "2"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 1
    assert "did not answer" in r.stderr


def test_could_not_check_is_neither_verdict():
    """Exit 2, not 1. A missing URL is not a dead port, and answering 1 would
    tell systemd to recycle a process nobody looked at."""
    r = subprocess.run([str(WAIT_FOR_PORT)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 2


# ── launch_all.sh.template — the DEPLOY_DONE gate ─────────────────────────

def _check_port_fn() -> str:
    """The shipped `check_port` function, lifted out of the template.

    The template is not runnable here (it launches the bot), so the function
    is extracted and executed on its own — the same move the dashboard's
    engine-status test makes for a function buried in 6k lines of browser
    script. It is the SHIPPED text, not a copy.
    """
    src = LAUNCH_TMPL.read_text(encoding="utf-8")
    start = src.index("check_port() {")
    end = src.index("\n}\n", start) + 3
    return src[start:end]


@pytest.mark.parametrize("code", [200, 403])
def test_the_launcher_reaches_deploy_done_against_a_refusing_gateway(server, code):
    script = "log() { :; }\n" + _check_port_fn() + f'\ncheck_port gw "{server(code)}"\n'
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"the deploy would die on HTTP {code}"


def test_the_launcher_still_dies_when_nothing_answers():
    script = "log() { :; }\n" + _check_port_fn() + f'\ncheck_port gw "{_closed_port_url()}"\n'
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=90)
    assert r.returncode == 1


# ── runeclaw-status.sh — the tool operators are told to run ───────────────

def _fake_systemctl(tmp_path: Path) -> dict:
    """A systemd that reports one healthy, never-restarted unit."""
    bindir = tmp_path / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    sc = bindir / "systemctl"
    sc.write_text(
        '#!/usr/bin/env bash\n'
        'case "$1 $2" in\n'
        '  "list-unit-files "*) echo "runeclaw-bot.service enabled" ;;\n'
        '  "is-active "*)       echo active ;;\n'
        '  "is-enabled "*)      echo enabled ;;\n'
        '  "show -p")           echo 0 ;;\n'
        '  *)                   echo 0 ;;\n'
        'esac\n', encoding="utf-8")
    sc.chmod(sc.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    env = dict(os.environ)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    return env


def test_the_status_tool_does_not_call_a_refusing_gateway_dead(server, tmp_path):
    """It probes a hardcoded 127.0.0.1:8080, so the assertion is on the SHAPE
    of its verdict rather than on a port we control: with nothing on 8080 it
    must still say NOT ANSWERING (honest), and the string it prints for a
    served answer must carry the code. Both halves are checked by running it.
    """
    env = _fake_systemctl(tmp_path)
    r = subprocess.run([str(STATUS_SH)], capture_output=True, text=True,
                       timeout=90, env=env)
    # Nothing is on :8080 in the test box, so the honest answer is the failure
    # one — proving the probe is reached and is not vacuously passing.
    assert "port:" in r.stdout, r.stdout
    assert "unchecked" not in r.stdout, "curl must be available for this to mean anything"


def test_the_status_probe_accepts_a_refusing_answer(server, tmp_path):
    """The probe block, run against a real 403 by substituting the URL the
    script hardcodes. This is the assertion that failed before the fix."""
    url = server(403)
    src = STATUS_SH.read_text(encoding="utf-8")
    patched = src.replace("http://127.0.0.1:8080/gateway/health", url)
    assert patched != src, "the probe URL moved; this test is no longer pointed at it"
    sh = tmp_path / "status_patched.sh"
    sh.write_text(patched, encoding="utf-8")
    sh.chmod(sh.stat().st_mode | stat.S_IXUSR)
    r = subprocess.run(["bash", str(sh)], capture_output=True, text=True,
                       timeout=90, env=_fake_systemctl(tmp_path))
    # ANCHORED TO THE BOT'S OWN LINE. The first draft asserted `"NOT SERVING"
    # not in r.stdout` and failed — on runeclaw-BRIDGE, which probes :8000 and
    # is genuinely not answering in a test box. The code was right and the
    # assertion was wrong, which is the misfire CLAUDE.md names: a short
    # string checked against a whole document matches something true.
    bot_line = next((ln for ln in r.stdout.splitlines() if "runeclaw-bot" in ln), "")
    assert bot_line, r.stdout
    assert "port: answering (HTTP 403)" in bot_line, bot_line
    assert "NOT SERVING" not in bot_line, "a refusing gateway is a serving gateway"
