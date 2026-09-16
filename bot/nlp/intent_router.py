"""
RUNECLAW Natural-Language Intent Router.

Maps free-text user messages to registered skills via:
  1. Greeting / social detection (fast-exit to chat)
  2. Rule-based keyword matching (no LLM key needed)
  3. Optional LLM intent classification (when configured)

The router ONLY resolves to existing skills — it never invents actions
and never touches the risk gate. Every resolved intent is audited.

Safety invariant: the router is a dispatcher, not an executor.
It returns a (skill_name, kwargs) tuple; the caller decides whether
to execute and under what auth/risk constraints.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from bot.utils.logger import audit, system_log

logger = logging.getLogger(__name__)


# ── Intent result ─────────────────────────────────────────────────────

@dataclass
class IntentResult:
    """Result of intent classification."""
    skill: str = ""                 # Skill name to dispatch to (empty = no match)
    kwargs: dict = field(default_factory=dict)
    confidence: float = 0.0         # 0.0–1.0 (rules always return 1.0)
    source: str = "rules"           # "rules" or "llm"
    raw_text: str = ""              # Original user message
    explanation: str = ""           # Why this intent was chosen
    is_social: bool = False         # True if message is greeting/thanks/social
    reply_mode: str = "standard"    # quick, full_scan, execution, bot, beginner, standard

    @property
    def matched(self) -> bool:
        return bool(self.skill)


# ── Social / greeting detection ──────────────────────────────────────

_GREETING_PATTERNS = re.compile(
    r"^(hey|hi|hello|yo|sup|what'?s up|howdy|gm|good (morning|afternoon|evening|night)|"
    r"hola|greetings|aloha|heya|hiya|wassup|wazzup)\b",
    re.IGNORECASE,
)

_THANKS_PATTERNS = re.compile(
    r"\b(thanks?( you)?|thx|ty|cheers|appreciate|gracias|much appreciated|"
    r"thank u|thankyou|tysm|tyvm)\b",
    re.IGNORECASE,
)

_FAREWELL_PATTERNS = re.compile(
    r"^(bye|goodbye|see ya|later|gn|good night|cya|peace|ttyl|"
    r"take care|until next time|catch you later)\b",
    re.IGNORECASE,
)

_AFFIRMATIVE_PATTERNS = re.compile(
    r"^(ok|okay|sure|yep|yup|yes|yeah|alright|got it|cool|nice|"
    r"sounds good|perfect|right|i see|understood|makes sense|"
    r"no worries|np|nw|all good)\s*[.!]?\s*$",
    re.IGNORECASE,
)

_SOCIAL_CHAT = re.compile(
    r"^(how are you|how'?s it going|what'?s new|how'?s your day|"
    r"you there|are you alive|are you real|who are you|what are you|"
    r"tell me about yourself|what can you do|lol|haha|lmao|rofl|"
    r"bruh|bro|dude|mate|fam)\b",
    re.IGNORECASE,
)

# ── Reply-mode patterns (compiled once at module level) ──────────────
_QUICK_PATTERNS = re.compile(
    r"^(long or short|entry|risk|valid|safe|direction|bias|"
    r"should i (enter|trade|buy|sell)|is (it|this) (safe|valid|good)|"
    r"thumbs up or down|go or no.?go|yes or no)\??$",
    re.IGNORECASE
)
_BOT_PATTERNS = re.compile(
    r"\b(bot (playbook|settings?|config|rules?)|bitget bot|automation|"
    r"dca (logic|settings?|bot)|grid bot|auto.?trade|bot.?ready)\b",
    re.IGNORECASE
)
_EXEC_PATTERNS = re.compile(
    r"\b(give (me )?(a )?signal|entry zones?|trade (plan|setup)|"
    r"setup|where (to|do i) (enter|buy|sell|long|short)|"
    r"execution plan|exact entry|sl and tp|stop.?loss.+take.?profit)\b",
    re.IGNORECASE
)
_SCAN_PATTERNS = re.compile(
    r"\b(scan|swing by swing|market read|full (analysis|read|scan)|"
    r"what does .{0,15}(claw|runeclaw) see|deep (analysis|dive|read)|"
    r"technical analysis|complete (scan|analysis|breakdown))\b",
    re.IGNORECASE
)
_BEGINNER_PATTERNS = re.compile(
    r"\b(what (is|does|are) .{0,10}(mean|work)|explain|help me understand|"
    r"i.?m (new|beginner|learning|confused|not sure)|"
    r"how (does|do) .{0,15}(work|mean)|can you explain|"
    r"what.?s (a |an )?(choch|bos|fvg|sweep|reclaim|liquidity|structure))\b",
    re.IGNORECASE
)


def _is_social_message(text: str) -> bool:
    """Detect greetings, thanks, farewells, and casual social chat."""
    stripped = text.strip().rstrip("!?.")
    if len(stripped) < 2:
        return True  # Single char or emoji
    # A whole-message ACTION is never social. `_THANKS_PATTERNS` is an
    # unanchored search, so "halt the bot, thanks" and "stop trading, ty"
    # were social before any rule ran and reached the chat model as small
    # talk — the natural shape of a polite command, defeated by its own
    # politeness. The rules are whole-message anchored, so consulting them
    # first widens nothing else.
    if any(rx.search(stripped) for rx in _ANCHORED_ACTION_RULES):
        return False
    # A GREETING LEAD IS INFORMALITY, AND WHAT FOLLOWS IT DECIDES. `_GREETING_
    # PATTERNS` is `^`-anchored — it is a LEAD, not a message — and it
    # returned True for everything it led. Driven, **19 of 19** ordinary
    # greeting-led reads were answered "hey!": `hey what is my balance`,
    # `hi what are my positions`, `yo what is my pnl`, `hey what are my open
    # orders`, `hey what is my net worth`, `hi am i overexposed`, and
    # `hey analyze btc` — a chart request with the symbol named.
    #
    # That is `HALT_SOCIAL_LEAD`'s lesson, which this file records one gate
    # up as "a social lead on a whole-message action is INFORMALITY, and
    # informality goes to the door". The fix reached the ACTION rules only,
    # through `_ANCHORED_ACTION_RULES` above; every READ rule was still
    # behind this line.
    #
    # The lead is STRIPPED and the remainder is asked the same question,
    # ONCE. Recursing rather than consulting the rule table is the narrow
    # choice on purpose: most of `_INTENT_RULES` is unanchored, so asking it
    # here would let a rule matching INSIDE a pleasantry acquit real small
    # talk — the shape this file records for the orders rule and the halt
    # rule both. What the remainder is, the message is: "hey there" leaves
    # "there", "hey how are you" leaves a `_SOCIAL_CHAT` match, and "hey"
    # alone leaves nothing — all three stay social, and each is in the table.
    if (m := _GREETING_PATTERNS.search(stripped)) is not None:
        rest = stripped[m.end():].lstrip(" ,.!?-—:;")
        return _is_social_message(rest) if rest else True
    if _THANKS_PATTERNS.search(stripped):
        return True
    if _FAREWELL_PATTERNS.search(stripped):
        return True
    if _AFFIRMATIVE_PATTERNS.search(stripped):
        return True
    if _SOCIAL_CHAT.search(stripped):
        return True
    # Very short messages are usually social (under 4 words, no crypto terms)
    words = stripped.split()
    if len(words) <= 3 and not _extract_symbol(stripped):
        # Check if any word looks like a crypto/trading term
        # THE LIST IS THE GATE. A message of three words or fewer with no
        # symbol and no word from this set is answered as small talk, and the
        # set held none of the vocabulary a trader actually types short:
        # "win rate", "profit factor", "sharpe ratio", "biggest loser",
        # "fees this month", "cpi tomorrow?", "15m setups", "api keys",
        # "connect bitget", "what is rsi" — every one of them greeted.
        trading_words = {
            "scan", "analyze", "portfolio", "risk", "backtest", "macro",
            "halt", "journal", "cost", "costs", "dashboard", "trade", "trades",
            "signal", "signals", "positions", "position", "balance", "equity",
            "pnl", "p&l", "swing", "swings", "scalp", "scalps", "intraday",
            "playbook", "performance", "entry", "entries", "setup", "setups",
            "liquidity",
            # what the record is asked about
            "win", "rate", "winrate", "profit", "factor", "sharpe", "expectancy",
            "drawdown", "exposure", "loser", "winner", "fees", "fee", "returns",
            "stats", "results", "record", "history",
            # indicators, so "what is rsi" reaches the model that can explain
            # it rather than the greeter
            "rsi", "macd", "atr", "vwap", "ema", "sma", "stochastic", "fib",
            "fibonacci", "bollinger", "ichimoku", "funding", "oi",
            # the account and its plumbing
            "orders", "order", "limit", "limits", "leverage", "margin", "stop",
            "stops", "target", "targets", "keys", "api", "connect", "venue",
            # the orders rule learned `triggers` when the capability card's
            # stop/take-profit half was given a vocabulary, and a term the
            # rules know and this gate does not is a trading question
            # answered with "hey!" — the `_ANALYSIS_WORDS` lesson below.
            "trigger", "triggers",
            "bitget", "bybit", "binance", "hyperliquid", "settings",
            # macro
            "cpi", "fomc", "nfp", "pce", "ppi", "fed", "events", "calendar",
            "payrolls", "unemployment", "jobs", "claims", "print", "blackout",
            "brief", "report",
            # the scanner's vocabulary, short
            "scanner", "gainers", "losers", "movers", "ideas", "plays", "hourly",
            "daily", "weekly", "monthly", "top",
            # The CHART spellings of the same timeframes. The English ones
            # above are words this set already knew; `1d` and `1w` are not,
            # and neither is claimed by a rule (the scanner runs 5m, 15m and
            # 4h — a daily or weekly sweep is none of them), so the gate's
            # final rule sweep acquitted nothing and both were GREETED, while
            # "daily" and "weekly" one line up reached the model. One
            # timeframe, two spellings, two destinations. The ones a rule DOES
            # claim ("4h", "15m", "5m") need no entry here for the same reason
            # "net" and "worth" are absent: the gate consults the rules before
            # it decides, so a word a rule claims is a word nothing reaches.
            # `1d` and `1h` are claimed by the sweep rules now, so they need
            # no entry (the gate consults the rules before it decides). These
            # are the spellings NO rule claims: the timeframes neither the
            # ladder card nor the full sweep runs.
            "1w", "1mo", "w1", "3d", "2d", "1week", "2h", "30m", "1m", "3m",
            "h4", "m15", "m5",
            # the research surface: registered skills and their arguments, so
            # "walk forward test" and "optimise the params" reach a model that
            # can say what runs where, not the greeter
            "walk", "forward", "walkforward", "walk-forward", "optimize",
            "optimise", "optimizer", "optimiser", "params", "parameters",
            "strategy", "validation",
            # the reads the website answers from its own intercepts, typed
            # short. Not "net"/"worth": every short net-worth phrasing is
            # claimed by its rule, which this gate consults below, so a word
            # here would be one nothing reaches. These are the ones a rule
            # deliberately does NOT claim — "what is rwa", "research report",
            # "radar" — which must reach the model rather than the greeter.
            "rwa", "rwas", "radar", "research", "dossier", "diligence",
            "tokenized", "tokenised",
            # The website-only reads' own nouns, for the EDUCATION shapes the
            # rules above decline ("what is defi", "how do airdrops work"):
            # three words, no rule, and without these the greeter answered.
            # Not "alert"/"alerts": every price-alert phrasing is claimed by
            # its rule first, and "turn off alerts" is pinned as small talk
            # by the halt suite's neighbour table.
            "defi", "aave", "nft", "nfts", "airdrop", "airdrops", "testnet",
            "testnets", "meme", "memes", "spot", "letter", "replay",
            "opensea", "dexscreener", "wallet",
        }
        # …and the chart vocabulary the analysis rules read, by construction.
        trading_words |= set(_ANALYSIS_WORDS)
        if not any(w.lower() in trading_words for w in words):
            # Only classify as social if it doesn't contain intent keywords
            for pattern, _, _, _ in _INTENT_RULES:
                if pattern.search(stripped):
                    return False
            return True
    return False


#: Rules that carry the symbol when one is named and route without one.
_SYMBOL_OPTIONAL = frozenset({"trade_postmortem"})


def symbol_mentioned(text: str) -> Optional[str]:
    """The asset a message names, or None — `_extract_symbol` for callers
    outside this module (the close-intent notice names the position it is
    about, when the user did)."""
    return _extract_symbol(text)


def casual_halt(text: str) -> bool:
    """Whether a routed halt-shaped message reached the door BECAUSE of a
    social lead — "bro stop the bot" rather than "stop the bot" — so the
    notice can say the sentence was read as casual and name the command,
    rather than call a sentence that named the bot "a stop-word with
    nothing named"."""
    t = text or ""
    return bool(HALT_SOCIAL_LEAD.match(t) or BARE_SOCIAL_LEAD.match(t))


def halt_verb(text: str) -> Optional[str]:
    """The bare verb a `halt_ambiguous` match carried — from the rule's own
    fixed vocabulary, never free input — or None."""
    m = HALT_BARE_VERB.match(text or "") or BARE_SOCIAL_LEAD.match(text or "")
    if not m:
        return None
    verb = re.sub(r"\s+", " ", m.group("verb").lower())
    # "shutdown", "shut down" and "shut it down" are one verb
    return "shut down" if verb.startswith("shut") else verb


def symbol_from_token(token: str) -> Optional[str]:
    """A symbol from ONE token the user typed where a symbol is expected —
    `/postmortem HYPE`, `/postmortem eth/usdt` — or None. A bare ticker is a
    ticker here by the command's own grammar, known to the list or not:
    `_extract_symbol` needs a command word before it will read an unknown
    all-caps token, so `/postmortem HYPE` read HYPE as a TRADE ID and looked
    it up as one."""
    t = str(token or "").strip().upper().lstrip("$")
    if not re.fullmatch(r"[A-Z]{2,10}(?:/[A-Z]{2,10})?", t):
        return None
    if "/" not in t:
        name = _NAME_TO_TICKER.get(t.lower())
        t = f"{name or t}/USDT"
    return _validate_symbol(t)


#: Every word the post-mortem rule below can match on — a trigger word,
#: however it is capitalised, is never the asset the question is about.
_PM_TRIGGER_WORDS = frozenset({
    "post", "mortem", "mortems", "postmortem", "postmortems", "debrief",
    "autopsy", "review", "walk", "me", "through", "break", "down", "go", "over",
    "why", "did", "you", "it", "the", "bot", "we", "enter", "open", "take",
    "buy", "short", "long", "what", "went", "wrong", "with", "on", "in", "of",
    "was", "thesis", "idea", "reasoning", "my", "that", "this", "our", "last",
    "latest", "recent", "previous", "closed", "trade", "trades", "position",
    "positions", "loss", "win", "entry", "and", "for", "to", "please",
})

#: The object slots a post-mortem question puts its asset in.
_PM_NOUN = re.compile(r"\b(\$?[A-Za-z]{2,10}(?:/[A-Za-z]{2,10})?)\s+"
                      r"(?:trade|position|loss|win|long|short|entry)\b")
_PM_AFTER = re.compile(r"\b(?:enter(?:ed)?|open(?:ed)?|t(?:ake|ook)|b(?:uy|ought)|short(?:ed)?|long(?:ed)?"
                       r"|on|of|with|in)\s+(?:the |my |a |that |this )?"
                       r"(\$?[A-Za-z]{2,10}(?:/[A-Za-z]{2,10})?)\s*[?!.]*$")


def postmortem_symbol(text: str) -> Optional[str]:
    """The asset a post-mortem question is ABOUT, or None — read from the
    slots such a question puts it in, never from anywhere in the message.

    `_extract_symbol` reads the whole text and its known-ticker list holds
    English words (near, etc, op, link, ton, dot, ray, sand, mana, gala,
    render), so "why did you enter near the top" attached NEAR/USDT and the
    skill answered a confident negative about a trade nobody named — and
    both dispatch sites then recorded NEAR as a topic in the user's recall.
    Its all-caps fallback fired on shouted prose ("WHAT WENT WRONG WITH THE
    TRADE" -> WHAT/USDT). A ticker in a sentence is a word written
    differently from its neighbours: `$X`, `X/USDT`, an all-caps token in a
    sentence that is not itself all-caps, or a known name in the noun slot
    ("my eth trade") or the object slot at the END of the question ("why did
    you enter eth?"). "enter near the top" fills neither: `near` is followed
    by more words, so it is prose.
    """
    if not text:
        return None
    explicit = re.search(r"\$?([A-Za-z]{2,10})/(?:USDT|USD|USDC|PERP)\b", text, re.IGNORECASE)
    if explicit:
        return _validate_symbol(f"{explicit.group(1).upper()}/USDT")
    dollar = re.search(r"\$([A-Za-z]{2,10})\b", text)
    if dollar:
        return _validate_symbol(f"{dollar.group(1).upper()}/USDT")
    words = re.findall(r"[A-Za-z]{2,}", text)
    shouted = bool(words) and all(w.isupper() for w in words)
    if not shouted:
        for w in re.findall(r"\b[A-Z]{2,10}\b", text):
            # The rule's own trigger words are never its object: "POST
            # MORTEM my last trade" is a shouted verb, not a $POST trade.
            if w.lower() in _PM_TRIGGER_WORDS:
                continue
            return _validate_symbol(f"{_NAME_TO_TICKER.get(w.lower(), w)}/USDT")
    for rx in (_PM_NOUN, _PM_AFTER):
        m = rx.search(text)
        if m:
            tok = m.group(1).lstrip("$").split("/")[0].lower()
            if tok in _NAME_TO_TICKER or tok in _KNOWN_SYMBOLS:
                return _validate_symbol(f"{_NAME_TO_TICKER.get(tok, tok.upper())}/USDT")
    return None


#: Known tickers that are also ordinary English words. `_extract_symbol`
#: reads them from anywhere in a sentence, which is how "why did you enter
#: near the top" put NEAR/USDT in a user's recall. Written AS a ticker ($LINK,
#: LINK/USDT, LINK inside a lowercase sentence) each is still read; the bare
#: lowercase word is prose.
AMBIGUOUS_TICKER_WORDS = frozenset({
    "near", "etc", "op", "link", "ton", "dot", "ray", "sand", "mana", "gala",
    "render", "apt", "fil", "ron", "vet", "algo", "uni", "atom", "arb", "bonk",
})


