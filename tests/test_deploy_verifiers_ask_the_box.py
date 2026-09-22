"""The deploy verifiers must ask the thing they claim to be checking.

TWO GAPS, BOTH IN GATES WHOSE HEADERS PROMISE THE OPPOSITE.

``verify_deploy.sh`` exists because of 2026-08-20, when a deploy reset to a
mirror 255 commits stale and every check agreed it was fine. Under a comment
saying so, its bot-box check read ``git rev-parse HEAD`` in the LOCAL
checkout — the same tree it was supposed to be comparing against — and printed
``OK`` whenever that directory was a git repo. Nothing was compared. Run from
a laptop it reported the laptop's commit as the bot's.

``verify_deploy_source.sh`` asks "is the checkout the code you think it is?"
and answered purely from commit ids, which uncommitted edits do not move. A
box patched by hand reported ``SOURCE OK`` while running code in no commit
anywhere. ``bot/utils/build_info.py`` already made ``dirty`` tri-state for
exactly this reason and says so in prose; the pre-launch gate had not learned
it.

These RUN both scripts — against a fake bot box serving a build id we choose,
and against a real temporary git repository we dirty on purpose. A source scan
cannot tell "reads a variable named live_build" from "compares it to the
box", which is how the first gap survived being commented.
"""
from __future__ import annotations

import http.server
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
VERIFY_DEPLOY = REPO / "scripts" / "verify_deploy.sh"
VERIFY_SOURCE = REPO / "scripts" / "verify_deploy_source.sh"


# ── verify_deploy.sh: does it ask the box which code it runs? ──────────────

def _fake_box(build: str, gateway: str = "mounted"):
    """A bot box: 403 on /gateway/health (as the real one does), and a
    /health carrying the build id we choose."""
    payload = json.dumps({"status": "ok", "build": build,
                          "gateway": gateway, "timestamp": "t"}).encode()

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):                                # noqa: N802
            if self.path.startswith("/gateway/health"):
                self.send_response(403)
                self.end_headers()
                self.wfile.write(b'{"error":"forbidden"}')
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *a):
            return

    httpd = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def _run_box_check(url: str):
    env = dict(os.environ, GATEWAY_URL=url, BRIDGE_URL=url)
    return subprocess.run([str(VERIFY_DEPLOY), "--box-only"],
                          capture_output=True, text=True, timeout=120, env=env)


def _head_short() -> str:
    return subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True).stdout.strip()


def test_a_box_on_the_wrong_commit_is_a_FAILURE_not_an_OK():
    """The 2026-08-20 case. Before the fix this printed `OK  checkout at
    <local sha>` no matter what the box was running."""
    httpd, url = _fake_box(build="dead0ff")
    try:
        r = _run_box_check(url)
        assert "WRONG CODE" in r.stdout, r.stdout
        assert "dead0ff" in r.stdout
        assert r.returncode == 1, "a stale box must be a verdict, not a warning"
    finally:
        httpd.shutdown()


def test_a_box_on_this_checkout_verifies():
    httpd, url = _fake_box(build=_head_short())
    try:
        r = _run_box_check(url)
        assert "running this checkout" in r.stdout, r.stdout
    finally:
        httpd.shutdown()


