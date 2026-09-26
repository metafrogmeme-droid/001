"""No new JSON store may read a failed read as empty and then write it back.

THE SHAPE, and the reason it is a ratchet rather than a list. A loader reads a
JSON file inside a ``try`` whose handler turns the failure into an empty map,
and a saver in the same scope writes the whole map back: the next write saves
``{}`` plus one row over a file that held every other user's rows. It was the
same few lines in twenty-eight places under ``bot/`` -- the per-user leverage,
strategy, memory and profile stores, the person-level drawdown peak, the
authority envelopes and their spend ledger, the venue selection, the secrets
vault, the free-chat quota, the learning record, the anchor record, the
leaderboards -- and fixing the ones somebody had named would have been the
`/setllm` ten-of-eleven shape: the store added tomorrow is the one missing
from the list.

`tests/json_store_writes.py` is the rule and this file drives it two ways
against `tests/json_store_baseline.txt`, the `known_failures.txt` rule: a site
the rule finds that is not listed fails, and a listed site the rule no longer
finds fails too, because a row kept after its reason is gone sits in front of
the next real one. A row with no reason fails as well: "decided harmless" and
"nobody decided" answer the same at runtime, so the decision is forced where
it can be read.

The cure is `bot/utils/json_store.py`, and it is not a writer here: its one
write (`update_json_store`) re-reads the file and refuses an unreadable one,
so a store converted to it has nothing left to pair.

The rule's branches are driven on PLANTED source below, because on the real
tree every site is listed and a mutation of the RULE changes no verdict there
-- a rule no input can reach is a claim that there is a check.
"""
from __future__ import annotations

import textwrap

import pytest

from tests.json_store_writes import REPO, keys, scan_source, scan_tree

BASELINE = REPO / "tests" / "json_store_baseline.txt"