def mentioned_symbol(text: str) -> Optional[str]:
    """The asset a message MENTIONS, for the user's recall, or None.

    The recall line is a claim about the user ("last discussed asset: NEAR"),
    so it is read only from a word written as a ticker — `$X`, `X/USDT`, or a
    known ticker in caps inside a sentence that is not itself shouted — or
    from a coin name or a known ticker that is not also an English word
    (`AMBIGUOUS_TICKER_WORDS`). Nothing comes from the command-word fallback
    `_extract_symbol` keeps for dispatch, where an unknown token beside
    "analyze" is worth a guess because the caller can ask which asset; a
    recall cannot ask.
    """
    if not text:
        return None
    explicit = re.search(r"\$?([A-Za-z]{2,10})/(?:USDT|USD|USDC|PERP)\b", text, re.IGNORECASE)
    if explicit:
        return _validate_symbol(f"{explicit.group(1).upper()}/USDT")
    dollar = re.search(r"\$([A-Za-z]{2,10})\b", text)
    if dollar:
        return _validate_symbol(f"{dollar.group(1).upper()}/USDT")
    words = re.findall(r"[A-Za-z]{2,}", text)
    shouted = bool(words) and all(w.isupper() for w in words)
    if not shouted:
        for w in re.findall(r"\b[A-Z]{2,10}\b", text):
            lw = w.lower()
            if lw in _NAME_TO_TICKER or lw in _KNOWN_SYMBOLS:
                return _validate_symbol(f"{_NAME_TO_TICKER.get(lw, w)}/USDT")
    for w in words:
        lw = w.lower()
        if lw in _NAME_TO_TICKER:
            return _validate_symbol(f"{_NAME_TO_TICKER[lw]}/USDT")
        if lw in _KNOWN_SYMBOLS and lw not in AMBIGUOUS_TICKER_WORDS:
            return _validate_symbol(f"{lw.upper()}/USDT")
    return None


def detect_reply_mode(text: str) -> str:
    """The turn's shape, for a caller with no IntentResult to hand.

    Public chat never runs skill resolution — an anonymous visitor has no
    account to dispatch against — so it had no `IntentResult` and therefore no
    mode, on exactly the turns (`bot`, `beginner`) the modes were written for.
    """
    return _detect_reply_mode(text)


def _detect_reply_mode(text: str) -> str:
    """Detect the appropriate reply mode for the user's message.

    Returns: 'quick', 'full_scan', 'execution', 'bot', 'beginner', or 'standard'
    """
    lower = text.lower().strip()

    # Quick mode — very short directional questions
    if _QUICK_PATTERNS.search(lower):
        return "quick"

    # Bot mode
    if _BOT_PATTERNS.search(lower):
        return "bot"

    # Execution mode
    if _EXEC_PATTERNS.search(lower):
        return "execution"

    # Full scan mode
    if _SCAN_PATTERNS.search(lower):
        return "full_scan"

    # Beginner mode — unsure language
    if _BEGINNER_PATTERNS.search(lower):
        return "beginner"

    return "standard"


# ── Symbol extraction ─────────────────────────────────────────────────

# SEC-H3 FIX: strict symbol format validator — prevents unsanitised strings
# reaching CCXT or LLM layers.
_SYMBOL_RE = re.compile(r'^[A-Z0-9]{1,15}(/[A-Z0-9]{1,15})?$')


def _validate_symbol(symbol: str) -> Optional[str]:
    """Return *symbol* unchanged if it matches the strict format, else None."""
    return symbol if _SYMBOL_RE.match(symbol) else None


# Common crypto symbols (without /USDT suffix)
_KNOWN_SYMBOLS = {
    "btc", "bitcoin", "eth", "ethereum", "sol", "solana", "bnb", "xrp",
    "doge", "ada", "avax", "dot", "link", "matic", "uni", "atom",
    "near", "apt", "sui", "arb", "op", "sei", "jup", "jto", "wif",
    "bonk", "pepe", "shib", "render", "inj", "fet", "ondo", "pyth",
    "ron", "ray", "ton", "trx", "ltc", "bch", "etc", "fil", "icp",
    "hbar", "vet", "algo", "ftm", "mana", "sand", "gala",
}

# Map common names to ticker
_NAME_TO_TICKER = {
    "bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL",
    "dogecoin": "DOGE", "doge": "DOGE", "cardano": "ADA",
    "avalanche": "AVAX", "polkadot": "DOT", "chainlink": "LINK",
    "polygon": "MATIC", "uniswap": "UNI", "cosmos": "ATOM",
    "arbitrum": "ARB", "optimism": "OP",
}


#: Determiners that make a ticker-shaped English word the noun it looks like.
_ENGLISH_LEAD = r"(?:the|a|an|this|that|these|those|my|your|our|his|her|their)"


def _written_as_english(text: str, word: str) -> bool:
    """Whether `word` (a ticker that is also an English word) appears in
    `text` only as prose — lowercase, behind a determiner.

    ONE occurrence written as a ticker is enough to make it one: "the link I
    sent, and LINK/USDT looks weak" names the asset.
    """
    for m in re.finditer(rf"\b{re.escape(word)}\b", text, re.IGNORECASE):
        raw = text[m.start():m.end()]
        if raw.isupper():
            return False                      # LINK
        before = text[:m.start()]
        if before.rstrip().endswith("$"):
            return False                      # $link
        if text[m.end():m.end() + 1] == "/":
            return False                      # link/usdt
        if not re.search(rf"\b{_ENGLISH_LEAD}\s+$", before, re.IGNORECASE):
            return False                      # no determiner: "link analysis"
    return True


def symbols_named(text: str) -> list[str]:
    """EVERY distinct asset the message names, in the order named.

    `_extract_symbol` answers the FIRST one, which is the right answer for a
    rule that carries one symbol and the wrong one for deciding whether the
    message named more than the skill can answer: "analyze btc and eth" and
    "sol or avax which is stronger" were dispatched as BTC and SOL, half the
    question answered as though it were the whole of it.
    """
    if not text:
        return []
    out: list[str] = []

    def add(sym: Optional[str]) -> None:
        if sym and sym not in out:
            out.append(sym)

    for m in re.finditer(r"\$?([A-Za-z]{2,10})/(?:USDT|USD|USDC|PERP)\b", text, re.IGNORECASE):
        add(_validate_symbol(f"{m.group(1).upper()}/USDT"))
    for m in re.finditer(r"\$([A-Za-z]{2,10})\b", text):
        add(_validate_symbol(f"{m.group(1).upper()}/USDT"))
    for raw in re.findall(r"[A-Za-z]{2,}", text):
        low = raw.lower()
        if low in _NAME_TO_TICKER:
            add(_validate_symbol(f"{_NAME_TO_TICKER[low]}/USDT"))
        elif low in _KNOWN_SYMBOLS:
            if low in AMBIGUOUS_TICKER_WORDS and _written_as_english(text, low):
                continue
            add(_validate_symbol(f"{low.upper()}/USDT"))
    return out


#: Every ticker and coin name the router knows, as one alternation — the
#: post-mortem rule's object slot and the compound close rules both read it.
_TICKER_WORDS = "|".join(sorted(_KNOWN_SYMBOLS | set(_NAME_TO_TICKER)))


def _extract_symbol(text: str) -> Optional[str]:
    """Extract a crypto symbol from free text. Returns 'BTC/USDT' format or None.

    Every candidate is run through ``_validate_symbol`` before being returned
    so that malformed strings never reach CCXT or the LLM (SEC-H3).
    """
    lower = text.lower()
    words = re.findall(r'[a-zA-Z]+', lower)

    for word in words:
        # Check name mapping first
        if word in _NAME_TO_TICKER:
            candidate = f"{_NAME_TO_TICKER[word]}/USDT"
            return _validate_symbol(candidate)
        # Check known tickers
        if word in _KNOWN_SYMBOLS:
            # …unless it is one of the tickers that is also an English word
            # and it is written here AS an English word: lowercase, behind a
            # determiner. "look at the link I sent" dispatched an analysis of
            # LINK/USDT at confidence 1.0, and "check out the near term"
            # analysed NEAR — the symbol resolving is precisely why
            # `_names_a_non_asset` (which exists for "look at the docs") was
            # never consulted. Written as a ticker it is still a ticker:
            # `$LINK`, `LINK/USDT`, caps `LINK`, or lowercase with no
            # determiner in front ("hows link looking", "link analysis").
            if word in AMBIGUOUS_TICKER_WORDS and _written_as_english(text, word):
                continue
            candidate = f"{word.upper()}/USDT"
            return _validate_symbol(candidate)

    # Try explicit patterns like "BTC/USDT" or "$BTC"
    explicit = re.search(r'\$?([A-Z]{2,10})/USDT', text.upper())
    if explicit:
        candidate = f"{explicit.group(1)}/USDT"
        return _validate_symbol(candidate)
    # A `$`-PREFIXED TOKEN IS A TICKER BY ITS SPELLING, known list or not —
    # the argument `symbol_from_token` already makes for a token typed where
    # a symbol is expected. This branch required membership of
    # `_KNOWN_SYMBOLS`, so "$HYPE" resolved to nothing and a message that IS
    # a ticker was answered by the social gate as small talk, while
    # "analyze $HYPE" worked (the command-word fallback below).
    dollar = re.search(r'\$([A-Z]{2,10})\b', text.upper())
    if dollar:
        candidate = f"{dollar.group(1)}/USDT"
        return _validate_symbol(candidate)

    # Fallback: a word that LOOKS like a ticker next to a command keyword, so a
    # coin too new for the list above is still tradeable from chat.
    #
    # THE GUARD THIS COMMENT PROMISED WAS NEVER IMPLEMENTED. It said "2-10
    # uppercase letters" and then tested `word.isalpha()` against `words`, which
    # is built from `text.lower()` — case is destroyed two lines before it is
    # checked, so `isalpha()` was always true and "uppercase" was never tested
    # at all. What remained was a hand-maintained denylist of English nouns
    # standing between the user and a fabricated ticker, and it lost:
    #
    #     look at the docs        -> DOCS/USDT
    #     analyze the situation   -> SITUATION/USDT
    #     look at the code        -> CODE/USDT
    #     analyze the results     -> RESULTS/USDT
    #     check out the news      -> OUT/USDT      (the verb's own particle)
    #     check out my portfolio  -> OUT/USDT      (a portfolio request)
    #
    # All at confidence 1.0, so each dispatched analyze_asset against a symbol
    # nobody named. A denylist of English words can only ever lose that race —
    # so this reads the case from the ORIGINAL text, which is the signal the
    # comment always claimed to use. Someone who means a ticker writes BTC, WIF
    # or $PEPE; a lowercase unknown word is a noun. When nothing qualifies the
    # result is None, the caller drops to 0.5 and asks WHICH asset, which is the
    # honest answer to "I think you want a chart and I cannot tell of what".
    _CMD_WORDS = {"analyze", "scan", "check", "trade", "buy", "sell", "long",
                  "short", "signal", "setup", "look", "analyse", "chart", "read",
                  # The NOUN too, not just the verbs: "HYPE analysis" is the
                  # widget's own phrasing, and without this an unknown ticker
                  # in that shape resolved to None while "scan HYPE" worked.
                  "analysis",
                  # The dossier's own verbs: "research PENDLE" names a
                  # listing the known-ticker list may not hold, and the
                  # caps token after the verb is how the caller spells it.
                  "research", "dossier", "diligence", "due",
                  # Particles of the multi-word verbs above. Without these,
                  # "check out the news" reads OUT as the asset.
                  "out", "at", "on", "up", "into", "for"}
    has_cmd = any(w in _CMD_WORDS for w in words)
    if has_cmd:
        # Case-preserving tokens, in order, so "the" and "DOCS" stay tellable
        # apart. `words` is lowercased and cannot answer this.
        for raw in re.findall(r'[A-Za-z]+', text):
            if raw.lower() in _CMD_WORDS or not (2 <= len(raw) <= 10):
                continue
            if raw.isupper():
                return _validate_symbol(f"{raw}/USDT")
            # A lowercase or Capitalised word is prose, not a ticker. Stop at
            # the first candidate either way: scanning past it to find some
            # later all-caps word would pick a symbol out of the middle of a
            # sentence the user never meant as one.
            return None

    return None


# ── Rule-based intent patterns ────────────────────────────────────────

# Each entry: (compiled_pattern, skill_name, needs_symbol, explanation)
_INTENT_RULES: list[tuple[re.Pattern, str, bool, str]] = []


#: Words that can sit outside a matched verb without being its object —
#: articles, possessives and the particles of the multi-word verbs…
_FILLER = {"the", "a", "an", "my", "your", "our", "this", "that", "these",
           "those", "it", "its", "me", "us", "to", "of", "for", "on", "in",
           "at", "up", "out", "into", "with", "and", "or", "is", "are",
           "please", "now", "some", "any",
           #: …and the generic market nouns, which name the THING being asked
           #: for rather than a different subject. "analyze the charts" is an
           #: asset request missing its asset — asking which coin is the right
           #: answer, and a test has pinned that since long before this
           #: function existed. Leaving them out made it a docs question.
           "chart", "charts", "market", "markets", "price", "prices",
           "level", "levels", "setup", "setups", "trend", "chartings"}


#: A chart timeframe written the way a chart writes it — `1d`, `4H`, `15m`,
#: `1w`, and the MT4 order (`d1`, `w1`). Bounded on both sides so a price
#: (`63000`), a count (`5 trades`) and a word (`m5x`) are not timeframes.
_TIMEFRAME_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])(?:\d{1,2}\s?[mhdwMHDW]|[mhdwMHDW]\d{1,2})(?![A-Za-z0-9])")

#: The trigger words that make a message a request to SWEEP the universe
#: rather than to read one asset. Read from the matched trigger itself, so a
#: rule added later is classified by what it says and not by a second list.
_SWEEP_TRIGGER = re.compile(r"sca+n+|screen|sweep", re.IGNORECASE)


def _names_a_non_asset(text: str, match) -> bool:
    """Did the user name an object, and was it not an asset?

    Only consulted when a symbol-needing rule matched and `_extract_symbol`
    found nothing, and the distinction it draws is the whole point:

        "analyze"            no object   -> ask which coin. Right answer.
        "check the setup"    no object   -> ask which coin. Right answer.
        "look at the docs"   object      -> not ours. Do not claim it.

    Both used to take the first branch, so a docs question was answered with
    "what coin do you want me to look at?". That is not a lie, but it answers
    a question nobody asked — and it used to be far worse: before the extractor
    was fixed, the same message resolved to DOCS/USDT and ran an analysis.

    Anything INSIDE the match is part of the trigger phrase, not an object;
    "check the setup" is matched whole by its own rule and correctly has
    nothing left over. Only leftover, non-filler words count.

    A TIMEFRAME IS AN OBJECT WHEN THE TRIGGER IS A SWEEP AND A MODIFIER WHEN
    IT IS AN ANALYSIS, and the word-based reading above could see neither.
    `re.findall(r"[A-Za-z]{2,}")` reads runs of two or more letters, so every
    chart spelling of a timeframe is INVISIBLE to it — `1d` is a digit and one
    letter, `d1` the same backwards — while the English spellings are ordinary
    words. That split the one request in two: "weekly scan" left the word
    `weekly` over, was read as naming a non-asset object and correctly reached
    the model, and "1d scan" left `1d` over, was read as naming NOTHING, and
    so took the bare-verb branch — answered "which coin do you want me to look
    at?" for a request that had named the sweep's own parameter and no asset
    at all. `1D scan` and `d1 scan` did the same.

    It is scoped to a SWEEP trigger on purpose. "analyze 4h" leaves the same
    token over and asking which coin IS the answer there: the caller wants a
    chart and has named the timeframe, not the subject. The trigger word is
    what tells the two apart, so it is read from the match rather than guessed.

    The destination for such a sweep today is the model, which is where
    `weekly` has been recorded as going since the mode rules were anchored.
    That is better than asking which coin and it is still not the door, and
    the reason is worth stating precisely because a first draft of this
    docstring got it wrong: the LADDER CARD (`ProScanSkill.MODE_CFG`) runs
    three modes — 5m, 15m and 4h — but the FULL-UNIVERSE SWEEP takes any of
    `candles.SUPPORTED_TIMEFRAMES`, which includes `1d`, so `/deepscan 1d` is
    a real daily sweep of the whole universe and "a ladder this scanner does
    not run" was true of one skill and false of the product. Routing a typed
    `1d` to the deep scan at the timeframe it names is the follow-up, and it
    needs a dispatch row per timeframe: `SCAN_DISPATCH` is keyed by intent
    name and `scan_deep` carries a fixed `{"timeframe": "4h"}`. `weekly` and
    `monthly` really are run by neither. Filed, not done.
    """
    leftover = text[:match.start()] + " " + text[match.end():]
    if any(w.lower() not in _FILLER
           for w in re.findall(r"[A-Za-z]{2,}", leftover)):
        return True
    return bool(_SWEEP_TRIGGER.search(match.group(0) or "")
                and _TIMEFRAME_TOKEN.search(leftover))


_JOURNAL_COUNT = re.compile(r"\b(?:last|recent|past|previous)\s+(\d{1,3})\s+(?:trades|closes)\b", re.IGNORECASE)
_JOURNAL_SUPERLATIVE = re.compile(
    r"\b(?:worst|biggest|largest|best)\s+(?:loss|loser|losing trade|win|winner|winning trade|trade)\b",
    re.IGNORECASE)
#: The window a superlative is read over. The journal card ranks nothing —
#: it prints the last N closes with their P&L — so "my worst loss" is
#: answered by a window wide enough for the reader to find it, and the card's
#: own header says which window that was.
JOURNAL_SUPERLATIVE_COUNT = 50


def place_target(text: str) -> Optional[str]:
    r"""The ONE asset a request to open a trade named, or None.

    One reading, both surfaces. The web had a private regex
    (`^(?:paper\s+)?(?:long|short|buy|sell)\s+([a-z0-9]{2,12})$`) that
    claimed "long eth", "buy eth" and "short btc" and answered them with the
    agent's setup, and Telegram had nothing at all — so the same three
    sentences got a setup card on one surface and a tool-less chat model on
    the other, and every other phrasing of the same request ("go long eth",
    "market buy eth", "open a long on eth") got the model on both.

    `symbols_named` is the reading, so the place door and the analysis rules
    agree about what asset a message named. TWO assets named is not one asset
    asked about — "buy eth and btc" answers None, and the door notice stands
    alone rather than picking the first and quietly answering half the
    message, which is the rule `symbols_named` was written for.

    A name the 49-symbol list cannot resolve (PENDLE, NATGAS, RAVE — all
    filled live) also answers None: the door is still shown, and no setup is
    named for an asset nobody could read.
    """
    named = symbols_named(text)
    return named[0] if len(named) == 1 else None


