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

from bot.utils.i18n import chat_language_name, t
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
    "open or pending orders, PnL, risk state, costs, the macro calendar, what "
    "is moving, or why a trade was rejected, CALL the tool and answer from "
    "what it returns. A "
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


#: THE RESPONSE CONTRACT FOR ONE TURN, and the rule is the one
#: `_CHAT_TOOLS_RULE` states about itself two blocks up: exactly one of these
#: is in the prompt on any turn, so the model is never told two answer shapes
#: at once.
#:
#: It was told five. `_CHAT_SYSTEM_PROMPT` carried "Keep answers short and
#: actionable", "Quick questions = 2-4 lines", "Scans = ~10-15 lines", "Keep
#: Quick Mode under 50 words, Full Scan under 300 words" and a six-section
#: SCAN FORMAT — every one of them, on every turn, including "thanks". It also
#: named "Quick Mode" and "Full Scan" as though the model knew which it was in.
#:
#: IT COULD HAVE KNOWN. `intent_router._detect_reply_mode` classifies every
#: free-text message into one of these six modes with five regex sets, and
#: `IntentResult.reply_mode` has carried the answer since it was written --
#: read by NOTHING outside that module, on any surface. The value was computed
#: at `telegram_handler.py` line ~2616, sat in a local, and was dropped three
#: hundred lines before the `_llm_chat` call that names its vocabulary.
#:
#: The numbers below are the prompt's OWN numbers. Nothing here is a new
#: opinion about length; the change is that exactly one of them now applies.
_REPLY_CONTRACTS: dict[str, str] = {
    "quick": (
        "THIS TURN: a short, direct question. Lead with the answer in the "
        "first sentence, then at most one line of why. Under 50 words, 2-4 "
        "lines, no headers, no sections. Do not add a closing prompt."
    ),
    "full_scan": (
        "THIS TURN: a full analysis was asked for. Use these six headings, in "
        "order, and keep the whole reply under 300 words:\n"
        "  1. Verdict — bullish / bearish / choppy, and what to do\n"
        "  2. Structure — trend, key levels\n"
        "  3. Momentum — RSI, volume, order flow if relevant\n"
        "  4. Long scenario / Short scenario\n"
        "  5. Setup quality, 1-10\n"
        "  6. What to watch next\n"
        "Any number you cannot source from a block in this prompt is one you "
        "leave out, not one you estimate."
    ),
    "execution": (
        "THIS TURN: they are asking to act. Give entry, stop, target, the "
        "size basis, and the ONE condition that would invalidate it — each on "
        "its own line, under 120 words. Every price must come from a block in "
        "this prompt; if one is missing, say which and stop rather than "
        "completing the plan with an estimate. End with what to watch."
    ),
    "bot": (
        "THIS TURN: about AUTOMATION — bot settings, DCA or grid logic, or "
        "whether a setup suits an automated entry. Answer in RULES and "
        "PARAMETERS (what triggers, what sizes, what stops it), not as a "
        "discretionary market call. Under 150 words. Name any parameter you "
        "could not read rather than supplying a typical value for it."
    ),
    "beginner": (
        "THIS TURN: they sound new. One idea per sentence, and define each "
        "term inline the first time it appears ('swept the lows — took out "
        "the stops sitting under support'). No stacked jargon. Under 150 "
        "words. End with the single next thing they should look at."
    ),
    "standard": (
        "THIS TURN: answer the question that was asked, in 3-8 lines. Add a "
        "closing 'what to watch' line ONLY if the answer was about a market "
        "or an open position — on anything else it is filler, and a line "
        "that appears whether or not it means anything is one the reader "
        "learns to skip."
    ),
}

#: The mode used when none was supplied. It is a REAL mode, not a stand-in for
#: a missing measurement: the contract shapes how an answer reads and asserts
#: nothing to the user, so defaulting costs no honesty. That is why this may
#: default where a price or a P&L may not.
DEFAULT_REPLY_MODE = "standard"

#: THE TWO MODES WHOSE CONTRACT IS A REQUEST FOR NUMBERS. `full_scan` asks for
#: six sections of structure and momentum; `execution` asks for an entry, a
#: stop and a target. Both are answerable only where a reading can be sourced.
DATA_BACKED_MODES = frozenset({"full_scan", "execution"})

