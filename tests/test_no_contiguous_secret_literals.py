"""Test fixtures must be assembled at runtime, not written as one literal.

GitHub secret scanning flagged Telegram bot tokens and a Google API key in
test files even though they were fixtures. The tree gate
(`scripts/secret_literal_gate.py`) is what CI runs; this test is the same
rule, plus a mutation that plants a runtime-built token in a temporary file
and checks the gate goes red. The planted value is never a contiguous
literal in this file.
"""

from __future__ import annotations

from pathlib import Path

import scripts.secret_literal_gate as gate
from tests.fake_secrets import (
    FAKE_GOOGLE_API_KEY,
    fake_anthropic_key,
    fake_openai_key,
    fake_telegram_bot_token,
)


def test_the_tree_has_no_contiguous_provider_pattern_literals():
    findings = gate.scan_tree()
    assert findings == [], (
        "contiguous provider-pattern literal(s): "
        + ", ".join(f"{path}:{line} ({rule})" for rule, path, line in findings)
        + " — assemble fixtures with tests/fake_secrets.py or app/test/fake_secrets.js"
    )


def test_a_test_file_may_not_embed_a_token_instead_of_using_the_builder(tmp_path: Path):
    """The gate is red on a planted Telegram token, a Google key, and an LLM key."""
    token = fake_telegram_bot_token()
    google = FAKE_GOOGLE_API_KEY
    openai = fake_openai_key()
    anthropic = fake_anthropic_key()
    planted = tmp_path / "test_planted.py"
    planted.write_text(
        f"TOKEN = {token!r}\nGOOGLE = {google!r}\nOPENAI = {openai!r}\nANTHROPIC = {anthropic!r}\n",
        encoding="utf-8",
    )
    findings = gate.scan_tree(only=[planted])
    rules = {rule for rule, _path, _line in findings}
    assert "telegram_bot_token" in rules
    assert "google_api_key" in rules
    assert "openai_key" in rules
    assert "anthropic_key" in rules


def test_the_allowlist_is_only_the_documented_builders_and_table():
    assert gate.ALLOWED == {
        "tests/fake_secrets.py",
        "app/test/fake_secrets.js",
        "bot/utils/secret_shapes.py",
        "app/lib/secret_shapes.generated.json",
        ".gitleaks.toml",
    }

def test_the_gitleaks_tree_allowlist_is_the_same_documented_paths():
    import re
    text = (Path(__file__).resolve().parent.parent / ".gitleaks-tree.toml").read_text(encoding="utf-8")
    start = text.index("[allowlist]")
    body = text[start:]
    m = re.search(r"paths\s*=\s*\[(.*?)\n\]", body, re.S)
    assert m, "could not find the tree-config allowlist paths"
    code = "\n".join(ln for ln in m.group(1).splitlines() if not ln.strip().startswith("#"))
    paths = re.findall("'''(.*?)'''", code, re.S)
    documented = [
        r"^tests/fake_secrets\.py$",
        r"^app/test/fake_secrets\.js$",
        r"^bot/utils/secret_shapes\.py$",
        r"^app/lib/secret_shapes\.generated\.json$",
        r"^\.gitleaks\.toml$",
    ]
    assert paths[:5] == documented, paths[:5]
    joined = "\n".join(paths[5:])
    for needle in ("__pycache__", "node_modules", r"\.git/", "jpe?g", "pyc"):
        assert needle in joined, needle
    # A blanket tests/ allowlist is how the 2026-11 fixtures evaded the history scan.
    assert all("tests/" not in p or "fake_secrets" in p for p in paths)
