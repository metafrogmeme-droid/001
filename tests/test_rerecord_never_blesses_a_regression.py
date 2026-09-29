"""`scripts/rerecord.py`: every ratchet re-recorded in one step, and never a
regression blessed.

Re-recording a baseline is how an improvement is banked, and it is equally how
a new finding would be written down as the new floor: each gate's `--update`
records whatever it measures. So `--all` reads every ratchet first and, while
any of them reports growth, re-records NOTHING. What it then records for a
Python ratchet is exactly the counts it compared, never a second analyser run
that could see a different tree; and every write is checked against the file
it replaced, and undone if any count went up.

Driven on planted ratchets (a baseline file in tmp_path, the counts handed in)
and through the real registry's readers with the analyser substituted, for
the reason `test_lint_type_ratchets` gives: a whole-tree analyser inside a
suite that writes the tree measures a moving target.
"""
from __future__ import annotations

import importlib
import json
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import rerecord as rr  # noqa: E402


def _planted(tmp_path, name, base, now, *, depth=1, total=None, writer=None,
             derives_total=True, check_record=lambda _r: None):
    """A ratchet whose baseline is a file and whose measurement is `now`."""
    path = tmp_path / f"{name}.json"
    record = {"counts": base}
    if total is not None:
        record["total"] = total
    path.write_text(json.dumps(record), encoding="utf-8")

    def read():
        return rr.reading_from_counts(name, path, depth, now, now, check_record)

    def write(payload):
        path.write_text(json.dumps({"counts": payload}), encoding="utf-8")

    return rr.Ratchet(name, path, depth, read=read, write=writer or write,
                      derives_total=derives_total)


def _counts(ratchet) -> dict:
    return json.loads(ratchet.baseline.read_text(encoding="utf-8"))["counts"]


# ── --all refuses while anything grew ────────────────────────────────────────

def test_one_ratchet_that_grew_blocks_every_re_record(tmp_path, capsys):
    improved = _planted(tmp_path, "a", {"E501": 5}, {"E501": 3})
    grew = _planted(tmp_path, "b", {"F401": 1}, {"F401": 2})
    before = {r.name: r.baseline.read_bytes() for r in (improved, grew)}

    assert rr.rerecord_all([improved, grew]) == 1
    for r in (improved, grew):
        assert r.baseline.read_bytes() == before[r.name], (
            f"{r.name} was rewritten while another ratchet grew")
    out = capsys.readouterr().out
    assert "REFUSED" in out and "Nothing was re-recorded" in out
    assert "F401: 1 -> 2  (+1)" in out, out


def test_a_new_rule_is_growth(tmp_path):
    """A key absent from the baseline counts zero there."""
    r = _planted(tmp_path, "a", {"E501": 5}, {"E501": 5, "W291": 1})
    assert r.read().state == rr.GREW
    assert rr.rerecord_all([r]) == 1
    assert _counts(r) == {"E501": 5}


# ── --all re-records what moved, and says what moved ────────────────────────

def test_an_improvement_is_re_recorded_and_what_moved_is_printed(tmp_path, capsys):
    r = _planted(tmp_path, "ruff", {"E501": 5, "F401": 2}, {"E501": 3, "F401": 2})
    assert rr.rerecord_all([r]) == 0
    assert _counts(r) == {"E501": 3, "F401": 2}
    out = capsys.readouterr().out
    assert "ruff: re-recorded, 7 -> 5" in out, out
    assert "E501: 5 -> 3  (-2)" in out, out


def test_a_cleared_file_is_an_improvement(tmp_path):
    r = _planted(tmp_path, "honesty", {"s": {"a.py": 2, "b.py": 1}},
                 {"s": {"a.py": 2}}, depth=2)
    assert r.read().state == rr.IMPROVED
    assert rr.rerecord_all([r]) == 0
    assert _counts(r) == {"s": {"a.py": 2}}


