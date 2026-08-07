"""Handoff open item #6: 27 stores wrote through a fixed `.tmp` name.

`os.replace` is atomic and every call site checked that part. The scratch
file's NAME was a pure function of the destination, so two processes writing
the same store share one scratch file and the write that fills it interleaves.

Latent only because `api_bridge` runs single-process today —
`docker-compose.yml` and `Dockerfile` both already say `--workers 2`.

The tests below come in two kinds, deliberately:

  * a DETERMINISTIC proof of the mechanism, which does not depend on winning
    a race — the old shape's collision is provable by construction, because
    the name is a function of the destination and nothing else;
  * a threaded stress run for the end-to-end property, which would be
    worthless on its own (a race that happens not to fire looks exactly like
    a race that cannot).
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from bot.utils.atomic_write import (atomic_write_bytes, atomic_write_json,
                                    atomic_write_text)


def _old_shape_tmp(dest: str) -> str:
    """The shape being removed, reproduced so the defect is stated once."""
    return dest + ".tmp"


class TestTheScratchNameIsNotAFunctionOfTheDestination:
    """The whole defect in one property."""

    def test_the_old_shape_collides_by_construction(self, tmp_path):
        dest = str(tmp_path / "positions.json")
        # Two writers, two calls, one scratch file. No race needed to show it.
        assert _old_shape_tmp(dest) == _old_shape_tmp(dest)

    def test_the_helper_never_reuses_a_scratch_name(self, tmp_path, monkeypatch):
        dest = tmp_path / "positions.json"
        seen = []
        real_replace = os.replace

        def spy(src, dst):
            seen.append(str(src))
            return real_replace(src, dst)

        monkeypatch.setattr(os, "replace", spy)
        for i in range(25):
            atomic_write_json(dest, {"i": i})
        assert len(set(seen)) == 25, "a scratch name was reused"

    def test_the_scratch_file_stays_in_the_destination_directory(self, tmp_path, monkeypatch):
        """Same directory means same filesystem, which is the precondition
        for os.replace being atomic at all. A temp file in /tmp would cross
        a device boundary and degrade to a copy."""
        dest = tmp_path / "sub" / "positions.json"
        seen = []
        real_replace = os.replace
        monkeypatch.setattr(os, "replace",
                            lambda s, d: (seen.append(str(s)), real_replace(s, d))[1])
        atomic_write_json(dest, {"a": 1})
        assert Path(seen[0]).parent == dest.parent

    def test_the_scratch_name_points_back_at_its_destination(self, tmp_path, monkeypatch):
        # Litter from a hard kill has to be identifiable.
        dest = tmp_path / "positions.json"
        seen = []
        real_replace = os.replace
        monkeypatch.setattr(os, "replace",
                            lambda s, d: (seen.append(str(s)), real_replace(s, d))[1])
        atomic_write_json(dest, {"a": 1})
        assert "positions.json" in Path(seen[0]).name


class TestConcurrentWritersNeverPublishASplice:
    def test_threads_hammering_one_store_always_leave_valid_json(self, tmp_path):
        dest = tmp_path / "store.json"
        # Sizes far enough apart that a splice of the two is detectable as
        # either invalid JSON or a payload that is neither writer's.
        payloads = [{"who": "A", "pad": "a" * 200_000},
                    {"who": "B", "pad": "b" * 40}]
        errors: list = []
        barrier = threading.Barrier(len(payloads))

        def writer(obj):
            barrier.wait()
            for _ in range(30):
                try:
                    atomic_write_json(dest, obj, fsync=False)
                except Exception as exc:          # pragma: no cover
                    errors.append(exc)
                    return

        threads = [threading.Thread(target=writer, args=(p,)) for p in payloads]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, errors
        got = json.loads(dest.read_text(encoding="utf-8"))
        assert got in payloads, "the published file was neither writer's"

    def test_no_scratch_files_are_left_behind(self, tmp_path):
        dest = tmp_path / "store.json"
        for i in range(10):
            atomic_write_json(dest, {"i": i})
        leftovers = [p.name for p in tmp_path.iterdir() if p.name != "store.json"]
        assert leftovers == []


class TestAFailedWriteCleansUpAfterItself:
    """A fixed name self-overwrote, so a crashed write left one stale file.
    Unique names do not — without cleanup this leaks one file per attempt."""

    def test_a_serialization_failure_leaves_no_litter(self, tmp_path):
        dest = tmp_path / "store.json"

        class Unserializable:
            pass

        with pytest.raises(TypeError):
            atomic_write_json(dest, {"bad": Unserializable()}, default=None)
        assert list(tmp_path.iterdir()) == []

    def test_a_write_failure_leaves_no_litter(self, tmp_path, monkeypatch):
        dest = tmp_path / "store.json"
        monkeypatch.setattr(os, "replace",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
        with pytest.raises(OSError):
            atomic_write_json(dest, {"a": 1})
        assert list(tmp_path.iterdir()) == []

    def test_a_failure_does_not_destroy_the_previous_contents(self, tmp_path, monkeypatch):
        dest = tmp_path / "store.json"
        atomic_write_json(dest, {"good": True})
        monkeypatch.setattr(os, "replace",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
        with pytest.raises(OSError):
            atomic_write_json(dest, {"good": False})
        assert json.loads(dest.read_text(encoding="utf-8")) == {"good": True}


class TestMigrationDoesNotChangeTheFilesThemselves:
    """mkstemp creates 0600 and os.replace carries the temp file's mode onto
    the destination. A naive migration would quietly make every store private
    and break any reader running as another user."""

    def test_an_existing_files_mode_is_preserved(self, tmp_path):
        dest = tmp_path / "store.json"
        dest.write_text("{}", encoding="utf-8")
        os.chmod(dest, 0o644)
        atomic_write_json(dest, {"a": 1})
        assert oct(os.stat(dest).st_mode & 0o777) == oct(0o644)

    def test_a_tight_mode_is_preserved_too(self, tmp_path):
        dest = tmp_path / "vault.enc"
        dest.write_text("{}", encoding="utf-8")
        os.chmod(dest, 0o600)
        atomic_write_json(dest, {"a": 1})
        assert oct(os.stat(dest).st_mode & 0o777) == oct(0o600)

    def test_a_new_file_is_readable_like_open_w_would_have_made_it(self, tmp_path):
        dest = tmp_path / "new.json"
        atomic_write_json(dest, {"a": 1})
        assert oct(os.stat(dest).st_mode & 0o777) == oct(0o644)

    def test_an_explicit_mode_wins_for_secrets(self, tmp_path):
        dest = tmp_path / "vault.enc"
        atomic_write_json(dest, {"a": 1}, mode=0o600)
        assert oct(os.stat(dest).st_mode & 0o777) == oct(0o600)

    def test_json_bytes_match_json_dump_exactly(self, tmp_path):
        obj = {"b": 1, "a": [1, 2, {"c": None}]}
        dest = tmp_path / "a.json"
        atomic_write_json(dest, obj)
        expected = tmp_path / "b.json"
        with open(expected, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=2)
        assert dest.read_bytes() == expected.read_bytes()

    def test_a_compact_store_stays_compact(self, tmp_path):
        # Six stores write separators=(",", ":"). Defaulting indent on top of
        # that would reformat every one of them on first write.
        obj = {"b": 1, "a": [1, 2]}
        dest = tmp_path / "a.json"
        atomic_write_json(dest, obj, separators=(",", ":"))
        assert dest.read_text(encoding="utf-8") == json.dumps(
            obj, separators=(",", ":"))

    def test_default_str_is_not_applied_behind_the_callers_back(self, tmp_path):
        # A write that used to raise must keep raising, not store a repr.
        class Thing:
            pass
        with pytest.raises(TypeError):
            atomic_write_json(tmp_path / "a.json", {"x": Thing()})
        assert json.loads(
            (lambda p: (atomic_write_json(p, {"x": Thing()}, default=str),
                        p.read_text(encoding="utf-8"))[1])(tmp_path / "b.json")
        )["x"].startswith("<")

    def test_parent_directories_are_created(self, tmp_path):
        dest = tmp_path / "a" / "b" / "c.json"
        atomic_write_json(dest, {"a": 1})
        assert json.loads(dest.read_text(encoding="utf-8")) == {"a": 1}

    def test_text_and_bytes_round_trip(self, tmp_path):
        atomic_write_text(tmp_path / "t.txt", "héllo\n")
        assert (tmp_path / "t.txt").read_text(encoding="utf-8") == "héllo\n"
        atomic_write_bytes(tmp_path / "b.bin", b"\x00\x01\x02")
        assert (tmp_path / "b.bin").read_bytes() == b"\x00\x01\x02"

    def test_a_str_path_works_as_well_as_a_Path(self, tmp_path):
        atomic_write_json(str(tmp_path / "s.json"), {"a": 1})
        assert (tmp_path / "s.json").exists()


class TestTheShapeDoesNotComeBack:
    """27 sites converged on the same wrong idiom independently, which is what
    a missing helper looks like. Now that one exists, a 28th is a regression.

    This is a source scan and says so: the property ("no two processes share a
    scratch path") cannot be observed from inside a single test process for a
    call site that is never executed. Comments are stripped first — the module
    docstring of bot/utils/atomic_write.py quotes the forbidden shape in order
    to explain it, and a scan that could not tell those apart would fail on
    the documentation of its own rule.
    """

    FORBIDDEN = (
        '+ ".tmp"',            # path + ".tmp"
        '+ \'.tmp\'',
        'with_suffix(".tmp")',  # Path.with_suffix — collides just the same,
        "with_suffix('.tmp')",  # and silently eats the real extension
        '}.tmp"',              # f"{path}.tmp"
    )

    def _py_sources(self):
        for path in Path("bot").rglob("*.py"):
            if path.name == "atomic_write.py":
                continue          # the helper names the shape to explain it
            yield path

    def test_no_module_derives_a_scratch_name_from_its_destination(self):
        from tests.source_scan import code_only
        offenders = []
        for path in self._py_sources():
            src = code_only(path.read_text(encoding="utf-8"))
            for shape in self.FORBIDDEN:
                if shape in src:
                    offenders.append(f"{path}: {shape}")
        assert not offenders, (
            "use bot.utils.atomic_write instead:\n  " + "\n  ".join(offenders))

    def test_the_scan_can_actually_see_the_shape(self):
        """A scan that matches nothing passes for the wrong reason. Prove it
        fires on the exact code it is meant to reject."""
        from tests.source_scan import code_only
        sample = code_only('tmp = self._path + ".tmp"\n')
        assert any(shape in sample for shape in self.FORBIDDEN)

    def test_the_helper_is_actually_used(self):
        # A helper nothing imports answers nothing.
        users = [p for p in self._py_sources()
                 if "from bot.utils.atomic_write import" in
                 p.read_text(encoding="utf-8")]
        assert len(users) >= 20, f"only {len(users)} modules migrated"
