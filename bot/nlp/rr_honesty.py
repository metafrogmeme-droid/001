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

#: What sits between a label and its number. The original pattern was
#: `\s*[:\s]\s*`, which reads "Entry: 59500" and "Entry 59500" and nothing
#: else. On 2026-09-01 a live reply put its levels as "Entry at $59,500,
#: stop at $58,500 ... take profit at $61,000" and stated a ratio of 1.70
#: that its own numbers give as 1.50. computed_ratio returned None and the
#: wrong figure went out unchanged — the guard was in the path and silent.
_CONN = r"(?:\s*[:=]\s*|\s+(?:at|is|of|was)\s+|\s+)"

_RE_ENTRY = re.compile(r"(?:entry[_\s]?price|Entry)" + _CONN + _NUM, re.IGNORECASE)
_RE_SL = re.compile(r"(?:stop[_\s]?loss|Stop Loss|stop|SL)" + _CONN + _NUM, re.IGNORECASE)
_RE_TP = re.compile(r"(?:take[_\s]?profit|Take Profit|target|TP1?)" + _CONN + _NUM,
                    re.IGNORECASE)
_RE_DIR = re.compile(r"(?:direction|Direction)\s*[:\s]\s*(LONG|SHORT)", re.IGNORECASE)

#: A separator row in a pipe table: "------|-----------|------|-----".
_RE_TABLE_SEP = re.compile(r"^[\s|:\-]+$")


def _cells(line: str):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _cell_num(cell: str):
    """First number in a table cell, so "$59,500" and "59500 USDT" both read."""
    m = re.search(_NUM, cell)
    return _num(m.group(1)) if m else None


