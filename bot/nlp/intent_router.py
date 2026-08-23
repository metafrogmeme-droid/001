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
    if _GREETING_PATTERNS.search(stripped):
        return True
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
        trading_words = {
            "scan", "analyze", "portfolio", "risk", "backtest", "macro",
            "halt", "journal", "cost", "dashboard", "trade", "signal",
            "positions", "position", "balance", "equity", "pnl",
            "swing", "scalp", "intraday", "playbook", "performance",
            "entry", "setup", "liquidity", "scan",
        }
        if not any(w.lower() in trading_words for w in words):
            # Only classify as social if it doesn't contain intent keywords
            for pattern, _, _, _ in _INTENT_RULES:
                if pattern.search(stripped):
                    return False
            return True
    return False


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
            candidate = f"{word.upper()}/USDT"
            return _validate_symbol(candidate)

    # Try explicit patterns like "BTC/USDT" or "$BTC"
    explicit = re.search(r'\$?([A-Z]{2,10})/USDT', text.upper())
    if explicit:
        candidate = f"{explicit.group(1)}/USDT"
        return _validate_symbol(candidate)
    dollar = re.search(r'\$([A-Z]{2,10})\b', text.upper())
    if dollar and dollar.group(1).lower() in _KNOWN_SYMBOLS:
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
                  # Particles of the multi-word verbs above. Without these,
                  # "check out the news" reads OUT as the asset.
                  "out", "at", "on", "up", "into"}
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
    """
    leftover = text[:match.start()] + " " + text[match.end():]
    return any(w.lower() not in _FILLER
               for w in re.findall(r"[A-Za-z]{2,}", leftover))


def _rule(pattern: str, skill: str, needs_symbol: bool = False, explanation: str = ""):
    """Register a rule-based intent pattern."""
    _INTENT_RULES.append((
        re.compile(pattern, re.IGNORECASE),
        skill,
        needs_symbol,
        explanation or f"Matched pattern for {skill}",
    ))


# --- Agent stance (talk-to-your-agent risk posture) ---
# Registered FIRST so risk-preference phrasing wins over scan/analyze rules.
# These never execute anything: the handler PROPOSES a stance switch with a
# confirm button (the mode_ callback path, which is permission-gated).
_rule(r"\b(be (a bit |a little |way |much )?more (careful|cautious|conservative|defensive)|"
      r"(reduce|lower|less|cut) (the )?risk|risk off|play it safe(r)?|"
      r"(take |use )?smaller positions?|slow (it |things )?down|protect (the )?capital|"
      r"trade (more )?defensive(ly)?|dial (it|risk) (back|down))\b",
      "stance_defensive", explanation="Wants a more defensive risk posture")
_rule(r"\b(be (a bit |a little |way |much )?more aggressive|"
      r"(increase|raise|more|add) (the )?risk|risk on|push (it )?harder|"
      r"(take |use )?bigger positions?|trade (more )?aggressive(ly)?|"
      r"step on (the )?gas|go bigger)\b",
      "stance_aggressive", explanation="Wants a more aggressive risk posture")
_rule(r"\b((back to|go) (normal|balanced|default)( risk| mode)?|"
      r"balanced (mode|stance|risk)|reset (the )?(risk|stance|mode))\b",
      "stance_balanced", explanation="Wants the balanced default posture")

# --- Scan / market overview ---
# RUNECLAW natural language triggers — scan modes
_rule(r"\b(swing(?: (?:scan|mode|trade))?|4h scan|swing by swing)\b",
      "scan_swing", explanation="Swing scan (4h)")
_rule(r"\b(scalp(?: (?:scan|mode|trade))?|5m scan|quick scan|fast scan)\b",
      "scan_scalp", explanation="Scalp scan (5m)")
_rule(r"\b(intraday(?: (?:scan|mode|trade))?|15m scan|day ?trade scan)\b",
      "scan_intraday", explanation="Intraday scan (15m)")
_rule(r"\b(deep ?scan|full universe|scan (all|everything)|67 symbols?)\b",
      "scan_deep", explanation="Deep scan (67+ symbols)")
_rule(r"\b(full ?scan|complete scan|scan with patterns)\b",
      "scan_full", explanation="Full scan with patterns")
# General scan triggers
_rule(r"\b(claw scan|run the bot scan|market read|read the trend)\b",
      "scan_market", explanation="RUNECLAW market scan request")
_rule(r"\b(what does the claw see|what.?s the claw (reading|saying|showing))\b",
      "analyze_asset", needs_symbol=True, explanation="RUNECLAW asset read request")
_rule(r"\b(scan (the )?market|what.?s moving|anything moving|top movers?|market (scan|overview)|show me movers)\b",
      "scan_market", explanation="Market scan request")
_rule(r"\b(volume spike|big moves?|unusual (volume|activity))\b",
      "scan_market", explanation="Volume/movement alert request")
# Bare "scan" as last resort → general market scan
_rule(r"^scan$",
      "scan_market", explanation="General scan request")

# --- Analyze specific asset ---
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
_rule(r"\b(analy[sz]e|look at|check out|how.?s .{0,10}(doing|looking|going))\b",
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
_ANALYSIS_TERMS = (
    r"elliott(?:\s+waves?)?|wave\s+(?:count|analysis|structure)|"
    r"rsi|macd|stoch(?:astic)?|bollinger|ichimoku|vwap|atr|"
    r"ema|sma|moving averages?|"
    r"fib(?:onacci)?(?:\s+(?:levels?|zones?|retracements?))?|"
    r"support|resistance|levels?|targets?|"
    r"setup|entry|entries|structure|trend|momentum|breakout|"
    r"order\s?blocks?|fair\s?value\s?gaps?|fvg|liquidity|"
    r"ta|technicals?|chart"
)
_rule(rf"^\s*[A-Za-z]{{2,15}}(?:/[A-Za-z]{{2,10}})?\s+(?:{_ANALYSIS_TERMS})\b",
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
_NOT_A_TICKER = (
    r"the|it|this|that|them|us|me|you|my|mine|"
    r"today|tomorrow|yesterday|now|later|tonight|"
    r"everything|anything|something|all|stuff|things|"
    r"market|markets|price|prices|chart|charts|trading|"
    r"docs|help|news|here|there|then|what|why|how"
)
_rule(rf"^\s*(?:check|look at|thoughts on|opinion on|view on|what about|how about|"
      rf"whats up with|what.?s (?:up )?with|any(?:thing)? on)\s+"
      rf"(?!(?:{_NOT_A_TICKER})\b)"
      rf"[A-Za-z]{{2,15}}(?:/[A-Za-z]{{2,10}})?\s*[?!.]*$",
      "analyze_asset", needs_symbol=True, explanation="Bare asset enquiry")

# --- Portfolio ---
_rule(r"\b(my (positions?|portfolio|book|trades?|holdings?|balance|equity)|show (my )?portfolio|check (my )?pnl|how.?s my (portfolio|pnl))\b",
      "get_portfolio", explanation="Portfolio status request")
_rule(r"\b(open positions?|what.?s open|current (positions?|trades?))\b",
      "get_portfolio", explanation="Open positions request")
_rule(r"\b(pos+i[st]+ions?|posistions?)\b",
      "get_portfolio", explanation="Positions request (typo-tolerant)")
_rule(r"\b(portfolio|balance|equity|pnl|profit|loss|p&l)\b",
      "get_portfolio", explanation="Portfolio keyword")

# --- Orders ---
_rule(r"\b(open orders?|pending orders?|limit orders?|my orders?|show orders?|active orders?|order book|what.?s pending)\b",
      "get_orders", explanation="Open/pending orders on exchange")

# --- Risk ---
# RUNECLAW risk triggers
_rule(r"\b(risk check|check (my )?risk|am i (over)?exposed)\b",
      "check_risk", explanation="RUNECLAW risk check")
# "risk" alone is too aggressive — require compound phrases
_rule(r"\b(risk (status|dashboard|check|engine|report)|show risk|check (the )?exposure|drawdown (status|report)|circuit.?breaker (status)?)\b",
      "check_risk", explanation="Risk status request")
_rule(r"\b(how.?s (the )?risk|risk level|am i safe)\b",
      "check_risk", explanation="Risk inquiry")

# --- Status / dashboard ---
_rule(r"\b(bot (status|state)|engine (status|state)|show (me )?dashboard|system status|is .{0,5}bot (running|alive|on)|status|dashboard)\b",
      "status", explanation="System status request")

# --- Journal ---
_rule(r"\b(trade (journal|history|log)|recent trades?|past trades?|show (my )?trades|journal|history|closed trades?)\b",
      "trade_journal", explanation="Trade journal request")

# --- Macro ---
_rule(r"\b(macro (calendar|events?)|fomc|cpi (data|release)|fed (meeting|decision)|nfp (data|release)|economic (calendar|data))\b",
      "macro_calendar", explanation="Macro event request")

# --- Backtest ---
# Two rules on purpose: the symbol-bearing form carries the coin into the
# skill (which runs the REAL frozen-snapshot backtest for it); the generic
# form keeps its old full-confidence match (synthetic smoke test) — a single
# needs_symbol rule would demote bare "run a backtest" to a 0.5-confidence
# partial and drop it to LLM chat.
_rule(r"\bbacktest\s+[A-Za-z0-9/]{2,12}\b",
      "run_backtest", needs_symbol=True, explanation="Backtest request (symbol)")
_rule(r"\b(run (a )?backtest|backtest (it|this)|replay|test (the )?strategy)\b",
      "run_backtest", explanation="Backtest request")

# --- Costs ---
_rule(r"\b(show costs?|llm (cost|spending|budget)|api (cost|spending)|how much .{0,12}(cost|spending|spend))\b",
      "costs", explanation="Cost breakdown request")

# --- Halt/emergency ---
# Only match explicit halt/stop commands, not casual "stop"
_rule(r"\b(halt (the )?bot|stop (the )?(bot|trading|engine|everything|all)|emergency (stop|halt)|kill (the )?bot|pause (the )?(bot|trading))\b",
      "halt", explanation="Emergency halt request")

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
_rule(r"\b(why (no|not a?) trade|why (was|did) .{0,24}(reject|skip|filter)"
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
                reply_mode="standard",
            )

        symbol = _extract_symbol(text)

        for pattern, skill, needs_symbol, explanation in self._rules:
            m = pattern.search(text)
            if m:
                kwargs = {}
                if needs_symbol:
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