def test_a_box_that_sends_no_build_is_UNKNOWN_not_a_mismatch():
    """An older bot omits the field. `"" != <sha>` must not be reported as
    serving different code — the same rule the web half already states."""
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):                                # noqa: N802
            self.send_response(403 if self.path.startswith("/gateway/") else 200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

        def log_message(self, *a):
            return

    httpd = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        r = _run_box_check(f"http://127.0.0.1:{httpd.server_address[1]}")
        assert "sent no 'build'" in r.stdout, r.stdout
        assert "WRONG CODE" not in r.stdout
        assert r.returncode == 3, "could not check is not a failure"
    finally:
        httpd.shutdown()


def test_a_gateway_that_did_not_mount_is_reported():
    """:8080 answering proves the dashboard is up and says nothing about the
    gateway sub-app — the distinction dashboard_server publishes for exactly
    this reason, and which nothing consumed."""
    httpd, url = _fake_box(build=_head_short(), gateway="failed")
    try:
        r = _run_box_check(url)
        assert "did NOT mount" in r.stdout, r.stdout
        assert r.returncode == 1
    finally:
        httpd.shutdown()


# ── verify_deploy_source.sh: is the TREE the code, not just the commit? ────

@pytest.fixture()
def deployed_clone(tmp_path):
    """A real git repo with a real remote, holding a copy of the script."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)

    work = tmp_path / "work"
    work.mkdir()
    g = ["git", "-C", str(work)]
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    subprocess.run(g + ["config", "user.email", "t@t"], check=True)
    subprocess.run(g + ["config", "user.name", "t"], check=True)
    (work / "scripts").mkdir()
    shutil.copy2(VERIFY_SOURCE, work / "scripts" / "verify_deploy_source.sh")
    (work / "bot").mkdir()
    (work / "bot" / "thing.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(g + ["add", "-A"], check=True)
    subprocess.run(g + ["commit", "-qm", "init"], check=True)
    subprocess.run(g + ["push", "-q", str(origin), "main"], check=True)
    return work, f"file://{origin}"


def _run_source_check(work: Path, url: str, *extra):
    return subprocess.run(
        ["bash", str(work / "scripts" / "verify_deploy_source.sh"),
         "--url", url, "--branch", "main", *extra],
        capture_output=True, text=True, timeout=120)


def test_a_clean_matching_checkout_passes(deployed_clone):
    work, url = deployed_clone
    r = _run_source_check(work, url)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "SOURCE OK" in r.stdout


def test_a_hand_patched_box_is_refused_even_on_the_right_commit(deployed_clone):
    """The whole point. HEAD still matches the remote; the code does not."""
    work, url = deployed_clone
    (work / "bot" / "thing.py").write_text("x = 2  # patched at 3am\n", encoding="utf-8")
    r = _run_source_check(work, url)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "SOURCE DIRTY" in r.stderr
    assert "bot/thing.py" in r.stderr, "it must name what differs"


def test_an_untracked_module_under_bot_counts_too(deployed_clone):
    """A stray module can shadow an import and change what runs without
    touching a single tracked file."""
    work, url = deployed_clone
    (work / "bot" / "shadow.py").write_text("boom = True\n", encoding="utf-8")
    r = _run_source_check(work, url)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "bot/shadow.py" in r.stderr


def test_allow_dirty_is_an_explicit_opt_out_that_still_says_so(deployed_clone):
    work, url = deployed_clone
    (work / "bot" / "thing.py").write_text("x = 3\n", encoding="utf-8")
    r = _run_source_check(work, url, "--allow-dirty")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "DIRTY" in r.stdout, "an allowed dirty tree must not print a clean OK"


# ── the two FALSE verdicts an operator hit in one day, 2026-09-22 ─────────
#
# Both were `fail`, which this script's own header classifies as A VERDICT,
# and both were about a box that was perfectly healthy. A checker that cries
# wolf is the failure its header warns about, aimed at itself.


def test_the_source_note_does_not_read_as_a_different_commit():
    """`7939268 (git)` is the SAME COMMIT as local `7939268e`.

    `build_info.short()` appends a source note by design — it is the one
    renderer every surface prints — and abbreviates to a fixed `sha[:7]`,
    while `git rev-parse --short` is adaptive and returns 8 here. Compared
    literally the two can never match, so a landed deploy printed WRONG CODE
    with instructions to re-fetch and reset.
    """
    httpd, url = _fake_box(build=f"{_head_short()[:7]} (git)")
    try:
        r = _run_box_check(url)
        assert "running this checkout" in r.stdout, r.stdout
        assert "WRONG CODE" not in r.stdout, (
            "a correct box was accused of running stale code:\n" + r.stdout)
    finally:
        httpd.shutdown()


def test_a_build_stamp_without_a_tree_check_verifies_but_says_so():
    """`(build stamp, tree unchecked)` is the right commit and an unverified
    tree — two facts, and folding them into one verdict loses the second."""
    httpd, url = _fake_box(build=f"{_head_short()[:7]} (build stamp, tree unchecked)")
    try:
        r = _run_box_check(url)
        assert "running this checkout" in r.stdout, r.stdout
        assert "did not check its tree" in r.stdout, (
            "an unchecked tree passed silently:\n" + r.stdout)
    finally:
        httpd.shutdown()


def test_a_dirty_box_matches_the_commit_and_is_still_a_failure():
    """The prefix matches; the box is still not running what was committed.

    `-dirty` must not be swallowed by the fix for the source note — that would
    trade one false verdict for a false acquittal, which is the quiet one.
    """
    httpd, url = _fake_box(build=f"{_head_short()[:7]}-dirty (git)")
    try:
        r = _run_box_check(url)
        assert "MODIFIED" in r.stdout, r.stdout
        assert r.returncode == 1, "a hand-patched box must be a verdict"
    finally:
        httpd.shutdown()


def test_a_slow_gateway_is_NOT_reported_as_unreachable():
    """Measured: a cold gateway answered in 58.7s and the 10s probe called a
    healthy service `unreachable — the website cannot reach the bot`.

    The repo's own systemd units wait 120s and 150s on these endpoints, so the
    script disagreed with its own deployment by a factor of twelve. A timeout
    (curl 28) and a refusal (curl 7) are different facts: the first is NOT a
    statement that the website cannot reach the bot, because the website has
    its own budget.
    """
    import socket
    import threading

    # A socket that ACCEPTS and never answers — the shape of a cold start.
    lis = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    lis.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    lis.bind(("127.0.0.1", 0))
    lis.listen(8)
    port = lis.getsockname()[1]
    held = []
    stop = threading.Event()

    def _accept_and_stall():
        lis.settimeout(0.5)
        while not stop.is_set():
            try:
                held.append(lis.accept()[0])
            except Exception:
                pass

    t = threading.Thread(target=_accept_and_stall, daemon=True)
    t.start()
    try:
        r = subprocess.run(
            [str(REPO / "scripts" / "verify_deploy.sh"), "--box-only"],
            capture_output=True, text=True, timeout=120,
            env={**os.environ,
                 "GATEWAY_URL": f"http://127.0.0.1:{port}",
                 "BRIDGE_URL": f"http://127.0.0.1:{port}",
                 "PROBE_TIMEOUT": "2"},
        )
        assert "did not answer within" in r.stdout, r.stdout
        assert "unreachable" not in r.stdout, (
            "a slow-but-listening gateway was called unreachable:\n" + r.stdout)
        assert r.returncode == 3, (
            f"a timeout is COULD NOT CHECK (3), not a failed deploy: rc={r.returncode}\n"
            + r.stdout)
    finally:
        stop.set()
        t.join(timeout=3)
        for c in held:
            try: c.close()
            except Exception: pass
        lis.close()


def test_nothing_listening_is_still_a_real_failure():
    """The fix must not turn a dead gateway into a shrug. curl returns 7
    instantly on a refused connection, so this stays a verdict and stays fast.
    """
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    free = s.getsockname()[1]
    s.close()
    r = subprocess.run(
        [str(REPO / "scripts" / "verify_deploy.sh"), "--box-only"],
        capture_output=True, text=True, timeout=60,
        env={**os.environ,
             "GATEWAY_URL": f"http://127.0.0.1:{free}",
             "BRIDGE_URL": f"http://127.0.0.1:{free}",
             "PROBE_TIMEOUT": "120"},
    )
    assert "unreachable" in r.stdout, r.stdout
    assert r.returncode == 1, "nothing listening is a FAILURE, not could-not-check"