def journal_count(text: str) -> Optional[int]:
    """How many closes the question asked for, or None for the card's own
    default: "my last 10 trades" names ten, "my worst loss" needs a window
    wide enough to hold it, and "recent trades" names nothing."""
    m = _JOURNAL_COUNT.search(text or "")
    if m:
        return max(1, min(200, int(m.group(1))))
    if _JOURNAL_SUPERLATIVE.search(text or ""):
        return JOURNAL_SUPERLATIVE_COUNT
    return None


def _rule(pattern: str, skill: str, needs_symbol: bool = False, explanation: str = ""):
    """Register a rule-based intent pattern."""
    _INTENT_RULES.append((
        re.compile(pattern, re.IGNORECASE),
        skill,
        needs_symbol,
        explanation or f"Matched pattern for {skill}",
    ))


def routed_skill_names() -> set[str]:
    """Every skill name some rule in this module can emit.

    Public because the capability card has to know which of the things it
    advertises a caller can actually ASK FOR in words, and the alternative was
    a hand-written list somewhere else — which is the shape that gave this repo
    three disagreeing copies of the scan dispatch. Derived from the table, so a
    rule added tomorrow is in the answer without anybody remembering.

    The names are the ROUTER's, not always the skill that finally runs:
    `scan_deep` is a name no registry knows. `skill_doors.dispatches_to` is the
    mapping, and it is deliberately not applied here — this function answers
    what the ROUTER emits, and conflating the two is how the third copy got
    its wrong rows.
    """
    return {skill for _pattern, skill, _needs, _why in _INTENT_RULES}


# "RISK ON" / "RISK OFF" as a STANCE, not as the noun. The bare alternative
# matched anywhere in a sentence, so "event risk on eth" — a read-only
# question about macro exposure — opened the card that PROPOSES trading more
# aggressively, at confidence 1.0, and "macro risk on sol" and "what's the
# risk on this trade" did the same. The stance reading is the whole message
# or a stance verb in front of it; anything else is the ordinary English noun.
_RISK_STANCE = r"(?:^|\b(?:go|going|gone|switch|flip|turn|be|get|stay|staying)\s+(?:to\s+)?)risk[-\s]{side}\b"

# --- Agent stance (talk-to-your-agent risk posture) ---
# Registered FIRST so risk-preference phrasing wins over scan/analyze rules.
# These never execute anything: the handler PROPOSES a stance switch with a
# confirm button (the mode_ callback path, which is permission-gated).
_rule(r"\b(be (a bit |a little |way |much )?more (careful|cautious|conservative|defensive)|"
      r"(reduce|lower|less|cut) (the )?risk|" + _RISK_STANCE.format(side="off") + r"|play it safe(r)?|"
      r"(take |use )?smaller positions?|slow (it |things )?down|protect (the )?capital|"
      r"trade (more )?defensive(ly)?|dial (it|risk) (back|down))\b",
      "stance_defensive", explanation="Wants a more defensive risk posture")
_rule(r"\b(be (a bit |a little |way |much )?more aggressive|"
      r"(increase|raise|more|add) (the )?risk|" + _RISK_STANCE.format(side="on") + r"|push (it )?harder|"
      r"(take |use )?bigger positions?|trade (more )?aggressive(ly)?|"
      r"step on (the )?gas|go bigger)\b",
      "stance_aggressive", explanation="Wants a more aggressive risk posture")
_rule(r"\b((back to|go) (normal|balanced|default)( risk| mode)?|"
      r"balanced (mode|stance|risk)|reset (the )?(risk|stance|mode))\b",
      "stance_balanced", explanation="Wants the balanced default posture")

# THE HALT RULES COME FIRST, and the ordering is the answer to a message
# carrying both: "close all positions and halt" is a flatten joined to a
# halt, and /emergency_stop's confirm card does BOTH where the close notice
# points at the positions card and says nothing about the halt — the second
# ask, dropped in silence. A close with no halt clause in it reaches the
# close rules below untouched: none of these matches one.
#: How two clauses are joined in one message — ",", "and", "then". Used by
#: the compound CLOSE rules further down and by the compound HALT rules further
#: down: one spelling of "and another thing", read the same way by both.
_CLAUSE_JOIN = r"(?:\s*[,;]\s*|\s+)(?:and\s+then\s+|and\s+|then\s+)?"

_HALT_LEAD_WORDS = (r"(?:(?:please|pls|plz|just|ok|okay|now|can you|could you|can u|would you|"
                    r"go ahead and|i need you to|i want you to|please can you|let's|lets)\s+)*")
_HALT_LEAD = r"^\s*" + _HALT_LEAD_WORDS
#: A SOCIAL lead — "bro", "lol", "thanks", "yo" — on a whole-message action.
#: `_SOCIAL_CHAT` and `_GREETING_PATTERNS` anchor on these words and
#: `_THANKS_PATTERNS` searches for them anywhere, so "bro stop the bot",
#: "lol stop" and "thanks, stop the bot" were greeted — the action rules
#: are consulted first by the social gate, and every one of them begins
#: with `_HALT_LEAD`, which knows politeness and not informality. The lead
#: is read as what it is: a signal that the sentence is casual. A casual
#: FLEET HALT goes to the ambiguous door (the notice and the door, never the
#: unconfirmed dispatch); the emergency phrase keeps its confirm card,
#: which is the confirmation; a `my`-scoped pause keeps /pause, which is the
#: caller's own and reversible. Politeness may sit on either side of it
#: ("ok bro, please stop the bot").
_SOCIAL_LEAD_WORDS = (r"(?:(?:bro|bruh|dude|mate|fam|man|lol|lmao|rofl|haha|hah|thanks|thank\s+you|"
                      r"thx|ty|tysm|cheers|yo|hey|hi|hello|ok\s+cool|cool|nice)[\s,!.:;\-–—]+)+")
_SOCIAL_LEAD = r"^\s*" + _HALT_LEAD_WORDS + _SOCIAL_LEAD_WORDS + _HALT_LEAD_WORDS
_HALT_TAIL = (r"(?:[\s,;\-–—]+(?:now|please|pls|plz|asap|immediately|right\s+now|for\s+now))*"
              r"(?:[\s,]+(?:thanks|thank\s+you|thx|ty|cheers|tysm))?"
              r"\s*[!.]*(?:\s*[^\w\s?]+)*\s*$")
_HALT_OBJECT = (r"(?:(?:the|this|all)\s+)?(?:trading\s+bot|bot|trading|engine)"
                r"|everything|all|all\s+(?:the\s+)?trades?")
_HALT_BODY = (
    r"(?:halt(?:\s+(?:" + _HALT_OBJECT + r"))?"
    r"|(?:stop|pause|freeze|disable|suspend)\s+(?:" + _HALT_OBJECT + r")"
    r"|kill\s+(?:(?:the|this)\s+)?(?:trading\s+bot|bot|engine|trading)"
    r"|kill\s+(?:everything|all)"
    r"|shut\s+(?:the\s+bot|the\s+engine|the\s+trading\s+bot|everything|it\s+all|all\s+of\s+it)\s+down"
    r"|shut\s*down\s+(?:the\s+)?(?:trading\s+bot|bot|engine|everything|trading)"
    r"|(?:turn|switch)\s+(?:the\s+)?(?:trading\s+bot|bot|engine|trading)\s+off"
    r"|(?:turn|switch)\s+off\s+(?:the\s+)?(?:trading\s+bot|bot|engine|trading)"
    r"|(?:stop|pause|halt|no|block)\s+(?:opening\s+|taking\s+|placing\s+|entering\s+)?"
    r"(?:any\s+)?(?:new|more|further)\s+(?:entries|trades|positions|orders))"
)
_EMERGENCY_BODY = (
    r"(?:emergency\s+(?:stop|halt|shutdown)(?:\s+(?:" + _HALT_OBJECT + r"))?"
    r"|(?:(?:hit|flip|pull|press|engage|trigger)\s+(?:the\s+)?)?kill\s*switch)"
)
_FLATTEN_BODY = (
    r"(?:(?:close|flatten)\s+(?:everything|all(?:\s+(?:open\s+)?positions)?"
    r"|every\s+(?:open\s+)?position|all\s+(?:my\s+|the\s+)?trades))"
)
HALT_IMPERATIVE = re.compile(_HALT_LEAD + _HALT_BODY + _HALT_TAIL, re.IGNORECASE)
EMERGENCY_STOP = re.compile(_HALT_LEAD + _EMERGENCY_BODY + _HALT_TAIL, re.IGNORECASE)
HALT_COMPOUND_HALT = re.compile(
    _HALT_LEAD + _HALT_BODY + r"(?:" + _CLAUSE_JOIN + _HALT_BODY + r")+" + _HALT_TAIL,
    re.IGNORECASE)
_ANY_CLAUSE = r"(?:" + _HALT_BODY + r"|" + _EMERGENCY_BODY + r"|" + _FLATTEN_BODY + r")"
HALT_COMPOUND_ANY = re.compile(
    _HALT_LEAD + _ANY_CLAUSE + r"(?:" + _CLAUSE_JOIN + _ANY_CLAUSE + r")+" + _HALT_TAIL,
    re.IGNORECASE)
_PAUSE_BODY = (
    r"(?:(?:stop|pause|halt|freeze|disable|suspend)\s+my\s+(?:trading\s+bot|trading|bot|engine|agent|account)"
    r"|(?:turn|switch)\s+my\s+(?:trading\s+bot|bot|engine|trading)\s+off"
    r"|(?:turn|switch)\s+off\s+my\s+(?:trading\s+bot|bot|engine|trading))")
PAUSE_OWN = re.compile(_HALT_LEAD + _PAUSE_BODY + _HALT_TAIL, re.IGNORECASE)
# "emergency" and "panic" with nothing named are not verbs, but they are the
# same state: an operator reaching for a switch and not naming one. Answered
# with the DOOR, never dispatched, exactly like a bare "stop". Until this they
# were three words or fewer with no trading word in them, so the social gate
# greeted them.
_BARE_LEAD_WORDS = r"(?:(?:please|pls|plz|just|ok|okay)\s+)*"
_BARE_VERB_BODY = (r"(?P<verb>stop|kill|pause|freeze|disable|panic|emergency"
                   r"|shut\s*down|shut\s+it\s+down)"
                   r"(?:[\s,]+(?:it|now|please|pls|plz|right\s+now|asap))*"
                   r"\s*[!.]*(?:\s*[^\w\s?]+)*\s*$")
HALT_BARE_VERB = re.compile(r"^\s*" + _BARE_LEAD_WORDS + _BARE_VERB_BODY, re.IGNORECASE)
#: The same bodies behind a social lead. Registered with the halt block and
#: consulted by the social gate with it; `halt_verb` reads the bare form's
#: verb through both.
HALT_SOCIAL_LEAD = re.compile(_SOCIAL_LEAD + _HALT_BODY + _HALT_TAIL, re.IGNORECASE)
EMERGENCY_SOCIAL_LEAD = re.compile(_SOCIAL_LEAD + _EMERGENCY_BODY + _HALT_TAIL, re.IGNORECASE)
PAUSE_SOCIAL_LEAD = re.compile(_SOCIAL_LEAD + _PAUSE_BODY + _HALT_TAIL, re.IGNORECASE)
BARE_SOCIAL_LEAD = re.compile(
    r"^\s*" + _BARE_LEAD_WORDS + _SOCIAL_LEAD_WORDS + _BARE_LEAD_WORDS + _BARE_VERB_BODY,
    re.IGNORECASE)
#: "What can you do?" — a whole-message question about the PRODUCT, not small
#: talk. The bare tokens (`help`, `commands`, `menu`) already routed; every
#: phrasing a person actually uses did not. Driven: `what can you do` was eaten
#: by `_SOCIAL_CHAT`, `capabilities` and `/help` by the three-word rule, and
#: `how does this work`, `show me what you can do`, `what can i ask` and
#: `im new what now` simply matched nothing and reached a tool-less model —
#: which is the exact failure the unavailable notice was written to prevent,
#: with the model improvising the product's own feature list.
#:
#: `how (does|do) (this|it|you) work` only, never a bare "how does X work":
#: "how does funding work" is a question about the market and belongs to the
#: model that can explain it.
#:
#: WIDENED, because the first version answered fifteen of thirty realistic
#: phrasings and the other fifteen reached a tool-less model that improvises
#: the product's own feature list — the failure this rule exists to prevent.
#: Driven corpus: "what can you help me with", "what are you capable of",
#: "what tools do you have", "how can you help", "what else can you do",
#: "list what you can do" and "what does this bot do" all matched NOTHING.
#:
#: AND A TRAILING "thanks" DEFEATED EVERY ONE OF THEM. The social gate does
#: consult this rule before its unanchored politeness pattern — but the rule
#: is anchored at `$`, so "what can you do thanks" matched no rule to consult
#: and fell through as small talk. That is the trailing-politeness trap this
#: file records fixing for the halt rules one slice earlier, in a new spelling
#: ten lines away: the lead was allowed and the TAIL was not.
CAPABILITY_ASK = re.compile(
    r"^\s*(?:(?:so|ok|okay|hey|hi|yo|erm|um)[,\s]+)*"
    r"(?:"
    r"what\s+(?:can|do)\s+you\s+(?:actually\s+|even\s+)?do(?:\s+for\s+me)?"
    r"|what\s+else\s+can\s+you\s+do"
    r"|what\s+(?:are\s+you\s+able\s+to\s+do|are\s+you\s+capable\s+of)"
    r"|what\s+can\s+(?:this|the)\s+(?:bot|thing|agent)\s+do"
    r"|what\s+does\s+(?:this|the)\s+(?:bot|thing|agent)\s+do"
    r"|what\s+(?:are\s+your|other)\s+(?:capabilities|features)"
    r"|what\s+(?:features|tools|capabilities)\s+do\s+you\s+have"
    r"|what\s+can\s+you\s+(?:help|show)(?:\s+me)?(?:\s+with)?"
    r"|what\s+can\s+i\s+ask(?:\s+you)?(?:\s+for)?"
    r"|what\s+should\s+i\s+ask(?:\s+you)?"
    r"|what\s+do\s+you\s+offer"
    r"|how\s+can\s+you\s+help(?:\s+me)?"
    r"|(?:show|tell|list)\s+(?:me\s+)?what\s+you\s+can\s+do"
    r"|capabilities|features"
    r"|how\s+(?:does|do)\s+(?:this|it|you)\s+work"
    r"|(?:i'?m|im)\s+new[,.]?\s*what\s+(?:now|next|do\s+i\s+do)"
    r"|/help"
    r")"
    # The politeness TAIL. Anchored rules that forbid it read a courteous
    # question as small talk, which is the one reading a courteous question
    # never deserves.
    r"(?:[\s,]+(?:please|pls|plz|thanks|thx|ty|mate|bro|dude|lol|here))*"
    r"\s*[?!.]*\s*$", re.IGNORECASE)

_NOT_A_TICKER = (
    r"the|it|this|that|them|us|me|you|my|mine|"
    r"today|tomorrow|yesterday|now|later|tonight|"
    r"everything|anything|something|all|stuff|things|"
    r"market|markets|price|prices|chart|charts|trading|"
    r"docs|help|news|here|there|then|what|why|how|"
    # THE MODE LEAD'S OWN DETERMINERS. `_MODE_LEAD` reads these as "ways of
    # asking for a ladder" — "any 30m setups", "some 4h ideas" — and the
    # symbol-first rule read the same first word as the SYMBOL slot, so
    # "any 30m setups" resolved no ticker and was answered "which coin do you
    # want me to look at?" for a request that had named a timeframe and no
    # asset. A word one rule treats as filler is not a ticker for another.
    r"any|some|good|best|top|nice|fresh|new|show|give|gimme|got|find|run|do|"
    r"lets|please|"
    # THE NOUNS THE OTHER RULES ALREADY OWN AS OBJECTS. `place_order`'s
    # object is written as an asset and its verbs are ordinary English
    # ("open", "get", "take", "add to"), so without these "open the
    # dashboard" and "get my balance" are requests to open a trade in an
    # asset called `dashboard` and one called `balance`. Same argument as
    # the mode lead's determiners above, one rule over: a word another rule
    # treats as its OBJECT is not a ticker for this one.
    r"a|an|our|more|back|up|down|out|in|on|off|again|"
    r"position|positions|trade|trades|order|orders|limit|limits|"
    r"dashboard|bot|engine|account|accounts|wallet|portfolio|balance|"
    r"profit|loss|money|cash|funds|risk|leverage|margin|stop|stops"
)


_CLOSE_LEAD = (r"^\s*(?:(?:please|pls|can you|could you|can u|go ahead and|i want to|"
               r"i'd like to|i need to|let's|lets|just|now)\s+)*")


