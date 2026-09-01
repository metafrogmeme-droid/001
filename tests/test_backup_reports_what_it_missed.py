"""A backup that skipped the credential stores must not report success.

RC-2026-008, parts (b) and (c). Part (a) — `exchange_creds.enc` absent from
`_CRITICAL` entirely — was already fixed.

(c) THE SILENT DROP. `secrets_vault` and `exchange_credentials` both resolve
through `RUNECLAW_STATE_DIR`; every entry in `_CRITICAL` is a literal
`data/...`. On a deployment that sets it, `critical_paths` looked for
`data/secrets_vault.enc`, found nothing, and — filtering on `is_file()` —
skipped it without a word. Reproduced before fixing: both credential stores
dropped out of the archive and the run returned success.

Only the ALL-absent case was reported. An archive missing exactly the two
files it exists to protect came back as an unqualified success — "a partial
total, printed as whole", on the disaster-recovery path.

(b) THE KEY IS STILL NOT ARCHIVED, and that is deliberate: putting a Fernet
master key beside the ciphertext it opens is a security trade-off for a human,
not an audit fix. What changed is that the dependency was SILENT. The manifest
now states it, so a restore operator learns the archive is unusable without
the key BEFORE the decrypt fails rather than after.
"""
from __future__ import annotations

import json
import logging

import pytest

from bot.utils import backup as bk

STORES = ("secrets_vault.enc", "exchange_creds.enc")
KEY = ".exchange_secret.key"


@pytest.fixture
def tree(tmp_path):
    """A repo root whose state dir is somewhere else entirely."""
    alt = tmp_path / "elsewhere"
    alt.mkdir()
    for n in (*STORES, KEY):
        (alt / n).write_bytes(b"ciphertext")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "runeclaw.db").write_bytes(b"db")
    return tmp_path, alt


# ── (c) the silent drop ───────────────────────────────────────────────────

def test_the_stores_are_found_when_the_state_dir_moves(tree, monkeypatch):
    root, alt = tree
    monkeypatch.setenv("RUNECLAW_STATE_DIR", str(alt))
    found, _ = bk.critical_status(str(root))
    names = {p.name for p in found}
    for s in STORES:
        assert s in names, (
            f"{s} dropped out of the backup because RUNECLAW_STATE_DIR moved it"
        )


def test_without_the_env_var_nothing_changes(tree, monkeypatch):
    """The default deployment must behave exactly as before."""
    root, _ = tree
    monkeypatch.delenv("RUNECLAW_STATE_DIR", raising=False)
    found, missing = bk.critical_status(str(root))
    assert {p.name for p in found} == {"runeclaw.db"}
    for s in STORES:
        assert f"data/{s}" in missing


def test_a_file_is_not_archived_twice_when_both_locations_exist(tree, monkeypatch):
    root, alt = tree
    (root / "data" / "secrets_vault.enc").write_bytes(b"other")
    monkeypatch.setenv("RUNECLAW_STATE_DIR", str(alt))
    found, _ = bk.critical_status(str(root))
    assert len(found) == len({str(p) for p in found}), "a path was archived twice"


# ── the honest half: what was NOT captured ────────────────────────────────

def test_missing_entries_are_reported_not_swallowed(tree, monkeypatch):
    root, alt = tree
    monkeypatch.setenv("RUNECLAW_STATE_DIR", str(alt))
    _, missing = bk.critical_status(str(root))
    assert missing, "every critical path was claimed found on a tree missing most"
    assert "data/attestation_key.bin" in missing


def test_a_complete_tree_reports_nothing_missing(tmp_path, monkeypatch):
    """The control: qualifying must not become always-complaining."""
    monkeypatch.delenv("RUNECLAW_STATE_DIR", raising=False)
    (tmp_path / "logs").mkdir()
    (tmp_path / "data").mkdir()
    for rel in bk._CRITICAL:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    _, missing = bk.critical_status(str(tmp_path))
    assert missing == [], f"a complete tree reported {missing} as missing"


# ── the manifest carries it ───────────────────────────────────────────────

def _make(root, tmp_path, monkeypatch):
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "backups"))
    return bk.create_backup(str(root))


