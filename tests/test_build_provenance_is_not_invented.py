"""The bot could not say which commit it was running.

On 2026-08-20 a deploy reset to a mirror 255 commits stale and reported
success. `scripts/verify_deploy_source.sh` stops that deploy from starting.
This covers the other half — after the restart, what IS running — which the bot
answered with

    ⚔️ RUNECLAW v0.1.0

a hand-maintained constant that reads the same before and after every deploy.

Three properties are load-bearing, and they are the three that the surrounding
codebase has been bitten by before:

1. NOTHING IS INVENTED. No sha resolves to the word `unknown`, never a blank
   and never a plausible-looking value. `ARG BUILD_SHA=dev` is in our own
   Dockerfile, so `dev` is a value that really arrives, and displaying it would
   be a stamp that is not a commit — worse than none, because it looks like one.

2. CLEAN IS NOT THE SAME FACT AS COULD-NOT-TELL. `git status --porcelain`
   prints nothing for a clean tree, which is the same empty output a failed
   call gives. Folding those together prints a bare sha over an unexamined
   tree, which asserts "these bytes are that commit" without having looked.

3. THE SURFACES ACTUALLY SHOW IT. A resolver nothing calls is indistinguishable
   from one that does not work — `token_dossier` and friends were pure,
   correct, and reachable by no human.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.utils import build_info as bi

#: Everything `short()` may ever emit. A sha, optionally `-dirty`, optionally a
#: parenthesised note — and nothing else. Asserted everywhere, so no scenario
#: can leak an environment value, a path or an exception string into a label an
#: operator reads as provenance.
LABEL_RE = re.compile(r"^(unknown|[0-9a-f]{7}(-dirty)?( \([a-z .,]+\))?)$")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


@pytest.fixture(autouse=True)
def _no_ambient_stamp(monkeypatch):
    """The env branch wins by design, so a stamp on the CI runner would mask
    every other test in this file."""
    monkeypatch.delenv("BUILD_SHA", raising=False)
    monkeypatch.delenv("SOURCE_COMMIT", raising=False)


@pytest.fixture
def repo(tmp_path) -> Path:
    """A real one-commit git repository, not a mock of one."""
    r = tmp_path / "checkout"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "T")
    (r / "code.py").write_text("x = 1\n", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "one")
    return r


@pytest.fixture
def no_git_binary(monkeypatch):
    """A slim image can carry .git and no `git`. version.js learned this the
    hard way; inheriting the lesson is the point of the plumbing reader."""
    def _boom(*a, **k):
        raise FileNotFoundError("git")
    monkeypatch.setattr(bi.subprocess, "run", _boom)


# ── 1. nothing is invented ──────────────────────────────────────────────────

class TestNothingIsInvented:
    def test_unknown_when_there_is_nothing_to_read(self, tmp_path):
        info = bi.resolve(tmp_path)
        assert info.sha is None, f"a sha appeared from an empty directory: {info}"
        assert info.dirty is None
        assert bi.short(info) == "unknown"

    def test_unknown_is_a_word_not_a_blank(self, tmp_path):
        """A blank beside the label "Build" reads as "no build problem". The
        whole module exists because an absent fact was read as a benign one."""
        assert bi.short(bi.resolve(tmp_path)).strip() != ""

    def test_unknown_cannot_be_misread_as_a_sha(self, tmp_path):
        assert not re.match(r"^[0-9a-f]{7}", bi.short(bi.resolve(tmp_path)))

    def test_the_dockerfiles_own_default_is_rejected(self, repo, monkeypatch):
        """`ARG BUILD_SHA=dev` — so `dev` is not hypothetical, it is what an
        image built without --build-arg actually carries."""
        monkeypatch.setenv("BUILD_SHA", "dev")
        info = bi.resolve(repo)
        assert info.source == "git", f"'dev' was displayed as provenance: {info}"

    def test_an_unexpanded_build_arg_is_rejected(self, repo, monkeypatch):
        monkeypatch.setenv("BUILD_SHA", "${BUILD_SHA}")
        assert bi.resolve(repo).source == "git"

    def test_junk_does_not_shadow_the_real_answer(self, repo, monkeypatch):
        """The dangerous half: a bad stamp must fall THROUGH, not blank the
        tree's own correct answer."""
        monkeypatch.setenv("BUILD_SHA", "not-a-sha")
        real = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        assert bi.resolve(repo).sha == real

    @pytest.mark.parametrize("body", ['{"sha": "dev"}', "{}", "not json",
                                      '[]', '{"sha": null}', '{"sha": 12345}'])
    def test_a_useless_build_file_falls_through_rather_than_crashing(
            self, repo, body):
        (repo / "build-info.json").write_text(body, encoding="utf-8")
        info = bi.resolve(repo)
        assert info.source == "git", f"{body!r} produced {info}"


