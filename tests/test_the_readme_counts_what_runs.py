"""The README's red-team figures are the ones the gate runs.

The README said the red team attacks the risk engine with "28 scenarios across
10 categories" (and the Chinese copy the same) while CI's own step was named
"30 adversarial scenarios" and ``scripts/red_team.py`` ran 30 in 11
categories: the eleventh, malformed input, was added after the sentence was
written. A count in prose is a measurement somebody remembered, so this file
takes the figure from the run itself, the same ``--json`` report
``tests/test_red_team_runner.py`` reads, and holds every place the README
pair states it.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def red_team():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "red_team.py"), "--json"],
                       capture_output=True, text=True, cwd=ROOT, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    rep = json.loads(r.stdout)
    return rep["total_scenarios"], len({s["category"] for s in rep["scenarios"]})


@pytest.mark.parametrize("name,sentence,tree", [
    ("README.md", r"with (\d+) scenarios across (\d+) categories", r"# (\d+)-scenario adversarial stress tester"),
    ("README.zh-TW.md", r"橫跨 (\d+) 個類別的 (\d+) 種情境", r"# (\d+)-scenario adversarial stress tester"),
])
def test_the_readme_states_the_red_teams_own_counts(name, sentence, tree, red_team):
    total, categories = red_team
    text = (ROOT / name).read_text(encoding="utf-8")
    m = re.search(sentence, text)
    assert m, f"{name}: the red-team sentence moved"
    a, b = int(m.group(1)), int(m.group(2))
    stated = (a, b) if name == "README.md" else (b, a)
    assert stated == (total, categories), f"{name} says {stated}; the run has {(total, categories)}"
    assert [int(x) for x in re.findall(tree, text)] == [total], f"{name}: the project tree's comment"
    assert f"all {total} adversarial scenarios" in text or f"全部 {total} 種對抗式情境" in text, name