#: ...AND PUBLIC CHAT HAS NO READING TO SOURCE. `_llm_chat(public=True)` serves
#: an anonymous visitor from a STATIC prompt: no portfolio, no positions, and
#: no live ticker block. `_public_chat_turn` already refuses the scan-shaped
#: asks its router predicate catches — but that predicate is about LIVE MARKET
#: DATA and the mode is about ANSWER SHAPE, and the two disagree on real
#: sentences: `needs_live_market_data` says False for "full analysis of ETH"
#: and for "trade plan for btc", both of which detect as a data-backed mode.
#:
#: So handing the base contract to the public path would put a six-heading
#: scan skeleton, or an entry/stop/target, into the prompt on the ONE surface
#: that cannot source a single number — which is the thing the public gate
#: exists to prevent, reintroduced one layer under it. The override says the
#: same thing the gate says, in the model's voice, for the phrasings the
#: predicate does not reach.
_PUBLIC_REPLY_CONTRACTS: dict[str, str] = {
    "full_scan": (
        "THIS TURN: a full analysis was asked for, and you have NO live market "
        "feed on this page. Say that in your first line — plainly, once, not "
        "as an apology. Then give what you actually have: which signals a scan "
        "reads and why, in under 150 words. Print no headings, no scores and "
        "no levels; an empty scan skeleton reads as an analysis that found "
        "nothing. Close by telling them a signed-in account gets the live one."
    ),
    "execution": (
        "THIS TURN: they are asking for a trade plan, and you can neither read "
        "a price nor place an order from this page. Say so in your first line. "
        "Do NOT print an entry, a stop, a target or a size, including as an "
        "example — a number in that shape is read as a plan whatever it is "
        "labelled. Under 150 words: explain how the plan WOULD be built, then "
        "point them to signing in and connecting their own exchange keys."
    ),
}


#: `standard` IS THE FALLTHROUGH, and that is the whole of this reading.
#: `_detect_reply_mode` tries five regex sets in order and returns `standard`
#: when none of them fires — no pattern produces the word, which
#: `tests/test_the_shape_of_a_turn_nobody_could_read.py` pins by driving every
#: branch. So the same value carries two different facts:
#:
#:   in English   — the five had a fair chance and none matched, which is a
#:                  real reading: this is a general question;
#:   in any other — the patterns are English and could not have matched
#:   language      whatever was typed, so nothing was measured at all.
#:
#: Asserting "answer in 3-8 lines, add a closing what-to-watch line only if…"
#: off the second one is a shape claimed from no evidence, on thirteen of the
#: fourteen languages this product ships in. It is the quiet half of the defect
#: this module already fixed loudly: absent rendered as a measurement.
#:
#: A POSITIVE MATCH IS STILL TRUSTED in every language. "scan BTC" typed by a
#: Spanish reader really did match `_SCAN_PATTERNS`, and a match is evidence
#: wherever it happens; only the fallthrough is empty.
_UNREAD_CONTRACT = (
    "THIS TURN: the shape of this question was NOT classified. The detector "
    "that picks an answer shape reads English patterns, this turn is in "
    "another language, and it fell through rather than measuring anything — "
    "so no length or structure is being prescribed here, because none was "
    "read. Match the answer to what was actually asked: a one-line question "
    "gets a one-line answer, a request for a full analysis gets sections, "
    "somebody who sounds new gets terms defined inline. Everything else "
    "holds — any number you cannot source from a block in this prompt is one "
    "you leave out, not one you estimate, and a closing 'what to watch' line "
    "is earned only when the answer was about a market or an open position."
)


def reply_contract(mode: str = "", public: bool = False,
                   reply_lang: str = "") -> str:
    """The one response contract for this turn.

    Unknown or empty falls back to `standard` rather than to nothing: a turn
    with no contract is a turn back under the five-at-once prompt this
    replaced.

    ``public`` is the anonymous-website surface, and it SELECTS a different
    contract rather than suppressing one — see `_PUBLIC_REPLY_CONTRACTS`. A
    visitor who asks for a scan still gets an answer; what they do not get is
    a document shaped like a reading nobody took.

    ``reply_lang`` is the language the model has been told to answer in, and
    it decides whether the FALLTHROUGH means anything — see
    `_UNREAD_CONTRACT`. The test is `chat_language_name`, which is already the
    reading `_llm_chat` uses to decide whether to issue a LANGUAGE directive
    at all: it answers "" for English, for empty, and for a code it does not
    know — exactly the cases where the reply comes back in English and the
    English detector therefore had a fair chance. Reusing it rather than
    writing a second language test is the rule `winrate-bar.js` states about
    `MIN_RATED`: a second copy of a threshold is a second answer.
    """
    key = str(mode or "").strip().lower()
    if key not in _REPLY_CONTRACTS:
        key = DEFAULT_REPLY_MODE
    if key == DEFAULT_REPLY_MODE and chat_language_name(reply_lang):
        return "\n\nHOW LONG AND WHAT SHAPE\n" + _UNREAD_CONTRACT + "\n"
    body = _PUBLIC_REPLY_CONTRACTS[key] if (
        public and key in _PUBLIC_REPLY_CONTRACTS) else _REPLY_CONTRACTS[key]
    return "\n\nHOW LONG AND WHAT SHAPE\n" + body + "\n"



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
