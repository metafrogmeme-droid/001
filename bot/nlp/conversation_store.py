"""
RUNECLAW Conversation Store — per-user multi-turn memory.

Stores recent messages per user for multi-turn LLM context injection.
In-memory with optional JSONL persistence. Thread-safe.

Design constraints:
  - Max messages per user (default 50) — older messages are pruned
  - Max total users tracked (default 200) — LRU eviction
  - Messages include role, content, timestamp
  - Conversation summarization for long histories
  - No secrets stored — only user text + assistant replies
"""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from bot.utils.atomic_write import atomic_write_text



@dataclass
class Message:
    """A single conversation message."""
    role: str           # "user" or "assistant"
    content: str        # Message text
    timestamp: float    # Unix timestamp
    metadata: dict = field(default_factory=dict)  # Optional: intent, symbol, etc.

    def to_llm_message(self) -> dict:
        """Convert to LLM API message format."""
        return {"role": self.role, "content": self.content}

    def age_seconds(self) -> float:
        return time.time() - self.timestamp


@dataclass
class UserContext:
    """Accumulated context about a user from their conversations."""
    preferred_assets: list[str] = field(default_factory=list)
    last_discussed_asset: str = ""
    interaction_count: int = 0
    first_seen: float = 0.0
    last_active: float = 0.0
    summary: str = ""  # Compressed summary of older conversations
    #: Turns the per-user cap pruned since `summary` was last written, in LLM
    #: message shape, waiting to be folded in. This field is what made
    #: `summary` writable at all: the docstring above promised summarization
    #: for a long time while nothing anywhere assigned it, so the prompt
    #: builder read an empty string on every turn and the fifty-first message
    #: silently erased the first.
    pending_summary: list[dict] = field(default_factory=list)
    mood_hints: list[str] = field(default_factory=list)  # Recent mood signals
    user_name: str = ""  # Display name

    def update_from_message(self, text: str) -> None:
        """Extract context signals from a user message."""
        from bot.nlp.intent_router import _extract_symbol
        self.interaction_count += 1
        self.last_active = time.time()
        if not self.first_seen:
            self.first_seen = time.time()

        # Track discussed assets
        symbol = _extract_symbol(text)
        if symbol:
            self.last_discussed_asset = symbol
            ticker = symbol.replace("/USDT", "")
            if ticker not in self.preferred_assets:
                self.preferred_assets.append(ticker)
                # Keep only last 10 preferred assets
                if len(self.preferred_assets) > 10:
                    self.preferred_assets = self.preferred_assets[-10:]

        # Detect mood signals from message
        lower = text.lower()
        mood = self._detect_mood(lower)
        if mood:
            self.mood_hints.append(mood)
            if len(self.mood_hints) > 5:
                self.mood_hints = self.mood_hints[-5:]

    @staticmethod
    def _detect_mood(text: str) -> str:
        """Detect emotional signals in user text."""
        # Frustration / confusion
        if any(w in text for w in ["wtf", "broken", "doesn't work", "not working",
                                    "confused", "don't understand", "why won't",
                                    "frustrated", "annoying", "ugh"]):
            return "frustrated"
        # Excitement / positive
        if any(w in text for w in ["awesome", "great", "love it", "amazing",
                                    "perfect", "nice", "let's go", "moon",
                                    "pumping", "lfg", "bullish af"]):
            return "excited"
        # Caution / worry
        if any(w in text for w in ["worried", "scared", "nervous", "dump",
                                    "crash", "careful", "risky", "fear"]):
            return "cautious"
        # Casual / social
        if any(w in text for w in ["lol", "haha", "lmao", "bro", "dude",
                                    "mate", "chill"]):
            return "casual"
        return ""

    @property
    def recent_mood(self) -> str:
        """Most recent mood signal, or empty."""
        return self.mood_hints[-1] if self.mood_hints else ""


