"""The getting-started checklist chat shows, and does not act on.

Plan item F8, the step after a paper Confirm. Four steps, each a door that
already exists: watch a signal, open its page, tap Confirm (a self-admitted
paper account's Confirm opens a PRACTICE row), and link keys with withdrawal
off. Chat stays read-only. This module places nothing, stages nothing, and
calls no executor.

A slash command is named only on a surface that runs it. The website's chat
cannot run ``/signals`` or ``/connect``; those doors are the dashboard's.
"""
from __future__ import annotations

import html

from bot.core.practice_fill import is_self_admitted_paper
from bot.skills.chat_runtime import link_door
from bot.utils.site_url import site_url

#: Surfaces whose doors are the website's, not Telegram commands.
_WEB_SURFACES = frozenset({"web", "public"})
#: The operator's API bridge speaks Telegram's commands, as the linking
#: door already does.
_TELEGRAM_SURFACES = frozenset({"telegram", "api"})


def _account(users: object, user_id: object) -> str:
    """``paper``, ``other``, ``absent`` or ``unreadable``.

    Empty identity is absent and does not ask a store. A store that cannot
    be asked, a record that is not a dict, or a role that is not a string
    is unreadable — not paper, and not a trader. ``None`` from a successful
    read is no account.
    """
    if user_id is None or user_id == "" or user_id == "auto":
        return "absent"
    if users is None:
        return "unreadable"
    getter = getattr(users, "get", None)
    if not callable(getter):
        return "unreadable"
    try:
        record = getter(user_id)
    except Exception:
        return "unreadable"
    if record is None:
        return "absent"
    if not isinstance(record, dict):
        return "unreadable"
    if is_self_admitted_paper(record):
        return "paper"
    role = record.get("role")
    if not isinstance(role, str) or role == "":
        return "unreadable"
    return "other"


def _practice_sentence(account: str) -> str:
    if account == "paper":
        return (
            "Your account is practice. Confirm on that signal opens a "
            "PRACTICE row on your paper book and does not send an order."
        )
    if account == "other":
        return (
            "This account is not self-admitted paper. Confirm is the trade "
            "door, and a trader without live permission still gets the live "
            "refusal."
        )
    if account == "absent":
        return (
            "No account was read, so this card does not say your Confirm "
            "opens a practice row. A self-admitted paper account's Confirm "
            "opens a PRACTICE row and does not send an order."
        )
    if account == "unreadable":
        return (
            "The account could not be read, so whether Confirm opens a "
            "practice row is unknown. An unreadable practice book places "
            "nothing."
        )
    raise ValueError(f"unknown account reading {account!r}")


def onboarding_checklist(surface: str, users: object = None,
                         user_id: object = None) -> str:
    """The four-step card for one surface and one account reading.

    ``surface`` is the transport the turn arrived on. An unmeasured surface
    raises: painting Telegram's commands onto the website is the defect this
    card exists beside. Places nothing.
    """
    if surface in _WEB_SURFACES:
        doors = "web"
    elif surface in _TELEGRAM_SURFACES:
        doors = "telegram"
    else:
        raise ValueError(
            f"{surface!r} has no onboarding doors; name web, public, "
            "telegram or api")
    account = _account(users, user_id)
    origin = html.escape(site_url(), quote=True)
    signals = f'<a href="{origin}/dashboard#signals">Signals</a>'
    if doors == "web":
        watch = f"Open {signals} on the dashboard."
        page = (
            f"Open the page {signals} links for that signal. "
            "Each row's page is its call receipt."
        )
    else:
        watch = (
            "<code>/signals</code> lists the record and "
            "<code>/latest_signal</code> shows the latest."
        )
        page = (
            f"Open that signal's page from {signals}. "
            "Each row links to its call receipt."
        )
    link = html.escape(link_door(doors)["link"], quote=False)
    # Only Telegram's /connect probes the key's scope and says whether
    # withdraw is on (`probe_bitget_key_scope`). The web's Connect an
    # exchange form stores the key and reads nothing about it, and the step
    # told web readers that the card they got back would say.
    scope = ("The card /connect sends back says whether withdraw is on."
             if doors == "telegram" else
             "This form does not check whether withdraw is on, so turn it off "
             "when you create the key.")
    steps = (
        f"1. Watch a signal. {watch}",
        f"2. Open its page. {page}",
        "3. Stage a practice ticket by tapping Confirm on that signal. "
        f"{_practice_sentence(account)} This chat does not stage a ticket.",
        "4. Link keys with withdrawal off (futures read and trade, "
        f"withdrawal off). {link}. {scope} This chat does not take keys.",
    )
    body = "\n".join(steps)
    return (
        "<b>Getting started</b>\n"
        "This chat does not place an order.\n\n"
        f"{body}"
    )
