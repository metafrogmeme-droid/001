"""Phase 0 of multi-venue: every trade can say where it happened.

`docs/MULTI_VENUE_RISK_SPLIT.md` names this the one real blocker, and it is not
a nice-to-have. `TradeExecution` and `JournalEntry` carried no venue, so a
closed trade could not say which exchange it was on — and PnL, the track
record, the loss streak, drawdown and Proof-of-PnL all read those records. The
moment a second venue starts trading they would pool two books into one number,
silently, in the direction that flatters the total.

This phase adds ONLY attribution. Nothing trades differently, and the default
back-fills a fact rather than guessing one: every live trade this bot has
placed went to Bitget, and paper marks come from Bitget too.

TWO PATHS, AND ATTRIBUTING ONLY ONE WOULD BE WORSE THAN NEITHER — it would look
done. Paper fills become a `TradeExecution` in the portfolio; LIVE closes never
touch that object at all and are recorded through `journal.record_trade`. The
live half is the half with real money in it.

WHAT THIS PHASE DOES NOT DO, pinned at the bottom so it cannot be mistaken for
done: a position is still keyed by `idea.id`, so one idea can produce exactly
one position. That is correct today and is the blocker for trading two venues
at once — the same idea firing on Bitget and Bybit would have the second
overwrite the first. Identity has to become (idea, venue) before any of that
turns on.
"""

from __future__ import annotations

import json


from bot.core.trade_journal import JournalEntry, TradeJournal
from bot.risk.portfolio import PortfolioTracker
from bot.utils.models import Direction, TradeExecution, TradeIdea


def _idea(idea_id="TI-venue1", asset="BTC/USDT"):
    return TradeIdea(
        id=idea_id, asset=asset, direction=Direction.LONG,
        entry_price=50000.0, stop_loss=49000.0, take_profit=52000.0,
        confidence=0.8, reasoning="phase-0 fixture", source="test",
    )


# ── The field exists, and its default is a fact ───────────────────────────

def test_a_trade_records_where_it_happened():
    assert "venue" in TradeExecution.model_fields
    assert TradeExecution.model_fields["venue"].default == "bitget", (
        "the default back-fills every existing record; anything else would "
        "relabel history")


def test_the_live_journal_records_it_too():
    """The half with real money in it.

    Live closes never become a TradeExecution — they go through
    journal.record_trade. Attributing only the paper path would leave every
    REAL trade venue-blind while looking finished.
    """
    fields = {f: getattr(JournalEntry, f, None) for f in JournalEntry.__annotations__}
    assert "venue" in fields


def test_the_venue_is_not_on_the_idea():
    """An idea is a MARKET read and does not belong to an exchange.

    "BTC long at 50k" is true whichever book you look at; it becomes
    venue-specific only when something executes it. A venue on TradeIdea would
    claim the scanner picked an exchange, which it does not — and would make
    one idea executing on two venues look like a contradiction rather than the
    two executions it is.
    """
    assert "venue" not in TradeIdea.model_fields


# ── It flows, and it survives the round trip ──────────────────────────────

def test_a_paper_fill_carries_the_venue_it_was_given():
    p = PortfolioTracker(initial_balance=10_000.0)
    t = p.open_position(_idea(), size_usd=100.0, venue="bybit")
    assert t.venue == "bybit"


def test_a_paper_fill_defaults_rather_than_blanking():
    # An unlabelled trade is worse than a defaulted one: "" reads as a venue
    # nobody can name, on a record that really did happen somewhere.
    p = PortfolioTracker(initial_balance=10_000.0)
    assert p.open_position(_idea(), size_usd=100.0).venue == "bitget"


def test_the_venue_survives_being_written_and_read_back(tmp_path):
    """Persistence is where an added field quietly disappears.

    A field that round-trips in memory and not through the state file attributes
    nothing after the first restart.
    """
    f = str(tmp_path / "portfolio.json")
    p = PortfolioTracker(initial_balance=10_000.0, state_file=f)
    p.open_position(_idea("TI-persist"), size_usd=100.0, venue="okx")
    p.save_state()

    raw = json.loads(open(f).read())
    assert "okx" in json.dumps(raw), "the venue never reached the file"

    q = PortfolioTracker(initial_balance=10_000.0, state_file=f)
    assert q.load_state() is True, "the state file did not load"
    assert [t.venue for t in q.open_positions] == ["okx"]


