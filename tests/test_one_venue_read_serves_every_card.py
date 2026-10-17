"""One venue balance read per caller, shared by every card that asks.

The website's net-worth card, its funds-by-venue card and Telegram's
/networth each made their own read-only `fetch_balance` on the caller's venue,
per page view. A dashboard that opens both cards, a reload or a second tab
spent the venue's rate budget once each. `bot/core/venue_balance_read.py` now
answers a reading the venue gave in the last few seconds from memory, joins a
read already in flight, and keeps only readings the venue answered.

What each test holds the shared read to: identity decides whose book (never a
reading across callers, keys or environments); a reading from memory says
when the venue answered, not when it was served; a failure is never
remembered; one caller giving up does not cancel the others' read; the memory
is bounded. And the two gateway cards: a total over venues that did not all
answer is not printed as a total, and the audit line names the class.
"""
from __future__ import annotations

import asyncio

import pytest

import bot.core.exchange_credentials as xc
from bot.core import venue_balance_read as vb
from bot.web import user_gateway as ug
from tests.test_networth_gateway import AUTHED, HDRS, SECRET, FakeEngine, FakeHandler, client_for

KEYS = {"api_key": "k" * 16, "api_secret": "s" * 16}


class _Venue:
    """A stand-in `balance_snapshot` that records each call it was handed."""

    def __init__(self, equity=512.34, ok=True, delay=0.0):
        self.calls: list[tuple[str, dict]] = []
        self.equity = equity
        self.ok = ok
        self.delay = delay

    async def __call__(self, venue, fields, sandbox=None):
        self.calls.append((venue, dict(fields)))
        if self.delay:
            await asyncio.sleep(self.delay)
        if not self.ok:
            return {"ok": False, "venue": venue, "equity_usd": None, "detail": "HTTP 429"}
        return {"ok": True, "venue": venue, "currency": "USDT",
                "equity_usd": self.equity, "detail": f"{self.equity} USDT total"}


@pytest.fixture
def venue(monkeypatch):
    v = _Venue()
    monkeypatch.setattr(xc, "balance_snapshot", v)
    monkeypatch.setattr(xc, "venue_sandbox", lambda name, cfg=None: False)
    return v


@pytest.fixture
def clock(monkeypatch):
    t = {"now": 1000.0}
    monkeypatch.setattr(vb, "_now", lambda: t["now"])
    return t


# ── the shared read ─────────────────────────────────────────────────────────

async def test_two_asks_at_once_are_one_venue_call(venue):
    venue.delay = 0.05
    a, b = await asyncio.gather(vb.read_balance("7", "bybit", KEYS),
                                vb.read_balance("7", "bybit", KEYS))
    assert len(venue.calls) == 1
    assert a == b and a["equity_usd"] == 512.34 and a["read_at"]


async def test_a_fresh_reading_is_served_with_the_time_the_venue_answered(venue, clock):
    first = await vb.read_balance("7", "bybit", KEYS)
    await asyncio.sleep(1.05)                      # the wall clock moves past the second
    clock["now"] += vb.TTL_S - 1
    again = await vb.read_balance("7", "bybit", KEYS)
    assert len(venue.calls) == 1
    assert again["read_at"] == first["read_at"], "a remembered reading claimed to be new"
    clock["now"] += 1
    await vb.read_balance("7", "bybit", KEYS)
    assert len(venue.calls) == 2, "a reading was served past its time"


async def test_identity_decides_whose_reading(venue):
    await vb.read_balance("7", "bybit", KEYS)
    await vb.read_balance("8", "bybit", KEYS)
    assert len(venue.calls) == 2, "one caller was answered with another's balance"


async def test_a_rotated_key_is_a_new_reading(venue):
    await vb.read_balance("7", "bybit", KEYS)
    await vb.read_balance("7", "bybit", {**KEYS, "api_key": "n" * 16})
    assert len(venue.calls) == 2
    assert venue.calls[1][1]["api_key"] == "n" * 16


async def test_a_switch_between_demo_and_live_is_a_new_reading(venue, monkeypatch):
    await vb.read_balance("7", "bitget", KEYS)
    monkeypatch.setattr(xc, "venue_sandbox", lambda name, cfg=None: True)
    await vb.read_balance("7", "bitget", KEYS)
    assert len(venue.calls) == 2, "the demo account was answered with the live one's balance"


async def test_another_venue_is_its_own_reading(venue):
    await vb.read_balance("7", "bybit", KEYS)
    await vb.read_balance("7", "bitget", KEYS)
    assert [c[0] for c in venue.calls] == ["bybit", "bitget"]


async def test_a_failure_is_never_remembered(venue):
    venue.ok = False
    first = await vb.read_balance("7", "bybit", KEYS)
    assert first["ok"] is False and first["equity_usd"] is None
    venue.ok = True
    second = await vb.read_balance("7", "bybit", KEYS)
    assert len(venue.calls) == 2 and second["ok"] is True, "an outage outlived its end"


async def test_a_timeout_is_said_and_not_remembered(venue):
    venue.delay = 0.5
    first = await vb.read_balance("7", "bybit", KEYS, timeout=0.01)
    assert first["ok"] is False and first["detail"] == "venue timeout"
    venue.delay = 0.0
    second = await vb.read_balance("7", "bybit", KEYS)
    assert second["ok"] is True and len(venue.calls) == 2


