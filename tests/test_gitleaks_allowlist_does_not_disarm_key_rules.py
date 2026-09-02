"""The allowlist must not switch off the rules it says it does not touch.

From the audit's confirmed-not-remediated tier: "secrets: gitleaks allowlist
disables Solana keypair rules under tests/ and app/ (MEDIUM)".

CONFIRMED BY EXECUTION, not by reading. gitleaks 8.21.2 was run over the same
64-integer array — the exact shape `solana-keygen` writes — placed in three
directories:

    token/…keypair.json       caught
    tests/…keypair.json       SILENTLY ALLOWED
    app/test/…keypair.json    SILENTLY ALLOWED

The comment sitting above those two allowlist entries said the opposite: "The
Solana keypair rules above are NOT path-allowlisted, so a real keypair
committed under tests/ is still caught." A top-level `[allowlist]` applies to
EVERY rule. The comment asserted a protection the config did not provide, on
what `.gitleaks.toml` itself calls "the single highest-value pattern in the
repo: that file holds mint, metadata, presale and LP authority plus the entire
supply."

WHY THIS TEST IS STRUCTURAL. gitleaks is a CI-only gate — `scripts/preflight.py`
names it among the jobs it cannot run — so this asserts the property over the
config's own path regexes instead of shelling out to a binary that is not
there. It answers one question: does an allowlist path pattern swallow a file
where a real key would live?
"""
from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = ROOT / ".gitleaks.toml"


def _allowlist_paths() -> list[str]:
    """The `paths = [...]` entries of the top-level [allowlist] block."""
    text = CONFIG.read_text(encoding="utf-8")
    start = text.index("[allowlist]")
    block = text[start:]
    m = re.search(r"paths\s*=\s*\[(.*?)\n\]", block, re.S)
    assert m, "could not find the allowlist paths array"
    body = m.group(1)
    # Strip comment lines so the prose explaining a pattern is never read as one.
    code = "\n".join(ln for ln in body.splitlines()
                     if not ln.strip().startswith("#"))
    return re.findall(r"'''(.*?)'''", code, re.S)


def _swallows(pattern: str, path: str) -> bool:
    try:
        return re.search(pattern, path) is not None
    except re.error:
        return False


#: Files where a real key would actually live. A keypair is a bare JSON array;
#: a base58 secret is assigned in a .env. Neither is a .py or .js.
KEY_BEARING = [
    "tests/fixtures/mint_keypair.json",
    "tests/wallet.json",
    "app/test/deploy_keypair.json",
    "tests/local.env",
    "app/test/secrets.env",
    "tests/config.yaml",
]


@pytest.mark.parametrize("path", KEY_BEARING)
def test_no_allowlist_path_swallows_a_key_bearing_file(path):
    swallowed = [p for p in _allowlist_paths() if _swallows(p, path)]
    assert swallowed == [], (
        f"{path} is allowlisted by {swallowed!r}, so BOTH Solana rules are "
        "switched off for it — a committed keypair there is never reported"
    )


#: The noise the allowlist exists to suppress. Measured, not guessed: with the
#: allowlist stripped, gitleaks reports 21 findings on tracked files, every one
#: in a .py or .js file. All of these must stay covered or the scanner becomes
#: the thing people click past.
NOISE_BEARING = [
    "tests/test_secrets_vault.py",
    "tests/test_web3_signer.py",
    "app/test/secrets_vault.test.js",
    "app/test/arena_diagnostic_reason.test.js",
]


@pytest.mark.parametrize("path", NOISE_BEARING)
def test_the_noisy_fixtures_are_still_allowlisted(path):
    """The other direction. An over-narrow fix re-floods the scan with 21
    known-fake findings, which is how a secret scanner stops being read."""
    assert any(_swallows(p, path) for p in _allowlist_paths()), (
        f"{path} is no longer allowlisted; the generic-api-key noise this "
        "allowlist exists to suppress is back"
    )


def test_the_parser_found_a_real_allowlist():
    """Guard the guard: an empty parse would pass every case above."""
    paths = _allowlist_paths()
    assert len(paths) >= 4, f"only parsed {len(paths)} allowlist paths"
    # The pattern is an escaped regex (\.env\.example$), so match it as one
    # rather than looking for the literal filename inside it — the first draft
    # of this line did the latter and failed on a correct parse.
    assert any(_swallows(p, ".env.example") for p in paths)


def test_the_solana_rules_still_exist():
    """The allowlist is only interesting while the rules it could disarm do."""
    text = CONFIG.read_text(encoding="utf-8")
    assert 'id = "solana-keypair-json"' in text
    assert 'id = "solana-private-key-env"' in text


def test_the_config_does_not_use_a_silently_ignored_allowlist_form():
    """`[[allowlists]]` + `targetRules` expresses this more directly and gitleaks
    8.21.2 PARSES IT AND IGNORES IT — the config is accepted while suppressing
    nothing, which reads as a working fix and is not one. Measured: 21 findings
    with it, the same as no allowlist at all."""
    text = CONFIG.read_text(encoding="utf-8")
    code = "\n".join(ln for ln in text.splitlines()
                     if not ln.strip().startswith("#"))
    assert "targetRules" not in code, (
        "targetRules is silently ignored by the pinned gitleaks; the allowlist "
        "it appears to scope is doing nothing"
    )
