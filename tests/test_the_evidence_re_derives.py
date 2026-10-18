"""Every number the repository publishes re-derives, and the verifier can say no.

`scripts/verify_evidence.py` is the one command a stranger runs instead of
trusting us: it recomputes each frozen dataset's hashes from its candle files,
checks each strategy card against its dataset's manifest and the commit it was
recorded at, and recomputes each pre-registered study's sealed hash. The
README's studies table is rendered from the same registry.

Both arms, for every check: the committed tree matches, and a tree with the
one thing wrong that the check exists to catch does not. Three verdicts, never
two: a check that could not run (no git history) is CANNOT CHECK, never a
pass and never a mismatch.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("verify_evidence", ROOT / "scripts" / "verify_evidence.py")
assert _spec and _spec.loader
ve = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("verify_evidence", ve)
_spec.loader.exec_module(ve)


def _statuses(rows):
    return {f"{r.group}/{r.subject}": r.status for r in rows}


# ── the committed tree ──────────────────────────────────────────────────────

class TestTheCommittedTree:
    def test_every_dataset_re_derives(self):
        rows = ve.check_datasets(ROOT)
        assert rows and all(r.status == ve.MATCH for r in rows), [r.__dict__ for r in rows if r.status != ve.MATCH]
        assert any(r.subject == "majors_1h" for r in rows), "the benchmark on record was not checked"

    def test_every_scorecard_matches_its_dataset_and_no_card_mismatches(self):
        rows = ve.check_scorecards(ROOT)
        assert len(rows) == len(list((ROOT / "benchmark" / "scorecards").glob("*.json")))
        # A shallow clone cannot answer for a commit: that is CANNOT CHECK, not a pass.
        assert not [r for r in rows if r.status == ve.MISMATCH], [r.__dict__ for r in rows]

    def test_every_study_s_seal_re_derives(self):
        rows = ve.check_studies(ROOT)
        reg = ve.load_studies(ROOT)
        assert len(rows) == len(reg["studies"]) >= 4
        assert all(r.status == ve.MATCH for r in rows), [r.__dict__ for r in rows]

    def test_the_registry_s_counts_are_its_rows(self):
        for s in ve.load_studies(ROOT)["studies"]:
            rules = [r for r in s["results"] if r.get("pass") is not None]
            if s["tests"] == len(rules):
                assert s["passes"] == sum(1 for r in rules if r["pass"] is True), s["id"]

    def test_the_readme_table_is_the_registry_s(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        start = readme.index("<!-- studies:start")
        end = readme.index(ve.README_END, start) + len(ve.README_END)
        assert readme[start:end] == ve.readme_block(ROOT), (
            "the README's studies table is not what the registry renders: run "
            "python3 scripts/verify_evidence.py --readme")

    def test_the_readme_no_longer_says_every_backtest_is_synthetic(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        assert "Backtest results use synthetic data" not in readme
        assert "backtests use synthetic GBM+GARCH price data" not in readme
        assert "python3 scripts/verify_evidence.py" in readme

    def test_nor_does_the_translation(self):
        zh = (ROOT / "README.zh-TW.md").read_text(encoding="utf-8")
        assert "回測結果使用合成資料" not in zh
        assert "python3 scripts/verify_evidence.py" in zh
        assert "README.md#verify-it-yourself" in zh, "the studies table has one copy; the translation links to it"


# ── a tree with one thing wrong ─────────────────────────────────────────────

@pytest.fixture
def tree(tmp_path):
    """A copy of the parts the verifier reads, small enough to break."""
    for rel in ("benchmark/studies.json", "docs/research/prereg"):
        src, dst = ROOT / rel, tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        (shutil.copytree if src.is_dir() else shutil.copy)(src, dst)
    (tmp_path / "benchmark" / "scorecards").mkdir(parents=True)
    shutil.copy(ROOT / "benchmark" / "scorecards" / "full-scan.json", tmp_path / "benchmark" / "scorecards")
    (tmp_path / "benchmark" / "majors_1h").mkdir()
    shutil.copy(ROOT / "benchmark" / "majors_1h" / "manifest.json", tmp_path / "benchmark" / "majors_1h")
    return tmp_path


class TestASealThatWasEdited:
    def test_an_edited_pre_registration_is_a_mismatch(self, tree):
        p = tree / "docs/research/prereg/funding_recost/PREREGISTRATION.md"
        p.write_text(p.read_text(encoding="utf-8").replace("Sharpe ≥ 0.5", "Sharpe ≥ 0.3"), encoding="utf-8")
        st = _statuses(ve.check_studies(tree))
        assert st["studies/funding_recost"] == ve.MISMATCH
        assert st["studies/kraken_spot_history"] == ve.MATCH, "one edited file is one mismatch"

    def test_a_registry_hash_that_is_not_the_file_s_is_a_mismatch(self, tree):
        reg = json.loads((tree / "benchmark/studies.json").read_text(encoding="utf-8"))
        reg["studies"][0]["prereg_sha256"] = "0" * 64
        (tree / "benchmark/studies.json").write_text(json.dumps(reg), encoding="utf-8")
        assert _statuses(ve.check_studies(tree))["studies/bitget_slow_rules"] == ve.MISMATCH

    def test_a_file_re_hashed_without_resealing_the_sidecar_is_a_mismatch(self, tree):
        p = tree / "docs/research/prereg/kraken_spot_history/PREREGISTRATION.md"
        p.write_text(p.read_text(encoding="utf-8") + "\nedited after the seal\n", encoding="utf-8")
        reg = json.loads((tree / "benchmark/studies.json").read_text(encoding="utf-8"))
        for s in reg["studies"]:
            if s["id"] == "kraken_spot_history":
                s["prereg_sha256"] = hashlib.sha256(p.read_bytes()).hexdigest()
        (tree / "benchmark/studies.json").write_text(json.dumps(reg), encoding="utf-8")
        row = [r for r in ve.check_studies(tree) if r.subject == "kraken_spot_history"][0]
        assert row.status == ve.MISMATCH and "sidecar" in row.detail

    def test_more_passes_than_tests_or_none_stated_is_a_mismatch(self, tree):
        reg = json.loads((tree / "benchmark/studies.json").read_text(encoding="utf-8"))
        reg["studies"][1]["passes"] = 4
        reg["studies"][2].pop("tests")
        (tree / "benchmark/studies.json").write_text(json.dumps(reg), encoding="utf-8")
        st = _statuses(ve.check_studies(tree))
        assert st["studies/funding_rules"] == ve.MISMATCH
        assert st["studies/kraken_spot_history"] == ve.MISMATCH, "an absent count is not zero"


    def test_a_dollar_field_on_a_study_row_is_a_mismatch_and_dollar_words_in_prose_are_not(self, tree):
        reg = json.loads((tree / "benchmark/studies.json").read_text(encoding="utf-8"))
        reg["studies"][0]["results"][0]["final_equity"] = 10450.0
        reg["studies"][1]["verdict"] = str(reg["studies"][1].get("verdict", "")) + " Rebalance costs, net_pnl aside."
        (tree / "benchmark/studies.json").write_text(json.dumps(reg), encoding="utf-8")
        rows = {r.subject: r for r in ve.check_studies(tree)}
        assert rows["bitget_slow_rules"].status == ve.MISMATCH and "final_equity" in rows["bitget_slow_rules"].detail
        assert rows["funding_rules"].status == ve.MATCH, "a word in a sentence is not a field"


class TestACardThatDoesNotMatch:
    def _card(self, tree):
        return tree / "benchmark/scorecards/full-scan.json"

    def test_a_card_on_another_dataset_hash_is_a_mismatch(self, tree):
        card = json.loads(self._card(tree).read_text(encoding="utf-8"))
        card["dataset_hash"] = "f" * 64
        self._card(tree).write_text(json.dumps(card), encoding="utf-8")
        assert ve.check_scorecards(tree)[0].status == ve.MISMATCH

    def test_a_dollar_figure_on_a_public_card_is_a_mismatch(self, tree):
        card = json.loads(self._card(tree).read_text(encoding="utf-8"))
        card["metrics"]["net_pnl"] = 12.5
        self._card(tree).write_text(json.dumps(card), encoding="utf-8")
        row = ve.check_scorecards(tree)[0]
        assert row.status == ve.MISMATCH and "net_pnl" in row.detail

    def test_a_card_whose_dataset_is_absent_is_a_mismatch(self, tree):
        shutil.rmtree(tree / "benchmark/majors_1h")
        assert ve.check_scorecards(tree)[0].status == ve.MISMATCH

    def test_a_tree_with_no_history_cannot_check_the_commit_and_says_so(self, tree):
        row = ve.check_scorecards(tree)[0]
        assert not (tree / ".git").exists()
        assert row.status == ve.CANNOT and "git history" in row.detail


class TestTheVerdict:
    def test_three_verdicts_three_exit_codes(self):
        m, x, c = ve.Row("a", "1", ve.MATCH), ve.Row("a", "2", ve.MISMATCH), ve.Row("a", "3", ve.CANNOT)
        assert ve.exit_code([m]) == 0
        assert ve.exit_code([m, c]) == 3, "a check that did not run is not a pass"
        assert ve.exit_code([m, c, x]) == 1, "a mismatch outranks everything"

    def test_nothing_found_is_not_a_pass(self, tmp_path):
        rows = ve.check_datasets(tmp_path) + ve.check_scorecards(tmp_path) + ve.check_studies(tmp_path)
        assert rows and all(r.status == ve.CANNOT for r in rows)
        assert ve.exit_code(rows) == 3


class TestTheRootConstruction:
    """The construction /api/roots states: leaves = day seals deduped + sorted
    ascending; parent = sha256(utf8(leftHex + rightHex)); an odd node is
    promoted; a single leaf is the root."""

    A, B, C = "a" * 64, "b" * 64, "c" * 64

    @staticmethod
    def _h(x: str) -> str:
        return hashlib.sha256(x.encode("utf-8")).hexdigest()

    def test_a_single_leaf_is_the_root(self):
        assert ve.merkle_root([self.A]) == self.A

    def test_two_leaves_hash_in_sorted_order_whatever_order_they_came_in(self):
        assert ve.merkle_root([self.B, self.A]) == self._h(self.A + self.B)

    def test_an_odd_node_is_promoted_not_duplicated(self):
        assert ve.merkle_root([self.C, self.A, self.B]) == self._h(self._h(self.A + self.B) + self.C)

    def test_a_repeated_seal_is_one_leaf(self):
        assert ve.merkle_root([self.A, self.A, self.B]) == ve.merkle_root([self.A, self.B])

    def test_no_seals_is_no_root(self):
        assert ve.merkle_root([]) == ""