async def test_one_caller_giving_up_does_not_cancel_the_others_read(venue):
    venue.delay = 0.1
    patient = asyncio.ensure_future(vb.read_balance("7", "bybit", KEYS))
    await asyncio.sleep(0)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(vb.read_balance("7", "bybit", KEYS), timeout=0.01)
    got = await patient
    assert got["ok"] is True and len(venue.calls) == 1


async def test_the_memory_is_bounded_and_drops_the_least_recently_asked(venue, monkeypatch):
    monkeypatch.setattr(vb, "MAX_READINGS", 3)
    for uid in ("1", "2", "3"):
        await vb.read_balance(uid, "bybit", KEYS)
    await vb.read_balance("1", "bybit", KEYS)        # asked again: now the newest
    assert len(venue.calls) == 3
    await vb.read_balance("4", "bybit", KEYS)        # over the bound: "2" goes
    assert len(vb._readings) == 3
    await vb.read_balance("1", "bybit", KEYS)
    assert len(venue.calls) == 4, "the reading asked for most recently was dropped"
    await vb.read_balance("2", "bybit", KEYS)
    assert len(venue.calls) == 5, "the least recently asked reading was kept past the bound"


def test_another_loops_read_is_never_awaited(venue):
    async def _park():
        fut = asyncio.get_running_loop().create_future()
        vb._inflight[vb._key("7", "bybit", KEYS, False)] = fut
    asyncio.run(_park())                       # that loop is closed now
    got = asyncio.run(vb.read_balance("7", "bybit", KEYS))
    assert got["ok"] is True and len(venue.calls) == 1


def test_the_digest_is_salted_and_never_the_key():
    d = vb._digest(KEYS)
    assert KEYS["api_key"] not in d and KEYS["api_secret"] not in d
    assert d == vb._digest(dict(reversed(list(KEYS.items())))), "field order made a new reading"


# ── the two cards ───────────────────────────────────────────────────────────

class _Store:
    def __init__(self, venues=("bybit", "bitget"), unreadable=()):
        self.venues = list(venues)
        self.unreadable = set(unreadable)

    def has(self, tg):
        return bool(self.venues)

    def list_venues(self, tg):
        return list(self.venues)

    def get_venue(self, tg):
        return self.venues[0]

    def get(self, tg):
        return self.get_for_venue(tg, self.venues[0])

    def get_for_venue(self, tg, venue):
        return None if venue in self.unreadable else {**KEYS, "venue": venue}


async def test_both_cards_and_a_reload_are_one_call_per_venue(venue, monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    monkeypatch.setattr(xc, "get_credential_store", lambda: _Store())
    async with client_for(FakeEngine(), FakeHandler(AUTHED)) as c:
        for path in ("/networth?telegram_id=7", "/holdings?telegram_id=7",
                     "/holdings?telegram_id=7", "/networth?telegram_id=7"):
            assert (await c.get(path, headers=HDRS)).status == 200
    assert sorted(v for v, _ in venue.calls) == ["bitget", "bybit"]


async def test_the_cards_say_when_the_venue_answered(venue, monkeypatch, clock):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    monkeypatch.setattr(xc, "get_credential_store", lambda: _Store())
    async with client_for(FakeEngine(), FakeHandler(AUTHED)) as c:
        nw = await (await c.get("/networth?telegram_id=7", headers=HDRS)).json()
        assert nw["updated_at"] == nw["cex"]["read_at"]
        await asyncio.sleep(1.1)                   # the wall clock moves on
        hd = await (await c.get("/holdings?telegram_id=7", headers=HDRS)).json()
    by_venue = {r["venue"]: r for r in hd["venues"]}
    assert by_venue["bybit"]["read_at"] == nw["cex"]["read_at"], "the shared reading was read twice"
    assert hd["updated_at"] == min(r["read_at"] for r in hd["venues"]), \
        "the card is dated by its newest reading, not its oldest"


async def test_a_total_needs_every_venue(venue, monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    async with client_for(FakeEngine(), FakeHandler(AUTHED)) as c:
        monkeypatch.setattr(xc, "get_credential_store", lambda: _Store())
        whole = await (await c.get("/holdings?telegram_id=7", headers=HDRS)).json()
        monkeypatch.setattr(xc, "get_credential_store",
                            lambda: _Store(venues=("bybit", "okx"), unreadable=("okx",)))
        part = await (await c.get("/holdings?telegram_id=7", headers=HDRS)).json()
    assert whole["venue_total_usd"] == round(512.34 * 2, 2) and whole["venues_unread"] == 0
    assert part["venue_total_usd"] is None, "one venue's balance was printed as the total"
    assert part["venues_unread"] == 1


async def test_a_failed_read_is_logged_by_its_class(venue, monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    lines: list[str] = []
    monkeypatch.setattr(ug, "audit", lambda _log, msg, **kw: lines.append(msg))

    def _boom():
        raise OSError("/srv/secret/exchange_creds.enc: permission denied")
    monkeypatch.setattr(xc, "get_credential_store", _boom)
    async with client_for(FakeEngine(), FakeHandler(AUTHED)) as c:
        body = await (await c.get("/holdings?telegram_id=7", headers=HDRS)).json()
    assert body["error"] == "holdings_unavailable"
    assert lines == ["Holdings read failed for 7: OSError"]
