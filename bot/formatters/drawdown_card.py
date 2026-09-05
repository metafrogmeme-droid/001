"""The drawdown-backstop block, extracted so a failed read cannot render blank.

`/drawdownlimit` is the command that decides how much real money the bot loses
before it halts. Its status block was built inline:

    st = {}
    try:
        st = self.engine.risk.drawdown_status()
    except Exception:
        st = {}
    lines = ["📉 <b>Live drawdown backstop</b>"]
    if st:
        ...
    return lines

`drawdown_status()` is itself documented "best-effort; returns empty on any
error", so there are two layers of swallow and one outcome: **a heading with
nothing underneath it**. Not a guard (nothing raises, no error state is
painted) and not an honest omission (the section still announces itself) —
CLAUDE.md's table says pick one, and an empty section reads as the third thing
it warns about: "there is nothing to report", i.e. no drawdown worth naming.

WHERE IT LANDS. The block is printed three times, and the worst is the
confirmation after an operator SETS a looser cap, directly above

    "Real money is down — a looser cap means the bot tolerates MORE LOSS
     before halting."

So the operator loosens the backstop, reads the confirmation, and the backstop
section is silently blank. Nothing tells them whether the override took.

WHY THE SOURCE LABEL IS NOW PRINTED. `drawdown_status()` computes
`drawdown_source` — "live" or "paper" — and carries a comment explaining that
this reporter "used to return the paper number while its own docstring promised
'the drawdown the breaker actually gates on', so an operator could read ~0%
from a gate that was refusing trades at 9%". The engine went to the trouble of
labelling which number it is; the card dropped the label, leaving the reader
unable to tell an enforced live figure from a paper snapshot.
"""

from __future__ import annotations

from typing import Optional

HEADING = "📉 <b>Live drawdown backstop</b>"

#: How the enforced figure was obtained. Printed because "3.2%" means two very
#: different things depending on which one it is.
_SOURCE_TEXT = {
    "live": "live equity high-water mark",
    "paper": "paper snapshot",
}


def _pct(v: Optional[float]) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.1f}%"
    except (TypeError, ValueError):
        return "—"


def drawdown_tile(dd: object) -> tuple:
    """(value, colour) for the /risk PNG's drawdown tile.

    A seam, because the tile was built inline and the only test that could
    reach it was a source scan — which duly passed against a mutation that
    reintroduced the defect under a different variable name.

    `"red" if dd > 0 else "green"` painted an unreadable drawdown GREEN, and
    on a tile labelled "Current Drawdown" green is an all-clear. 0.0 keeps its
    green: a measured flat book is a measurement, and it is the most common
    state the bot is ever in.
    """
    known = isinstance(dd, (int, float)) and not isinstance(dd, bool)
    if not known:
        return "--", "gray"
    return f"{dd:.1f}%", ("red" if dd > 0 else "green")


#: What the daily report prints when the drawdown could not be read.
UNKNOWN_RISK = "Unknown"


def live_risk_status(st: Optional[dict]) -> tuple:
    """``(drawdown_pct, verdict)`` for the LIVE daily report. Tri-state.

    `/daily_report`'s live branch had no reading at all behind these two:

        dd = 0.0
        risk_status = "Healthy"

    — hardcoded, under a shield icon, on real money, while the PAPER branch
    directly beneath it computed both from a snapshot. So the one branch that
    could not afford to guess was the one that did.

    `st` is `risk.drawdown_status()`'s return, documented "best-effort;
    returns empty on any error", so `{}`/None is the unreadable case and gets
    ``(None, "Unknown")`` — never the calmest of the three verdicts.

    The bands come off ``effective_limit_pct``, the limit the breaker is
    ACTUALLY enforcing, rather than the two bare constants the paper branch
    carries: a fixed "Critical above 3%" is meaningless against a 7% live cap
    and would read Critical on a book the gate is happy with.
    """
    if not st:
        return None, UNKNOWN_RISK
    dd = st.get("drawdown_pct")
    if not isinstance(dd, (int, float)) or isinstance(dd, bool) or dd != dd:
        return None, UNKNOWN_RISK
    limit = st.get("effective_limit_pct")
    if not isinstance(limit, (int, float)) or isinstance(limit, bool) or limit <= 0:
        # A drawdown with no limit to judge it against is a number, not a
        # verdict. Report the number and decline the verdict.
        return float(dd), UNKNOWN_RISK
    dd = float(dd)
    if dd >= limit:
        return dd, "Critical"
    if dd >= limit * (2.0 / 3.0):
        return dd, "Warning"
    return dd, "Healthy"


