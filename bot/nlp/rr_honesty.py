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

ONE SETUP AT A TIME. The first version read the FIRST entry/stop/target on
the page (`search`, not `finditer`) and rewrote EVERY stated ratio to that
one number. A reply carrying two setups — a BTC long at 2.0 and an ETH short
at 3.0, both true — came out with the second ratio overwritten by the first
setup's, and an `rr_corrected` audit line saying a lie had been fixed. A
level stated with two different values is two trades, not one trade read
twice (`AMBIGUOUS`): no single ratio is attributable to the page, so the
page is read one paragraph at a time, each against its own levels, and a
table one ROW at a time, each against its own cells. A ladder of targets
(TP1/TP2) is the same ambiguity inside one setup.
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

#: More than one trade's levels on the page — two setups, a ladder of
#: targets, a multi-row table: no single ratio can be attributed to any of
#: them. `computed_ratio` answers None for it (cannot check), and
#: `correct_stated_rr` reads each paragraph, and each table row, on its own.
AMBIGUOUS = object()

#: A ladder of targets in one setup: "TP1 … TP2 …", "target 2", "take profit
#: 2". A ladder has more than one reward per risk, and a stated ratio names
#: its rung or does not, so no one number can be checked against it. The
#: digit must not start a price ("target 2.50" on a $2 coin is a level).
_RE_LADDER = re.compile(r"\b(?:TP|target|take[\s_]?profit)[\s_-]*[2-9](?![.,]?\d)\b",
                        re.IGNORECASE)

#: Where one setup ends and the next begins, for the paragraph-by-paragraph
#: read: a blank line, or a heading line — markdown `#`, a line that is only
#: bold text, or "Setup 2" / "Trade 2" / "Idea 2". Over-splitting can only
#: LOSE a correction (a paragraph without a full triple is left alone), never
#: attach one to the wrong setup, which is why the whole page is tried first.
_RE_SEG = re.compile(
    r"(\n[ \t]*\n"
    r"|\n(?=[ \t]*(?:#{1,6}[ \t]|\*\*[^\n*]+\*\*[ \t]*\n"
    r"|(?:setup|trade|idea|scenario|option)[ \t]*#?\d+\b)))",
    re.IGNORECASE)


def _cells(line: str):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _cell_num(cell: str):
    """First number in a table cell, so "$59,500" and "59500 USDT" both read."""
    m = re.search(_NUM, cell)
    return _num(m.group(1)) if m else None


def _table_columns(head):
    """Column indexes of a header row, or None when it is not a levels
    table. ``ladder`` is True when more than one target column exists."""
    def col(*keys):
        for j, c in enumerate(head):
            if any(k in c for k in keys):
                return j
        return None

    i_e, i_s, i_t = col("entry"), col("stop"), col("take profit", "target")
    if i_e is None or i_s is None or i_t is None:
        return None
    return {"entry": i_e, "sl": i_s, "tp": i_t,
            "dir": col("direction", "side"),
            "ladder": sum(1 for c in head if "take profit" in c or "target" in c) > 1}


def _row_levels(cols, cells):
    """(entry, sl, tp, direction) from one value row; None for a level the
    row does not hold. Direction is the row's own cell when the table has
    that column, else None (the caller may read a Direction line)."""
    if max(cols["entry"], cols["sl"], cols["tp"]) >= len(cells):
        return None, None, None, None
    d = None
    if cols["dir"] is not None and cols["dir"] < len(cells):
        m = re.search(r"\b(LONG|SHORT)\b", cells[cols["dir"]], re.IGNORECASE)
        d = m.group(1).upper() if m else None
    return (_cell_num(cells[cols["entry"]]), _cell_num(cells[cols["sl"]]),
            _cell_num(cells[cols["tp"]]), d)


def _table_rows(lines, i):
    """The value rows under header line ``i``: (line_index, cells) for every
    pipe line up to the first line without a pipe, separators skipped."""
    rows = []
    for k in range(i + 1, len(lines)):
        nxt = lines[k]
        if "|" not in nxt:
            break
        if _RE_TABLE_SEP.match(nxt):
            continue
        rows.append((k, _cells(nxt)))
    return rows


