"""The chat brain, reachable from a process that is not the Telegram bot.

`TelegramHandler._llm_chat` is the ONE chat brain: Telegram, the web gateway,
public chat and the Study Room tutor all call it. Until now the only way to
build a `TelegramHandler` was the bot process's `__init__`, which seeds the
admin, migrates the user store and wires a proactive monitor and a channel
forwarder — so `api_bridge.py`, which owns a `RuneClawEngine` of its own and
is the surface an external PROGRAM reaches, could not answer a question.

`headless_handler(engine)` builds a handler the way the test suites do:
`__new__` plus exactly the attributes `_llm_chat` reads. `ask()` runs one turn
on it with the same append-around-call shape the gateway uses, so the caller
keeps a memory of its own conversation.

What a headless handler does NOT get, on purpose:

  * a user store — so `_chat_tools_for` offers the model NO tools. The tool
    gate reads roles (bot/nlp/chat_tools.py), and "an unreadable role holds
    nothing" is its rule; a bearer token is authentication, not a role
    record.
  * a persisted store — the bot process owns `data/conversations.jsonl` and
    compacts it in place, so a second process appending to the same file
    would race that rewrite. Memory here lives as long as the process.
  * the Telegram-only parts: rate limiter, monitor, forwarder, command menu.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from bot.nlp.conversation_store import ConversationStore
from bot.nlp.sanitize import MAX_CHAT_INPUT_LEN, sanitize_chat_input
from bot.skills.telegram_handler import TelegramHandler


def headless_handler(engine: Any, *,
                     conversations: Optional[ConversationStore] = None,
                     registry: Any = None) -> TelegramHandler:
    """A `TelegramHandler` carrying only what `_llm_chat` reads."""
    h = TelegramHandler.__new__(TelegramHandler)
    h.engine = engine
    h.conversations = conversations or ConversationStore(
        max_messages_per_user=50, max_users=200, context_window=10)
    h.registry = registry
    # Deliberately unset: the type says UserStore, and the point is that
    # there is none, so the tool gate reads "no role" and offers nothing.
    h.users = None  # type: ignore[assignment]
    return h


def check_question(question: Any) -> str:
    """The stripped question, or a ValueError naming what is wrong with it.

    One place for the two bounds every caller must apply, so the bridge and
    any later transport refuse the same input the same way.
    """
    text = str(question or "").strip()
    if not text:
        raise ValueError("question required")
    if len(text) > MAX_CHAT_INPUT_LEN:
        raise ValueError(f"question too long ({MAX_CHAT_INPUT_LEN} chars max)")
    return text


async def ask(handler: TelegramHandler, question: str, *, user_id: str = "",
              user_name: str = "", is_admin: bool = False, public: bool = False,
              reply_lang: str = "", surface: str = "api") -> dict[str, Any]:
    """One chat turn: remember the question, answer it, remember the answer.

    Returns ``{"reply_html", "provider", "model", "tools", "answered_by"}``.
    ``answered_by`` is ``"model"`` when a provider produced the text and
    ``"none"`` when nothing did — the FAQ short-circuit, an exhausted budget
    and an unreachable provider all answer with prose and no model, and a
    caller that cannot tell those from a model's answer quotes an apology
    as advice. ``model`` and ``provider`` are ``""`` in that case, never a
    guess.

    A public turn (``public=True``) is account-free and remembered nowhere,
    exactly as it is on the website.
    """
    text = check_question(question)
    remember = bool(user_id) and not public
    if remember:
        handler.conversations.append(
            user_id, "user", text, metadata={"intent": "chat", "surface": surface})
    answer, meta = await handler._llm_chat(
        sanitize_chat_input(text), user_id=user_id, user_name=user_name,
        is_admin=is_admin, public=public, reply_lang=reply_lang,
        return_meta=True, surface=surface)
    tools = [str(t.get("name", "")) for t in (meta or {}).get("tools", [])]
    if remember:
        handler.conversations.append(
            user_id, "assistant", answer,
            metadata={"surface": surface,
                      **({"provider": meta["provider"], "model": meta["model"]}
                         if meta else {"answered_by": "none"}),
                      **({"tools": tools} if tools else {})})
        # Fold whatever the cap just pruned into the rolling note, off the
        # reply path — the same call the other surfaces make after a reply.
        try:
            asyncio.create_task(handler._summarize_if_due(user_id, is_admin))
        except Exception:
            pass
    return {
        "reply_html": answer,
        "provider": (meta or {}).get("provider", ""),
        "model": (meta or {}).get("model", ""),
        "tools": tools,
        "answered_by": "model" if meta else "none",
    }
