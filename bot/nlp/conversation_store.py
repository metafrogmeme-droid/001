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
import math
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from bot.utils.atomic_write import atomic_write_text

#: Below this age a turn carries no stamp. "[12 s ago]" on every live
#: exchange is noise, and the age that matters is the one the model cannot
#: see — hours and days, when Monday's `[get_portfolio] result:` is read on
#: Friday as the current book.
FRESH_SECONDS = 60.0


def turn_time(ts) -> Optional[float]:
    """``ts`` as a unix time, or None when the record has none.

    PUBLIC because it is one reading with more than one module reading it:
    everything dated in this file, and the chat prompt's unprompted-alerts
    block, which asks the same question about the same rows. A second copy
    of "is this a time the record holds" would be a second answer.

    The loader defaults a row with no timestamp to 0, and 0 is not the epoch,
    it is an absence — rendered as an age it reads "56 y ago", which is a
    confident number about a moment nobody recorded."""
    if isinstance(ts, bool) or not isinstance(ts, (int, float)):
        return None
    if not math.isfinite(ts) or ts <= 0:
        return None
    return float(ts)


def _announced_cut(body: str, cap: int) -> str:
    """``body`` bounded to ``cap``, with the truncation SAID rather than done
    quietly. One reading, two callers - `note_alert` writes the row and
    `_load` reads one back - because a second copy would be a second answer
    about how much of an alert is on record. The marker goes on the END, so a
    silent cap anywhere downstream would remove the only thing announcing it.
    """
    if len(body) <= cap:
        return body
    return body[:cap] + f"\n[… {len(body) - cap} more characters not recorded]"


def age_words(seconds: float) -> str:
    """A duration in words: '45 s', '4 min', '3 h 2 min', '2 d 5 h'."""
    s = max(0, int(seconds))
    if s < 60:
        return f"{s} s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m} min"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h} h {m} min" if m else f"{h} h"
    d, h = divmod(h, 24)
    return f"{d} d {h} h" if h else f"{d} d"


def when_words(ts) -> str:
    """An absolute date for the note-writer ('2026-09-10 14:02 UTC') —
    relative ages rot inside a note that is read weeks later — or 'time not
    on record'."""
    t = turn_time(ts)
    if t is None:
        return "time not on record"
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(t))


@dataclass
class Message:
    """A single conversation message."""
    role: str           # "user" or "assistant"
    content: str        # Message text
    timestamp: float    # Unix timestamp; 0 means NOT ON RECORD (see turn_time)
    metadata: dict = field(default_factory=dict)  # Optional: intent, symbol, etc.

    def age_seconds(self, now: Optional[float] = None) -> Optional[float]:
        """Seconds since this turn, or None when its time is not on record."""
        t = turn_time(self.timestamp)
        if t is None:
            return None
        return max(0.0, (time.time() if now is None else now) - t)

    def is_tool_record(self) -> bool:
        """Whether this turn is what a TOOL said — the `[skill] result:` shape
        `skill_memory` writes, recorded with the skill in its metadata by every
        dispatch site — rather than something the user or the model said."""
        return self.role == "assistant" and bool((self.metadata or {}).get("skill"))

    def age_stamp(self, now: Optional[float] = None) -> str:
        """The bracketed age the model reads this turn under, '' when fresh.

        `to_llm_message` dropped the timestamp, so a `[get_portfolio] result:`
        recorded on Monday was handed to the model on Friday shaped exactly
        like one recorded a second ago, and restated as the current book. The
        stamp is the MEASUREMENT (how old), plus the one fact true of any
        record (it is as of then); the verdict — call the tool again — is the
        prompt rule's, so a two-minute-old snapshot is not called stale here.
        A tool record whose time is not on record says so rather than
        carrying an age computed from 0.
        """
        age = self.age_seconds(now)
        if age is not None and age < FRESH_SECONDS:
            return ""
        if self.is_tool_record():
            if age is None:
                return ("[recorded at a time not on record — as of some earlier "
                        "moment, not now]")
            return f"[recorded {age_words(age)} ago — as of then, not now]"
        return "[time not on record]" if age is None else f"[{age_words(age)} ago]"

    def to_llm_message(self, now: Optional[float] = None) -> dict:
        """Convert to LLM API message format, with the turn's age in front of
        it when it is not fresh (a tool record on its own first line, so the
        `[skill] result:` marker stays at the start of a line for every reader
        that anchors on it)."""
        stamp = self.age_stamp(now)
        if not stamp:
            return {"role": self.role, "content": self.content}
        sep = "\n" if self.is_tool_record() else " "
        return {"role": self.role, "content": f"{stamp}{sep}{self.content}"}

    def to_summary_turn(self) -> dict:
        """The shape the pruned queue keeps for the note-writer: the turn with
        the ABSOLUTE date it was said, so the note can say "as of 2026-09-10
        the user held ETH" instead of "the user holds ETH" forever."""
        return {"role": self.role, "content": self.content,
                "at": when_words(self.timestamp)}


