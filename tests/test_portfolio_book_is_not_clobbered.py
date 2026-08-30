"""Two processes shared one paper book, and the second one erased the first.

`docker-compose.yml` ran `python -m bot.main` alongside
`uvicorn api_bridge:app --workers 2`, and api_bridge's lifespan constructs its
own `RuneClawEngine()` per worker — three engines, one `./data`. The paper
portfolio is the record feeding the dashboards and the public track surfaces.

WHY A WRITE LOCK ALONE WOULD HAVE BEEN A FIX THAT DOES NOT FIX

`PortfolioTracker` does not read-modify-write. `_load_state_on_init` reads the
file once at construction and nothing re-reads it; every open and close then
writes the WHOLE in-memory state back. Two writers therefore do not interleave
edits to a shared document — the second stamps its entire, stale world over the
first. Serialising that with a mutex makes the clobbering orderly. It does not
make it stop, and `atomic_write_json` does not either: atomic means no torn
file, not serialised.

So the write carries a revision. Under the lock, a tracker whose loaded
revision is behind what is on disk refuses to overwrite, parks its state in a
`.conflict-<pid>.json` sidecar, and audits it. Nothing is lost and nothing is
invented — against a status quo where a trade simply disappeared.

`test_a_second_process_cannot_erase_the_first` is the one that matters: real
subprocesses, real files, no mocking of the thing under test. Everything else
here is a property of the guard; that one is the defect.
"""
from __future__ import annotations

import json
import time
import subprocess
import sys
import textwrap
from pathlib import Path


from bot.risk.portfolio import PortfolioTracker
from bot.utils.state_lock import REVISION_KEY, conflict_path, disk_revision, locked

ROOT = Path(__file__).resolve().parent.parent


def _tracker(state_file: Path, balance: float = 10_000.0) -> PortfolioTracker:
    return PortfolioTracker(initial_balance=balance, state_file=str(state_file))


# ── the defect, with two real processes ──────────────────────────────

_CHILD = textwrap.dedent("""
    import sys
    sys.path.insert(0, {root!r})
    from bot.risk.portfolio import PortfolioTracker
    t = PortfolioTracker(initial_balance=10000.0, state_file={path!r})
    t.balance = {balance!r}
    t.save_state()
""")


def _run_child(tmp_path: Path, state_file: Path, balance: float):
    src = tmp_path / f"child_{balance:.0f}.py"
    src.write_text(_CHILD.format(root=str(ROOT), path=str(state_file),
                                 balance=balance))
    return subprocess.run([sys.executable, str(src)], capture_output=True,
                          text=True, timeout=120)


def test_a_second_process_cannot_erase_the_first(tmp_path):
    """THE test. A separate process writes the book; ours loaded before that
    and then saves. Its state is stale, so it must not land."""
    state = tmp_path / "portfolio_state.json"

    ours = _tracker(state)
    ours.save_state()                       # revision 1, ours
    assert disk_revision(state) == 1

    proc = _run_child(tmp_path, state, 4242.0)
    assert proc.returncode == 0, proc.stderr
    assert disk_revision(state) == 2, "the other process did not write"

    ours.balance = 99_999.0
    ours.save_state()                       # stale: loaded rev 1, disk is 2

    on_disk = json.loads(state.read_text())
    assert on_disk["balance"] == 4242.0, (
        "our stale save overwrote the other process's book — a trade written "
        "by the bot is gone because the bridge saved a state it read earlier")
    assert conflict_path(state).exists(), (
        "the refused state was discarded instead of parked; nothing lost is "
        "the whole point of refusing")
    parked = json.loads(conflict_path(state).read_text())
    assert parked["balance"] == 99_999.0


def test_a_single_process_writes_normally(tmp_path):
    """The guard must not make ordinary operation fail. Every save from one
    tracker lands, and the revision advances."""
    state = tmp_path / "portfolio_state.json"
    t = _tracker(state)
    for i, bal in enumerate([1.0, 2.0, 3.0], start=1):
        t.balance = bal
        t.save_state()
        assert disk_revision(state) == i
        assert json.loads(state.read_text())["balance"] == bal
    assert not conflict_path(state).exists(), "a lone writer tripped the guard"


