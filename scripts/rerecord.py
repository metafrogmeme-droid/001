#!/usr/bin/env python3
"""Re-record every ratchet baseline in one step, and never bless a regression.

    python3 scripts/rerecord.py --check   # read every ratchet, write nothing
    python3 scripts/rerecord.py --all     # re-record every ratchet that moved

THE RATCHETS
------------
``scripts/ruff_gate.py``, ``scripts/mypy_gate.py``, ``scripts/honesty_gate.py``
and the JS honesty ratchet (``app/test/js_honesty_ratchet.test.js``). Each is a
count that may only go down, and a count that goes down is re-recorded in the
same commit. That used to be four commands, run by hand, in whatever order a
branch remembered.

WHY IT REFUSES
--------------
Re-recording a baseline is how an improvement is banked, and it is equally how
a regression would be blessed: ``--update`` writes whatever it measures. So
``--all`` reads every ratchet FIRST, and while any of them reports growth (a
new finding) it re-records nothing at all and exits 1. The growth reading is
the gates' own (``ratchet_baseline.compare``; for the JS ratchet, its own
"no new honesty shapes" test), not a second copy of it here, and what a Python
ratchet records is exactly the counts that were just compared -- never a second
analyser run that could see a different tree. Every write is then checked
against the file it replaced, and a write that raised a count is undone.

A ratchet that could not be read -- a toolchain that is not the pinned one, an
analyser that cannot be started, a baseline that does not parse, a rule set
the honesty baseline was not recorded under, or any exception out of its
reading -- is left untouched and named (exit 2); its counts are not comparable,
so re-recording it would be the blessing this command exists to refuse. The
others are still re-recorded. A baseline that only STORES A TOTAL is not one
of those: its counts are read and compared like any other, and re-recording is
exactly what drops the total (``scripts/ratchet_baseline.py`` says why no
baseline stores one).

EXIT
----
    0  --check: every ratchet matches its baseline.
       --all:   everything that moved was re-recorded, and nothing grew.
    1  a ratchet GREW (nothing was re-recorded); or --check found an
       improvement not yet recorded; or a write was refused and undone.
    2  a ratchet could not be checked, or --check found a baseline that still
       stores a total. The gates' own could-not-check code.
"""
from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import ratchet_baseline as rb

ROOT = Path(__file__).resolve().parent.parent

MATCHED, IMPROVED, GREW, CANNOT_CHECK = "matched", "improved", "GREW", "cannot check"


@dataclass
class Reading:
    """One ratchet, read: its state and what moved, and what a write records."""

    gate: str
    state: str
    grew: list = field(default_factory=list)
    shrank: list = field(default_factory=list)
    now_total: int | None = None
    base_total: int | None = None
    #: Whether the baseline still STORES a total (a JSON null included: the
    #: gates refuse the key, whatever it holds), and the figure it stores.
    stores_total: bool = False
    stored_total: Any = None
    detail: str = ""
    #: What the ratchet's writer records: exactly what was measured.
    payload: Any = None


@dataclass
class Ratchet:
    name: str
    baseline: Path
    depth: int
    read: Callable[[], Reading]
    write: Callable[[Any], None]
    #: False only for the JS baseline, which is out of this change's scope and
    #: still records a total (its own test requires it to equal the sum of its
    #: counts; nothing here refuses or drops it).
    derives_total: bool = True


def reading_from_counts(gate: str, baseline: Path, depth: int, now: Any,
                        payload: Any, check_record: Callable[[dict], str | None]
                        = lambda _r: None) -> Reading:
    """Compare measured counts with a baseline file, with the gates' reading.

    A stored total does not stop the comparison -- the counts are the facts --
    but it is carried on the reading, so ``--check`` can refuse it the way the
    gate does and ``--all`` knows the file has to be rewritten.
    """
    if not baseline.exists():
        return Reading(gate, CANNOT_CHECK, detail=f"no {baseline.name}; create it "
                       "with the gate's own --update")
    try:
        record = rb.read_record(baseline)
    except rb.BaselineUnreadable as exc:
        return Reading(gate, CANNOT_CHECK, detail=f"{baseline.name}: {exc}")
    refusal = check_record(record)
    if refusal:
        return Reading(gate, CANNOT_CHECK, detail=refusal)
    try:
        base_total = sum(rb.counts_table(record, depth).values())
        grew, shrank = rb.compare(now, record["counts"], depth)
        now_total = rb.summed(now, depth)
    except rb.BaselineUnreadable as exc:
        return Reading(gate, CANNOT_CHECK, detail=f"{baseline.name}: {exc}")
    state = GREW if grew else IMPROVED if shrank else MATCHED
    return Reading(gate, state, grew=grew, shrank=shrank, now_total=now_total,
                   base_total=base_total, stores_total="total" in record,
                   stored_total=record.get("total"), payload=payload)


# ── the four ratchets ────────────────────────────────────────────────────────

