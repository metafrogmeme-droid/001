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
    """Written to track ENFORCE_IMPLEMENTED rather than to be deleted and
    rewritten later. While routing is unwired this pins that enforce yields
    nothing; the day the executor learns to route, the same assertion becomes
    the real one with no edit — and if somebody flips the constant WITHOUT
    wiring a caller, this fails."""
    import bot.core.venue_selection as vs
    mode(True, "enforce")
    store.set_selection("alice", ["bitget", "bybit"], connected=_ALL)
    got = routing_decision("alice", connected=_ALL, store=store)["effective"]
    assert got == (("bitget", "bybit") if vs.ENFORCE_IMPLEMENTED else ())


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


def test_a_dropped_venue_is_reported_rather_than_quietly_routing_fewer(store, mode):
    """The `dropped` half holds in every mode — it is a fact about the user's
    credentials, not about routing — so this does not wait on
    ENFORCE_IMPLEMENTED."""
    import bot.core.venue_selection as vs
    mode(True, "enforce")
    store.set_selection("alice", ["bitget", "bybit"], connected=_ALL)
    d = routing_decision("alice", connected=lambda u: ["bitget"], store=store)
    assert d["venues"] == ("bitget",)
    assert d["dropped"] == ("bybit",)
    assert "not connected" in d["reason"]
    assert d["effective"] == (("bitget",) if vs.ENFORCE_IMPLEMENTED else ())


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


# ── enforce cannot silently be a no-op ───────────────────────────────────

def test_enforce_and_its_caller_cannot_drift_apart():
    """`ENFORCE_IMPLEMENTED` is a claim about the ENGINE, so it is checked
    against the engine.

    For one commit it was False, and that gap was worth naming: with nothing
    acting on `effective`, `enforce` behaved exactly like `shadow` while
    reporting that it routed across venues. An operator who set it would have
    believed the feature was live and been wrong.

    Keeping the constant keeps the claim checkable in BOTH directions —
    flipping it without a caller fails here, and deleting the caller while it
    is True fails here too.
    """
    import inspect

    import bot.core.venue_selection as vs
    from bot.core.engine import RuneClawEngine

    src = inspect.getsource(RuneClawEngine)
    routes = "choose_venue(" in src and "_executor_for(user_id, _venue)" in src
    assert vs.ENFORCE_IMPLEMENTED == routes, (
        "ENFORCE_IMPLEMENTED says %r but the engine %s route on it"
        % (vs.ENFORCE_IMPLEMENTED, "does" if routes else "does NOT"))


def test_a_refused_route_does_not_fall_back_to_the_default_venue():
    """The single most dangerous line that could be written here is a fallback.
    When no selected venue can take the order — margin unreadable, or short —
    routing to the default venue anyway would place a trade somewhere the
    person did not select, at exactly the moment least is known."""
    import inspect

    from bot.core.engine import RuneClawEngine
    src = inspect.getsource(RuneClawEngine)
    i = src.index("_venue, _why = choose_venue(")
    window = src[i:i + 1400]
    assert "if _venue is None:" in window and "return" in window, (
        "a failed venue choice does not refuse — it falls through to whatever "
        "executor was resolved before")


# ── it is reachable from production, not only from tests ─────────────────

def test_the_engine_observes_the_routing_decision():
    """#58: a resolver nothing calls is indistinguishable from one that does
    not work, and its passing tests say nothing about production. The shadow
    observation in `_executor_for` is what makes this module reachable AND
    what makes shadow mode mean something."""
    import inspect

    from bot.core.engine import RuneClawEngine
    src = inspect.getsource(RuneClawEngine._executor_for)
    assert "routing_decision(" in src, (
        "nothing in production calls the venue routing resolver")
    assert "venue_routing" in src, "the shadow decision is computed and not recorded"


