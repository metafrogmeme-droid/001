"""A Guardian verdict over part of the book is not a verdict on the book.

Both readers that take a position book silently DROPPED any row they could not
price and published the result as the book's verdict — into a card, and through
`twin_payload` / `sentinel_payload` into the tamper-evident chain.

The row that triggers it is ordinary, not a corner: `live_executor` writes
`entry_price=0.0` AND `cost_usd=0.0` for an adopted position whose venue stated
neither, and the restore path reads them back the same way, so the state
survives restarts.

Driven before the fix, on one such book:

    digital_twin.run   -> position_count 1, risk LOW,  drawdown 1.05%, liquidations []
    the same, readable -> position_count 2, risk HIGH, drawdown 36.04%, liquidates PENDLE

    risk_sentinel      -> gross $30.00,    top_group BTC 100%
    the same, readable -> gross $8,430.00, top_group ALT 99.6%

and the twin's card contradicted itself in place — *"1 position(s) · worst-case
LOW"* directly above *"Most fragile: PENDLE/USDT"*, a position no scenario had
simulated, above *"🟢 sealed to the evidence chain"*.

Three claims are driven here, because each fails differently:

1. the READINGS count what they could not price and name it;
2. a verdict over ZERO priced rows is `unknown`, never an all-clear — while an
   EMPTY book stays a real reading of `none`, since those are different facts;
3. the CARDS say so, and say nothing extra on a healthy book.
"""

from __future__ import annotations

import asyncio
import pathlib
from unittest.mock import MagicMock

import pytest

import bot.guardian.book_read as bk
import bot.guardian.digital_twin as dt
import bot.guardian.risk_sentinel as rs
import bot.skills.guardian_commands as gc
from bot.guardian.book_read import BookCoverage, coverage_note, num
from tests.source_scan import code_only

REPO = pathlib.Path(__file__).resolve().parents[1]

#: The exact shape `live_executor` writes for an adopted position the venue
#: priced neither way. Both zeros are independent (`:3767` and `:3768`).
ADOPTED_UNREADABLE = {"symbol": "PENDLE/USDT", "entry": 0.0, "qty": 0.0,
                      "cost_usd": 0.0, "leverage": 20, "group": "ALT",
                      "direction": "LONG"}
READABLE = {"symbol": "BTC/USDT", "entry": 60000.0, "qty": 0.0005,
            "leverage": 2, "group": "BTC", "direction": "LONG"}


def _priced(row: dict) -> dict:
    return {**row, "entry": 4.20, "qty": 238.0, "cost_usd": 50.0}


def test_the_docstrings_figures_are_the_ones_a_drive_returns():
    """`book_read`'s header quotes six figures as the REASON for the design.

    A number in prose is the part that rots first, and this one already had:
    an earlier draft of that header said `$8,430.00 / ALT 99.6% / 281x` from a
    fixture that had since moved, so a reader deciding whether the design was
    worth it would have been reading a measurement nobody could reproduce.
    Same rule `test_claude_md_accuracy` applies to CLAUDE.md, applied to a
    module header — and to the fixtures in THIS file, so the two cannot drift
    apart either.

    Bounded to the bullet block on purpose: the correction underneath has to
    NAME the figures it corrected, and a scan of the whole docstring would
    match the retraction and report it as the defect. *A comment that quotes
    the string it forbids*, which this repo has now hit from the author's side
    four slices running.
    """
    doc = bk.__doc__ or ""
    start = doc.index("* ``digital_twin.run``")
    end = doc.index("Those six figures are not remembered")
    bullets = doc[start:end]
    assert "8,430" not in bullets and "99.6" not in bullets, \
        "the retracted figures are back inside the bullet block"

    partial, whole = [READABLE, ADOPTED_UNREADABLE], [READABLE, _priced(ADOPTED_UNREADABLE)]
    tp, tw = dt.run(partial, 1000.0), dt.run(whole, 1000.0)
    sp, sw = rs.analyze(partial), rs.analyze(whole)
    wp, ww = tp["worst"] or {}, tw["worst"] or {}

    for claim in (
        f"position_count {tp['position_count']}",
        f"risk **{tp['risk']}**",
        f"drawdown {wp['drawdown_pct']}%",
        f"``liquidations {wp['liquidations']}``",
        f"position_count {tw['position_count']}",
        f"risk **{tw['risk'].upper()}**",
        f"{int(ww['drawdown_pct'])}% drawdown",
        f"{ww['liquidations'][0].split('/')[0]} liquidating",
        f"``gross_notional_usd ${sp['gross_notional_usd']:,.2f}``",
        f"``top_group BTC {sp['top_group']['share_pct']}%``",
        f"**${sw['gross_notional_usd']:,.2f}**",
        f"``ALT {sw['top_group']['share_pct']}%``",
        f"{int(sw['gross_notional_usd'] // sp['gross_notional_usd'])}\u00d7 understatement",
    ):
        assert claim in bullets, f"{claim!r} is not what a drive returns"


