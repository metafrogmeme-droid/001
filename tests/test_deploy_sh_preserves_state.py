"""deploy.sh must never destroy live state.

deploy.sh's header promises "Idempotent and non-destructive". On 2026-08-02 it
was neither: the first run on the production box deleted secrets_vault.enc,
runeclaw.db, live_positions.json and shadow_book.json, and was only recoverable
because a snapshot happened to have been taken minutes earlier.

The mechanism was ordering. link_persistent migrates the repo copy into the
persistent store only when the store path does not yet exist:

    [ -e "$repo_path" ] && [ ! -e "$store_path" ]   -> mv

A `mkdir -p "$PERSIST_DIR/data"` placed ABOVE the call made that test
permanently false for data/, so the next branch fired instead — "the store is
authoritative" — and rm -rf'd the repo copy. Nothing pre-created
$PERSIST_DIR/.env, so .env took the migrate branch and moved correctly. That
asymmetry is what hid it: the script printed success for both paths and only
one was telling the truth.

These tests run the real script against a throwaway repo and store, so they
fail on the broken ordering rather than on a description of it. They also cover
the SECOND layer, which the first version of this fix did not have: an EMPTY
store beside a POPULATED repo directory is migrated, never discarded. That
layer would have contained the incident on its own, with the ordering bug still
in place — so it also covers a store created by hand, and a future
reintroduction of the same ordering mistake.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SH = REPO_ROOT / "deploy.sh"

# The files the incident actually destroyed. Named explicitly so a regression
# reads as "the vault is gone" rather than "a file count changed".
LIVE_STATE = {
    "secrets_vault.enc": b"\x00encrypted-operator-keys",
    "runeclaw.db": b"SQLite format 3\x00",
    "live_positions.json": b'[{"symbol": "MSFT", "size_usd": 12.51}]',
    "shadow_book.json": b'{"open": []}',
}


def _make_repo(tmp_path: Path, *, with_data: bool = True, with_env: bool = True) -> Path:
    """A minimal checkout: deploy.sh plus whatever state the case needs."""
    repo = tmp_path / "runeclaw"
    repo.mkdir()
    shutil.copy2(DEPLOY_SH, repo / "deploy.sh")
    os.chmod(repo / "deploy.sh", 0o755)
    if with_data:
        data = repo / "data"
        data.mkdir()
        for name, blob in LIVE_STATE.items():
            (data / name).write_bytes(blob)
        # A tracked file too — data/ holds committed benchmark CSVs, which is
        # why a fresh clone always brings a copy of this directory.
        (data / "benchmark.csv").write_text("date,close\n2026-01-01,100\n")
    if with_env:
        (repo / ".env").write_text(
            "BITGET_API_KEY=fixture-not-a-real-key\nJWT_SECRET=fixture-not-a-real-secret\n")
    return repo


def _run(repo: Path, persist: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "PERSIST_DIR": str(persist)}
    return subprocess.run(
        ["bash", "./deploy.sh"], cwd=repo, env=env,
        capture_output=True, text=True, timeout=60,
    )


def _assert_state_intact(repo: Path, persist: Path) -> None:
    """Every byte of live state is still reachable, through the symlink."""
    assert (repo / "data").is_symlink(), "data/ should be a symlink into the store"
    assert (repo / "data").resolve() == persist.resolve() / "data"
    for name, blob in LIVE_STATE.items():
        through_link = repo / "data" / name
        in_store = persist / "data" / name
        assert in_store.exists(), f"{name} was destroyed — it is not in the store"
        assert in_store.read_bytes() == blob, f"{name} was corrupted"
        assert through_link.read_bytes() == blob, f"{name} unreachable via the symlink"


class TestFirstRunMigratesLiveState:
    """The incident case: real state in the repo, nothing in the store yet."""

    def test_data_directory_survives(self, tmp_path):
        repo = _make_repo(tmp_path)
        persist = tmp_path / "persist"
        r = _run(repo, persist)
        assert r.returncode == 0, r.stderr
        _assert_state_intact(repo, persist)

    def test_secrets_vault_specifically_survives(self, tmp_path):
        # Called out on its own: losing this one costs the operator their
        # exchange keys, and re-entering them by hand is what produced the
        # quoted-key 40006 auth failure the script was written to prevent.
        repo = _make_repo(tmp_path)
        persist = tmp_path / "persist"
        _run(repo, persist)
        vault = persist / "data" / "secrets_vault.enc"
        assert vault.exists(), "secrets_vault.enc destroyed on first deploy"
        assert vault.read_bytes() == LIVE_STATE["secrets_vault.enc"]

    def test_tracked_files_move_too(self, tmp_path):
        # data/ is not purely runtime state — the benchmark CSVs are committed.
        # A migration that moved only the untracked files would leave the repo
        # copy behind for the next run's rm -rf.
        repo = _make_repo(tmp_path)
        persist = tmp_path / "persist"
        _run(repo, persist)
        assert (persist / "data" / "benchmark.csv").exists()

    def test_env_survives(self, tmp_path):
        repo = _make_repo(tmp_path)
        persist = tmp_path / "persist"
        _run(repo, persist)
        assert (repo / ".env").is_symlink()
        assert (persist / ".env").read_text().startswith(
            "BITGET_API_KEY=fixture-not-a-real-key")

    def test_reports_migration_not_discard(self, tmp_path):
        # The output has to match what happened. The bug's whole shape was a
        # success message printed over a deletion.
        repo = _make_repo(tmp_path)
        r = _run(repo, tmp_path / "persist")
        assert "discarded" not in r.stdout, f"claimed a discard on first run:\n{r.stdout}"


class TestRedeployOverPopulatedStore:
    """The steady state: a fresh clone lays its committed copy over live data."""

    def test_store_wins_and_live_state_is_kept(self, tmp_path):
        repo = _make_repo(tmp_path)
        persist = tmp_path / "persist"
        _run(repo, persist)                      # first deploy: migrate
        # Simulate the re-clone: symlinks replaced by the committed copy.
        (repo / "data").unlink()
        (repo / ".env").unlink()
        (repo / "data").mkdir()
        (repo / "data" / "benchmark.csv").write_text("date,close\n2026-01-01,100\n")
        (repo / ".env").write_text("BITGET_API_KEY=placeholder\n")

        r = _run(repo, persist)
        assert r.returncode == 0, r.stderr
        _assert_state_intact(repo, persist)
        # The clone's placeholder .env must not overwrite the operator's real one.
        assert "fixture-not-a-real-key" in (persist / ".env").read_text()

    def test_repeated_deploys_never_erode_state(self, tmp_path):
        repo = _make_repo(tmp_path)
        persist = tmp_path / "persist"
        for _ in range(3):
            r = _run(repo, persist)
            assert r.returncode == 0, r.stderr
            _assert_state_intact(repo, persist)


class TestEmptyStoreIsNeverAuthoritative:
    """An empty store beside populated data is a bug signature, not a fresh clone.

    THIS is the layer the first version of the fix lacked. With only the
    ordering corrected, a store that exists but is empty — created by hand, or
    by any future reintroduction of the hoisted mkdir — still routes to the
    "store is authoritative" branch and deletes the only copy of the data.
    """

    def test_populated_repo_wins_over_empty_store(self, tmp_path):
        repo = _make_repo(tmp_path)
        persist = tmp_path / "persist"
        (persist / "data").mkdir(parents=True)   # store exists but is empty
        r = _run(repo, persist)
        assert r.returncode == 0, r.stderr
        _assert_state_intact(repo, persist)

    def test_dotfiles_migrate_too(self, tmp_path):
        # `mv src/* dst/` silently skips dotfiles; data/ carries them.
        repo = _make_repo(tmp_path)
        (repo / "data" / ".cursor").write_text("1738000000")
        persist = tmp_path / "persist"
        (persist / "data").mkdir(parents=True)
        _run(repo, persist)
        assert (persist / "data" / ".cursor").read_text() == "1738000000"


class TestBareBox:
    """No repo state, no store — the symlink still has to resolve."""

    def test_data_symlink_resolves_to_a_directory(self, tmp_path):
        repo = _make_repo(tmp_path, with_data=False, with_env=False)
        persist = tmp_path / "persist"
        r = _run(repo, persist)
        assert r.returncode == 0, r.stderr
        assert (repo / "data").is_symlink()
        assert (repo / "data").is_dir(), "data/ symlink dangles — the bot cannot write"

    def test_missing_env_is_reported_not_invented(self, tmp_path):
        repo = _make_repo(tmp_path, with_data=False, with_env=False)
        r = _run(repo, tmp_path / "persist")
        assert "No .env in the persistent store yet" in r.stdout


class TestAlreadyLinked:
    def test_existing_symlink_is_repointed_not_followed(self, tmp_path):
        repo = _make_repo(tmp_path)
        persist = tmp_path / "persist"
        _run(repo, persist)
        # Point it somewhere else, as a half-finished manual fix would.
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (repo / "data").unlink()
        (repo / "data").symlink_to(elsewhere)
        r = _run(repo, persist)
        assert r.returncode == 0, r.stderr
        _assert_state_intact(repo, persist)
        # And the stray directory was not deleted on the way past.
        assert elsewhere.is_dir()


class TestTheGuardWouldCatchTheOriginalBug:
    """Mutation check — reintroduce the ordering and confirm a test fails.

    Without this, the suite above proves only that today's script is correct,
    not that it would have caught the thing that actually happened.
    """

    def test_hoisting_the_mkdir_destroys_state(self, tmp_path):
        broken = DEPLOY_SH.read_text().replace(
            'link_persistent "data"\n'
            'mkdir -p "$PERSIST_DIR/data"     # AFTER the move, never before',
            'mkdir -p "$PERSIST_DIR/data"     # AFTER the move, never before\nlink_persistent "data"',
            1,
        )
        assert broken != DEPLOY_SH.read_text(), (
            "could not reintroduce the bug — the ordering block was edited; "
            "update this mutation before trusting the suite"
        )
        # Also drop the empty-store guard, so this reproduces the original
        # single-layer script rather than being saved by the later defence.
        broken = broken.replace('[ -z "$(ls -A "$store_path" 2>/dev/null)" ]', "false", 1)

        repo = _make_repo(tmp_path)
        (repo / "deploy.sh").write_text(broken)
        persist = tmp_path / "persist"
        _run(repo, persist)
        assert not (persist / "data" / "secrets_vault.enc").exists(), (
            "the mutation did not destroy state — this suite is not testing "
            "what it claims to test"
        )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))