def _levels_from_table(text: str):
    """(entry, sl, tp, direction) from a pipe table whose labels are in a
    header ROW; None when there is no such table, a needed column is missing
    or a cell holds no number — never a partial reading; AMBIGUOUS when the
    table has more than one value row (two trades) or more than one target
    column (a ladder).

    The label regexes look for a label adjacent to its number. A table puts
    every label on one line and every value on another, so adjacency is gone
    and each of them reads the header text as its number-that-isn't. Columns
    are matched by POSITION, which is the only thing that actually ties a
    header cell to a value cell:

        Entry   | Stop Loss | Take Profit | R:R
        --------|-----------|-------------|-----
        $59,500 | $58,500   | $61,000     | 1:1.70
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if "|" not in line:
            continue
        cols = _table_columns([c.lower() for c in _cells(line)])
        if cols is None:
            continue
        rows = _table_rows(lines, i)
        if cols["ladder"] or len(rows) > 1:
            return AMBIGUOUS
        if not rows:
            return None
        e, s, t, d = _row_levels(cols, rows[0][1])
        return None if None in (e, s, t) else (e, s, t, d)
    return None


def _distinct(nums):
    return sorted({n for n in nums if n is not None})


def _prose_levels(text: str):
    """(entry, sl, tp) from labelled levels in prose; None when one is
    missing or unreadable; AMBIGUOUS when a level is stated with more than
    one distinct value — the page describes more than one trade, and the
    first triple on it is not "the" setup. A level repeated with the SAME
    value (the header and the check list both say Entry 100) is one level."""
    es = _distinct(_num(m.group(1)) for m in _RE_ENTRY.finditer(text))
    ss = _distinct(_num(m.group(1)) for m in _RE_SL.finditer(text))
    ts = _distinct(_num(m.group(1)) for m in _RE_TP.finditer(text))
    if not (es and ss and ts):
        return None
    if len(es) > 1 or len(ss) > 1 or len(ts) > 1:
        return AMBIGUOUS
    return es[0], ss[0], ts[0]

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


def _ratio(entry, sl, tp, direction):
    """reward / risk for ONE set of levels, or None when it cannot be read.

    Direction is inferred only when the geometry is unambiguous — target and
    stop on opposite sides of entry. Guessing a direction would make up the
    sign of every number downstream.
    """
    if entry is None or sl is None or tp is None:
        return None
    if direction is None:
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


def levels(text: str):
    """ONE trade's levels on the page — (entry, sl, tp, direction or None) —
    or None when they cannot be read, or AMBIGUOUS when the page holds more
    than one trade's (two setups, a multi-row table, a target ladder).

    A pipe table is tried FIRST. Its header row contains the words the label
    regexes hunt for, so leaving it to them means they match the header and
    read whatever number follows it on the page — a wrong reading rather
    than no reading, which is the worse of the two.
    """
    if not text:
        return None
    table = _levels_from_table(text)
    if table is AMBIGUOUS:
        return AMBIGUOUS
    if table is not None:
        entry, sl, tp, direction = table
    else:
        prose = _prose_levels(text)
        if prose is None or prose is AMBIGUOUS:
            return prose
        entry, sl, tp = prose
        direction = None
    if _RE_LADDER.search(text):
        return AMBIGUOUS
    if direction is None:
        m_dir = _RE_DIR.search(text)
        direction = m_dir.group(1).upper() if m_dir else None
    return entry, sl, tp, direction


def computed_ratio(text: str):
    """The risk:reward the text's own levels imply, or None if unreadable —
    or if the text holds more than one trade's levels, because a ratio the
    page states is then attributable to none of them in particular.

    None is a real answer here and is treated as one by every caller: it
    means the claim cannot be checked, not that it is wrong.
    """
    lv = levels(text)
    if lv is None or lv is AMBIGUOUS:
        return None
    return _ratio(*lv)


def _wrong(stated, actual):
    """True only for a plausible ratio that disagrees with the levels."""
    if stated is None:
        return False
    if not _PLAUSIBLE[0] <= stated <= _PLAUSIBLE[1]:
        return False
    return abs(stated - actual) > TOLERANCE


def _correct_prose(text: str, actual: float):
    """Rewrite the prose ratio shapes that disagree with ``actual``."""
    corrected = 0

    def _repl(m):
        nonlocal corrected
        if not _wrong(_num(m.group("ratio")), actual):
            return m.group(0)
        corrected += 1
        # The "1:" prefix is kept when the model used it, so a corrected
        # line reads like the rest of the report rather than like a patch.
        return f"{m.group('label')}{m.group('mid')}{actual:.2f}"

    def _repl_pre(m):
        nonlocal corrected
        if not _wrong(_num(m.group("ratio")), actual):
            return m.group(0)
        corrected += 1
        return f"{m.group('pre') or ''}{actual:.2f}{m.group('label')}"

    def _repl_quot(m):
        nonlocal corrected
        if not _wrong(_num(m.group("ratio")), actual):
            return m.group(0)
        corrected += 1
        return f"{m.group('expr')}{actual:.2f}"

    text = _RE_STATED.sub(_repl, text)
    text = _RE_STATED_PRE.sub(_repl_pre, text)
    text = _RE_STATED_QUOT.sub(_repl_quot, text)
    return text, corrected


def _correct_table_rr(text: str, actual):
    """Rewrite R:R cells. Returns (text, n).

    A table cell holds the bare ratio — "1:1.70" with the label sitting in a
    header row two lines up — so neither prose pattern can reach it. Columns
    are matched by position, same as the level reader, and only a cell that
    is ENTIRELY a ratio is touched; anything else is left alone rather than
    guessed at.

    A row that carries its own entry/stop/target cells is checked against
    ITS OWN levels — a two-row table is two trades, and the first row's
    ratio is not the second's. A table with an R:R column and no level
    columns is checked against ``actual``, the page's single setup, when the
    page has one (``None`` when it does not: nothing is rewritten).
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
        cols = _table_columns(head)
        if cols is not None and cols["ladder"]:
            continue
        for k, cells in _table_rows(lines, i):
            nxt = lines[k]
            raw = nxt.split("|")
            off = 1 if nxt.lstrip().startswith("|") else 0
            idx = rr + off
            if idx >= len(raw):
                continue
            row_actual = _ratio(*_row_levels(cols, cells)) if cols is not None else actual
            if row_actual is None:
                continue
            cell = raw[idx]
            m = _RE_RR_CELL.fullmatch(cell.strip())
            if not m or not _wrong(_num(m.group("ratio")), row_actual):
                continue
            lead = cell[:len(cell) - len(cell.lstrip())]
            trail = cell[len(cell.rstrip()):]
            raw[idx] = f"{lead}{m.group('pre') or ''}{row_actual:.2f}{trail}"
            lines[k] = "|".join(raw)
            n += 1
    return "\n".join(lines), n


