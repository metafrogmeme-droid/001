"""An unreadable credential file destroyed every key in it, on the next save.

    def _load(self) -> None:
        try:
            self._enc = json.load(f)
        except (json.JSONDecodeError, OSError):
            log.error("exchange_creds file unreadable — starting empty ...")
            self._enc = {}

Nothing else changed. But `_save()` writes `self._enc` wholesale through
tmp+rename, so the NEXT /connect by ANY user replaced the real file with an
empty one — every BYOK user's encrypted venue keys and every stored agent
private key, gone. There is no .bak anywhere in that module.

The catch included **OSError**, which is what moves this from exotic to
likely: a transient read failure — disk full, a permissions blip, EINTR — was
enough. No corruption required.

The old log line ("existing linked accounts will need to /connect again")
shows the data loss was ANTICIPATED. What was not anticipated is that the
file would be overwritten, which is what makes it unrecoverable rather than
an inconvenience.

    AN UNREADABLE STORE IS NOT AN EMPTY STORE, AND THE DIFFERENCE IS EVERY
    KEY IN IT.

Three changes, in order of what they protect:

  _save REFUSES while the load failed. This alone stops the destruction.
  The damaged file is RENAMED to .corrupt before anything can touch it.
  The log is CRITICAL, not error, and says the store is being protected.

A missing file is still legitimately empty — first boot — and still saves.
That distinction is the whole point: absent is not the same as unreadable.
"""
from __future__ import annotations

import json
import os

import pytest

from bot.core.exchange_credentials import ExchangeCredentialStore


def _store(tmp_path, name="creds.json"):
    return ExchangeCredentialStore(
        creds_file=str(tmp_path / name), key_file=str(tmp_path / "key.bin"))


class TestAFirstBootIsStillEmpty:
    """Absent must keep working, or the fix costs every new deployment."""

    def test_a_missing_file_loads_empty_and_can_save(self, tmp_path):
        s = _store(tmp_path)
        assert s._enc == {}
        assert s._load_failed is False
        s._enc = {"1": {"venue": "bitget", "fields": {}}}
        s._save()
        assert json.loads((tmp_path / "creds.json").read_text())["1"]

    def test_a_legitimately_empty_file_is_not_a_failure(self, tmp_path):
        (tmp_path / "creds.json").write_text("{}", encoding="utf-8")
        s = _store(tmp_path)
        assert s._load_failed is False
        s._save()          # must not raise


class TestAnUnreadableStoreIsNeverOverwritten:
    def test_corrupt_json_blocks_the_save(self, tmp_path):
        (tmp_path / "creds.json").write_text("{not json", encoding="utf-8")
        s = _store(tmp_path)
        assert s._load_failed is True
        with pytest.raises(RuntimeError):
            s._save()

    def test_the_original_bytes_are_preserved(self, tmp_path):
        (tmp_path / "creds.json").write_text("{not json BUT REAL", encoding="utf-8")
        _store(tmp_path)
        kept = tmp_path / "creds.json.corrupt"
        assert kept.exists(), "the damaged file must be recoverable by hand"
        assert "BUT REAL" in kept.read_text()

    def test_an_os_error_is_treated_the_same_as_corruption(self, tmp_path, monkeypatch):
        # THE LIKELY CASE. Disk full, a permissions blip, EINTR — no
        # corruption needed, and the old code lost every key just the same.
        (tmp_path / "creds.json").write_text('{"1": {"fields": {}}}',
                                             encoding="utf-8")
        real_open = open

        def boom(path, *a, **kw):
            if str(path).endswith("creds.json"):
                raise OSError(28, "No space left on device")
            return real_open(path, *a, **kw)

        monkeypatch.setattr("builtins.open", boom)
        s = _store(tmp_path)
        monkeypatch.undo()
        assert s._load_failed is True
        with pytest.raises(RuntimeError):
            s._save()

    def test_the_real_file_still_holds_its_keys_after_a_blocked_save(self, tmp_path):
        """The property that matters: the bytes survive the whole episode."""
        original = '{"777": {"venue": "bitget", "fields": {"apiKey": "CT"}}}'
        (tmp_path / "creds.json").write_text(original, encoding="utf-8")

        # A load that fails for a transient reason.
        real_open = open
        import builtins
        def boom(path, *a, **kw):
            if str(path).endswith("creds.json"):
                raise OSError(13, "Permission denied")
            return real_open(path, *a, **kw)
        builtins.open = boom
        try:
            s = _store(tmp_path)
        finally:
            builtins.open = real_open

        with pytest.raises(RuntimeError):
            s._save()
        # Preserved under .corrupt rather than replaced by "{}".
        kept = tmp_path / "creds.json.corrupt"
        assert kept.exists() and "CT" in kept.read_text()
        assert not (tmp_path / "creds.json").exists() or \
            (tmp_path / "creds.json").read_text() != "{}"

    def test_an_existing_corrupt_sidecar_is_not_clobbered(self, tmp_path):
        # A second failure must not overwrite the FIRST rescue — that would
        # lose the good copy on the second restart.
        (tmp_path / "creds.json.corrupt").write_text("FIRST RESCUE",
                                                     encoding="utf-8")
        (tmp_path / "creds.json").write_text("{bad again", encoding="utf-8")
        _store(tmp_path)
        assert "FIRST RESCUE" in (tmp_path / "creds.json.corrupt").read_text()


class TestEveryWriterIsCovered:
    """_save is the single chokepoint — every mutation must go through it."""

    def test_all_mutations_route_through_save(self):
        from tests.source_scan import code_only
        src = code_only(
            open("bot/core/exchange_credentials.py", encoding="utf-8").read())
        assert src.count("self._save()") >= 4, (
            "a mutation that writes the file directly would bypass the guard"
        )
        # No direct write of the creds path outside _save.
        i = src.index("def _save(")
        j = src.index("\n    def ", i + 10)
        body = src[i:j]
        assert "json.dump" in body
        assert src.count("json.dump") == 1, (
            "json.dump outside _save would be an unguarded write path"
        )

    def test_the_guard_is_checked_before_any_filesystem_work(self):
        from tests.source_scan import code_only
        src = code_only(
            open("bot/core/exchange_credentials.py", encoding="utf-8").read())
        i = src.index("def _save(")
        body = src[i:i + 700]
        guard = body.index("_load_failed")
        mkdir = body.index("mkdir")
        assert guard < mkdir, (
            "the refusal must come before the store is touched at all"
        )
