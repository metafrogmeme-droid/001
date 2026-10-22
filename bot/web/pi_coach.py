"""Ask the Claw: the coach inside the Pi Browser app.

The Pi app is practice and education with no token, no sale and no link out
(app/routes/piapp.js). Its coach is the public chat's model with a prompt of
its own, and three things around the model that a prompt cannot promise:

  * INPUT GATES, before any model runs. A question about a token, a sale or
    an airdrop is answered by one fixed sentence; a question that needs a
    live price is answered by another, unless the facts carry live prices
    (`has_live_prices`). Neither fixed sentence spends a question.
  * FACTS, not memory. The website hands the coach what it read for this
    person (their practice positions and closed trades, today's Duel rounds
    and their calls, the signal card they asked about), as a block the
    prompt labels as read from the app. The coach explains; it cannot act.
  * AN OUTPUT CHECK, after the model. A reply that names a token sale, the
    project's token, an airdrop, a link, an exchange key or signing up
    elsewhere is WITHHELD and replaced with a fixed sentence. An instruction
    is advisory; this is structural, the shape the public scan gate took
    after a model was watched ignoring its prompt.

Nothing here can reach an account, a portfolio read, an order or a tool: the
gateway calls `_llm_chat(public=True)`, which is account-free and tool-free by
construction, and the coach's facts arrive from the website as text.
"""
from __future__ import annotations

import re

#: The longest question the coach takes (the website's own cap is the same).
MAX_TEXT = 1000

#: The longest facts block the coach is handed. The website builds it from
#: its own reads; this bound exists because it arrives in a request body.
MAX_CONTEXT = 2400

PROMPT = (
    "You are the Claw, the friendly owl coach inside RUNECLAW Arena, a free "
    "practice app in the Pi Browser. People here call the crypto market "
    "against an AI in the Daily Duel and practise trading with credits that "
    "are not money.\n\n"
    "WHAT YOU DO:\n"
    "- Explain trading ideas in plain words: long and short, leverage, "
    "stop-loss, take-profit, risk and reward (\"2R\" means the target is twice "
    "as far as the stop), liquidation, volatility.\n"
    "- Explain the Daily Duel: a few rounds a day; the person calls each one "
    "(up or down, a range that breaks or holds, which of two coins moves "
    "more, back or fade the Claw, a football result); the Claw's own call is "
    "sealed before anyone plays and shown after; marks are 1 per correct "
    "call and 2 for beating the Claw.\n"
    "- Explain receipts: every round, call and settlement is sealed with a "
    "SHA-256 hash; tapping a receipt's Check button hashes it again in the "
    "person's browser, and a day's seals are folded into one daily root.\n"
    "- Use the FACTS block below when the person asks about their own "
    "trades, calls or a signal card. Say what the facts show; do not guess "
    "beyond them.\n\n"
    "RULES YOU NEVER BREAK:\n"
    "- Practice and education only. Never tell anyone to buy, sell or trade "
    "real money, and never promise a result.\n"
    "- Never mention any token, coin of this project, token sale, presale, "
    "airdrop, listing or price target for a project token. If asked, say "
    "this app is practice only and you do not discuss tokens or sales.\n"
    "- Never send anyone outside the app: no links, no websites, no "
    "addresses, no social accounts, no telling them to sign up or to connect "
    "an exchange or API keys anywhere.\n"
    "- Live prices are in the FACTS block when the app could read them. Quote "
    "only those, and say when they were read; never state a price that is "
    "not there.\n"
    "- Never invent facts about the person's account, calls or results.\n\n"
    "STYLE: warm, short and clear, for someone new to trading. At most about "
    "120 words. Plain HTML only: <b>bold</b> and <code>mono</code>, no links. "
    "One emoji at most."
)

