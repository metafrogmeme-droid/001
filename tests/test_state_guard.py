"""The startup guard must refuse a launch that would write state into the void.

Every state path in the bot is relative to the process CWD (live_executor's
"data", logger's Path("logs"), engine's "logs/audit_chain.jsonl"). They are
correct today only because the bot is launched from the repo root. This guard
turns "correct by coincidence" into "checked before anything writes".

The cases below are the ones that actually happen: a launcher that forgot to
cd, a deploy.sh that never ran, a half-applied deploy where one link exists and
the other does not, and a symlink pointing somewhere other than the store.
"""

from pathlib import Path

import pytest

from bot.utils import state_guard as sg


def _repo(tmp_path: Path, *, linked=("data", "logs"), real=(), store=True):
    """A repo root, plus a persistent store, wired the way deploy.sh wires them."""
    repo = tmp_path / "runeclaw"
    repo.mkdir()
    (repo / "bot").mkdir()
    (repo / "deploy.sh").write_text("#!/usr/bin/env bash\n")
    persist = tmp_path / "runeclaw-persist"
    if store:
        persist.mkdir()
        for name in linked:
            (persist / name).mkdir()
            (repo / name).symlink_to(persist / name)
    for name in real:
        (repo / name).mkdir()
    return repo, persist


def _env(persist: Path) -> dict:
    return {"PERSIST_DIR": str(persist), "HOME": str(persist.parent)}


class TestHealthyLayout:
    def test_correctly_deployed_repo_passes(self, tmp_path):
        repo, persist = _repo(tmp_path)
        assert sg.check_state_paths(cwd=repo, env=_env(persist)) == []
        sg.assert_state_paths_are_safe(cwd=repo, env=_env(persist))   # must not raise

    def test_developer_checkout_without_a_store_passes(self, tmp_path):
        # No persistent store means nothing redeploys over this tree, so real
        # directories are fine. Guarding here would break every dev machine.
        repo, persist = _repo(tmp_path, linked=(), real=("data", "logs"), store=False)
        assert sg.check_state_paths(cwd=repo, env=_env(persist)) == []


class TestWrongWorkingDirectory:
    """The failure this guard exists for: relative paths resolved from elsewhere."""

    def test_launch_from_the_wrong_directory_is_refused(self, tmp_path):
        repo, persist = _repo(tmp_path)
        elsewhere = tmp_path / "somewhere-else"
        elsewhere.mkdir()
        problems = sg.check_state_paths(cwd=elsewhere, env=_env(persist))
        assert problems, "a wrong CWD was accepted"
        assert "repo root" in problems[0]
        with pytest.raises(sg.StateGuardError, match="refusing to start"):
            sg.assert_state_paths_are_safe(cwd=elsewhere, env=_env(persist))

    def test_a_parent_containing_a_bot_folder_is_not_the_root(self, tmp_path):
        # `bot/` alone is not enough — a parent directory can contain one.
        # Without deploy.sh in the marker set this would false-negative.
        parent = tmp_path / "parent"
        (parent / "bot").mkdir(parents=True)
        _, persist = _repo(tmp_path)
        problems = sg.check_state_paths(cwd=parent, env=_env(persist))
        assert problems and "deploy.sh" in problems[0]


class TestDeployNotRun:
    def test_real_directory_where_a_symlink_belongs_is_refused(self, tmp_path):
        # deploy.sh never ran, but a store exists — so this directory is inside
        # the redeploy path and everything written to it dies.
        repo, persist = _repo(tmp_path, linked=("data",), real=("logs",))
        problems = sg.check_state_paths(cwd=repo, env=_env(persist))
        assert any("logs/ is a real directory" in p for p in problems)
        assert not any(p.startswith("data/") for p in problems), \
            "data/ was correctly linked and should not be flagged"

    def test_half_applied_deploy_names_only_the_broken_one(self, tmp_path):
        repo, persist = _repo(tmp_path, linked=("logs",), real=("data",))
        problems = sg.check_state_paths(cwd=repo, env=_env(persist))
        assert len(problems) == 1 and problems[0].startswith("data/")

    def test_symlink_pointing_outside_the_store_is_refused(self, tmp_path):
        repo, persist = _repo(tmp_path, linked=("logs",))
        stray = tmp_path / "stray"
        stray.mkdir()
        (repo / "data").symlink_to(stray)
        problems = sg.check_state_paths(cwd=repo, env=_env(persist))
        assert any("points at" in p for p in problems)

    def test_dangling_symlink_is_refused(self, tmp_path):
        repo, persist = _repo(tmp_path, linked=("logs",))
        (repo / "data").symlink_to(tmp_path / "does-not-exist")
        problems = sg.check_state_paths(cwd=repo, env=_env(persist))
        assert problems, "a dangling state symlink was accepted"


class TestOverride:
    def test_skip_env_allows_the_launch_but_says_so(self, tmp_path, capsys):
        repo, persist = _repo(tmp_path, linked=("logs",), real=("data",))
        env = dict(_env(persist), RUNECLAW_SKIP_STATE_GUARD="1")
        sg.assert_state_paths_are_safe(cwd=repo, env=env)          # must not raise
        out = capsys.readouterr().out
        assert "RUNECLAW_SKIP_STATE_GUARD is set" in out
        assert "data/" in out, "the bypass must print what it is skipping"

    def test_override_is_not_on_by_default(self, tmp_path):
        repo, persist = _repo(tmp_path, linked=("logs",), real=("data",))
        with pytest.raises(sg.StateGuardError):
            sg.assert_state_paths_are_safe(cwd=repo, env=_env(persist))


class TestStaysInSyncWithDeploySh:
    def test_guarded_paths_match_what_deploy_sh_links(self):
        """Adding link_persistent "x" to deploy.sh without adding "x" here would
        leave the new path unguarded — silently, and exactly the way logs/ went
        unguarded between being untracked and being persisted."""
        import re
        deploy = (Path(__file__).resolve().parents[1] / "deploy.sh").read_text()
        linked = set(re.findall(r'^link_persistent "([^"]+)"', deploy, re.M))
        linked.discard(".env")          # a file, not a state directory
        assert linked == set(sg._LINKED), (
            f"deploy.sh links {sorted(linked)} but the guard checks "
            f"{sorted(sg._LINKED)} — they must match")


class TestMutation:
    def test_MUTATION_the_root_check_is_what_produces_the_root_diagnosis(
            self, tmp_path, monkeypatch):
        """Break the root-marker check and prove which diagnosis it owns.

        Disabling it does NOT stop a wrong CWD being caught — the link checks
        catch it too, because `elsewhere` has no data/ or logs/ either. That is
        defence in depth and worth having. But the message degrades badly: the
        operator is told "data/ does not exist, run ./deploy.sh" when the real
        problem is that they launched from the wrong directory, and running
        deploy.sh there would make things worse by creating a second store.

        So the root check earns its place on the QUALITY of the diagnosis, not
        on detection alone, and that is what this asserts.
        """
        repo, persist = _repo(tmp_path)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()

        before = sg.check_state_paths(cwd=elsewhere, env=_env(persist))
        assert any("repo root" in p for p in before), "guard is not firing at all"

        monkeypatch.setattr(sg, "_ROOT_MARKERS", ())
        after = sg.check_state_paths(cwd=elsewhere, env=_env(persist))
        assert not any("repo root" in p for p in after), (
            "removing the markers did not disable the root check — this test is "
            "not measuring what it claims to")
        assert after, "nothing caught the bad CWD once the root check was gone"
        assert any("deploy.sh" in p for p in after), (
            "the fallback diagnosis should still name a remedy, even a wrong one")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