def test_a_journal_entry_round_trips_its_venue(tmp_path):
    j = TradeJournal(journal_file=str(tmp_path / "j.json"))
    j.record_trade(
        trade_id="T1", symbol="BTC/USDT", direction="LONG", strategy_type="swing",
        entry_price=100.0, exit_price=110.0, stop_loss=95.0, take_profit=115.0,
        pnl=10.0, venue="hyperliquid")
    assert j._entries[-1].venue == "hyperliquid"

    k = TradeJournal(journal_file=str(tmp_path / "j.json"))
    assert k._entries[-1].venue == "hyperliquid", (
        "the venue was lost on reload — every entry would read as the default "
        "after a restart, which is a relabelling, not a default")


def test_an_old_record_without_a_venue_still_loads(tmp_path):
    """Back-fill, not breakage.

    Every journal file and portfolio state written before this change has no
    venue key. They must load and read as bitget — which is what they are.
    """
    f = tmp_path / "j.json"
    # The journal's on-disk names, which are NOT the dataclass field names —
    # "entry"/"exit"/"sl"/"tp"/"ts". Writing the field names instead produced an
    # empty load and a test that would have passed for the wrong reason.
    f.write_text(json.dumps([{
        "trade_id": "OLD", "symbol": "ETH/USDT", "direction": "LONG",
        "strategy_type": "swing", "entry": 1.0, "exit": 2.0,
        "sl": 0.5, "tp": 3.0, "pnl": 1.0, "pnl_pct": 1.0,
        "r_mult": 1.0, "hold_hrs": 1.0, "ts": 0,
    }]), encoding="utf-8")
    j = TradeJournal(journal_file=str(f))
    assert j._entries and j._entries[0].venue == "bitget"


# ── The live resolver ─────────────────────────────────────────────────────

class _Pos:
    symbol = "BTC/USDT"
    venue = ""


def test_the_resolver_prefers_what_the_position_says():
    from bot.core.engine import RuneClawEngine
    p = _Pos(); p.venue = "gate"
    eng = type("E", (), {})()
    assert RuneClawEngine._venue_of_closed_position(eng, p, "u1") == "gate"


def test_the_resolver_falls_back_rather_than_returning_empty():
    """An unlabelled live trade would look like one from an unknown venue.

    Every trade this bot has actually placed went to Bitget, so the fallback is
    a true statement today. The docstring says plainly that it stops being one
    when a second venue can trade — this is a Phase 0 answer, not a Phase 2 one.
    """
    from bot.core.engine import RuneClawEngine
    eng = type("E", (), {})()          # no _executor_for, no store
    assert RuneClawEngine._venue_of_closed_position(eng, _Pos(), "") == "bitget"


def test_a_label_failure_never_costs_the_record():
    """The bug my own first draft introduced, kept as a test.

    The resolver was called inline inside the journal write's fail-open `try`,
    so anything it raised swallowed the WHOLE trade record. A live close that
    never reaches the journal is precisely the defect
    tests/test_journal_records_live_closes.py exists to stop — reintroduced
    while adding a label to it.
    """
    import inspect
    from bot.core.engine import RuneClawEngine
    src = inspect.getsource(RuneClawEngine._on_live_position_closed)
    resolve = src.index("_venue_of_closed_position")
    record = src.index("self.journal.record_trade(")
    assert resolve < record, (
        "the venue is resolved inside or after the journal write; a resolver "
        "fault can suppress the trade record")
    assert "venue=_venue," in src, (
        "the journal call resolves the venue inline again — put it back behind "
        "its own guard")


# ── What Phase 0 deliberately leaves broken ───────────────────────────────

def test_one_idea_still_makes_only_one_position():
    """THE PHASE 2 BLOCKER, measured rather than asserted in prose.

    `_positions[idea.id] = trade` means identity is the idea. Fire the same
    idea at two venues — exactly what "trade several venues at once" means —
    and the second silently replaces the first. One position, one venue label,
    and a position that was opened and is now untracked.

    This is CORRECT today (only one venue can trade) and it is the thing that
    must change before more than one can. When identity becomes (idea, venue)
    this test should fail, and the fix is to delete it and assert two.
    """
    p = PortfolioTracker(initial_balance=10_000.0)
    idea = _idea("TI-same")
    p.open_position(idea, size_usd=100.0, venue="bitget")
    p.open_position(idea, size_usd=100.0, venue="bybit")

    positions = p.open_positions
    assert len(positions) == 1, (
        "identity is now venue-aware — good. Delete this test and assert that "
        "two venues hold two positions; then check every caller that keys on "
        "trade_id (505 references in bot/, 21 in app/).")
    assert positions[0].venue == "bybit", (
        "the second open replaced the first, which is what makes this the "
        "blocker: the Bitget position is open at the exchange and gone here")