def test_the_manifest_records_what_was_not_captured(tree, tmp_path, monkeypatch):
    root, alt = tree
    monkeypatch.setenv("RUNECLAW_STATE_DIR", str(alt))
    _, manifest = _make(root, tmp_path, monkeypatch)
    assert manifest["missing"], (
        "the manifest lists only what WAS captured, so a partial archive is "
        "indistinguishable from a complete one"
    )
    assert manifest["complete"] is False


def test_a_complete_backup_says_so(tmp_path, monkeypatch):
    monkeypatch.delenv("RUNECLAW_STATE_DIR", raising=False)
    root = tmp_path / "full"
    for rel in bk._CRITICAL:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    _, manifest = _make(root, tmp_path, monkeypatch)
    assert manifest["complete"] is True
    assert manifest["missing"] == []


def test_the_manifest_is_readable_json_on_disk(tree, tmp_path, monkeypatch):
    """`missing` has to survive to the file, not just the return value."""
    root, alt = tree
    monkeypatch.setenv("RUNECLAW_STATE_DIR", str(alt))
    archive, _ = _make(root, tmp_path, monkeypatch)
    side = archive.with_name(archive.name.replace(".tar.gz", ".manifest.json"))
    on_disk = json.loads(side.read_text())
    assert "missing" in on_disk and "externally_managed" in on_disk


# ── (b) the key: still out, and now said out loud ─────────────────────────

def test_the_fernet_key_is_still_not_archived(tree, tmp_path, monkeypatch):
    """The security decision is UNCHANGED and pinned so it cannot drift either way.

    Archiving a master key beside the ciphertext it opens is a trade-off for a
    human to make. This test exists so that if someone makes it, they make it
    deliberately — and update the manifest note in the same commit.
    """
    root, alt = tree
    monkeypatch.setenv("RUNECLAW_STATE_DIR", str(alt))
    found, _ = bk.critical_status(str(root))
    assert KEY not in {p.name for p in found}, (
        "the Fernet master key is now in the archive — that is a security "
        "decision, not a bug fix; see RC-2026-008(b)"
    )


def test_the_archive_says_it_cannot_be_restored_without_the_key(tree, tmp_path, monkeypatch):
    root, alt = tree
    monkeypatch.setenv("RUNECLAW_STATE_DIR", str(alt))
    _, manifest = _make(root, tmp_path, monkeypatch)
    ext = manifest["externally_managed"]
    assert any(KEY in k for k in ext), "the key dependency is still silent"
    note = next(v for k, v in ext.items() if KEY in k)
    assert "cannot decrypt" in note.lower(), (
        "the note names the file without saying what its absence costs"
    )


# ── the operator hears about it ───────────────────────────────────────────

def test_a_partial_backup_warns(tree, tmp_path, monkeypatch, caplog):
    root, alt = tree
    monkeypatch.setenv("RUNECLAW_STATE_DIR", str(alt))
    with caplog.at_level(logging.WARNING, logger=bk.logger.name):
        _make(root, tmp_path, monkeypatch)
    assert any("PARTIAL" in r.message for r in caplog.records), (
        "a backup that skipped critical paths logged nothing; only the "
        "all-absent case was ever reported"
    )


def test_a_complete_backup_does_not_cry_wolf(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv("RUNECLAW_STATE_DIR", raising=False)
    root = tmp_path / "full"
    for rel in bk._CRITICAL:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    with caplog.at_level(logging.WARNING, logger=bk.logger.name):
        _make(root, tmp_path, monkeypatch)
    assert not any("PARTIAL" in r.message for r in caplog.records)


# ── one definition, not two ───────────────────────────────────────────────

def test_there_is_no_second_resolver_to_drift_from(tree):
    """`critical_paths` was kept as a wrapper and became dead on the same day.

    `create_backup` needs `missing`, so it moved to `critical_status`, which
    left the older name called by nothing but tests —
    `tests/test_no_new_unreachable_functions.py` caught it in CI. A resolver
    that only tests call is the shape this repo's ratchets exist to stop, and
    two resolvers for one question is how the archive and the manifest come to
    disagree. It is deleted rather than baselined.
    """
    assert not hasattr(bk, "critical_paths"), (
        "a second resolver is back; `create_backup` reads `critical_status`, "
        "so anything else is a copy that can drift from what is archived"
    )
