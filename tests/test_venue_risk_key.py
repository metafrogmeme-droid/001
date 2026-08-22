"""Phase 2 of multi-venue: the (user, venue) key for risk and portfolio state.

The scope calls this the phase where "a cap can be silently loosened", and the
two ways it loosens are both about the KEY rather than about arithmetic:

  * a key that COLLIDES pools two books into one — two accounts sharing one
    circuit breaker, which reads as a breaker working right up until it does
    not;
  * a key that does not ROUND-TRIP creates a phantom book — a fresh, empty,
    default-balance account, which reads on every surface as "nothing is
    happening here" while a real position sits on the other side of it.

Both are silent. Neither raises. So these tests drive the real resolvers with
real venues and assert identity of the objects returned, rather than checking
that a path string looks plausible.

The third property, and the one that makes the phase shippable: with no venue
named, EVERYTHING must be exactly what it was before Phase 2 existed.
"""
from __future__ import annotations

import pytest

from bot.core import venue_key
from bot.risk.multi_portfolio import MultiUserPortfolio


@pytest.fixture
def multi(tmp_path, monkeypatch):
    """A manager rooted in a temp dir, so nothing reads or writes real books."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("bot.utils.paths.REPO_ROOT", tmp_path, raising=False)
    return MultiUserPortfolio(default_balance=1000.0)


# ── the default path is untouched ────────────────────────────────────────

def test_no_venue_named_is_the_original_single_venue_book(multi):
    """Phase 2 must be byte-identical to Phase 1 when nobody names a venue."""
    a = multi.get("alice")
    assert multi.get("alice") is a
    assert multi.get("alice", "") is a
    assert multi.get("alice", None) is a


def test_the_default_venue_is_not_a_split(multi):
    """Naming bitget explicitly and naming nothing must reach the SAME book.
    Two books for the default venue would silently halve an existing balance
    the first time a caller started passing the venue through."""
    assert multi.get("alice", "bitget") is multi.get("alice")
    assert not venue_key.is_split("bitget")
    assert not venue_key.is_split("")


def test_an_unrecognised_venue_falls_back_rather_than_opening_a_book(multi):
    """A typo must not become a second, empty account. It resolves to the
    caller's existing book — the safe direction — and `venue_state_path`
    refuses outright, so a caller that got here with one has a bug that
    surfaces at the boundary instead of six hours into a trading day."""
    assert multi.get("alice", "bitgetx") is multi.get("alice")
    assert multi.get("alice", "../../etc") is multi.get("alice")
    with pytest.raises(ValueError):
        venue_key.venue_state_path("risk_state", "alice", "bitgetx")


# ── the two silent failures ──────────────────────────────────────────────

def test_a_split_venue_is_a_different_book_from_the_default(multi):
    a = multi.get("alice")
    b = multi.get("alice", "bybit")
    assert a is not b, "the split venue shares the default venue's breakers"
    assert multi.get("alice", "bybit") is b, "the split book is recreated per call"


def test_user_and_venue_cannot_collide_into_one_book(multi):
    """The collision the on-disk layout exists to prevent. `_sanitize` keeps
    underscores and hyphens, so a `{user}_{venue}` key cannot tell the user
    `alice_bybit` apart from `alice` on Bybit — and one shared circuit breaker
    between two strangers is the result."""
    victim = multi.get("alice_bybit")
    other = multi.get("alice", "bybit")
    assert victim is not other
    # And the reverse pairing, which is the same collision from the other side.
    assert multi.get("bybit", "okx") is not multi.get("bybit_okx")


def test_the_venue_is_a_directory_so_the_filename_still_round_trips(tmp_path):
    """A dot separator is DELETED by `_sanitize` — measured, not assumed — so
    `portfolio_alice.bybit.json` would reload as the user `alicebybit`. The
    path keeps the venue as its own component for exactly this reason."""
    assert MultiUserPortfolio._sanitize("alice.bybit") == "alicebybit"
    p = venue_key.venue_state_path("portfolio", "alice", "bybit")
    assert p.endswith("/bybit/portfolio_alice.json"), p
    # The user id, recovered the way _load_existing_venues recovers it.
    import os
    raw = os.path.basename(p)[len("portfolio_"):-len(".json")]
    assert MultiUserPortfolio._sanitize(raw) == "alice", (
        "the user id does not survive the round trip through the filename")


def test_the_default_glob_cannot_see_split_venue_files(tmp_path, monkeypatch):
    """`_load_existing` globs `data/portfolio_*.json` non-recursively. If a
    split file landed there it would be restored as a USER whose id happened to
    contain the venue — the phantom account, arriving at startup."""
    import glob
    import os
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("bot.utils.paths.REPO_ROOT", tmp_path, raising=False)
    target = venue_key.venue_state_path("portfolio", "alice", "bybit")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    open(target, "w").write("{}")
    assert glob.glob(os.path.join("data", "portfolio_*.json")) == []


# ── state survives a restart ─────────────────────────────────────────────

def test_a_split_book_is_restored_rather_than_silently_reset(tmp_path, monkeypatch):
    """Without a restore path, a restart resets every split venue to the
    default paper balance with no positions — which reads as "nothing is
    happening on Bybit" while a real position sits there unmonitored.

    THE CWD IS DELIBERATELY NOT THE REPO ROOT. The first version of this test
    pointed both at ``tmp_path``, which made a relative glob and an anchored
    one the same directory — so it passed against a ``venue_root()`` that
    returned the bare relative literal, which is the precise bug
    ``test_durable_paths_are_not_cwd_dependent`` had just caught in this
    module. A test that cannot distinguish the fix from the defect is not
    testing the fix."""
    root = tmp_path / "repo"
    elsewhere = tmp_path / "launched-from-here"
    root.mkdir()
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr("bot.utils.paths.REPO_ROOT", root, raising=False)
    m1 = MultiUserPortfolio(default_balance=1000.0)
    book = m1.get("alice", "bybit")
    book.balance = 777.0
    book.save_state()

    m2 = MultiUserPortfolio(default_balance=1000.0)          # "restart"
    assert ("alice", "bybit") in m2.venue_portfolios(), (
        "the split book was not restored, so it silently reset to default")
    assert m2.get("alice", "bybit").snapshot().balance_usd == pytest.approx(777.0)


def test_a_stray_venue_directory_is_skipped_not_adopted(tmp_path, monkeypatch):
    import os
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("bot.utils.paths.REPO_ROOT", tmp_path, raising=False)
    stray = os.path.join(venue_key.venue_root(), "notavenue")
    os.makedirs(stray, exist_ok=True)
    open(os.path.join(stray, "portfolio_alice.json"), "w").write("{}")
    m = MultiUserPortfolio(default_balance=1000.0)
    assert m.venue_portfolios() == {}


# ── the split must not loosen a gate ─────────────────────────────────────

def test_exposure_totals_count_every_venue(multi):
    """§2 of the scope: the split must never loosen a gate. A total that skips
    split venues under-reports exposure, and an under-reported exposure is a
    cap that does not bind. `all_portfolios()` keeps meaning "which USERS have
    a book" — the totals are what have to span venues."""
    from bot.utils.models import Direction, TradeIdea

    def _idea(asset):
        return TradeIdea(asset=asset, direction=Direction.LONG, entry_price=100.0,
                         stop_loss=95.0, take_profit=115.0, confidence=0.8,
                         reasoning="t")

    multi.get("alice").open_position(_idea("BTC/USDT"), 100.0)
    multi.get("alice", "bybit").open_position(_idea("ETH/USDT"), 100.0)

    assert multi.total_open_positions() == 2, (
        "an open position on a split venue is invisible to the exposure count")
    assert multi.combined_snapshot().open_positions == 2
    # ...while the user-keyed view still answers the question it always did.
    assert multi.all_user_ids() == ["alice"]
    assert list(multi.all_portfolios()) == ["alice"]