def test_the_observation_cannot_block_an_order():
    """Best-effort by construction. An observation that can throw is an
    observation that can stop a trade that would otherwise have been placed."""
    import inspect

    from bot.core.engine import RuneClawEngine
    src = inspect.getsource(RuneClawEngine._executor_for)
    head = src.index("routing_decision(")
    # The call sits inside a try/except whose handler only logs.
    assert "try:" in src[:head]
    tail = src[head:head + 1200]
    assert "except Exception" in tail and "logger.debug" in tail, (
        "the routing observation is not guarded — it can raise on the path "
        "that places an order")


def test_a_relative_path_is_anchored_to_the_repo_not_the_cwd(tmp_path, monkeypatch):
    """`data/venue_selection.json` is baselined as anchored-downstream, so the
    cwd guard no longer watches it and this is the only thing that does. Every
    other test here passes an absolute tmp path, which `state_path` returns
    unchanged — so they all pass against a store with the anchoring removed."""
    root = tmp_path / "repo"
    elsewhere = tmp_path / "elsewhere"
    root.mkdir(); elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr("bot.utils.paths.REPO_ROOT", root, raising=False)

    s = VenueSelectionStore(path="data/sel_relative.json")
    s.set_selection("alice", ["bitget"], connected=_ALL)
    assert (root / "data" / "sel_relative.json").exists(), (
        "the selection was written relative to the working directory")
    assert not (elsewhere / "data" / "sel_relative.json").exists()


# ── which venue gets the order ───────────────────────────────────────────

def _c(venue, free):
    return {"venue": venue, "free_usd": free}


def test_one_order_goes_to_one_venue():
    """Not to all of them. Placing the same idea on two venues doubles the
    exposure while every per-venue check still passes — §2's "two venues each
    with their own max-5 is ten positions" in its purest form. Multi-venue
    spreads the book; it does not duplicate the trade."""
    from bot.core.venue_selection import choose_venue
    v, _ = choose_venue([_c("bitget", 1000), _c("bybit", 1000)], 100)
    assert isinstance(v, str)


def test_the_most_free_margin_wins():
    from bot.core.venue_selection import choose_venue
    assert choose_venue([_c("bitget", 100), _c("bybit", 500)], 50)[0] == "bybit"


def test_an_unreadable_margin_is_never_a_candidate():
    """§7: an unreadable balance must not read as free margin. Routing there
    bets the order on a number nobody read."""
    from bot.core.venue_selection import choose_venue
    for bad in (None, "abc", float("nan")):
        v, why = choose_venue([_c("bitget", bad), _c("bybit", 500)], 50)
        assert v == "bybit", f"{bad!r} was treated as available margin"
        assert "unreadable" in why


def test_everything_unreadable_refuses_rather_than_guessing():
    from bot.core.venue_selection import choose_venue
    v, why = choose_venue([_c("bitget", None), _c("bybit", None)], 50)
    assert v is None
    assert "unreadable" in why and "bitget" in why and "bybit" in why


def test_a_venue_without_room_is_skipped_and_said_so():
    from bot.core.venue_selection import choose_venue
    v, why = choose_venue([_c("bitget", 10), _c("bybit", 500)], 100)
    assert v == "bybit"
    assert "bitget" in why and "10.00" in why


def test_no_venue_with_room_refuses():
    from bot.core.venue_selection import choose_venue
    v, why = choose_venue([_c("bitget", 10)], 100)
    assert v is None, "an order was routed to a venue that cannot take it"
    assert "no venue can take this order" in why


def test_the_choice_is_deterministic_on_identical_state():
    """A router that picks differently on the same inputs is one nobody can
    reason about after the fact."""
    from bot.core.venue_selection import choose_venue
    a = [_c("okx", 500), _c("bybit", 500), _c("bitget", 500)]
    assert choose_venue(a, 10)[0] == choose_venue(list(reversed(a)), 10)[0] == "bitget"


def test_no_candidates_at_all_refuses():
    from bot.core.venue_selection import choose_venue
    assert choose_venue([], 10)[0] is None
    assert choose_venue(None, 10)[0] is None


