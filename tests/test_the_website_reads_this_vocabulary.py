"""The website reads THIS table, and the artifact it reads cannot go stale.

`bot/utils/secret_shapes.py` is the one vocabulary of secret shapes, and its own
coverage note recorded the hole: "`app/lib/safe_error.js` has its own vocabulary
— wider on labels, narrower on token shapes — and a second file read by two
runtimes is filed, not done."

Driven, that second vocabulary published a Telegram bot token, a bare provider
key, a JWT, `RUNECLAW_SECRETS_KEY=`, `WEB3_SIGNER_PRIVATE_KEY=`, `WEB_CREDS_KEY=`
and `api key: …`, and turned `Authorization: Bearer sk-ant-…` into
`Authorization: ***REDACTED*** sk-ant-…` — the label redacted and the key
printed. `app/test/one_secret_vocabulary.test.js` drives every row of this table
through that runtime's real reader; this file guards the half that a JS test
cannot see: the artifact between them is CURRENT, and every row is renderable
into it at all.

The "the copies are gone" half is deliberately NOT here. Its Python twin scans
`bot/`, and the first draft of a Python scan over `safe_error.js` accused the
file on its own COMMENT — the prose that explains which shared spelling it must
not use named the spelling. Stripping JS comments is `app/test/helpers/code_only.js`,
so that assertion lives beside the JS drives, where the stripper is.

The artifact is rendered rather than hand-kept for the reason the README command
tables are: a second author reading the first author's table is what produced
the gap this closes. It is committed rather than generated at boot because
`app/` and `bot/` are different deploy targets, and a website that had to run a
Python script to learn what a secret looks like would fail open on the box where
that script is missing.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.utils.secret_shapes import (  # noqa: E402
    REDACTED,
    SHAPES,
    replacement_spec,
    scrub_secrets,
    table_rows,
)

sys.path.insert(0, str(ROOT / "scripts"))
import render_secret_shapes  # noqa: E402

ARTIFACT = ROOT / "app" / "lib" / "secret_shapes.generated.json"


def test_the_committed_artifact_is_what_the_renderer_renders_today():
    """The ratchet: a row added here and not rendered is a row the website never
    learns, and nothing else would say so.

    It compares the BYTES rather than reading the script's own verdict. A guard
    that asserts `--check` exited 0 is a guard a broken `--check` satisfies —
    the `ruff_gate.check_version` lesson, where a launcher read truthiness and
    learned nothing about what was measured."""
    assert ARTIFACT.read_text(encoding="utf-8") == render_secret_shapes.rendered(), (
        f"{ARTIFACT.name} is stale — run: python3 scripts/render_secret_shapes.py"
    )


def test_the_checker_itself_says_stale_when_it_is():
    """And the script an operator runs is driven too, both ways."""
    assert render_secret_shapes.main(["--check"]) == 0
    original = ARTIFACT.read_text(encoding="utf-8")
    try:
        ARTIFACT.write_text(original.replace('"rows"', '"ROWS"', 1), encoding="utf-8")
        assert render_secret_shapes.main(["--check"]) == 1
    finally:
        ARTIFACT.write_text(original, encoding="utf-8")


def test_the_artifact_holds_every_row_in_table_order():
    doc = json.loads(ARTIFACT.read_text())
    assert doc["redacted"] == REDACTED
    assert [r["name"] for r in doc["rows"]] == [s.name for s in SHAPES]


@pytest.mark.parametrize("shape", SHAPES, ids=lambda s: s.name)
def test_every_row_is_renderable_and_carries_its_own_pins(shape):
    """A row whose replacement cannot be described as data cannot travel, and a
    row that travels without its example and decoy arrives unpinned."""
    spec = replacement_spec(shape.replacement)
    assert spec["kind"] in {"redact", "template", "keep_words"}
    if spec["kind"] == "keep_words":
        assert spec["label"] >= 1 and spec["value"] >= 1 and isinstance(spec["sep"], str)
    if spec["kind"] == "template":
        assert REDACTED in spec["template"]
    assert shape.example and shape.decoy
    assert scrub_secrets(shape.example) != shape.example
    assert scrub_secrets(shape.decoy) == shape.decoy


@pytest.mark.parametrize("row", table_rows(), ids=lambda r: r["name"])
def test_every_pattern_is_written_in_the_subset_both_engines_read(row):
    """Python-only constructs cannot be honoured by a JavaScript RegExp, and a
    row that silently means something else there is the second vocabulary in a
    new spelling. The JS side drives each example; this names the constructs."""
    for construct in ("(?P<", "(?<=", "(?<!", "(?#", r"\A", r"\Z", "(?i)", "(?s)", "(?m)"):
        assert construct not in row["pattern"], f"{row['name']}: {construct} is Python-only"