def test_stops_run_on_every_venue_and_are_keyed_by_user(multi):
    """A stop-loss that only runs on the default venue is the "half-done
    version worse than not doing it" the scope opens with. The result stays
    keyed by USER — never a composite — and a user closing on two venues in one
    tick keeps both closes rather than one overwriting the other."""
    from bot.utils.models import Direction, TradeIdea

    def _idea(asset):
        return TradeIdea(asset=asset, direction=Direction.LONG, entry_price=100.0,
                         stop_loss=95.0, take_profit=115.0, confidence=0.8,
                         reasoning="t")

    multi.get("alice").open_position(_idea("BTC/USDT"), 100.0)
    multi.get("alice", "bybit").open_position(_idea("ETH/USDT"), 100.0)

    closed = multi.check_stops_all({"BTC/USDT": 90.0, "ETH/USDT": 90.0})
    assert set(closed) == {"alice"}, f"stops keyed by something other than a user: {list(closed)}"
    assert len(closed["alice"]) == 2, (
        "one venue's close overwrote the other's, or a venue was never checked")


def test_marking_to_market_reaches_split_venues(multi):
    """An unmarked tracker keeps ENTRY as its mark for ever: unrealised PnL
    reads 0.00 and the stop is measured against the opening price. Not a
    reporting gap — a stop that does not fire."""
    from bot.utils.models import Direction, TradeIdea

    multi.get("alice", "bybit").open_position(
        TradeIdea(asset="ETH/USDT", direction=Direction.LONG, entry_price=100.0,
                  stop_loss=95.0, take_profit=115.0, confidence=0.8, reasoning="t"),
        100.0)
    multi.mark_to_market_all({"ETH/USDT": 110.0})
    assert multi.get("alice", "bybit")._last_prices.get("ETH/USDT") == 110.0