def _correct_segment(seg: str):
    """Correct one paragraph against its own levels. Returns (seg, n).

    A paragraph that itself holds more than one trade has only its table
    rows corrected — each carries its own levels — and its prose ratios are
    left exactly as written: they belong to one of two setups and nothing on
    the page says which.
    """
    lv = levels(seg)
    if lv is AMBIGUOUS:
        return _correct_table_rr(seg, None)
    # `computed_ratio`, not `_ratio(*lv)` inline: the public reading and the
    # one the guard acts on have to be the same derivation, or the answer a
    # caller can ask for is a second answer. It re-reads `levels` — a regex
    # pass over one paragraph — and that is the price of there being one.
    actual = computed_ratio(seg)
    if actual is None:
        return seg, 0
    seg, n_cells = _correct_table_rr(seg, actual)
    seg, n_prose = _correct_prose(seg, actual)
    return seg, n_cells + n_prose


def correct_stated_rr(text: str):
    """Replace ratios the text's own levels contradict. Returns (text, n).

    `n` is how many statements were corrected — 0 when the text made no
    ratio claim, when its levels are unreadable, or when the claim already
    agrees with them. A page holding more than one trade's levels is read
    one paragraph at a time, each against its own levels; a paragraph whose
    setup cannot be told apart from its neighbour's is left alone.
    """
    if not text:
        return text, 0
    if levels(text) is not AMBIGUOUS:
        return _correct_segment(text)
    parts = _RE_SEG.split(text)
    total = 0
    for j in range(0, len(parts), 2):
        parts[j], n = _correct_segment(parts[j])
        total += n
    return "".join(parts), total