# ── the engine-level safety properties ───────────────────────────────────

def test_flatten_and_the_kill_switch_reach_every_venue_by_construction():
    """§3's safety core. Per-venue executors go into the SAME dict as the
    single-venue ones, and `_all_live_executors()` iterates `.values()` — so
    flatten, the emergency halt, SL/TP monitoring and reconciliation cover
    every venue without any of them being edited. Structural, because a
    property that depends on remembering does not survive."""
    import inspect

    from bot.core.engine import RuneClawEngine
    src = inspect.getsource(RuneClawEngine._all_live_executors)
    assert "_user_executors.values()" in src, (
        "executors are enumerated by KEY somewhere — a per-venue executor "
        "would be missed by flatten and by the kill switch")

    flat = inspect.getsource(RuneClawEngine.flatten_all_positions)
    assert "_all_live_executors()" in flat

    # The kill switch reaches venues by TWO routes, and both matter. It trips
    # every RiskEngine by iterating `_user_risk`, which Phase 2 made
    # venue-keyed, so no venue can still pass risk; and it flattens through
    # `flatten_all_positions`, which is the executor half above. Asserting it
    # calls `_all_live_executors` DIRECTLY would have been wrong — the first
    # draft of this test did, and the chain is what actually holds.
    halt = inspect.getsource(RuneClawEngine.emergency_halt_all)
    assert "self._user_risk.items()" in halt, (
        "the kill switch does not trip every per-(user, venue) risk engine")
    assert "flatten_all_positions(" in halt, (
        "the kill switch does not flatten through the all-executors path")


def test_disconnect_drops_every_venue_executor_not_just_the_default():
    """Popping only `user` would leave per-venue executors cached holding
    credentials the user just revoked — a /disconnect that disconnects
    nothing, on the path that places orders."""
    from bot.core.engine import RuneClawEngine

    e = RuneClawEngine.__new__(RuneClawEngine)
    e._user_executors = {"alice": 1, "bybit/alice": 2, "okx/alice": 3, "bob": 4,
                         "bybit/bob": 5}
    e._balance_view_executors = {"alice": 1, "bybit/alice": 2}
    e.invalidate_user_executor("alice")
    assert set(e._user_executors) == {"bob", "bybit/bob"}, (
        "a revoked user kept a live per-venue executor")
    assert e._balance_view_executors == {}


def test_an_error_reading_a_balance_is_unreadable_not_zero():
    """`fetch_balance` returns {"error": ..., "free": 0} on failure — a
    measured-looking ZERO for a balance nobody read. Taking `free` straight
    from it would report "$0.00 < $50.00" about a venue that never answered,
    and an order of size 0 would find it eligible."""
    import inspect

    from bot.core.engine import RuneClawEngine
    src = inspect.getsource(RuneClawEngine._venue_margin_candidates)
    assert 'bal.get("error")' in src, (
        "the error key is not checked, so a failed balance read becomes a "
        "measured zero")


def test_a_venue_refusal_is_a_readable_message_not_a_dict():
    """`_confirm_trade_inner` returns the TEXT the user is shown. The first
    draft of the venue refusal returned {"ok": False, ...}, which would have
    rendered a raw Python repr into the chat at the exact moment a trade was
    refused — the worst moment to be unreadable.

    Caught by checking the enclosing method's real signature rather than
    trusting the name in the commit message, which was also wrong.
    """
    import inspect
    import re

    from bot.core.engine import RuneClawEngine
    src = inspect.getsource(RuneClawEngine._confirm_trade_inner)
    assert not [ln for ln in src.split("\n") if re.match(r"\s*return \{", ln)], (
        "this function is annotated -> str and every sibling refusal is a "
        '"Trade REJECTED: ..." line; something returns a dict')
    assert 'Trade REJECTED: {_why}' in src, (
        "the no-eligible-venue refusal does not use the house refusal format")
