"""The block that certifies which prices are real must not certify a nameless one.

`_live_ticker_block()` appends measured exchange prices to the chat system
prompt and ends with "State ONLY these prices". That sentence is what makes
the block load bearing: everything inside it is, by the prompt's own promise,
a reading the model is licensed to repeat.

On 2026-08-31 the feed returned a tick keyed by an EMPTY STRING and the block
rendered it as a bare `  $101.49  (-3.3% 24h)` — a price with nothing attached
to it, sitting inside the certified region. A number attributable to nothing
is a number the model can attach to whatever was just asked about, which is
the recalled-price failure arriving through the one channel the prompt tells
it to trust.

The file also pins the prompt rule those prices are read under, because that
rule and this block used to contradict each other outright: the prompt said
"You do NOT have a live market-data feed in this chat" while this function
appended one.
"""

from __future__ import annotations

import types

import pytest

from bot.skills.telegram_handler import TelegramHandler


def _tick(last, chg=0.0):
    return types.SimpleNamespace(last=last, change_pct_24h=chg)


def _block(ticks):
    """Drive the real method with a stand-in self.

    The module already documents that suites invoke its functions with a
    SimpleNamespace for `self`; the method reads only the feed and three class
    constants, so this exercises the shipped code rather than a copy of it.
    """
    me = types.SimpleNamespace(
        engine=types.SimpleNamespace(
            ws_feed=types.SimpleNamespace(get_snapshot=lambda **_kw: ticks)),
        CHAT_TICKER_MAX_AGE_SEC=TelegramHandler.CHAT_TICKER_MAX_AGE_SEC,
        CHAT_TICKER_LEAD=TelegramHandler.CHAT_TICKER_LEAD,
        CHAT_TICKER_MAX=TelegramHandler.CHAT_TICKER_MAX,
    )
    return TelegramHandler._live_ticker_block(me)


# ── the nameless price ────────────────────────────────────────────────────

def test_a_tick_with_an_empty_symbol_is_not_rendered():
    out = _block({"": _tick(101.49, -0.033), "BTC/USDT": _tick(101000.0)})
    assert "101.49" not in out, "a price with no symbol reached the certified block"
    assert "BTC/USDT" in out, "the real tick must survive the filter"


@pytest.mark.parametrize("bad", ["", "   ", "\t", "\n"])
def test_whitespace_is_not_a_symbol_either(bad):
    out = _block({bad: _tick(101.49), "ETH/USDT": _tick(3000.0)})
    assert "101.49" not in out
    assert "ETH/USDT" in out


def test_a_feed_of_only_nameless_ticks_says_so_rather_than_printing_nothing():
    """Omit-then-blank is the failure this repository names first.

    Filtering every row and emitting a LIVE MARKET header with no rows under
    it would be the `_status_lines` shape: a section that announces itself and
    then says nothing, which reads as 'nothing to report'. The honest branch
    already exists for the empty-feed case and must be the one taken here.
    """
    out = _block({"": _tick(101.49), "  ": _tick(3000.0)})
    assert "NONE AVAILABLE" in out
    assert "101.49" not in out
    assert "3000" not in out


def test_the_existing_zero_price_guard_still_holds():
    """Guard the neighbour: the new check sits directly beside it."""
    out = _block({"BTC/USDT": _tick(0.0), "ETH/USDT": _tick(3000.0)})
    assert "NONE AVAILABLE" not in out
    assert "ETH/USDT" in out
    assert "BTC/USDT" not in out


def test_a_normal_feed_still_renders():
    """The failure mode of every filter: filtering everything."""
    out = _block({"BTC/USDT": _tick(101000.0, 0.021)})
    assert "BTC/USDT" in out
    assert "101,000" in out
    assert "+2.1%" in out
    assert "State ONLY these prices" in out


# ── the rule those prices are read under ──────────────────────────────────

def test_the_private_prompt_does_not_deny_the_feed_it_is_handed():
    """The contradiction, asserted on the prompt VALUE rather than the source.

    Deliberately not a source scan: the code comment explaining this change
    quotes the old sentence, and a grep over the file would match the comment
    describing the fix as though it were the defect. Asserting on the built
    string asks the question that matters — what does the model actually
    read — and cannot be answered wrongly by prose.
    """
    prompt = TelegramHandler._CHAT_SYSTEM_PROMPT
    assert "do NOT have a live market-data feed in this chat" not in prompt
    assert "LIVE MARKET block" in prompt


def test_the_private_prompt_still_forbids_a_remembered_price():
    """The half of the old rule that was correct and must not be lost."""
    prompt = TelegramHandler._CHAT_SYSTEM_PROMPT.lower()
    assert "never state a price from memory" in prompt
    assert "scan" in prompt


def test_the_private_prompt_covers_the_symbol_that_is_not_listed():
    """'No block' and 'block without your symbol' both mean unknown."""
    assert "does not list the symbol" in TelegramHandler._CHAT_SYSTEM_PROMPT


def test_the_public_prompt_keeps_saying_it_has_no_feed():
    """It gets no ticker block, so there the original sentence is TRUE.

    Reconciling the private prompt must not 'tidy' this one into claiming a
    feed the public surface does not receive.
    """
    assert "do NOT have a live market-data feed here" in (
        TelegramHandler._PUBLIC_CHAT_SYSTEM_PROMPT)


def test_the_private_prompt_forbids_writing_a_tool_result_block():
    prompt = TelegramHandler._CHAT_SYSTEM_PROMPT
    assert "NEVER write such a block yourself" in prompt
    assert "[PENDING]" in prompt
