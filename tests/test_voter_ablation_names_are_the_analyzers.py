"""The ablation can only mute a voter by the label the analyzer gives it.

`ABLATE_VOTERS` zeroes weights by name, and a name that matches nothing mutes
nothing: the run is identical to the baseline and the voter reads as "dead
weight" when it was never ablated. `bollinger` and `stochastic` did exactly
that (the labels are `bb_pct_b` and `stoch`) until the list was checked
against the code.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import voter_ablation as va  # noqa: E402

# Sub-labels built as f"divergence_{kind}" carry no literal of their own.
_DYNAMIC_PREFIXES = ("divergence_",)


def _bot_source() -> str:
    return "\n".join(p.read_text(encoding="utf-8")
                     for p in (ROOT / "bot").rglob("*.py"))


def test_every_curated_name_is_a_label_the_bot_can_emit():
    src = _bot_source()
    missing = []
    for name in va.CURATED:
        if re.search(r"""["']""" + re.escape(name) + r"""["']""", src):
            continue
        if name.startswith(_DYNAMIC_PREFIXES) and any(
                f'"{p}' in src or f"'{p}" in src or f"{p}{{" in src
                for p in _DYNAMIC_PREFIXES):
            continue
        missing.append(name)
    assert missing == [], f"ablation names that match no label: {missing}"


def test_the_two_names_that_once_matched_nothing_are_the_real_ones():
    assert "bollinger" not in va.CURATED and "stochastic" not in va.CURATED
    assert {"bb_pct_b", "stoch", "squeeze"} <= set(va.CURATED)
