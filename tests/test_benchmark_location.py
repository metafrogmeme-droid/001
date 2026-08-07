"""The frozen benchmark could not be committed, and .gitignore said it could.

`.gitignore` carried `!data/benchmark/` with a comment explaining that
committing the snapshot "survives ephemeral container rebuilds". It never
fired. On a deployed box `deploy.sh` makes `data/` a symlink into
`~/runeclaw-persist/`, and git does not traverse symlinks — so `git add -f
data/benchmark/...` silently adds nothing, and every fresh clone, CI included,
got a repository whose own benchmark data could not exist inside it.

Five tests failed on `main` for that one reason, and the negation made it look
like a solved problem. A rule that cannot fire is not a rule; it is a comment
that reads like one — the same shape as a source scan matching nothing, or a
CI gate whose event never occurs.

The split is the fix, not a workaround:

    benchmark/          version-controlled build INPUT — always committed
    data/ -> persist/   runtime state — never committed, hence the symlink

One directory could not be both. Separating them leaves `deploy.sh` and the
19 tests pinning its state-preservation layers completely untouched.

`data/benchmark/` stays readable as a fallback so a box that has not yet moved
its copy keeps working and the migration needs no flag day.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from bot.backtest.snapshot import (BENCHMARK_DIR_NAMES, benchmark_root,
                                   default_benchmark_dir)

REPO = Path(__file__).resolve().parent.parent


class TestTheResolverPrefersTheCommittableHome:
    def test_the_new_location_wins_when_both_exist(self, tmp_path, monkeypatch):
        (tmp_path / "benchmark").mkdir()
        (tmp_path / "data" / "benchmark").mkdir(parents=True)
        monkeypatch.chdir(tmp_path)
        assert benchmark_root() == Path("benchmark")

    def test_the_old_location_still_works_alone(self, tmp_path, monkeypatch):
        # A box that has not moved its copy yet must not break.
        (tmp_path / "data" / "benchmark").mkdir(parents=True)
        monkeypatch.chdir(tmp_path)
        assert benchmark_root() == Path("data", "benchmark")

    def test_neither_present_names_where_it_SHOULD_be(self, tmp_path, monkeypatch):
        # An error should point at the new home, not the one being retired.
        monkeypatch.chdir(tmp_path)
        assert benchmark_root() == Path("benchmark")

    def test_the_default_dataset_path_follows_the_resolver(self, tmp_path, monkeypatch):
        (tmp_path / "data" / "benchmark").mkdir(parents=True)
        monkeypatch.chdir(tmp_path)
        assert default_benchmark_dir() == os.path.join(
            "data", "benchmark", "majors_1h")

    def test_resolution_is_not_frozen_at_import(self, tmp_path, monkeypatch):
        """The old constant was evaluated once at import. A module-level
        `Path("data/benchmark")` cannot notice which location exists, which is
        why every consumer now calls the function instead."""
        monkeypatch.chdir(tmp_path)
        assert benchmark_root() == Path("benchmark")
        (tmp_path / "data" / "benchmark").mkdir(parents=True)
        assert benchmark_root() == Path("data", "benchmark")


class TestNoConsumerPinsTheOldPath:
    """Four constants pointed at `data/benchmark`. A fifth would silently
    reintroduce the symlink trap for whichever surface added it."""

    CONSUMERS = ("bot/api/lab.py", "bot/skills/skill_registry.py",
                 "scripts/gen_agent_scorecards.py")

    @pytest.mark.parametrize("rel", CONSUMERS)
    def test_it_resolves_rather_than_hardcodes(self, rel):
        from tests.source_scan import code_only
        src = code_only((REPO / rel).read_text(encoding="utf-8"))
        assert 'Path("data/benchmark")' not in src
        assert '"data/benchmark/majors_1h"' not in src
        assert ("benchmark_root" in src or "default_benchmark_dir" in src), (
            f"{rel} must resolve the benchmark root, not pin it"
        )


class TestTheNewHomeIsActuallyCommittable:
    """The property the old layout lacked, asserted directly.

    This is the whole point of the move, and it is exactly what nobody
    checked: `.gitignore` was written as though the path were committable and
    the filesystem layout made it impossible.
    """

    def _check_ignore(self, path: str) -> bool:
        """True when git would ignore `path`. `git check-ignore` exits 0 on a
        match, 1 on no match."""
        return subprocess.run(
            ["git", "check-ignore", "-q", path],
            cwd=REPO, capture_output=True).returncode == 0

    def test_the_benchmark_tree_is_not_ignored(self):
        assert not self._check_ignore("benchmark/majors_1h/manifest.json"), (
            "benchmark/ is ignored — the snapshot could not be committed"
        )

    def test_runtime_state_under_data_is_still_ignored(self):
        # The move must not have loosened what data/ keeps out. These are the
        # files whose loss on 2026-08-02 the deploy layers now guard.
        for p in ("data/secrets_vault.enc", "data/runeclaw.db",
                  "data/live_positions.json", "data/shadow_book.json"):
            assert self._check_ignore(p), f"{p} is no longer ignored"

    def test_the_dead_negation_is_gone(self):
        # Comments stripped first. The replacement comment in .gitignore quotes
        # the pattern in order to explain why it was removed, and a raw
        # substring check cannot tell that apart from the rule itself — this
        # test failed on its own documentation the first time it ran, which is
        # the failure mode CLAUDE.md records having produced four times.
        rules = [ln.strip() for ln in
                 (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
                 if ln.strip() and not ln.lstrip().startswith("#")]
        assert "!data/benchmark/" not in rules, (
            "a re-include that git can never reach through the data/ symlink "
            "reads as a solved problem and is not one"
        )

    def test_the_scan_would_still_catch_a_real_reinstatement(self):
        # Stripping comments must not have stripped the rule's teeth: prove the
        # check fires on an actual rule line, not just on prose.
        rules = ["data/*", "!data/benchmark/", "# !data/benchmark/"]
        live = [ln for ln in rules if not ln.lstrip().startswith("#")]
        assert "!data/benchmark/" in live