def _rows(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        key, _, reason = line.partition("  ")
        out.append((key.strip(), reason.strip()))
    return out


def reasonless_rows(text: str) -> list[str]:
    """Every row that names a site and gives no reason for keeping it."""
    return [k for k, why in _rows(text) if not why]


def baseline() -> set[str]:
    return {k for k, _ in _rows(BASELINE.read_text(encoding="utf-8"))}


# ── the real tree, both ways ─────────────────────────────────────────────


def test_no_new_store_writes_over_a_failed_read():
    found = keys(scan_tree())
    unlisted = sorted(found - baseline())
    assert not unlisted, (
        "These loaders turn a failed read into an empty map in a scope that "
        "also writes the whole file back, so the next write can erase every "
        "row the file held:\n  " + "\n  ".join(unlisted) + "\n"
        "Read and write through bot/utils/json_store.py (a missing or empty "
        "file is a fresh start; a file that will not read is never written "
        "over), or add a row to tests/json_store_baseline.txt saying why this "
        "one cannot lose anything.")


def test_every_listed_site_is_still_there():
    stale = sorted(baseline() - keys(scan_tree()))
    assert not stale, (
        "These rows no longer match any site the rule finds. Delete them in "
        "the same commit that removed the shape:\n  " + "\n  ".join(stale))


def test_every_row_says_why():
    assert reasonless_rows(BASELINE.read_text(encoding="utf-8")) == []


def test_a_row_with_no_reason_is_refused():
    """Driven on planted lines: the real baseline has no reasonless row, so a
    mutation of the rule could not be seen from the file alone."""
    text = ("# a comment  with two spaces is not a row\n"
            "\n"
            "bot/a.py::A._load  A DERIVED ARTEFACT.\n"
            "bot/b.py::B._load\n"
            "bot/c.py::C._load   \n")
    assert reasonless_rows(text) == ["bot/b.py::B._load", "bot/c.py::C._load"]


# ── the rule, driven on planted source ───────────────────────────────────


def _scan(src: str) -> list[str]:
    return scan_source(textwrap.dedent(src))


_CLASS = """
    import json
    from bot.utils.atomic_write import atomic_write_json

    class S:
        def _load(self):
            try:
                self._d = json.loads(open(self._p).read())
            except {caught}:
{handler}

        def put(self, k, v):
            self._d[k] = v
            atomic_write_json(self._p, self._d)
    """


def _class(handler: str, caught: str = "Exception") -> str:
    body = textwrap.indent(textwrap.dedent(handler).strip(), " " * 16)
    return _CLASS.replace("{caught}", caught).replace("{handler}", body)


@pytest.mark.parametrize("handler,caught", [
    ("self._d = {}", "Exception"),
    ("self._d = dict()", "Exception"),
    ("self._d.clear()", "Exception"),
    ("self._d = None", "(OSError, ValueError)"),
    # A report is not a refusal: the logger is handed the error and the
    # write still happens.
    ("logger.warning('unreadable %s', str(e))\nself._d = {}", "Exception as e"),
    ("audit(system_log, 'unreadable')\nself._d = {}", "Exception"),
    ("pass", "Exception"),
])
def test_a_handler_that_swallows_is_found(handler, caught):
    assert _scan(_class(handler, caught)) == ["S._load"]


def test_a_bare_except_is_found():
    src = _CLASS.replace("except {caught}:", "except:").replace(
        "{handler}", " " * 16 + "self._d = {}")
    assert _scan(src) == ["S._load"]


@pytest.mark.parametrize("handler,caught,why", [
    ("raise", "Exception", "a handler that raises stops the write"),
    ("self._load_failed = True", "Exception",
     "a flag the saver reads is a refusal, not an empty map"),
    ("self._quarantine()", "Exception",
     "a call that is not a logger does something the rule cannot read"),
    ("return self._fallback", "Exception",
     "a non-empty return is not an empty map"),
    ("self._d = {}", "FileNotFoundError",
     "a missing file IS a fresh start"),
    ("if not self._p.exists():\n    self._d = {}\n    return\nraise",
     "Exception", "a refusal on one branch is a refusal"),
])
def test_a_handler_that_refuses_is_not(handler, caught, why):
    assert _scan(_class(handler, caught)) == [], why


def test_a_loader_with_no_writer_in_scope_is_not_found():
    src = _class("self._d = {}").replace(
        "atomic_write_json(self._p, self._d)", "return None")
    assert _scan(src) == []


@pytest.mark.parametrize("write,found", [
    ("atomic_write_json(self._p, self._d)", True),
    ("json.dump(self._d, open(self._p, 'w'))", True),
    ("self._p.write_text(json.dumps(self._d))", True),
    # `Path.open` takes its mode FIRST; the probe this rule grew from read it
    # second and called an append-only JSONL writer a whole write.
    ("with self._p.open('w') as fh:\n    json.dump(self._d, fh)", True),
    ("with self._p.open('a') as fh:\n    fh.write(json.dumps(self._d) + '\\n')",
     False),
    ("with open(self._p, 'a') as fh:\n    fh.write(json.dumps(self._d))", False),
    ("with open(self._p, mode='a') as fh:\n    json.dump(self._d, fh)", False),
    # json.dumps alone, handed to nothing that writes, is not a write.
    ("self._last = json.dumps(self._d)", False),
    # The cure re-reads and refuses; a store written through it has nothing
    # left to pair.
    ("update_json_store(self._p, lambda d: d.update(self._d))", False),
])
def test_what_counts_as_a_whole_write(write, found):
    body = textwrap.indent(textwrap.dedent(write).strip(), " " * 12).lstrip()
    src = _class("self._d = {}").replace(
        "atomic_write_json(self._p, self._d)", body)
    assert (_scan(src) == ["S._load"]) is found


def test_the_cures_own_read_in_a_swallowing_try_is_the_defect_again():
    src = _class("self._d = {}").replace(
        "json.loads(open(self._p).read())", "load_json_store(self._p)")
    assert _scan(src) == ["S._load"]


def test_a_class_that_saves_through_a_module_writer_is_paired():
    src = """
        import json

        def _save(path, d):
            with open(path, "w") as fh:
                json.dump(d, fh)

        class S:
            def _load(self):
                try:
                    self._d = json.load(open(self._p))
                except Exception:
                    self._d = {}

            def put(self):
                _save(self._p, self._d)
        """
    assert _scan(src) == ["S._load"]


def test_a_module_loader_is_paired_through_its_callers():
    """The free-chat quota's shape: `consume` read with `_load` and wrote
    with `_save`, and neither function holds both."""
    src = """
        import json

        def _load():
            try:
                return json.load(open(P))
            except Exception:
                return {}

        def _save(d):
            with open(P, "w") as fh:
                json.dump(d, fh)

        def _bump(d, uid):
            d[uid] = d.get(uid, 0) + 1
            _save(d)

        def consume(uid):
            _bump(_load(), uid)
        """
    assert _scan(src) == ["_load"]


def test_a_module_loader_nothing_pairs_with_a_writer_is_not_found():
    src = """
        import json

        def _load():
            try:
                return json.load(open(P))
            except Exception:
                return {}

        def _save(d):
            with open(P, "w") as fh:
                json.dump(d, fh)

        def read_only():
            return _load()

        def write_only(d):
            _save(d)
        """
    assert _scan(src) == []


_NESTED = """
    import json
    from bot.utils.atomic_write import atomic_write_json

    {opener}
        class S:
            def _load(self):
                try:
                    self._d = json.load(open(P))
                except Exception:
                    self._d = {}

            def put(self):
                atomic_write_json(P, self._d)
    {closer}
    """


@pytest.mark.parametrize("opener,closer", [
    ("class Outer:", ""),
    ("if True:", ""),
    ("try:", "except ImportError:\n        pass"),
])
def test_the_blind_spots_are_stated_and_pinned(opener, closer):
    """What the rule does NOT see, pinned so that widening it is a deliberate
    change rather than an accident: only the module's top-level defs and
    classes are read. `tests/json_store_writes.py` says so, and says that
    no site in `bot/` sits in one of these today."""
    src = _NESTED.replace("{opener}", opener).replace("{closer}", closer)
    assert _scan(src) == []


def test_a_def_nested_in_a_function_is_seen_and_charged_to_it():
    """The first draft of the rule's docstring listed a nested def as a blind
    spot; the pin written for it found the rule reading it."""
    src = """
        import json
        from bot.utils.atomic_write import atomic_write_json

        def make():
            def _load():
                try:
                    return json.load(open(P))
                except Exception:
                    return {}
            atomic_write_json(P, _load())
        """
    assert _scan(src) == ["make"]
