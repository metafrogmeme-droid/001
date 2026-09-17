"""Arming the limit-price capture, and the prompt that may only follow it.

**A PROMPT THAT ASKS A QUESTION MUST NOT BE SENT UNLESS SOMETHING IS LISTENING.**
That is the `/vault` hint shape pointed at an input: there a card named a
COMMAND that did nothing, here a card asks for a VALUE that nothing will read.

`scan_skill`'s Limit button armed the capture ``if hasattr(handler,
"_pending_limit_input")`` and then sent "Type your limit price"
UNCONDITIONALLY below it — and that attribute is a bare annotation on the
callback mixin whose own comment says *"created on first use"*, so `hasattr`
is False until the OTHER door has run once in the process. Driven on a live
account (PENDLE LONG, 2026-09-15): the card asked for a price, armed nothing,
and the typed ``$2.367`` matched no router rule, fell through to the chat
model and came back as a macro-risk card. The caller answered the bot's own
question and was told about event risk, on the flow that CONFIRMS AND EXECUTES
A TRADE.

`callback_handler`'s copy of the same six lines creates the dict before
writing it, so that door worked. **A second copy of a sequence is a second
answer, and this one had lost the line that mattered** — which is invisible
from either file, because each reads correct on its own.

Two things follow from the rule, and both are why arming returns a verdict
rather than being assumed:

* The state is CREATED here, never tested for. `hasattr` asks whether some
  earlier caller happened to make it; the question this module answers is
  whether THIS caller is now being listened to.
* An empty caller id is a refusal, not a key. Both doors compute
  ``str(update.effective_user.id) if update.effective_user else ""`` — their
  own authors anticipated no user — and arming under ``""`` writes a row the
  free-text intercept (which looks up ``str(uid)``) can never match. That is
  the same silent no-listener a second way, so it answers False and the
  prompt is not sent.
"""

from __future__ import annotations

import time
from typing import Any

from bot.utils.i18n import t

#: The attribute the free-text intercept reads to complete a pending limit
#: price. Named once here because three modules reach for it and a fourth
#: spelling would be a fourth answer.
STATE_ATTR = "_pending_limit_input"


def arm_limit_input(
    handler: Any,
    caller_uid: str | None,
    *,
    trade_id: str,
    asset: str,
    pair: str,
    direction: str,
    current_entry: float,
    now: float | None = None,
) -> bool:
    """Arm the pending limit-price capture for `caller_uid`.

    Answers whether this caller is now being listened to. The caller sends
    the prompt ONLY on True — see the module docstring for what a prompt
    without a listener cost.
    """
    if handler is None or not caller_uid:
        return False

    state = getattr(handler, STATE_ATTR, None)
    if not isinstance(state, dict):
        # CREATED, not tested for. The bare annotation on the mixin is a
        # type declaration, so `hasattr` is False until something assigns —
        # and the door that only tested was the one that broke.
        state = {}
        setattr(handler, STATE_ATTR, state)

    state[caller_uid] = {
        # This literal is a SUPERSET of `_ROW_FIELDS`, which is what the
        # readers need — never the other way round.
        "trade_id": trade_id,
        "asset": asset,
        "pair": pair,
        "direction": direction,
        "current_entry": current_entry,
        "timestamp": time.time() if now is None else now,
    }
    return True


#: How long a "type your limit price" prompt is answerable for. The handler
#: had this as a bare 300 beside the read that used it; it is named here
#: because the READING and the sentence about it must agree, and a second copy
#: of a threshold is a second answer.
PENDING_TTL_SEC = 300

#: What a row must carry for the READERS to use it: the capture body indexes
#: `trade_id`, `pair` and `direction`, and both the expiry check and its
#: sentence need `timestamp` and `pair`.
#:
#: The first draft demanded every key `arm_limit_input` WRITES, which is the
#: wrong invariant and the full gate said so: two existing tests plant the
#: four fields the body reads, and a six-field precondition answered NONE for
#: them — so an arming already on the handler when this build started, or any
#: row carrying only what its readers need, would have fallen through to the
#: chat model. That is the exact silence this slice removes, rebuilt inside
#: the cure for it. A reading's precondition is its READERS' needs; `asset`
#: and `current_entry` are written and read by nobody here, and a field no
#: reader reads is not part of the contract.
_ROW_FIELDS = frozenset({"trade_id", "pair", "direction", "timestamp"})