def test_a_stored_total_is_dropped_when_nothing_moved(tmp_path, capsys):
    r = _planted(tmp_path, "mypy", {"arg-type": 4}, {"arg-type": 4}, total=4)
    assert rr.rerecord_all([r]) == 0
    written = json.loads(r.baseline.read_text(encoding="utf-8"))
    assert "total" not in written and written["counts"] == {"arg-type": 4}
    assert "dropped the stored total (4)" in capsys.readouterr().out


def test_a_stored_null_total_is_still_a_stored_total(tmp_path, capsys):
    """The gates refuse the KEY, whatever it holds; a null there must not read
    as "stores none" here, or --check says 0 while the gate says 2."""
    r = _planted(tmp_path, "ruff", {"E501": 5}, {"E501": 5})
    r.baseline.write_text(json.dumps({"total": None, "counts": {"E501": 5}}))
    assert rr.check([r]) == 2
    assert rr.rerecord_all([r]) == 0
    assert "total" not in json.loads(r.baseline.read_text(encoding="utf-8"))
    assert "dropped the stored total (None)" in capsys.readouterr().out


def test_a_matched_ratchet_is_left_alone(tmp_path, capsys):
    r = _planted(tmp_path, "ruff", {"E501": 5}, {"E501": 5})
    before = r.baseline.read_bytes()
    assert rr.rerecord_all([r]) == 0
    assert r.baseline.read_bytes() == before
    assert "ruff: unchanged" in capsys.readouterr().out


def test_what_cannot_be_checked_is_left_untouched_and_the_rest_re_recorded(
        tmp_path, capsys):
    unreadable = _planted(tmp_path, "bad", {"E501": True}, {"E501": 1})
    improved = _planted(tmp_path, "good", {"E501": 5}, {"E501": 3})
    before = unreadable.baseline.read_bytes()

    assert rr.rerecord_all([unreadable, improved]) == 2
    assert unreadable.baseline.read_bytes() == before
    assert _counts(improved) == {"E501": 3}
    out = capsys.readouterr().out
    assert "bad: NOT re-recorded -- could not be checked" in out, out


def test_a_record_the_ratchet_refuses_is_not_checkable(tmp_path):
    r = _planted(tmp_path, "honesty", {"E501": 5}, {"E501": 3},
                 check_record=lambda _r: "recorded by a different rule set")
    reading = r.read()
    assert reading.state == rr.CANNOT_CHECK
    assert "different rule set" in reading.detail


def test_a_missing_baseline_is_not_checkable(tmp_path):
    reading = rr.reading_from_counts("x", tmp_path / "nope.json", 1, {"E501": 1},
                                     None)
    assert reading.state == rr.CANNOT_CHECK and "nope.json" in reading.detail


# ── every write is checked against the file it replaced ─────────────────────

def test_a_write_that_raises_a_count_is_undone(tmp_path, capsys):
    """The JS writer rescans the tree; a tree that changed between the check
    and the write would otherwise record the growth."""
    r = _planted(tmp_path, "js", {"s": {"a.js": 2}}, {"s": {"a.js": 1}}, depth=2,
                 writer=lambda _p: (tmp_path / "js.json").write_text(
                     json.dumps({"counts": {"s": {"a.js": 3}}})))
    before = r.baseline.read_bytes()
    assert rr.rerecord_all([r]) == 1
    assert r.baseline.read_bytes() == before
    out = capsys.readouterr().out
    assert "REFUSED and undone" in out and "s a.js 2 -> 3" in out, out


def test_a_writer_that_still_stores_a_total_is_undone(tmp_path, capsys):
    r = _planted(tmp_path, "ruff", {"E501": 5}, {"E501": 3},
                 writer=lambda p: (tmp_path / "ruff.json").write_text(
                     json.dumps({"total": 3, "counts": p})))
    before = r.baseline.read_bytes()
    assert rr.rerecord_all([r]) == 1
    assert r.baseline.read_bytes() == before
    assert "still stores a total" in capsys.readouterr().out


def test_a_baseline_out_of_scope_may_keep_its_total(tmp_path):
    r = _planted(tmp_path, "js", {"E501": 5}, {"E501": 3}, derives_total=False,
                 writer=lambda p: (tmp_path / "js.json").write_text(
                     json.dumps({"total": 3, "counts": p})))
    assert rr.rerecord_all([r]) == 0