# ── 2. clean is not the same fact as could-not-tell ─────────────────────────

class TestCleanIsNotCouldNotTell:
    """THE ONE THAT MATTERS. `git status --porcelain` exits 0 and prints
    nothing for a clean tree — identical to a failure, if failure is folded
    into the empty string."""

    def test_a_clean_tree_is_false_not_none(self, repo):
        assert bi._working_tree_dirty(repo) is False

    def test_a_directory_that_is_not_a_repo_is_none_not_false(self, tmp_path):
        assert bi._working_tree_dirty(tmp_path) is None, (
            "could-not-tell collapsed into clean — a bare sha would then be "
            "printed over a tree nobody examined")

    def test_a_modified_tracked_file_is_true(self, repo):
        (repo / "code.py").write_text("x = 2\n", encoding="utf-8")
        assert bi._working_tree_dirty(repo) is True

    def test_a_hand_patched_box_is_marked_dirty(self, repo):
        """Patching a running box by hand happens on this project, and a bot
        reporting a clean sha over three hand-edited files is false provenance
        dressed as precision."""
        (repo / "code.py").write_text("x = 2\n", encoding="utf-8")
        assert bi.short(bi.resolve(repo)).endswith("-dirty (git)")

    def test_untracked_files_are_not_a_code_difference(self, repo):
        """deploy.sh symlinks data/, logs/ and .env into the tree, and the
        operator's nohup.out lands beside them. A guard that cries wolf on
        every deploy gets ignored, and then it is not a guard."""
        (repo / "nohup.out").write_text("...", encoding="utf-8")
        (repo / "bot.log").write_text("...", encoding="utf-8")
        assert bi._working_tree_dirty(repo) is False
        assert "-dirty" not in bi.short(bi.resolve(repo))

    def test_without_git_the_tree_is_unchecked_and_says_so(self, repo,
                                                           no_git_binary):
        info = bi.resolve(repo)
        assert info.sha is not None, "the .git dir was readable; a sha was owed"
        assert info.dirty is None
        assert "tree unchecked" in bi.short(info)

    def test_the_three_states_render_differently(self, repo, monkeypatch):
        clean = bi.short(bi.resolve(repo))
        (repo / "code.py").write_text("x = 2\n", encoding="utf-8")
        dirty = bi.short(bi.resolve(repo))
        monkeypatch.setattr(bi.subprocess, "run",
                            MagicMock(side_effect=FileNotFoundError("git")))
        unchecked = bi.short(bi.resolve(repo))
        assert len({clean, dirty, unchecked}) == 3, (
            f"two states are indistinguishable to a reader: "
            f"{clean!r} {dirty!r} {unchecked!r}")

    def test_a_build_stamp_never_claims_a_clean_tree(self, repo, monkeypatch):
        """The sha came from a build arg, so the files on disk were never
        compared to it. Printing a bare sha would assert they match."""
        monkeypatch.setenv("BUILD_SHA", "a" * 40)
        info = bi.resolve(repo)
        assert info.dirty is None
        assert "tree unchecked" in bi.short(info)


# ── did my deploy land? ─────────────────────────────────────────────────────

class TestItAnswersTheQuestionItExistsFor:
    def test_the_label_moves_when_the_commit_moves(self, repo):
        before = bi.short(bi.resolve(repo))
        (repo / "code.py").write_text("x = 3\n", encoding="utf-8")
        _git(repo, "commit", "-qam", "two")
        after = bi.short(bi.resolve(repo))
        assert after != before, (
            "a new commit produced an identical label, so the label cannot "
            "answer 'did my deploy land?'")

    def test_an_unchanged_tree_gives_an_unchanged_label(self, repo):
        assert bi.short(bi.resolve(repo)) == bi.short(bi.resolve(repo))

    def test_it_agrees_with_git_in_this_very_repo(self):
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(bi.REPO_ROOT),
                              capture_output=True, text=True).stdout.strip()
        assert bi.resolve().sha == head

    def test_it_reads_this_repo_not_the_callers_directory(self, repo,
                                                          monkeypatch):
        """`verify_deploy_source.sh` shipped with a bare `git rev-parse HEAD`
        and answered from whatever directory the launcher was standing in. A
        launcher belongs OUTSIDE the repo, so that is the normal case, not an
        edge one."""
        monkeypatch.chdir(repo)
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(bi.REPO_ROOT),
                              capture_output=True, text=True).stdout.strip()
        assert bi.resolve().sha == head, "it answered about the caller's cwd"


