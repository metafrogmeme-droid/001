"""A `.pyc` can outlive the source change that should have invalidated it.

Python reuses cached bytecode whenever the source's ``(mtime, size)`` match
what the ``.pyc`` recorded. Both are coarse — mtime is stored as whole seconds
and size says nothing about content — so an edit that is reverted inside the
same second, to a string of the same length, leaves both unchanged and the
cache "valid" over bytecode that no longer exists in any file.

THIS HAPPENED, and it cost an hour. A mutation experiment swapped
``r.get("max_margin")`` for ``r.get("margin_cap")`` in
``bot/utils/control_pull.py`` and put it back 260 ms later. Three
``test_control_pull`` tests then failed against source byte-identical to the
commit CI had just passed green. Everything that could be checked, checked
out: ``git diff`` empty, ``git status`` clean, ``inspect.getsource`` showing
the right line — and ``margin`` was ``None`` after
``margin = r.get("max_margin")`` ran on a dict that had that key. The
constant baked into the ``.pyc`` was ``'margin_cap'``.

It is the same fault as resetting to a stale remote-tracking ref, one layer
down. Every check passes because each is true of the stale artifact; the only
thing wrong is WHICH CODE, and nothing asks.

The failing direction is not the dangerous one. A stale cache can as easily
hold bytecode that PASSES, and `scripts/preflight.py` exists to answer "will
CI pass" — CI checks out a fresh tree and never has a cache at all. So
preflight now starts cold, and this pins both halves: that the trap is real,
and that the purge closes it.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import preflight  # noqa: E402


def _write_module(d: Path, key: str) -> Path:
    """A module whose behaviour depends on one same-length string constant."""
    p = d / "victim.py"
    p.write_text(textwrap.dedent(f"""
        def read(row):
            return row.get("{key}")
    """))
    return p


def _import_fresh(path: Path):
    """Import the module as Python would, honouring any cache next to it."""
    for m in list(sys.modules):
        if m == "victim":
            del sys.modules[m]
    spec = importlib.util.spec_from_file_location("victim", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["victim"] = mod
    spec.loader.exec_module(mod)
    return mod


def _stale_cache(tmp_path: Path) -> Path:
    """Build the exact condition: same size, same second, different content."""
    p = _write_module(tmp_path, "margin_cap")
    st = p.stat()
    # Compile and cache the FIRST version.
    subprocess.run([sys.executable, "-c",
                    f"import sys; sys.path.insert(0, {str(tmp_path)!r}); import victim"],
                   check=True, capture_output=True)
    # Revert to the real key — same length, so size is identical — and restore
    # the original timestamps, which is what an edit inside one second does.
    _write_module(tmp_path, "max_margin")
    os.utime(p, (st.st_atime, st.st_mtime))
    assert p.stat().st_size == st.st_size, "the two keys must be the same length"
    return p


def test_the_trap_is_real(tmp_path):
    """Without a purge, the interpreter runs bytecode no file contains."""
    p = _stale_cache(tmp_path)
    assert '"max_margin"' in p.read_text(), "the source says max_margin"
    served = _import_fresh(p).read({"max_margin": 250})
    assert served is None, (
        "the stale-cache trap did not reproduce on this interpreter. If Python "
        "has moved to hash-based .pyc by default, or the cache is disabled "
        "here (PYTHONDONTWRITEBYTECODE), the purge below is no longer load-"
        "bearing and this file should say so rather than assert a fiction."
    )


def test_the_purge_makes_the_source_win(tmp_path, monkeypatch):
    p = _stale_cache(tmp_path)
    monkeypatch.setattr(preflight, "ROOT", tmp_path)
    assert preflight.purge_pycache() == 1, "the cache directory was not found"
    assert _import_fresh(p).read({"max_margin": 250}) == 250, (
        "the source is running now")


def test_the_purge_leaves_the_tree_alone(tmp_path, monkeypatch):
    """It deletes caches and nothing else — including inside skipped dirs."""
    (tmp_path / "keep.py").write_text("x = 1\n")
    (tmp_path / "node_modules" / "dep" / "__pycache__").mkdir(parents=True)
    nested = tmp_path / "pkg" / "__pycache__"
    nested.mkdir(parents=True)
    (nested / "a.cpython-311.pyc").write_bytes(b"\x00")
    monkeypatch.setattr(preflight, "ROOT", tmp_path)
    assert preflight.purge_pycache() == 1, "node_modules must not be walked"
    assert not nested.exists()
    assert (tmp_path / "keep.py").read_text() == "x = 1\n"
    assert (tmp_path / "node_modules" / "dep" / "__pycache__").exists(), (
        "a dependency's cache is not ours to delete, and walking node_modules "
        "makes the purge slower than the tests it protects")


def _run_main(argv, tmp_path, monkeypatch) -> tuple[int, list[str]]:
    """Drive preflight.main() with one trivial gate, rooted at tmp_path.

    Returns its exit code and an ORDERED log of what happened, so "the purge
    ran" and "the purge ran first" are different assertions.
    """
    order: list[str] = []
    real_purge = preflight.purge_pycache

    def spy() -> int:
        order.append("purge")
        return real_purge()

    def fake_call(cmd, *a, **k) -> int:
        order.append(f"gate:{cmd}")
        return 0

    monkeypatch.setattr(preflight, "ROOT", tmp_path)
    monkeypatch.setattr(preflight, "purge_pycache", spy)
    monkeypatch.setattr(preflight, "steps", lambda fast: [("noop", "true", ".")])
    monkeypatch.setattr(preflight, "uncovered", lambda: ())
    monkeypatch.setattr(preflight.subprocess, "call", fake_call)
    monkeypatch.setattr(sys, "argv", ["preflight.py", *argv])
    return preflight.main(), order


def test_a_real_run_purges_before_the_first_gate(tmp_path, monkeypatch):
    """The function existing is not the function running — #999's whole lesson.

    Driven, not grepped: a source scan would pass with the call sitting after
    the loop, or inside a branch nothing takes.
    """
    p = _stale_cache(tmp_path)
    cache = p.parent / "__pycache__"
    assert cache.exists(), "the fixture did not produce a cache to purge"

    _, order = _run_main([], tmp_path, monkeypatch)

    assert order[:1] == ["purge"], (
        f"a run's first action was {order[:1]} — a gate that executes before "
        "the purge still sees the stale cache, which is the whole bug")
    assert any(o.startswith("gate:") for o in order), "no gate ran at all"
    assert not cache.exists(), "the cache survived a real run"


def test_list_runs_nothing_and_so_purges_nothing(tmp_path, monkeypatch):
    """`--list` prints the plan; a read-only flag must not mutate the tree."""
    p = _stale_cache(tmp_path)
    cache = p.parent / "__pycache__"
    rc, order = _run_main(["--list"], tmp_path, monkeypatch)
    assert rc == 0
    assert order == [], f"--list did something: {order}"
    assert cache.exists(), "--list deleted files while claiming to only print"
