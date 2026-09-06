"""The chat's runtime pieces that are not the handler.

`bot/skills/telegram_handler.py` is 15,000-plus lines, 138 commands, and the
chat brain in one file. This is the first slice out of it, chosen for what
it does NOT touch: everything here is a leaf. The per-user rate limiter, the
chain's two timing constants, the thinking phrases, the two tool rules the
system prompt carries, the Telegram edit-stream, the event fan-out and the
reply funnel (`_chat_ret`) read nothing from the handler and are read by it.

What stays behind, and why: `_llm_chat` and `_chat_tools_for` read the
handler's module globals — `CONFIG`, `llm_complete`, `create_llm_client`,
`resolve_tier_config`, `resolve_profile_note` — and eighteen test files
monkeypatch exactly those names on the handler module to plant a provider,
a budget or a tier. Move the brain and every one of those patches lands on
a namespace the brain no longer reads. So the brain stays where its seams
are, and its plumbing moved here.

The handler re-exports every name below under its original spelling; ninety
test files import from the handler, and none of them had to change. A test
pins both facts: the definitions live here, the names still resolve there.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from collections import defaultdict

from bot.utils.i18n import t
from bot.utils.logger import audit, system_log

logger = logging.getLogger(__name__)


class RateLimiter:
    def __init__(self, max_per_minute: int = 20) -> None:
        self._limit = max_per_minute
        self._calls: dict[int, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def allow(self, user_id: int) -> bool:
        with self._lock:
            now = time.time()
            window = [t for t in self._calls[user_id] if now - t < 60]
            self._calls[user_id] = window
            if len(window) >= self._limit:
                return False
            self._calls[user_id].append(now)
            # F-13 FIX: prune stale user entries to prevent unbounded dict growth
            if len(self._calls) > 500:
                stale = [uid for uid, ts in self._calls.items()
                         if not ts or now - ts[-1] > 300]
                for uid in stale:
                    del self._calls[uid]
            return True


# Floor on a chat attempt whose timeout has been clamped to the remaining chain
# deadline. Once less than this is left, starting one more provider buys a
# near-certain timeout: it still bills the prompt tokens, still spends the
# user's patience, and cannot plausibly return a 1024-token answer (flash-class
# p50 ~2-4s). 0.2s left is not an attempt, it is a slower way to fail. Below
# this the chain stops and SAYS it stopped.
#
# A constant rather than a second env knob deliberately: one dial per decision.
# A floor an operator can raise above the deadline is a way to configure a chain
# that never tries anything.
CHAT_MIN_ATTEMPT_SEC = 6.0

# A tool-calling attempt is one model call, then a tool, then another model
# call — so it is allowed more wall-clock than the per-attempt LLM timeout, up
# to this, and never more than what is LEFT of the chain's deadline. A constant
# for the same reason CHAT_MIN_ATTEMPT_SEC is one: a second knob that can be
# raised past the deadline is a way to configure an attempt that never ends.
CHAT_TOOL_ATTEMPT_SEC = 30.0

#: The thinking phrases, as dictionary keys: the chat's first words to a user
#: were an English literal while the answer that followed was in their
#: language. `thinking_phrase()` draws one in the caller's dictionary language.
THINKING_PHRASE_KEYS: tuple[str, ...] = tuple(f"chat_thinking_{i}" for i in range(9))


def thinking_phrase(lang: str = "en") -> str:
    """A varied "working on it" line, in the user's dictionary language."""
    import random
    return t(random.choice(THINKING_PHRASE_KEYS), lang)


def _say(lang: str, key: str, en: str) -> str:
    """The chat's own words in the user's dictionary language.

    The ENGLISH text stays at the call site, as the default — two source-scan
    suites pin these sentences to the branches that produce them (a reply
    that blames availability for an empty completion sent an operator to
    check a tunnel), and a key would hide the words from them. The
    dictionary's English entry is the same wording; a test pins that.
    """
    if lang and lang != "en":
        out = t(key, lang)
        if out and out != key:
            return out
    return en


