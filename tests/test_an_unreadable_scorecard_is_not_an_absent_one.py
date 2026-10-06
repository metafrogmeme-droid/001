"""A committed scorecard that cannot be read is not "No track record published".

`_load_scorecard` turned every exception into None, and the catalogue then
fell through to `unpublished_scorecard`, which is None for the house presets:
four public renderers printed "No track record published." for a preset
whose frozen record is in the repo, when the file was merely unreadable. The
follow verdict stays withheld (no verdict), which was already right.
"""
import json
import shutil
from pathlib import Path

import pytest

from bot.core import strategy_catalog as sc

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def cards(tmp_path, monkeypatch):
    d = tmp_path / "scorecards"
    shutil.copytree(ROOT / "benchmark" / "scorecards", d)
    monkeypatch.setattr(sc, "_SCORECARD_DIR", str(d))
    return d


def _entry(aid):
    return next(a for a in sc.catalog() if a["id"] == aid)


def test_a_corrupt_file_says_it_could_not_be_read(cards):
    (cards / "dip-sniper.json").write_text("{ not json")
    e = _entry("dip-sniper")
    assert e["scorecard"] == {"omitted": sc.SCORECARD_UNREADABLE}
    assert "No track record published" not in json.dumps(e["scorecard"])
    assert e["copy_follow"] is False and e["copy_follow_reason"] == "no_verdict"
    # The others still read.
    assert _entry("momentum-hunter")["scorecard"].get("metrics")


def test_a_payload_that_is_not_a_card_is_unreadable_too(cards):
    (cards / "dip-sniper.json").write_text(json.dumps(["not", "a", "card"]))
    assert _entry("dip-sniper")["scorecard"] == {"omitted": sc.SCORECARD_UNREADABLE}


def test_a_missing_file_is_still_absent(cards):
    (cards / "dip-sniper.json").unlink()
    assert sc._load_scorecard("dip-sniper") is None
    assert _entry("dip-sniper")["scorecard"] != {"omitted": sc.SCORECARD_UNREADABLE}