# --- Open a trade: ROUTED, never dispatched ---
# A request to PLACE was the one action with no door. Driven over 54 ordinary
# phrasings before this rule existed: six got a CONFIDENT WRONG CARD — "place
# a limit order on pendle", "place a limit order" and "put in a limit order
# for btc" reached `get_orders`, whose `limit orders?` alternative matches
# inside the sentence, so a request to PLACE one was answered with the card
# that LISTS the resting ones; "open a position in sol", "add to my eth
# position" and "double my eth position" reached `get_portfolio`. That is the
# `get_orders` lesson one VERB over: a rule matching inside a sentence routes
# the sentence's verb as the command. A bare "long" / "short" / "buy" /
# "sell" was GREETED — one word, no symbol, no trading word — which is
# "stake my usdc" one action over. Everything else reached the chat model,
# which holds read-only tools and would narrate a placement.
#
# THREE SOURCE COMMENTS ALREADY NAMED THIS RULE. `manual_trade.py` twice and
# `telegram_handler.py` once say that what the full grammar declines "is the
# router's `place_order` rule's, which answers with this grammar as the
# door". No such rule existed: what the grammar declined reached the orders
# card, the positions card, the greeter or the model. That is the `/vault`
# hint shape inside a code comment — a claim about a door nobody built.
#
# Registered AFTER close/cancel/modify/stake so each keeps its own words
# ("close my long", "cancel my order", "sell my eth position", "stake my
# usdc"), and BEFORE the halt block and every read rule. Anchored to the
# whole message like the close rule, so a compound ("buy eth and close my
# btc") falls through to the close-compound rules below, which already
# decide that of two intents in one message the close door answers.
#
# `?` is never a terminal and a leading question or report word disqualifies:
# "should i long eth" is the chart's, "is it a good time to buy eth", "what
# is a limit order", "did you buy eth", "do you think eth is a buy", "are we
# long or short" and "the bot went long eth" are the model's.
#
# THE OBJECT IS PERMISSIVE ON PURPOSE. `_KNOWN_SYMBOLS` holds 49 names and
# the bot filled PENDLE, NATGAS, TRUMP and RAVE live on 2026-09-15 — none of
# them on it — so an object restricted to that list would refuse the door to
# the assets the product actually trades, which is the `67+ symbols` lesson
# one noun over. It is a token NOT in `_NOT_A_TICKER` instead, and the rule
# is whole-message anchored, so a stray idiom costs one notice saying nothing
# was placed. That is the same trade `_CLOSE_TARGET` makes and states.
_PLACE_NOTQ = (r"(?!\s*(?:what|how|why|when|where|which|who|should|would|shall"
               r"|is|are|was|were|do|does|did|am|has|have|had|the|it|we|they"
               r"|you|he|she)\b)")
#: The urgency/politeness tail, with the COMMA the halt rule's own tail
#: allows. Without it "buy eth, thanks" is a request to open a trade that
#: `_THANKS_PATTERNS` — an unanchored search — answers with "hey!", which is
#: the defect the social slice fixed for the halt and left here.
_PLACE_TAIL = r"(?:[\s,]+(?:now|please|pls|plz|asap|ty|thanks|thx))*\s*[!.]*\s*$"
#: A CASUAL lead is informality, and informality goes to the door. "bro buy
#: eth" and "lol long eth" met `_SOCIAL_CHAT`'s `^bro` and the three-word
#: rule and were GREETED. The halt rule needed a separate intent for this
#: (a casual fleet halt must not DISPATCH); here nothing is dispatched
#: either way, so the lead simply joins this rule's own and the request
#: reaches the same door it always would.
_PLACE_LEAD = (r"^\s*(?:(?:bro|dude|mate|man|lol|lmao|ok|okay|yo|hey|hi|right|so|"
               r"alright|thanks|thx|ty|cheers|please|pls|can you|could you|can u|"
               r"go ahead and|i want to|"
               r"i'd like to|i need to|let's|lets|just|now)[\s,]+)*")
_PLACE_ASSET = (rf"(?!(?:{_NOT_A_TICKER})\b)"
                r"\$?[A-Za-z][A-Za-z0-9]{1,11}(?:[/:][A-Za-z0-9]{2,10})*")
#: "buy me 0.5 eth", "buy 100 of eth", "buy some eth" — a size between the
#: verb and the asset. Never read as the size to place: nothing here sizes
#: anything, and the notice says so.
_PLACE_SIZE = (r"(?:(?:me|us)\s+)?(?:(?:some|more|a\s+bit\s+of|a\s+little|a\s+few)\s+)?"
               r"(?:\$?\d[\d,.]*\s*[km]?\s*(?:usd|usdt|worth\s+of|of)?\s+)?"
               r"(?:(?:some|more)\s+)?")
_PLACE_LEV = r"(?:\d{1,3}\s*x\s+)?"
_PLACE_KIND = r"(?:(?:market|limit|stop|spot|perp|perps|paper)\s+)*"
_PLACE_DIRW = r"(?:(?:buy|sell|long|short)\s+)?"
_PLACE_VERB = r"(?:long|short|buy|sell)"
#: The ways of wrapping a directional verb: "go long eth", "take a long on
#: sol", "put on a long eth", "open a 10x long on eth".
_PLACE_WRAP = (r"(?:(?:go|take|put on|put|open|enter|get|start|initiate|place|set)\s+"
               r"(?:(?:a|an|the|my)\s+)?)?")
_PLACE_PRICE = r"(?:\s+(?:at|around|near|@)\s*\$?\d[\d,.]*\s*[km]?)?"
_PLACE_TO = r"(?:\s+(?:on|for|in|into|of)\b)?"
PLACE_ORDER = re.compile(
    _PLACE_LEAD + _PLACE_NOTQ + r"(?:"
    # (A) the word ORDER, with a verb that places one. `open` is NOT one of
    # them: "open orders" is the LISTING, and "open an order" is nobody's
    # phrasing — "open a trade" is (E).
    r"(?:place|put in|put|set|submit|create|leave|rest|send)\s+"
    r"(?:(?:a|an|the|my|another)\s+)?" + _PLACE_LEV + _PLACE_KIND + _PLACE_DIRW
    + r"orders?(?:\s+(?:on|for|in|at|to|with)\s+.{1,40})?"
    # (A2) an order named by its KIND — "Place limit PENDLE", the filed case
    # that reached no rule at all.
    r"|(?:place|put in|submit|rest|leave|set)\s+(?:(?:a|an|another)\s+)?"
    + _PLACE_LEV + r"(?:market|limit|stop)\s+" + _PLACE_DIRW + _PLACE_ASSET
    + _PLACE_PRICE
    # (B) a directional verb and something written as an asset. The
    # preposition is `on|for|into` and never a bare `in`: "buy in bulk" is an
    # idiom, "buy into eth" is (C).
    + r"|" + _PLACE_WRAP + _PLACE_LEV + _PLACE_KIND + _PLACE_VERB
    + r"\s+(?:(?:on|for|into)\s+)?(?:the\s+dip\s+(?:on|in)\s+)?"
    + _PLACE_SIZE + _PLACE_ASSET + _PLACE_PRICE
    # (C) into an asset — "scale into btc", "get me into eth". `get` and
    # `buy` need `into` or `in on`, or "get in touch" and "buy in bulk"
    # become requests to open a trade in an asset called `touch`.
    + r"|(?:(?:scale|size|ape|lean|load)\s+(?:(?:me|us|back|up)\s+)?(?:in|into|on)"
    r"|(?:get|buy|ape)\s+(?:(?:me|us)\s+)?(?:into|in\s+on))"
    r"\s+(?:(?:some|more)\s+)?" + _PLACE_ASSET
    # (D) grow an existing position — "add to my eth position".
    + r"|(?:add(?:\s+to)?|double|increase|top\s+up)\s+(?:(?:my|the|our)\s+)?"
    r"(?:" + _PLACE_ASSET + r"\s+)?(?:positions?|trades?|longs?|shorts?|bags?)"
    # (E) "open a position in SOL". An ARTICLE and a SINGULAR, or an
    # explicitly named asset — "open positions" and "my positions" are the
    # read, and this rule sits above it.
    + r"|(?:open|start|enter|take)\s+(?:(?:a|an|another)\s+" + _PLACE_LEV
    + r"(?:position|trade)"
    r"|(?:a|an|another|my|the)?\s*" + _PLACE_LEV
    + r"(?:positions?|trades?)\s+(?:in|on|for)\s+" + _PLACE_ASSET + r")"
    r"(?:\s+(?:in|on|for)\s+" + _PLACE_ASSET + r")?"
    # (F) symbol first — "eth long", "btc short".
    + r"|" + _PLACE_ASSET + r"\s+" + _PLACE_LEV + r"(?:long|short)"
    # (G) the bare verb, nothing named. Answered with the door and no setup:
    # there is no asset to read one for, and naming one nobody named would be
    # the invention the door exists to prevent.
    + r"|" + _PLACE_VERB
    + r")" + _PLACE_TO + _PLACE_TAIL, re.IGNORECASE)


#: Whole-message ACTION rules: a message that IS one of these is never social,
#: whatever thanks or greeting it also carries (the social gate consults this
#: tuple before its own unanchored politeness pattern). The doc-comment used to
#: sit twenty lines up, above `CAPABILITY_ASK`, where an insertion had orphaned
#: it onto the wrong name.
_ANCHORED_ACTION_RULES = (HALT_COMPOUND_HALT, HALT_COMPOUND_ANY, EMERGENCY_STOP,
                          HALT_IMPERATIVE, PAUSE_OWN, HALT_BARE_VERB, PLACE_ORDER,
                          HALT_SOCIAL_LEAD, EMERGENCY_SOCIAL_LEAD, PAUSE_SOCIAL_LEAD,
                          BARE_SOCIAL_LEAD,
                          CAPABILITY_ASK)
_rule(HALT_COMPOUND_HALT.pattern, "halt",
      explanation="Two or more halt clauses in one message — the operator's own imperative, twice")
_rule(HALT_COMPOUND_ANY.pattern, "emergency_stop",
      explanation="A halt joined to a flatten or the emergency phrase — routed to the confirm card")
_rule(EMERGENCY_STOP.pattern, "emergency_stop",
      explanation="Emergency stop request — routed to the /emergency_stop confirm card")
_rule(HALT_IMPERATIVE.pattern, "halt",
      explanation="Halt request — the operator's own imperative, anchored to the whole message")
_rule(PAUSE_OWN.pattern, "pause",
      explanation="A my-scoped stop/pause — routed to the scope-aware /pause, never the fleet halt")
_rule(HALT_BARE_VERB.pattern, "halt_ambiguous",
      explanation="A bare stop/kill/pause with nothing named — answered with the door, never dispatched")
# A social lead ("bro", "lol", "thanks", "yo") on any of the above: casual,
# so the fleet halt is demoted to the door; the confirm card and the
# caller's own pause keep their destinations.
_rule(EMERGENCY_SOCIAL_LEAD.pattern, "emergency_stop",
      explanation="Emergency stop request behind a social lead — the confirm card is the confirmation")
_rule(PAUSE_SOCIAL_LEAD.pattern, "pause",
      explanation="A my-scoped stop behind a social lead — the caller's own /pause")
_rule(HALT_SOCIAL_LEAD.pattern, "halt_ambiguous",
      explanation="A fleet halt behind a social lead — casual, so answered with the door, never dispatched")
_rule(BARE_SOCIAL_LEAD.pattern, "halt_ambiguous",
      explanation="A bare stop-word behind a social lead — answered with the door, never dispatched")

# --- Close a position: ROUTED, never dispatched ---
# "close my ETH" had no rule, so it fell through to the chat model — which
# holds read-only tools, was never told it cannot act, and is guarded against
# fabricated `[skill] result:` BLOCKS only; a prose "Done, I closed it" passes.
# This is a routing intent like `help` and `status`, not a registered skill:
# Telegram answers with the positions card, whose owner-checked Close button
# is the only honest free-text-adjacent door (/liveclose is admin-only, takes
# a TRADE ID and closes with no confirmation — the wrong door), and the web
# names that door. Anchored to the START so it claims IMPERATIVES — "close my
# ETH", "exit the BTC long", "flatten everything" — and leaves "should I close
# my BTC?" and "how do I close a trade?" to the model, which is who answers
# advice. Registered before the portfolio rules because `my positions?` would
# otherwise take "close my position" first.
#: "close my ETH", "exit BTC", "get out of sol" — the bare-ticker close, with
#: no `positions?` noun to anchor on. Named because the compound rules below
#: reuse it: a second copy of this would be a second answer.
_CLOSE_TARGET = (r"(?:close|exit|flatten|unwind|get out of)\s+(?:my\s+|the\s+)?"
                 r"[A-Za-z][A-Za-z0-9/:]{1,12}")
#: The same clause for the COMPOUND rules, whose object is narrowed to
#: something written as an asset or as the whole book. The anchored rule above
#: is the whole message, so "close the gap" costs a notice; a compound reaches
#: into the middle of a sentence, where "close the gap and move on" would be a
#: close request manufactured from an idiom.
_CLOSE_OBJECT = (r"(?:\$?(?:{tickers})(?:/[A-Za-z]{{2,10}})?"
                 r"|(?-i:[A-Z]{{2,10}})(?:/(?-i:[A-Z]{{2,10}}))?"
                 r"|everything|all of it|all positions|the book|my book)")
_CLOSE_CLAUSE = (r"(?:close|exit|flatten|unwind|get out of)\s+(?:my\s+|the\s+|all\s+)?"
                 + _CLOSE_OBJECT)
_rule(_CLOSE_LEAD
      + r"(?:(?:close|exit|flatten|unwind|liquidate)\s+(?:(?:my|the|this|that|all|all my|every|all of my)\s+)?"
      r"(?:[A-Za-z0-9/:]+\s+)?(?:positions?|trades?|longs?|shorts?)\b"
      # "stop the trade", "halt my trades", "freeze this position": a request
      # about a TRADE or POSITION is a close request and meets the close
      # door — never the fleet halt one draft of the halt rule made of it.
      # The determiner is NOT `all` alone: "stop all trades" is the halt's.
      r"|(?:stop|halt|freeze|pause)\s+(?:(?:my|the|this|that|all my|every|all of my)\s+)"
      r"(?:[A-Za-z0-9/:]+\s+)?(?:positions?|trades?|longs?|shorts?)\b"
      r"|" + _CLOSE_TARGET + r"(?:\s+now|\s+please)?\s*[!.]*\s*$"
      r"|sell\s+(?:my|the|this)\s+\S+\s+(?:positions?|trades?)\b)",
      "close_position",
      explanation="Wants a position closed — routed to the positions card, never dispatched")

# AN ACTION JOINED TO A READ MUST NOT LOSE THE ACTION. The branch above is
# anchored to the end of the message, so "close my ETH and scan the market"
# matched no close rule and the scan rule below took it: the close request
# vanished, answered with a market scan and no sentence. The other order is
# the same message ("scan the market and close my eth"), and so is a join by
# `then` or a comma. Registered directly under the close rule, before every
# read rule, for the reason the halt compound is: of the two intents in one
# message, the one that asks to MOVE MONEY is the one that must be answered,
# and the notice says the rest was not run.
# A stop or target change. There is NO door for it — no command, no button,
# no executor method changes SL/TP on an open position — so the honest
# answer says so. Registered here rather than lower because "set stop LOSS"
# and "take PROFIT" matched the bare Portfolio-keyword rule below and came
# back as the positions card, silently.
_rule(_CLOSE_LEAD
      # The REMOVAL verbs were missing entirely, so "remove my stop loss" —
      # a request to take protection OFF an open position — fell through to
      # the bare Portfolio keyword rule and came back as the positions card
      # with no sentence, and "take off my sl" reached the model. Removing a
      # stop is the modification with the most at stake.
      + r"(?:(?:set|move|change|update|adjust|raise|lower|tighten|widen|trail|edit|modify"
      r"|remove|delete|drop|clear|cancel|kill|take\s+off|get\s+rid\s+of|turn\s+off)\s+"
      r"(?:(?:my|the|this|that|a)\s+)?(?:[A-Za-z0-9/:]+\s+)?"
      r"(?:stops?(?:[\s-]?loss)?|sl|take[\s-]?profit|tp|targets?\s+(?:on|for|to|at))\b"
      r"|(?:stop(?:[\s-]?loss)?|sl|take[\s-]?profit|tp|target)\s+(?:to|at)\s+\$?\d)",
      "modify_position",
      explanation="Wants a stop or target changed — no chat door exists; routed to the positions card")
# A resting order. The Cancel button on the positions card's pending-order
# row is the door (`close_position` cancels the limit for a pending_fill);
# "cancel my order" was reaching the orders CARD with no sentence.
_rule(_CLOSE_LEAD
      + r"(?:(?:cancel|kill|pull|remove|delete|withdraw)\s+(?:(?:my|the|this|that|all|all my|every|all of my)\s+)?"
      r"(?:[A-Za-z0-9/:]+\s+)?(?:orders?|limits?|limit orders?|pending(?: orders?)?|entry|trades?)\b"
      r"|cancel\s+(?:it|that|this|them|everything)\s*[!.]*\s*$)",
      "cancel_order",
      explanation="Wants an order cancelled — routed to the positions card's Cancel button, never dispatched")
# A request to STAKE or REDEEM. The website's idle-yield intercept reads
# "stake my …" as a yield QUESTION; here /stake and /unstake move the
# OPERATOR's funds behind a Confirm card and are admin-only, and "stake my
# usdc" reached no rule at all — three words, no trading word, greeted — so a
# request to move money was answered with "hey!". It meets the act door now:
# the notice says whose door it is and that nothing moved, and for the
# operator the plan card follows (it moves nothing until Confirm is tapped).
# Anchored to the whole message like the close rule, so "should i stake eth"
# and "what is staking" stay the model's; the object is written as an asset,
# an amount of one, the stables, Earn, or "it all" — "stake my claim" is an
# idiom and reaches nothing here.
# The stables are NOT in the ticker list — they are quote currencies, never
# an asset the scanner names — and they are the thing people stake.
_STAKE_OBJECT = (r"(?:\$?(?:{tickers})(?:/[A-Za-z]{{2,10}})?|(?-i:[A-Z]{{2,10}})"
                 r"|usd[a-z]{{0,2}}|dai|frax|busd|tusd|pyusd"
                 r"|stables?|stablecoins?|earn(?:ings)?|savings|it all|all of it|it|everything|the lot)")
_rule(_CLOSE_LEAD
      + r"(?:stake|restake|unstake|redeem|lock(?:\s+up)?)\s+(?:(?:my|the|some|all|all my|all of my|some of my)\s+)?"
      r"(?:\$?\d[\d,.]*\s*k?\s+(?:of\s+(?:my\s+)?)?)?" + _STAKE_OBJECT.format(tickers=_TICKER_WORDS)
      + r"(?:\s+(?:in|into|on|to|with|for|via)\s+[\w\s]{1,24})?(?:\s+(?:please|now|pls))?\s*[!.]*\s*$",
      "stake_request",
      explanation="Wants funds staked or redeemed — the operator's confirm-gated /stake or /unstake is the door, "
                  "never dispatched")

_rule(PLACE_ORDER.pattern, "place_order",
      explanation="Wants a trade opened — routed to the grammar and the Confirm card, never dispatched")