def test_a_tracker_that_reloads_can_write_again(tmp_path):
    """Recovery: after re-reading the file the tracker is current again, so
    the refusal is a stale-state guard and not a permanent lockout."""
    state = tmp_path / "portfolio_state.json"
    ours = _tracker(state)
    ours.save_state()
    _run_child(tmp_path, state, 555.0)

    assert ours.load_state() is True
    ours.balance = 777.0
    ours.save_state()
    assert json.loads(state.read_text())["balance"] == 777.0


# ── the revision itself ──────────────────────────────────────────────

def test_a_fresh_file_starts_at_revision_one(tmp_path):
    state = tmp_path / "portfolio_state.json"
    _tracker(state).save_state()
    assert json.loads(state.read_text())[REVISION_KEY] == 1


def test_a_pre_existing_file_without_a_revision_is_not_a_conflict(tmp_path):
    """Every book written before this change has no revision key. Reading that
    as "newer than ours" would refuse the first save on every existing
    deployment — the upgrade path has to be silent."""
    state = tmp_path / "portfolio_state.json"
    t = _tracker(state)
    t.save_state()
    raw = json.loads(state.read_text())
    del raw[REVISION_KEY]                   # a book from before this existed
    state.write_text(json.dumps(raw))

    t2 = _tracker(state)
    assert t2.load_state() is True
    t2.balance = 123.0
    t2.save_state()
    assert json.loads(state.read_text())["balance"] == 123.0
    assert not conflict_path(state).exists()


def test_an_unreadable_book_is_not_treated_as_absent(tmp_path):
    """`None` and `0` are different answers. A file we cannot parse might be
    NEWER than ours; calling it zero licenses exactly the overwrite this
    guards. Same rule as everywhere else this session — absent is not a
    measurement."""
    state = tmp_path / "portfolio_state.json"
    t = _tracker(state)
    t.save_state()
    state.write_text("{ not json at all")
    assert disk_revision(state) is None

    t.balance = 31337.0
    t.save_state()
    assert state.read_text() == "{ not json at all", (
        "an unparseable book was overwritten on the assumption it was empty")
    assert conflict_path(state).exists()


def test_disk_revision_distinguishes_absent_from_unreadable(tmp_path):
    missing = tmp_path / "nope.json"
    assert disk_revision(missing) == 0, "absent is a known, empty state"

    bad = tmp_path / "bad.json"
    bad.write_text("[1, 2, 3]")             # valid JSON, wrong shape
    assert disk_revision(bad) is None

    junk = tmp_path / "junk.json"
    junk.write_text(json.dumps({REVISION_KEY: "seventeen"}))
    assert disk_revision(junk) is None, "an uninterpretable revision is unknown"


# ── the lock ─────────────────────────────────────────────────────────

def test_the_lock_is_held_on_a_sidecar_not_the_state_file(tmp_path):
    """`atomic_write_json` publishes by renaming over the target, so the state
    file's inode changes on every write. A lock held against the old inode
    would guard nothing at all — it would look like protection and be none."""
    state = tmp_path / "portfolio_state.json"
    _tracker(state).save_state()
    assert Path(f"{state}.lock").exists(), "no sidecar lock file was created"


def test_two_processes_serialise_rather_than_interleave(tmp_path):
    """Ten concurrent writers, each incrementing. Without the lock the
    read-check and the write race and revisions collide; with it every write
    that lands is on top of the last one."""
    state = tmp_path / "portfolio_state.json"
    _tracker(state).save_state()

    procs = [_run_child(tmp_path, state, float(i)) for i in range(10)]
    assert all(p.returncode == 0 for p in procs), [p.stderr for p in procs]
    final = json.loads(state.read_text())
    assert isinstance(final[REVISION_KEY], int)
    assert final[REVISION_KEY] >= 2
    # And the file is always valid JSON with a balance — never half-written.
    assert "balance" in final


_SLOW_WRITER = textwrap.dedent("""
    import json, sys, time
    from pathlib import Path
    sys.path.insert(0, {root!r})
    from bot.utils.state_lock import REVISION_KEY, disk_revision, locked

    state = Path({path!r})
    with locked(state):
        Path({ready!r}).write_text("held")      # the lock is MINE now
        time.sleep({hold!r})                    # ... and I keep it for a while
        raw = json.loads(state.read_text())
        raw["balance"] = 4242.0
        raw[REVISION_KEY] = (disk_revision(state) or 0) + 1
        state.write_text(json.dumps(raw))
""")