def test_a_writer_that_raises_leaves_the_old_file(tmp_path, capsys):
    def boom(_payload):
        (tmp_path / "ruff.json").write_text("half written")
        raise OSError("disk full")

    r = _planted(tmp_path, "ruff", {"E501": 5}, {"E501": 3}, writer=boom)
    before = r.baseline.read_bytes()
    assert rr.rerecord_all([r]) == 1
    assert r.baseline.read_bytes() == before
    out = capsys.readouterr().out
    assert "the writer raised OSError" in out and "disk full" not in out


# ── --check writes nothing ───────────────────────────────────────────────────

@pytest.mark.parametrize("base,now,total,want", [
    ({"E501": 5}, {"E501": 5}, None, 0),
    ({"E501": 5}, {"E501": 6}, None, 1),
    ({"E501": 5}, {"E501": 4}, None, 1),
    ({"E501": 5}, {"E501": 5}, 5, 2),
    ({"E501": "5"}, {"E501": 5}, None, 2),
])
def test_check_reports_and_writes_nothing(tmp_path, capsys, base, now, total, want):
    r = _planted(tmp_path, "ruff", base, now, total=total)
    before = r.baseline.read_bytes()
    assert rr.check([r]) == want
    assert r.baseline.read_bytes() == before
    assert "nothing written" in capsys.readouterr().out


def test_check_names_a_stored_total(tmp_path, capsys):
    r = _planted(tmp_path, "ruff", {"E501": 5}, {"E501": 5}, total=5)
    rr.check([r])
    assert "stores a total (5)" in capsys.readouterr().out


def test_growth_outranks_could_not_check(tmp_path):
    grew = _planted(tmp_path, "a", {"E501": 1}, {"E501": 2})
    bad = _planted(tmp_path, "b", {"E501": None}, {"E501": 1})
    assert rr.check([grew, bad]) == 1


def test_main_refuses_anything_but_one_mode(capsys):
    assert rr.main([]) == 2
    assert rr.main(["--all", "--check"]) == 2
    assert "--all" in capsys.readouterr().err


# ── the real registry reads each gate's own reading ─────────────────────────

def test_the_registry_holds_every_ratchet():
    names = [r.name for r in rr.ratchets()]
    assert names == ["ruff", "mypy", "honesty", "js-honesty"]
    by = {r.name: r for r in rr.ratchets()}
    for gate in ("ruff", "mypy", "honesty"):
        assert by[gate].baseline == importlib.import_module(f"{gate}_gate").BASELINE
        assert by[gate].derives_total
    assert by["honesty"].depth == importlib.import_module("honesty_gate").DEPTH
    assert by["js-honesty"].baseline == rr.JS_BASELINE and by["js-honesty"].depth == 2


def test_a_python_ratchet_records_exactly_what_it_compared(tmp_path, monkeypatch):
    """One analyser run: the counts written are the counts compared. A second
    run for the write could see a different tree and record its growth."""
    g = importlib.import_module("ruff_gate")
    path = tmp_path / "ruff_baseline.json"
    path.write_text(json.dumps({"total": 7, "counts": {"E501": 5, "F401": 2}}))
    runs = []

    def counts():
        runs.append(1)
        return Counter({"E501": 3, "F401": 2}) if len(runs) == 1 else Counter(
            {"E501": 9})

    monkeypatch.setattr(g, "BASELINE", path)
    monkeypatch.setattr(g, "check_version", lambda _t: None)
    monkeypatch.setattr(g, "current_counts", counts)
    ruff = next(r for r in rr.ratchets() if r.name == "ruff")

    assert rr.rerecord_all([ruff]) == 0
    assert len(runs) == 1, "the analyser ran again for the write"
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["counts"] == {"E501": 3, "F401": 2} and "total" not in written