def _levels_from_table(text: str):
    """(entry, sl, tp) from a pipe table whose labels are in a header ROW.

    The label regexes above look for a label adjacent to its number. A table
    puts every label on one line and every value on another, so adjacency is
    gone and each of them reads the header text as its number-that-isn't.
    Columns are matched by POSITION, which is the only thing that actually
    ties a header cell to a value cell:

        Entry   | Stop Loss | Take Profit | R:R
        --------|-----------|-------------|-----
        $59,500 | $58,500   | $61,000     | 1:1.70

    Returns None when there is no such table, when a needed column is
    missing, or when a cell holds no number — never a partial reading.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if "|" not in line:
            continue
        head = [c.lower() for c in _cells(line)]

        def col(*keys):
            for j, c in enumerate(head):
                if any(k in c for k in keys):
                    return j
            return None

        i_e, i_s, i_t = col("entry"), col("stop"), col("take profit", "target")
        if i_e is None or i_s is None or i_t is None:
            continue
        # The value row is the next line carrying pipes that is not a rule.
        for nxt in lines[i + 1:i + 4]:
            if "|" not in nxt:
                continue
            if _RE_TABLE_SEP.match(nxt):
                continue
            vals = _cells(nxt)
            if max(i_e, i_s, i_t) >= len(vals):
                return None
            e, s, t = (_cell_num(vals[i_e]), _cell_num(vals[i_s]),
                       _cell_num(vals[i_t]))
            return None if None in (e, s, t) else (e, s, t)
    return None

_LABEL = r"Risk[:\s_-]?Reward(?:\s+ratio)?|R:R|R/R|RISK[_ ]REWARD"

#: A stated ratio, in the shapes the model actually emits: "Risk:Reward: 1:2.35",
#: "R:R 1.40", "risk:reward of 1.25", "RISK_REWARD: 3.0".
#: Two lookaheads, both load-bearing.
#:
#: (?!\d)(?!\.\d)  stops the ratio matching a PREFIX of a longer number.
#: Without it, backtracking finds "10" inside "10.00" the moment the second
#: guard rejects the whole token, and the correction lands mid-number.
#:
#: IT WAS `(?![\d.])`, WHICH ALSO REJECTED A FULL STOP. A ratio that ends a
#: sentence is the most ordinary thing in prose, and every shape broke on one:
#:
#:     "R:R = 1.50."          -> None   the guard runs and recognises nothing
#:     "Risk:Reward: 1:2.35." -> 1      WORSE: the wrong number, not no number
#:
#: The first is the shape this module exists to prevent — runs, matches
#: nothing, reports success. The second is worse and was not predicted: the
#: `1:` prefix is optional, so rejecting "2.35." makes the pattern fall back
#: to the "1" and hand back a confident misparse.
#:
#: A trailing `.` is only part of the number when a digit follows it, which is
#: exactly what `(?!\.\d)` says. Verified against the division form and the
#: "10.00" prefix case both guards were written for.
#:
#: (?!\s*/)   leaves a DIVISION alone. v13's corpus writes the honest form
#: "R:R = 10.00 / 5.00 = 2.00"; matching the numerator there would rewrite
#: correct arithmetic into nonsense. Silently corrupting a right answer is a
#: worse failure than missing a wrong one, so the guard declines the shape.
_RATIO = r"(?P<ratio>\d+\.?\d*)(?!\d)(?!\.\d)(?!\s*/)"

#: Horizontal whitespace only. `\s*` crosses newlines, which let a label in
#: a table HEADER bind to the first number of the value row below it.
_H = r"[ \t]"

_RE_STATED = re.compile(
    r"(?P<label>" + _LABEL + r")"
    r"(?P<mid>" + _H + r"*(?:of" + _H + r"+|is" + _H + r"+|[:=]" + _H + r"*)?"
    + _H + r"*(?:1" + _H + r"*:" + _H + r"*)?)" + _RATIO,
    re.IGNORECASE)

#: The same claim with the label AFTER the number — "for a 1:1.70 R:R". The
#: pattern above cannot see this one, which is why the live 2026-09-01 reply
#: kept its wrong 1.70 even once the levels were being read correctly.
_RE_STATED_PRE = re.compile(
    r"(?P<pre>1" + _H + r"*:" + _H + r"*)?" + _RATIO
    + r"(?P<label>" + _H + r"{0,3}(?:" + _LABEL + r"))",
    re.IGNORECASE)

#: The QUOTIENT of a written-out division: the "2.00" in
#: "R:R = 10.00 / 5.00 = 2.00". _RATIO deliberately refuses to touch the
#: operands of a division, which would otherwise leave this — the one number
#: in the line that is actually the claim — unreachable.
#:
#: Only the quotient is rewritten, never the operands. When they disagree
#: with the levels too, the line is left visibly inconsistent rather than
#: rebuilt from numbers this function did not derive.
_RE_STATED_QUOT = re.compile(
    r"(?P<expr>\d+\.?\d*" + _H + r"*/" + _H + r"*\d+\.?\d*" + _H + r"*=" + _H + r"*)"
    r"(?P<ratio>\d+\.?\d*)(?!\d)(?!\.\d)")

#: A whole table cell that is nothing but a ratio: "1:1.70", "1.70".
_RE_RR_CELL = re.compile(r"(?P<pre>1\s*:\s*)?(?P<ratio>\d+\.?\d*)")

#: Ratios outside this range are not risk:reward claims — they are prices,
#: sizes or step counts that happened to sit next to the words. Bounding it
#: is what makes the label-after-number pattern safe to run.
_PLAUSIBLE = (0.01, 100.0)


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
    # A pipe table is tried FIRST. Its header row contains the words the
    # label regexes hunt for, so leaving it to them means they match the
    # header and read whatever number follows it on the page — a wrong
    # reading rather than no reading, which is the worse of the two.
    table = _levels_from_table(text)
    if table is not None:
        entry, sl, tp = table
    else:
        m_e = _RE_ENTRY.search(text)
        m_s = _RE_SL.search(text)
        m_t = _RE_TP.search(text)
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

    def _wrong(stated):
        """True only for a plausible ratio that disagrees with the levels."""
        if stated is None:
            return False
        if not _PLAUSIBLE[0] <= stated <= _PLAUSIBLE[1]:
            return False
        return abs(stated - actual) > TOLERANCE

    def _repl(m):
        nonlocal corrected
        if not _wrong(_num(m.group("ratio"))):
            return m.group(0)
        corrected += 1
        # The "1:" prefix is kept when the model used it, so a corrected
        # line reads like the rest of the report rather than like a patch.
        return f"{m.group('label')}{m.group('mid')}{actual:.2f}"

    def _repl_pre(m):
        nonlocal corrected
        if not _wrong(_num(m.group("ratio"))):
            return m.group(0)
        corrected += 1
        return f"{m.group('pre') or ''}{actual:.2f}{m.group('label')}"

    def _repl_quot(m):
        nonlocal corrected
        if not _wrong(_num(m.group("ratio"))):
            return m.group(0)
        corrected += 1
        return f"{m.group('expr')}{actual:.2f}"

    text, n_cells = _correct_table_rr(text, actual, _wrong)
    corrected += n_cells
    text = _RE_STATED.sub(_repl, text)
    text = _RE_STATED_PRE.sub(_repl_pre, text)
    text = _RE_STATED_QUOT.sub(_repl_quot, text)
    return text, corrected


def _correct_table_rr(text: str, actual: float, wrong):
    """Rewrite an R:R column's value cell. Returns (text, n).

    A table cell holds the bare ratio — "1:1.70" with the label sitting in a
    header row two lines up — so neither prose pattern can reach it. Column
    is matched by position, same as the level reader, and only a cell that is
    ENTIRELY a ratio is touched; anything else is left alone rather than
    guessed at.
    """
    lines = text.splitlines()
    n = 0
    for i, line in enumerate(lines):
        if "|" not in line:
            continue
        head = [c.lower() for c in _cells(line)]
        rr = next((j for j, c in enumerate(head)
                   if "r:r" in c or "r/r" in c
                   or ("risk" in c and "reward" in c)), None)
        if rr is None:
            continue
        for k in range(i + 1, min(i + 4, len(lines))):
            nxt = lines[k]
            if "|" not in nxt or _RE_TABLE_SEP.match(nxt):
                continue
            raw = nxt.split("|")
            off = 1 if nxt.lstrip().startswith("|") else 0
            idx = rr + off
            if idx >= len(raw):
                break
            cell = raw[idx]
            m = _RE_RR_CELL.fullmatch(cell.strip())
            if not m or not wrong(_num(m.group("ratio"))):
                break
            lead = cell[:len(cell) - len(cell.lstrip())]
            trail = cell[len(cell.rstrip()):]
            raw[idx] = f"{lead}{m.group('pre') or ''}{actual:.2f}{trail}"
            lines[k] = "|".join(raw)
            n += 1
            break
    return "\n".join(lines), n