def render_drawdown_status(st: Optional[dict]) -> list:
    """The backstop block. Never a bare heading.

    `st` is `risk.drawdown_status()`'s return — `{}` or None when it could not
    be read, which is the case this exists for.
    """
    if not st:
        # NOT an empty section. This is the whole point of the file: the read
        # failed, and a blank block under a heading reads as "nothing to
        # report" on the control that stops real-money losses.
        return [
            HEADING,
            "• <b>Could not be read.</b>",
            "<i>The drawdown state is unknown — this is a failed read, not a "
            "flat equity curve, and it does not mean the backstop is clear. "
            "Any override shown elsewhere in this message may or may not be "
            "in force.</i>",
            "<i>Check <code>/status</code> and <code>/risk</code>; if those "
            "are also blank the risk engine is not answering.</i>",
        ]

    lines = [HEADING]

    src = st.get("drawdown_source")
    src_txt = _SOURCE_TEXT.get(src)
    dd = st.get("drawdown_pct")
    lines.append(
        f"• Current drawdown: <b>{_pct(dd)}</b>"
        + (f" <i>({src_txt})</i>" if src_txt else ""))

    lines.append(f"• Limit in force: <b>{_pct(st.get('effective_limit_pct'))}</b>")

    ov = st.get("override_pct")
    default_txt = _pct(st.get("config_live_limit_pct"))
    lines.append(
        f"• Override: <b>{_pct(ov)}</b> (default {default_txt})"
        if ov is not None else
        f"• Override: <b>none</b> (default {default_txt})")

    # `live_hardening` absent would render this warning from no information at
    # all, so it is only claimed when the key is actually present. A dict this
    # far along always carries it; the explicit test is so a future partial
    # payload cannot quietly manufacture an OFF.
    if "live_hardening" in st and not st.get("live_hardening"):
        lines.append("• ⚠️ Live hardening OFF — override only bites on live.")
    return lines


def enforced_drawdown(st: Optional[dict]) -> tuple:
    """``(pct, source, limit_pct)`` — the drawdown the BREAKER is enforcing.

    A seam, because four surfaces asked this question and three answered it
    differently. `/status` folded an unreadable paper figure to 0.0, then
    replaced it only when `drawdown_source == "live"` — so a failed read left
    the PAPER number standing while the limit beside it stayed the LIVE cap.
    `CheckRiskSkill._status` and `._risk` never called `drawdown_status()` at
    all and scored Health straight off the paper snapshot, which in pure-live
    operation never moves. CLAUDE.md records the consequence in an operator's
    own words: reading "~0% from a gate that was refusing trades at 9%".

    `source` is the load-bearing return value and the reason this is a triple.
    Falling back to the paper number is a defensible fail-safe — blanking the
    line on a transient read failure is worse — but it is only defensible if
    the card SAYS SO. An unattributable percentage is not a measurement of
    anything the reader can name.

    Every field is validated independently: a payload can carry a good limit
    and an unreadable drawdown, and folding the pair together would discard
    the half that arrived. `st` is `risk.drawdown_status()`'s return,
    documented "best-effort; returns empty on any error", so `{}`/None is the
    unreadable case — ``(None, None, None)``, never a zero.
    """
    if not st:
        return None, None, None

    dd = st.get("drawdown_pct")
    # `dd != dd` is the NaN test: NaN is a float and passes isinstance, and
    # every comparison against it is False, so it would slip through a band
    # check as "below the limit" — the calmest answer, from a broken read.
    if (not isinstance(dd, (int, float)) or isinstance(dd, bool)
            or dd != dd):
        dd = None
    else:
        dd = float(dd)

    limit = st.get("effective_limit_pct")
    if (not isinstance(limit, (int, float)) or isinstance(limit, bool)
            or limit != limit or limit <= 0):
        limit = None
    else:
        limit = float(limit)

    # An unrecognised source is not "live". Only the two vocabulary words
    # licence a claim about where the number came from.
    src = st.get("drawdown_source")
    if src not in _SOURCE_TEXT:
        src = None
    return dd, src, limit


def drawdown_source_note(source: Optional[str]) -> str:
    """The parenthetical that makes a drawdown figure attributable.

    Deliberately non-empty for the unknown case. An omitted note reads as
    "the usual source", which is the assumption that made the number
    unattributable in the first place.
    """
    if source is None:
        return " (source unknown)"
    txt = _SOURCE_TEXT.get(source)
    return f" ({txt})" if txt else " (source unknown)"


def resolve_display_drawdown(paper_pct: object, status: Optional[dict],
                             default_limit: float) -> tuple:
    """``(pct, source, limit)`` for a card, from the paper figure AND the gate.

    The seam three surfaces needed and none had. `/status` open-coded this;
    `CheckRiskSkill._status` and `._risk` skipped the gate entirely and scored
    off the paper snapshot alone. Open-coded logic in three places is three
    chances to disagree, and they did.

    THE ORDER IS THE CONTRACT, and it is a fail-safe rather than a preference:
    the paper figure is seeded first and kept when the gate cannot be read,
    because blanking the line on a transient fault is worse than showing a
    stale-but-real number. The live figure OVERRIDES it only when the gate
    actually reports one. What was missing is the third return value — with no
    source the reader cannot tell which of those two happened, and the paper
    number does not move in pure-live operation.

    `paper_pct` is validated rather than trusted: `if state.max_drawdown_pct
    else 0.0` was falsy, so an unreadable paper drawdown became a measured
    0.0% — the calmest possible reading, manufactured, on the control that
    decides how much real money is lost before the bot halts.
    """
    if (isinstance(paper_pct, (int, float)) and not isinstance(paper_pct, bool)
            and paper_pct == paper_pct):
        pct: Optional[float] = round(float(paper_pct), 2)
        src: Optional[str] = "paper"
    else:
        pct, src = None, None

    live_pct, live_src, limit = enforced_drawdown(status)
    if live_src == "live" and live_pct is not None:
        pct, src = round(live_pct, 2), "live"
    return pct, src, (limit if limit is not None else default_limit)