def test_a_toolchain_mismatch_is_not_re_recorded(tmp_path, monkeypatch):
    g = importlib.import_module("mypy_gate")
    path = tmp_path / "mypy_baseline.json"
    path.write_text(json.dumps({"counts": {"arg-type": 5}}))

    def mismatch(_tool):
        raise SystemExit(2)

    monkeypatch.setattr(g, "BASELINE", path)
    monkeypatch.setattr(g, "check_version", mismatch)
    monkeypatch.setattr(g, "current_counts", lambda: (Counter(), 0))
    mypy = next(r for r in rr.ratchets() if r.name == "mypy")
    before = path.read_bytes()
    assert mypy.read().state == rr.CANNOT_CHECK
    assert rr.rerecord_all([mypy]) == 2
    assert path.read_bytes() == before


def test_the_honesty_ratchet_refuses_another_rule_set(tmp_path, monkeypatch):
    g = importlib.import_module("honesty_gate")
    path = tmp_path / "honesty_baseline.json"
    path.write_text(json.dumps({"rules_fingerprint": "0" * 16,
                                "counts": {"s": {"a.py": 5}}}))
    monkeypatch.setattr(g, "BASELINE", path)
    monkeypatch.setattr(g, "scan", lambda: [("s", "a.py", 1, "x")])
    honesty = next(r for r in rr.ratchets() if r.name == "honesty")
    reading = honesty.read()
    assert reading.state == rr.CANNOT_CHECK and "rule set" in reading.detail
    monkeypatch.setattr(g, "scan", lambda: [])
    path.write_text(json.dumps({"rules_fingerprint": g._rules_fingerprint(),
                                "total": 5, "counts": {"s": {"a.py": 5}}}))
    assert rr.rerecord_all([honesty]) == 0
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["counts"] == {} and "total" not in written


# ── the JS ratchet is read from its own tests' verdicts ─────────────────────

def _tap(fingerprint="ok", grew="ok", improved="ok", other="ok"):
    v = rr.JS_VERDICTS
    return "\n".join([
        "TAP version 13",
        f"# Subtest: {v['fingerprint']}",
        f"{fingerprint} 1 - {v['fingerprint']}",
        f"{grew} 2 - {v['grew']}",
        f"{improved} 3 - {v['improved']}",
        f"{other} 4 - the honest guard is not a hit",
        # A nested subtest line carrying a verdict's name is not the verdict:
        # only a top-level line is read.
        f"    not ok 1 - {v['grew']}",
        "# tests 4",
    ])


@pytest.mark.parametrize("tap,state", [
    (_tap(), rr.MATCHED),
    (_tap(other="not ok"), rr.MATCHED),
    (_tap(grew="not ok"), rr.GREW),
    (_tap(improved="not ok"), rr.IMPROVED),
    (_tap(grew="not ok", improved="not ok"), rr.GREW),
    (_tap(fingerprint="not ok", grew="not ok"), rr.CANNOT_CHECK),
])
def test_the_js_verdicts_are_read_from_the_tap(tap, state):
    assert rr.classify_js_tap(tap).state == state


@pytest.mark.parametrize("tap", [
    # Node reports ONE passing test for the whole file when a filter matches
    # nothing: an exit status of 0 and no verdict at all.
    "TAP version 13\nok 1 - app/test/js_honesty_ratchet.test.js\n# pass 1\n",
    "",
    _tap().replace(f"2 - {rr.JS_VERDICTS['grew']}",
                   f"2 - {rr.JS_VERDICTS['grew']} # SKIP"),
    _tap().replace(f"2 - {rr.JS_VERDICTS['grew']}",
                   f"2 - {rr.JS_VERDICTS['grew']} # TODO not yet"),
])
def test_a_verdict_that_was_not_reported_is_not_a_pass(tap):
    reading = rr.classify_js_tap(tap)
    assert reading.state == rr.CANNOT_CHECK, reading


def test_the_verdict_names_are_the_js_ratchets_own():
    """A rename in the JS test would otherwise leave this reading nothing --
    which it reports as CANNOT CHECK, and this says where to look."""
    src = rr.JS_TEST.read_text(encoding="utf-8")
    for name in rr.JS_VERDICTS.values():
        assert f"test('{name}'" in src, f"the JS ratchet has no test named {name!r}"
