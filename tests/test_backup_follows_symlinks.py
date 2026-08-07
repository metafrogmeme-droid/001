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


class TestTheDrillCannotPassOnAnEmptyArchive:
    """The drill gave a FALSE PASS, and the mechanism is worth spelling out.

    An archive holding only the `data` symlink extracts, into the probe dir, an
    ABSOLUTE pointer straight back at the live store. Every check then read the
    live files, compared them with themselves, and printed

        [verify]   IDENTICAL to live: data/secrets_vault.enc
        [verify] restore drill PASSED

    over 138 bytes containing no data at all. A restore test that reads the
    thing it exists to be independent of is not a test — it is the live store
    wearing the archive's name. Same defect as a card reading a stale cache and
    calling it current.
    """

    def _symlink_only_archive(self, repo, home):
        """Reproduce the pre-fix backup: no -h, so tar stores the pointer."""
        script = repo / "scripts" / "backup_data.sh"
        original = script.read_text(encoding="utf-8")
        script.write_text(
            original.replace("tar -h --ignore-failed-read", "tar --ignore-failed-read"),
            encoding="utf-8")
        _run(repo, home)                       # fails the content check, still writes
        script.write_text(original, encoding="utf-8")
        return _newest_archive(home)

    def test_the_archive_really_does_hold_a_pointer_for_the_data_tree(self, deployed):
        repo, home = deployed
        archive = self._symlink_only_archive(repo, home)
        with tarfile.open(archive) as tf:
            names = tf.getnames()
            # `data` is archived as a LINK — the whole store reduced to a
            # pointer. logs/audit_chain.jsonl survives as content because it is
            # named as an explicit file path, so tar resolves the directory
            # component on the way to it. That asymmetry is worth pinning: it
            # is exactly what made the failure look partial and plausible.
            assert "data" in names
            assert tf.getmember("data").issym(), "expected data/ stored as a link"
            assert not any(n.startswith("data/") for n in names), (
                f"no file under data/ should be present: {names}")
            assert "logs/audit_chain.jsonl" in names

    def test_the_drill_refuses_it_instead_of_passing(self, deployed):
        repo, home = deployed
        self._symlink_only_archive(repo, home)
        r = _run(repo, home, "--verify-restore")
        assert r.returncode == 1, (
            "the drill passed on an archive containing no data:\n" + r.stdout)
        assert "symlink members" in r.stdout
        assert "PASSED" not in r.stdout

    def test_it_names_the_offending_member(self, deployed):
        repo, home = deployed
        self._symlink_only_archive(repo, home)
        r = _run(repo, home, "--verify-restore")
        assert "data ->" in r.stdout, r.stdout

    def test_an_absolute_symlink_is_the_dangerous_case(self, tmp_path):
        """A RELATIVE pointer dangles in the probe and fails naturally; an
        ABSOLUTE one resolves back to live data. Only the second produces a
        confident false pass, so it is the one pinned here."""
        persist = tmp_path / "persist" / "data"
        persist.mkdir(parents=True)
        (persist / "secrets_vault.enc").write_bytes(VAULT_BYTES)
        probe = tmp_path / "probe"
        probe.mkdir()
        os.symlink(persist, probe / "data")     # absolute
        target = probe / "data" / "secrets_vault.enc"
        assert target.is_file() and target.read_bytes() == VAULT_BYTES, (
            "an absolute link reads live data from inside the probe — which is "
            "why presence alone could never be the check"
        )
        assert not str(os.path.realpath(target)).startswith(str(probe)), (
            "and realpath escaping the probe is what detects it"
        )


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