# The tool rule in the chat system prompt, in its two states. Exactly one of
# them is in the prompt on any turn — `_llm_chat` substitutes the second for
# the first when it attaches tools — so the model is never told both that it
# cannot run a tool and that it should.
_CHAT_NO_TOOLS_RULE = (
    "- Earlier assistant turns may contain blocks like '[analyze_asset] "
    "result: ...'. Those are outputs from tools that ALREADY RAN, kept so "
    "you can refer back to them. NEVER write such a block yourself. You "
    "cannot run a tool from this chat, so a '[scan_symbol] result:' or a "
    "'[PENDING] scanning...' written by you is a claim that something "
    "executed when nothing did. If a fresh scan is needed, say so in your "
    "own words.\n\n"
)
_CHAT_TOOLS_RULE = (
    "- You have TOOLS in this conversation (they are listed in the API tool "
    "definitions). When the answer depends on the user's account, positions, "
    "PnL, risk state, costs, the macro calendar, what is moving, or why a "
    "trade was rejected, CALL the tool and answer from what it returns. A "
    "tool's output is a measurement; your memory of an earlier turn is not — "
    "positions close and prices move. Earlier assistant turns may contain "
    "blocks like '[get_portfolio] result: ...': those were written by the "
    "runtime after a tool really ran. NEVER write such a block yourself, and "
    "never claim a tool ran unless you called it in this turn. If a tool "
    "answers UNAVAILABLE, FAILED, TIMED OUT or NOT RUN, say so plainly and "
    "do not fill the gap with a guess. Deeper work — a full analysis, a "
    "backtest, a deep scan — is not a tool here; tell the user to ask for "
    "it directly, e.g. 'analyze BTC'.\n\n"
)


async def _emit_event(on_event, event: dict) -> None:
    """Deliver one streaming event to a listener that may be sync or async.
    A listener observes a reply; it is never allowed to break one."""
    if on_event is None:
        return
    try:
        import inspect
        result = on_event(event)
        if inspect.isawaitable(result):
            await result
    except Exception as exc:
        logger.debug("chat stream listener failed: %s", exc)


