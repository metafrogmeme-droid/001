"""An unreadable free margin must not be spent as if it were measured zero.

RC-2026-017, SAFE variant.

`LiveExecutor.fetch_balance` builds its success return as::

    usdt = balance.get(self._venue.balance_coin, {})
    ...
    "free": float(usdt.get("free", 0)),

`usdt` is `{}` whenever the balance-coin entry is absent -- a USDC-margined
venue (`venues.py` sets `balance_coin = "USDC"` for two of them), a response
shape ccxt parses differently, or a `fetch_balance()` issued without the params
some venues require. So `free` was minted as `0.0` while `total` came from the
raw equity and stayed correct: a payload reading "you have $512 of equity and
$0 of it available".

The engine then clamped against it::

    available = live_bal.get("free", 0.0)
    if size_usd > available:
        audit(... f"Live size clamped: ${size_usd:.2f} -> ${available:.2f} (exchange available)")
        size_usd = available

so every live order was sized at $0 -- rejected by the venue, and read by the
operator as an exchange fault -- and the reason sealed into the tamper-evident
audit chain was a dollar figure with the word "exchange" beside it, for a
number nobody measured.

WHY REFUSE RATHER THAN LET IT THROUGH. The obvious "fix" is to skip the clamp
when free is unreadable, which lets the order go at its full risk-sized amount.
That is the LOOSENING direction on live money: today the position does not
open, and after that change it would. This keeps the outcome exactly as it is
-- no fill -- and corrects only the lie about why. A $0 order is not a safety
control, but it is currently acting as one, and quietly removing it is not a
display fix.
"""
import pytest

from bot.core.margin_clamp import clamp_to_free_margin

# ── the producer ──────────────────────────────────────────────────────────

def test_an_absent_balance_coin_entry_does_not_mint_a_free_of_zero():
    """The whole payload, built the way fetch_balance builds it."""
    from bot.core.margin_clamp import read_free_margin

    assert read_free_margin({}) is None
    assert read_free_margin({"used": 10.0}) is None, "no free key at all"
    assert read_free_margin(None) is None


def test_a_real_zero_free_margin_is_still_a_reading():
    """Fully-deployed capital is a genuine 0.0 and must not read as unknown."""
    from bot.core.margin_clamp import read_free_margin

    assert read_free_margin({"free": 0}) == 0.0
    assert read_free_margin({"free": "0"}) == 0.0


def test_a_normal_free_margin_reads_through():
    from bot.core.margin_clamp import read_free_margin

    assert read_free_margin({"free": 512.34}) == 512.34


# ── the clamp decision ────────────────────────────────────────────────────

def test_an_unreadable_free_margin_refuses_rather_than_sizing_to_zero():
    size, reason = clamp_to_free_margin(50.0, {"total": 512.34})
    assert size is None, "the trade must not proceed at any size"
    assert reason == "unreadable"


def test_a_measured_zero_free_margin_also_refuses_but_says_so_differently():
    """Both refuse; the operator must be able to tell WHICH happened."""
    size, reason = clamp_to_free_margin(50.0, {"free": 0.0, "total": 512.34})
    assert size is None
    assert reason == "insufficient"


def test_a_sufficient_balance_is_untouched():
    assert clamp_to_free_margin(50.0, {"free": 500.0}) == (50.0, None)


def test_an_oversized_request_still_clamps_to_the_real_free_margin():
    """The clamp is the point of the code and must keep working."""
    assert clamp_to_free_margin(500.0, {"free": 120.0}) == (120.0, "clamped")


def test_no_balance_at_all_skips_the_clamp_as_before():
    """`get_user_live_equity` returns None on fetch failure and the clamp is
    skipped -- documented, pre-existing, and NOT changed here."""
    assert clamp_to_free_margin(50.0, None) == (50.0, None)


def test_an_error_payload_is_treated_as_unreadable_not_as_zero():
    size, reason = clamp_to_free_margin(
        50.0, {"error": "bitget 40006 Invalid sign", "free": 0, "total": 0})
    assert size is None
    assert reason == "unreadable"


@pytest.mark.parametrize("bad", ["", "abc", float("nan"), float("inf"), None])
def test_a_non_numeric_free_is_unreadable(bad):
    from bot.core.margin_clamp import read_free_margin

    assert read_free_margin({"free": bad}) is None


def test_the_reason_never_states_a_dollar_figure_for_an_unread_margin():
    """What went into the audit chain is the actual finding.

    `Live size clamped: $50.00 -> $0.00 (exchange available)` names a number
    the exchange never reported, sealed into a tamper-evident record.
    """
    _, reason = clamp_to_free_margin(50.0, {"total": 512.34})
    assert "$" not in reason and "0.00" not in reason