class TestNoGuardIsDefeatedBySIGPIPE:
    """Both content guards failed at scale, in opposite directions, and every
    small-fixture test in this file passed while they did.

        tar -tzf ARCHIVE | grep -q PATTERN

    `grep -q` exits on the first match. `tar` then writes into a closed pipe,
    takes SIGPIPE and exits 141, and `set -o pipefail` reports 141 for the
    whole pipeline. So a SUCCESSFUL find returns non-zero:

      * the vault check read that as "vault absent" and failed a good backup —
        while blaming the -h bug, a specific, confident, WRONG diagnosis;
      * the drill's symlink check read it as "no symlinks found" and failed
        OPEN, passing exactly the archives it exists to reject.

    It only fires once tar still has output to write after the match, i.e. on a
    store with many members. The fixtures above hold three files, so tar always
    finished first and neither bug could appear. Found on the operator's box,
    at 4000+ members — production was the first environment large enough.

    Hence PAD_COUNT: these fixtures are deliberately big. A future tidy-up that
    shrinks them silently removes the only coverage of this failure mode.
    """

    PAD_COUNT = 3000

    @pytest.fixture
    def big_deployed(self, tmp_path):
        persist = tmp_path / "persist"
        (persist / "data").mkdir(parents=True)
        (persist / "logs").mkdir(parents=True)
        (persist / "data" / "secrets_vault.enc").write_bytes(VAULT_BYTES)
        (persist / "data" / "runeclaw.db").write_bytes(b"DB")
        (persist / "logs" / "audit_chain.jsonl").write_bytes(CHAIN_BYTES)
        # Sort AFTER secrets_vault.enc so tar is still emitting when grep quits.
        for i in range(self.PAD_COUNT):
            (persist / "data" / f"zz_pad_{i}.json").write_bytes(b"{}")

        repo = tmp_path / "repo"
        (repo / "scripts").mkdir(parents=True)
        (repo / "scripts" / "backup_data.sh").write_bytes(SCRIPT.read_bytes())
        os.symlink(persist / "data", repo / "data")
        os.symlink(persist / "logs", repo / "logs")
        home = tmp_path / "home"
        home.mkdir()
        return repo, home

    def test_a_good_backup_is_not_failed_by_its_own_success(self, big_deployed):
        repo, home = big_deployed
        r = _run(repo, home)
        assert r.returncode == 0, (
            "a backup CONTAINING the vault was reported as missing it:\n"
            + r.stdout + r.stderr)
        assert "absent from" not in r.stdout

    def test_the_vault_really_is_in_that_archive(self, big_deployed):
        repo, home = big_deployed
        _run(repo, home)
        with tarfile.open(_newest_archive(home)) as tf:
            assert tf.extractfile("data/secrets_vault.enc").read() == VAULT_BYTES

    def test_the_symlink_guard_still_fires_at_scale(self, big_deployed):
        """The fail-open half. With many members the guard silently passed."""
        repo, home = big_deployed
        script = repo / "scripts" / "backup_data.sh"
        script.write_text(script.read_text(encoding="utf-8")
                          .replace("tar -h --ignore-failed-read",
                                   "tar --ignore-failed-read"),
                          encoding="utf-8")
        _run(repo, home)
        r = _run(repo, home, "--verify-restore")
        assert r.returncode == 1, (
            "the symlink guard failed OPEN on a pointer-only archive:\n" + r.stdout)
        assert "symlink members" in r.stdout
        assert "PASSED" not in r.stdout

    def test_the_drill_still_passes_on_a_good_big_archive(self, big_deployed):
        repo, home = big_deployed
        _run(repo, home)
        r = _run(repo, home, "--verify-restore")
        assert r.returncode == 0, r.stdout
        assert "restore drill PASSED" in r.stdout

    def test_no_guard_pipes_into_an_early_exiting_reader(self):
        """The shape itself, so a new check cannot reintroduce it.

        `grep -q`/`head` after a pipe is the hazard; `grep` without -q drains
        its input and is safe. Asserted on the source because the property is
        'no future guard is written this way', which no single run can show.
        """
        src = SCRIPT.read_text(encoding="utf-8")
        offenders = [ln.strip() for ln in src.splitlines()
                     if "| grep -q" in ln and not ln.strip().startswith("#")]
        assert not offenders, (
            "pipe into grep -q — SIGPIPE + pipefail inverts the result:\n  "
            + "\n  ".join(offenders))
