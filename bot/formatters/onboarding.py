"""What the bot says to someone who is not (yet) a user.

Extracted from the handler because every string in this path was built inline,
and every one of them made a claim the code beside it contradicted:

  * ``_handle_message`` called ``users.register(...)`` and then replied
    "I don't recognize you yet ... Use /start to register, then wait for
    approval." Three assertions, three of them false: it had just created the
    record, /start was not needed, and there is no approval step on a bot with
    an empty allowlist. A person who believed it waited for a message that was
    never coming.
  * ``_guard`` replied "This bot is locked to its configured operator" and
    stopped there. True, and a dead end — it named no way to ask, and nobody
    was told the person had shown up.
  * A 24h-stale session was reported as "Your role (trader) cannot use
    /trade". The role can. The session had expired, and the fix (/start) was
    unguessable from the message.

So the rule for this file: **say the state the store is actually in, and name
the next step.** Which is why ``access`` is required and unknown values raise —
an onboarding message that guesses is the defect, not the fallback.

These are pure functions over explicit state, which is the point:
``tests/test_onboarding_notices.py`` plants each state and asserts what the
card says and what it must never say.
"""
from __future__ import annotations

from bot.utils.i18n import t

# The three ways a caller can stand with the gate, and there is no fourth.
#   "open"            no allowlist configured (demo/paper) — everyone is in
#   "granted"         allowlist configured, and this caller is on it, either
#                     via env (operator / admin / live trader) or an admin's
#                     /approve (UserStore.is_admitted)
#   "needs_approval"  allowlist configured, this caller is not on it
ACCESS_STATES = ("open", "granted", "needs_approval")


def _next_step(tg_id: str, operator_notified: bool, lang: str) -> str:
    """Tell them what happens next — and only what actually happened.

    ``operator_notified`` is the RESULT of the send, not the intent to send.
    "I've told the operator" printed after a failed Telegram call is the same
    class of lie as a 0.00% standing in for an unreadable price: it reads as a
    measurement of a thing that never occurred, and it leaves the person
    waiting instead of asking. When the ping did not land, point them at a
    human.
    """
    if operator_notified:
        return t("reg_notified_wait", lang)
    return t("reg_ask_operator", lang, tg_id=tg_id)


def welcome_notice(name: str, tg_id: str, *, access: str,
                   operator_notified: bool = False, lang: str = "en") -> str:
    """First contact: the record now exists. Say so, and say what it buys.

    ``access`` must be one of ACCESS_STATES. There is deliberately no default:
    the caller knows whether the allowlist is configured, and a wrong guess
    here either promises access that does not exist or invents an approval
    step that does not.
    """
    if access not in ACCESS_STATES:
        raise ValueError(
            f"unknown access state {access!r}; expected one of {ACCESS_STATES}")
    if access == "needs_approval":
        return t("reg_welcome_needs_approval", lang, name=name, tg_id=tg_id,
                 next_step=_next_step(tg_id, operator_notified, lang))
    return t("reg_welcome_ready", lang, name=name, tg_id=tg_id)


def access_denied_notice(tg_id: str, *, operator_notified: bool = False,
                         lang: str = "en") -> str:
    """A guarded command refused because the caller is not on the allowlist.

    Says "locked, not broken" on purpose: an unexplained refusal on a bot that
    had just welcomed them is indistinguishable from a bug, and that reading is
    what makes people leave rather than ask.
    """
    return t("reg_access_denied", lang, tg_id=tg_id,
             next_step=_next_step(tg_id, operator_notified, lang))


def permission_denied_notice(command: str, role: str, reason: str,
                             lang: str = "en") -> str:
    """Refusal for a caller who IS on the bot: name the real cause.

    ``reason`` comes from ``UserStore.permission_denial`` — "role" or
    "stale_session". They are not interchangeable and were printed
    identically: one is permanent and needs an admin, the other clears with
    /start. Anything unrecognised falls back to the role wording, which is the
    conservative half: it never tells someone a 24h timer will fix a
    permission they do not have.
    """
    if reason == "stale_session":
        return t("reg_session_expired", lang, command=command)
    return t("reg_role_denied", lang, command=command, role=role)


def admin_access_request(name: str, tg_id: str, lang: str = "en") -> str:
    """The operator's side of an access request.

    Spells out that approving grants PAPER bot access only. ``/approve`` writes
    UserStore admission, which opens ``_is_allowlisted``; live trading keeps
    reading the env allowlist alone (``_can_trade_live``), and the engine's
    operator identity is env-only too. An operator tapping a button in a chat
    should be able to read what the tap does without going to the source.
    """
    return t("reg_admin_request", lang, name=name, tg_id=tg_id)


#: Free-text intents whose skill is not wired, mapped to the nearest thing the
#: user CAN reach. A suggestion is only worth printing if it is real, so this
#: holds commands that exist; an intent absent from it gets no suggestion
#: rather than an invented one.
_UNAVAILABLE_ALTERNATIVES = {
    "analyze_asset": "/analyze BTC",
    "scan_market": "/scan",
    "get_portfolio": "/portfolio",
    "check_risk": "/risk",
    "get_orders": "/orders",
}


def skill_unavailable_notice(skill: str, lang: str = "en") -> str:
    """Said when the router recognised the request and the tool is not there.

    THE ALTERNATIVE TO THIS MESSAGE IS NOT SILENCE — it is the chat model. A
    matched intent whose skill is unregistered used to fall past every branch
    in the handler into the tool-less AI fallback, which answers anyway. On
    "status" that produced a language model's impression of whether the engine
    was running, and on "analyze BTC" a description of a chart nothing had
    read.

    So this says the two things that are true and separable: the request WAS
    understood, and it was NOT run. Collapsing those into "sorry, I didn't get
    that" would be its own small lie — the router understood perfectly.

    ``lang`` is accepted for symmetry with the other notices here and is not
    yet keyed into the translation table; the English string is returned for
    every locale rather than a missing-key placeholder.
    """
    alt = _UNAVAILABLE_ALTERNATIVES.get(skill)
    lines = [
        "I understood that as <b>" + skill.replace("_", " ") + "</b>, "
        "but that tool is not available on this bot right now.",
        "",
        "I have not looked anything up, so I am not going to guess at an "
        "answer.",
    ]
    if alt:
        lines += ["", f"Closest thing you can run: <code>{alt}</code>"]
    return "\n".join(lines)