# ── the stamp the image already writes ──────────────────────────────────────

class TestTheDockerfileStampIsReadByThePythonHalfToo:
    """`Dockerfile:35` writes /app/build-info.json — the repo root in the
    image — and `app/lib/version.js` has read it since July. The bot runs on
    the same box behind the same deploys and read nothing."""

    def test_it_is_read(self, repo, no_git_binary):
        (repo / "build-info.json").write_text(
            json.dumps({"sha": "b" * 40, "committed_at": "2026-08-20"}),
            encoding="utf-8")
        info = bi.resolve(repo)
        assert info.sha == "b" * 40
        assert info.source == "image"

    def test_it_wins_over_a_git_dir_the_image_may_still_carry(self, repo):
        (repo / "build-info.json").write_text('{"sha": "%s"}' % ("c" * 40),
                                              encoding="utf-8")
        assert bi.resolve(repo).sha == "c" * 40

    def test_the_container_env_wins_over_everything(self, repo, monkeypatch):
        (repo / "build-info.json").write_text('{"sha": "%s"}' % ("c" * 40),
                                              encoding="utf-8")
        monkeypatch.setenv("BUILD_SHA", "d" * 40)
        assert bi.resolve(repo).sha == "d" * 40

    def test_source_commit_is_honoured_too(self, repo, monkeypatch):
        monkeypatch.setenv("SOURCE_COMMIT", "e" * 40)
        assert bi.resolve(repo).sha == "e" * 40


# ── .git without the binary ─────────────────────────────────────────────────

class TestPlumbingWithoutGit:
    def test_a_loose_ref(self, repo, no_git_binary):
        assert bi.resolve(repo).source == "gitdir"
        assert bi.resolve(repo).sha is not None

    def test_packed_refs(self, repo, no_git_binary, monkeypatch):
        monkeypatch.undo()      # need the real binary to pack
        _git(repo, "pack-refs", "--all")
        loose = list((repo / ".git" / "refs" / "heads").rglob("*"))
        assert not [p for p in loose if p.is_file()], "setup: refs not packed"
        monkeypatch.setattr(bi.subprocess, "run",
                            MagicMock(side_effect=FileNotFoundError("git")))
        assert bi.resolve(repo).sha is not None, "packed-refs was not consulted"

    def test_a_detached_head(self, repo, no_git_binary):
        """A deploy that reset to a SHA rather than a branch is precisely the
        case this is most needed in."""
        head = (repo / ".git" / "HEAD")
        sha = (repo / ".git" / "refs" / "heads" /
               head.read_text(encoding="utf-8").split("refs/heads/")[1].strip()
               ).read_text(encoding="utf-8").strip()
        head.write_text(sha + "\n", encoding="utf-8")
        assert bi.resolve(repo).sha == sha

    def test_a_git_file_pointer(self, tmp_path, repo, no_git_binary):
        """A worktree or submodule has `.git` as a FILE holding a path."""
        moved = tmp_path / "elsewhere.git"
        (repo / ".git").rename(moved)
        (repo / ".git").write_text(f"gitdir: {moved}\n", encoding="utf-8")
        assert bi.resolve(repo).sha is not None

    def test_a_corrupt_git_dir_is_unknown_not_a_crash(self, repo,
                                                      no_git_binary):
        (repo / ".git" / "HEAD").write_text("garbage\n", encoding="utf-8")
        assert bi.resolve(repo).sha is None
        assert bi.short(bi.resolve(repo)) == "unknown"


# ── §F-15: only a commit id ever leaves ─────────────────────────────────────