def read_pending(handler: Any, caller_uid: str | None,
                 *, now: float | None = None) -> tuple[str, dict | None]:
    """Whether this caller is being listened to: ARMED, EXPIRED, or NONE.

    **A PROMPT THAT EXPIRED IS NOT A PROMPT THAT WAS NEVER SENT**, and the
    handler read them as one thing. Its block deleted the stale row, set
    `pending_info = None` and fell through — so a caller who tapped Limit,
    stepped away for six minutes and came back to type ``2.367`` was answered
    by the chat model, which holds no limit order and was never told a price
    had been asked for. Driven: a bare number matches no router rule at any
    confidence, so the fall-through is the model every time.

    That is the incident this module's header describes, arriving through the
    other door: there the prompt armed nothing, here the arming timed out, and
    from the caller's side both are *"I answered the bot's own question and it
    replied about something else"*.

    EXPIRED hands the row BACK rather than dropping it, because the sentence
    the caller is owed names what expired — a price for a trade they can no
    longer see is not the same fact as "I did not follow that". The row is
    consumed either way: a stale arming must not answer the NEXT number.
    """
    state = getattr(handler, STATE_ATTR, None)
    if not isinstance(state, dict) or not caller_uid:
        return "none", None
    row = state.get(caller_uid)
    # A row missing what `arm_limit_input` writes is not an arming this build
    # made, and it is NOT "expired": the sentence for that names the trade,
    # and there is no trade to name. It is consumed so it cannot sit there
    # forever, and answered NONE — which is also what the capture body needs,
    # because that body reads `row["trade_id"]` outside any handler that
    # catches a KeyError, so today such a row crashes the message.
    if not isinstance(row, dict) or not _ROW_FIELDS <= set(row):
        # `pop` with a default, unguarded: a caller who was never armed has no
        # key to remove and the guard the first draft wrote for that case
        # (`isinstance(row, dict) or row is not None`) could not be false when
        # the first half was true — a redundant clause is a claim that there
        # is a check.
        state.pop(caller_uid, None)
        return "none", None
    clock = time.time() if now is None else now
    if clock - float(row["timestamp"]) > PENDING_TTL_SEC:
        del state[caller_uid]
        return "expired", row
    return "armed", row


def consume_pending(handler: Any, caller_uid: str | None) -> None:
    """Stop listening to `caller_uid` — the price was used, or they cancelled.

    The capture body reached into the dict with `del self._pending_limit_input[
    caller_uid]` at three sites while `read_pending` decided whether a prompt
    was still live. Two readers of one dict are two answers to that question,
    and this one only had to drift by a `del` on the wrong branch to leave a
    stale arming answering the caller's NEXT number.

    Deliberately silent on a caller who was not armed: consuming twice is not
    an error, it is the second caller to finish.
    """
    state = getattr(handler, STATE_ATTR, None)
    if isinstance(state, dict) and caller_uid:
        state.pop(caller_uid, None)


def limit_expired_text(lang: str, *, pair: str) -> str:
    """What to say to a caller whose limit-price prompt timed out.

    It names the trade the price was for and says plainly that nothing was
    placed, because the one reading a bare number invites is that it was.
    """
    return t("limit_expired", lang, pair=pair,
             minutes=str(PENDING_TTL_SEC // 60))


def caller_lang(handler: Any, update: Any) -> str:
    """The caller's UI language, or English when nobody can be asked.

    `_lang` reads a STORED preference off the handler's user store, so a
    door reached without a handler has no preference to read. English there
    is a fact about what could be read, not a guess about the person — and
    it is the same fallback `_lang` itself takes when the store raises.
    """
    resolve = getattr(handler, "_lang", None)
    if resolve is None:
        return "en"
    try:
        return str(resolve(update))
    except Exception:
        return "en"


def _price(value: float) -> str:
    """A price as this product prints one: significant digits, not 4dp.

    `$0.0522` has no meaningful digits at 4dp and `$2.367` has one to spare,
    so the two doors' `:,.4f` and `:,.6g` disagreed about the same field.
    """
    return f"{value:,.6g}"


def limit_prompt_text(
    lang: str,
    *,
    asset: str,
    direction: str,
    entry: float,
    stop_loss: float,
    take_profit: float,
) -> str:
    """The "type your limit price" card, in the caller's own language.

    `limit_prompt` has carried all fourteen translations since it was
    written, and BOTH doors hand-wrote the English instead — so a Dutch
    caller read an English prompt and a Dutch answer in one exchange, with
    `Typ je limietprijs` sitting unused in the table.

    The examples are derived from the entry. `callback_handler` offered
    "e.g. 84.07 or 0.0522" whatever the asset, which is not guidance about
    a coin trading at $2.37 — it is two numbers from some other trade.
    """
    return t(
        "limit_prompt",
        lang,
        asset=asset,
        direction=direction,
        entry=_price(entry),
        sl=_price(stop_loss),
        tp=_price(take_profit),
        example1=_price(entry * 0.99),
        example2=_price(entry * 0.98),
    )


def limit_unarmed_text(lang: str) -> str:
    """What to say INSTEAD of the prompt when nothing was armed.

    It claims nothing about the order: nothing was armed, so nothing is
    waiting, and no price was read because none was asked for.
    """
    return t("limit_not_armed", lang)
