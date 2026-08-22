"""Phase 3 of multi-venue: caps counted per PERSON, breakers left per VENUE.

The decision, made deliberately: **caps and drawdown per person, breakers per
venue.** A cap is a statement about how much of your money is at risk, and
money is not per-venue. A breaker is a statement about a book behaving badly,
and a book is.

The scope calls this the highest-risk phase — "this is where a cap can be
silently loosened" — and the loosening has no symptom. Two venues each holding
their own "max 5 open positions" is ten positions against one person's money;
the cap passes on both engines, every existing test stays green, and the only
evidence is twice the exposure the operator asked for.

The subtler half is the MISSING ADDEND. A venue whose book cannot be read is
unknown, not zero, and a sum that quietly drops it is not a sum — it is a
FLOOR. A floor can prove somebody is over a cap. It can never prove they are
under one, and treating it as though it can is how a timeout loosens a limit.
"""
from __future__ import annotations

import pytest

from bot.risk.venue_aggregate import PersonTotals, VenueReading, aggregate, cap_verdict


def _r(venue, pos=0, eq=100.0, daily=0.0):
    return VenueReading(venue=venue, open_positions=pos, equity_usd=eq,
                        daily_pnl_usd=daily)


# ── the addition ─────────────────────────────────────────────────────────

def test_positions_are_counted_across_every_venue():
    """The loosening this phase exists to prevent, stated as arithmetic."""
    t = aggregate([_r("bitget", pos=3), _r("bybit", pos=4)])
    assert t.open_positions == 7, (
        "each venue counted its own positions, so one person's 'max 5' became "
        "5 per venue")
    assert t.complete


def test_equity_and_daily_pnl_are_person_level_too():
    t = aggregate([_r("bitget", eq=1000.0, daily=-20.0),
                   _r("bybit", eq=500.0, daily=5.0)])
    assert t.equity_usd == pytest.approx(1500.0)
    assert t.daily_pnl_usd == pytest.approx(-15.0)


def test_one_venue_is_the_same_answer_it_always_was():
    """The property that makes this shippable: with a single book, the
    person-level total IS that book, so nothing changes for anybody who has
    not connected a second venue."""
    t = aggregate([_r("bitget", pos=2, eq=900.0, daily=-3.0)])
    assert (t.open_positions, t.equity_usd, t.daily_pnl_usd) == (2, 900.0, -3.0)
    assert t.complete


def test_no_venues_at_all_is_not_a_measured_zero():
    """`equity_usd: 0.0` over no readings is the "absent is never a
    measurement" shape — downstream it reads as a real, empty account."""
    t = aggregate([])
    assert t.open_positions == 0
    assert t.equity_usd is None, "no reading was reported as $0.00 of equity"
    assert t.daily_pnl_usd is None


# ── the missing addend ───────────────────────────────────────────────────

def test_an_unreadable_venue_is_unknown_not_zero():
    """Scope §7: "A venue whose balance cannot be read is unknown, not 0 — an
    unreadable balance must not read as 'no margin here' or, worse, as free
    margin.\""""
    t = aggregate([_r("bitget", pos=2, eq=1000.0), VenueReading(venue="bybit")])
    assert "bybit" in t.unreadable
    assert not t.complete
    # What WAS read is still kept — omitting the venue is right, discarding
    # the measurement that succeeded is not.
    assert t.open_positions == 2
    assert t.equity_usd == pytest.approx(1000.0)


def test_a_partial_answer_still_marks_the_total_incomplete():
    """A venue can report positions while its equity read times out. The
    position count is kept; the total is still not whole."""
    t = aggregate([_r("bitget", pos=1),
                   VenueReading(venue="bybit", open_positions=2,
                                equity_usd=None, daily_pnl_usd=0.0)])
    assert t.open_positions == 3
    assert "bybit" in t.unreadable, (
        "a venue that answered SOME fields was treated as fully read")


# ── the verdict, which is where a floor gets misused ─────────────────────

def test_over_the_cap_is_a_refusal_even_when_the_reading_is_incomplete():
    """A floor OVER the cap proves the breach whatever the unread venue holds.
    Refusing here needs no completeness."""
    t = aggregate([_r("bitget", pos=5), VenueReading(venue="bybit")])
    ok, reason = cap_verdict(t.open_positions, 5, t, "MAX_POSITIONS")
    assert ok is False
    assert "5 >= 5" in reason


def test_under_the_cap_on_an_incomplete_reading_is_also_a_refusal():
    """THE test for this phase. A floor UNDER the cap proves nothing: the
    venue that did not answer is exactly the one that might hold the position
    putting this person over. Allowing here is how a timeout loosens a limit."""
    t = aggregate([_r("bitget", pos=1), VenueReading(venue="bybit")])
    ok, reason = cap_verdict(t.open_positions, 5, t, "MAX_POSITIONS")
    assert ok is False, "an unverifiable count was allowed through the cap"
    assert "floor" in reason.lower()
    assert "bybit" in reason, "the refusal does not say WHICH venue went unread"


def test_under_the_cap_on_a_complete_reading_is_allowed():
    """The other half. Refusing everything is not safety, it is a broken bot."""
    t = aggregate([_r("bitget", pos=1), _r("bybit", pos=1)])
    ok, reason = cap_verdict(t.open_positions, 5, t, "MAX_POSITIONS")
    assert ok is True, reason


