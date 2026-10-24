"""An xdist worker does not share the checkout's state files.

The serial gate never sets ``PYTEST_XDIST_WORKER``. These tests drive both
arms: a process that is not a worker keeps the paths the rest of the suite
pins, and a worker is pointed at a root of its own before collection.
"""

from __future__ import annotations

import os
import shutil

import bot.utils.paths as paths
import tests.conftest as conftest


def test_a_process_that_is_not_a_worker_keeps_the_shared_paths(monkeypatch):
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    monkeypatch.delenv("RUNECLAW_TEST_STATE_ROOT", raising=False)
    monkeypatch.delenv("DB_PATH", raising=False)
    before = paths.state_path
    conftest._point_a_worker_at_its_own_state()
    conftest._install_xdist_worker_state_root()
    assert "RUNECLAW_TEST_STATE_ROOT" not in os.environ
    assert "DB_PATH" not in os.environ
    assert paths.state_path is before
    assert conftest._runtime_state_path("data/live_positions.json") == (
        "data/live_positions.json")


def test_a_worker_is_pointed_at_a_root_inside_the_checkout(monkeypatch):
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
    monkeypatch.delenv("RUNECLAW_TEST_STATE_ROOT", raising=False)
    monkeypatch.delenv("DB_PATH", raising=False)
    root = None
    try:
        conftest._point_a_worker_at_its_own_state()
        root = os.environ["RUNECLAW_TEST_STATE_ROOT"]
        db = os.environ["DB_PATH"]
        assert root.endswith("/data/xdist/gw0")
        assert db == root + "/data/runeclaw.db"
        assert os.path.isdir(root + "/data")
        assert db.startswith(str(paths.REPO_ROOT))
        # A second call must not move a root collection has already bound.
        conftest._point_a_worker_at_its_own_state()
        assert os.environ["RUNECLAW_TEST_STATE_ROOT"] == root
        assert os.environ["DB_PATH"] == db
    finally:
        os.environ.pop("RUNECLAW_TEST_STATE_ROOT", None)
        os.environ.pop("DB_PATH", None)
        if root:
            shutil.rmtree(root, ignore_errors=True)


def test_the_worker_root_redirects_state_and_not_repo_files(monkeypatch, tmp_path):
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw1")
    monkeypatch.setenv("RUNECLAW_TEST_STATE_ROOT", str(tmp_path))
    original = paths.state_path
    original_env = paths.env_state_path
    try:
        conftest._install_xdist_worker_state_root()
        assert paths.state_path("data/x.json") == tmp_path / "data" / "x.json"
        assert paths.state_path(str(tmp_path / "abs.json")) == tmp_path / "abs.json"
        monkeypatch.setenv("SOME_PATH", "data/custom.db")
        assert paths.env_state_path("SOME_PATH", "data/default.db") == (
            tmp_path / "data" / "custom.db")
        assert paths.repo_path("bot/utils/paths.py") == (
            paths.REPO_ROOT / "bot" / "utils" / "paths.py")
        assert conftest._runtime_state_path("data/live_positions.json") == str(
            tmp_path / "data" / "live_positions.json")
    finally:
        paths.state_path = original
        paths.env_state_path = original_env
