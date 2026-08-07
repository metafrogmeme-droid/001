"""The test suite deleted a production store, and its own comment said it couldn't.

`tests/conftest.py` cleans runtime state before and after EVERY test:

    data/live_positions.json   data/closed_trades.json   data/combined_state.json
    data/risk_state.json       data/users.json           data/portfolio_*.json
    shutil.rmtree("data/learning")

justified by a comment reading "the data/ files are all gitignored runtime
artifacts (never committed fixtures), so it is safe to remove them between
tests." That is true on CI, where data/ is a scratch directory. On a deployed
box `deploy.sh` makes data/ a SYMLINK into ~/runeclaw-persist/, and every one
of those paths resolves to the live store. The reasoning is sound and the
environment invalidates it — the same shape as `!data/benchmark/` re-including
a path git could not reach through that identical symlink.

On 2026-08-07 the operator's box was found with exactly this delete-list
missing — closed_trades.json, live_positions.json, risk_state.json, users.json
— every file NOT on the list intact, and data/learning/ present but EMPTY,
which is what rmtree leaves once the store recreates the directory. 119 closes
existed in the audit chain with nothing on disk to match them, and no backup
existed on any path.

The absences had all been explained away in passing: "no trades closed since
last restart", "transient", "no active trades at the moment". Each plausible.
Together they nearly closed an open incident. Unreadable is never zero, and
absent is never a measurement — including when the thing doing the explaining
is a person rather than a card.

So the fixture now asks what state_guard.py asks at startup: am I somewhere it
is safe to write? A vault or a symlink means no, and the run aborts at
configure time rather than per-test — by the time a fixture runs, half the
damage is already scheduled.
"""
from __future__ import annotations

import os

import pytest

from tests.conftest import (_LIVE_MARKERS, _OVERRIDE_ENV, _live_store_reasons,
                            pytest_configure)


def _repo_shaped(tmp_path, monkeypatch):
    """A tmp dir standing in for the repo root, with tests/ beside it so the
    guard's own __file__-relative anchor resolves there."""
    (tmp_path / "tests").mkdir()
    monkeypatch.setattr(
        "tests.conftest.__file__", str(tmp_path / "tests" / "conftest.py"))
    monkeypatch.chdir(tmp_path)
    return tmp_path


class TestItDetectsAStoreOutsideTheWorkingTree:
    """The discriminator is the PATH, not the presence of a vault.

    The first cut of this guard refused whenever data/secrets_vault.enc or
    data/runeclaw.db existed — and immediately refused to run in a clean
    checkout, because the suite CREATES those files: ~6000 tests build
    LiveExecutor / SecretsVault at their default paths. A guard that trips on
    its own side effects gets routinely overridden, and a routinely-overridden
    guard protects nothing. It is the same lesson as a caveat printed on every
    healthy card.
    """

    def test_a_scratch_data_dir_is_fine(self, tmp_path, monkeypatch):
        root = _repo_shaped(tmp_path, monkeypatch)
        (root / "data").mkdir()
        assert _live_store_reasons() == []

    def test_no_data_dir_at_all_is_fine(self, tmp_path, monkeypatch):
        _repo_shaped(tmp_path, monkeypatch)
        assert _live_store_reasons() == []

    @pytest.mark.parametrize("marker", _LIVE_MARKERS)
    def test_a_suite_created_marker_in_a_LOCAL_data_dir_is_fine(
            self, marker, tmp_path, monkeypatch):
        """The false positive that killed version one. These files are the
        suite's own output when data/ is local; they must not block a re-run."""
        root = _repo_shaped(tmp_path, monkeypatch)
        path = root / marker
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
        assert _live_store_reasons() == []

    def test_a_symlinked_data_dir_is_refused(self, tmp_path, monkeypatch):
        root = _repo_shaped(tmp_path, monkeypatch)
        real = root / "persist" / "data"
        real.mkdir(parents=True)
        os.symlink(real, root / "data")
        reasons = _live_store_reasons()
        assert any("outside the working tree" in r for r in reasons), reasons
        assert any("symlink" in r for r in reasons), reasons

    def test_it_names_the_live_files_it_would_have_deleted(self, tmp_path,
                                                           monkeypatch):
        root = _repo_shaped(tmp_path, monkeypatch)
        real = root / "persist" / "data"
        real.mkdir(parents=True)
        (real / "secrets_vault.enc").write_text("x", encoding="utf-8")
        os.symlink(real, root / "data")
        assert any("secrets_vault.enc" in r for r in _live_store_reasons())


class TestTheRunActuallyAborts:
    """A detector nothing acts on answers nothing."""

    def _deployed(self, tmp_path, monkeypatch):
        root = _repo_shaped(tmp_path, monkeypatch)
        real = root / "persist" / "data"
        real.mkdir(parents=True)
        (real / "secrets_vault.enc").write_text("x", encoding="utf-8")
        os.symlink(real, root / "data")
        return root

    def test_configure_raises_on_a_live_store(self, tmp_path, monkeypatch):
        self._deployed(tmp_path, monkeypatch)
        monkeypatch.delenv(_OVERRIDE_ENV, raising=False)
        with pytest.raises(pytest.UsageError) as exc:
            pytest_configure(None)
        assert "refusing to run" in str(exc.value)
        assert "secrets_vault.enc" in str(exc.value), "must name what it saw"

    def test_configure_is_silent_on_a_clean_tree(self, tmp_path, monkeypatch):
        root = _repo_shaped(tmp_path, monkeypatch)
        (root / "data").mkdir()
        pytest_configure(None)          # must not raise

    def test_the_override_works_and_says_what_it_skipped(self, tmp_path,
                                                         monkeypatch, capsys):
        # Mirrors RUNECLAW_SKIP_STATE_GUARD=1: an escape hatch that prints what
        # it is overriding, so a bypass can never be silent.
        self._deployed(tmp_path, monkeypatch)
        monkeypatch.setenv(_OVERRIDE_ENV, "1")
        pytest_configure(None)          # must not raise
        out = capsys.readouterr().out
        assert _OVERRIDE_ENV in out and "WILL be deleted" in out

    def test_the_override_must_be_exactly_1(self, tmp_path, monkeypatch):
        # "0", "false", "" must not disable a guard this consequential.
        self._deployed(tmp_path, monkeypatch)
        for value in ("0", "false", "", "yes"):
            monkeypatch.setenv(_OVERRIDE_ENV, value)
            with pytest.raises(pytest.UsageError):
                pytest_configure(None)


class TestTheGuardCoversWhatTheFixtureDeletes:
    """If the delete-list grows, the guard must still be the thing standing in
    front of it — this pins that the destructive fixture and the guard have not
    drifted apart."""

    def test_the_fixture_still_deletes_the_files_this_guard_protects(self):
        from tests.conftest import _STATE_DIRS, _STATE_FILES
        assert "data/closed_trades.json" in _STATE_FILES
        assert "data/live_positions.json" in _STATE_FILES
        assert "data/learning" in _STATE_DIRS

    def test_the_guard_runs_before_any_test_can(self):
        # pytest_configure, not an autouse fixture: by the time a fixture runs
        # the run has already started and a per-test skip leaves it half
        # destructive. Asserted structurally because there is no way to
        # observe "ran before collection" from inside a test.
        from tests import conftest as c
        assert hasattr(c, "pytest_configure")
        import inspect
        assert "pytest_configure" not in inspect.getsource(c._clean_runtime_state)
