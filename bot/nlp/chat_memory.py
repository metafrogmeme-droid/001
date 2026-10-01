"""The dated note the chat model can read. It writes nothing.

The note is the rolling summary ``ConversationStore`` already keeps, dated
when it was written. The watchlist is the one the user saved in
``user_profile_store``. The open question is the latest user turn that still
has no reply after it. None of those is a new store, and a missing one is
said as absent. A file that will not read is unread, not an empty watchlist.
"""
from __future__ import annotations

from typing import Any, Optional

from bot.nlp.conversation_store import ConversationStore, _announced_cut, when_words
from bot.skills.skill_registry import BaseSkill

_QUESTION_MAX = 400


def read_note(conversations: Optional[ConversationStore], user_id: str) -> str:
    """``READ|UNREAD|ABSENT`` plus the three lines the model may quote.

    An empty ``user_id`` names nobody, so nothing is read and the tag is
    UNREAD. One user's note is never returned for another.
    """
    uid = str(user_id or "").strip()
    if not uid:
        return (
            "UNREAD\n"
            "Dated note: UNREADABLE — no user was named, so no note was read\n"
            "Watchlist: UNREADABLE — no user was named, so no watchlist was read\n"
            "Open question: UNREADABLE — no user was named, so no question was read"
        )
    note_line, note_state = _dated_note(conversations, uid)
    watch_line, watch_state = _watchlist(uid)
    question_line, question_state = _open_question(conversations, uid)
    states = (note_state, watch_state, question_state)
    if "unread" in states:
        tag = "UNREAD"
    elif "read" in states:
        tag = "READ"
    else:
        tag = "ABSENT"
    return f"{tag}\n{note_line}\n{watch_line}\n{question_line}"


def _dated_note(conversations: Optional[ConversationStore],
                user_id: str) -> tuple[str, str]:
    if conversations is None:
        return ("Dated note: UNREADABLE — the conversation store could not be read",
                "unread")
    ctx = conversations.get_context(user_id)
    if ctx is None or not (ctx.summary or "").strip():
        return ("Dated note: ABSENT — nothing older has been folded into a note",
                "absent")
    text = ctx.summary.strip()
    return (f"Dated note, written {when_words(ctx.summary_at)}: {text}", "read")


def _watchlist(user_id: str) -> tuple[str, str]:
    from bot.core import user_profile_store
    from bot.utils.json_store import StoreUnreadable
    try:
        profile = user_profile_store.get(user_id)
    except StoreUnreadable:
        return ("Watchlist: UNREADABLE — the saved watchlist could not be read",
                "unread")
    symbols = []
    if isinstance(profile, dict):
        raw = profile.get("watchlist")
        if isinstance(raw, (list, tuple)):
            symbols = [str(s) for s in raw if str(s or "").strip()]
    if not symbols:
        return ("Watchlist: ABSENT — no watchlist is saved", "absent")
    return ("Watchlist, declared by the user and not a holding: "
            + ", ".join(symbols), "read")


def _open_question(conversations: Optional[ConversationStore],
                   user_id: str) -> tuple[str, str]:
    if conversations is None:
        return ("Open question: UNREADABLE — the conversation store could not be read",
                "unread")
    rows = conversations.get_recent(user_id, limit=10**6)
    if not rows or rows[-1].role != "user":
        return ("Open question: ABSENT — no question is still open", "absent")
    body = _announced_cut(rows[-1].content, _QUESTION_MAX)
    return (f"Open question, {when_words(rows[-1].timestamp)}: {body}", "read")


class MemoryNoteSkill(BaseSkill):
    """Read the caller's dated note. ``execute`` does not write it."""

    name = "memory_note"
    description = (
        "The dated note already kept for this user: the folded summary, "
        "the watchlist they saved, and a question that still has no reply. "
        "It writes nothing and it is not the current account."
    )

    async def execute(self, engine: Any, **kwargs: Any) -> str:
        return read_note(kwargs.get("conversations"),
                         str(kwargs.get("user_id") or ""))