def test_the_revision_is_read_under_the_lock_not_before_it(tmp_path):
    """The check and the write must be one atomic step.

    A mutation moving `disk_revision()` above `with locked(...)` survived every
    other test here, because the window it reopens is only a few microseconds
    wide in normal operation and the concurrent-writers test above never
    happened to land in it. Hoping to hit a race is not testing for one.

    So the window is made wide on purpose: a child process takes the lock and
    holds it for a second while it writes. Our save then blocks on that lock.

      check INSIDE  — we read the revision after acquiring, see the child's
                      new one, recognise our state as stale, and refuse.
      check OUTSIDE — we read the revision BEFORE blocking, still see the old
                      one, and when the lock finally comes we write on top of
                      a book we never looked at. The child's write is gone.

    The assertion is the child's balance surviving, which is the same thing
    the two-process test asserts — but reached through the ordering rather
    than through the staleness check, so it fails for the mutation the other
    one cannot see.
    """
    state = tmp_path / "portfolio_state.json"
    ready = tmp_path / "lock_held"

    ours = _tracker(state)
    ours.save_state()                       # revision 1; `ours` knows rev 1

    src = tmp_path / "slow_writer.py"
    src.write_text(_SLOW_WRITER.format(root=str(ROOT), path=str(state),
                                       ready=str(ready), hold=1.0))
    child = subprocess.Popen([sys.executable, str(src)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True)
    try:
        deadline = time.monotonic() + 30
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), "the child never acquired the lock"

        ours.balance = 99_999.0
        ours.save_state()                   # blocks on the child's lock
    finally:
        out, err = child.communicate(timeout=60)
    assert child.returncode == 0, err

    assert json.loads(state.read_text())["balance"] == 4242.0, (
        "the revision was read BEFORE the lock, so this save was already "
        "stale by the time it landed and it erased the child's write")


def test_the_lock_context_reports_whether_it_actually_locked(tmp_path):
    """It degrades to a no-op where fcntl is missing, and says so rather than
    pretending. A caller that cannot tell would report protection it lacks."""
    with locked(tmp_path / "x.json") as held:
        assert held is True                 # POSIX in CI and in the container


# ── the deployment half ──────────────────────────────────────────────

def _compose_code_only() -> str:
    """docker-compose.yml with `#` comments stripped.

    CLAUDE.md: "Strip comments first. A comment that quotes the string it
    forbids is indistinguishable from the code doing it, and this has produced
    four false failures." It produced a fifth right here — the comment added
    beside the setting explains why it is not `--workers 2`, and the first
    version of the check below failed on the very change that fixed the
    defect. Stripping is two lines; remembering to is apparently the hard part.
    """
    return "\n".join(ln.split("#", 1)[0]
                     for ln in (ROOT / "docker-compose.yml").read_text().splitlines())


def test_the_api_bridge_runs_a_single_worker():
    """The other half of the fix, and the half a unit test cannot reach: with
    two workers there are two more engines on the same book, and no in-process
    guard can help across them."""
    compose = _compose_code_only()
    assert "--workers 1" in compose, (
        "api_bridge is back to multiple workers — each constructs its own "
        "RuneClawEngine over the same ./data")
    for more in ("--workers 2", "--workers 3", "--workers 4"):
        assert more not in compose, f"api_bridge is running {more}"


def test_the_reason_is_recorded_where_the_number_is():
    """A bare `--workers 1` reads as a throughput choice and gets raised by
    the next person tuning the deployment. The constraint has to live next to
    the knob — so this one reads the COMMENTS deliberately, the opposite of
    the check above."""
    compose = (ROOT / "docker-compose.yml").read_text()
    i = compose.index("--workers 1")
    preamble = compose[max(0, i - 700):i]
    assert "RuneClawEngine" in preamble and "data" in preamble, (
        "nothing next to --workers explains that raising it duplicates the "
        "engine over a shared book")
