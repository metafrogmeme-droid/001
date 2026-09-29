"""`PER_USER_MAX_FUNDS_USD` is read once, and a cap that cannot be read refuses.

The linked-account max-funds cap is the one knob `docs/LIVE_TESTING_READINESS.md`
tells an operator to SET. `_hard_cap_refusal` read it as
`float(os.environ.get("PER_USER_MAX_FUNDS_USD", "100"))` under
`except ValueError: 100.0`, and applied it under `per_user_cap > 0`. Driven
against the unfixed reader, a $60 order on a linked account the operator had
capped at $20:

    PER_USER_MAX_FUNDS_USD='20'      -> REFUSED
    PER_USER_MAX_FUNDS_USD='20 usd'  -> ALLOWED   (a typo read as the default $100)
    PER_USER_MAX_FUNDS_USD='$20'     -> ALLOWED
    PER_USER_MAX_FUNDS_USD='nan'     -> ALLOWED   (`nan > 0` is False: the cap disabled)
    PER_USER_MAX_FUNDS_USD='-5'      -> ALLOWED
    PER_USER_MAX_FUNDS_USD='inf'     -> ALLOWED   (a cap of infinity)

`size_bounds.per_user_funds_cap` is the one reading: unset or empty is the
default, `0` disables (documented in `.env.example`, pinned by
`test_per_user_max_funds`), and anything else that is not a finite
non-negative number is UNREAD with a reason. The refusal refuses a
linked-account order by name on an unread cap; the operator's own executor
never reads it, as before.
"""
from __future__ import annotations

import inspect
from unittest.mock import patch

import pytest

from bot.core import live_executor as le
from bot.core import size_bounds
from bot.core.live_executor import LiveExecutor
from tests.source_scan import code_only


def _ex(tmp_path, user_id="7"):
    return LiveExecutor(state_dir=str(tmp_path), user_id=user_id)


def _preflight(ex, size):
    audits: list[dict] = []
    with patch.object(le, "audit", lambda log, msg, **kw: audits.append({"message": msg, **kw})):
        err = ex._preflight_check(size, symbol="BTC/USDT:USDT")
    return err, [a for a in audits if a.get("action") == "per_user_cap"]


# ── the reading ─────────────────────────────────────────────────────────

class TestTheReading:

    @pytest.mark.parametrize("raw, usd", [("50", 50.0), (" 25.5 ", 25.5), ("1e3", 1000.0)])
    def test_a_figure_is_the_cap(self, raw, usd):
        assert size_bounds.per_user_funds_cap({"PER_USER_MAX_FUNDS_USD": raw}) == (usd, False, None)

    @pytest.mark.parametrize("env", [{}, {"PER_USER_MAX_FUNDS_USD": ""}, {"PER_USER_MAX_FUNDS_USD": "  "}],
                             ids=["unset", "empty", "blank"])
    def test_unset_or_empty_is_the_default(self, env):
        assert size_bounds.per_user_funds_cap(env) == (size_bounds.PER_USER_CAP_DEFAULT_USD, False, None)
        assert size_bounds.PER_USER_CAP_DEFAULT_USD == 100.0

    def test_zero_disables_as_documented(self):
        assert size_bounds.per_user_funds_cap({"PER_USER_MAX_FUNDS_USD": "0"}) == (None, True, None)
        assert size_bounds.per_user_funds_cap({"PER_USER_MAX_FUNDS_USD": "0.0"}) == (None, True, None)

    @pytest.mark.parametrize("raw, why", [
        ("20 usd", "not a number"), ("$20", "not a number"), ("abc", "not a number"),
        ("nan", "not finite"), ("inf", "not finite"), ("-inf", "not finite"),
        ("-5", "negative"), ("-0.01", "negative")])
    def test_anything_else_is_unread_with_its_reason(self, raw, why):
        cap = size_bounds.per_user_funds_cap({"PER_USER_MAX_FUNDS_USD": raw})
        assert cap == (None, False, why)

    def test_an_explicit_empty_env_is_not_the_process_env(self, monkeypatch):
        monkeypatch.setenv("PER_USER_MAX_FUNDS_USD", "abc")
        assert size_bounds.per_user_funds_cap({}).unread is None, "{} is an empty environment"
        assert size_bounds.per_user_funds_cap().unread == "not a number", "None is the process's"


# ── the refusal ─────────────────────────────────────────────────────────

class TestTheRefusal:

    @pytest.mark.parametrize("raw", ["20 usd", "$20", "nan", "-5", "inf"])
    def test_an_unreadable_cap_refuses_a_linked_account_by_name(self, tmp_path, monkeypatch, raw):
        monkeypatch.setenv("PER_USER_MAX_FUNDS_USD", raw)
        err, rows = _preflight(_ex(tmp_path), 60.0)
        assert err is not None and "PER_USER_MAX_FUNDS_USD could not be read" in err
        assert "refusing rather than reading an unreadable cap as the default $100" in err
        assert raw not in err, "the sentence names the reason, never the value"
        assert [r["result"] for r in rows] == ["UNREAD"]
        assert raw not in rows[0]["message"]

    def test_a_readable_cap_still_caps(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PER_USER_MAX_FUNDS_USD", "20")
        err, rows = _preflight(_ex(tmp_path), 60.0)
        assert err is not None and "max-funds cap $20.00" in err
        assert [r["result"] for r in rows] == ["BLOCKED"]
        err, rows = _preflight(_ex(tmp_path), 15.0)
        assert err is None and rows == []

    def test_zero_still_disables_and_unset_is_still_100(self, tmp_path, monkeypatch):
        """The total is what the cap bounds: $60 held plus a $50 order is $110
        against the default $100 (an order of $101 alone meets the flat
        per-trade bound first, which is the fixture the first draft wrote)."""
        from bot.core.live_executor import LivePosition

        def _held():
            return {"t1": LivePosition(trade_id="t1", symbol="ETH/USDT", direction="LONG",
                                       entry_price=4_000.0, quantity=0.075, cost_usd=60.0,
                                       leverage=5, stop_loss=3_600.0, take_profit=4_400.0,
                                       status="open")}
        monkeypatch.setenv("PER_USER_MAX_FUNDS_USD", "0")
        ex = _ex(tmp_path)
        ex._positions = _held()
        assert _preflight(ex, 50.0)[0] is None, "0 disables"
        monkeypatch.delenv("PER_USER_MAX_FUNDS_USD", raising=False)
        ex = _ex(tmp_path)
        ex._positions = _held()
        assert "max-funds cap $100.00" in _preflight(ex, 50.0)[0]
        assert _preflight(ex, 30.0)[0] is None

    def test_the_operators_own_executor_never_reads_it(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PER_USER_MAX_FUNDS_USD", "abc")
        err, rows = _preflight(_ex(tmp_path, user_id=None), 60.0)
        assert err is None and rows == []

    def test_the_refusal_asks_the_one_reading(self):
        src = code_only(inspect.getsource(LiveExecutor._hard_cap_refusal))
        assert "size_bounds.per_user_funds_cap()" in src
        assert "os.environ" not in src, "a second read of the variable is a second answer"
        assert "PER_USER_CAP_DEFAULT_USD" in src, "the sentence's default is the reading's"
