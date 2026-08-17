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

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


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
    fake = ROOT / "build" / "_fakepy"
    fake.mkdir(parents=True, exist_ok=True)
    shim = fake / "python3"
    shim.write_text(
        '#!/bin/sh\ncase "$*" in *version_info*) echo "3.10"; exit 0;; esac\n'
        'exec /usr/bin/python3 "$@"\n', encoding="utf-8")
    shim.chmod(0o755)
    try:
        import os
        env = {**os.environ, "PATH": f"{fake}:{os.environ['PATH']}",
               "PERSIST_DIR": str(ROOT / "build" / "_pdtest")}
        r = subprocess.run(["bash", str(ROOT / "deploy.sh")], cwd=ROOT,
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
    finally:
        import shutil
        shutil.rmtree(fake, ignore_errors=True)
        shutil.rmtree(ROOT / "build" / "_pdtest", ignore_errors=True)


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
    import os
    import shutil
    venv = ROOT / ".venv" / "bin"
    stale = ROOT / "build" / "_stalepy"
    venv.mkdir(parents=True, exist_ok=True)
    stale.mkdir(parents=True, exist_ok=True)
    shim = ('#!/bin/sh\ncase "$*" in *version_info*) echo "%s"; exit 0;; esac\n'
            'exec /usr/bin/python3 "$@"\n')
    (venv / "python").write_text(shim % "3.11", encoding="utf-8")
    (venv / "python").chmod(0o755)
    (stale / "python3").write_text(shim % "3.10", encoding="utf-8")
    (stale / "python3").chmod(0o755)
    try:
        env = {**os.environ, "PATH": f"{stale}:{os.environ['PATH']}",
               "PERSIST_DIR": str(ROOT / "build" / "_pdvenv")}
        r = subprocess.run(["bash", str(ROOT / "deploy.sh")], cwd=ROOT,
                           capture_output=True, text=True, env=env, timeout=60)
        assert r.returncode == 0, (
            "the gate refused a correct venv deploy because PATH's python3 is "
            f"stale (exit {r.returncode})")
        assert ".venv/bin/python" in r.stdout, (
            "the gate must name WHICH interpreter it checked; otherwise a "
            "passing line is unattributable to a machine with two of them")
    finally:
        shutil.rmtree(ROOT / ".venv", ignore_errors=True)
        shutil.rmtree(stale, ignore_errors=True)
        shutil.rmtree(ROOT / "build" / "_pdvenv", ignore_errors=True)
        for leftover in ("data", "logs"):
            pth = ROOT / leftover
            if pth.is_symlink():
                pth.unlink()