class ConversationStore:
    """Per-user conversation memory with LRU eviction.

    Usage:
        store = ConversationStore()
        store.append("12345", "user", "How's BTC doing?")
        store.append("12345", "assistant", "BTC is at $67,000...")
        history = store.get_recent("12345", limit=10)
        context = store.get_context("12345")
    """

    def __init__(
        self,
        max_messages_per_user: int = 50,
        max_users: int = 200,
        persist_path: Optional[str | Path] = None,
        context_window: int = 10,
    ) -> None:
        self._max_messages = max_messages_per_user
        self._max_users = max_users
        self._context_window = context_window  # Default messages to inject
        self._lock = threading.Lock()
        # OrderedDict for LRU eviction
        self._conversations: OrderedDict[str, list[Message]] = OrderedDict()
        self._user_contexts: dict[str, UserContext] = {}
        self._persist_path = Path(persist_path) if persist_path else None

        if self._persist_path:
            self._load()

    def append(self, user_id: str, role: str, content: str,
               metadata: Optional[dict] = None) -> None:
        """Append a message to a user's conversation history."""
        if not content or not content.strip():
            return

        msg = Message(
            role=role,
            content=content.strip(),
            timestamp=time.time(),
            metadata=metadata or {},
        )

        with self._lock:
            # Move user to end (most recently active)
            if user_id in self._conversations:
                self._conversations.move_to_end(user_id)
            else:
                self._conversations[user_id] = []

            self._conversations[user_id].append(msg)

            # Update user context
            if user_id not in self._user_contexts:
                self._user_contexts[user_id] = UserContext()
            if role == "user":
                self._user_contexts[user_id].update_from_message(content)

            # Prune oldest messages if over limit — and KEEP what was pruned,
            # in message shape, until a summary has folded it in. Pruned is
            # not forgotten: it is the queue the summary is written from.
            if len(self._conversations[user_id]) > self._max_messages:
                overflow = self._conversations[user_id][:-self._max_messages]
                self._conversations[user_id] = \
                    self._conversations[user_id][-self._max_messages:]
                ctx = self._user_contexts[user_id]
                ctx.pending_summary.extend(m.to_llm_message() for m in overflow)
                if len(ctx.pending_summary) > self.PENDING_SUMMARY_MAX:
                    ctx.pending_summary = \
                        ctx.pending_summary[-self.PENDING_SUMMARY_MAX:]

            # LRU eviction of oldest users
            while len(self._conversations) > self._max_users:
                self._conversations.popitem(last=False)

        if self._persist_path:
            self._persist_message(user_id, msg)

    def get_recent(self, user_id: str, limit: Optional[int] = None) -> list[Message]:
        """Get recent messages for a user."""
        limit = limit or self._context_window
        with self._lock:
            msgs = self._conversations.get(user_id, [])
            return list(msgs[-limit:])

    def get_recent_as_llm_messages(self, user_id: str,
                                    limit: Optional[int] = None,
                                    *, drop_trailing_user: bool = False
                                    ) -> list[dict]:
        """Recent turns as LLM `messages`, starting on a USER turn.

        Two shapes the raw slice gets wrong, both invisible at the call site:

        DUPLICATED QUESTION. Both transports append the user's turn to this
        store BEFORE calling the chat path, which then reads history back and
        hands `user_prompt` to `llm_complete` separately — and `llm_complete`
        appends that too. The API received the question as two consecutive
        user turns. `drop_trailing_user` removes the echo at the source rather
        than asking every caller to slice.

        ASSISTANT-FIRST HISTORY. With an even limit over an alternating log,
        the window starts on an assistant turn from the fifth exchange onward
        (9 stored, `[-8:]` drops index 0). Anthropic requires the first entry
        in `messages` to be a user turn, so on any established conversation
        the Anthropic candidate 400s and the chain falls through to the next
        provider — a silent downgrade to a worse model, visible only as
        `chat_fallback` audit lines. Dropping the leading assistant turn costs
        one message of context and keeps the good provider.
        """
        msgs = [m.to_llm_message() for m in self.get_recent(user_id, limit)]
        if drop_trailing_user and msgs and msgs[-1].get("role") == "user":
            msgs = msgs[:-1]
        while msgs and msgs[0].get("role") != "user":
            msgs = msgs[1:]
        return msgs

    def get_context(self, user_id: str) -> Optional[UserContext]:
        """Get accumulated user context."""
        with self._lock:
            return self._user_contexts.get(user_id)

    # ── Summary of what the cap pruned ───────────────────────────
    #
    # `UserContext.summary` is read by build_context_prompt and was assigned
    # by nothing. The queue below is filled by append() as it prunes, drained
    # by the chat handler, which asks the cheapest chat-tier model to fold
    # the pruned turns into the existing note, and written back with
    # set_summary(). The note is persisted as its own JSONL row (role
    # "summary") so a restart keeps it, and it is never a message: get_recent
    # cannot return it and the model never sees it as a turn.

    #: How many pruned turns wait for a summary at most. Beyond this the
    #: oldest are dropped — a queue that grows without bound while the model
    #: is unreachable is a memory leak dressed as memory.
    PENDING_SUMMARY_MAX = 60
    #: Bound on the note itself. The prompt carries it on every turn.
    SUMMARY_MAX_CHARS = 900

    def take_pending_summary(self, user_id: str) -> list[dict]:
        """The pruned turns not yet folded into the summary, and clear them.

        The caller owns them from here: if it cannot summarise (no model
        configured, the call failed) it hands them back with
        `push_back_pending`, so a transient failure loses nothing and a
        permanent one is bounded by PENDING_SUMMARY_MAX rather than infinite."""
        with self._lock:
            ctx = self._user_contexts.get(user_id)
            if ctx is None or not ctx.pending_summary:
                return []
            pending = list(ctx.pending_summary)
            ctx.pending_summary = []
            return pending

    def push_back_pending(self, user_id: str, turns: list[dict]) -> None:
        """Return turns `take_pending_summary` handed out but nobody folded."""
        if not turns:
            return
        with self._lock:
            ctx = self._user_contexts.setdefault(user_id, UserContext())
            ctx.pending_summary = (list(turns) + ctx.pending_summary)[
                -self.PENDING_SUMMARY_MAX:]

    def set_summary(self, user_id: str, text: str) -> None:
        """Write the rolling note for `user_id` and persist it.

        An empty text clears the note — deliberately possible, so a summary
        that turned out wrong can be removed rather than only overwritten."""
        note = (text or "").strip()[:self.SUMMARY_MAX_CHARS]
        with self._lock:
            ctx = self._user_contexts.setdefault(user_id, UserContext())
            ctx.summary = note
        if self._persist_path:
            self._persist_summary(user_id, note)

    def clear_user(self, user_id: str) -> None:
        """Clear all conversation history for a user."""
        with self._lock:
            self._conversations.pop(user_id, None)
            self._user_contexts.pop(user_id, None)

    def clear_all(self) -> None:
        """Clear all conversation data."""
        with self._lock:
            self._conversations.clear()
            self._user_contexts.clear()

    def user_count(self) -> int:
        """Number of users with conversation history."""
        with self._lock:
            return len(self._conversations)

    def message_count(self, user_id: str) -> int:
        """Number of stored messages for a user."""
        with self._lock:
            return len(self._conversations.get(user_id, []))

    def build_context_prompt(self, user_id: str, portfolio_summary: str = "",
                              engine_state: str = "",
                              user_name: str = "") -> str:
        """Build a context block to inject into the system prompt.

        Returns a string with user-specific context that makes the
        conversation feel continuous and personalized.
        """
        ctx = self.get_context(user_id)
        if not ctx:
            return ""

        # Store user name if provided
        if user_name and not ctx.user_name:
            ctx.user_name = user_name

        parts = []

        display_name = ctx.user_name or user_name
        if display_name:
            parts.append(f"User's name: {display_name}")
        if ctx.last_discussed_asset:
            parts.append(
                f"Last discussed asset: {ctx.last_discussed_asset}")
        if ctx.preferred_assets:
            assets = ", ".join(ctx.preferred_assets[-5:])
            parts.append(f"User's frequently discussed assets: {assets}")
        if ctx.interaction_count > 1:
            parts.append(
                f"This user has sent {ctx.interaction_count} messages "
                f"(returning user).")
        if ctx.recent_mood:
            mood_map = {
                "frustrated": "User seems frustrated — be patient and helpful",
                "excited": "User is in a good/excited mood — match their energy",
                "cautious": "User seems worried or cautious — be reassuring and measured",
                "casual": "User is being casual/informal — match their relaxed tone",
            }
            parts.append(mood_map.get(ctx.recent_mood,
                                       f"User mood: {ctx.recent_mood}"))
        if portfolio_summary:
            parts.append(f"Current portfolio: {portfolio_summary}")
        if engine_state:
            parts.append(f"Engine state: {engine_state}")
        if ctx.summary:
            parts.append(f"Previous conversation summary: {ctx.summary}")

        if not parts:
            return ""

        return "\n\nUser context:\n" + "\n".join(f"- {p}" for p in parts)

    # ── Persistence ──────────────────────────────────────────────

    def _persist_message(self, user_id: str, msg: Message) -> None:
        """Append a message to JSONL file."""
        if not self._persist_path:
            return
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "user_id": user_id,
                "role": msg.role,
                "content": msg.content[:2000],  # Cap stored content
                "timestamp": msg.timestamp,
                "metadata": msg.metadata,
            }
            with open(self._persist_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            pass  # Non-critical — memory store is primary

    def _persist_summary(self, user_id: str, note: str) -> None:
        """Append the rolling note as a `summary` row. The LAST such row for a
        user wins on load, so an update is an append, like everything else in
        this file, and compaction keeps only the current one."""
        if not self._persist_path:
            return
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "user_id": user_id,
                "role": "summary",
                "content": note[:self.SUMMARY_MAX_CHARS],
                "timestamp": time.time(),
                "metadata": {},
            }
            with open(self._persist_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            pass

    def _load(self) -> None:
        """Load conversation history from JSONL file."""
        if not self._persist_path or not self._persist_path.exists():
            return
        try:
            with open(self._persist_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        uid = entry["user_id"]
                        if entry["role"] == "summary":
                            # A note, not a turn: it must never come back
                            # from get_recent as something somebody said.
                            ctx = self._user_contexts.setdefault(
                                uid, UserContext())
                            ctx.summary = str(entry.get("content") or "")[
                                :self.SUMMARY_MAX_CHARS]
                            continue
                        msg = Message(
                            role=entry["role"],
                            content=entry["content"],
                            timestamp=entry.get("timestamp", 0),
                            metadata=entry.get("metadata", {}),
                        )
                        if uid not in self._conversations:
                            self._conversations[uid] = []
                        self._conversations[uid].append(msg)

                        if uid not in self._user_contexts:
                            self._user_contexts[uid] = UserContext()
                        if msg.role == "user":
                            self._user_contexts[uid].update_from_message(
                                msg.content)
                    except (KeyError, json.JSONDecodeError):
                        continue

            # Prune loaded data to limits
            for uid in list(self._conversations.keys()):
                if len(self._conversations[uid]) > self._max_messages:
                    self._conversations[uid] = \
                        self._conversations[uid][-self._max_messages:]
            self._maybe_compact()
        except OSError:
            pass

    # Rewrite the JSONL from retained in-memory state once the on-disk file
    # outgrows what memory keeps. The file is append-only and was NEVER
    # pruned, so a long-lived bot re-parsed an ever-growing history on every
    # restart while keeping only max_messages/user of it.
    COMPACT_THRESHOLD_LINES = 5000

    def _maybe_compact(self) -> None:
        try:
            with open(self._persist_path) as f:
                raw_lines = sum(1 for _ in f)
            retained = sum(len(v) for v in self._conversations.values())
            if raw_lines <= max(self.COMPACT_THRESHOLD_LINES, retained):
                return
            rows = [
                json.dumps({
                    "user_id": uid,
                    "role": msg.role,
                    "content": msg.content[:2000],
                    "timestamp": msg.timestamp,
                    "metadata": msg.metadata,
                }) + "\n"
                for uid, msgs in self._conversations.items()
                for msg in msgs]
            # The current note per user survives compaction; the superseded
            # ones it was appended over do not — that is the compaction.
            rows.extend(
                json.dumps({
                    "user_id": uid,
                    "role": "summary",
                    "content": ctx.summary[:self.SUMMARY_MAX_CHARS],
                    "timestamp": ctx.last_active or time.time(),
                    "metadata": {},
                }) + "\n"
                for uid, ctx in self._user_contexts.items()
                if ctx.summary)
            atomic_write_text(self._persist_path, "".join(rows))
        except OSError:
            pass  # compaction is an optimization, never a requirement

    def stats(self) -> dict:
        """Return store statistics."""
        with self._lock:
            total_msgs = sum(
                len(msgs) for msgs in self._conversations.values())
            return {
                "users": len(self._conversations),
                "total_messages": total_msgs,
                "max_users": self._max_users,
                "max_messages_per_user": self._max_messages,
                "context_window": self._context_window,
            }
