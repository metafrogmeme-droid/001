"""The shared meme preflight — and the absences it must not flatten.

This module exists to stop `/memeplan` and the web gateway growing two copies
of a fail-closed gate. So most of what is asserted here is that an unreadable
input stays unreadable all the way to the verdict, rather than becoming a
number somewhere in the middle.
"""
from __future__ import annotations

import asyncio

import pytest

from bot.core import meme_preflight as mp

MINT = "So11111111111111111111111111111111111111112"
NOW = 1_700_000_000.0
HOUR_MS = 3_600_000


class FakeSource:
    """A venue read, or the absence of one."""

    def __init__(self, features=None):
        self.features = features


async def _fake_gather(sources, chain, mint, timeout=None):
    feats = {}
    for s in sources:
        if getattr(s, "features", None):
            feats.update(s.features)
    return {"features": feats}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    import bot.core.token_sources as ts
    monkeypatch.setattr(ts, "gather", _fake_gather)


def run(features=None, **kw):
    kw.setdefault("authorized", True)
    kw.setdefault("now", lambda: NOW)
    kw.setdefault("sources", [FakeSource(features or {})])
    return asyncio.run(mp.preflight(MINT, kw.pop("size_usd", 25.0), **kw))


# ── an undateable pool is not a new one, and not an old one ───────────────

def test_age_is_none_when_the_pool_cannot_be_dated():
    """The textbook rug shape. 0 would read as brand new; a big number would
    read as seasoned. Both are inventions, and the gate refuses on None."""
    for bad in (None, 0, "", "soon", float("nan")):
        assert mp.age_hours({"pair_created_at_ms": bad}, now=lambda: NOW) is None
    assert mp.age_hours({}, now=lambda: NOW) is None
    assert mp.age_hours(None, now=lambda: NOW) is None


def test_age_is_computed_when_the_stamp_is_there():
    feats = {"pair_created_at_ms": (NOW - 6 * 3600) * 1000}
    assert mp.age_hours(feats, now=lambda: NOW) == pytest.approx(6.0)


def test_a_future_stamp_clamps_to_zero_rather_than_going_negative():
    feats = {"pair_created_at_ms": (NOW + 9999) * 1000}
    assert mp.age_hours(feats, now=lambda: NOW) == 0.0


# ── absences survive the trip to the gate ─────────────────────────────────

def test_an_unreadable_venue_yields_unknowns_not_zeros():
    # A 503 must not present as "$0 liquidity, 0 buys" — that is a measurement
    # of a market nobody read, and the gate would be judging a fiction.
    plan = run(features={})
    m = plan["market"]
    assert m["liquidity_usd"] is None
    assert m["age_hours"] is None
    assert m["buys_24h"] is None and m["sells_24h"] is None
    assert plan["allowed"] is False


def test_the_market_that_was_read_travels_with_the_verdict():
    """A surface showing different figures than the verdict was based on is a
    surface quietly disagreeing with itself."""
    plan = run(features={"liquidity_usd": 250_000.0, "buys_24h": 900,
                         "sells_24h": 400,
                         "pair_created_at_ms": (NOW - 48 * 3600) * 1000})
    m = plan["market"]
    assert m["liquidity_usd"] == 250_000.0
    assert m["buys_24h"] == 900 and m["sells_24h"] == 400
    assert m["age_hours"] == pytest.approx(48.0)


def test_zero_liquidity_is_kept_as_zero_and_not_confused_with_absent():
    # 0.0 is falsy and 0.0 is a real, measured, empty pool.
    plan = run(features={"liquidity_usd": 0.0})
    assert plan["market"]["liquidity_usd"] == 0.0
    assert plan["market"]["liquidity_usd"] is not None


# ── an unreadable envelope is not an authorizing one ──────────────────────

def test_an_unreadable_envelope_does_not_authorize(monkeypatch):
    import bot.guardian.user_authority_store as store

    def boom():
        raise RuntimeError("store unavailable")

    monkeypatch.setattr(store, "get_user_authority_store", boom)
    assert mp.envelope_authorized("123") is False


def test_an_enforcing_envelope_authorizes(monkeypatch):
    import bot.guardian.user_authority_store as store

    class _S:
        def is_enforcing(self, _tg_id):
            return True

    monkeypatch.setattr(store, "get_user_authority_store", lambda: _S())
    assert mp.envelope_authorized("123") is True


def test_a_non_enforcing_envelope_does_not(monkeypatch):
    import bot.guardian.user_authority_store as store

    class _S:
        def is_enforcing(self, _tg_id):
            return False

    monkeypatch.setattr(store, "get_user_authority_store", lambda: _S())
    assert mp.envelope_authorized("123") is False


def test_preflight_defaults_to_asking_the_envelope(monkeypatch):
    """`authorized=None` must consult the store, not fall through to True."""
    seen = []
    monkeypatch.setattr(mp, "envelope_authorized",
                        lambda tg: (seen.append(tg), False)[1])
    plan = run(features={"liquidity_usd": 999_999.0}, authorized=None, tg_id="42")
    assert seen == ["42"]
    assert plan["allowed"] is False, "no envelope, no plan"


# ── it plans; it never executes ───────────────────────────────────────────

def test_the_plan_never_claims_it_would_execute():
    assert run(features={"liquidity_usd": 999_999.0})["would_execute"] is False


def test_the_verdict_is_stamped_so_it_can_go_stale():
    """`meme_swap.build_swap` refuses a plan older than MAX_PLAN_AGE_S, and can
    only do that if somebody wrote the timestamp down."""
    assert run()["created_at"] == NOW


def test_preflight_and_the_telegram_command_share_one_path():
    """The reason this module exists, asserted rather than assumed.

    A second inline copy of the gather/age/envelope sequence in the handler is
    the drift this was extracted to prevent, so the handler must not regrow one.
    """
    import inspect

    from bot.skills.telegram_handler import TelegramHandler

    src = inspect.getsource(TelegramHandler._cmd_memeplan)
    code = "\n".join(ln for ln in src.splitlines()
                     if not ln.strip().startswith("#"))
    assert "preflight(" in code
    for regrown in ("plan_swap(", "assess_token(", "pair_created_at_ms",
                    "is_enforcing("):
        assert regrown not in code, f"{regrown} belongs in meme_preflight now"
