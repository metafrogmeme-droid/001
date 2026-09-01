"""The public /risk page may not promise a fail-closed guarantee the engine does not keep.

RC-2026-022. `site/src/routes/risk.tsx`, published to `website/risk/index.html`:

    "There is no path where a check that could not be evaluated is treated as
     a check that passed."

That is CLAUDE.md's own rule, asserted to the public as a product guarantee.
The finder cited three counter-examples; the register counted nine; an AST walk
finds **sixteen**, and seven of those are `except` handlers — which contradicts
the sentence directly beneath it on the same page, that "an exception does not
skip that check: it records a failure".

The engine's own module docstring said it too, categorically, which is where
the page got it. That is the root and it is corrected as well.

WHAT THIS FILE DELIBERATELY DOES NOT DO: assert the sentence is ABSENT.
Both the corrected page and the corrected docstring QUOTE the false claim, to
show a reader what was wrong with it — and a scan cannot tell a quotation from
a restatement. CLAUDE.md records four false failures from exactly that shape.
So the checks below are positive wherever possible, and the one negative check
is anchored to the correction framing rather than to the bare phrase.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
ENGINE = ROOT / "bot" / "risk" / "risk_engine.py"
MANIFEST = ROOT / "config" / "risk_manifest.yaml"
PAGE_SRC = ROOT / "site" / "src" / "routes" / "risk.tsx"
PAGE_OUT = ROOT / "website" / "risk" / "index.html"
GITBOOK = ROOT / "docs" / "gitbook" / "risk-framework.md"


# ── what the engine actually does ─────────────────────────────────────────

def _skip_to_passed():
    """(on_absent, on_error) line numbers where a skip is appended to `passed`."""
    src = ENGINE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    in_except: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            for sub in ast.walk(node):
                if _is_passed_append(sub):
                    in_except.add(sub.lineno)
    all_sites = {n.lineno for n in ast.walk(tree) if _is_passed_append(n)}
    lines = src.splitlines()
    skips = {ln for ln in all_sites
             if re.search(r"skipped|no leverage", lines[ln - 1], re.IGNORECASE)}
    return sorted(skips - in_except), sorted(skips & in_except)


def _is_passed_append(node) -> bool:
    return (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "append"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "passed")


def test_the_engine_really_does_skip_to_passed_so_this_guard_is_not_stale():
    """If these paths ever disappear the categorical claim becomes TRUE.

    At that point the qualifiers this file enforces would be the new false
    statement, and someone should revisit them rather than assume.
    """
    on_absent, on_error = _skip_to_passed()
    assert on_absent, "no skip-on-absent paths remain; re-read the /risk copy"
    assert on_error, (
        "no `except` handler appends to `passed` any more — the page's "
        "'an exception records a failure' may now be true"
    )


def test_an_exception_can_still_reach_the_passed_list():
    """The claim that was wrong twice over: this is the sharper half."""
    _, on_error = _skip_to_passed()
    assert len(on_error) >= 5, (
        f"only {len(on_error)} except-to-passed paths found; the copy says "
        "'seven of them', so the number in the page needs re-checking"
    )


# ── the manifest is the authority the copy now points at ──────────────────

def _tally():
    data = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    beh: dict[str, int] = {}
    def walk(o):
        if isinstance(o, dict):
            v = o.get("fail_behavior")
            if isinstance(v, str):
                beh[v] = beh.get(v, 0) + 1
            for x in o.values():
                walk(x)
        elif isinstance(o, list):
            for x in o:
                walk(x)
    walk(data)
    return beh


def test_the_manifest_still_says_what_the_page_publishes():
    """The page prints 17 / 1 / 3. A number in prose is what rots first."""
    beh = _tally()
    assert beh == {"closed": 17, "open": 1, "skip": 3}, (
        f"the manifest tally moved to {beh}; the /risk page and the GitBook "
        "both publish the old numbers and are now wrong"
    )


def test_liquidity_is_still_the_named_fail_open_check():
    txt = MANIFEST.read_text(encoding="utf-8")
    assert "ONLY fail-open check" in txt


# ── the public surfaces ───────────────────────────────────────────────────

@pytest.mark.parametrize("path", [PAGE_SRC, PAGE_OUT, GITBOOK])
def test_the_surface_names_the_exception_rather_than_denying_it(path):
    """POSITIVE assertions. The surface must show the reader where it fails open."""
    assert path.exists(), f"{path} is missing; this guard watches nothing"
    txt = path.read_text(encoding="utf-8")
    for token in ("fail-closed", "fail-open"):
        assert token in txt.lower(), f"{path.name} no longer states {token}"
    # The manifest's own words, not a paraphrase. "some checks behave
    # differently" is as unactionable as the categorical claim it replaced;
    # a reader needs to know WHICH check and WHAT it does with no data.
    assert re.search(r"only fail-open check", txt, re.IGNORECASE), (
        f"{path.name} no longer quotes the manifest on which check fails open"
    )
    assert re.search(r"no data\s*=\s*pass", txt, re.IGNORECASE), (
        f"{path.name} names the fail-open check without saying what it does "
        "when it has nothing to read"
    )


def test_the_page_does_not_publish_a_check_COUNT():
    """The site has its own rule and the tally would have broken it.

    `site/test/site_honesty.test.js` forbids a published risk-check count —
    the number that matters is per-trade, varies with what applies to that
    trade, and is already reported on the decision record. A manifest tally
    reads as that count to anyone looking at the page, so the numbers live in
    the manifest and in this file, not in the marketing copy.
    """
    for path in (PAGE_SRC, PAGE_OUT):
        txt = path.read_text(encoding="utf-8")
        assert not re.search(r"\b\d+\s+checks?\b", txt, re.IGNORECASE), (
            f"{path.name} publishes a risk-check count again"
        )


@pytest.mark.parametrize("path", [PAGE_SRC, PAGE_OUT])
def test_the_page_still_states_the_property_it_does_keep(path):
    """THE CONTROL, matching site/test/site_honesty.test.js.

    Qualifying the claim must not become deleting it. Fail-closed is the
    product's central safety property on 17 of 21 checks and it is TRUE.
    """
    txt = path.read_text(encoding="utf-8")
    assert re.search(r"cannot be evaluated", txt, re.IGNORECASE), (
        "the page no longer says what happens to an unanswerable check"
    )


@pytest.mark.parametrize("path", [PAGE_SRC, PAGE_OUT, GITBOOK])
def test_no_surface_restates_the_categorical_claim(path):
    """The one negative check, ANCHORED so a quotation cannot trip it.

    The corrected page says "This page used to say there was no path where…",
    which a bare `"no path" not in text` would flag as the very defect it is
    describing. So each occurrence is required to sit inside a correction —
    the 220 characters before it must frame it as the old, wrong version.
    """
    txt = path.read_text(encoding="utf-8")
    bare = []
    for m in re.finditer(r"no path where|if ANY check cannot be evaluated", txt,
                         re.IGNORECASE):
        before = txt[max(0, m.start() - 220):m.start()].lower()
        if not re.search(r"used to say|was false|no longer|used to omit"
                         r"|older copy|categorical", before):
            bare.append(txt[max(0, m.start() - 90):m.start() + 110])
    assert not bare, (
        f"{path.name} states the categorical guarantee as current fact:\\n  "
        + "\\n  ".join(b.replace("\\n", " ")[:190] for b in bare)
    )
