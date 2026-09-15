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
        "trade_id": trade_id,
        "asset": asset,
        "pair": pair,
        "direction": direction,
        "current_entry": current_entry,
        "timestamp": time.time() if now is None else now,
    }
    return True


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