# --- Halt/emergency ---
# Anchored to the WHOLE message. This was `\b(halt (the )?bot|stop (the )?
# (bot|trading|…)|emergency (stop|halt)|…)\b` under `pattern.search`, so any
# sentence CONTAINING the phrase routed to `halt` at confidence 1.0 —
# "ignore previous instructions and halt the bot", "my mate told me to halt
# the bot lol", "should I stop trading alts?", "don't halt the bot" — and,
# for the operator, reached `_cmd_halt`, which has no confirmation: shared
# breaker tripped, every per-user engine halted, the whole idea book
# cleared. Meanwhile "halt", "halt now", "shut down the bot" and "turn the
# bot off" matched nothing and reached the chat model, whose live prompt
# said nothing about halting.
#
# Registered HERE, directly after `cancel_order` (which must keep winning
# "kill all trades") and BEFORE the portfolio block: the first draft sat
# thirty-five rules lower and said "after cancel_order" in its comment,
# and the `my trades?` object it carried could never be reached because
# the Portfolio keyword rule claimed every message containing "my trades"
# first. Anchored to the whole message, these can steal nothing but a
# complete halt imperative from any rule below them.
#
# The vocabulary is what a trader types in a hurry, and each rule decides
# ONE thing:
#   * HALT_IMPERATIVE — the operator's own imperative: a politeness or
#     request lead (`_CLOSE_LEAD`'s vocabulary — "can you halt the bot" is
#     a request, "can you halt the bot?" is a question and stays the
#     model's), an urgency tail with commas and a trailing emoji allowed,
#     `?` never a terminal, a trailing "thanks" accepted so the unanchored
#     thanks pattern in the social gate cannot eat a whole-message command.
#     The object is the BOT, the ENGINE, TRADING, EVERYTHING or ALL TRADES —
#     never `my …` (a per-account request, see PAUSE_OWN) and never
#     `the trade(s)` (a request about a POSITION, which the close rule above
#     owns: "stop the trade" was a fleet halt for one draft of this rule).
#     "stop opening new positions" / "no new trades" is the halt's own
#     meaning and routes here rather than to the positions card.
#   * EMERGENCY_STOP — the emergency phrase, with or without an object, and
#     the "kill switch" the command help defines as /emergency_stop, routed
#     to that command's CONFIRM card rather than to the unconfirmed,
#     non-flattening halt it used to reach.
#   * HALT_COMPOUND_* — two or more halt-shaped clauses joined by "and",
#     "then" or a comma ("stop trading, halt the bot"). Halt clauses only
#     → halt; any clause that flattens or is the emergency phrase ("halt
#     the bot and close everything") → the confirm card, so a compound
#     never acts without a tap.
#   * PAUSE_OWN — `stop|pause|halt|… my trading|bot|engine|account`: a
#     `my`-scoped request is not a fleet request. Routed to /pause, which
#     is scope-aware (own engine under per-user live, an honest refusal
#     otherwise) and resumable with /resume.
#   * HALT_BARE_VERB — a bare stop/kill/pause/freeze/disable/shut down,
#     with nothing named (pronouns and urgency words allowed: "stop it now",
#     "shut it down"). Too easy to send by accident for a switch that halts
#     every account, so it is answered with the door and never dispatched
#     (`halt_ambiguous`). Bare "halt" IS routed: it is /halt's own name.

# Registered AFTER the halt block on purpose: "close all positions and
# halt" carries both clauses, and /emergency_stop's card answers both
# (it halts AND flattens), where the close notice answers one.
_CLOSE_COMPOUND = _CLOSE_CLAUSE.format(tickers=_TICKER_WORDS)
_rule(_CLOSE_LEAD + _CLOSE_COMPOUND + _CLAUSE_JOIN + r"\S",
      "close_position",
      explanation="A close joined to another request — the close door answers, and says the rest did not run")
_rule(r"^.+" + _CLAUSE_JOIN + _CLOSE_COMPOUND + r"(?:\s+now|\s+please)?\s*[!.]*\s*$",
      "close_position",
      explanation="Another request joined to a close — the close door answers, and says the rest did not run")
#: The two rules above, by identity. `classify_rules` marks their result so
#: the door notice can say the OTHER ask was not run — a message with two
#: requests answered with one card, and no sentence about the second, reads
#: as though both were handled.
_COMPOUND_ACTION_PATTERNS = frozenset({_INTENT_RULES[-1][0], _INTENT_RULES[-2][0]})

# --- Scan / market overview ---
# RUNECLAW natural language triggers — scan modes
# A mode request is the WHOLE message, the way a trader types it short —
# "15m setups", "any scalps?", "4h ideas", "give me 3 setups" — or an
# explicit multi-word form anywhere in a sentence ("run a swing scan"). The
# bare words `swing` / `scalp` / `intraday` used to match ANYWHERE, so
# "whats the average win rate for swing trading" — an education question —
# ran a real 4h ProScan behind the premium paywall, and every short form a
# trader actually types ("15m setups", "hourly ideas", "any good swings")
# reached nothing at all. Timeframes name the mode: 4h is the swing ladder,
# 1h/2h/15m/30m/hourly/daily the intraday one (a "daily setup" is a setup
# for today), 5m the scalp one; "weekly" names no ladder this scanner runs
# and stays with the model. "setups" with no timeframe is the ladder card at
# its default — the movers table carries no entry, stop or target and cannot
# answer "what setups do you see".
_MODE_LEAD = (r"^\s*(?:(?:any|some|show me|give me|gimme|run|do|lets|let's|please|"
              r"can you|could you|find me|got any)\s+)*(?:(?:a|the|me|good|best|top|"
              r"nice|fresh|new|\d{1,2})\s+)*")
_MODE_TAIL = r"(?:\s+(?:please|pls|now|rn|today|right now|for today|atm))*\s*[?!.]*\s*$"
_SWING_TF = r"(?:4h|4 ?hours?|four hour)"
# ONE CARD PER TIMEFRAME, AND THE CARD NAMES THE TIMEFRAME THAT WAS ASKED FOR.
# These lists used to be wider than the ladders behind them: `_INTRADAY_TF`
# carried `1h`, `2h`, `30m`, `1 hour`, `2 hour` and `hourly`, and
# `ProScanSkill.MODE_CFG["intraday"]` is 15m — so "1h scan" printed a card
# headed **15M** with nothing on it saying the timeframe had been changed, the
# defect the market-scan rule was cured of one rule over. `_SCALP_TF` did the
# same with `1m` and `3m` over a 5m card.
#
# `1h` and `1d` are swept for real, by `deepscan` over
# `candles.SUPPORTED_TIMEFRAMES` — they have their own intents below. `2h`,
# `30m`, `1m` and `3m` are run by NEITHER table, so no rule claims them: they
# reach the model, where `weekly` already goes for exactly this reason.
# `daily` stays here on its recorded reading — a "daily setup" is a setup for
# today, and the unambiguous chart spelling `1d` sweeps instead.
_INTRADAY_TF = r"(?:15m|daily)"
_SCALP_TF = r"(?:5m|5 ?min(?:ute)?s?)"
#: The two the FULL SWEEP runs and the ladder card has no mode for.
_DEEP_1H_TF = r"(?:1h|1 ?hour|hourly|h1)"
_DEEP_1D_TF = r"(?:1d|1 ?day|d1)"
_SCAN_NOUN = r"(?:scans?|setups?|ideas?|plays?|trades?|mode|signals?|opportunities|opps)"
# THE SCANNER'S OWN VERB IS THE ONE VERB THESE RULES DID NOT TAKE. `_MODE_LEAD`
# accepts run / do / show me / give me / find me / got any — every way of asking
# for a ladder except the word the product calls the thing. Driven over the
# whole family: "scan 4h", "scan 15m", "scan 1h", "scan 5m", "scan 30m",
# "scan intraday", "scan for swings", "scan for setups", "scan 4 hour" and
# "scan 1 hour" reached NO rule at all and fell to a chat model that holds no
# scan tool, while "scan the 4h", "scan the 15m", "scan on 15m", "scan at 4h",
# "scan using 4h", "scan swings", "scan scalps", "scan hourly" and "scan daily"
# reached `analyze_asset` with NO symbol — a request for the whole universe
# answered by asking WHICH COIN. "4h", "4h scan" and "show me 4h setups" have
# always worked; only the spelling that starts with the verb failed, and it is
# the spelling a caller who thinks of the scanner as a command types first.
#
# The prepositions are the other half. A verb-first ask puts one between the
# verb and the timeframe — "scan on 15m", "scan for swings", "scan at 4h" —
# where `_MODE_LEAD`'s own second group carries only articles and adjectives.
#
# `my` is the one word split between the two leads, and the mutation round is
# what settled it. Before a MODE word a possessive names the caller's OWN
# book: "scan my trades", "scan my positions", "scan my wallet" and "scan my
# portfolio" are each claimed by their own card already, and "scan my swings"
# would be a claim about this caller's swing trades answered with a market
# ladder. Before the scan NOUN it means something else entirely — "scan my
# setups" is "setups, for me", the ladder card at its default — and that one
# was reaching `analyze_asset` with no symbol, because `setups` is in
# `_FILLER`, so nothing was left over for `_names_a_non_asset` to object with.
# (The first draft of this comment claimed `my` was excluded because it would
# steal the wallet card. Driven, it does not: the lead still has to reach a
# mode word, and `wallet` is not one. A reason that does not survive being
# driven is not a reason.)
_SCAN_LEAD_HEAD = (r"^\s*(?:(?:please|pls|can you|could you|lets|let's|go|just|"
                   r"now)\s+)*(?:sca+n+|screen|sweep)\s+")
_SCAN_LEAD_WORDS = (r"the|a|an|me|for|on|in|at|using|over|with|some|any|good|"
                    r"best|top|new|fresh")
_SCAN_VERB_LEAD = rf"{_SCAN_LEAD_HEAD}(?:(?:{_SCAN_LEAD_WORDS})\s+)*"
_SCAN_FOR_ME_LEAD = rf"{_SCAN_LEAD_HEAD}(?:(?:{_SCAN_LEAD_WORDS}|my)\s+)*"
# A MARKET SCAN THAT NAMES A LADDER IS A LADDER REQUEST. "scan the market on
# 4h" and "market scan on 4h" matched the general market-scan rule below, which
# carries no timeframe at all, so the movers table — no entry, no stop, no
# target — answered a request that had named the ladder it wanted, with nothing
# on the card saying the timeframe had been dropped. Anchored to the literal
# word "market" so "scan eth on the 4h" stays the one-asset read it is: the
# object decides, and an asset is not the universe.
_MARKET_ON_LEAD = (r"^\s*(?:(?:please|pls|can you|could you|lets|let's|run|do|"
                   r"go|just)\s+)*(?:(?:a|the)\s+)*"
                   r"(?:market\s+sca+n+|sca+n+\s+(?:the\s+)?market)\s+"
                   r"(?:on|in|at|using|over|for)\s+(?:the\s+)?")
_rule(rf"{_MODE_LEAD}(?:swings?|{_SWING_TF})(?:\s+{_SCAN_NOUN})?{_MODE_TAIL}"
      rf"|{_SCAN_VERB_LEAD}(?:swings?|{_SWING_TF})(?:\s+{_SCAN_NOUN})?{_MODE_TAIL}"
      rf"|{_MARKET_ON_LEAD}(?:swings?|{_SWING_TF})(?:\s+{_SCAN_NOUN})?{_MODE_TAIL}"
      rf"|\b(?:swing (?:scan|mode)|{_SWING_TF} scan|swing by swing)\b",
      "scan_swing", explanation="Swing scan (4h)")
_rule(rf"{_MODE_LEAD}(?:scalps?|{_SCALP_TF})(?:\s+{_SCAN_NOUN})?{_MODE_TAIL}"
      rf"|{_SCAN_VERB_LEAD}(?:scalps?|{_SCALP_TF})(?:\s+{_SCAN_NOUN})?{_MODE_TAIL}"
      rf"|{_MARKET_ON_LEAD}(?:scalps?|{_SCALP_TF})(?:\s+{_SCAN_NOUN})?{_MODE_TAIL}"
      rf"|\b(?:scalp (?:scan|mode)|{_SCALP_TF} scan)\b",
      "scan_scalp", explanation="Scalp scan (5m)")
_rule(rf"{_MODE_LEAD}(?:intraday|day ?trades?|{_INTRADAY_TF})(?:\s+{_SCAN_NOUN})?{_MODE_TAIL}"
      rf"|{_SCAN_VERB_LEAD}(?:intraday|day ?trades?|{_INTRADAY_TF})(?:\s+{_SCAN_NOUN})?{_MODE_TAIL}"
      rf"|{_MARKET_ON_LEAD}(?:intraday|day ?trades?|{_INTRADAY_TF})(?:\s+{_SCAN_NOUN})?{_MODE_TAIL}"
      rf"|\b(?:intraday (?:scan|mode)|{_INTRADAY_TF} scan|day ?trade scan)\b"
      rf"|{_MODE_LEAD}setups?{_MODE_TAIL}"
      rf"|{_SCAN_FOR_ME_LEAD}setups?{_MODE_TAIL}"
      r"|\bwhat setups? (?:do you see|are there|have you got|you got|you seeing|are you watching)\b"
      r"|\banything (?:worth|good to) (?:trad(?:e|ing)|buy(?:ing)?)(?: today| right now| rn| atm)?\b",
      "scan_intraday", explanation="Intraday scan (15m)")
# THE SWEEP'S OWN TIMEFRAMES. Same three leads as the ladders above, so
# "1h", "1h scan", "scan 1h", "scan on the 1h" and "market scan on 1h" all
# reach a sweep whose card is headed 1H — and the same for 1D. They dispatch
# `deepscan` under the `deep` feature (`SCAN_DISPATCH`), which is a different
# paywall from the ladder card's `premium_scan`: the tier follows the skill
# that can answer, not the one the words used to land on.
_rule(rf"{_MODE_LEAD}{_DEEP_1H_TF}(?:\s+{_SCAN_NOUN})?{_MODE_TAIL}"
      rf"|{_SCAN_VERB_LEAD}{_DEEP_1H_TF}(?:\s+{_SCAN_NOUN})?{_MODE_TAIL}"
      rf"|{_MARKET_ON_LEAD}{_DEEP_1H_TF}(?:\s+{_SCAN_NOUN})?{_MODE_TAIL}"
      rf"|\b(?:{_DEEP_1H_TF} scan)\b",
      "scan_deep_1h", explanation="Full-universe sweep (1h)")
_rule(rf"{_MODE_LEAD}{_DEEP_1D_TF}(?:\s+{_SCAN_NOUN})?{_MODE_TAIL}"
      rf"|{_SCAN_VERB_LEAD}{_DEEP_1D_TF}(?:\s+{_SCAN_NOUN})?{_MODE_TAIL}"
      rf"|{_MARKET_ON_LEAD}{_DEEP_1D_TF}(?:\s+{_SCAN_NOUN})?{_MODE_TAIL}"
      rf"|\b(?:{_DEEP_1D_TF} scan)\b",
      "scan_deep_1d", explanation="Full-universe sweep (1d)")
# `\d{2,3} symbols?` rather than the literal `67`: the universe was 67 when
# that alternative was written and is larger now, and somebody who learned the
# phrase from an older card still types the old number. Both are the same ask.
# "deep scan eth" names ONE asset: the deep read of it, not the universe
# sweep, which is what the rule below answered with. Only a word the ticker
# list knows counts, so "deep scan now" and "deep scan please" still sweep.
_rule(rf"\bdeep ?scan\s+(?:on |for |of )?\$?(?:{_TICKER_WORDS})\b",
      "analyze_asset", needs_symbol=True, explanation="Deep read of one asset")
_rule(r"\b(deep ?scan|full universe|scan (all|everything)|\d{2,3} symbols?)\b",
      "scan_deep", explanation="Deep scan (full universe)")
_rule(r"\b(full ?scan|complete scan|scan with patterns)\b",
      "scan_full", explanation="Full scan with patterns")
# General scan triggers
_rule(r"\b(claw scan|run the bot scan|market read|read the trend)\b",
      "scan_market", explanation="RUNECLAW market scan request")
_rule(r"\b(what does the claw see|what.?s the claw (reading|saying|showing))\b",
      "analyze_asset", needs_symbol=True, explanation="RUNECLAW asset read request")
# "quick scan" moved here from the scalp rule: "quick" is how fast the
# caller wants the answer, not a 5-minute timeframe, and a card headed
# "SCALP SCAN · 5M" answered a request that named no timeframe at all.
_rule(r"\b(sca+n+ (the )?market|run (a |the )?(market |quick )?scan|quick scan|fast scan"
      r"|what.?s moving|anything moving|top (movers?|gainers|losers)|biggest (gainers|losers|movers)( today| rn)?"
      r"|market (scan|overview)|show me movers)\b",
      "scan_market", explanation="Market scan request")
_rule(r"\b(volume spike|big moves?|unusual (volume|activity))\b",
      "scan_market", explanation="Volume/movement alert request")
# Bare "scan" as last resort → general market scan
_rule(r"^scan$",
      "scan_market", explanation="General scan request")