class TelegramStream:
    """Turn one Telegram message into the streamed reply, edit by edit.

    Telegram has no stream; it has `edit_message_text`, rate-limited to about
    one edit a second per chat. So the thinking message is edited with the
    provisional text as fragments arrive — at most every MIN_INTERVAL seconds
    and only once at least MIN_GROWTH new characters exist — and edited one
    last time with the FINAL, checked answer. The provisional text is shown
    plain (tags stripped) because a fragment can end inside a tag; the final
    edit carries the HTML.

    An edit that fails (rate limit, message deleted) turns editing off for
    the rest of the turn rather than retrying into the same limit; the final
    answer then goes out as a fresh message, as it always did.
    """
    MIN_INTERVAL = 1.5
    MIN_GROWTH = 40
    CARET = " ▍"

    def __init__(self, message, clock=time.monotonic):
        self.message = message
        self.text = ""
        self.edits = 0
        self.dead = message is None
        self._last_edit = 0.0
        self._last_len = 0
        self._clock = clock

    async def on_event(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "delta":
            self.text += str(event.get("text") or "")
            await self._maybe_edit()
        elif kind == "attempt":
            # A new candidate: whatever the last one streamed did not finish.
            self.text = ""
            self._last_len = 0

    def _plain(self, limit: int = 3900) -> str:
        return re.sub(r"<[^>]+>", "", self.text)[:limit]

    async def _maybe_edit(self) -> None:
        if self.dead:
            return
        now = self._clock()
        if (now - self._last_edit < self.MIN_INTERVAL
                or len(self.text) - self._last_len < self.MIN_GROWTH):
            return
        plain = self._plain()
        if not plain.strip():
            return
        try:
            await self.message.edit_text(plain + self.CARET, parse_mode=None)
            self.edits += 1
            self._last_edit = now
            self._last_len = len(self.text)
        except Exception as exc:
            self.dead = True
            logger.debug("telegram stream edit stopped: %s", exc)

    async def finish(self, final_html: str) -> bool:
        """Replace the provisional message with the final answer. True when
        the edit landed; False means the caller must send it the usual way."""
        if self.dead or not final_html or len(final_html) > 4000:
            return False
        try:
            await self.message.edit_text(final_html, parse_mode="HTML")
            return True
        except Exception:
            try:
                await self.message.edit_text(
                    re.sub(r"<[^>]+>", "", final_html), parse_mode=None)
                return True
            except Exception as exc:
                logger.debug("telegram stream final edit failed: %s", exc)
                return False


def _chat_ret(text: str, cfg, return_meta: bool, tool_events=None):
    """Shape _llm_chat's return: plain string (default, every existing caller),
    or (string, meta) when the caller wants model transparency (the web
    gateway shows which model answered). Module-level — several test suites
    invoke _llm_chat with a SimpleNamespace stand-in for self, so this must
    not live on the class.

    ALSO the one place a stated risk:reward gets checked against the levels
    it sits beside. Every return in _llm_chat funnels through here and there
    are eight-plus callers across two surfaces, so this is the only spot
    where the correction is reached on all of them — a guard applied at call
    sites is a guard that is missing from the next one somebody adds.

    v12 approved three trades under the 1.2 floor while printing ratios that
    clear it (1.17 shown as 1.25, 1.14 as 1.41, 1.18 as 1.40), and a whole
    training generation aimed at that did not fix it. Division is not a
    language problem: the levels are in the text, so the number is computed
    here rather than believed.

    AND the one place a claim that a TOOL RAN gets checked. Same seam for the
    same reason: both surfaces return through it. On 2026-08-31 v12 answered
    "Doji BTC" by writing its own `[analyze_asset] result:` block and a
    `[PENDING] scanning...` that nothing would ever resolve — it had copied
    the format `skill_memory` uses to record REAL tool output into the
    history. A fabricated result is worse than the empty reply that fix
    replaced: an empty reply is a failure the bot can see and report, and
    this one is indistinguishable from a real execution.

    ORDER MATTERS, and precisely: the fabrication check runs FIRST so that no
    `rr_corrected` audit event is written for a ratio inside a block that is
    about to be discarded. The user-visible text is the same either way — the
    truncation removes whatever the correction did — so the difference lives
    entirely in the record, which is the reason to care. A log saying the bot
    corrected a risk:reward, for a number nobody was ever shown, is a false
    account of what happened on exactly the surface built to be audited.
    (The first draft of this docstring claimed the reordering changed the
    reply. A mutation that swapped the two blocks passed all 22 tests and said
    otherwise.)
    """
    try:
        from bot.nlp.fabricated_tool_calls import strip_fabricated_tool_results
        cleaned, n = strip_fabricated_tool_results(text)
        if n:
            text = cleaned
            audit(system_log,
                  "Dropped a fabricated tool-result claim from a model reply",
                  action="fabricated_tool_result", result="REFUSED",
                  data={"count": n})
    except Exception as exc:  # never let a display fix break a reply
        logger.debug("fabricated tool-result check skipped: %s", exc)

    try:
        from bot.nlp.rr_honesty import correct_stated_rr
        fixed, n = correct_stated_rr(text)
        if n:
            text = fixed
            audit(system_log,
                  f"Corrected {n} stated risk:reward value(s) the levels contradict",
                  action="rr_corrected", result="CORRECTED", data={"count": n})
    except Exception as exc:  # never let a display fix break a reply
        logger.debug("risk:reward correction skipped: %s", exc)

    if not return_meta:
        return text
    meta = ({"provider": cfg.provider.value, "model": cfg.model}
            if cfg is not None else {})
    # What ran on the way to this answer, for the surface to show and the
    # store to keep. Name and outcome only — the arguments are already in
    # the audit log and the result is already in the conversation memory.
    if meta and tool_events:
        meta["tools"] = [{"name": str(e.get("name", "")),
                          "ok": bool(e.get("ok")),
                          "ms": int(e.get("ms", 0) or 0)}
                         for e in tool_events]
    return text, meta
