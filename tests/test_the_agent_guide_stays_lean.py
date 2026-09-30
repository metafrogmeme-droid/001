"""CLAUDE.md is the short guide; the history lives in docs/lessons/.

CLAUDE.md had grown to 1,324,924 bytes of incident chapters. It is injected
into every agent that works on this repository, so an agent started with most
of its context spent and ran out before it finished: on 2026-09-29 three
parallel workflow lanes each died with "Prompt is too long" after about forty
tool calls, having written nothing they could commit.

The chapters moved verbatim to `docs/lessons/ENGINEERING_LOG.md`, indexed by
`docs/lessons/INDEX.md`, and CLAUDE.md keeps the rules. This file holds the
split: the guide stays under its size bound, every path it names exists, it
sends a reader to the log and the index, and the index is the one the script
generates from the log.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "CLAUDE.md"
LOG = ROOT / "docs" / "lessons" / "ENGINEERING_LOG.md"
INDEX = ROOT / "docs" / "lessons" / "INDEX.md"

#: The bound the plan set (docs/IMPROVEMENT_PLAN_2026-09-29.md, G1).
MAX_BYTES = 25_000


def _lessons_index():
    spec = importlib.util.spec_from_file_location(
        "lessons_index", ROOT / "scripts" / "lessons_index.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_guide_stays_under_its_bound():
    size = len(GUIDE.read_bytes())
    assert size < MAX_BYTES, (
        f"CLAUDE.md is {size} bytes; the bound is {MAX_BYTES}. A new lesson "
        "goes in docs/lessons/ENGINEERING_LOG.md, not here.")


def test_every_path_the_guide_names_exists():
    text = GUIDE.read_text(encoding="utf-8")
    named = set(re.findall(
        r"`((?:app|bot|docs|scripts|tests|benchmark)/[\w./-]*?)(?:::\w+)?`", text))
    assert named, "the guide names no paths; the pattern no longer matches it"
    missing = sorted(p for p in named if not (ROOT / p).exists())
    assert missing == [], f"CLAUDE.md names missing paths: {missing}"


def test_the_guide_sends_a_reader_to_the_log_and_its_index():
    text = GUIDE.read_text(encoding="utf-8")
    assert "docs/lessons/ENGINEERING_LOG.md" in text
    assert "docs/lessons/INDEX.md" in text
    assert LOG.exists() and INDEX.exists()


def test_the_guide_still_states_the_rule():
    text = GUIDE.read_text(encoding="utf-8")
    assert "Unreadable is never zero" in text
    for shape in ("float(x or 0)", 'getattr(o, "pnl", 0)', "losses = len(all) - wins"):
        assert shape in text, f"the shapes table lost {shape!r}"


def test_the_log_is_the_old_guide_with_a_header():
    text = LOG.read_text(encoding="utf-8")
    assert text.startswith("# RUNECLAW engineering log\n")
    assert "## Before you push, run one command" in text
    assert "## The rule behind most of the tests here" in text
    assert len(text) > 1_000_000, "the history was cut rather than moved"


def test_the_index_is_the_one_the_script_generates():
    mod = _lessons_index()
    want = mod.build(LOG.read_text(encoding="utf-8"))
    assert INDEX.read_text(encoding="utf-8") == want, (
        "docs/lessons/INDEX.md is stale: run python3 scripts/lessons_index.py")


def test_the_index_finds_a_chapter_and_its_guard():
    """Driven on planted text, because the real log changes."""
    mod = _lessons_index()
    planted = "\n".join([
        "# x", "", "## A section", "",
        "**THE LEAD SENTENCE OF A CHAPTER.** Body that cites",
        "`tests/test_one.py` and app/test/two.test.js.", "",
        "**not a lead, lower case words here.** body", "",
        "**THE SECOND CHAPTER LEAD IS HERE.** body with no test.", "",
    ])
    out = mod.build(planted)
    assert "## A section (line 3)" in out
    assert ("- L5: THE LEAD SENTENCE OF A CHAPTER — `app/test/two.test.js`, "
            "`tests/test_one.py`") in out
    assert "- L10: THE SECOND CHAPTER LEAD IS HERE" in out
    assert "not a lead" not in out


def test_check_mode_reports_a_stale_index(tmp_path, monkeypatch):
    mod = _lessons_index()
    log = tmp_path / "log.md"
    idx = tmp_path / "index.md"
    log.write_text("## S\n\n**A CHAPTER LEAD SENTENCE.** x\n", encoding="utf-8")
    idx.write_text("stale\n", encoding="utf-8")
    monkeypatch.setattr(mod, "LOG", log)
    monkeypatch.setattr(mod, "INDEX", idx)
    assert mod.main(["--check"]) == 1
    idx.write_text(mod.build(log.read_text(encoding="utf-8")), encoding="utf-8")
    assert mod.main(["--check"]) == 0


def test_a_cited_json_file_is_not_indexed_as_a_js_file():
    """The path pattern once had no end boundary, so `tests/x_baseline.json`
    was indexed as `tests/x_baseline.js`, a file that does not exist."""
    mod = _lessons_index()
    planted = "\n".join([
        "## S", "",
        "**A CHAPTER THAT CITES A BASELINE.** It names",
        "`tests/honesty_baseline.json`, `app/test/asset_versions.json` and",
        "`tests/test_real.py`.", "",
    ])
    out = mod.build(planted)
    assert "`tests/test_real.py`" in out
    assert "honesty_baseline.js`" not in out
    assert "asset_versions.js`" not in out


def test_every_guard_the_index_names_exists():
    text = INDEX.read_text(encoding="utf-8")
    named = set(re.findall(r"`((?:tests|app/test)/[\w./-]+\.(?:py|js))`", text))
    assert named, "the index names no guard tests; the pattern no longer matches it"
    missing = sorted(p for p in named if not (ROOT / p).exists())
    assert missing == sorted(NOT_A_FILE), (
        "the index names a guard that does not exist (a renamed test, or a "
        f"path the pattern misread): {sorted(set(missing) - set(NOT_A_FILE))}; "
        f"stale exemptions: {sorted(set(NOT_A_FILE) - set(missing))}")


#: Paths the log names that are not files, each with its reason.
NOT_A_FILE = {
    "tests/planted.py": "a synthetic nodeid the network-reach chapter quotes; "
                        "that guard drives the containment with it on purpose",
}