# ── the property the whole feature depends on ────────────────────────────

def test_one_kill_switch_still_halts_every_venue(monkeypatch):
    """§3 of the scope: the kill switch MUST NOT split. "A kill switch that
    halts one venue while another keeps trading is a worse product than a
    single-venue bot."

    Phase 2 does not touch `_HALT_CHECK` — it is a MODULE-level global, so
    every executor on every venue reads the same function by construction.
    That is exactly why it is worth a test rather than a comment: the property
    holds today because of where a variable lives, and nothing about the
    per-venue split would fail loudly if someone later moved it onto the
    executor instance to make it "cleaner".
    """
    from bot.core import live_executor as le

    saved = le._HALT_CHECK
    try:
        le.set_halt_check(lambda: True)
        # There is one answer, and it is not reachable per venue.
        assert le.trading_halted() is True
        le.set_halt_check(lambda: False)
        assert le.trading_halted() is False

        # And it fails CLOSED, which is the half that matters on the day the
        # halt state cannot be read at all.
        def _boom():
            raise RuntimeError("halt state unreadable")

        le.set_halt_check(_boom)
        assert le.trading_halted() is True, (
            "an unreadable halt state read as 'not halted' — unreadable is "
            "never zero, on the control that stops the bot")
    finally:
        le.set_halt_check(saved)


def test_the_halt_check_is_module_level_not_per_executor():
    """The structural half of the claim above. If this moves onto the instance,
    each venue's executor gets its own copy and one stop button stops one
    venue — silently, since every existing test would still pass."""
    from bot.core import live_executor as le

    assert hasattr(le, "_HALT_CHECK"), (
        "the kill switch is no longer a module-level global, so it can differ "
        "per executor and per venue")
    assert "_HALT_CHECK" not in getattr(le.LiveExecutor, "__annotations__", {}), (
        "the kill switch became an instance attribute")