def _python_reading(gate: str, measure: Callable[[], tuple[Any, Any]],
                    baseline: Path, depth: int,
                    check_record: Callable[[dict], str | None] = lambda _r: None
                    ) -> Reading:
    try:
        now, payload = measure()
    except SystemExit as exc:
        # check_version and current_counts leave by SystemExit(2) and have
        # already said why on stderr.
        return Reading(gate, CANNOT_CHECK, detail="its analyser could not be "
                       f"run as pinned (exit {exc.code}; the reason is above)")
    return reading_from_counts(gate, baseline, depth, now, payload, check_record)


def _ruff() -> Ratchet:
    import ruff_gate as g

    def measure():
        g.check_version("ruff")
        counts = g.current_counts()
        return counts, counts

    return Ratchet("ruff", g.BASELINE, 1,
                   read=lambda: _python_reading("ruff", measure, g.BASELINE, 1),
                   write=lambda counts: g.record(counts))


def _mypy() -> Ratchet:
    import mypy_gate as g

    def measure():
        g.check_version("mypy")
        counts, _files = g.current_counts()
        return counts, counts

    return Ratchet("mypy", g.BASELINE, 1,
                   read=lambda: _python_reading("mypy", measure, g.BASELINE, 1),
                   write=lambda counts: g.record(counts))


def _honesty() -> Ratchet:
    import honesty_gate as g

    def measure():
        counts = g.counts_from(g.scan())
        return counts, (counts, g._rules_fingerprint())

    def check_record(record: dict) -> str | None:
        recorded, fp = record.get("rules_fingerprint"), g._rules_fingerprint()
        if recorded == fp:
            return None
        return (f"the baseline was recorded by a different rule set ({recorded}) "
                f"and the analyser is {fp}, so its counts are not comparable. "
                "Read the new hits (--list) and re-record deliberately with "
                "python3 scripts/honesty_gate.py --update")

    return Ratchet("honesty", g.BASELINE, g.DEPTH,
                   read=lambda: _python_reading("honesty", measure, g.BASELINE,
                                                g.DEPTH, check_record),
                   write=lambda payload: g.record(*payload))


JS_TEST = ROOT / "app" / "test" / "js_honesty_ratchet.test.js"
JS_UPDATE = ROOT / "app" / "test" / "update_js_honesty_baseline.js"
JS_BASELINE = ROOT / "app" / "test" / "js_honesty_baseline.json"

#: The JS ratchet's three verdicts, by the test names that carry them.
JS_VERDICTS = {
    "fingerprint": "the rule set matches the one the baseline was recorded under",
    "grew": "no new honesty shapes in the JS surfaces",
    "improved": "an improvement is re-recorded in the same commit",
}

_TAP = re.compile(r"^(not ok|ok) \d+ - (.*?)(?:\s+#\s*(\w+).*)?$")


def classify_js_tap(tap: str) -> Reading:
    """The JS ratchet's reading, from its own tests' TAP lines.

    A verdict is read only from a top-level ``ok``/``not ok`` line carrying its
    exact name. A name that is absent -- renamed, or filtered out, which Node
    reports as ONE passing test for the whole file -- or a verdict marked SKIP
    or TODO is not a pass: this cannot say whether it grew.
    """
    seen: dict[str, bool] = {}
    for line in tap.splitlines():
        m = _TAP.match(line)
        if not m:
            continue
        name, directive = m.group(2).strip(), (m.group(3) or "").upper()
        if directive in ("SKIP", "TODO"):
            continue
        seen[name] = m.group(1) == "ok"
    missing = [n for n in JS_VERDICTS.values() if n not in seen]
    if missing:
        return Reading("js-honesty", CANNOT_CHECK, detail="the JS ratchet did not "
                       f"report {missing[0]!r}, so this cannot say whether it grew")
    if not seen[JS_VERDICTS["fingerprint"]]:
        return Reading("js-honesty", CANNOT_CHECK, detail="the JS baseline was "
                       "recorded by a different rule set; re-record it "
                       "deliberately with node app/test/update_js_honesty_baseline.js")
    if not seen[JS_VERDICTS["grew"]]:
        return Reading("js-honesty", GREW, detail="new shapes; the list: "
                       "node --test app/test/js_honesty_ratchet.test.js")
    if not seen[JS_VERDICTS["improved"]]:
        return Reading("js-honesty", IMPROVED)
    return Reading("js-honesty", MATCHED)


def _js() -> Ratchet:
    def read() -> Reading:
        try:
            proc = subprocess.run(
                ["node", "--test", "--test-reporter=tap", str(JS_TEST)],
                capture_output=True, text=True, cwd=ROOT)
        except OSError as exc:
            return Reading("js-honesty", CANNOT_CHECK,
                           detail=f"node could not be run ({type(exc).__name__})")
        return classify_js_tap(proc.stdout)

    def write(_payload: Any) -> None:
        subprocess.run(["node", str(JS_UPDATE)], check=True, cwd=ROOT,
                       capture_output=True, text=True)

    # The JS baseline still records a total (out of this change's scope), so
    # a total in it is neither refused nor dropped here.
    return Ratchet("js-honesty", JS_BASELINE, 2, read=read, write=write,
                   derives_total=False)