#: How long and what shape, for the coach. It replaces the public website
#: page's contracts (`bot.skills.chat_runtime.reply_contract`), which describe
#: a page with no facts and no account and send the reader to sign in for
#: "the live one": a door the Pi app does not have, said to a signed-in
#: person beside the facts the app read for them.
REPLY_CONTRACT = (
    "\n\nHOW LONG AND WHAT SHAPE\n"
    "THIS TURN: you are the coach inside the Pi app. The FACTS block is what "
    "the app read for this person; when it is there, answer from it and never "
    "say the app has no data. A level written in the FACTS (a signal card's "
    "entry, stop or target) you may repeat, saying it is from the card, and "
    "you may explain what would prove the call wrong in those terms. Never "
    "add a level, a size or a plan of your own. The only current prices you "
    "have are the FACTS block's live prices, with the time they were read: "
    "you may compare one with the card's levels; a coin not listed there you "
    "cannot read now, so say so in one short sentence. "
    "Never tell them to sign in, sign up or go anywhere else for more. Match "
    "the length to the question, at most about 120 words, and end on a "
    "complete sentence.\n"
)

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_context(raw) -> str:
    """The facts block as the coach may read it: text, bounded, no control
    characters. Anything that is not a string is no facts at all."""
    if not isinstance(raw, str):
        return ""
    return _CTRL.sub("", raw).strip()[:MAX_CONTEXT]


def build_prompt(context: str = "") -> str:
    facts = clean_context(context)
    if not facts:
        return PROMPT + "\n\nFACTS: none were read for this question."
    return (PROMPT + "\n\nFACTS (read from the app for this person just now; "
            "the only facts you have about them):\n" + facts)


# ── the gates ──────────────────────────────────────────────────────────────

#: A question about a token, a sale or an airdrop. Answered without a model.
_TOKEN_ASK = re.compile(
    r"\b(pre-?sale|presales|rclaw|airdrops?|ico|ido|token\s*sale|tokenomics|"
    r"launchpad|smithii|whitelist|vesting)\b",
    re.IGNORECASE)

#: What a reply in the Pi app may never carry. A link in any form, the
#: project's token or a sale, signing up or linking an exchange elsewhere.
_WITHHOLD = [
    ("link", re.compile(r"<a\b|https?://|\bwww\.|\bt\.me/|\b[a-z0-9-]+\.(com|io|org|net|app|xyz|finance|gg)\b", re.I)),
    ("token", re.compile(r"\b(pre-?sale|rclaw|airdrops?|token\s*sale|tokenomics|ico|ido|smithii|launchpad)\b", re.I)),
    ("sign_up", re.compile(r"\bsign(ing)?\s*up\b|\bcreate an account\b", re.I)),
    ("exchange_keys", re.compile(r"\bapi\s*keys?\b|\bconnect(ing)?\s+(an?\s+|your\s+)?(own\s+)?exchange\b", re.I)),
]

TOKEN_ANSWER = ("This app is practice only, and I do not talk about tokens, sales or "
                "airdrops here. Ask me about the Daily Duel, a signal card, or any "
                "trading word you have met in the app.")
NO_FEED_ANSWER = ("I could not read a live price for that just now. The Signals tab "
                  "shows the agent's current calls with their charts, and I can "
                  "explain any card you see there: tap its Why? button.")
WITHHELD_ANSWER = ("I started an answer I am not allowed to give in this app, so I "
                   "have left it out. Try asking it another way, about the Duel, a "
                   "signal or a trading idea.")
UNAVAILABLE_ANSWER = ("The coach could not answer just now. Your question was not "
                      "counted; try again in a minute.")


#: The head of the live-prices line the website writes into the facts
#: (app/lib/pi_coach.js LIVE_PRICES_HEAD; a test holds the two equal). A read
#: that failed writes a different line, so its presence means prices.
LIVE_PRICES_HEAD = "Live prices (Bitget USDT-M futures), read "


def has_live_prices(context: object) -> bool:
    """Whether the facts carry live prices the coach may quote."""
    return LIVE_PRICES_HEAD in clean_context(context)


def token_ask(text: str) -> bool:
    return bool(_TOKEN_ASK.search(text or ""))


def withheld_reason(reply: str) -> str | None:
    """The first rule a reply breaks, or None when it may be shown."""
    for name, rx in _WITHHOLD:
        if rx.search(reply or ""):
            return name
    return None
