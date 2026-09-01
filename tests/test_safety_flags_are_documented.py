"""A protection an operator cannot see is one they can disable by accident.

RC-2026-005. A boolean env flag defaulting True IS a protection — setting it
false removes it. 86 of them appeared nowhere in `.env.example`, 13 on money
paths: the machinery that rescues a filled position whose stop never landed
(`UNPROTECTED_GUARD_ENABLED`, `UNPROTECTED_ESCALATION_ENABLED`), the slippage
guard, the per-strategy notional cap, the four Guardian subsystems, the
trailing and time stops.

The defaults are correct and fail-safe. The gap was that the file documenting
configuration never named them, so no operator could audit what was protecting
them, and no inventory existed at all.

WHY THE NUMBER IN THE FINDING IS NOT THE NUMBER HERE. The audit counted 110
default-ON flags with 90 undocumented, from a literal scan. Four of those 90 —
`ENV_NAME`, `THING_ENABLED`, `WRAPPED_ENABLED`, `RUNECLAW_TEST_SWITCH` — are
example strings inside `tests/test_flag_prose_matches_default.py`'s own
fixtures and one test's monkeypatch. A grep cannot tell a flag from a string
shaped like one, which is exactly what produced two of this audit's recorded
false positives.

Scanning only `_env_bool` gets it wrong the other way: `LLM_BACKGROUND_SCANS`
is real, defaults ON, and is read by `_env_switch` — a separate helper that
exists because `_env_bool` reads "off" as True. `_env_flag` and `_env_on` are
two more. Sound: **106 default-ON, 86 undocumented** before this change.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
ENV_EXAMPLE = ROOT / ".env.example"


def _inv():
    """The SAME module the generator uses. Two copies would drift."""
    spec = importlib.util.spec_from_file_location(
        "safety_flag_inventory", ROOT / "scripts" / "safety_flag_inventory.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


INV = _inv()


# ── the finding, as a ratchet ─────────────────────────────────────────────

def test_no_default_on_flag_is_undocumented():
    missing = INV.undocumented()
    assert missing == [], (
        f"{len(missing)} boolean flag(s) default to ON and are absent from "
        f".env.example, so an operator can disable a protection through a "
        f"variable that file never names: {', '.join(missing[:12])}"
        f"{' …' if len(missing) > 12 else ''}\n\n"
        "Regenerate the block:  python3 scripts/safety_flag_inventory.py --section"
    )


def test_the_scan_still_finds_flags_at_all():
    """If the readers are renamed this file silently guards nothing.

    An inventory that finds zero flags would make the assertion above pass
    vacuously — the same failure it exists to prevent, one level up.
    """
    on = INV.default_on_flags()
    assert len(on) > 80, (
        f"only {len(on)} default-ON flags found; the inventory has probably "
        "stopped seeing a reader rather than the flags having gone away"
    )


@pytest.mark.parametrize("reader", INV.READERS)
def test_every_boolean_reader_is_still_defined(reader):
    """The other half of the same worry: a reader that no longer exists."""
    hits = [
        p for p in (ROOT / "bot").rglob("*.py")
        if f"def {reader}(" in p.read_text(encoding="utf-8", errors="ignore")
    ]
    assert hits, (
        f"`{reader}` is in the inventory's reader list but is defined nowhere "
        "in bot/ — either it was renamed (update READERS) or the list is stale"
    )


def test_a_new_reader_would_be_noticed():
    """A sixth helper would make the inventory a subset reported as the whole.

    This is the mistake the first draft made: only `_env_bool` was scanned and
    `LLM_BACKGROUND_SCANS` went missing.
    """
    import re
    defined = set()
    for p in (ROOT / "bot").rglob("*.py"):
        src = p.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"^def (_env_\w+)\(", src, re.M):
            n = m.group(1)
            if re.search(rf"def {n}\([^)]*\)\s*->\s*bool", src):
                defined.add(n)
    unknown = defined - set(INV.READERS)
    assert not unknown, (
        f"boolean env reader(s) the inventory does not scan: {sorted(unknown)}. "
        "Add them to READERS in scripts/safety_flag_inventory.py, or every flag "
        "they read is invisible to this guard."
    )


# ── the money path is called out, not buried ──────────────────────────────

def test_the_money_path_flags_are_listed_first():
    txt = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert INV.BEGIN in txt and INV.END in txt, "the generated block is gone"
    block = txt.split(INV.BEGIN, 1)[1].split(INV.END, 1)[0]
    money_hdr = block.index("money path")
    rest_hdr = block.index("everything else")
    assert money_hdr < rest_hdr
    for flag in sorted(INV.MONEY_PATH):
        pos = block.find(f"#{flag}=")
        assert pos != -1, f"{flag} is not in the block"
        assert pos < rest_hdr, (
            f"{flag} gates money and is filed under 'everything else'"
        )


def test_the_block_changes_nothing_by_being_there():
    """Every generated line is a comment. A default must not become a setting."""
    txt = ENV_EXAMPLE.read_text(encoding="utf-8")
    block = txt.split(INV.BEGIN, 1)[1].split(INV.END, 1)[0]
    live = [ln for ln in block.splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]
    assert live == [], (
        f"the generated block contains {len(live)} UNCOMMENTED line(s), which "
        f"would change behaviour on any deployment copying this file: {live[:3]}"
    )


def test_the_block_is_what_the_generator_produces():
    """Hand-edited documentation drifts from the code it documents."""
    txt = ENV_EXAMPLE.read_text(encoding="utf-8")
    on_disk = INV.BEGIN + txt.split(INV.BEGIN, 1)[1].split(INV.END, 1)[0] + INV.END
    assert on_disk.strip() == INV.section().strip(), (
        ".env.example's generated block is not what the generator produces — "
        "it was hand-edited, or a flag moved and it was not regenerated. Run: "
        "python3 scripts/safety_flag_inventory.py --section"
    )


# ── the tests/ exclusion, which is what made the count wrong ──────────────

@pytest.mark.parametrize("fixture", [
    "ENV_NAME", "THING_ENABLED", "WRAPPED_ENABLED", "RUNECLAW_TEST_SWITCH",
])
def test_test_fixtures_are_not_counted_as_flags(fixture):
    """These four were in the audit's 90. They are strings in test files."""
    assert fixture not in INV.default_on_flags(), (
        f"{fixture} is a test fixture, not a deployed safety flag; counting it "
        "is how the finding's 90 became four higher than the truth"
    )


