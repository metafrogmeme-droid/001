"""`data -> already linked`, while data/ changed which store it points at.

`link_persistent` tested one fact and reported another:

    if [ -L "$repo_path" ]; then
      ln -sfn "$store_path" "$repo_path"
      echo "  $name -> already linked"

The condition asks "is this a symlink". The action REPOINTS it. The message
claims continuity. All three are about different things, and when PERSIST_DIR
differs from the previous run — a different value, or the DEFAULT after an
earlier run used a custom one — the three come apart:

    PERSIST_DIR=A ./deploy.sh    data/ -> A/data, users.json migrated in
    PERSIST_DIR=B ./deploy.sh    "data -> already linked"
                                 data/ -> B/data, which is EMPTY

Nothing is deleted. A still holds everything. But the bot now reads an empty
directory, and a user store that loads empty and then saves becomes a roster of
whoever messages next — which is the shape of the incident where eighteen
registered users showed as two.

REFUSED, NOT WARNED. Repointing a populated store at an empty one is never what
anybody means by "deploy", and a warning scrolls past: the next thing the
operator does is start the bot, and starting the bot is what converts a wrong
symlink into a rewritten file. `test_it_refuses_rather_than_warning` is the one
that matters.

REPOINTING TO A POPULATED STORE IS STILL ALLOWED. That is a real thing an
operator does — moving the store to a bigger disk — and blocking it would be
the opposite error. It just may not call itself "already linked".
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy.sh"


def _deploy(repo, persist):
    p = subprocess.run(["bash", "deploy.sh"], cwd=str(repo), timeout=60,
                       capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin:/usr/local/bin",
                            "HOME": str(repo.parent),
                            "PERSIST_DIR": str(persist)})
    return p.returncode, p.stdout + p.stderr


@pytest.fixture
def box(tmp_path):
    """A repo with a populated data/, and two empty candidate stores."""
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    (repo / "data" / "users.json").write_text('{"1": {"role": "admin"}}')
    (repo / "deploy.sh").write_text(DEPLOY.read_text(encoding="utf-8"),
                                    encoding="utf-8")
    return {"repo": repo, "A": tmp_path / "storeA", "B": tmp_path / "storeB",
            "tmp": tmp_path}


def _link(repo):
    return (repo / "data").readlink() if (repo / "data").is_symlink() else None


# ── the incident ────────────────────────────────────────────────────────────

def test_it_refuses_rather_than_warning(box):
    """THE ONE THAT MATTERS. A warning scrolls past; the next thing the
    operator does is start the bot, and that is what turns a wrong symlink
    into a rewritten users.json."""
    _deploy(box["repo"], box["A"])
    assert (box["A"] / "data" / "users.json").exists(), "setup: A holds the data"

    code, out = _deploy(box["repo"], box["B"])
    assert code != 0, f"a swing to an empty store succeeded:\n{out}"
    assert "REFUSING" in out


def test_the_refusal_leaves_the_link_and_the_data_alone(box):
    """A guard that damages what it is protecting is worse than none."""
    _deploy(box["repo"], box["A"])
    before = _link(box["repo"])

    _deploy(box["repo"], box["B"])
    assert _link(box["repo"]) == before, "the link moved despite the refusal"
    assert (box["repo"] / "data" / "users.json").exists(), "the roster is gone"


def test_the_refusal_names_both_paths_and_says_nothing_was_deleted(box):
    """An operator reading this needs to know where the data still is, or the
    refusal reads as loss."""
    _deploy(box["repo"], box["A"])
    _, out = _deploy(box["repo"], box["B"])
    assert str(box["A"] / "data") in out, "the surviving store is not named"
    assert str(box["B"] / "data") in out
    assert "Nothing has been deleted" in out
    assert "PERSIST_DIR" in out, "the remedy does not name the variable"


# ── the ordinary path stays quiet ───────────────────────────────────────────

def test_the_same_store_twice_is_still_just_already_linked(box):
    """CONTROL. deploy.sh is documented as idempotent and is run on every
    deploy; making the normal case noisy would train the operator to ignore
    the line that matters."""
    _deploy(box["repo"], box["A"])
    code, out = _deploy(box["repo"], box["A"])
    assert code == 0, out
    assert "data -> already linked" in out
    assert "REFUSING" not in out and "REPOINTED" not in out


def test_first_run_still_migrates(box):
    """CONTROL. The guard must not break the path that creates the store."""
    code, out = _deploy(box["repo"], box["A"])
    assert code == 0, out
    assert (box["A"] / "data" / "users.json").exists()
    assert _link(box["repo"]) == box["A"] / "data"


# ── a real store move is allowed, but is not called continuity ──────────────

def test_repointing_to_a_populated_store_is_allowed(box):
    """Moving the store to a bigger disk is a real thing an operator does.
    Blocking it would be the opposite error."""
    _deploy(box["repo"], box["A"])
    (box["B"] / "data").mkdir(parents=True)
    (box["B"] / "data" / "users.json").write_text('{"2": {}}')

    code, out = _deploy(box["repo"], box["B"])
    assert code == 0, out
    assert _link(box["repo"]) == box["B"] / "data"


def test_a_store_change_does_not_report_itself_as_already_linked(box):
    """THE MESSAGE IS THE DEFECT. Repointing is fine; calling it continuity is
    not, because "already linked" is what an operator reads as "nothing
    changed"."""
    _deploy(box["repo"], box["A"])
    (box["B"] / "data").mkdir(parents=True)
    (box["B"] / "data" / "users.json").write_text('{"2": {}}')

    _, out = _deploy(box["repo"], box["B"])
    assert "REPOINTED" in out
    assert "data -> already linked" not in out, (
        "a change of store still reports as continuity")


# ── the shape, source-checked ───────────────────────────────────────────────

def test_the_symlink_branch_compares_targets_rather_than_existence():
    """The property that makes the rest possible: the branch must ask WHERE the
    link points, not merely whether it is a link."""
    src = DEPLOY.read_text(encoding="utf-8")
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert "readlink" in code, (
        "deploy.sh no longer reads the existing link target, so it cannot tell "
        "a repoint from continuity")
    assert 'current" = "$store_path' in code or '"$current" = "$store_path"' in code
