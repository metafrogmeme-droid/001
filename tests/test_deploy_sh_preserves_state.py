"""deploy.sh deleted the live trading state it exists to protect.

On 2026-08-02, on the production box, its FIRST run removed `data/` —
`secrets_vault.enc`, `runeclaw.db`, `live_positions.json`, `shadow_book.json`.
The only reason it was recoverable is that a snapshot had been taken minutes
earlier for an unrelated reason.

THE BUG WAS AN ORDERING ONE, THREE LINES APART:

    # data/ must exist as a directory in the store so the symlink resolves.
    mkdir -p "$PERSIST_DIR/data"      <- creates the store FIRST
    link_persistent "data"
    link_persistent ".env"

`link_persistent` moves the repo copy only when the store does NOT yet exist:

    if [ -e "$repo_path" ] && [ ! -e "$store_path" ]; then
        mv "$repo_path" "$store_path"          # unreachable for data/
    elif [ -e "$repo_path" ] && [ -e "$store_path" ]; then
        rm -rf "$repo_path"                    # what actually ran

Because `mkdir -p` guaranteed the store existed, the move branch could never
fire for `data/` and the discard branch always did.

`.env` moved correctly the entire time — it had no pre-created store path.
That asymmetry is what hid it: the script reports success for both, and only
one of them is telling the truth.

    THE SCRIPT WRITTEN TO PREVENT THE 2026-07-14 DATA-LOSS INCIDENT CAUSED
    ONE ITSELF, WHILE ITS OWN HEADER PROMISED "non-destructive".

The fix moves the mkdir BELOW link_persistent "data". This file exists because
no test would have caught the original — the failure needed a real directory
with real files in it, which is exactly what these tests build.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SH = REPO_ROOT / "deploy.sh"


def _fake_repo(tmp_path: Path, *, with_data=True, with_env=True) -> Path:
    """A stand-in deploy checkout: deploy.sh plus live state beside it."""
    repo = tmp_path / "runeclaw"
    repo.mkdir()
    shutil.copy(DEPLOY_SH, repo / "deploy.sh")
    (repo / "deploy.sh").chmod(0o755)
    if with_data:
        d = repo / "data"
        (d / "benchmark").mkdir(parents=True)
        # The four files the real incident destroyed.
        (d / "secrets_vault.enc").write_text("ENCRYPTED OPERATOR KEYS")
        (d / "runeclaw.db").write_text("TRADE DATABASE")
        (d / "live_positions.json").write_text('[{"symbol":"BTC/USDT"}]')
        (d / "shadow_book.json").write_text("{}")
        (d / "benchmark" / "BTC.csv.gz").write_text("tracked benchmark data")
    if with_env:
        (repo / ".env").write_text("BITGET_API_KEY=REAL\nJWT_SECRET=REAL\n")
    return repo


def _run(repo: Path, persist: Path):
    return subprocess.run(
        ["bash", str(repo / "deploy.sh")],
        cwd=str(repo), env={**os.environ, "PERSIST_DIR": str(persist), "HOME": str(repo.parent)},
        capture_output=True, text=True, timeout=60,
    )


class TestTheFirstRunMustNotDestroyAnything:
    """The exact incident. Every assertion here failed before the fix."""

    def test_the_secrets_vault_survives(self, tmp_path):
        repo, persist = _fake_repo(tmp_path), tmp_path / "persist"
        _run(repo, persist)
        kept = persist / "data" / "secrets_vault.enc"
        assert kept.exists(), "deploy.sh deleted the encrypted operator keys"
        assert kept.read_text() == "ENCRYPTED OPERATOR KEYS"

    @pytest.mark.parametrize("name,body", [
        ("runeclaw.db", "TRADE DATABASE"),
        ("live_positions.json", '[{"symbol":"BTC/USDT"}]'),
        ("shadow_book.json", "{}"),
        ("benchmark/BTC.csv.gz", "tracked benchmark data"),
    ])
    def test_every_state_file_survives(self, tmp_path, name, body):
        repo, persist = _fake_repo(tmp_path), tmp_path / "persist"
        _run(repo, persist)
        moved = persist / "data" / name
        assert moved.exists(), f"deploy.sh deleted {name}"
        assert moved.read_text() == body

    def test_the_env_survives(self, tmp_path):
        # This one always passed — .env had no pre-created store path. Kept so
        # a future change cannot break the half that was working.
        repo, persist = _fake_repo(tmp_path), tmp_path / "persist"
        _run(repo, persist)
        assert (persist / ".env").read_text().startswith("BITGET_API_KEY=REAL")

    def test_the_repo_paths_become_symlinks(self, tmp_path):
        repo, persist = _fake_repo(tmp_path), tmp_path / "persist"
        _run(repo, persist)
        for name in ("data", ".env"):
            p = repo / name
            assert p.is_symlink(), f"{name} is not a symlink — redeploy would wipe it"
            assert Path(os.readlink(p)).resolve() == (persist / name).resolve()


class TestTheOrderingIsTheFix:
    def test_mkdir_comes_after_the_move(self):
        """Guards the specific line order. Reverse it and the tests above fail,
        but this says WHY in one line when they do."""
        src = DEPLOY_SH.read_text().splitlines()
        link = next(i for i, l in enumerate(src)
                    if 'link_persistent "data"' in l and not l.strip().startswith("#"))
        mkdir = next(i for i, l in enumerate(src)
                     if 'mkdir -p "$PERSIST_DIR/data"' in l and not l.strip().startswith("#"))
        assert mkdir > link, (
            "mkdir -p runs BEFORE link_persistent \"data\" — that makes the move "
            "branch unreachable and the rm -rf branch inevitable. This is the "
            "2026-08-02 data-loss bug."
        )


class TestTheOtherStatesStillWork:
    """The fix must not cost the cases that were already correct."""

    def test_a_redeploy_keeps_the_store_and_discards_the_clone(self, tmp_path):
        # Second run: a fresh clone brought its own data/, the store holds the
        # REAL state. The store must win — that is the whole point.
        persist = tmp_path / "persist"
        repo = _fake_repo(tmp_path)
        _run(repo, persist)                     # first deploy: moves state out
        shutil.rmtree(repo / "data", ignore_errors=True)
        (repo / "data").unlink(missing_ok=True)
        (repo / "data" / "benchmark").mkdir(parents=True)   # the clone's copy
        (repo / "data" / "benchmark" / "BTC.csv.gz").write_text("from the clone")
        _run(repo, persist)
        assert (persist / "data" / "secrets_vault.enc").exists(), \
            "a redeploy discarded the persistent store"
        assert (repo / "data").is_symlink()

    def test_rerunning_is_idempotent(self, tmp_path):
        repo, persist = _fake_repo(tmp_path), tmp_path / "persist"
        for _ in range(3):
            assert _run(repo, persist).returncode == 0
        assert (persist / "data" / "secrets_vault.enc").read_text() == \
            "ENCRYPTED OPERATOR KEYS"

    def test_a_bare_box_gets_a_resolvable_symlink(self, tmp_path):
        # No data/ anywhere. The symlink must still resolve, which is what the
        # mkdir was originally for — it just cannot run before the move.
        repo = _fake_repo(tmp_path, with_data=False, with_env=False)
        persist = tmp_path / "persist"
        assert _run(repo, persist).returncode == 0
        assert (repo / "data").is_symlink()
        assert (repo / "data").resolve().is_dir(), "dangling symlink on a fresh box"