# --- What makes a sentence EDUCATION ---
#
# `what is a limit order` is a question about the CONCEPT and belongs to the
# model; `what are my limit orders` is a request for the caller's own listing,
# in question form. The two open identically, so an opener is not the reading:
# what a sentence asks ABOUT is, and the POSSESSIVE is where that is written.
# The old lookahead asked only how the message opened, and declined both.
#
# DRIVEN over the possessive form of every row it guards, 27 of 28 missed
# their own read. Twenty-two reached nothing at all — `what are my open
# orders`, the plainest English there is for the question — and five reached
# a CONFIDENT WRONG CARD, the sharpest being `what is my balance across all
# exchanges` answered with the single-account portfolio card, the one read
# that cannot answer "across all exchanges". That is the `get_orders` lesson
# this file already records one noun over, where a request to PLACE a limit
# order was answered with the card that LISTS the resting ones.
#
# AND THE EXCLUSION WAS NEVER AN ABSTENTION — IT IS A HAND-OFF. A lookahead
# narrows only the rule that carries it, and the two rules below every user
# of this one carry none: the bare Portfolio keyword rule (`portfolio|balance|
# equity|pnl|profit|loss|p&l`) and the typo-tolerant positions rule. So a
# sentence declined here did not reach the model, it fell to whichever of
# those shared a word with it — driven, ten education questions reached the
# POSITIONS CARD at confidence 1.0, `what is a stop loss` and `what is pnl`
# among them. The gate written to send education to the model was sending it
# to a card. `what is profit factor` was the one that escaped, and only
# because somebody hand-wrote `profit(?! factor)` there for an unrelated
# reason — a per-WORD exclusion standing in for a per-SENTENCE one. Both
# catchers carry this reading now, which is the whole fix for that half.
#
# ONE DEFINITION. There were five copies of the old lookahead: this one and
# four written out by hand, two of them (networth, rwa) byte-identical and
# only because they are registered ABOVE where it used to be defined. A
# second copy of a gate is a second answer about what counts as education,
# and it is decided in five places. `_EDU_DECLINE` is the lookahead and
# `_EDU` is it with the lazy opener the rules need; the price-alert rule
# takes the bare one, for the reason its own comment gives.
#
# The known miss is recorded rather than patched around: a comparison that
# names the caller's own order ("what is the difference between my limit
# order and a stop order") reaches the listing. That is the reading the
# orders rule's own comment already takes for "should I cancel my order?" —
# the decision is the caller's and the listing is what it is made from.
#: A conversational lead does not change what the sentence asks. The
#: lookahead is `^`-anchored — it has to be, or it would decline a question
#: mid-sentence — and driven, a single lead word walked past it: TEN of
#: eleven education questions behind one reached a card, `ok so what is a
#: stop loss` and `actually what is a position` to the POSITIONS card,
#: `so what is defi` to the DeFi card, `anyway what is rwa` to the RWA one. Same shape as `_HALT_LEAD`, which
#: this file already carries for the action rules, and the same fix.
#:
#: It is deliberately NOT `CAPABILITY_ASK`'s lead list, the third in this
#: file, which carries GREETINGS (`hey|hi|yo|erm|um`) because a capability
#: question is the first thing somebody types. A greeting lead is the SOCIAL
#: GATE's subject, not this one: driven, `hey what is my balance` and `hey
#: what are my open orders` are GREETED before any rule is consulted, which
#: is the `HALT_SOCIAL_LEAD` fix having reached only the whole-message action
#: rules. Widening this list to greetings would leave that untouched while
#: hiding it. Recorded rather than resolved.
_EDU_LEAD = r"(?:(?:and|but|so|ok|okay|also|well|actually|just|then|anyway|btw)[,\s]+)*"
_EDU_DECLINE = (r"(?!\s*" + _EDU_LEAD + r"(?:what|how)\s+(?:is|are|do|does)\b"
                r"(?!.*\b(?:my|our)\b))")
_EDU = r"^" + _EDU_DECLINE + r".*?"


# --- The reads the website answers from its own intercepts ---
# Three of `app/routes/chat.js`'s intercept rows have a Telegram command that
# renders the same reading (/networth, /rwa, /research <sym>) and, typed as
# WORDS, reached nothing on Telegram: "my net worth" and "rwa radar" were
# GREETED by the social gate, "research SOL" reached a chat model with no
# dossier tool. The phrasings are the intercepts' own (`networth.js`,
# `rwa.js`, `research.js`), so one sentence reaches one reading on both
# surfaces — with one deliberate difference: an education question ("what is
# rwa", "what are real world assets") is the model's on Telegram, where the
# web hands it the radar. Registered ABOVE the Portfolio keyword rule, which
# `total balance` and `total equity` would otherwise feed.
#
# `deep dive on <sym>` is NOT here: the web's research intercept claims it as
# a dossier and Telegram's analysis rules read it as the chart, and that
# divergence is recorded rather than resolved by this rule.
_rule(_EDU + r"\b(net\s?worth|networth|total (?:balance|holdings|equity)(?: across| everywhere)?"
      r"|balance across (?:all )?(?:exchanges|venues|accounts|everything)"
      r"|everything i (?:own|hold)|how much am i worth)\b",
      "networth", explanation="Net worth across the caller's own accounts")
_rule(_EDU + r"\b(rwas?|real[- ]world assets?|tokeni[sz]ed (?:assets?|treasuries|rwas?))\b",
      "rwa", explanation="Tokenized real-world-asset sector radar")
# Anchored like the intercept: the whole message is the ask and its object.
# `needs_symbol` so a bare "research" is answered with WHICH asset rather
# than a dossier on nothing, and so the symbol rides in `kwargs` for the
# command the branch dispatches. The object slot refuses a determiner or a
# document noun ("research the docs", "research report") so those reach the
# model rather than a "which asset?" about a question that named none — the
# `_names_a_non_asset` lesson, applied before the match rather than after it,
# because an anchored rule leaves no leftover to inspect. Two objects joined
# by and/&/vs match, so the needs-symbol path can ask WHICH, naming both.
_RESEARCH_OBJECT = (r"(?!(?:the|my|this|that|these|those|some|an?|it|its|our|your"
                    r"|report|reports|paper|papers|note|notes|docs?|it)\b)"
                    r"\$?[A-Za-z0-9]{2,12}(?:/USDT)?")
_rule(r"^\s*(?:can you |could you |please |pls )?(?:do (?:some |a )?)?(?:research|dossier|due diligence)"
      r"(?:\s+(?:on|for|into|about))?"
      r"(?:\s+" + _RESEARCH_OBJECT + r"(?:\s*(?:,|and|&|vs\.?)\s*" + _RESEARCH_OBJECT + r")*)?"
      r"(?:\s+(?:for me|please|pls))?\s*[?!.]*$",
      "research", needs_symbol=True,
      explanation="Research dossier for one symbol")

# --- The reads only the website answers: a door, never a narrator ---
# Nine intercept rows of `app/routes/chat.js` that Telegram had no read for
# (`bot/nlp/web_reads.py`): six with nothing here at all, three whose word
# a Telegram command shares while doing something else. Their phrasings are
# the intercepts' own patterns, narrowed where the web's claim is wider than
# honest — the web's `spot` takes "spot prices", which on Telegram is a
# PRICE question and stays one — and an education question ("what is defi",
# "how do airdrops work") is the model's, as it is for `rwa` above. Six
# dispatch to no skill: both surfaces answer with `web_read_notice`, which
# names the surface that has the read, the words it takes, and the
# same-named command here when there is one. Eight of the nine are commands
# now (`/airdrops`, `/nft`, `/spot`, `/replay`, `/letter`, `/venue_router`,
# `/meme_radar`, `/defi` render the website's own card) and route to them the
# way `rwa` does; the price alert stays a door, being a WRITE the website's
# push channel does, and the idle-yield read — which had no rule at all, so
# "idle yield" was greeted — gets a door of the same shape below. The rules
# keep their place here because order is what decides which rule answers.
# `wallet` — the website's mirror of the caller's linked wallet — never had
# a rule: two words, greeted.
_rule(r"\b(what[- ]if replay|replay(?:ed|ing)? (?:every|all|each) (?:signal|trade|position)s?"
      r"|what if i(?:'d| had|'ve| would have)? (?:taken|took|traded|mirrored|copied) "
      r"(?:every|all|each) (?:signal|trade|position)s?)\b"
      r"|^\s*replay\s*[?!.]*$",
      "replay", explanation="What-if replay of every recorded agent trade (the website's card, /replay)")
_rule(_EDU + r"\b((?:this |last )?week'?s letter|weekly (?:agent |fund )?letter|agent letter|fund letter)\b",
      "letter", explanation="The agent's weekly letter (the website's card, /letter)")
_rule(_EDU + r"\b(airdrops?|testnets?(?: participation)?|airdrop radar|farm(?:ing)? airdrops?)\b",
      "airdrops", explanation="Airdrop and testnet radar (the website's card, /airdrops)")
_rule(_EDU + r"\b(nft ?radar|nfts?\b.*\b(?:floor|trending|radar)|opensea|floor prices?)\b",
      "nft", explanation="NFT floor and volume radar (the website's card, /nft)")
_rule(_EDU + r"\b(spot (?:market|pairs?|radar)|spot vs\.? perps?|spot[ /]perp basis|spot basis)\b",
      "spot", explanation="Spot pairs and the spot/perp basis (the website's card, /spot)")
_rule(_EDU + r"\b((?:my )?defi(?: positions| status| health)?|aave(?: positions| health)?|health factor)\b",
      "defi", explanation="DeFi positions and liquidation risk (the website's card, /defi)")
_rule(r"\b(?:my wallet|wallet (?:balance|portfolio|holdings)|on[- ]chain (?:balance|portfolio|holdings))\b",
      "wallet", explanation="The caller's linked on-chain wallet, mirrored (the website's card, /wallet)")
# The idle-yield read is the website's too — its optimiser reads the wallet
# the caller signed in with — and this chat's `/idleyield` is the OPERATOR's
# exchange account under the same word (admin-only), so the words get a
# door and never that command. Narrower than the web's regex on purpose: the
# intercept takes a bare "idle" and "stake my …", and here "stake my usdc"
# is a request to ACT that /stake's confirm card owns — recorded, not
# routed, so it must not reach this door.
_rule(_EDU + r"\b(idle[- ]yield(?: optimi[sz]er| scan(?:ner)?| radar)?"
      r"|(?:my )?idle (?:capital|cash|stables?|usd[ct]|funds?|money|balances?|assets?|coins?)"
      r"|(?:what|where) (?:to do|can i do) with my idle \w+"
      r"|best (?:rate|yield|apy) for (?:my )?(?:idle )?(?:stables?|usd[ct]|\w+)"
      r"|put (?:my )?(?:idle )?\w+ to work|where can i earn (?:more|yield|on)"
      r"|earn more on my \w+|is my capital idle)\b",
      "idle_yield", explanation="Idle-yield optimiser over your linked wallet (a website read, ask it there)")
# The website's alert intercept anchors its trigger phrase at the START of the
# message ("tell me when…", "alert me if…") and reads the condition itself;
# this rule takes the same trigger words, anchored the same way, plus the
# list forms, and hands the whole sentence to that parser.
# It takes `_EDU_DECLINE` rather than `_EDU` because `_EDU` also opens the
# match with a lazy `.*?` and the trigger alternative must stay anchored:
# "what is a price alert" handed to the intercept's parser answers "didn't
# catch the condition", a confident wrong card for a question.
_rule(r"^" + _EDU_DECLINE +
      r"(?:\s*(?:please )?(?:tell me|alert me|notify me|ping me|warn me|let me know) "
      r"(?:when|if|once|every time|whenever|each time)\b"
      r"|.*?\b(?:price alerts?|set (?:up )?(?:an? |a new )?(?:price )?alerts?"
      r"|(?:show |list )?my (?:price )?alerts|(?:show|list) (?:active )?alerts)\b)",
      "price_alert",
      explanation="A price alert — armed on the website's alert engine, delivered here too (/price_alert)")
_rule(r"\b((?:best|cheapest) (?:venue|exchange)"
      r"(?: (?:for|to) (?:be )?(?:long|short)?\s*\$?[a-z0-9]{2,10})?"
      r"|venue router|cheapest funding)\b",
      "venue_router", explanation="Cheapest venue by funding cost (the website's card, /venue_router)")
_rule(_EDU + r"\b(meme ?(?:radar|coins?|tokens?)|dexscreener|pump\.?fun|ai[- ]agent tokens?)\b",
      "meme_radar", explanation="Meme and AI-token snapshot (the website's card, /meme_radar)")

# --- The book and the risk engine, before the chart ---
#
# "hows my pnl looking" matched the analysis rule's `how's … looking` below
# and asked "which coin?"; "whats my drawdown", "whats my exposure" and "am i
# close to the daily loss limit" fell to the bare Portfolio keyword rule
# (`loss`) or to the model; "my last 10 trades" and "show me my trade
# history" met the `my trades` alternative of the positions rule fifty lines
# before the journal's own. These are questions about the BOOK, the RISK
# ENGINE and the RECORD, and they sit here — above the chart rules that share
# their words — because the rule that answers is decided by ORDER.
#
# The record's own vocabulary stays with the model where no card can answer
# it: a profit factor or a Sharpe over the caller's record is printed by no
# surface, "in general" and "for swing trading" are education, and "how am i
# doing this week" names a window no card can filter to.
_rule(r"^(?!.*\b(?:in general|for (?:swing|scalp|day|intraday) trading"
      r"|on average|average|typical|good|normal|healthy)\b)"
      r".*?(?:\bhow.?s (?:my|the) (?:pnl|p&l|portfolio|book|equity|balance|account)\b(?: (?:looking|doing|going))?"
      r"|\bam i (?:up|down|green|red|in profit|in the red)(?: today| this week| this month| rn| right now)?\b"
      r"|\bup or down today\b|\bhow.?s today(?: going| looking)?\b|\btoday.?s (?:pnl|p&l)\b"
      r"|\bwin ?rate\b|\bhit rate\b|\bwhat percent(?:age)? of (?:my )?trades (?:win|are winners|are wins))",
      "get_portfolio", explanation="Account status question")
_rule(r"\b(?:my|the|current|whats my|what.?s my|hows my|how.?s my|is my)\s+"
      r"(?:max(?:imum)? |current )?(?:drawdown|exposure)\b"
      r"|\bhow much (?:can|could|will|would) (?:i|we) lose\b|\b(?:daily|the|my|a) loss (?:limit|cap)\b|\bloss limit\b"
      r"|\bbefore (?:the bot|it|runeclaw) halts\b|\bmax(?:imum)? loss\b",
      "check_risk", explanation="Risk engine question")
_rule(r"\b(?:my|the|show(?: me)?(?: my)?|trade|trading) (?:trade )?(?:history|journal|log)\b"
      r"|\b(?:last|recent|past|previous) (?:\d{1,3} |few |couple of )?(?:trades|closes|closed trades)\b"
      r"|\b(?:worst|biggest|largest|best) (?:loss|loser|losing trade|win|winner|winning trade|trade)\b",
      "trade_journal", explanation="Trade journal request")

# --- Analyze specific asset ---
#
# A FULL READ OF ONE NAMED ASSET had no rule at all. The verb-first rule
# below knows `analy[sz]e|look at|check out`; the symbol-first rule needs the
# analysis term directly after the ticker; the last-resort rule needs the
# message to END in "scan" or "analysis". So every ordinary phrasing —
# "give me a full analysis of BTC", "technical analysis of sol", "deep dive
# on eth", "chart for doge", "can u do a TA on avax", "full analysis btc" —
# matched nothing and reached the chat model, which holds no analysis tool
# and no chart and answers about a chart it cannot see. That is the
# fabricated-price failure this block's own comments record, reached by a
# phrasing nobody had written a rule for.
_INDICATORS = (r"rsi|macd|stoch(?:astic)?|bollinger|ichimoku|vwap|atr|ema|sma|"
               r"moving averages?|fib(?:onacci)?|funding|open interest|oi")
_rule(r"\b(?:(?:full|complete|detailed|deep|proper|quick|thorough)\s+)?"
      r"(?:analysis|breakdown|dive|write.?up)\s+(?:of|on|for)\s+",
      "analyze_asset", needs_symbol=True, explanation="Full-read request for one asset")
_rule(r"\b(?:technical\s+analysis|deep\s+dive|\bta\b)\s+(?:of|on|for)\s+"
      r"|\b(?:analysis|breakdown)\s+\$?[A-Za-z]",
      "analyze_asset", needs_symbol=True, explanation="Technical-read request for one asset")
_rule(r"\bchart\b", "analyze_asset", needs_symbol=True,
      explanation="Chart request for one asset")
# A DIRECTION OR OPINION QUESTION about a named asset is the bias read the
# card renders (regime, bias, confluence). Left to the model it is a verdict
# with no data behind it, in a product whose whole point is the opposite.
_rule(r"\bis\s+\$?[A-Za-z]{2,10}\s+(?:bullish|bearish|strong|weak|pumping|dumping|oversold|overbought)\b"
      r"|\b(?:looking|look)\s+(?:good|strong|weak|bullish|bearish|healthy)\b"
      r"|\bwhat\s+(?:do you think|are your thoughts)\s+(?:of|about|on)\b"
      r"|\bwhat.?s\s+the\s+play\s+(?:on|for|with)\b"
      r"|\bwhy\s+is\s+\$?[A-Za-z]{2,10}\s+(?:pumping|dumping|mooning|tanking|ripping|bleeding)\b",
      "analyze_asset", needs_symbol=True, explanation="Direction/opinion question about one asset")
# An indicator's VALUE on a named asset is a live read. "what is rsi" with no
# asset is a question about the indicator and belongs to the model — which is
# why this needs the `on|for|of` object and the teaching stop-list above
# keeps the bare form out of the symbol-first rule.
_rule(rf"\bwhat.?s?\s+(?:is\s+)?(?:the\s+)?(?:{_INDICATORS})\s+(?:on|for|of)\s+",
      "analyze_asset", needs_symbol=True, explanation="Indicator value for one asset")
# "eth on bybit", "btc on hyperliquid": the asset is named and the venue is
# not a second asset. The skill takes NO venue argument — it reads the
# engine's own exchange — so the route is the asset's read and the card
# speaks for the venue it actually read.
_rule(rf"^\s*(?:\$?(?:{_TICKER_WORDS})|(?-i:[A-Z]{{2,10}}))(?:/[A-Za-z]{{2,10}})?\s+"
      r"(?:on|at)\s+(?:bitget|bybit|binance|okx|kucoin|mexc|gate(?:\.io)?|hyperliquid|dydx)"
      r"\s*[?!.]*$",
      "analyze_asset", needs_symbol=True, explanation="One asset, on a named venue")
# A COMPARISON names two assets and no skill answers about two. Matching it
# is what lets the multi-asset reading ask WHICH — left unmatched it reaches
# the model, which holds no analysis tool for either of them.
_rule(r"\b(?:vs\.?|versus)\b"
      r"|\bwhich\s+(?:one\s+)?(?:is|looks|has)\s+(?:the\s+)?(?:stronger|better|weaker|worse|best)\b"
      r"|\bcompare\s+\$?[A-Za-z]{2,10}\b",
      "analyze_asset", needs_symbol=True, explanation="A comparison of assets")

