"""Only the bot writes the bot's state. Every other engine is a reader.

`RuneClawEngine` loads the operator's portfolio and risk state from the data
directory and saves the WHOLE of it back from memory on every change
(`_save_combined_state`). So any second process that builds one over the same
directory holds a stale copy, and its first save erases whatever the bot
recorded since. The API bridge was the one in production
(tests/test_the_bridge_is_a_reader_of_the_bots_state.py), and it was not the
only one:

  * `live_e2e_test.py` -- "Runs against the live bot process" -- resets the
    breaker it loaded "for a clean test", calls `emergency_halt`, and undoes
    that in memory only. Driven: the bot's saved state then reads HALTED,
    cause "manual", and a restarted bot comes up halted, with the reason in
    an audit line nobody reads beside it.
  * Read, not driven: `scripts/e2e_pipeline.py` opens positions in the
    operator's shared paper book, which saves on every open; `live_test.py`,
    `scripts/test_all_skills.py` and `bot/main.py`'s `--mode cli` and
    `--mode scan` each build their own engine too, and the CLI runs any
    registered skill -- the halt skill included -- against its copy and
    prints what the skill says.
  * The MCP adapter built one for itself whenever none was handed in.

A list of those sites would be the `/setllm` ten-of-eleven shape -- the
script added tomorrow is the one missing -- so this is a RULE over every
construction outside `tests/`: the engine is detached
(`detach_state_persistence`) in the same scope, AFTER it is built, or the
site is the owner and says why. The owner list has one row, and a row whose
site is gone fails, as `known_failures.txt` does.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

#: The processes that OWN the bot's state, with why. One row: the deployed
#: bot. Everything else that builds an engine is a reader.
OWNERS = {
    ("bot/main.py", "run_telegram"):
        "the deployed bot (`--mode telegram`); the state is its record",
}

_SKIP_DIRS = {"tests", "node_modules", ".git", "__pycache__", "venv", ".venv"}


def _sources(root: Path):
    import os
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        for fn in sorted(filenames):
            if fn.endswith(".py"):
                p = Path(dirpath) / fn
                yield p.relative_to(root).as_posix(), p.read_text(encoding="utf-8")


def _is_construction(node) -> bool:
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    return ((isinstance(f, ast.Name) and f.id == "RuneClawEngine")
            or (isinstance(f, ast.Attribute) and f.attr == "RuneClawEngine"))


def _scopes(tree):
    """(name, node) for the module and every function, each walked without
    descending into the functions nested in it."""
    yield "<module>", tree
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield n.name, n


def _own_nodes(scope):
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        n = stack.pop()
        yield n
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(n))


def _target(assign) -> str | None:
    """The dotted name an assignment binds, or None."""
    if not isinstance(assign, ast.Assign) or len(assign.targets) != 1:
        return None
    try:
        return ast.unparse(assign.targets[0])
    except Exception:
        return None


def constructions(sources):
    """Every construction site as (path, scope, line, verdict): "detached",
    "never detached", or "not bound to a name". Whether a never-detached site
    is an OWNER is `violations`'s question, not this walk's."""
    out = []
    for path, src in sources:
        tree = ast.parse(src)
        for name, scope in _scopes(tree):
            own = list(_own_nodes(scope))
            for n in own:
                if not _is_construction(n):
                    continue
                parent = next((a for a in own if isinstance(a, ast.Assign)
                               and a.value is n), None)
                target = _target(parent) if parent is not None else None
                if target is None:
                    out.append((path, name, n.lineno, "not bound to a name"))
                    continue
                detached = any(
                    isinstance(c, ast.Call)
                    and isinstance(c.func, ast.Attribute)
                    and c.func.attr == "detach_state_persistence"
                    and ast.unparse(c.func.value) == target
                    and c.lineno > n.lineno
                    for c in own)
                out.append((path, name, n.lineno,
                            "detached" if detached else "never detached"))
    return out


def violations(sources, owners):
    found = constructions(sources)
    bad = []
    for path, scope, line, verdict in found:
        if verdict == "detached":
            continue
        if (path, scope) in owners and verdict == "never detached":
            continue
        bad.append(f"{path}:{line} ({scope}) -- {verdict}")
    sites = {(p, s) for p, s, _, v in found if v == "never detached"}
    for key in owners:
        if key not in sites:
            bad.append(f"owner row {key} names no construction that writes")
    return bad


# ── the rule, on the real tree ───────────────────────────────────────────

def test_every_engine_but_the_bots_is_a_reader():
    bad = violations(_sources(ROOT), OWNERS)
    assert not bad, (
        "an engine built over the bot's data directory writes the bot's state "
        "unless it is detached; detach it, or name it as an owner with the "
        "reason:\n  " + "\n  ".join(bad))


def test_the_rule_sees_the_known_readers():
    # A walk that found nothing would pass the test above over any tree.
    found = {(p, s) for p, s, _, v in constructions(_sources(ROOT))
             if v == "detached"}
    for site in [("api_bridge.py", "lifespan"), ("bot/main.py", "run_cli"),
                 ("bot/main.py", "run_scan"), ("bot/mcp/server.py", "__init__")]:
        assert site in found, (site, sorted(found))


# ── the rule, on planted trees (the real one answers nothing) ────────────

def _plant(src):
    return [("x.py", src)]


@pytest.mark.parametrize("src,expect", [
    ("def f():\n    e = RuneClawEngine()\n    e.detach_state_persistence()\n", []),
    ("def f():\n    e = RuneClawEngine()\n", ["never detached"]),
    ("def f():\n    e = RuneClawEngine()\n    other.detach_state_persistence()\n",
     ["never detached"]),
    ("def f():\n    e.detach_state_persistence()\n    e = RuneClawEngine()\n",
     ["never detached"]),
    ("def f():\n    RuneClawEngine().scan()\n", ["not bound to a name"]),
    ("def f():\n    self.e = RuneClawEngine()\n    self.e.detach_state_persistence()\n", []),
    ("def f():\n    e = core.RuneClawEngine()\n", ["never detached"]),
    ("def f():\n    e = RuneClawEngine()\n    def g():\n        e.detach_state_persistence()\n",
     ["never detached"]),
], ids=["detached", "never", "another-name", "before-construction",
        "unbound", "attribute-target", "module-qualified", "only-in-a-nested-def"])
def test_the_rule(src, expect):
    got = [v.split(" -- ")[1] for v in violations(_plant(src), {})]
    assert got == expect


def test_an_owner_is_accepted_and_a_stale_owner_is_not():
    src = "def run():\n    e = RuneClawEngine()\n"
    assert violations(_plant(src), {("x.py", "run"): "the owner"}) == []
    stale = violations(_plant("def run():\n    pass\n"), {("x.py", "run"): "gone"})
    assert stale and "names no construction" in stale[0]


def test_an_owner_row_does_not_excuse_an_unbound_construction():
    got = violations(_plant("def run():\n    RuneClawEngine().go()\n"),
                     {("x.py", "run"): "the owner"})
    assert got and "not bound" in got[0]


# ── and the adapter that builds one for itself ───────────────────────────

def test_the_mcp_adapter_detaches_an_engine_it_builds(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    monkeypatch.setattr("bot.utils.paths.REPO_ROOT", tmp_path)
    import bot.mcp.server as srv
    monkeypatch.setattr(srv, "_MCP_AUTH_TOKEN", "t" * 32)
    built = srv.RuneClawMCPServer()
    assert built._engine._state_persistence_detached is True
    handed = srv.RuneClawEngine()
    kept = srv.RuneClawMCPServer(engine=handed)
    assert kept._engine is handed
    assert not getattr(handed, "_state_persistence_detached", False)
