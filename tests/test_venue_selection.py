"""Phase 4: which venues a user chose, and whether that choice is acted on.

This is the phase that decides where real orders go, so every test here is
pointed at one question: **can any path through this code cause MORE venues to
trade than the user deliberately chose?**

The answers that must hold, in rough order of how much they would cost:

  * connecting a venue is not consenting to trade on it;
  * an error never widens the set — every failure path narrows to single-venue;
  * a venue that stopped being connected drops out AND is reported, because
    silently shortening the list is how somebody believes they are trading two
    venues while one has been dead for a week;
  * a venue holding open positions cannot be deselected out from under them;
  * `shadow` executes exactly like `off`, or it is not a shadow.
"""
from __future__ import annotations

import dataclasses

import pytest

from bot.core.venue_selection import VenueSelectionStore, routing_decision


@pytest.fixture
def store(tmp_path):
    return VenueSelectionStore(path=str(tmp_path / "sel.json"))


@pytest.fixture
def mode():
    """Set (enabled, mode) on the frozen config, restored afterwards."""
    from bot.config import CONFIG

    def _set(enabled: bool, m: str):
        object.__setattr__(CONFIG, "multi_venue_trading_enabled", enabled)
        object.__setattr__(CONFIG, "multi_venue_trading_mode", m)

    saved = (getattr(CONFIG, "multi_venue_trading_enabled", False),
             getattr(CONFIG, "multi_venue_trading_mode", "shadow"))
    yield _set
    object.__setattr__(CONFIG, "multi_venue_trading_enabled", saved[0])
    object.__setattr__(CONFIG, "multi_venue_trading_mode", saved[1])


_ALL = lambda uid: ["bitget", "bybit", "okx"]          # noqa: E731
_NONE = lambda uid: []                                  # noqa: E731


# ── connecting is not consenting ─────────────────────────────────────────

def test_a_fresh_user_trades_one_venue(store):
    """The default is EMPTY, and empty means single-venue. A user who never
    opens the picker sees no change at all."""
    assert store.raw_selection("alice") == []
    assert store.active_venues("alice", connected=_ALL) == ((), ())


def test_connecting_a_venue_does_not_start_trading_on_it(store):
    """The most dangerous shortcut available in this phase would be to treat
    `list_venues()` — a fact about KEYS — as the routing set. Pasting an API
    key would then be an instruction to trade with it."""
    assert store.active_venues("alice", connected=_ALL) == ((), ()), (
        "three connected venues became three trading venues with nobody "
        "choosing that")


def test_selection_is_explicit_and_persists(store, tmp_path):
    ok, _ = store.set_selection("alice", ["bitget", "bybit"], connected=_ALL)
    assert ok
    assert store.active_venues("alice", connected=_ALL) == (("bitget", "bybit"), ())
    reloaded = VenueSelectionStore(path=store._path)
    assert reloaded.raw_selection("alice") == ["bitget", "bybit"]


# ── you cannot select what you have not connected ────────────────────────

def test_an_unconnected_venue_cannot_be_selected(store):
    ok, why = store.set_selection("alice", ["bitget", "kucoin"],
                                  connected=lambda u: ["bitget"])
    assert ok is False
    assert "kucoin" in why
    assert store.raw_selection("alice") == [], "a refused write was partly applied"


def test_a_venue_this_bot_does_not_support_is_refused(store):
    ok, why = store.set_selection("alice", ["bitget", "notavenue"], connected=_ALL)
    assert ok is False and "notavenue" in why


def test_unverifiable_connections_refuse_the_write(store):
    """Cannot check → cannot widen. The alternative is trusting a list nothing
    confirmed, on the setting that decides where orders go."""
    def _boom(uid):
        raise RuntimeError("credential store down")

    ok, why = store.set_selection("alice", ["bybit"], connected=_boom)
    assert ok is False
    assert "could not verify" in why.lower()


# ── a venue that stops being connected ───────────────────────────────────

def test_a_disconnected_venue_drops_out_AND_is_reported(store):
    """The honest half. Filtering it out silently leaves the user believing
    they trade two venues while one has been dead for a week."""
    store.set_selection("alice", ["bitget", "bybit"], connected=_ALL)
    live, dropped = store.active_venues("alice", connected=lambda u: ["bitget"])
    assert live == ("bitget",)
    assert dropped == ("bybit",), "a venue vanished from the routing set silently"


def test_the_raw_choice_survives_a_disconnection(store):
    """`raw_selection` still shows it, so the UI can distinguish "you turned
    this off" from "your keys stopped working" — two very different things to
    show somebody."""
    store.set_selection("alice", ["bitget", "bybit"], connected=_ALL)
    assert "bybit" in store.raw_selection("alice")


def test_an_unreadable_connection_lookup_narrows_to_single_venue(store):
    """A raising lookup must NARROW, not crash and not widen.

    The first version of `active_venues` guarded only the default import path,
    so an injected callable that raised propagated straight out — an unhandled
    exception on the read that decides where an order goes. This test failed
    against that, which is how it was found.
    """
    def _boom(uid):
        raise RuntimeError("credential store down")

    store.set_selection("alice", ["bitget", "bybit"], connected=_ALL)
    live, dropped = store.active_venues("alice", connected=_boom)
    assert live == (), "an unverifiable read left venues in the routing set"
    assert dropped == ("bitget", "bybit"), (
        "the venues fell out with nothing reported — the user would see a "
        "silently shorter list")