# ── The readings ──────────────────────────────────────────────────────────


def test_the_sentinel_counts_the_row_it_could_not_price():
    a = rs.analyze([READABLE, ADOPTED_UNREADABLE])
    assert a["counted_positions"] == 2, "the book has two rows"
    assert a["scored_positions"] == 1, "only one of them could be priced"
    assert a["unpriced_symbols"] == ["PENDLE/USDT"], (
        "the dropped row is not named, so no reader can tell the figure is partial")


def test_the_twin_counts_the_row_it_could_not_simulate():
    r = dt.run([READABLE, ADOPTED_UNREADABLE], 1000.0)
    assert (r["scored_positions"], r["counted_positions"]) == (1, 2)
    assert r["unpriced_symbols"] == ["PENDLE/USDT"]


def test_the_twin_verdict_really_does_flip_when_the_row_is_readable():
    """The measurement the whole slice rests on, kept as a test.

    If this ever stops flipping, the dropped row stopped mattering and the
    caveat below is a hedge about nothing.
    """
    partial = dt.run([READABLE, ADOPTED_UNREADABLE], 1000.0)
    whole = dt.run([READABLE, _priced(ADOPTED_UNREADABLE)], 1000.0)
    assert partial["risk"] == "low"
    assert whole["risk"] == "high"
    assert whole["worst"]["liquidations"] == ["PENDLE/USDT"]
    assert partial["worst"]["liquidations"] == []


def test_the_sentinel_gross_and_group_are_over_the_priced_rows_only():
    partial = rs.analyze([READABLE, ADOPTED_UNREADABLE])
    whole = rs.analyze([READABLE, _priced(ADOPTED_UNREADABLE)])
    assert partial["top_group"]["group"] == "BTC"
    assert whole["top_group"]["group"] == "ALT", (
        "the readable book is ALT-dominated; if this does not move, the "
        "concentration card cannot name the wrong group and the defect is gone")
    assert whole["gross_notional_usd"] > partial["gross_notional_usd"] * 10


def test_nothing_priceable_is_not_an_all_clear():
    """`risk: none` over zero priced rows is a verdict assembled from no data."""
    assert rs.analyze([ADOPTED_UNREADABLE])["risk"] == "unknown"
    assert dt.run([ADOPTED_UNREADABLE], 1000.0)["risk"] == "unknown"


def test_an_empty_book_is_still_a_real_reading():
    """The other direction: a flat account MEASURED flat is not "unknown"."""
    assert rs.analyze([])["risk"] == "none"
    assert dt.run([], 1000.0)["risk"] == "none"
    assert rs.analyze([])["counted_positions"] == 0
    assert dt.run([], 1000.0)["counted_positions"] == 0


def test_a_zero_entry_is_an_absence_and_a_real_price_is_not():
    """The predicate's own boundary, both sides.

    `price_on_record` refuses `<= 0` because a price of zero is a level nobody
    stated, and `position_size_basis` documents `cost_usd == 0.0` as the orphan
    case. A fixture on only one side of that measures nothing about it.
    """
    assert rs._notional({"entry": 0.0, "qty": 10.0}) is None
    assert rs._notional({"entry": 0.0, "qty": 0.0, "cost_usd": 0.0}) is None
    assert rs._notional({"entry": 0.01, "qty": 10.0}) == pytest.approx(0.1)
    assert rs._notional({"cost_usd": 50.0, "leverage": 20}) == pytest.approx(1000.0)
    assert not dt._simulable({"entry": 0.0, "qty": 10.0})
    assert dt._simulable({"entry": 0.01, "qty": 10.0})


