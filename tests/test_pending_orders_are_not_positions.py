"""Three positions the user did not hold, in the model's evidence about money.

2026-08-20, from the operator's own screen. Within one minute:

    [scan_asset] context  ACTIVE POSITIONS (live): 3 PENDING (DOGE, SOL, AVAX)
    /orders               "No pending orders on Bitget right now."
    /open_positions       OPEN POSITIONS (1) — PUMP

Three surfaces, three answers. The one that asked the exchange said none; the
one feeding the LLM said three, under a heading claiming to be live exchange
state.

`live_executor.py` states the semantics itself:

    A pending_fill position has no open position on exchange — only an
    unfilled limit order.

and carries an 8-hour force-close safety net for pending records because "the
exchange silently cancelled the order" leaves them stuck. So the code knows
they go stale — and they went stale under a heading reading ACTIVE POSITIONS
(live exchange). Two false claims in one line: not active positions, and not
from the exchange — `executor.open_positions` is an in-memory list.

AND IT DEFEATED THE GUARD DIRECTLY ABOVE IT. The comment there records:

    Real incident: a user with zero live positions was told by chat "HYPE
    (your open short)" -- there was no position at all; the prompt simply
    never said so either way.

The fix for that was an else-branch printing "none right now — do not
reference any open position". But the condition is `if
executor.open_positions:`, which is TRUE for three stale pendings, so on the
screen above the else never ran. A user holding nothing was not told so. The
guard was defeated by exactly the records that made the list non-empty without
making any of it true — which is why the test that matters here is
`test_pendings_alone_still_say_none`, not the one checking the new heading.

THE STAKES ARE THE REASON THIS IS SEPARATED RATHER THAN RELABELLED. The
sibling branch fifty lines down carries the comment "THE WORST PLACE TO INVENT
A NUMBER... a fabrication laundered through natural language is harder to
catch than a wrong number on a card, because the sentence sounds considered."
A model told the user holds DOGE will discuss managing it.
"""

from __future__ import annotations

import pathlib
from types import SimpleNamespace as NS

import pytest

from bot.skills.telegram_handler import _live_positions_block

ROOT = pathlib.Path(__file__).resolve().parent.parent

block = _live_positions_block


def _pos(symbol, status="open", direction="LONG"):
    return NS(symbol=symbol, status=status, direction=direction,
              entry_price=0.186440, stop_loss=0.188220, take_profit=0.184370,
              quantity=100.0, cost_usd=52.48, leverage=5)


def _exec(*positions):
    return NS(open_positions=list(positions))


# ── the reported case ───────────────────────────────────────────────────────

def test_pendings_alone_still_say_none():
    """THE ONE THAT MATTERS. Three unfilled orders and nothing held: the
    instruction that stops the model inventing a position must still fire."""
    out = block(_exec(_pos("DOGE/USDT", "pending_fill"),
                      _pos("SOL/USDT", "pending_fill"),
                      _pos("AVAX/USDT", "pending_fill")))
    assert "ACTIVE POSITIONS: none right now" in out
    assert "Do not reference any open position" in out


def test_pendings_are_not_filed_under_active_positions():
    out = block(_exec(_pos("DOGE/USDT", "pending_fill")))
    head, _, tail = out.partition("UNFILLED LIMIT ORDERS")
    assert "DOGE" not in head, (
        "an unfilled limit order is listed under ACTIVE POSITIONS")
    assert "DOGE" in tail


def test_pendings_are_reported_rather_than_hidden():
    """The opposite error. Dropping them would leave the operator's own bot
    unable to say what it had placed."""
    out = block(_exec(_pos("DOGE/USDT", "pending_fill")))
    assert "UNFILLED LIMIT ORDERS" in out
    assert "DOGE/USDT" in out


def test_the_pending_section_says_the_user_does_not_hold_them():
    out = block(_exec(_pos("SOL/USDT", "pending_fill")))
    assert "does NOT hold these" in out
    assert "Never describe these as open positions" in out


def test_the_pending_section_does_not_claim_the_exchange_confirmed_it():
    """`executor.open_positions` is in-memory and has an 8-hour staleness
    timeout. Saying "live exchange" over it is a provenance claim the read
    cannot support."""
    out = block(_exec(_pos("SOL/USDT", "pending_fill")))
    assert "NOT confirmed against the exchange" in out
    assert "/orders" in out, "the reader is not pointed at what does ask"


def test_no_section_claims_live_exchange_provenance_for_a_local_list():
    out = block(_exec(_pos("PUMP/USDT"), _pos("SOL/USDT", "pending_fill")))
    assert "(live exchange)" not in out


