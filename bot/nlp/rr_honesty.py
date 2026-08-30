"""Correct a risk:reward the model asserted but its own levels contradict.

WHY. The v12 eval (2026-08-30) put three trades to the model whose geometry
sits just under the 1.2 floor. It approved all three, and in each case it
printed a ratio that clears the floor and that its own entry/stop/target do
not support:

    levels give 1.17  ->  model wrote "Risk:Reward: 1.25"  -> APPROVED
    levels give 1.14  ->  model wrote "Risk:Reward: 1.41"  -> APPROVED
    levels give 1.18  ->  model wrote "Risk:Reward: 1.40"  -> APPROVED

That was after a training generation built specifically to fix it: 36,000
targeted samples deriving the ratio in view, plus 25,003 rows dropped for
stating values their prompts never supplied. Across the whole eval the
risk:reward check still scored 55.4%.

The lesson is that the training worked on the wrong layer. Showing a model
thousands of correct divisions teaches it what a correct-looking division
looks like; nothing in next-token prediction forces the quotient to follow
from the operands. It is a good way to teach a FORMAT and a bad way to teach
ARITHMETIC.

WHAT THIS DOES NOT PROTECT. Not the money path. `RiskEngine` reads
`TradeIdea.risk_reward_ratio`, a computed property over entry/stop/target
(bot/utils/models.py), so a fabricated ratio never reaches a trade decision
— those three trades would have been refused whatever the prose said. This
is a HONESTY fix for what a person reads, not a safety one.

THE PRINCIPLE IS THE ONE ALREADY APPLIED TO SCAN ROUTING. The route is the
grounding: a number the code can compute should never be rendered from a
sentence the model wrote. Division is not a language problem, and the levels
are right there.

WHAT IT WILL NOT DO. It never invents. A ratio whose levels are absent or
unreadable is left exactly as written, because "the model may be right and I
cannot check" is a different state from "the model is wrong", and printing a
correction derived from nothing would be the same defect wearing the
opposite hat.
"""

from __future__ import annotations

import re

#: Agreement tolerance. The model rounds, and so does the corpus — a stated
#: 2.00 against a computed 1.99 is the same claim. Anything past this is a
#: disagreement about the trade, not about rounding.
TOLERANCE = 0.05

_NUM = r"\$?([\d][\d,]*\.?\d*)"
_RE_ENTRY = re.compile(r"(?:entry[_\s]?price|Entry)\s*[:\s]\s*" + _NUM, re.IGNORECASE)
_RE_SL = re.compile(r"(?:stop[_\s]?loss|Stop Loss|SL)\s*[:\s]\s*" + _NUM, re.IGNORECASE)
_RE_TP = re.compile(r"(?:take[_\s]?profit|Take Profit|TP1?)\s*[:\s]\s*" + _NUM, re.IGNORECASE)
_RE_DIR = re.compile(r"(?:direction|Direction)\s*[:\s]\s*(LONG|SHORT)", re.IGNORECASE)

#: A stated ratio, in the shapes the model actually emits: "Risk:Reward: 1:2.35",
#: "R:R 1.40", "risk:reward of 1.25", "RISK_REWARD: 3.0".
_RE_STATED = re.compile(
    r"(?P<label>Risk[:\s_-]?Reward(?:\s+ratio)?|R:R|RISK[_ ]REWARD)"
    r"(?P<mid>\s*(?:of\s+|is\s+|[:=]\s*)?\s*(?:1\s*:\s*)?)"
    r"(?P<ratio>\d+\.?\d*)",
    re.IGNORECASE)


def _num(s: str):
    try:
        return float(s.replace(",", "").replace("$", ""))
    except (ValueError, AttributeError):
        return None


def computed_ratio(text: str):
    """The risk:reward the text's own levels imply, or None if unreadable.

    None is a real answer here and is treated as one by every caller: it
    means the claim cannot be checked, not that it is wrong.
    """
    m_e, m_s, m_t = _RE_ENTRY.search(text), _RE_SL.search(text), _RE_TP.search(text)
    if not (m_e and m_s and m_t):
        return None
    entry, sl, tp = _num(m_e.group(1)), _num(m_s.group(1)), _num(m_t.group(1))
    if entry is None or sl is None or tp is None:
        return None

    m_dir = _RE_DIR.search(text)
    direction = m_dir.group(1).upper() if m_dir else None
    if direction is None:
        # Infer only when the geometry is unambiguous — target and stop on
        # opposite sides of entry. Guessing a direction would make up the
        # sign of every number downstream.
        if tp > entry > sl:
            direction = "LONG"
        elif tp < entry < sl:
            direction = "SHORT"
        else:
            return None

    if direction == "LONG":
        risk, reward = entry - sl, tp - entry
    else:
        risk, reward = sl - entry, entry - tp
    if risk <= 0 or reward <= 0:
        return None
    return round(reward / risk, 2)


def correct_stated_rr(text: str):
    """Replace ratios the text's own levels contradict. Returns (text, n).

    `n` is how many statements were corrected — 0 when the text made no
    ratio claim, when its levels are unreadable, or when the claim already
    agrees with them.
    """
    if not text:
        return text, 0
    actual = computed_ratio(text)
    if actual is None:
        return text, 0

    corrected = 0

    def _repl(m):
        nonlocal corrected
        stated = _num(m.group("ratio"))
        if stated is None or abs(stated - actual) <= TOLERANCE:
            return m.group(0)
        corrected += 1
        # The "1:" prefix is kept when the model used it, so a corrected
        # line reads like the rest of the report rather than like a patch.
        return f"{m.group('label')}{m.group('mid')}{actual:.2f}"

    return _RE_STATED.sub(_repl, text), corrected
