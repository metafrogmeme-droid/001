"""deploy.sh must never destroy live state.

deploy.sh's header promises "Idempotent and non-destructive". Twice it wasn't.

**2026-08-02 — data/.** link_persistent migrates the repo copy into the store
only while the store path does not yet exist:

    [ -e "$repo_path" ] && [ ! -e "$store_path" ]   -> mv

A `mkdir -p "$PERSIST_DIR/data"` above the call made that permanently false, so
the "store is authoritative" branch fired instead and rm -rf'd secrets_vault.enc,
runeclaw.db, live_positions.json and shadow_book.json. `.env` was unaffected —
nothing pre-creates its store path — and that asymmetry is what hid it: the
script printed success for both paths and only one was telling the truth.

**2026-08-06 — logs/.** Those files were protected only by accident: they were
tracked in git, so a re-clone restored them. Untracking them was correct —
runtime state does not belong in the redeploy path — but it removed the accident
without replacing it, leaving logs/ with no protection at all. deploy.sh now
persists it the way it persists data/.

Three layers, all covered here:
  1. ordering        — link before the mkdir, so migration is reachable
  2. empty-store     — an empty store beside populated data is migrated, never
                       discarded; this alone would have contained (1)
  3. logs/           — the forensic record and the tamper-evident audit chain

These run the real script against a throwaway repo, so they fail on a broken
script rather than on a description of one.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SH = REPO_ROOT / "deploy.sh"

# The files the 2026-08-02 incident actually destroyed. Named explicitly so a
# regression reads as "the vault is gone", not "a file count changed".
LIVE_STATE = {
    "secrets_vault.enc": b"\x00encrypted-operator-keys",
    "runeclaw.db": b"SQLite format 3\x00",
    "live_positions.json": b'[{"symbol": "ETC/USDT", "status": "pending_fill"}]',
    "shadow_book.json": b'{"open": []}',
    "closed_trades.json": b'[{"symbol": "NEAR/USDT:USDT", "pnl_usd": 0.47}]',
}

# logs/ carries the forensic record AND a tamper-evident chain. Losing the chain
# does not merely lose history — it breaks continuity, which is unrecoverable
# and indistinguishable from tampering.
LOG_STATE = {
    "audit_chain.jsonl": b'{"seq":1,"prev":null,"hash":"a1"}\n{"seq":2,"prev":"a1","hash":"b2"}\n',
    "trade.jsonl": b'{"action":"live_execute","symbol":"ETC/USDT"}\n',
    "live_trading_log.csv": b"ts,symbol,pnl\n2026-06-17,BTC,1.5\n",
}


def _make_repo(tmp_path: Path, *, with_data: bool = True, with_env: bool = True,
               with_logs: bool = True) -> Path:
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
    if with_logs:
        logs = repo / "logs"
        logs.mkdir()
        for name, blob in LOG_STATE.items():
            (logs / name).write_bytes(blob)
    if with_env:
        (repo / ".env").write_text(
            "BITGET_API_KEY=fixture-not-a-real-key\nJWT_SECRET=fixture-not-a-real-secret\n")
    return repo


def _run(repo: Path, persist: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "PERSIST_DIR": str(persist)}
    return subprocess.run(["bash", "./deploy.sh"], cwd=repo, env=env,
                          capture_output=True, text=True, timeout=60)


def _assert_data_intact(repo: Path, persist: Path) -> None:
    assert (repo / "data").is_symlink(), "data/ should be a symlink into the store"
    assert (repo / "data").resolve() == persist.resolve() / "data"
    for name, blob in LIVE_STATE.items():
        in_store = persist / "data" / name
        assert in_store.exists(), f"{name} was destroyed — not in the store"
        assert in_store.read_bytes() == blob, f"{name} was corrupted"
        assert (repo / "data" / name).read_bytes() == blob, f"{name} unreachable via symlink"


def _assert_logs_intact(repo: Path, persist: Path) -> None:
    assert (repo / "logs").is_symlink(), "logs/ is not linked into the store"
    for name, blob in LOG_STATE.items():
        assert (persist / "logs" / name).read_bytes() == blob, f"{name} lost"


class TestFirstRunMigratesLiveState:
    """The incident case: real state in the repo, nothing in the store yet."""

    def test_data_directory_survives(self, tmp_path):
        repo = _make_repo(tmp_path); persist = tmp_path / "persist"
        r = _run(repo, persist)
        assert r.returncode == 0, r.stderr
        _assert_data_intact(repo, persist)

    def test_secrets_vault_specifically_survives(self, tmp_path):
        # Losing this costs the operator their exchange keys, and re-entering
        # them by hand is what produced the quoted-key 40006 auth failure the
        # script was written to prevent.
        repo = _make_repo(tmp_path); persist = tmp_path / "persist"
        _run(repo, persist)
        vault = persist / "data" / "secrets_vault.enc"
        assert vault.exists(), "secrets_vault.enc destroyed on first deploy"
        assert vault.read_bytes() == LIVE_STATE["secrets_vault.enc"]

    def test_closed_trades_survive(self, tmp_path):
        # The realized-PnL record. Its absence is what made the UI report
        # "$+0.00 all-time" — a zero asserted where the truth was "no data".
        repo = _make_repo(tmp_path); persist = tmp_path / "persist"
        _run(repo, persist)
        assert (persist / "data" / "closed_trades.json").exists()

    def test_tracked_files_move_too(self, tmp_path):
        repo = _make_repo(tmp_path); persist = tmp_path / "persist"
        _run(repo, persist)
        assert (persist / "data" / "benchmark.csv").exists()

    def test_env_survives(self, tmp_path):
        repo = _make_repo(tmp_path); persist = tmp_path / "persist"
        _run(repo, persist)
        assert (repo / ".env").is_symlink()
        assert (persist / ".env").read_text().startswith("BITGET_API_KEY=fixture-not-a-real-key")

    def test_reports_migration_not_discard(self, tmp_path):
        # The output must match what happened. The bug's whole shape was a
        # success message printed over a deletion.
        repo = _make_repo(tmp_path)
        r = _run(repo, tmp_path / "persist")
        assert "discarded" not in r.stdout, f"claimed a discard on first run:\n{r.stdout}"


class TestRedeployOverPopulatedStore:
    def test_store_wins_and_live_state_is_kept(self, tmp_path):
        repo = _make_repo(tmp_path); persist = tmp_path / "persist"
        _run(repo, persist)
        # Simulate the re-clone: symlinks replaced by committed copies.
        for name in ("data", ".env", "logs"):
            (repo / name).unlink()
        (repo / "data").mkdir()
        (repo / "data" / "benchmark.csv").write_text("date,close\n2026-01-01,100\n")
        (repo / "logs").mkdir()
        (repo / ".env").write_text("BITGET_API_KEY=placeholder\n")

        r = _run(repo, persist)
        assert r.returncode == 0, r.stderr
        _assert_data_intact(repo, persist)
        _assert_logs_intact(repo, persist)
        assert "fixture-not-a-real-key" in (persist / ".env").read_text()

    def test_repeated_deploys_never_erode_state(self, tmp_path):
        repo = _make_repo(tmp_path); persist = tmp_path / "persist"
        for _ in range(3):
            r = _run(repo, persist)
            assert r.returncode == 0, r.stderr
            _assert_data_intact(repo, persist)
            _assert_logs_intact(repo, persist)


class TestEmptyStoreIsNeverAuthoritative:
    """An empty store beside populated data is a bug signature, not a clone.

    This is the layer that would have contained the 2026-08-02 incident with
    the ordering still wrong.
    """

    def test_populated_repo_wins_over_empty_store(self, tmp_path):
        repo = _make_repo(tmp_path); persist = tmp_path / "persist"
        (persist / "data").mkdir(parents=True)
        r = _run(repo, persist)
        assert r.returncode == 0, r.stderr
        _assert_data_intact(repo, persist)

    def test_empty_logs_store_does_not_eat_the_audit_chain(self, tmp_path):
        repo = _make_repo(tmp_path); persist = tmp_path / "persist"
        (persist / "logs").mkdir(parents=True)
        _run(repo, persist)
        _assert_logs_intact(repo, persist)

    def test_dotfiles_migrate_too(self, tmp_path):
        # `mv src/* dst/` silently skips dotfiles; data/ carries them.
        repo = _make_repo(tmp_path)
        (repo / "data" / ".cursor").write_text("1738000000")
        persist = tmp_path / "persist"
        (persist / "data").mkdir(parents=True)
        _run(repo, persist)
        assert (persist / "data" / ".cursor").read_text() == "1738000000"


class TestLogsSurviveToo:
    """logs/ gets the same treatment as data/, for the same reasons."""

    def test_logs_migrate_on_first_run(self, tmp_path):
        repo = _make_repo(tmp_path); persist = tmp_path / "persist"
        r = _run(repo, persist)
        assert r.returncode == 0, r.stderr
        _assert_logs_intact(repo, persist)

    def test_audit_chain_specifically_survives(self, tmp_path):
        repo = _make_repo(tmp_path); persist = tmp_path / "persist"
        _run(repo, persist)
        chain = persist / "logs" / "audit_chain.jsonl"
        assert chain.exists(), "the tamper-evident audit chain was destroyed"
        assert chain.read_bytes() == LOG_STATE["audit_chain.jsonl"]

    def test_logs_symlink_resolves_on_a_bare_box(self, tmp_path):
        repo = _make_repo(tmp_path, with_data=False, with_env=False, with_logs=False)
        persist = tmp_path / "persist"
        r = _run(repo, persist)
        assert r.returncode == 0, r.stderr
        assert (repo / "logs").is_dir(), "logs/ symlink dangles — the bot cannot write"


class TestBareBox:
    def test_data_symlink_resolves_to_a_directory(self, tmp_path):
        repo = _make_repo(tmp_path, with_data=False, with_env=False, with_logs=False)
        persist = tmp_path / "persist"
        r = _run(repo, persist)
        assert r.returncode == 0, r.stderr
        assert (repo / "data").is_dir(), "data/ symlink dangles"

    def test_missing_env_is_reported_not_invented(self, tmp_path):
        repo = _make_repo(tmp_path, with_data=False, with_env=False, with_logs=False)
        r = _run(repo, tmp_path / "persist")
        assert "No .env in the persistent store yet" in r.stdout


class TestAlreadyLinked:
    def test_existing_symlink_is_repointed_not_followed(self, tmp_path):
        repo = _make_repo(tmp_path); persist = tmp_path / "persist"
        _run(repo, persist)
        elsewhere = tmp_path / "elsewhere"; elsewhere.mkdir()
        (repo / "data").unlink(); (repo / "data").symlink_to(elsewhere)
        r = _run(repo, persist)
        assert r.returncode == 0, r.stderr
        _assert_data_intact(repo, persist)
        assert elsewhere.is_dir(), "a stray directory was deleted on the way past"


class TestTheGuardsWouldCatchTheRealBugs:
    """Mutations — break each layer on purpose, prove a guard notices.

    Without these the suite proves only that today's script is correct, not
    that it would have caught what actually happened.
    """

    def test_MUTATION_hoisting_the_mkdir_destroys_state(self, tmp_path):
        src = DEPLOY_SH.read_text()
        broken = src.replace('link_persistent "data"\nmkdir -p "$PERSIST_DIR/data"',
                             'mkdir -p "$PERSIST_DIR/data"\nlink_persistent "data"', 1)
        assert broken != src, (
            "could not reintroduce the ordering bug — the block was reworded; "
            "update this mutation before trusting the suite")
        # Also disable the empty-store guard, so this reproduces the original
        # single-layer script rather than being rescued by the later defence.
        broken = broken.replace('[ -z "$(ls -A "$store_path" 2>/dev/null)" ]', "false", 1)
        repo = _make_repo(tmp_path)
        (repo / "deploy.sh").write_text(broken)
        persist = tmp_path / "persist"
        _run(repo, persist)
        assert not (persist / "data" / "secrets_vault.enc").exists(), (
            "the mutation did not destroy state — this suite is not testing "
            "what it claims to test")

    def test_MUTATION_dropping_logs_persistence_loses_the_chain(self, tmp_path):
        src = DEPLOY_SH.read_text()
        broken = src.replace('link_persistent "logs"', '# link_persistent "logs"', 1)
        assert broken != src, "could not remove logs persistence — reword check"
        repo = _make_repo(tmp_path)
        (repo / "deploy.sh").write_text(broken)
        persist = tmp_path / "persist"
        _run(repo, persist)
        assert not (persist / "logs" / "audit_chain.jsonl").exists(), (
            "logs/ survived without deploy.sh persisting it — the fixture or "
            "the mutation is wrong")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