@dataclass
class UserContext:
    """Accumulated context about a user from their conversations."""
    preferred_assets: list[str] = field(default_factory=list)
    #: How many distinct mentioned assets are kept. Written as the literal 10
    #: twice in the writer and read as a SECOND bound of 5 in the renderer -
    #: two caps, neither named, on a line headed "Assets the user has
    #: mentioned". The renderer shows all of these and names this one.
    PREFERRED_ASSETS_MAX = 10
    last_discussed_asset: str = ""
    interaction_count: int = 0
    first_seen: float = 0.0
    last_active: float = 0.0
    summary: str = ""  # Compressed summary of older conversations
    #: When `summary` was written (unix time); 0.0 = not on record. The note
    #: is an LLM's undated paraphrase injected on every later turn, and the
    #: prompt names its age so "the user holds ETH" is read as a dated claim.
    summary_at: float = 0.0
    #: When `last_discussed_asset` was mentioned; 0.0 = not on record.
    last_discussed_at: float = 0.0
    #: ticker -> how many of the user's messages mentioned it.
    asset_mentions: dict[str, int] = field(default_factory=dict)
    #: Turns the per-user cap pruned since `summary` was last written, in LLM
    #: message shape, waiting to be folded in. This field is what made
    #: `summary` writable at all: the docstring above promised summarization
    #: for a long time while nothing anywhere assigned it, so the prompt
    #: builder read an empty string on every turn and the fifty-first message
    #: silently erased the first.
    pending_summary: list[dict] = field(default_factory=list)
    mood_hints: list[str] = field(default_factory=list)  # Recent mood signals
    user_name: str = ""  # Display name
    #: What the bot told this user UNPROMPTED, newest last — `{at, kind, text}`
    #: per row, bounded by `ConversationStore.NOTIFICATIONS_MAX`.
    #:
    #: A RING RATHER THAN MESSAGES, and the arithmetic is the reason. An alert
    #: is a NOTIFICATION, not a conversation turn, and appending one to
    #: `_conversations` would put it under `max_messages_per_user` (50) with
    #: everything the user actually said. `anomaly_scope` budgets ADVISORY
    #: alerts at twelve an hour and exempts CRITICAL on purpose, so a watching
    #: user's own conversation would be evicted inside about four hours — and
    #: not merely dropped: `append` pushes what it prunes into
    #: `pending_summary`, so the rolling note would then summarise the bot
    #: talking to itself. Kept apart, the two cannot crowd each other however
    #: loud the channel gets.
    notifications: list[dict] = field(default_factory=list)

    def update_from_message(self, text: str, now: Optional[float] = None) -> None:
        """Extract context signals from a user message.

        ``now`` is the message's own time — the loader passes the stored
        timestamp, so a restart does not re-date every mention to boot time.
        """
        # `mentioned_symbol`, not `_extract_symbol`: the recall line is a
        # claim about the user, and the whole-text reader put NEAR/USDT in it
        # off "why did you enter near the top".
        from bot.nlp.intent_router import mentioned_symbol
        now = time.time() if now is None else now
        self.interaction_count += 1
        self.last_active = now
        if not self.first_seen:
            self.first_seen = now

        # Track mentioned assets
        symbol = mentioned_symbol(text)
        if symbol:
            self.last_discussed_asset = symbol
            self.last_discussed_at = now
            ticker = symbol.replace("/USDT", "")
            self.asset_mentions[ticker] = self.asset_mentions.get(ticker, 0) + 1
            if ticker not in self.preferred_assets:
                self.preferred_assets.append(ticker)
                if len(self.preferred_assets) > self.PREFERRED_ASSETS_MAX:
                    self.preferred_assets = self.preferred_assets[
                        -self.PREFERRED_ASSETS_MAX:]

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
                ctx.pending_summary.extend(m.to_summary_turn() for m in overflow)
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
                                    *, drop_trailing_user: bool = False,
                                    now: Optional[float] = None
                                    ) -> list[dict]:
        """Recent turns as LLM `messages`, starting on a USER turn, each
        carrying its age when it is not fresh (`Message.age_stamp`).

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
        msgs = [m.to_llm_message(now) for m in self.get_recent(user_id, limit)]
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

    #: How many unprompted messages are remembered per user. A burst is
    #: twelve an hour by `anomaly_scope`'s budget, and somebody asking "what
    #: was that?" means the last one or two; eight covers a burst without
    #: crowding out the blocks beside it in the prompt.
    NOTIFICATIONS_MAX = 8
    #: Bound on ONE remembered alert. Truncation is announced by the shared
    #: marker every other record uses, never silent.
    NOTIFICATION_MAX_CHARS = 400

    def note_alert(self, user_id: str, kind: str, text: str, *,
                   at: Optional[float] = None) -> None:
        """Remember that the bot told `user_id` something they did not ask for.

        Called at the DELIVERY site, once per recipient, after the send
        succeeded — what was SENT, not what was built, which is the correction
        `_mark_sent` already records for the channel budget.

        It creates a `UserContext` where there is none, which `_user_contexts`
        has never evicted (`append`'s LRU bounds `_conversations` alone). That
        is bounded on the caller's side rather than here: the only writer is
        `_note_unprompted`, which refuses a chat the bot has not ADMITTED, so
        the set is the admitted users and each of them holds at most
        `NOTIFICATIONS_MAX` rows of `NOTIFICATION_MAX_CHARS`.
        """
        from bot.nlp.skill_memory import plain_text
        body = plain_text(text)
        if not body:
            return
        body = _announced_cut(body, self.NOTIFICATION_MAX_CHARS)
        # `turn_time` rather than a bare float(): an `at` that is not a
        # usable time records 0.0, which is this file's word for NOT ON
        # RECORD, and the renderer says so. Manufacturing `now` for a moment
        # the caller could not name would be the `fmtAgo(0)` shape.
        row = {"at": time.time() if at is None else (turn_time(at) or 0.0),
               "kind": str(kind or "alert"), "text": body}
        with self._lock:
            ctx = self._user_contexts.setdefault(user_id, UserContext())
            ctx.notifications = (ctx.notifications + [row])[
                -self.NOTIFICATIONS_MAX:]
        if self._persist_path:
            self._persist_alert(user_id, row)

    def recent_alerts(self, user_id: str) -> list[dict]:
        """What this user was told unprompted, oldest first. A COPY, because
        the caller renders it and must not be able to edit the store."""
        with self._lock:
            ctx = self._user_contexts.get(user_id)
            return [dict(r) for r in ctx.notifications] if ctx else []

    def set_summary(self, user_id: str, text: str, *,
                    at: Optional[float] = None) -> None:
        """Write the rolling note for `user_id`, dated, and persist it.

        An empty text clears the note — deliberately possible, so a summary
        that turned out wrong can be removed rather than only overwritten.
        ``at`` is when the note was written (now, unless a test says)."""
        note = (text or "").strip()[:self.SUMMARY_MAX_CHARS]
        written = (time.time() if at is None else float(at)) if note else 0.0
        with self._lock:
            ctx = self._user_contexts.setdefault(user_id, UserContext())
            ctx.summary = note
            ctx.summary_at = written
        if self._persist_path:
            self._persist_summary(user_id, note, written)

    def clear_user(self, user_id: str) -> bool:
        """Erase everything held for one user — in memory AND in the file
        memory is reloaded from. True when anything was held.

        The JSONL is append-only and `_load` replays it on the next boot, so
        popping the in-memory rows alone was a deletion that lasted until the
        next restart — and `handle_account_purge` did not even do that: this
        method had no caller outside a test, so a purged account's whole
        chat history, its rolling summary and its "last discussed" recall
        came back with the process. The file is rewritten without the user's
        rows, and an OSError there PROPAGATES: a purge that could not reach
        the disk must say so, not report the memory half as the whole.

        The rewrite runs under the lock; `append` persists its row outside
        it, so a row another user appends in the microseconds between the
        read and the replace can be lost — the window `_maybe_compact` has
        always had, and a chat message, never a credential.
        """
        with self._lock:
            had_rows = self._conversations.pop(user_id, None) is not None
            had_ctx = self._user_contexts.pop(user_id, None) is not None
            had_disk = self._forget_on_disk(user_id)
        return had_rows or had_ctx or had_disk

    def _forget_on_disk(self, user_id: str) -> bool:
        """Rewrite the JSONL without one user's rows. True when any were there.

        A line that does not parse as one of this store's rows is kept as it
        is: it is not evidence about the user, and dropping it would make a
        deletion into a repair. Raises on a disk fault, for `clear_user`'s
        reason.
        """
        if not self._persist_path or not self._persist_path.exists():
            return False
        with open(self._persist_path) as f:
            lines = f.readlines()
        kept: list[str] = []
        dropped = 0
        for line in lines:
            try:
                owner = json.loads(line).get("user_id")
            except (ValueError, AttributeError):
                kept.append(line)
                continue
            if str(owner) == str(user_id):
                dropped += 1
            else:
                kept.append(line)
        if not dropped:
            return False
        atomic_write_text(self._persist_path, "".join(kept))
        return True

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
                              user_name: str = "",
                              now: Optional[float] = None) -> str:
        """Build a context block to inject into the system prompt.

        Returns a string with user-specific context that makes the
        conversation feel continuous and personalized. Every line that is a
        claim about the user says what it was read from: a MENTION is not a
        holding, and the memory note is the assistant's own undated paraphrase
        until this names how old it is.
        """
        ctx = self.get_context(user_id)
        if not ctx:
            return ""
        now_t = time.time() if now is None else now

        def _ago(ts: float, absent: str = "time not on record") -> str:
            t = turn_time(ts)
            return f"{age_words(now_t - t)} ago" if t is not None else absent

        # Store user name if provided
        if user_name and not ctx.user_name:
            ctx.user_name = user_name

        parts = []

        display_name = ctx.user_name or user_name
        if display_name:
            parts.append(f"User's name: {display_name}")
        if ctx.last_discussed_asset:
            parts.append(
                f"Asset the user last MENTIONED: {ctx.last_discussed_asset} "
                f"({_ago(ctx.last_discussed_at)}) — a mention in their own "
                "words, not a holding or a position")
        if ctx.preferred_assets:
            # ONE BOUND, NAMED. This was `[-5:]` over a list the writer had
            # already capped at 10: two caps, neither said, on a line a model
            # answers "have I mentioned SOL?" from - the bounded-list-printed-
            # as-whole shape the unprompted-alerts block was cured of. The
            # render cap is gone (all that is kept is shown) so there is only
            # the writer's, and the sentence names it.
            assets = ", ".join(
                f"{a} x{ctx.asset_mentions[a]}" if ctx.asset_mentions.get(a) else a
                for a in ctx.preferred_assets)
            parts.append(
                "Assets the user has mentioned (mentions in their own "
                f"messages, not holdings; only the {UserContext.PREFERRED_ASSETS_MAX} "
                "most recently mentioned are kept, so one they name that is "
                f"not listed may still have been mentioned): {assets}")
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
            parts.append(
                "Memory note (written by the assistant "
                f"{_ago(ctx.summary_at, 'at a time not on record')} "
                "from older turns no longer in this history; UNVERIFIED and "
                "possibly out of date — nothing in it is current: never state "
                "a position, balance, price or figure from it as the present "
                f"state; call a tool or ask the user): {ctx.summary}")

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

    def _persist_summary(self, user_id: str, note: str, at: float) -> None:
        """Append the rolling note as a `summary` row. The LAST such row for a
        user wins on load, so an update is an append, like everything else in
        this file, and compaction keeps only the current one. ``timestamp`` is
        when the note was WRITTEN, which is what the prompt reports back."""
        if not self._persist_path:
            return
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "user_id": user_id,
                "role": "summary",
                "content": note[:self.SUMMARY_MAX_CHARS],
                "timestamp": at,
                "metadata": {},
            }
            with open(self._persist_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            pass

    def _persist_alert(self, user_id: str, row: dict) -> None:
        """Append one remembered alert as an `alert` row.

        The row shape is the file's - `user_id`, `role`, `content`,
        `timestamp`, `metadata` - so `_forget_on_disk` drops a purged
        account's alerts by the same reading that drops their turns, rather
        than needing to learn a second shape. The KIND rides in `metadata`
        for the same reason: a new top-level key would be invisible to every
        row reader already here.

        The body is NOT capped again. `note_alert` bounded it with the
        truncation announced at the end, and a second cut here would remove
        the marker and turn an announced truncation into a silent one.
        """
        if not self._persist_path:
            return
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "user_id": user_id,
                "role": "alert",
                "content": row["text"],
                "timestamp": row["at"],
                "metadata": {"kind": row["kind"]},
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
                            ctx.summary_at = (turn_time(entry.get("timestamp"))
                                              or 0.0) if ctx.summary else 0.0
                            continue
                        if entry["role"] == "alert":
                            # An unprompted NOTIFICATION, and neither of the
                            # two things this loop otherwise builds. Not a
                            # turn - `get_recent` must never hand it back as
                            # something somebody said - and not a MENTION
                            # either: the bot naming ETH/USDT in a stop-loss
                            # card is the BOT discussing it, so it must not
                            # reach `update_from_message` and become "the
                            # asset the user last mentioned".
                            text = _announced_cut(
                                str(entry.get("content") or ""),
                                self.NOTIFICATION_MAX_CHARS)
                            if not text.strip():
                                # An alert with no body is not an alert, and
                                # the ring is BOUNDED: keeping one would
                                # evict a real notification to hold a row
                                # nothing can render. `note_alert` refuses
                                # the same row on the way in.
                                continue
                            ctx = self._user_contexts.setdefault(
                                uid, UserContext())
                            ctx.notifications = (ctx.notifications + [{
                                # 0.0 is an ABSENCE here, as everywhere else
                                # in this file; the renderer says so rather
                                # than ageing it.
                                "at": turn_time(entry.get("timestamp")) or 0.0,
                                "kind": str((entry.get("metadata") or {}).get(
                                    "kind") or "alert"),
                                "text": text,
                            }])[-self.NOTIFICATIONS_MAX:]
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
                            # The turn's own time, so a restart does not
                            # re-date every mention to boot time; a row
                            # with no time leaves the mention undated.
                            self._user_contexts[uid].update_from_message(
                                msg.content, now=turn_time(msg.timestamp) or 0.0)
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
            # ones it was appended over do not — that is the compaction. The
            # note keeps ITS date: `last_active or time.time()` re-dated a
            # month-old note to the user's last message, or to the restart.
            rows.extend(
                json.dumps({
                    "user_id": uid,
                    "role": "summary",
                    "content": ctx.summary[:self.SUMMARY_MAX_CHARS],
                    "timestamp": ctx.summary_at,
                    "metadata": {},
                }) + "\n"
                for uid, ctx in self._user_contexts.items()
                if ctx.summary)
            # THE RING SURVIVES COMPACTION, and it only does because it is
            # written out here. This rewrite is from IN-MEMORY state, so a row
            # type the loops above do not re-emit is not pruned, it is
            # DESTROYED - the shape `secrets_vault._load_vault` is on record
            # for, where the reader dropped what it could not open and the
            # writer then saved the map wholesale. The turns and the note are
            # re-emitted above for exactly this reason.
            rows.extend(
                json.dumps({
                    "user_id": uid,
                    "role": "alert",
                    "content": row["text"],
                    "timestamp": row["at"],
                    "metadata": {"kind": row["kind"]},
                }) + "\n"
                for uid, ctx in self._user_contexts.items()
                for row in ctx.notifications)
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
