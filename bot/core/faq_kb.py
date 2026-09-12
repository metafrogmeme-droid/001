"""Built-in FAQ knowledge base for the public chat.

The website's landing-page chat shows five starter questions. These get an
instant, deterministic, §4-safe answer straight from here — no LLM, no cost, and
it works even before any provider is connected (the default for anonymous web
visitors). This is what stops the public chat from ever replying with a raw
internal error (e.g. an "add a key to .env" hint), which is both a broken first
impression and a config leak.

Answers are compliance-aware by construction: simulation-first framing, risk
warnings, no dollar amounts, no performance promises. Free-form questions are
NOT answered here — they fall through to the LLM; only close matches to the
known FAQ topics return an answer, so precision stays high.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

from bot.utils.i18n import t, ui_lang

# The five landing-page starter questions, each a canned answer + the trigger
# phrases that map a user message onto it. Triggers are distinctive multi-word
# phrases so free-form questions ("should I use leverage on SOL?") do NOT match
# and instead reach the model.
_FAQ: list[dict] = [
    {
        "id": "what_is",
        "triggers": ["what is runeclaw", "whats runeclaw", "what's runeclaw",
                     "what is rune claw", "about runeclaw", "tell me about runeclaw",
                     "what does runeclaw do", "who are you", "what are you"],
        "answer": (
            "RUNECLAW is a simulation-first, explainable AI trading agent for "
            "crypto. It scans the market through a multi-check analysis pipeline, "
            "forms a trade thesis you can actually read (the “decision "
            "picture”), and passes every idea through a hard risk gate before "
            "anything can execute. It runs on the web, as a Telegram bot, and via "
            "an MCP server — the same agent everywhere.\n\n"
            "You stay in control: the AI proposes, deterministic controls "
            "authorize, and you confirm. Start in paper mode to watch it think, "
            "then connect an exchange when you're ready — everything it does "
            "is explainable and auditable."
        ),
    },
    {
        "id": "risk",
        "triggers": ["manage risk", "manage the risk", "risk management",
                     "how does it manage risk", "how do you manage risk",
                     "control risk", "how is risk managed"],
        "answer": (
            "Risk is enforced by deterministic controls — not the AI's "
            "judgment. Every idea passes a risk gate before it can execute:\n\n"
            "• Position-size caps, plus per-trade and daily loss limits\n"
            "• A circuit breaker that halts trading on a drawdown or losing "
            "streak\n"
            "• Correlation / exposure caps so you're not stacking the same "
            "bet\n"
            "• A stop-loss placed with the order\n\n"
            "Funds only ever move inside limits a human has set and can revoke: "
            "the AI proposes, the controls authorize, your wallet enforces. It "
            "defaults to paper trading so you can see the risk logic work before "
            "any real capital is involved."
        ),
    },
    {
        "id": "sweep",
        "triggers": ["liquidity sweep", "what is a liquidity sweep",
                     "liquidity grab", "stop hunt", "stop-loss hunt",
                     "liquidity sweeps"],
        "answer": (
            "A liquidity sweep is when price briefly pushes through an obvious "
            "level — just past a swing high or low where stop-loss orders "
            "cluster — triggering those stops, then reverses. Larger players "
            "do this to fill big orders against the liquidity those stops "
            "provide.\n\n"
            "RUNECLAW reads sweeps as context: one that fails and reverses can "
            "mark a high-probability turning point, while getting caught on the "
            "wrong side of one is a classic stop-out. It's one of roughly twenty "
            "checks the engine weighs — never a signal on its own."
        ),
    },
    {
        "id": "leverage",
        "triggers": ["how does leverage work", "what is leverage",
                     "explain leverage", "how leverage works",
                     "how does leverage", "leverage work"],
        "answer": (
            "Leverage lets your margin control a position several times its size "
            "— e.g. 5x means your margin controls five times its own value in "
            "exposure. It multiplies both gains AND losses, and it sets your "
            "liquidation price: the more leverage, the smaller the move against "
            "you that wipes the position.\n\n"
            "RUNECLAW defaults to a conservative 5x and lets you set your own, and "
            "its risk gate sizes every position off your real margin so one trade "
            "can't over-extend the account. Leverage is a risk multiplier, not "
            "free money — most blow-ups come from too much of it."
        ),
    },
    {
        "id": "exchanges",
        "triggers": ["which exchanges", "which exchange", "what exchanges",
                     "exchanges are supported", "supported exchanges",
                     "which venues", "what venues", "exchanges supported",
                     "exchange supported"],
        "answer": (
            "Bitget USDT-M futures is the primary trading venue. Hyperliquid "
            "(on-chain USDC perps) is supported too, and you can link read-only "
            "balances across major exchanges and on-chain wallets for one unified "
            "net-worth and exposure view.\n\n"
            "A venue router compares fees and liquidity per pair and recommends "
            "the cheapest place to trade — recommendations only; you decide. "
            "You connect an exchange with your own API keys, which are encrypted "
            "per user; RUNECLAW never takes custody of your funds."
        ),
    },
]


def _norm(text: str) -> str:
    r"""Casefold, strip punctuation, collapse whitespace — for phrase matching.

    THE CHARACTER CLASS USED TO BE `[^a-z0-9\'\s]`, and everything it did not
    name it replaced with a space. That is every script this product ships in
    except Latin. A Japanese message came out of here holding only whatever
    English happened to be inside it:

        "who are you 今どのコインに強気ですか"  ->  "who are you"

    — and `faq_answer` then measured THAT against the trigger and concluded the
    message WAS the question. Six of the fourteen supported languages (ja, zh,
    ru, ar, hi, ko) were erased outright, so a genuine question in any of them
    that contained a trigger's English words returned the about-page brochure:
    the exact miss `_slack_for` documents itself as fixing, working in English
    and wide open everywhere else.

    Accented Latin failed in the other direction from the same class — Spanish
    `qué` became `qu ` and `cómo` became `c mo`, splitting one word into two and
    INFLATING the count, so a real starter question in Spanish was pushed out of
    range of its own answer.

    What is kept now is letters, digits, and the COMBINING MARKS that complete
    them, in every script. `\w` alone was tried and is not enough: Python's
    `\w` is `str.isalnum()`, and a Devanagari matra or an Arabic diacritic is
    category Mn — not alphanumeric on its own — so Hindi `क्या` ("what")
    came apart into two tokens and INFLATED the Hindi count. That errs toward
    deferring, which is the safe direction here and is still the wrong reading;
    a measure that happens to fail safely is not a measure.

    For ASCII input this is identical to the old expression, which is the
    property `tests/test_faq_matches_in_every_script.py` pins: nothing about
    English changes here.
    """
    out = []
    for ch in (text or "").casefold().replace("\u2019", "'"):
        if ch.isalnum() or ch.isspace() or ch == "'" \
                or unicodedata.category(ch)[0] == "M":
            out.append(ch)
        else:
            out.append(" ")
    return re.sub(r"\s+", " ", "".join(out)).strip()


#: Codepoint ranges written WITHOUT spaces between words, where a whitespace
#: token is a whole clause rather than a word. Chinese and Japanese are the two
#: in `SUPPORTED_LANGS`; Thai is here because the same property holds and a
#: visitor may type it whatever the UI offers. Korean is deliberately NOT here:
#: hangul is written with spaces between eojeol, so it counts normally.
_UNSPACED_RANGES = (
    (0x3040, 0x30FF),   # hiragana + katakana
    (0x3400, 0x4DBF),   # CJK unified ideographs, extension A
    (0x4E00, 0x9FFF),   # CJK unified ideographs
    (0xF900, 0xFAFF),   # CJK compatibility ideographs
    (0xFF66, 0xFF9D),   # halfwidth katakana
    (0x0E00, 0x0E7F),   # Thai
)


def _is_unspaced(ch: str) -> bool:
    o = ord(ch)
    return any(lo <= o <= hi for lo, hi in _UNSPACED_RANGES)


def _length_in_words(text: str) -> int:
    """How many WORDS a normalised message carries, in any script.

    `len(text.split())` is a word count only where words are separated by
    spaces. In Chinese and Japanese they are not, so a whole sentence scored 1
    — and after `_norm` had erased it, 0. The measure and the thing being
    measured have to agree about what a word is, or the comparison against
    `_slack_for` is arithmetic on two different units.

    A token counts as its number of unspaced characters, plus one more if it
    also carries anything else, so a mixed token ("runeclaw是什麼") is not
    rounded down to either half.
    """
    total = 0
    for token in text.split():
        dense = sum(1 for ch in token if _is_unspaced(ch))
        total += dense + (1 if len(token) > dense else 0)
    return total


def _slack_for(trigger_words: int) -> int:
    """How many extra words a message may carry and still BE this question.

    Scaled to the trigger, not flat. The trigger list runs from 2 words
    ("leverage work", "stop hunt") to 5 ("how does it manage risk"), and a
    2-word trigger sitting inside a 5-word message is far weaker evidence
    than a 5-word one inside an 8-word message. A flat allowance was tried
    first and let "who are you bullish on" through on the 3-word trigger
    "who are you" — the exact class of miss this is fixing.
    """
    return max(1, trigger_words // 2)


def faq_answer(question: str) -> Optional[str]:
    """The canned §4-safe answer for a landing-page starter question, or None.

    SUBSTRING-ANYWHERE WAS THE BUG. This read `if _norm(trig) in q`, and the
    trigger list holds bare phrases like "who are you" and "leverage work", so
    a signed-in user asking a real question about their own account got a
    marketing blurb instead of an answer. Measured against the live triggers,
    five of six realistic questions matched:

        "what are you seeing on BTC right now"      -> the about-page
        "who are you bullish on"                    -> the about-page
        "what are you doing with my money"          -> the about-page
        "is my leverage working against me right now" -> a leverage explainer

    The docstring above this function claimed the opposite ("only CLOSE
    matches answer here; free-form questions fall through"), and the module
    docstring says triggers are "distinctive multi-word phrases so free-form
    questions do NOT match". The intent was right and the operator `in` did
    not implement it.

    A match now requires the message to BE the question rather than to
    contain it: the trigger's words, in order, with at most
    `_FAQ_SLACK_WORDS` of other words in the whole message. That keeps every
    landing-page starter ("what is runeclaw", "how does it work?") and drops
    the account questions, which are longer and about something else.
    """
    q = _norm(question)
    if not q:
        return None
    # `_length_in_words`, not `len(q.split())`. Both sides of the comparison
    # below have to be counted the same way, and in Chinese and Japanese a
    # whitespace token is a clause rather than a word.
    q_words = _length_in_words(q)
    for item in _FAQ:
        for trig in item["triggers"]:
            # NOT `t`. This module imports the i18n `t` at module level now
            # (for `public_fallback`), and Python decides a name is local for
            # the WHOLE function the moment it sees any binding of it — so a
            # loop variable called `t` here would make every future `t(key,
            # lang)` call in this function raise UnboundLocalError, which is
            # exactly how `_cmd_portfolio` died (tests/test_no_i18n_shadowing).
            # That guard flags a function only when it both binds and CALLS
            # the name, so this one would have passed while staying one line
            # away from the same failure.
            norm_trig = _norm(trig)
            if not norm_trig or norm_trig not in q:
                continue
            _tw = _length_in_words(norm_trig)
            if q_words - _tw <= _slack_for(_tw):
                return item["answer"]
    return None


def public_fallback(lang: str = "") -> str:
    """Friendly, no-leak reply when the live model isn't reachable and the
    message didn't match a FAQ topic. Never mentions provider config, keys, or
    env — it guides the visitor to the starter topics and to signing in.

    IN THE VISITOR'S LANGUAGE, and it was the only half of its own branch that
    was not. `_llm_chat` chooses between this and `chat_no_model_admin` on one
    `if is_admin and not public`, and the admin's half went through `_say(_ui,
    ...)` while the visitor's was this English literal — the operator was told,
    in their language, that the model is not connected; the visitor was handed
    a paragraph in English that did not mention it.

    Both halves of that are fixed here, because they are the same omission.
    The text now SAYS the live AI is unreachable rather than opening "I'm
    RUNECLAW, the AI trading agent. I can walk you through the essentials",
    which presented a degraded state as the normal offering. Saying so leaks
    nothing: `chat_unavailable` already tells a visitor the AI is down, and the
    rule this function exists for is about provider config, keys and env — not
    about admitting that something is wrong.

    The five starter questions stay in English inside every translation. That
    is deliberate and stated in the dictionary: the built-in answers exist only
    in English and match only English triggers, so translating the suggestions
    would offer five questions that this branch — reached precisely because no
    model is available to answer anything else — cannot answer either.
    """
    return t("chat_public_fallback", ui_lang(lang))