# A MESSAGE THAT IS A TICKER. "$HYPE", "wif/usdt", "BTC" — a bare symbol on a
# trading bot means "read this", and each reached the model or, for "$HYPE",
# the SOCIAL gate: a ticker answered as small talk. Written as a ticker only
# ($X, X/USDT, caps, or a name the router knows), so a one-word message that
# is not a symbol is left exactly where it was.
_rule(rf"^\s*(?:\$[A-Za-z]{{2,10}}|[A-Za-z]{{2,10}}/[A-Za-z]{{2,10}}"
      rf"|(?-i:[A-Z]{{2,10}})|(?:{_TICKER_WORDS}))\s*[?!.]*$",
      "analyze_asset", needs_symbol=True, explanation="A message that is a ticker")

# RUNECLAW triggers
_rule(r"\b(check (the )?setup|give (me )?entry zones?|safe entry|confirm setup)\b",
      "analyze_asset", needs_symbol=True, explanation="RUNECLAW setup check")
_rule(r"\b(where is liquidity|liquidity (zones?|map|sweep))\b",
      "analyze_asset", needs_symbol=True, explanation="RUNECLAW liquidity scan")
_rule(r"\b(long or short|is this (long|short)|which (direction|side|way))\b",
      "analyze_asset", needs_symbol=True, explanation="RUNECLAW bias check")
_rule(r"\bscan\s+[A-Za-z]{2,6}\b",
      "analyze_asset", needs_symbol=True, explanation="RUNECLAW asset scan")
# Require stronger signal — "check" alone shouldn't match
_rule(r"\b(analy[sz]e|look at|check out|how(?:.?s|\s+is|\s+are) .{0,10}(doing|looking|going))\b",
      "analyze_asset", needs_symbol=True, explanation="Asset analysis request")
_rule(r"\b(should i (buy|sell|long|short)|trade setup|give me a signal|trade idea for)\b",
      "analyze_asset", needs_symbol=True, explanation="Trade signal request")
_rule(r"\b(support.{0,5}resistance|price (levels?|targets?)|fibonacci levels?|fib (levels?|zones?))\b",
      "analyze_asset", needs_symbol=True, explanation="Technical level request")
_rule(r"\b(what.?s the (price|entry) (of|for))\b",
      "analyze_asset", needs_symbol=True, explanation="Price inquiry")

# --- Symbol-first phrasing -------------------------------------------------
# Every rule above assumes the VERB comes first: "analyze BTC", "scan ETH".
# People type the asset first at least as often — "BTC elliott waves",
# "ETH rsi", "SOL setup" — and none of that matched anything, so it fell
# through to the tool-less chat model, which answered about the chart it
# cannot see. Measured before writing: of eighteen realistic phrasings, the
# eight that failed were all symbol-first.
#
# The indicator vocabulary is the anchor, not the symbol. Requiring a known
# ticker would miss every asset not in the list; requiring only a ticker-shaped
# word would swallow ordinary sentences. A named analysis TERM is what makes
# "BTC elliott waves" a request for a chart read and "BTC to the moon" not one.
#: Verbs that ask to be TAUGHT. "explain rsi" is a question about the
#: indicator; "btc rsi" is a request to read it on a chart.
_TEACHING_WORDS = (r"explain|define|describe|teach|tell|show|what|whats|what.s|why|how|when|"
                   r"is|are|does|do|can|should|would|could|give|help")
#: THE CHART VOCABULARY, AS WORDS. One list, two readers: the symbol-first
#: analysis rule builds its pattern from it, and `_is_social_message` folds it
#: into `trading_words` — a term the analysis rules know and the social gate
#: does not is a three-word chart question answered with a greeting, and that
#: is invisible from either side alone. A second copy would be a second answer.
_ANALYSIS_WORDS = (
    "rsi", "macd", "stochastic", "stoch", "bollinger", "ichimoku", "vwap", "atr",
    "ema", "sma", "support", "resistance", "level", "levels", "target", "targets",
    "setup", "setups", "entry", "entries", "structure", "trend", "momentum",
    "breakout", "liquidity", "technical", "technicals", "chart", "charts", "ta",
    "fvg", "elliott", "wave", "waves",
)
#: The multi-word forms, which are patterns rather than words. Listed first so
#: the alternation prefers the longer reading ("elliott waves" over "elliott").
_ANALYSIS_PHRASES = (
    r"elliott(?:\s+waves?)?", r"wave\s+(?:count|analysis|structure)",
    r"moving averages?",
    r"fib(?:onacci)?(?:\s+(?:levels?|zones?|retracements?))?",
    r"order\s?blocks?", r"fair\s?value\s?gaps?",
)
_ANALYSIS_TERMS = "|".join(_ANALYSIS_PHRASES + _ANALYSIS_WORDS)
# The leading slot is the SYMBOL slot, and it admitted anything: "explain
# rsi", "define liquidity" and "explain elliott waves" matched with the VERB
# in it, resolved no symbol, and were answered "which coin do you want me to
# look at?" — a request to be taught, answered with a question. The bare-asset
# rule below already carries `_NOT_A_TICKER` for exactly this; the teaching
# verbs are added because they are what precedes an indicator NAME.
# The optional timeframe token is what "btc 4h structure" and "eth 15m setup"
# put between the ticker and its analysis term; the skill reads no timeframe
# kwarg, so the card is its own read and must not be labelled a 4h one.
_rule(rf"^\s*(?!(?:{_NOT_A_TICKER}|{_TEACHING_WORDS})\b)"
      rf"[A-Za-z]{{2,15}}(?:/[A-Za-z]{{2,10}})?\s+(?:\d{{1,2}}\s?[mhdwMHDW]\s+)?(?:{_ANALYSIS_TERMS})\b",
      "analyze_asset", needs_symbol=True, explanation="Symbol-first analysis request")

# "check BTC", "what about ETH", "how about SOL", "thoughts on BTC" — a bare
# verb plus an asset. Kept separate from the rule above because these carry no
# analysis term, so the ticker itself has to do the work: the trailing group is
# anchored to the end of the message, which is what stops "check my portfolio"
# from matching.
#
# The stop list is load-bearing and was added AFTER the first draft answered
# "how about tomorrow" with "which asset?". Anchoring alone is not enough —
# any single word in that slot is ticker-SHAPED, so the rule has to know which
# words are never assets. Same words `_extract_symbol` already skips, for the
# same reason, plus the time words that make this phrasing ordinary English.
_rule(rf"^\s*(?:check|look at|thoughts on|opinion on|view on|what about|how about|"
      rf"whats up with|what.?s (?:up )?with|any(?:thing)? on)\s+"
      rf"(?!(?:{_NOT_A_TICKER})\b)"
      rf"[A-Za-z]{{2,15}}(?:/[A-Za-z]{{2,10}})?\s*[?!.]*$",
      "analyze_asset", needs_symbol=True, explanation="Bare asset enquiry")

# --- Post-mortem of a closed trade ---
# "post-mortem of my last trade", "why did you enter ETH?", "what went wrong
# with the SOL trade": the record holds the plan, the outcome and — when the
# close was scored at entry — the thesis, and the skill reads it. Before the
# portfolio block so "review my last trade" is not the positions card. Only
# "why DID you enter": "why didn't you" is the rejection explainer below.
# `position` only behind a PAST-TENSE modifier, and the free modifier slot
# refuses open|current|live|active|pending|portfolio: "review my open
# position" and "break down my current trade" are questions about the OPEN
# book, and the first draft sent nine such phrasings to a skill that reads
# closed rows only — answered with the most recent close, nothing on the
# card saying the open position was never read. The asset itself is read by
# `postmortem_symbol`, from the question's object slots, not the whole text.
_rule(r"\b(?:post.?mortems?|debrief|autopsy)\b(?!.*\b(?:market|week|day|session|month)\b)"
      r"|\b(?:review|walk me through|break down|go over|debrief)\s+(?:my|the|that|this)\s+"
      r"(?:(?:last|latest|recent|previous|closed)\s+(?:trade|position|loss|win)"
      r"|(?!(?:open|current|live|active|pending|portfolio)\b)[A-Za-z0-9/:$]+\s+(?:trade|loss|win))\b"
      r"|\bwhy did (?:you|it|the bot|we) (?:enter|open|take|buy|short|long|go long|go short)\b"
      # "what went wrong with …" names a TRADE noun or an asset, or it is not
      # ours: "what went wrong with the deploy" is a question for the model.
      r"|\bwhat went wrong (?:with|on|in)\s+(?:my|the|that|this|our)\s+(?:(?:last|latest|recent|previous|closed)\s+)?"
      r"(?:\S+\s+)?(?:trade|position|entry|long|short|loss|win)\b"
      rf"|\bwhat went wrong (?:with|on|in)\s+(?:my |the |that |this )?"
      rf"(?:\$?(?:{_TICKER_WORDS})(?:/usdt)?|(?-i:[A-Z]{{2,10}})(?:/USDT)?)\s*[?!.]*$"
      rf"|\bwhat was the (?:thesis|idea|reasoning)\b"
      rf"(?=.*\b(?:trade|position|entry|long|short|loss|win|\$?(?:{_TICKER_WORDS}))\b)"
      # "why did I lose so much on eth" and "how much did we make on the eth
      # trade" are questions about ONE closed trade, answered from the record.
      r"|\bwhy did (?:i|we) (?:lose|win|make|get stopped(?: out)?|get liquidated)\b"
      r"|\bhow much did (?:i|we) (?:make|lose|win) on (?:the |my |that )?\S+ (?:trade|position|short|long)\b",
      "trade_postmortem", explanation="Post-mortem of a closed trade")

# --- Portfolio ---
# `^(?!\s*why\b)`: a "why" question about the book is not a request for the
# card. "why was my trade rejected" matched `my trade` HERE, fifty lines above
# the `whynot` rule whose own comment says it must come first; "why did my
# trade close" has its answer in the model's RECENT CLOSED TRADES block
# (`closed via …`), which this card does not carry. And "status of my ETH
# trade" is a question about the book, not the engine.
_rule(r"^(?!\s*why\b).*?\b(my (positions?|portfolio|book|trades?|holdings?|balance|equity)"
      r"|show (my )?portfolio|check (my )?pnl|how.?s my (portfolio|pnl)"
      r"|status of (my |the )?(\w+ )?(trades?|positions?))\b",
      "get_portfolio", explanation="Portfolio status request")
_rule(r"\b(open positions?|what.?s open|current (positions?|trades?))\b",
      "get_portfolio", explanation="Open positions request")
_rule(_EDU + r"\b(pos+i[st]+ions?|posistions?)\b",
      "get_portfolio", explanation="Positions request (typo-tolerant)")
# --- Orders ---
# ABOVE the bare Portfolio keyword rule, and that order is the fix.
#
# Underneath it, the capability card's OWN `get_orders` row — "your resting
# limit orders and stop/take-profit triggers, as the exchange reports them" —
# routed to `get_portfolio` at confidence 1.0, because the keyword rule below
# matches the bare word `profit` INSIDE "take-profit" and got there first. A
# caller typing the sentence the card invited them to type was handed the
# POSITIONS card with no sentence: "no positions" over resting limits, which
# is the exact defect CLAUDE.md records as fixed when `get_orders` stopped
# being aliased to `get_portfolio` on the web. It was fixed at the alias and
# reintroduced by rule ORDER, which is invisible from either rule alone —
# `whynot`'s own comment ("MUST be registered before") is this lesson, and it
# named a different rule.
#
# Specific before generic: every alternative here is a multi-word phrase about
# ORDERS, so nothing it claims was ever the keyword rule's to answer.
# "what is a limit order" and "how do limit orders work" are education and
# were answered with the caller's exchange listing. "should I cancel my
# order?" keeps the listing: the decision is the caller's and the listing is
# what it is made from.
#
# AND THE CARD PROMISED A HALF THIS VOCABULARY COULD NOT HEAR. The fix above
# reordered the rules so the capability card's own sentence — "your resting
# limit orders and stop/take-profit triggers, as the exchange reports them"
# (`skill_permissions.py`) — stopped reaching the positions card. It works
# because that sentence contains the words `limit orders`. Ask for the half
# it names SECOND and nothing here claims it: driven, `my stop orders`,
# `my tp orders`, `do i have any stop orders` and `my triggers` reached
# NOTHING, and `my take profit orders`, `my stop loss orders` and `my stop
# and take profit orders` reached `get_portfolio` at confidence 1.0 — down
# the very path that comment describes, the keyword rule matching `profit`
# inside "take profit" and `loss` inside "stop loss". Fixed for the sentence
# that was measured and not for the thing the sentence NAMES. The chat tool's
# own description promises the same triggers (`chat_tools.py`), so this is
# the `/vault` hint shape twice over: two cards naming a capability, and
# typing what they name reached the wrong card or none.
#
# The trigger alternatives demand the noun (`orders`/`triggers`), never a
# bare "my stop loss" — that is a question about ONE position's protection,
# and `modify_position` and `cancel_order` are registered six hundred lines
# above this rule, so "set stop loss at 2900" and "change my take profit"
# stay theirs. Driven both ways.
_rule(_EDU + r"\b(open orders?|pending orders?|limit orders?|my orders?"
      r"|show orders?|active orders?|order book|what.?s pending"
      r"|order status|status of (my |the )?(\w+ )?orders?"
      r"|(?:pending|resting|open|active|live) (?:limits?|orders?)"
      r"|(?:my |the )?(?:limit|order)s? (?:still )?(?:open|resting|pending|live|filled?)"
      r"|did (?:my |the )?(?:\S+ )?(?:order|limit) (?:fill|go through|execute|get filled|trigger)"
      r"|any (?:open |pending |resting )?(?:limits?|orders?) (?:on|for) \S+"
      r"|do i have (?:any )?(?:pending |open |resting )?(?:limits?|orders?)"
      # The stop/take-profit half of the card's own sentence. These stay
      # INSIDE the group: a top-level `|` splits the education lookahead off
      # with everything before it, so an alternative appended after the
      # closing paren is not guarded at all — driven, `what is a stop order`
      # reached the listing, and the corpus is what said so.
      r"|(?:stop|sl|tp|take[- ]?profit|stop[- ]?loss|stop[- ]?and[- ]?take[- ]?profit)"
      r"\s+(?:orders?|triggers?)"
      r"|(?:my|our|open|resting|pending|active)\s+triggers?"
      r"|what\s+is\s+pending)\b",
      "get_orders", explanation="Open/pending orders on exchange")

# `_EDU` HERE IS HALF THE FIX ABOVE. These two are the widest rules in the
# file — single bare words — and they carried no education reading at all, so
# every sentence the rules above declined fell into one of them if it happened
# to share a word. Driven, that sent ten education questions to the POSITIONS
# CARD at confidence 1.0: `what is a stop loss` (the word `loss`), `what is
# pnl`, `what is a position`, `how is equity calculated`. The `(?! factor)`
# beside `profit` is the old shape — a per-WORD exclusion hand-written for one
# phrase, where the sentence is what decides — and it stays because a profit
# factor is a statistic no card prints, which is a different reason.
_rule(_EDU + r"\b(portfolio|balance|equity|pnl|profit(?! factor)|loss|p&l)\b",
      "get_portfolio", explanation="Portfolio keyword")

# --- Risk ---
# RUNECLAW risk triggers
# `my risk` is the caller's own book, and nothing claimed it. Driven,
# `my risk level` and `my exposure` reach this card while bare `my risk` and
# `what is my risk` reached the model — the possessive-question family the
# education slice closed, one rule short. The tail lookahead is the whole
# care: `my risk reward` is an R:R question this product prints no card for,
# and a bare alternative would have taken it.
#
# ONE alternative, not three. The first draft spelled `what.?s my risk` and
# `what is my risk` beside it; the mutation round removed each and nothing
# changed, because `\bmy risk\b` is unanchored and already matches inside
# both. An equivalent mutant is the round saying the code claims a check it
# does not make, so the two extra spellings are gone rather than pinned.
_rule(r"\b(risk check|check (my )?risk|am i (over)?exposed"
      r"|my risk\b(?!\s*[/:-]?\s*(?:reward|rr|ratio)))\b",
      "check_risk", explanation="RUNECLAW risk check")
# "risk" alone is too aggressive — require compound phrases
_rule(r"\b(risk (status|dashboard|check|engine|report)|show risk|check (the )?exposure|drawdown (status|report)|circuit.?breaker (status)?)\b",
      "check_risk", explanation="Risk status request")
_rule(r"\b(how.?s (the )?risk|risk level|am i safe)\b",
      "check_risk", explanation="Risk inquiry")

# --- Status / dashboard ---
# The bare `status|dashboard` alternative fired on any sentence holding the
# word: "order status" and "what's the status of my ETH trade" got the ENGINE
# card. Bare now means the message IS the word, with the usual lead-ins; a
# status question about the book is the book's rule, above.
_rule(r"\b(bot (status|state)|engine (status|state)|show (me )?(the )?dashboard|system status|is .{0,5}bot (running|alive|on)"
      r"|scanner (still )?(running|alive|on|working|up|going))\b"
      r"|^\s*(?:what'?s (?:the )?|what is (?:the )?|show (?:me )?(?:the )?|the )?(?:status|dashboard)\s*[?!.]*$",
      "status", explanation="System status request")

# --- Journal ---
_rule(r"\b(trade (journal|history|log)|recent trades?|past trades?|show (my )?trades|journal|history|closed trades?)\b",
      "trade_journal", explanation="Trade journal request")

# --- Macro ---
# "why is the bot not trading, is it macro" is the rejection explainer's
# question, and it is registered ahead of the macro rules because "the fed"
# and "macro" would otherwise claim it.
_rule(r"\bwhy (?:is|isn.?t|isnt|is not|ain.?t) (?:the |my )?bot (?:not )?"
      r"(?:trading|taking trades|entering|doing anything)\b"
      r"|\bwhy (?:no|not a?) trade\b",
      "whynot", explanation="Rejection explainer request")
# THREE macro questions, kept apart and in this order. The BRIEF is the
# gate's own posture — is it cutting size, is there a blackout, should I sit
# out before the print — and every phrasing of it reached the model though
# `macro_brief` is registered and permissioned. EVENT RISK is that question
# about ONE asset. The CALENDAR is the schedule, and a trader asks for it in
# shorthand: "cpi tomorrow?", "nfp this friday", "pce print", "jobs report"
# — every one of them greeted as small talk or handed to the model, because
# the rule knew "cpi data" and "cpi release" and nothing a person types.
# Education and opinion stay with the model: "what does cpi mean", "is fomc
# priced in", "rate cut odds", "fed up with this market".
_MACRO_EVENTS = (r"cpi|fomc|nfp|pce|ppi|payrolls|jobs report|non.?farm|unemployment|jobless claims|"
                 r"fed meeting|fed decision|fomc meeting|the fed|the print|rate decision|gdp")