def ratchets() -> list[Ratchet]:
    return [_ruff(), _mypy(), _honesty(), _js()]


def read_one(ratchet: Ratchet) -> Reading:
    """One ratchet's reading. Anything its reading raises is CANNOT CHECK.

    An exception out of a reading is still a reading that did not happen.
    Escaping as a traceback it exited 1, which is "a ratchet GREW" in the
    table above, and the ratchets after it were never read. Named by class,
    not message: the message is not ours to print.
    """
    try:
        return ratchet.read()
    except Exception as exc:
        return Reading(ratchet.name, CANNOT_CHECK, detail="reading it raised "
                       f"{type(exc).__name__}, so no count was read")


# ── reporting ────────────────────────────────────────────────────────────────

def _key(path: tuple) -> str:
    return " ".join(path)


def _moves(rows: list, sign: str) -> list[str]:
    return [f"      {_key(k)}: {was} -> {now}  ({sign}{abs(now - was)})"
            for k, was, now in rows]


def describe(r: Reading) -> list[str]:
    if r.now_total is not None:
        head = f"  {r.gate:11} {r.state:12} {r.now_total} (baseline {r.base_total})"
    else:
        head = f"  {r.gate:11} {r.state}"
    out = [head]
    if r.detail:
        out.append(f"      {r.detail}")
    out += _moves(r.grew, "+") + _moves(r.shrank, "-")
    if r.stores_total:
        out.append(f"      stores a total ({r.stored_total!r}); the gate refuses it "
                   "as CANNOT CHECK until it is re-recorded")
    return out


# ── the two modes ────────────────────────────────────────────────────────────

def check(rs: list[Ratchet]) -> int:
    readings = [read_one(r) for r in rs]
    print("ratchets (nothing written):")
    for rd in readings:
        print("\n".join(describe(rd)))
    if any(rd.state in (GREW, IMPROVED) for rd in readings):
        return 1
    if any(rd.state == CANNOT_CHECK or rd.stores_total for rd in readings):
        return 2
    return 0


def _write(ratchet: Ratchet, reading: Reading) -> tuple[bool, list[str]]:
    """Record one ratchet, then check the file it wrote against the one it
    replaced. A write that raised any count, or that still stores a total,
    is undone."""
    path = ratchet.baseline
    old_bytes = path.read_bytes()
    old = rb.read_record(path)

    def undo(why: str) -> tuple[bool, list[str]]:
        path.write_bytes(old_bytes)
        return False, [f"  {ratchet.name}: REFUSED and undone -- {why}"]

    try:
        ratchet.write(reading.payload)
    except Exception as exc:  # a writer that failed leaves the old file
        return undo(f"the writer raised {type(exc).__name__}")
    try:
        new = rb.read_record(path)
        grew, shrank = rb.compare(new["counts"], old["counts"], ratchet.depth)
    except (rb.BaselineUnreadable, KeyError):
        return undo("the file it wrote does not read as a baseline")
    if grew:
        return undo("it would have raised " + ", ".join(
            f"{_key(k)} {was} -> {now}" for k, was, now in grew))
    if ratchet.derives_total and "total" in new:
        return undo("the file it wrote still stores a total")
    was = rb.summed(old["counts"], ratchet.depth)
    now = rb.summed(new["counts"], ratchet.depth)
    lines = [f"  {ratchet.name}: re-recorded, {was} -> {now}"]
    lines += _moves(shrank, "-")
    if ratchet.derives_total and "total" in old:
        lines.append(f"      dropped the stored total ({old['total']!r})")
    return True, lines


def rerecord_all(rs: list[Ratchet]) -> int:
    readings = [read_one(r) for r in rs]
    grown = [rd for rd in readings if rd.state == GREW]
    if grown:
        print("REFUSED: a ratchet grew, and re-recording would bless the "
              "regression. Nothing was re-recorded.")
        for rd in readings:
            print("\n".join(describe(rd)))
        return 1
    unread = [rd.gate for rd in readings if rd.state == CANNOT_CHECK]
    if unread:
        # Growth in a ratchet that was not read is unknown, not absent: the
        # header says what was read, and names what was not.
        print("re-recording what could be read (nothing that was read grew; "
              f"not read, so not known: {', '.join(unread)}):")
    else:
        print("re-recording (nothing grew):")
    failed = skipped = False
    for ratchet, rd in zip(rs, readings):
        if rd.state == CANNOT_CHECK:
            skipped = True
            print(f"  {ratchet.name}: NOT re-recorded -- could not be checked: "
                  f"{rd.detail}")
        elif rd.state == MATCHED and not rd.stores_total:
            print(f"  {ratchet.name}: unchanged")
        else:
            ok, lines = _write(ratchet, rd)
            failed |= not ok
            print("\n".join(lines))
    return 1 if failed else 2 if skipped else 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args == ["--check"]:
        return check(ratchets())
    if args == ["--all"]:
        return rerecord_all(ratchets())
    print(__doc__.split("THE RATCHETS")[0].strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
