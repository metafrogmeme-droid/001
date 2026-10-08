"""The benchmark record names a commit `main` carries.

`benchmark/majors_1h/result.json` carries `code_sha`, the commit the run was
measured at, and `docs/FROZEN_BENCHMARK.md` says the artefact lands in the
commit after that one. The record of 29 September was stamped `b8c36630`, a
branch commit that a rebase replaced before it merged, so the stamp named
nothing in this repository. Four more of the page's re-record stamps had the
same history. Nobody could check out the code a published figure came from.

Two readings are held here:

- the record's stamp is the parent of the commit that last wrote the file,
  the rule `tests/test_agent_scorecard_projection.py` already holds for the
  strategy scorecards. A re-record run on a branch and rebased fails it;
- every commit the page cites (an eight-digit hex in backticks) resolves in
  this repository's history, or is a lost stamp the page's table maps to a
  commit that does.

Both read git history. CI checks out with `fetch-depth: 0`. A shallow local
clone cannot say whether an old commit exists, so the citation check is
skipped there with the command that fixes it, and fails under GitHub Actions,
where shallow history would mean the checkout changed.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_RECORD = _REPO / "benchmark" / "majors_1h" / "result.json"
_DOC = _REPO / "docs" / "FROZEN_BENCHMARK.md"

#: A commit cited on the page: eight hex digits in backticks. The page's
#: dataset hashes are twelve digits, so they never match.
_CITED = re.compile(r"`([0-9a-f]{8})`")
#: A row of the lost-stamp table: the stamp, then its commit on `main`.
_MAPPED = re.compile(r"^\|\s*`([0-9a-f]{8})`\s*\|\s*`([0-9a-f]{8})`", re.M)


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=_REPO, text=True).strip()


def _is_commit_on_head(sha: str) -> bool:
    """Whether `sha` names a commit HEAD's history carries.

    `merge-base --is-ancestor` answers 0 for an ancestor, 1 for a commit that
    is not one, and 128 for a hash that names no commit (a tree, or nothing).
    """
    return subprocess.run(
        ["git", "merge-base", "--is-ancestor", sha, "HEAD"], cwd=_REPO,
        capture_output=True).returncode == 0


def unresolved_citations(text: str, carried) -> list[str]:
    """The commits `text` cites that `carried` does not, after the mapping.

    A stamp the table maps is answered by its commit on `main`; the stamp
    itself need not resolve, and the commit it maps to must.
    """
    mapped = dict(_MAPPED.findall(text))
    answers = set(mapped.values())
    out = []
    for sha in sorted(set(_CITED.findall(text))):
        if sha in mapped:
            if not carried(mapped[sha]):
                out.append(f"{sha} -> {mapped[sha]}")
        elif sha in answers:
            continue  # checked through its row above
        elif not carried(sha):
            out.append(sha)
    return out


class TestTheReading:
    """`unresolved_citations` on planted pages, both arms."""

    CARRIED = {"aaaaaaaa", "bbbbbbbb"}.__contains__

    def test_a_cited_commit_main_carries_passes(self):
        assert unresolved_citations("at `aaaaaaaa`.", self.CARRIED) == []

    def test_a_cited_commit_nothing_carries_is_named(self):
        assert unresolved_citations("at `cccccccc`.", self.CARRIED) == ["cccccccc"]

    def test_a_lost_stamp_mapped_to_a_carried_commit_passes(self):
        page = "at `cccccccc`.\n\n| stamp | on main |\n|---|---|\n| `cccccccc` | `bbbbbbbb` | x |\n"
        assert unresolved_citations(page, self.CARRIED) == []

    def test_a_lost_stamp_mapped_to_nothing_carried_is_named(self):
        page = "at `cccccccc`.\n\n| `cccccccc` | `dddddddd` | x |\n"
        assert unresolved_citations(page, self.CARRIED) == ["cccccccc -> dddddddd"]

    def test_a_twelve_digit_dataset_hash_is_not_a_citation(self):
        assert unresolved_citations("hash `05074080c0a3`.", self.CARRIED) == []


def _need_full_history() -> None:
    if _git("rev-parse", "--is-shallow-repository") != "true":
        return
    why = ("this checkout is shallow, so whether an old commit exists cannot be "
           "read; run `git fetch --unshallow origin main`")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        pytest.fail("CI checks out with fetch-depth: 0, and this one is shallow: " + why)
    pytest.skip(why)


def test_the_carried_reading_wants_a_commit_on_heads_history():
    head = _git("rev-parse", "HEAD")
    assert _is_commit_on_head(head)
    assert _is_commit_on_head(_git("rev-parse", "HEAD~1"))
    # A tree is an object but not a commit, and an absent hash is neither.
    assert not _is_commit_on_head(_git("rev-parse", "HEAD^{tree}"))
    assert not _is_commit_on_head("0" * 40)


def test_the_record_is_stamped_at_the_commit_before_the_one_that_wrote_it():
    record = json.loads(_RECORD.read_text(encoding="utf-8"))
    sha = record["code_sha"]
    assert re.fullmatch(r"[0-9a-f]{40}", sha), sha
    assert _git("cat-file", "-t", sha) == "commit", sha
    head = _git("rev-parse", "HEAD")
    # Measured at the current HEAD (the file is not committed yet), or at the
    # parent of the commit that last wrote it: the code the run measured.
    last = _git("log", "-1", "--format=%H", "--", str(_RECORD.relative_to(_REPO)))
    produced = _git("rev-parse", f"{last}^")
    assert sha in (head, produced), (
        f"result.json is stamped {sha[:8]}, but the commit that wrote it "
        f"({last[:8]}) sits on {produced[:8]}. Re-run the record from a clean "
        f"checkout of main and commit it on top.")


def test_every_commit_the_benchmark_page_cites_is_one_main_carries():
    _need_full_history()
    text = _DOC.read_text(encoding="utf-8")
    assert _CITED.search(text), "expected the page to cite commits"
    assert _MAPPED.search(text), "expected the lost-stamp table"
    assert unresolved_citations(text, _is_commit_on_head) == []


def test_the_page_states_the_records_own_figures():
    """The paragraph that names the stamp carries the record's own numbers."""
    record = json.loads(_RECORD.read_text(encoding="utf-8"))
    text = _DOC.read_text(encoding="utf-8")
    short = record["code_sha"][:8]
    i = text.find(f"at `{short}`")
    assert i >= 0, f"the page does not name the record's stamp {short}"
    para = text[i:text.find("\n\n", i)]
    pooled = record["pooled"]
    assert pooled["net_usd"] is not None and pooled["pf"] is not None, pooled
    sign = "−" if pooled["net_usd"] < 0 else "+"
    flat = para.replace("\n", " ")
    assert f"{pooled['trades']} pooled" in flat, para
    assert f"net {sign}${abs(pooled['net_usd']):,.2f}" in flat, para
    assert f"PF {pooled['pf']:.2f}" in flat, para