def test_a_flag_read_only_by_a_switch_helper_is_still_counted():
    """`LLM_BACKGROUND_SCANS` is real, defaults ON, and `_env_bool` never sees it."""
    assert "LLM_BACKGROUND_SCANS" in INV.default_on_flags(), (
        "the inventory has stopped scanning _env_switch, so every flag read "
        "that way is invisible — which is how the first draft undercounted"
    )


# ── the detector must actually detect ─────────────────────────────────────

def test_planting_a_missing_flag_is_reported(tmp_path):
    """Plant a file with a real flag removed; the guard must name that flag.

    Every other assertion in this file passes with `undocumented()` replaced
    by `return []` — mutation-tested, and it survived. A guard that reports
    clean because of how it looked is the exact defect this inventory exists
    to catch, one level up from the flags it counts.
    """
    victim = "SLIPPAGE_GUARD_ENABLED"
    assert victim in INV.default_on_flags(), (
        f"{victim} is no longer a default-ON flag; pick another money-path "
        "flag for this test rather than deleting it"
    )
    real = ENV_EXAMPLE.read_text(encoding="utf-8")
    planted = tmp_path / ".env.example"
    planted.write_text(
        "\n".join(
            ln for ln in real.splitlines()
            if not ln.lstrip().lstrip("#").startswith(f"{victim}=")
        ) + "\n",
        encoding="utf-8",
    )

    assert victim not in INV.declared_in_env_example(planted)
    assert INV.undocumented(planted) == [victim], (
        "the detector did not report a flag that is provably absent from the "
        "file it was pointed at"
    )


def test_the_unplanted_file_is_the_control(tmp_path):
    """The same call against the real file reports nothing — so the test above
    is measuring the plant, not a detector that always complains."""
    assert INV.undocumented(ENV_EXAMPLE) == []
