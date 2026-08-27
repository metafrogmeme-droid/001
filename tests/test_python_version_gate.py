"""The interpreter is declared in three places; nothing checked it at deploy.

2026-08-17. `pip install -r requirements.lock` failed on the box with "no
matching distribution" for `numpy==2.3.5`, and the report back was that the pin
was wrong and PyPI's latest was 2.2.6.

Both claims were false. 2.3.5 exists, the latest is 2.5.2, and the lock file is
correct. numpy 2.3.x declares `requires-python >=3.11`, so an older interpreter
cannot SEE those releases — pip reports the newest version visible to THAT
python and the message reads as a fact about the index rather than about the
local machine. Acting on it would have downgraded a correct pin for everyone,
to fix a machine that was simply out of date.

`.python-version`, `pyproject.toml` and `ci.yml` all already said 3.11. All
three were right. Nothing compared them to the interpreter actually running, at
the one moment it mattered, so `deploy.sh` does that now — before pip is called,
because afterwards the evidence is a misleading error message.

These tests keep the three declarations agreeing with each other, and keep the
gate able to refuse.
"""

import contextlib
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# A 3.x interpreter shim: answers version_info with whatever it was built for,
# and forwards everything else to the real python3.
_SHIM = ('#!/bin/sh\ncase "$*" in *version_info*) echo "%s"; exit 0;; esac\n'
         'exec /usr/bin/python3 "$@"\n')


@contextlib.contextmanager
def _deploy_sandbox(venv_version=None):
    """A scratch tree to run `deploy.sh` in. NEVER run it against ROOT.

    deploy.sh's job is to MOVE `.env`, `data/` and `logs/` out of the repo and
    into PERSIST_DIR (`link_persistent`, deploy.sh:136-139). Two tests here
    used to invoke it with `cwd=ROOT` and then `rmtree` the store in their
    `finally:` — which on any machine with a populated `.env` deleted the
    operator's Bitget keys, Telegram token, encrypted secrets vault, shadow
    book, position state and `logs/audit_chain.jsonl`, plus `<repo>/.venv`.
    That is the 2026-07-14 incident described at the top of deploy.sh, caused
    by the tests written to prove it had been fixed. CI never saw it because CI
    checks out a fresh tree with no `.env` to lose.

    deploy.sh reads exactly two things from its repo root — `.python-version`
    and `.venv/bin/python` — and derives that root from BASH_SOURCE. So a copy
    of the script beside a copy of those two files exercises the gate
    identically, with nothing outside the sandbox reachable.

    Yields (sandbox_path, env_overrides).
    """
    with tempfile.TemporaryDirectory() as tmp:
        sandbox = Path(tmp)
        shutil.copy(ROOT / "deploy.sh", sandbox / "deploy.sh")
        shutil.copy(ROOT / ".python-version", sandbox / ".python-version")
        if venv_version is not None:
            vbin = sandbox / ".venv" / "bin"
            vbin.mkdir(parents=True)
            (vbin / "python").write_text(_SHIM % venv_version, encoding="utf-8")
            (vbin / "python").chmod(0o755)
        yield sandbox, {"PERSIST_DIR": str(sandbox / "_persist")}


def _stale_python(sandbox, version="3.10"):
    """A directory to put on PATH whose `python3` reports `version`."""
    stale = sandbox / "_stalepath"
    stale.mkdir(parents=True, exist_ok=True)
    (stale / "python3").write_text(_SHIM % version, encoding="utf-8")
    (stale / "python3").chmod(0o755)
    return stale


def _declared():
    return (ROOT / ".python-version").read_text(encoding="utf-8").strip()


def test_the_three_declarations_agree():
    """A gate that reads one file while CI honours another is worse than none:
    it passes locally and fails in the place nobody is watching."""
    pinned = _declared()
    assert re.fullmatch(r"\d+\.\d+", pinned), f"unparsable .python-version: {pinned!r}"

    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'requires-python\s*=\s*"([^"]+)"', pyproject)
    assert m, "pyproject.toml stopped declaring requires-python"
    assert pinned in m.group(1), (
        f"pyproject requires {m.group(1)}, .python-version says {pinned}")

    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    versions = set(re.findall(r'python-version:\s*"?([\d.]+)"?', ci))
    assert versions, "ci.yml no longer pins a python-version"
    assert versions == {pinned}, (
        f"CI validates on {sorted(versions)}, the repo declares {pinned} — "
        f"every pin resolved by CI may be unreachable where it is deployed")


def test_the_deploy_refuses_an_older_interpreter():
    """The behaviour, driven — not the presence of the code that implements it."""
    # No `.venv` in the sandbox, so the gate falls through to PATH's python3,
    # which the stale shim answers as 3.10.
    with _deploy_sandbox() as (sandbox, overrides):
        stale = _stale_python(sandbox, "3.10")
        env = {**os.environ, "PATH": f"{stale}:{os.environ['PATH']}", **overrides}
        r = subprocess.run(["bash", str(sandbox / "deploy.sh")], cwd=sandbox,
                           capture_output=True, text=True, env=env, timeout=60)
        assert r.returncode == 1, (
            f"deploy.sh continued on Python 3.10 (exit {r.returncode}) — it would "
            f"reach pip and produce the misleading 'no matching distribution'")
        out = r.stdout + r.stderr
        assert "older than" in out
        # The message must name the real cause. A bare version error sends the
        # operator to the lock file, which is what happened.
        assert "Do NOT edit requirements.lock" in out, (
            "the refusal must say not to change the pin, or the next person "
            "will change the pin")


