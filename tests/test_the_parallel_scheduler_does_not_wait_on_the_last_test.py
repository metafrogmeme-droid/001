"""The loadfile scheduler must not leave a worker blocked on its last test.

xdist's own scheduler refills when a worker has two tests left. The worker
will not run the test it is holding until another index, or shutdown, is
already queued. A file longer than that gap blocks in ``TestQueue.get``, and
the completion that would send the next file never arrives. These tests drive
the scheduler the shadow job installs.
"""
from __future__ import annotations

from collections import OrderedDict

import pytest

from tests.xdist_loadfile import incomplete_files, prefetch_decision


def test_a_finished_file_does_not_count_as_work_still_open():
    workload = {
        "tests/done.py": {"tests/done.py::test_a": True},
        "tests/open.py": {
            "tests/open.py::test_a": True,
            "tests/open.py::test_b": False,
        },
    }
    assert incomplete_files(workload) == 1
    assert incomplete_files({}) == 0


def test_no_further_file_means_shutdown_so_the_last_test_can_run():
    assert prefetch_decision(incomplete=10, work_left=False, shutting_down=False) == "shutdown"


def test_two_open_files_means_the_worker_already_has_a_prefetch():
    assert prefetch_decision(incomplete=2, work_left=True, shutting_down=False) == "wait"


def test_one_open_file_with_more_work_waiting_is_given_another_file():
    assert prefetch_decision(incomplete=1, work_left=True, shutting_down=False) == "assign"


def test_a_worker_already_shutting_down_is_left_alone():
    assert prefetch_decision(incomplete=0, work_left=True, shutting_down=True) == "wait"


class _Node:
    def __init__(self):
        self.shutting_down = False
        self.sent: list[list[int]] = []
        self.shut = False

    def shutdown(self):
        self.shutting_down = True
        self.shut = True

    def send_runtest_some(self, indices):
        self.sent.append(list(indices))


def _scheduler():
    pytest.importorskip("xdist")
    from tests.xdist_loadfile import prefetching_loadfile_scheduler

    cls = prefetching_loadfile_scheduler()
    sched = cls.__new__(cls)
    sched.workqueue = OrderedDict()
    sched.assigned_work = {}
    sched.registered_collections = {}
    return sched


def _arm(sched, node, assigned, queued):
    """``assigned`` and ``queued`` are scope -> [nodeid, ...], none finished."""
    collection: list[str] = []
    workload = {}
    for scope, nodeids in assigned.items():
        workload[scope] = {nodeid: False for nodeid in nodeids}
        collection.extend(nodeids)
    for scope, nodeids in queued.items():
        sched.workqueue[scope] = {nodeid: False for nodeid in nodeids}
        collection.extend(nodeids)
    sched.assigned_work[node] = workload
    sched.registered_collections[node] = collection


def test_a_long_file_is_given_the_next_file_before_it_runs_dry():
    """Ten pending tests used to look like 'enough' and the next file stayed
    unsent. The worker then blocked before the last of the ten."""
    sched = _scheduler()
    node = _Node()
    big = [f"tests/big.py::test_{i}" for i in range(10)]
    _arm(sched, node, {"tests/big.py": big}, {"tests/next.py": ["tests/next.py::test_a"]})
    sched._reschedule(node)
    assert node.sent == [[10]]
    assert "tests/next.py" not in sched.workqueue
    assert node.shut is False


def test_the_last_file_is_followed_by_shutdown_while_tests_remain():
    sched = _scheduler()
    node = _Node()
    big = [f"tests/big.py::test_{i}" for i in range(10)]
    _arm(sched, node, {"tests/big.py": big}, {})
    sched._reschedule(node)
    assert node.sent == []
    assert node.shut is True


def test_the_conftest_hook_returns_that_scheduler_for_loadfile():
    pytest.importorskip("xdist")
    import tests.conftest as conftest
    from tests.xdist_loadfile import prefetching_loadfile_scheduler

    class _Cfg:
        def getoption(self, name):
            assert name == "dist"
            return "loadfile"

        def getvalue(self, name):
            assert name == "tx"
            return ["popen"]

    class _Log:
        def __getattr__(self, name):
            return self

        def __call__(self, *args, **kwargs):
            return None

    sched = conftest.pytest_xdist_make_scheduler(_Cfg(), _Log())
    assert isinstance(sched, prefetching_loadfile_scheduler())

    class _Other(_Cfg):
        def getoption(self, name):
            return "load"

    assert conftest.pytest_xdist_make_scheduler(_Other(), _Log()) is None


def test_two_open_files_are_not_given_a_third():
    sched = _scheduler()
    node = _Node()
    _arm(
        sched,
        node,
        {
            "tests/a.py": ["tests/a.py::test_a"],
            "tests/b.py": ["tests/b.py::test_b"],
        },
        {"tests/c.py": ["tests/c.py::test_c"]},
    )
    sched._reschedule(node)
    assert node.sent == []
    assert "tests/c.py" in sched.workqueue
    assert node.shut is False
