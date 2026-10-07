"""A self-admitted paper user's confirm is practice, never a live order.

Plan item F8. The role is ``SELF_ADMISSION_ROLE`` (``paper``): a signup nobody
vouched for. Their Confirm opens a labelled row on their own paper book.
``PRACTICE`` is the only label that keeps a close out of the engine's learners
and journal. A missing label is not practice — an old row, a vouched trader's
opt-in fill, and a live close all still train. An unreadable account record
is not paper either: the live door keeps refusing, and nothing is placed.
"""
from __future__ import annotations

from bot.utils.user_store import SELF_ADMISSION_BY, SELF_ADMISSION_ROLE

#: Stamped on the paper row a self-admitted confirm opens. Not a count.
PRACTICE_FILL = "PRACTICE"

#: Why a caller's mode reads PAPER when the live flags alone would say LIVE:
#: the engine opens a practice row for their confirm (`confirm_is_practice`),
#: so every surface that names the mode says this rather than LIVE.
PRACTICE_MODE_REASON = (
    "practice account: your confirms open practice rows on your own paper "
    "book, not live orders (a self-admitted account, or practice mode opted "
    "in). Live trading needs an account an admin has vouched for.")


def is_self_admitted_paper(user: object) -> bool:
    """True when nobody vouched for this account: the stored role is the
    self-admission role, or the record still carries the self-admission
    stamp under any role but ``admin``.

    The stamp is the second half because the role alone missed records
    written before `authorize()` clamped self-admission: a stranger let in
    as ``trader`` with ``admitted_by="auto-accept"`` read as vouched, and
    fixing that waited on a migration someone had to run. An admin's
    ``/approve`` rewrites ``admitted_by`` to the admin's id, so the stamp
    never outlives a vouch. ``admin`` is excluded because `seed_admin`
    promotes the operator's own record without touching ``admitted_by``.

    A missing record, a non-dict, or a role that is not a string is not
    paper. Callers that could not read the store must not pass that failure
    in as ``None`` and read it as permission; ``None`` here means "no record".
    """
    if not isinstance(user, dict):
        return False
    role = user.get("role")
    if not isinstance(role, str):
        return False
    if role == SELF_ADMISSION_ROLE:
        return True
    return role != "admin" and user.get("admitted_by") == SELF_ADMISSION_BY


def self_admitted_paper_caller(users: object, user_id: object) -> bool:
    """Whether this caller is a self-admitted paper account.

    An unreadable store answers False. That is not "they are a trader": the
    live door still refuses, so nothing is placed. The engine asks again
    before any order.
    """
    if users is None or user_id is None or user_id == "" or user_id == "auto":
        return False
    getter = getattr(users, "get", None)
    if not callable(getter):
        return False
    try:
        record = getter(user_id)
    except Exception:
        return False
    return is_self_admitted_paper(record)


def admission_unread(users: object, user_id: object) -> bool:
    """Whether this person's admission could not be read.

    True when the store failed to load or reading the record raised: the
    question "did anyone vouch for this account" has no answer, which
    `self_admitted_paper_caller` reports as "not paper". That is right for
    a card naming the mode and wrong for a live fill, which the engine
    refuses on it. Only a person's id is asked; ``"auto"`` and ``""`` have
    no record. No store at all is not unread: an engine not wired to the
    user store (a detached reader, a test) has no admissions to read.
    """
    if users is None or user_id is None or user_id == "" or user_id == "auto":
        return False
    probe = getattr(users, "load_failed", None)
    if callable(probe):
        # `is True`: the real store answers a bool. A stand-in that answers
        # anything else has said nothing about the file, and the read below
        # still runs.
        try:
            if probe() is True:
                return True
        except Exception:
            return True
    getter = getattr(users, "get", None)
    if not callable(getter):
        return True
    try:
        getter(user_id)
    except Exception:
        return True
    return False


def is_practice_fill(trade: object) -> bool:
    """True only when the row carries the PRACTICE label.

    A missing attribute, or a label that is not a string, is not practice.
    Absence must not be read as the label, and must not be read as zero
    practice closes.
    """
    label = getattr(trade, "fill_label", None)
    return isinstance(label, str) and label == PRACTICE_FILL


def close_trains_engine(trade: object) -> bool:
    """Whether a close may enter the engine journal and learners.

    A PRACTICE close is the person's rehearsal. Every other row still
    trains, including one whose label could not be read.
    """
    return not is_practice_fill(trade)


def practice_book_refusal(exc: BaseException | None = None) -> str:
    """The confirm placed nothing, and says what to do next.

    The exception's class, never its message: a book that failed to load
    can carry a path. No dollar figure. ``placed_nothing`` reads the
    ``Trade REJECTED`` prefix.
    """
    why = type(exc).__name__ if exc is not None else "unavailable"
    return (
        "Trade REJECTED: the practice book could not take this confirm "
        f"({why}). Watch a signal, open its page, and link read-only keys "
        "with /connect. Nothing was placed."
    )
