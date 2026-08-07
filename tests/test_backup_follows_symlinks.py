"""A backup that archived a symlink and exited 0.

On a deployed box `deploy.sh` makes data/ and logs/ symlinks into
~/runeclaw-persist/. `tar -czf archive data/` then archives the SYMLINK — one
138-byte pointer, no data — and exits 0. Observed on the operator's box
2026-08-07, the same day its only recovery mechanism was needed and found not
to exist.

The content check added alongside caught it (`data` was listed, not
`data/secrets_vault.enc`) and failed the run rather than reporting a success
that held nothing. But refusing is not backing up. `-h` is the fix, and it is
load-bearing on every deployed box — which is exactly the environment no
CI run ever reproduces.

The audit chain rides along now too. It lives under logs/, is hash-chained
from genesis and does not rotate, and once data/closed_trades.json was deleted
it became the only surviving record of 119 closes. A backup that omits the one
file capable of reconstructing the others is not a backup of the state that
matters.

Exercised rather than grepped: a symlinked tree is built, the real script is
run against it, and the archive is opened to see what is actually inside.
"""
from __future__ import annotations

import os
import subprocess
import tarfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "backup_data.sh"

VAULT_BYTES = b"VAULT-CONTENT-NOT-A-SYMLINK"
CHAIN_BYTES = b'{"sequence":0,"event_type":"GENESIS"}\n'


@pytest.fixture
def deployed(tmp_path):
    """A box-shaped tree: data/ and logs/ symlinked into a sibling store."""
    persist = tmp_path / "persist"
    (persist / "data").mkdir(parents=True)
    (persist / "logs").mkdir(parents=True)
    (persist / "data" / "secrets_vault.enc").write_bytes(VAULT_BYTES)
    (persist / "data" / "runeclaw.db").write_bytes(b"DB")
    (persist / "logs" / "audit_chain.jsonl").write_bytes(CHAIN_BYTES)

    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "backup_data.sh").write_bytes(SCRIPT.read_bytes())
    os.symlink(persist / "data", repo / "data")
    os.symlink(persist / "logs", repo / "logs")

    home = tmp_path / "home"
    home.mkdir()
    return repo, home


def _run(repo, home, *args):
    return subprocess.run(
        ["bash", "scripts/backup_data.sh", *args],
        cwd=repo, capture_output=True, text=True,
        env={**os.environ, "HOME": str(home), "PATH": os.environ.get("PATH", "")})


def _newest_archive(home):
    archives = sorted((home / "runeclaw-backups").glob("*.tar.gz"))
    assert archives, "no archive written"
    return archives[-1]


class TestItArchivesThroughTheSymlink:
    def test_the_backup_succeeds(self, deployed):
        repo, home = deployed
        r = _run(repo, home)
        assert r.returncode == 0, r.stdout + r.stderr

    def test_the_vault_CONTENT_is_in_the_archive_not_a_pointer(self, deployed):
        repo, home = deployed
        _run(repo, home)
        with tarfile.open(_newest_archive(home)) as tf:
            member = tf.getmember("data/secrets_vault.enc")
            assert member.isfile(), "archived as a link, not a file"
            assert tf.extractfile(member).read() == VAULT_BYTES

    def test_the_audit_chain_rides_along(self, deployed):
        repo, home = deployed
        _run(repo, home)
        with tarfile.open(_newest_archive(home)) as tf:
            assert tf.extractfile("logs/audit_chain.jsonl").read() == CHAIN_BYTES

    def test_nothing_is_stored_as_a_bare_symlink(self, deployed):
        repo, home = deployed
        _run(repo, home)
        with tarfile.open(_newest_archive(home)) as tf:
            links = [m.name for m in tf.getmembers() if m.issym() or m.islnk()]
        assert links == [], f"archived as links: {links}"


class TestItRefusesRatherThanReportingAnEmptySuccess:
    """The guard that caught this on the box. Without -h the archive holds one
    pointer and tar exits 0 — so 'tar succeeded' cannot be the success test."""

    def test_an_archive_missing_the_vault_fails_the_run(self, deployed, tmp_path):
        repo, home = deployed
        # Reproduce the pre-fix behaviour exactly: drop -h back out.
        script = (repo / "scripts" / "backup_data.sh")
        script.write_text(script.read_text(encoding="utf-8")
                          .replace("tar -h --ignore-failed-read", "tar --ignore-failed-read"),
                          encoding="utf-8")
        r = _run(repo, home)
        assert r.returncode == 1
        assert "absent from" in r.stdout, r.stdout
        assert "symlinked data/ archived without -h" in r.stdout


class TestTheRestoreDrill:
    """Handoff item #2. A backup that has never been restored is a hypothesis."""

    def test_it_reports_the_absence_of_any_archive_as_the_finding(self, deployed):
        repo, home = deployed
        r = _run(repo, home, "--verify-restore")
        assert r.returncode == 1
        assert "nothing to restore FROM" in r.stdout
        assert "the finding, not an error" in r.stdout

    def test_it_passes_after_a_backup_and_compares_bytes(self, deployed):
        repo, home = deployed
        _run(repo, home)
        r = _run(repo, home, "--verify-restore")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "restore drill PASSED" in r.stdout
        assert "IDENTICAL to live: data/secrets_vault.enc" in r.stdout

    def test_it_fails_when_the_restored_vault_is_empty(self, deployed):
        """Presence is not integrity — a truncated vault restores 'fine' until
        the day it is needed."""
        repo, home = deployed
        _run(repo, home)
        (repo.parent / "persist" / "data" / "secrets_vault.enc").write_bytes(b"")
        _run(repo, home)                      # second backup, now empty vault
        r = _run(repo, home, "--verify-restore")
        assert r.returncode == 1
        assert "MISSING data/secrets_vault.enc" in r.stdout


class TestTheArchiveLivesOutsideTheRepo:
    def test_no_backups_directory_is_created_in_the_working_tree(self, deployed):
        repo, home = deployed
        _run(repo, home)
        assert not (repo / "backups").exists(), (
            "an archive inside the repo is one `git clean -fdx` from gone, on a "
            "box whose deploy path runs `git reset --hard`"
        )

    def test_the_archive_is_not_world_readable(self, deployed):
        repo, home = deployed
        _run(repo, home)
        mode = os.stat(_newest_archive(home)).st_mode & 0o777
        assert mode == 0o600, oct(mode)