def test_a_quantity_of_zero_is_an_absence_too():
    """The OTHER field the restore path writes as a zero, and the mutation
    round is what said this fixture was missing.

    `_simulable` refuses `qty == 0` and the corpus had no row that could tell
    that clause from the truthiness expression it replaced, so restoring the
    old `_num(p.get("qty")) is not None` changed no verdict anywhere. It is
    not hypothetical: `live_executor`'s restore reads
    `quantity=float(item.get("quantity") or 0)`, the same or-zero shape as the
    `entry_price` one line above it, so an unstated quantity persists as a
    measured 0.0 across every restart. Counted as simulable it contributes
    `position_pnl(entry, 0, ...)` == 0.0 -- a position the card counts and the
    shock cannot move, which is the failed read rendered as a measured
    zero-risk row.
    """
    assert not dt._simulable({"entry": 60000.0, "qty": 0.0})
    r = dt.run([{"symbol": "BTC/USDT", "entry": 60000.0, "qty": 0.0,
                 "direction": "LONG", "leverage": 10, "group": "BTC"}], 1000.0)
    assert r["position_count"] == 0
    assert r["counted_positions"] == 1
    assert r["unpriced_symbols"] == ["BTC/USDT"]
    assert r["risk"] == "unknown"


def test_the_two_predicates_are_allowed_to_disagree():
    """`book_read` holds the COUNT and deliberately not the per-row question.

    The twin needs an entry AND a quantity to shock; the sentinel needs either
    of two notional bases. A row with a readable entry, NO quantity and a
    readable margin is the input where that difference is a fact rather than a
    preference -- the sentinel can size it, the twin cannot move it -- and
    folding the two into one shared predicate would be a second answer about
    what "priced" means. Pinned because the claim is in the module's own
    docstring and nothing drove it.
    """
    row = {"symbol": "ARB/USDT", "entry": 0.42, "qty": 0.0,
           "cost_usd": 50.0, "leverage": 20, "direction": "LONG", "group": "ALT"}
    assert rs._priced(row)
    assert not dt._simulable(row)
    assert rs.analyze([row])["scored_positions"] == 1
    assert dt.run([row], 1000.0)["scored_positions"] == 0


def test_the_seal_carries_what_the_verdict_covers():
    """Both payloads reach the tamper-evident chain; a partial verdict sealed
    as the book's verdict is permanent."""
    book = [READABLE, ADOPTED_UNREADABLE]
    for payload in (dt.twin_payload(book, 1000.0), rs.sentinel_payload(book)):
        assert payload["counted_positions"] == 2
        assert payload["scored_positions"] == 1
        assert payload["unpriced_symbols"] == ["PENDLE/USDT"]


def test_num_has_one_definition():
    """`_num` was byte-identical in both Guardian modules.

    A second copy of a reading is a second answer the moment one is edited, and
    these two sit directly under the verdicts this file exists to keep honest.
    """
    for mod in ("digital_twin", "risk_sentinel"):
        src = code_only((REPO / "bot" / "guardian" / f"{mod}.py").read_text())
        assert "def _num(" not in src, (
            f"{mod} grew its own _num back — import it from book_read")
    assert num(float("nan")) is None and num("x") is None and num("3") == 3.0


# ── The note ──────────────────────────────────────────────────────────────


def test_the_note_is_silent_on_a_complete_book():
    assert coverage_note(BookCoverage(2, 2, ())) == ""


def test_the_note_speaks_when_NOTHING_was_read():
    """Deliberately unlike `committed_margin_note`, which goes silent there.

    There the figure beside it is already an em dash, so a caveat would be a
    hedge about a figure that is not there. Here the verdict still renders as
    real numbers — `gross $0.00`, `drawdown 0.0%`, `liquidations []` — so
    silence is the all-clear this module exists to remove.
    """
    note = coverage_note(BookCoverage(0, 3, ("A", "B", "C")))
    assert note, "silent over a book where nothing could be priced"
    assert "none of the 3" in note and "A" in note


def test_the_note_names_at_most_four_and_counts_the_rest():
    note = coverage_note(BookCoverage(0, 6, ("A", "B", "C", "D", "E", "F")))
    assert "and 2 more" in note and "E" not in note


# ── The cards ─────────────────────────────────────────────────────────────


def _render(method, report) -> str:
    host = MagicMock()
    sent: list[str] = []

    async def _send(update, text, **kw):
        sent.append(text)

    host._send = _send
    host._is_admin = MagicMock(return_value=True)
    host._lang = MagicMock(return_value="en")
    host.engine = MagicMock()
    host.engine.run_digital_twin = MagicMock(return_value=report)
    host.engine.run_risk_sentinel = MagicMock(return_value=report)
    asyncio.new_event_loop().run_until_complete(
        method(host, MagicMock(), MagicMock()))
    return sent[0] if sent else ""


