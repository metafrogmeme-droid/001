"""Load-file scheduling that does not stall on the last test of a file.

pytest-xdist 3.8.0 ``LoadFileScheduling`` (a ``LoadScopeScheduling``) hands a
worker one file at a time and refills only when that worker's pending *test*
count drops to 2. The worker will not run the test it has already been given
until the next index, or ``SHUTDOWN``, is sitting in its queue
(``xdist/remote.py`` ``WorkerInteractor.run_one_test`` prefetches before it
calls the test). A file of more than two tests therefore leaves a one-test
gap: the worker blocks in ``TestQueue.get`` on the last test of the file, and
the completion that would queue the next file is the event the blocked worker
never sends. The controller then sits in ``dsession.loop_once`` until
something else kills the job. The workers are still alive.

That is the stall in pytest-dev/pytest-xdist#1313, and it is the shadow job on
main: when the race is missed the suite finishes in about fifteen minutes
(run 2056, 892s, four workers); otherwise the job sits at its timeout with
python3 workers still in the tree and no pytest summary, because
``ci_test_gate`` only prints the captured output after pytest exits.

The fix keeps loadfile — one module stays on one worker — and keeps a second
file queued whenever another file exists, so the prefetch has something to
take. When no file remains, the worker is told to shut down while it still
has tests queued, which is what lets it run the last one.
"""
from __future__ import annotations

#: A worker with two files still open cannot be blocked on the last test of
#: the earlier one: the later file's indexes are already in its queue.
_FILES_AHEAD = 2


def incomplete_files(workload: dict) -> int:
    """How many assigned files still have a test that has not finished.

    A file whose tests are all done does not count. The worker has already
    moved on, and the queue slot it occupied is free.
    """
    pending = 0
    for tests in workload.values():
        if not isinstance(tests, dict):
            continue
        if any(done is False for done in tests.values()):
            pending += 1
    return pending


def prefetch_decision(*, incomplete: int, work_left: bool, shutting_down: bool) -> str:
    """What to tell one worker: ``shutdown``, ``assign``, or ``wait``.

    ``shutdown`` is how the last test of the last file gets to run. The worker
    is blocked until that message arrives, so leaving it unsent is the hang.
    """
    if shutting_down:
        return "wait"
    if not work_left:
        return "shutdown"
    if incomplete >= _FILES_AHEAD:
        return "wait"
    return "assign"


_PREFETCHING = None


def prefetching_loadfile_scheduler():
    """The loadfile scheduler class, built only when pytest-xdist is installed.

    The serial gate does not install pytest-xdist. Importing it at module
    import would make every test that loads this file fail that gate.
    The class is built once: a second call must be the same type the hook
    already handed the controller.
    """
    global _PREFETCHING
    if _PREFETCHING is not None:
        return _PREFETCHING
    from xdist.scheduler.loadfile import LoadFileScheduling

    class PrefetchingLoadFileScheduling(LoadFileScheduling):
        """``LoadFileScheduling`` that keeps a second file queued."""

        def _reschedule(self, node) -> None:
            workload = self.assigned_work.get(node) or {}
            decision = prefetch_decision(
                incomplete=incomplete_files(workload),
                work_left=bool(self.workqueue),
                shutting_down=bool(node.shutting_down),
            )
            if decision == "shutdown":
                node.shutdown()
                return
            if decision == "assign":
                self._assign_work_unit(node)

    _PREFETCHING = PrefetchingLoadFileScheduling
    return _PREFETCHING