class TestTheLabelCarriesNothingButProvenance:
    @pytest.mark.parametrize("setup", ["empty", "clean", "dirty", "stamp",
                                       "nogit", "corrupt"])
    def test_every_scenario_renders_within_the_grammar(self, setup, repo,
                                                       tmp_path, monkeypatch):
        if setup == "empty":
            info = bi.resolve(tmp_path)
        elif setup == "dirty":
            (repo / "code.py").write_text("x = 2\n", encoding="utf-8")
            info = bi.resolve(repo)
        elif setup == "stamp":
            monkeypatch.setenv("BUILD_SHA", "f" * 40)
            info = bi.resolve(repo)
        elif setup == "nogit":
            monkeypatch.setattr(bi.subprocess, "run",
                                MagicMock(side_effect=FileNotFoundError("git")))
            info = bi.resolve(repo)
        elif setup == "corrupt":
            monkeypatch.setattr(bi.subprocess, "run",
                                MagicMock(side_effect=FileNotFoundError("git")))
            (repo / ".git" / "HEAD").write_text("garbage\n", encoding="utf-8")
            info = bi.resolve(repo)
        else:
            info = bi.resolve(repo)
        assert LABEL_RE.match(bi.short(info)), (
            f"{setup}: {bi.short(info)!r} is outside the grammar — something "
            f"other than a commit id reached an operator-visible label")

    def test_a_dotenv_secret_cannot_reach_the_label(self, repo):
        (repo / ".env").write_text("BITGET_SECRET=hunter2hunter2\n",
                                   encoding="utf-8")
        assert "hunter2" not in bi.short(bi.resolve(repo))
        assert "hunter2" not in repr(bi.resolve(repo))


# ── 3. the surfaces actually show it ────────────────────────────────────────

class TestTheSurfacesShowIt:
    """A resolver nothing calls is indistinguishable from one that does not
    work. Both of these are what the operator reads after a restart."""

    def test_the_startup_banner_carries_the_build(self):
        from bot.main import _banner
        out = _banner()
        assert "Build:" in out, (
            "the banner reports mode, venue and balance — on 2026-08-20 all "
            "of those were correct while the code was 255 commits stale")
        assert bi.short() in out

    def test_the_banner_names_the_build_before_the_configuration(self):
        """Everything else in the banner describes how the bot is configured,
        and the configuration was the new deploy's while the code was not."""
        from bot.main import _banner
        out = _banner()
        assert out.index("Build:") < out.index("Mode:"), out

    async def test_the_version_command_carries_the_build(self):
        from bot.skills.telegram_handler import TelegramHandler
        h = TelegramHandler.__new__(TelegramHandler)
        h._limiter = MagicMock(allow=MagicMock(return_value=True))
        h._send = AsyncMock()
        await h._cmd_version(MagicMock(), MagicMock())
        sent = h._send.await_args[0][1]
        assert "Build:" in sent, f"/version still answers with a constant: {sent}"
        assert bi.short() in sent

    def test_the_health_endpoint_names_the_commit_that_answered(self):
        """The machine-readable twin. /version needs a human with Telegram
        open; this is what a deploy script or a probe can assert on."""
        import asyncio
        import json as _json
        from types import SimpleNamespace

        from bot.web import dashboard_server as ds
        resp = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            ds.handle_health(SimpleNamespace(app={})))       # type: ignore[arg-type]
        body = _json.loads(resp.body.decode())
        assert resp.status == 200, "provenance must never cost liveness"
        assert body["build"] == bi.short()
        assert body["status"] == "ok", "the liveness contract is unchanged"

    def test_health_still_answers_when_provenance_cannot_be_resolved(
            self, monkeypatch):
        """Liveness may not be made to fail by asking a question about the
        build. `unknown` is an answer; a 500 is not."""
        import asyncio
        import json as _json
        from types import SimpleNamespace

        from bot.web import dashboard_server as ds
        monkeypatch.setattr(ds, "build_short", lambda: bi.UNKNOWN)
        resp = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            ds.handle_health(SimpleNamespace(app={})))       # type: ignore[arg-type]
        assert resp.status == 200
        assert _json.loads(resp.body.decode())["build"] == "unknown"

    def test_version_no_longer_answers_with_a_constant_alone(self):
        """`__version__` has read "0.1.0" since the repo was created. It is a
        claim about intent; the build line is the part that can differ between
        two runs, which is the only reason anyone runs /version."""
        from bot import __version__
        assert bi.short() != __version__