def test_both_cards_name_the_row_they_could_not_price():
    book = [READABLE, ADOPTED_UNREADABLE]
    twin = _render(gc.GuardianCommands._cmd_twin, dt.run(book, 1000.0))
    sent = _render(gc.GuardianCommands._cmd_sentinel, rs.analyze(book))
    for card in (twin, sent):
        assert "1 of 2 open position(s)" in card
        assert "PENDLE/USDT could not be priced" in card


def test_the_twin_card_no_longer_contradicts_itself():
    """"Most fragile: PENDLE/USDT" over "1 position(s)" with nothing between
    them told the reader a position was both the book's worst risk and not in
    the book. The fragility row is a REAL reading (leverage was read) and
    stays; what was missing is the sentence that makes the two coherent."""
    card = _render(gc.GuardianCommands._cmd_twin,
                   dt.run([READABLE, ADOPTED_UNREADABLE], 1000.0))
    assert "Most fragile" in card and "PENDLE/USDT" in card
    assert card.index("could not be priced") < card.index("Most fragile"), (
        "the caveat must come BEFORE the row it explains")


def test_a_healthy_card_says_nothing_extra():
    """A permanent "2 of 2" is the row that trains a reader to skip the line."""
    twin = _render(gc.GuardianCommands._cmd_twin, dt.run([READABLE], 1000.0))
    sent = _render(gc.GuardianCommands._cmd_sentinel, rs.analyze([READABLE]))
    for card in (twin, sent):
        assert "could not be priced" not in card
        assert "describe no part" not in card
    assert twin.count("drawdown") == 4, "the healthy card lost its scenarios"


def test_the_twin_card_omits_scenario_rows_it_could_not_measure():
    """Four green "drawdown 0.0%" rows over zero simulated positions is a
    claim that this book survives a flash crash. Colour is a claim."""
    card = _render(gc.GuardianCommands._cmd_twin,
                   dt.run([ADOPTED_UNREADABLE], 1000.0))
    assert "drawdown" not in card, (
        "scenario rows were painted over a book nothing was simulated from")
    assert "describe no part of this book" in card
    assert "UNKNOWN" in card


def test_the_sentinel_flat_branch_asks_the_BOOK_not_the_priced_subset():
    """`position_count` is now the PRICED count, so a branch keyed on it would
    answer "no open positions to assess" about a book full of them — the
    confident negative this slice exists to remove, rebuilt inside the cure."""
    card = _render(gc.GuardianCommands._cmd_sentinel,
                   rs.analyze([ADOPTED_UNREADABLE]))
    assert "no open positions to assess" not in card
    # NOT `"could not be priced"`: the nothing-read sentence reads "none of the
    # 1 open position(s) could be priced", so the negation moves the words —
    # this file's own recorded trap, met while writing it.
    assert "none of the 1 open position(s) could be priced" in card


def test_the_sentinel_all_clear_is_scoped_to_what_was_priced():
    """"Book looks diversified" is a claim about the book."""
    # Genuinely diversified, or the all-clear branch is unreachable and the
    # test measures a concern instead: three groups (top share 33.3%, under
    # the 35% medium bar) and mixed directions (net bias 0.33, under 0.60).
    clean = [
        {"symbol": "A", "entry": 10.0, "qty": 1.0, "group": "GA", "direction": "LONG"},
        {"symbol": "B", "entry": 10.0, "qty": 1.0, "group": "GB", "direction": "LONG"},
        {"symbol": "C", "entry": 10.0, "qty": 1.0, "group": "GC", "direction": "SHORT"},
    ]
    whole = _render(gc.GuardianCommands._cmd_sentinel, rs.analyze(clean))
    assert "Book looks diversified" in whole, (
        "the fixture trips a concern, so it never reaches the all-clear branch")

    partial = _render(gc.GuardianCommands._cmd_sentinel,
                      rs.analyze(clean + [ADOPTED_UNREADABLE]))
    assert "Book looks diversified" not in partial, (
        "an all-clear about 'the book' over a book one row of which was "
        "never read")
    assert "positions that could be priced" in partial


def test_the_concentration_sentence_does_not_claim_the_whole_book():
    a = rs.analyze([READABLE, ADOPTED_UNREADABLE])
    details = " ".join(c.get("detail", "") for c in a["concerns"])
    assert "of the book" not in details, (
        "the share is over the PRICED rows; on a partial book that is not the "
        "book, and the sentence must not make the wider claim")
    assert "of gross notional" in details