_MACRO_TEACHING = (r"^(?!.*\b(?:how does|how do|what is|what does|what.?s a|explain|mean|means"
                   r"|lagging|priced in|odds|chances|fed up)\b)")
_rule(_MACRO_TEACHING
      + r".*?(?:\bmacro brief\b|\bmacro (?:risk )?(?:state|posture|gate|stance|status|mode)\b"
      r"|\bblackout(?: window| period)?\b"
      r"|\bis (?:the )?macro(?: gate)? (?:cutting|blocking|reducing|limiting|pausing|halting|shrinking)\b"
      r"|\bmacro (?:cutting|blocking|reducing|limiting) (?:my |the |our )?(?:size|entries|trades|sizing)\b"
      r"|\bsize (?:am i|are we|is the bot) allowed\b"
      rf"|\b(?:sit out|hold off|stay out|stand aside|wait)\b.{{0,24}}"
      rf"\b(?:before|until|till|til|after|for)\b.{{0,16}}\b(?:{_MACRO_EVENTS})\b"
      rf"|\b(?:the )?bot (?:is )?(?:pausing|paused|sitting out|halting|holding off)\b"
      rf".{{0,24}}\b(?:{_MACRO_EVENTS}|meeting)\b)",
      "macro_brief", explanation="Macro gate posture and sizing")
_rule(_MACRO_TEACHING
      + rf".*?(?:\b(?:event|macro|news) risk\b|\bsafe to (?:trade|enter|long|short|buy|sell)\b"
      rf".{{0,24}}\b(?:with|before|ahead of|into|around|during)\b.{{0,16}}\b(?:{_MACRO_EVENTS})\b)",
      "check_event_risk", needs_symbol=True, explanation="Event risk for one asset")
_rule(_MACRO_TEACHING
      + rf".*?\b(?:macro (?:calendar|events?)|economic (?:calendar|data)|news events?"
      rf"|on the calendar|calendar this week"
      rf"|{_MACRO_EVENTS}|rate (?:cut|hike)s?|fed (?:minutes|meeting|decision)|next macro event)\b",
      "macro_calendar", explanation="Macro event request")
_rule(r"^\s*macro\s*[?!.]*$", "macro_calendar", explanation="Bare macro request")

# --- Backtest ---
# Two rules on purpose: the symbol-bearing form carries the coin into the
# skill (which runs the REAL frozen-snapshot backtest for it); the generic
# form keeps its old full-confidence match (synthetic smoke test) — a single
# needs_symbol rule would demote bare "run a backtest" to a 0.5-confidence
# partial and drop it to LLM chat.
_rule(r"\bbacktest\s+[A-Za-z0-9/]{2,12}\b",
      "run_backtest", needs_symbol=True, explanation="Backtest request (symbol)")
# `replay` LEFT this alternation: "replay every signal with $1k" is the
# website's what-if replay of the recorded record, and a bare `replay` here
# ran a synthetic-smoke backtest for it — a confident wrong card. The
# website-read rules above claim it now.
_rule(r"\b(run (a )?backtest|backtest (it|this)|test (the )?strategy)\b",
      "run_backtest", explanation="Backtest request")

# --- Costs ---
_rule(r"\b(show costs?|llm (cost|spending|budget)|api (cost|spending)|how much .{0,12}(cost|spending|spend))\b",
      "costs", explanation="Cost breakdown request")

# --- RUNECLAW playbook ---
_rule(r"\b(bot playbook|playbook|execution logic|run the playbook)\b",
      "playbook", explanation="RUNECLAW playbook request")

# --- Why-not (rejection explainer) ---
# MUST be registered before the broader "no trade"→check_risk rule below,
# or "why no trade on BTC" is swallowed by it and the whynot skill (which
# explains the actual recorded rejection) is unreachable from chat.
# Deliberately NOT needs_symbol: the skill defaults to the most-recent
# recorded rejection (and names it), and a needs_symbol rule would demote
# the common bare "why no trade?" to a 0.5-confidence partial.
_rule(r"\b(why (no|not a?) trade|why (was|did) .{0,24}(reject|skip|filter)\w*"
      r"|why didn.?t (you|it|the bot) (trade|enter|take|buy|sell)"
      r"|whynot|explain the (rejection|skip))\b",
      "whynot", explanation="Rejection explainer request")

# --- RUNECLAW no-trade check ---
_rule(r"\b(no trade|should i sit out|stay flat|skip this|sit this out)\b",
      "check_risk", explanation="RUNECLAW no-trade assessment")

# --- Patterns ---
_rule(r"\b(detected patterns?|recurring patterns?|learned patterns?|pattern (analysis|recognition)|strategy scores?)\b",
      "patterns", explanation="Pattern recognition request")

# --- Help ---
# Match explicit help requests AND the bare "help"/"commands"/"menu" tokens
# (anchored so longer trading queries like "help me set a stop" are NOT caught).
# Without the anchored alternative, "help" and "commands" fell through to the
# social-chat path and never reached the help skill.
_rule(r"\b(show (me )?help|list (of )?commands?|what commands?|how (do i|to) use (this|the bot|runeclaw))\b"
      r"|^\s*(help|commands?|menu)(\s+me)?\s*$",
      "help", explanation="Help request")
# The phrasings a person actually uses for the same question. Registered as a
# SEPARATE rule rather than folded into the one above, because it is also in
# `_ANCHORED_ACTION_RULES` — the social gate consults it before deciding that
# "what can you do" is small talk — and a rule that is consulted in two places
# has to be one object, not one spelling copied twice.
_rule(CAPABILITY_ASK.pattern, "help",
      explanation="Capability question — what the bot can do for this caller")

# --- Learning ---
_rule(r"\b(learning (dashboard|stats|status)|self.?improv|what did you learn|adaptation (stats|status))\b",
      "learning", explanation="Learning dashboard request")

# Symbol-FIRST phrasing: "BTC scan", "eth analysis". The rule above only knew
# verb-first, so the web widget's quick-action button — which sends exactly
# this shape — matched nothing, fell through to freeform chat, and the model
# published a complete "BTC/USDT Scan — Full Analysis" around an invented
# price ($93.06, observed live 2026-08-23). The same fall-through produced the
# fabricated elliott-wave read on Telegram; both surfaces share classify_rules.
# The route IS the grounding: a scan that reaches analyze_asset is built from
# live indicators, and one that reaches the chat model is fiction in the shape
# of a measurement.
#
# THE MATCH DELIBERATELY EXCLUDES THE LEADING WORD. Everything before it is
# leftover, so `_names_a_non_asset` gets to rule on whether the user named an
# asset or something else — which is the machinery that already exists for
# exactly this question, and it settles the cases a pattern cannot:
#
#     BTC scan          symbol resolves          -> analyze_asset
#     portfolio scan    leftover "portfolio"     -> fall through to get_portfolio
#     wallet scan       leftover "wallet"        -> fall through to chat
#     technical analysis leftover "technical"    -> fall through to chat
#     buy BTC after the scan  leftover has verbs -> fall through to trade rules
#
# A denylist in the pattern was the first draft and it cannot win this: the
# excluded set is every English noun, while the admitted set is tickers, and
# guessing "portfolio"/"wallet"/"technical"/"risk"/"sentiment" are symbols is
# the SAME defect as the fabricated price one level down. `_extract_symbol`
# is already the authority — it reads BTC, resolves bitcoin, accepts an
# all-caps unknown like HYPE, and refuses lowercase prose — so this rule asks
# it rather than re-deciding.
#
# Anchored to the END so it claims only messages that ARE the request, and
# registered LAST so every more specific intent is tried first. Both are
# load-bearing and the second was added after a test caught the rule taking
# "close my BTC position after the scan": the symbol resolves there, so the
# `if symbol:` branch fires before `_names_a_non_asset` is ever consulted and
# a close request became an analysis at confidence 1.0. Ordering is the only
# thing that settles it — a pattern cannot tell which of two real intents a
# message carrying both is about.
#
# Bare "scan", "deep scan" and "market scan" are registered above and never
# reach here; "how does the scan work" does not end in the word.
_rule(r"\b(?:scan|analysis)\s*[?!.]*$",
      "analyze_asset", needs_symbol=True, explanation="Symbol-first scan request")


# ── Intent Router ─────────────────────────────────────────────────────

class IntentRouter:
    """Routes free-text messages to skills.

    Uses social detection first (greetings, thanks → chat),
    then rule-based matching (fast, no API call).
    Falls back to LLM classification when rules don't match
    and an LLM is available.
    """

    def __init__(self) -> None:
        self._rules = _INTENT_RULES

    def classify_rules(self, text: str) -> IntentResult:
        """Pure rule-based classification. No LLM call.

        Returns IntentResult with is_social=True for greetings/social chat,
        matched skill for trading intents, or empty for LLM fallback.
        """
        # Fast exit: social messages should go to chat, not skills
        if _is_social_message(text):
            return IntentResult(
                raw_text=text,
                is_social=True,
                explanation="Social/greeting message — route to conversational chat",
                # DETECTED, not asserted. This branch is about ROUTING (no
                # skill runs) and it was also answering a different question:
                # it hard-coded the answer SHAPE to `standard` while the other
                # three returns all detect one. That cost nothing while nothing
                # read `reply_mode`; it costs a wrong contract now that the
                # prompt does.
                #
                # `_is_social_message` is much wider than "hello". It calls any
                # message of three words or fewer social unless it carries a
                # trading word, and it matches a leading `bro|dude|mate|fam`,
                # so "grid bot", "dca logic" and "bro can you explain a sweep"
                # all arrive here -- an automation question and a beginner
                # question, told to answer in the general shape.
                #
                # Nothing is lost on the messages the branch is FOR: every
                # actually-social phrasing detects as `standard` on its own
                # (tests/test_one_answer_shape_per_turn.py drives the list), so
                # this substitutes a reading for a guess that agreed with it.
                reply_mode=_detect_reply_mode(text),
            )

        symbol = _extract_symbol(text)
        # EVERY asset named, not just the first. A skill that carries ONE
        # symbol cannot answer "analyze btc and eth", and answering BTC alone
        # is half the question rendered as the whole of it.
        named = symbols_named(text)

        for pattern, skill, needs_symbol, explanation in self._rules:
            m = pattern.search(text)
            if m:
                kwargs: dict[str, object] = {}
                if pattern in _COMPOUND_ACTION_PATTERNS:
                    # Not a skill argument: nothing dispatches a routed
                    # action. The surfaces read it to say what did NOT run.
                    kwargs["also_asked"] = True
                if skill == "trade_journal":
                    n = journal_count(text)
                    if n:
                        kwargs["count"] = n
                # A symbol is OPTIONAL for these: "post-mortem of my last
                # trade" names none and must still route, and "post mortem on
                # the ETH trade" names one the skill should be handed — read
                # from the question's object slots, not from anywhere in it.
                if skill in _SYMBOL_OPTIONAL:
                    slot = postmortem_symbol(text)
                    if slot:
                        kwargs["symbol"] = slot
                if needs_symbol:
                    if len(named) > 1:
                        # Ask which. The alternative is to pick one and print
                        # a card that looks like an answer to the question
                        # that was asked.
                        return IntentResult(
                            skill=skill,
                            kwargs={},
                            confidence=0.5,
                            source="rules",
                            raw_text=text,
                            explanation=f"{explanation} (names {len(named)} assets: "
                                        + ", ".join(named) + ")",
                            reply_mode=_detect_reply_mode(text),
                        )
                    if symbol:
                        kwargs["symbol"] = symbol
                    elif _names_a_non_asset(text, m):
                        # The message HAS an object and it is not an asset —
                        # "look at the docs", "check out my portfolio". Asking
                        # "which coin?" answers a question nobody asked, so let
                        # a later rule or chat take it instead of claiming it.
                        continue
                    else:
                        # A bare verb: "analyze", "check the setup", "where is
                        # liquidity". The user does want a chart and has not
                        # said of what, so asking IS the answer. Partial match —
                        # the caller asks for clarification.
                        return IntentResult(
                            skill=skill,
                            kwargs={},
                            confidence=0.5,
                            source="rules",
                            raw_text=text,
                            explanation=f"{explanation} (no symbol detected)",
                            reply_mode=_detect_reply_mode(text),
                        )
                return IntentResult(
                    skill=skill,
                    kwargs=kwargs,
                    confidence=1.0,
                    source="rules",
                    raw_text=text,
                    explanation=explanation,
                    reply_mode=_detect_reply_mode(text),
                )

        # No rule matched
        return IntentResult(raw_text=text, reply_mode=_detect_reply_mode(text))

    async def classify(self, text: str, llm_fn=None) -> IntentResult:
        """Classify intent using rules first, then optional LLM fallback.

        Args:
            text: User's free-text message
            llm_fn: Optional async callable(prompt) -> str for LLM classification
        """
        # Try rules first (instant, free)
        result = self.classify_rules(text)

        # Social messages always go to chat
        if result.is_social:
            return result

        if result.matched and result.confidence >= 0.8:
            audit(system_log,
                  f"Intent matched by rules: {result.skill}",
                  action="intent_route", result="RULES",
                  data={"skill": result.skill, "text": text[:100]})
            return result

        # If rules gave a partial match (needs symbol), return it
        if result.matched and result.confidence >= 0.5:
            return result

        # No match from rules — try LLM if available
        if llm_fn is not None:
            try:
                llm_result = await self._classify_with_llm(text, llm_fn)
                if llm_result.matched:
                    audit(system_log,
                          f"Intent matched by LLM: {llm_result.skill}",
                          action="intent_route", result="LLM",
                          data={"skill": llm_result.skill, "text": text[:100]})
                    return llm_result
            except Exception as exc:
                logger.debug("LLM intent classification failed: %s", exc)

        # No match at all — return empty (caller falls back to chat)
        return IntentResult(raw_text=text)

    async def _classify_with_llm(self, text: str, llm_fn) -> IntentResult:
        """Use LLM to classify intent when rules don't match."""
        prompt = (
            "Classify this user message into ONE of these skills. "
            "Respond with ONLY the skill name, nothing else.\n\n"
            "Skills:\n"
            "- scan_market: scanning/overview of market movers\n"
            "- analyze_asset: analysis of a specific crypto (include symbol)\n"
            "- get_portfolio: portfolio, positions, PnL\n"
            "- check_risk: risk status, exposure, drawdown\n"
            "- trade_journal: trade history\n"
            "- macro_calendar: macro events, FOMC, CPI\n"
            "- costs: spending, budget\n"
            "- help: how to use the bot\n"
            "- NONE: doesn't match any skill (general chat)\n\n"
            f"User message: \"{text[:500]}\"\n\n"
            "Skill name:"
        )
        # F-08 FIX: Explicit timeout on LLM classification call
        import asyncio
        try:
            raw = await asyncio.wait_for(llm_fn(prompt), timeout=10.0)
        except asyncio.TimeoutError:
            logger.debug("LLM intent classification timed out")
            return IntentResult(raw_text=text)
        skill_name = raw.strip().lower().replace('"', '').replace("'", "")

        # Validate the response is a known skill
        # NLP-2: 'whynot' is a real registered skill (WhyNotSkill); kept.
        # Removed the duplicate 'trade_journal' entry (cosmetic — it's a set).
        valid_skills = {
            "scan_market", "analyze_asset", "get_portfolio", "check_risk",
            "trade_journal", "macro_calendar", "costs", "help",
            "run_backtest", "halt", "playbook", "patterns", "learning",
            "status", "whynot",
        }
        if skill_name in valid_skills:
            kwargs = {}
            if skill_name == "analyze_asset":
                symbol = _extract_symbol(text)
                if symbol:
                    kwargs["symbol"] = symbol
            return IntentResult(
                skill=skill_name,
                kwargs=kwargs,
                confidence=0.7,
                source="llm",
                raw_text=text,
                explanation=f"LLM classified as {skill_name}",
            )

        return IntentResult(raw_text=text)


# ── Public-surface gate ───────────────────────────────────────────────

#: Skills whose answer IS live market numbers. A visitor with no session
#: cannot be served any of these honestly, because the public path has no
#: feed behind it.
_LIVE_MARKET_SKILLS = frozenset({
    "analyze_asset", "scan_market", "scan_deep", "scan_full",
    "scan_swing", "scan_scalp", "scan_intraday",
})

#: Price asks, which the rules above do NOT route — measured, not assumed:
#: "current price of bitcoin", "price of ETH" and "btc price" all fall
#: through classify_rules to chat today. A gate built only on the router
#: would have let every one of them reach the model.
_PRICE_PHRASE = re.compile(
    r"\b(?:current|live|latest|spot|real[\s-]?time)\s+price\b"
    r"|\bprice\s+(?:of|for)\s"
    r"|\bhow much is\b",
    re.IGNORECASE)
_PRICE_WORD = re.compile(r"\b(?:price|worth|value|quote)\b", re.IGNORECASE)

_public_gate_router: Optional["IntentRouter"] = None


def needs_live_market_data(text: str) -> bool:
    """Would answering this honestly require reading the live market?

    The public (anonymous) chat has no feed, and its system prompt already
    ORDERED the model never to state a live price. It was watched ignoring
    that order and publishing a full "BTC/USDT Scan — Full Analysis" around
    an invented $93.06. An instruction is advisory; a caller that returns
    before the LLM runs is structural, and this is the predicate for it.

    Deliberately built on `classify_rules` rather than a second regex. The
    alternative — a pattern in the gateway listing the scan shapes — is two
    parsers for one question, and the failure mode of two is not a crash: it
    is the router learning a new phrasing (symbol-first "BTC scan" was added
    the same day) while the gate silently keeps letting that phrasing through
    to the model. One source, so a new route is a new refusal for free.

    The price branch is the part the router cannot answer, and it is here
    because it was CHECKED rather than assumed — see `_PRICE_PHRASE`.
    """
    t = text or ""
    if _PRICE_PHRASE.search(t):
        return True
    if _extract_symbol(t) and _PRICE_WORD.search(t):
        return True

    global _public_gate_router
    if _public_gate_router is None:
        _public_gate_router = IntentRouter()
    result = _public_gate_router.classify_rules(t)
    return bool(result.matched and result.skill in _LIVE_MARKET_SKILLS)