def test_the_gate_runs_before_pip_is_ever_reached():
    """Order is the whole point: after pip has spoken, the evidence on screen
    argues for the wrong fix."""
    sh = (ROOT / "deploy.sh").read_text(encoding="utf-8")
    gate = sh.index(".python-version")
    persist = sh.index("RUNECLAW deploy — persisting state to")
    assert gate < persist, "the interpreter check must come first"


def test_numpy_is_not_quietly_downgraded():
    """The pin this incident argued against. If a future commit lowers it, that
    is a decision someone should make deliberately — not a workaround for an
    out-of-date box."""
    lock = (ROOT / "requirements.lock").read_text(encoding="utf-8")
    m = re.search(r"^numpy==(\d+)\.(\d+)\.(\d+)", lock, re.M)
    assert m, "numpy is no longer pinned in requirements.lock"
    major, minor = int(m.group(1)), int(m.group(2))
    assert (major, minor) >= (2, 3), (
        f"numpy pinned to {m.group(0)}: 2.3+ needs Python 3.11, which this repo "
        f"already requires. Dropping below it to satisfy an older interpreter "
        f"fixes the machine by changing everyone else's dependency.")


def test_the_gate_prefers_the_project_venv_over_path():
    """The box runs 3.11 inside `.venv` while its system python3 is still 3.10.

    Checking PATH alone would refuse a deploy that is in fact correct, and a
    gate that cries wolf gets commented out — at which point it is not a gate.
    So the venv interpreter wins when there is one.
    """
    with _deploy_sandbox(venv_version="3.11") as (sandbox, overrides):
        stale = _stale_python(sandbox, "3.10")
        env = {**os.environ, "PATH": f"{stale}:{os.environ['PATH']}", **overrides}
        r = subprocess.run(["bash", str(sandbox / "deploy.sh")], cwd=sandbox,
                           capture_output=True, text=True, env=env, timeout=60)
        assert r.returncode == 0, (
            "the gate refused a correct venv deploy because PATH's python3 is "
            f"stale (exit {r.returncode})")
        assert ".venv/bin/python" in r.stdout, (
            "the gate must name WHICH interpreter it checked; otherwise a "
            "passing line is unattributable to a machine with two of them")


def test_the_gate_tests_never_run_deploy_against_the_repo():
    """The guard on the two tests above, because they cost real secrets once.

    `deploy.sh` MOVES `.env`, `data/` and `logs/` into PERSIST_DIR and symlinks
    them back — that is the whole point of it. Both tests here used to invoke it
    with `cwd=ROOT` and then `rmtree` the store, so on a developer machine the
    suite deleted the operator's Bitget keys, Telegram token, secrets vault,
    position state and audit chain, and `<repo>/.venv` with them. Nothing
    noticed, because CI's tree has none of those to lose.

    Source-scanned rather than driven, deliberately: the property is that no
    call site passes ROOT, and a behavioural test for "did this delete your
    .env" can only be written by having something to delete.
    """
    from tests.test_preflight_matches_ci import code_only
    src = (ROOT / "tests" / "test_python_version_gate.py").read_text(encoding="utf-8")
    # Strip comments and docstrings first: the docstring above QUOTES what this
    # forbids, and a raw-text scan cannot tell a warning about a pattern from
    # the pattern.
    code = code_only(src)
    # ...and the needles are ASSEMBLED, never written whole, because they are
    # VALUES — code_only keeps them, so a literal list would match itself and
    # fail on the file that is already correct. Same trap one level in.
    needles = ["cwd" + "=ROOT", 'str(ROOT / "deploy' + '.sh")']
    for bad in needles:
        assert bad not in code, (
            f"{bad!r} is back in this file. deploy.sh MOVES .env, data/ and "
            f"logs/ out of whatever directory it is pointed at; pointing it at "
            f"the repo root and cleaning up afterwards deletes the developer's "
            f"Bitget keys, Telegram token, secrets vault and audit chain. Use "
            f"_deploy_sandbox() instead.")


def test_the_suite_leaves_the_repo_dotenv_alone():
    """A dangling `.env` symlink is the fingerprint of the bug above.

    The old cleanup removed the `data` and `logs` symlinks and forgot `.env`,
    so even when the store was empty the repo was left with `.env` pointing at
    a directory that had just been deleted — every later `load_dotenv()` in the
    run reading a broken link.
    """
    env_path = ROOT / ".env"
    if env_path.is_symlink():
        assert env_path.exists(), (
            f"{env_path} is a symlink to {os.readlink(env_path)}, which does not "
            f"exist. Something in this suite ran deploy.sh against the repo root "
            f"and deleted the store it moved .env into.")
