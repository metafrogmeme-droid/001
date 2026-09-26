"""A test that switches logging off does not switch it off for the next one.

`logging.disable(N)` is process-wide and nothing undoes it.
`test_backtest_validity._run_once` calls `logging.disable(logging.WARNING)` and
never restores it, so every test collected after that file ran with WARNING and
below switched off until a later red-team drive happened to reset it. A test
asserting a warning was NOT logged passed there whatever the code did.

`tests/conftest.py::_contain_logging_disable` hands the setting back after every
test. The two tests below are a pair, in definition order, which is the order
pytest runs a module in: the first leaks exactly what the backtest suite leaks,
and the second reads what it inherited. Run alone, the second proves nothing,
which is why the third reads the harness's fixture list.
"""
from __future__ import annotations

import logging


def test_1_switches_logging_off_the_way_the_backtest_suite_does():
    logging.disable(logging.WARNING)
    assert not logging.getLogger("bot.anything").isEnabledFor(logging.WARNING)


def test_2_the_next_test_has_logging_back():
    assert logging.root.manager.disable == logging.NOTSET
    assert logging.getLogger("bot.anything").isEnabledFor(logging.WARNING)


def test_3_the_containment_runs_for_every_test(request):
    """Autouse binds on the decorator, not the name, so the name alone is not
    the check: a fixture present in this list is one pytest set up here."""
    assert "_contain_logging_disable" in request.fixturenames


def test_4_a_warning_asserted_absent_is_read(caplog):
    """The quiet direction, driven: with logging switched on, a warning that
    IS logged is seen, so an assertion that none was logged can fail."""
    logging.getLogger("bot.anything").warning("said")
    assert [r.getMessage() for r in caplog.records] == ["said"]