def test_nothing_measured_is_refused_and_says_so():
    ok, reason = cap_verdict(None, 5, PersonTotals(unreadable=("bybit",)), "DAILY_LOSS")
    assert ok is False
    assert "not measured" in reason


def test_no_cap_configured_is_not_a_cap_of_zero():
    ok, _ = cap_verdict(9999, None, aggregate([_r("bitget", pos=9999)]), "X")
    assert ok is True, "an absent cap was enforced as a cap of zero"


# ── the reader ───────────────────────────────────────────────────────────

@pytest.fixture
def multi(tmp_path, monkeypatch):
    from bot.risk.multi_portfolio import MultiUserPortfolio
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("bot.utils.paths.REPO_ROOT", tmp_path, raising=False)
    return MultiUserPortfolio(default_balance=1000.0)


def _idea(asset):
    from bot.utils.models import Direction, TradeIdea
    return TradeIdea(asset=asset, direction=Direction.LONG, entry_price=100.0,
                     stop_loss=95.0, take_profit=115.0, confidence=0.8, reasoning="t")


def test_the_reader_sees_both_books_and_only_this_users(multi):
    multi.get("alice").open_position(_idea("BTC/USDT"), 100.0)
    multi.get("alice", "bybit").open_position(_idea("ETH/USDT"), 100.0)
    multi.get("bob").open_position(_idea("SOL/USDT"), 100.0)

    t = aggregate(multi.venue_readings("alice"))
    assert t.open_positions == 2, "another user's positions leaked into the total"
    assert t.complete
    assert {r.venue for r in multi.venue_readings("alice")} == {"bitget", "bybit"}


def test_a_book_that_raises_yields_none_not_zero(multi):
    """The reader's own guard-or-omit. One book failing costs that book's
    numbers, not the whole reading — and the failure propagates as INCOMPLETE
    rather than as a confident smaller total."""
    multi.get("alice").open_position(_idea("BTC/USDT"), 100.0)
    broken = multi.get("alice", "bybit")

    def _boom():
        raise RuntimeError("state unreadable")

    broken.snapshot = _boom
    t = aggregate(multi.venue_readings("alice"))
    assert t.open_positions == 1, "the readable book was discarded too"
    assert "bybit" in t.unreadable
    assert not t.complete
    ok, reason = cap_verdict(t.open_positions, 5, t, "MAX_POSITIONS")
    assert ok is False and "bybit" in reason


# ── wired into the engine that enforces it ───────────────────────────────

def _engine(tmp_path):
    from bot.risk.portfolio import PortfolioTracker
    from bot.risk.risk_engine import RiskEngine
    return RiskEngine(PortfolioTracker(initial_balance=10_000.0),
                      state_file=str(tmp_path / "risk.json"))


def test_an_unwired_engine_reads_exactly_what_it_always_read(tmp_path):
    """#58's shape, facing the other way: the hook must be a NO-OP when
    nothing supplies it, or every single-venue deployment changes behaviour."""
    eng = _engine(tmp_path)
    assert eng._person_open_positions() is None
    assert eng._person_totals_incomplete() == ""


def test_the_person_count_tightens_and_never_loosens(tmp_path):
    """max(), not assignment. The person total is a superset of this book, so
    it can only raise the count — a smaller person number (which would mean
    the reading is wrong) must never lower this book's own count."""
    eng = _engine(tmp_path)
    eng.set_person_totals_fn(lambda: PersonTotals(open_positions=7))
    assert eng._person_open_positions() == 7
    eng.set_person_totals_fn(lambda: PersonTotals(open_positions=0))
    assert eng._person_open_positions() == 0  # the raw read...
    # ...and evaluate() takes max(), so a 0 here cannot erase a real local
    # count. Pinned at the call site rather than here, where it is arithmetic.


def test_a_raising_totals_fn_falls_back_to_single_venue_not_to_zero(tmp_path):
    """Fail-soft to None — "single venue", the ORIGINAL behaviour — rather
    than to a zeroed total, which would read as a person holding nothing
    anywhere and open every cap wide."""
    eng = _engine(tmp_path)

    def _boom():
        raise RuntimeError("engine gone")

    eng.set_person_totals_fn(_boom)
    assert eng._person_open_positions() is None, (
        "an unreadable person total became a measured zero")
    assert eng._person_totals_incomplete() == ""


def test_the_cap_check_actually_consults_it(tmp_path):
    """#58: computed and never called is indistinguishable from broken. The
    behaviour above is unreachable unless evaluate() reads it."""
    import inspect

    from bot.risk.risk_engine import RiskEngine
    # The caps live in _evaluate_locked; `evaluate` is the lock wrapper.
    src = inspect.getsource(RiskEngine._evaluate_locked)
    assert "_person_open_positions()" in src, (
        "the person-level count is computed and never used by any cap")
    assert "_person_totals_incomplete()" in src, (
        "an incomplete reading is never checked, so a floor under the cap is "
        "allowed through")


def test_the_engine_binds_the_hook_for_every_per_user_engine():
    """Not only the venue-scoped ones. A user with one venue today may add a
    second tomorrow, and a cap that starts counting across venues only when
    somebody remembers to rebind was loose for as long as nobody noticed."""
    import inspect

    from bot.core.engine import RuneClawEngine
    src = inspect.getsource(RuneClawEngine.risk_for)
    assert "set_person_totals_fn" in src, (
        "risk_for never wires the person-level totals, so the cap silently "
        "stays per-venue")
    assert "venue_readings" in src
