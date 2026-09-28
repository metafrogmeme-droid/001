"""A halted engine one test built does not refuse the next test's orders.

`RuneClawEngine.__init__` wires the executor's module-level halt check to a
closure over itself, and nothing unwires it. The last engine a test builds
therefore answers "halted?" for every `LiveExecutor` after it:
`test_the_bridge_is_a_reader_of_the_bots_state` builds one whose restored
breaker is tripped, and eight `test_the_placed_order_is_the_checked_order`
cases were refused as halted in a grouped run and passed alone.

`tests/conftest.py::_contain_executor_halt_check` hands the check back after
every test. The first two tests are a pair, in definition order, which is the
order pytest runs a module in: the first leaks what an engine's constructor
leaks, and the second reads what it inherited. Run alone, the second proves
nothing, which is why the third reads the harness's fixture list.
"""
from __future__ import annotations

import bot.core.live_executor as le


def test_1_wires_a_halted_check_the_way_an_engine_constructor_does():
    le.set_halt_check(lambda: True)
    assert le.trading_halted() is True


def test_2_the_next_test_is_not_halted():
    assert le._HALT_CHECK is None
    assert le.trading_halted() is False


def test_3_the_containment_runs_for_every_test(request):
    """Autouse binds on the decorator, not the name, so the name alone is not
    the check: a fixture present in this list is one pytest set up here."""
    assert "_contain_executor_halt_check" in request.fixturenames


def test_4_a_check_a_test_set_is_still_in_force_inside_it():
    """The containment restores after the test, never during it: a test that
    wires a check reads its own check."""
    le.set_halt_check(lambda: False)
    assert le._HALT_CHECK is not None and le.trading_halted() is False
