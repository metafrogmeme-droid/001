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


class TestTheDrillVerifiesTheStateItExistsToProtect:
    """The drill checked three files by name, and they were the wrong three.

    `data/secrets_vault.enc`, `data/runeclaw.db`, `logs/audit_chain.jsonl` are
    INFRASTRUCTURE. This script's own header names something else as the point:
    "open/closed positions, risk state, the learning store ... The learning
    loop's value COMPOUNDS in these files." An archive that restored all three
    infrastructure files and lost every trade, every per-user risk engine and
    the whole learning/ tree printed `restore drill PASSED`.

    Same shape as the conftest cleanup allowlist found one commit earlier: a
    hand-kept list of important files, correct when it was written, silently
    outgrown by every feature that added a file after it. The fix is the same —
    stop enumerating. The inventory now comes from the live tree, so a file a
    future feature adds is covered the day it is written.

    Note every assertion here is on a POSITIVE rendering or an anchored verdict
    line. `"PASSED" not in stdout` is the loose form this repo keeps getting
    burned by; `"restore drill PASSED"` is the line that actually decides.
    """

    def _backdated(self, path: Path, archive: Path, content: bytes = b"{}") -> None:
        """A file that EXISTED before the backup but is not in the archive.

        Written after the fact and stamped older, because that is precisely what
        `tar --ignore-failed-read` produces when it cannot read a file: the file
        is on disk, predates the run, and is missing from the tarball. Creating
        it normally would postdate the archive, which is the legitimate case.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        older = os.stat(archive).st_mtime - 60
        os.utime(path, (older, older))

    def test_an_archive_that_lost_the_trading_state_is_not_a_pass(self, deployed):
        repo, home = deployed
        _run(repo, home)
        archive = _newest_archive(home)
        data = repo.parent / "persist" / "data"
        for name in ("closed_trades.json", "live_positions.json"):
            self._backdated(data / name, archive)

        r = _run(repo, home, "--verify-restore")
        assert r.returncode == 1, (
            "the drill passed over an archive missing the state the script's "
            "own header calls the point:\n" + r.stdout)
        assert "restore drill FAILED" in r.stdout
        assert "data/closed_trades.json" in r.stdout
        assert "data/live_positions.json" in r.stdout

    def test_the_inventory_is_derived_from_the_live_tree_not_a_list(self, deployed):
        """The property, not the instance.

        `risk_state_zeta.json` is on no list anywhere in this repo — that is the
        point. A per-user risk engine for a user who does not exist yet is the
        file class that defeated the conftest allowlist, so it is the file class
        the drill has to notice without being told.
        """
        repo, home = deployed
        _run(repo, home)
        archive = _newest_archive(home)
        data = repo.parent / "persist" / "data"
        self._backdated(data / "risk_state_zeta.json", archive)
        self._backdated(data / "learning" / "voter_weights.json", archive)

        r = _run(repo, home, "--verify-restore")
        assert r.returncode == 1, r.stdout
        assert "data/risk_state_zeta.json" in r.stdout
        assert "data/learning/voter_weights.json" in r.stdout, (
            "a whole subtree can go missing; the inventory must walk it"
        )

    def test_a_file_created_after_the_backup_is_not_held_against_it(self, deployed):
        """The half that decides whether anyone leaves this gate switched on.

        A file written since the last backup is legitimately absent from it. A
        drill that fails on that is wrong every time the bot is running, and a
        gate that cries wolf gets disabled — deploy.sh carries the same note
        about its interpreter check, for the same reason.
        """
        repo, home = deployed
        _run(repo, home)
        data = repo.parent / "persist" / "data"
        (data / "risk_state_zeta.json").write_bytes(b'{"after": true}')

        r = _run(repo, home, "--verify-restore")
        assert r.returncode == 0, (
            "a file created after the backup was reported as a backup "
            "defect:\n" + r.stdout)
        assert "restore drill PASSED" in r.stdout
        assert "risk_state_zeta.json" not in r.stdout

    def test_the_coverage_it_actually_achieved_is_reported(self, deployed):
        """`PASSED` alone cannot distinguish three files from three thousand."""
        repo, home = deployed
        _run(repo, home)
        r = _run(repo, home, "--verify-restore")
        assert r.returncode == 0, r.stdout
        assert "coverage: all 3 file(s) predating the archive are in it" in r.stdout, (
            "the fixture holds vault + db + chain; the count has to say so\n"
            + r.stdout)


class TestADrillThatCheckedNothingDoesNotSayPassed:
    """`restore drill PASSED`, exit 0, zero bytes verified.

    With none of its three hardcoded files present, every iteration of the check
    loop hit `continue`, rc stayed 0, and the verdict printed with not one line
    of output above it. "Nothing was checked" and "everything checked out" are
    different findings and this printed the second for the first — the same trap
    as `integrity_veto.assess({})` answering `clear` over checked == 0, and the
    same one as a section heading with nothing under it.

    It matters here more than most places: this drill is the ONLY mechanism that
    claims the backups work, on a box that was found on 2026-08-07 with its
    entire runtime state deleted and no archive on any path.
    """

    @pytest.fixture
    def bare(self, tmp_path):
        """A box with a data/ store that holds none of the three named files."""
        persist = tmp_path / "persist"
        (persist / "data").mkdir(parents=True)
        repo = tmp_path / "repo"
        (repo / "scripts").mkdir(parents=True)
        (repo / "scripts" / "backup_data.sh").write_bytes(SCRIPT.read_bytes())
        os.symlink(persist / "data", repo / "data")
        home = tmp_path / "home"
        home.mkdir()
        return repo, home

    def test_it_reports_inconclusive_rather_than_a_pass(self, bare):
        repo, home = bare
        _run(repo, home)
        r = _run(repo, home, "--verify-restore")
        assert "restore drill PASSED" not in r.stdout, (
            "the drill certified an archive it never opened a file from:\n"
            + r.stdout)
        assert r.returncode == 2, (
            f"expected the third outcome, got {r.returncode}:\n" + r.stdout)
        assert "INCONCLUSIVE" in r.stdout

    def test_it_says_plainly_that_it_verified_nothing(self, bare):
        """An operator reading this is deciding whether they have a recovery
        plan. The exit code is for scripts; the sentence is for them."""
        repo, home = bare
        _run(repo, home)
        r = _run(repo, home, "--verify-restore")
        assert "verified no bytes at all" in r.stdout, r.stdout

    def test_inconclusive_is_distinct_from_failed(self, bare):
        """Three outcomes, not two. A drill that cannot check anything has not
        found a broken backup, and reporting it as one would train the operator
        to ignore the exit code that means a backup really is broken."""
        repo, home = bare
        no_archive = _run(repo, home, "--verify-restore")   # a real failure
        _run(repo, home)                                    # now there is one
        inconclusive = _run(repo, home, "--verify-restore")

        assert no_archive.returncode == 1, no_archive.stdout
        assert "nothing to restore FROM" in no_archive.stdout
        assert inconclusive.returncode == 2, inconclusive.stdout
        assert no_archive.returncode != inconclusive.returncode


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
        offenders = []
        for line in src.splitlines():
            stripped = line.strip()
            if "| grep -q" not in stripped or stripped.startswith("#"):
                continue
            # Two safe forms, and the distinction is the whole point:
            #   (set +o pipefail; tar ... | grep -q ...) && found=1 || true
            # disables the propagation for that pipeline only — af3211f's
            # approach, correct and surgical. An UNGUARDED pipe is the defect.
            if "set +o pipefail" in stripped:
                continue
            offenders.append(stripped)
        assert not offenders, (
            "unguarded pipe into grep -q — SIGPIPE + pipefail inverts the "
            "result. Either wrap it in `(set +o pipefail; ...)` or capture the "
            "output first:\n  " + "\n  ".join(offenders))

    def test_the_scan_still_rejects_the_bare_form(self):
        """An exemption that swallows the defect is worse than no scan. Prove
        the unguarded shape is still caught now that a guarded one is allowed."""
        guarded = '(set +o pipefail; tar -tzf "$A" | grep -q X) && f=1 || true'
        bare = 'if tar -tvzf "$ARCHIVE" | grep -q \'^l\'; then'
        def flagged(line):
            st = line.strip()
            return ("| grep -q" in st and not st.startswith("#")
                    and "set +o pipefail" not in st)
        assert not flagged(guarded), "the guarded form must be accepted"
        assert flagged(bare), "the bare form must still be rejected"