# ── open positions cannot be stranded ────────────────────────────────────

def test_a_venue_with_open_positions_cannot_be_deselected(store):
    """Deselecting stops routing while the positions stay real — orphans, self
    inflicted. The repo already has a card for reading orphans it did not open;
    manufacturing them is worse."""
    store.set_selection("alice", ["bitget", "bybit"], connected=_ALL)
    ok, why = store.set_selection("alice", ["bitget"], connected=_ALL,
                                  open_positions=lambda u, v: 2 if v == "bybit" else 0)
    assert ok is False
    assert "bybit (2 open)" in why
    assert store.raw_selection("alice") == ["bitget", "bybit"], "it deselected anyway"


def test_a_venue_with_no_open_positions_deselects_fine(store):
    store.set_selection("alice", ["bitget", "bybit"], connected=_ALL)
    ok, _ = store.set_selection("alice", ["bitget"], connected=_ALL,
                                open_positions=lambda u, v: 0)
    assert ok
    assert store.raw_selection("alice") == ["bitget"]


def test_an_unreadable_position_count_refuses_rather_than_assuming_zero(store):
    """UNREADABLE IS NOT ZERO, on the step that would strand real positions."""
    store.set_selection("alice", ["bitget", "bybit"], connected=_ALL)

    def _boom(u, v):
        raise RuntimeError("venue unreachable")

    ok, why = store.set_selection("alice", ["bitget"], connected=_ALL,
                                  open_positions=_boom)
    assert ok is False
    assert "could not check" in why
    assert store.raw_selection("alice") == ["bitget", "bybit"]


def test_clearing_the_selection_returns_to_single_venue(store):
    store.set_selection("alice", ["bitget"], connected=_ALL)
    ok, why = store.set_selection("alice", [], connected=_ALL,
                                  open_positions=lambda u, v: 0)
    assert ok and why == "single venue"
    assert store.raw_selection("alice") == []


# ── the ladder ───────────────────────────────────────────────────────────

def test_off_routes_nowhere_new(store, mode):
    mode(False, "enforce")            # even with mode=enforce, disabled is off
    store.set_selection("alice", ["bitget", "bybit"], connected=_ALL)
    d = routing_decision("alice", connected=_ALL, store=store)
    assert d["mode"] == "off"
    assert d["effective"] == (), "multi-venue routed while the flag was off"


def test_shadow_reports_what_it_would_do_and_does_nothing(store, mode):
    """A shadow that executes is not a shadow. `effective` empty means the
    caller behaves exactly as it does today, while `venues` records the
    decision for observation."""
    mode(True, "shadow")
    store.set_selection("alice", ["bitget", "bybit"], connected=_ALL)
    d = routing_decision("alice", connected=_ALL, store=store)
    assert d["mode"] == "shadow"
    assert d["venues"] == ("bitget", "bybit"), "shadow recorded nothing"
    assert d["effective"] == (), "SHADOW EXECUTED — it routed across venues"
    assert "SHADOW" in d["reason"]


def test_enforce_is_the_only_mode_that_routes(store, mode):
    mode(True, "enforce")
    store.set_selection("alice", ["bitget", "bybit"], connected=_ALL)
    assert routing_decision("alice", connected=_ALL, store=store)["effective"] \
        == ("bitget", "bybit")


def test_an_unrecognised_mode_is_off_not_the_default(store, mode):
    """A typo like MULTI_VENUE_TRADING_MODE=enfroce must not resolve to the
    DEFAULT, which would look deliberate and silently be wrong. It resolves to
    the most restrictive value."""
    mode(True, "enfroce")
    store.set_selection("alice", ["bitget", "bybit"], connected=_ALL)
    d = routing_decision("alice", connected=_ALL, store=store)
    assert d["mode"] == "off"
    assert d["effective"] == ()


def test_enforce_with_no_selection_is_still_single_venue(store, mode):
    mode(True, "enforce")
    d = routing_decision("alice", connected=_ALL, store=store)
    assert d["effective"] == ()
    assert "no venues selected" in d["reason"]


def test_enforce_reports_a_dropped_venue_rather_than_quietly_routing_fewer(store, mode):
    mode(True, "enforce")
    store.set_selection("alice", ["bitget", "bybit"], connected=_ALL)
    d = routing_decision("alice", connected=lambda u: ["bitget"], store=store)
    assert d["effective"] == ("bitget",)
    assert d["dropped"] == ("bybit",)
    assert "not connected" in d["reason"]


def test_no_failure_path_widens_the_routing_set(store, mode):
    """The property the whole module is built around, asserted directly rather
    than inferred from the cases above."""
    mode(True, "enforce")

    class _Broken(VenueSelectionStore):
        def active_venues(self, user_id, connected=None):
            raise RuntimeError("store wedged")

    broken = _Broken.__new__(_Broken)
    d = routing_decision("alice", connected=_ALL, store=broken)
    assert d["effective"] == (), "a broken store routed somewhere"
    assert "unreadable" in d["reason"]


# ── the config default ───────────────────────────────────────────────────

def test_the_flag_ships_off():
    """Nothing about this phase changes behaviour until an operator opts in,
    and then only into shadow."""
    from bot.config import CONFIG
    fields = {f.name: f for f in dataclasses.fields(CONFIG)}
    assert fields["multi_venue_trading_enabled"].default is False
    assert fields["multi_venue_trading_mode"].default == "shadow"
