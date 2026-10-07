"""A known unified account is unified when one leverage read fails.

The pre-order check started every order at `_uta_account = False` and turned
it True only on this call's own 40085. On an account the hold-mode probe had
already recorded as unified (`_is_uta True`), a timeout or a 429 from
`fetch_leverage` was a classic verdict: the sticky position row refused the
order and the symbol row, the one that states the fill, was never asked.
The other way round, a 40085 caught here did not reach `_is_uta`, so the
post-fill guard read the position row this check had declined to read.
"""
from tests.leverage_drive import drive_ensure_leverage
from tests.test_the_sync_reads_the_row_the_guard_reads import (
    ORDER_MODE,
    _drive_settings,
    _held_row,
)


def _drive(reading, monkeypatch, *, is_uta, symbol_row="5"):
    return drive_ensure_leverage(
        [reading], positions=[_held_row(20)], target=5, margin_mode=ORDER_MODE,
        side="long", uta_settings=_drive_settings(symbol_row), is_uta=is_uta,
        monkeypatch=monkeypatch)


def test_a_timeout_on_a_probed_unified_account_reads_the_symbol_row(monkeypatch):
    out = _drive(TimeoutError("read timed out"), monkeypatch, is_uta=True)
    assert out.aborted is False, out.why
    assert out.position_reads == 0
    assert out.uta_reads == 1


def test_the_symbol_row_still_refuses_an_overshoot_there(monkeypatch):
    out = _drive(TimeoutError("read timed out"), monkeypatch, is_uta=True, symbol_row="20")
    assert out.aborted is True
    assert "symbol config at 20x" in out.why
    assert out.position_reads == 0


def test_an_unprobed_or_classic_account_still_reads_the_position_row(monkeypatch):
    for is_uta in (None, False):
        out = _drive(TimeoutError("read timed out"), monkeypatch, is_uta=is_uta)
        assert out.position_reads == 1, is_uta
        assert out.uta_reads == 0, is_uta
        assert out.aborted is True and "position at 20x" in out.why


def test_a_40085_here_is_recorded_for_the_guard_after_the_fill(monkeypatch):
    out = _drive(Exception("40085 unified account"), monkeypatch, is_uta=None)
    assert out.aborted is False, out.why
    assert out.executor._is_uta is True
    # And a read that failed for another reason records nothing.
    out = _drive(TimeoutError("read timed out"), monkeypatch, is_uta=None)
    assert getattr(out.executor, "_is_uta", None) is None