# ── the mixed and empty cases ───────────────────────────────────────────────

def test_a_filled_position_is_still_reported_as_held():
    """CONTROL. The whole point of the section is that a real holding reaches
    the model."""
    out = block(_exec(_pos("PUMP/USDT")))
    assert "ACTIVE POSITIONS (held on the exchange)" in out
    assert "PUMP/USDT" in out
    assert "none right now" not in out


def test_filled_and_pending_are_both_present_and_separate():
    out = block(_exec(_pos("PUMP/USDT"),
                      _pos("SOL/USDT", "pending_fill"),
                      _pos("AVAX/USDT", "pending_fill")))
    held, _, unfilled = out.partition("UNFILLED LIMIT ORDERS")
    assert "PUMP/USDT" in held
    assert "SOL/USDT" in unfilled and "AVAX/USDT" in unfilled
    assert "SOL/USDT" not in held and "AVAX/USDT" not in held


def test_nothing_at_all_says_none_and_nothing_else():
    out = block(_exec())
    assert "none right now" in out
    assert "UNFILLED" not in out, (
        "an empty pending list is rendering a section about nothing")


@pytest.mark.parametrize("bad", [None, NS(), NS(open_positions=None)])
def test_a_missing_executor_state_does_not_raise(bad):
    """This runs on the chat path for every message; an exception here costs
    the reply, not just the section."""
    out = block(bad)
    assert "none right now" in out


def test_a_position_with_no_status_counts_as_held():
    """Absent is not pending. A record whose status field never got written is
    ambiguous, and the safe reading is the one that does NOT tell the user
    they hold nothing while they do."""
    p = _pos("PUMP/USDT")
    del p.status
    assert "PUMP/USDT" in block(_exec(p))
    assert "none right now" not in block(_exec(p))


# ── the model is told the difference ────────────────────────────────────────

def test_the_grounding_rules_name_the_distinction():
    """The section is only half the fix: the standing instruction says "only
    state the user has an open position if it appears in ACTIVE POSITIONS",
    and a new section listing symbols invites exactly the reading it forbids
    unless the rule names it."""
    from tests.source_scan import code_only

    src = code_only((ROOT / "bot" / "skills" / "telegram_handler.py")
                    .read_text(encoding="utf-8"))
    assert "UNFILLED LIMIT ORDERS ARE NOT POSITIONS" in src
    assert "'ACTIVE POSITIONS: none' means none even when unfilled orders are" in src


def test_the_block_is_reached_from_the_prompt():
    """Every test above calls the seam directly and none prove the prompt uses
    it. #999 shipped a card that rendered zero times."""
    from tests.source_scan import code_only

    src = code_only((ROOT / "bot" / "skills" / "telegram_handler.py")
                    .read_text(encoding="utf-8"))
    i = src.index("def _build_chat_system_prompt")
    body = src[i:src.index("async def _llm_chat", i)]
    assert "_live_positions_block(executor)" in body, (
        "the chat prompt no longer builds its positions section from the "
        "seam, so none of the separation above is reached")


def test_the_section_can_never_be_silence():
    """THE DEFECT THE TEST FAILURE EXPOSED, which is older than this change.

    The whole positions block sits inside a broad `except Exception:`, so ANY
    error in it silently dropped the section — while the comment at the
    injection site says, in capitals, "NEVER leave this section blank when
    is_live", because an LLM given no statement about positions invents one
    from conversation history. The except allowed precisely what the comment
    forbids, and it took a stub missing one attribute to show it: two grounding
    tests went from passing to failing with no position text in the prompt at
    all.

    So `positions_detail` starts as a statement rather than "". A failure now
    degrades to "could not be confirmed" instead of to silence.
    """
    from tests.source_scan import code_only

    src = code_only((ROOT / "bot" / "skills" / "telegram_handler.py")
                    .read_text(encoding="utf-8"))
    assert 'positions_detail = ""' not in src, (
        "the positions section defaults to empty again — an exception "
        "anywhere in that block now produces a prompt that says nothing "
        "about positions, which is the state the guard exists to prevent")
    i = src.index("positions_detail = (")
    assert "could not be read" in src[i:i + 400]


def test_the_helper_needs_no_instance():
    """It is module-level, not a method, and that is deliberate: the call site
    `self._live_positions_block(...)` failed on every stub-based caller and the
    broad except turned that AttributeError into a missing section rather than
    a crash. A free function has nothing to look up."""
    import inspect

    from bot.skills import telegram_handler as th
    assert inspect.isfunction(th._live_positions_block)
    assert not hasattr(th.TelegramHandler, "_live_positions_block")
