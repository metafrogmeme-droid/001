"""A crowd that scans together shares one read, and a group chat gets one card.

No on-demand scan shared anything between callers. The Telegram limiter is
per person, so twenty people sending /scan in one second ran twenty full
market sweeps, and each card fetched eighteen candle series through the
operator's TRADING client with no cache: 360 requests queued in front of the
next order. /deepscan and /fullscan did the same at 115 and 67 symbols. In a
group, every member's /scan posted its own copy of the same card. A second
/forcescan or Latest-Signal tap during a scan was handed a zero-count dict
marked "skipped" that nobody read, and said "Signals found: 0" / "No trade
setups found" about a scan that had not happened.

Driven here:
- `ScanFlights`: callers together share one read; a finished read is served
  with its age inside the TTL and read again after it; a failed or unkept
  read is never served; a caller who gives up does not cancel it; at most
  `max_concurrent` distinct reads run at once;
- the market sweep: twenty /scan calls make one sweep, each caller's list is
  its own copy, and a sweep in which every venue failed is read again;
- candle reads: callers who miss together make one fetch, and the scan card
  reads candles from the market-data client through that cache, never from
  the trading client;
- a deep scan is shared per timeframe, and a budget-cut partial is not kept;
- a group chat gets one card per scan: the others are told it is running or
  pointed at it, private chats are not collapsed, and a failed scan frees the
  chat;
- a second force scan joins the first, and a "skipped" answer is never read
  as "nothing found".
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.core import scan_flight as sf
from bot.core.scan_flight import ScanFlights, Shared


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def _fresh_flights(monkeypatch):
    monkeypatch.setattr(sf, "FLIGHTS", ScanFlights(max_concurrent=3))
    monkeypatch.setattr(sf, "ROOMS", sf.RoomScans())


# ── the flights ──────────────────────────────────────────────────────────

class TestScanFlights:
    def test_callers_together_share_one_read(self):
        calls = []

        async def read():
            calls.append(1)
            await asyncio.sleep(0.02)
            return {"n": 1}

        async def go():
            fl = ScanFlights()
            return await asyncio.gather(*[fl.run("k", read, ttl_s=30) for _ in range(20)])
        out = _run(go())
        assert len(calls) == 1
        assert [o.how for o in out].count("ran") == 1
        assert [o.how for o in out].count("joined") == 19
        assert all(o.value is out[0].value for o in out)

    def test_a_finished_read_is_served_with_its_age_then_read_again(self):
        now = [100.0]
        calls = []

        async def read():
            calls.append(1)
            return len(calls)

        async def go():
            fl = ScanFlights(clock=lambda: now[0])
            a = await fl.run("k", read, ttl_s=30)
            now[0] += 12
            b = await fl.run("k", read, ttl_s=30)
            now[0] += 31
            c = await fl.run("k", read, ttl_s=30)
            return a, b, c
        a, b, c = _run(go())
        assert (a.how, a.value) == ("ran", 1)
        assert (b.how, b.value, round(b.age_s)) == ("cached", 1, 12)
        assert (c.how, c.value) == ("ran", 2)

    def test_a_failed_read_reaches_every_waiter_and_is_not_kept(self):
        calls = []

        async def read():
            calls.append(1)
            await asyncio.sleep(0.01)
            raise RuntimeError("venue down")

        async def go():
            fl = ScanFlights()
            res = await asyncio.gather(*[fl.run("k", read, ttl_s=30) for _ in range(5)],
                                       return_exceptions=True)
            again = await asyncio.gather(fl.run("k", read, ttl_s=30), return_exceptions=True)
            return res, again
        res, again = _run(go())
        assert all(isinstance(r, RuntimeError) for r in res)
        assert len(calls) == 2, "a failed read was served to the next caller"
        assert isinstance(again[0], RuntimeError)

    def test_an_unkept_value_is_read_again(self):
        calls = []

        async def read():
            calls.append(1)
            return "partial"

        async def go():
            fl = ScanFlights()
            await fl.run("k", read, ttl_s=30, keep=lambda v: False)
            await fl.run("k", read, ttl_s=30, keep=lambda v: False)
        _run(go())
        assert len(calls) == 2

    def test_a_caller_who_gives_up_does_not_cancel_it_for_the_others(self):
        async def read():
            await asyncio.sleep(0.05)
            return "done"

        async def go():
            fl = ScanFlights()
            first = asyncio.ensure_future(fl.run("k", read, ttl_s=30))
            await asyncio.sleep(0)
            second = asyncio.ensure_future(fl.run("k", read, ttl_s=30))
            await asyncio.sleep(0.01)
            first.cancel()
            return await second
        assert _run(go()).value == "done"

    def test_distinct_reads_queue_past_the_cap(self):
        live = [0]
        peak = [0]

        async def read():
            live[0] += 1
            peak[0] = max(peak[0], live[0])
            await asyncio.sleep(0.01)
            live[0] -= 1
            return 1

        async def go():
            fl = ScanFlights(max_concurrent=2)
            await asyncio.gather(*[fl.run(i, read, ttl_s=30) for i in range(6)])
        _run(go())
        assert peak[0] == 2

    def test_the_note_says_how_old_a_shared_read_is(self):
        assert sf.shared_note(Shared(1, "ran", 0.0), 45) == ""
        assert "Joined" in sf.shared_note(Shared(1, "joined", 0.0), 45)
        note = sf.shared_note(Shared(1, "cached", 17.4), 45)
        assert "17s old" in note and "45s" in note


# ── the market sweep ─────────────────────────────────────────────────────

def _signal(sym, chg=1.0):
    from bot.utils.models import MarketSignal
    return MarketSignal(symbol=sym, price=10.0, change_pct_24h=chg,
                        volume_usd_24h=5_000_000.0)


def _engine(errors=None, delay=0.02):
    calls = []

    async def scan():
        calls.append(1)
        await asyncio.sleep(delay)
        return [_signal("BTC/USDT", 2.0), _signal("ETH/USDT", -1.0)]
    scanner = SimpleNamespace(scan=scan, last_fetch_errors=dict(errors or {}))
    return SimpleNamespace(scanner=scanner, _last_scan_signals=None), calls


class TestMarketSweep:
    def test_twenty_scans_make_one_sweep(self):
        from bot.skills.skill_registry import ScanMarketSkill
        eng, calls = _engine()

        async def go():
            return await asyncio.gather(*[ScanMarketSkill().execute(eng) for _ in range(20)])
        texts = _run(go())
        assert len(calls) == 1
        assert all("MARKET SCANNER" in t for t in texts)
        assert sum("Joined a scan" in t for t in texts) == 19

    def test_each_caller_gets_its_own_copy(self):
        from bot.skills.skill_registry import market_sweep
        eng, _ = _engine()

        async def go():
            a, b = await asyncio.gather(market_sweep(eng), market_sweep(eng))
            a.value[0].sort(key=lambda s: s.symbol, reverse=True)
            a.value[0][0].price = 999.0
            return a, b
        a, b = _run(go())
        assert [s.symbol for s in b.value[0]] == ["BTC/USDT", "ETH/USDT"]
        assert b.value[0][0].price == 10.0

    def test_a_sweep_where_every_venue_failed_is_read_again(self):
        from bot.skills.skill_registry import market_sweep
        eng, calls = _engine(errors={"spot": "Timeout", "futures": "Timeout"}, delay=0)

        async def go():
            await market_sweep(eng)
            return await market_sweep(eng)
        second = _run(go())
        assert len(calls) == 2 and second.how == "ran"

    def test_a_partial_sweep_is_kept_and_says_partial(self):
        from bot.skills.skill_registry import ScanMarketSkill
        eng, calls = _engine(errors={"spot": "Timeout", "futures": None}, delay=0)

        async def go():
            a = await ScanMarketSkill().execute(eng)
            b = await ScanMarketSkill().execute(eng)
            return a, b
        a, b = _run(go())
        assert len(calls) == 1
        assert "that venue was not read" in a and "that venue was not read" in b
        assert "From a scan" in b


# ── candles ──────────────────────────────────────────────────────────────

def _bars(n=30):
    base = 1_700_000_000_000
    return [[base + i * 3_600_000, 1, 2, 0.5, 1.5, 10] for i in range(n)]


class TestCandles:
    def test_callers_who_miss_together_make_one_fetch(self):
        from bot.core.engine import RuneClawEngine
        eng = RuneClawEngine()
        eng.detach_state_persistence()
        calls = []

        async def fetch_ohlcv(sym, tf, limit=100):
            calls.append(sym)
            await asyncio.sleep(0.02)
            return _bars(limit)
        ex = SimpleNamespace(fetch_ohlcv=fetch_ohlcv)

        async def go():
            return await asyncio.gather(*[
                eng._cached_ohlcv(ex, "BTC/USDT", "1h", limit=30, ttl=900)
                for _ in range(20)])
        out = _run(go())
        assert calls == ["BTC/USDT"]
        assert all(o == out[0] for o in out) and out[0]

    def test_the_scan_card_reads_the_market_client_never_the_trading_one(self):
        from bot.skills.scan_commands import ScanCommands
        fetched = []

        async def fetch_ohlcv(sym, tf, limit=100):
            fetched.append(sym)
            await asyncio.sleep(0.01)
            return _bars(limit)
        market = SimpleNamespace(fetch_ohlcv=fetch_ohlcv)

        async def _trading():
            raise AssertionError("the scan card asked the trading client")
        from bot.core.engine import RuneClawEngine
        eng = RuneClawEngine()
        eng.detach_state_persistence()
        eng.live_executor._get_exchange = _trading

        async def _futures():
            return market
        eng.get_futures_exchange = _futures
        sent = []

        async def _send_photo(update, png, caption):
            sent.append(caption)
            return True
        host = SimpleNamespace(engine=eng, _send_photo=_send_photo)
        signals = [_signal(f"S{i}/USDT") for i in range(18)]

        async def go():
            return await asyncio.gather(*[
                ScanCommands._render_scan_signals_card(host, None, signals, "MARKET SCAN")
                for _ in range(10)])
        _run(go())
        assert sorted(fetched) == sorted(s.symbol for s in signals), (
            "ten cards fetched each series more than once")


# ── deep scan ────────────────────────────────────────────────────────────

class TestDeepScan:
    def _skill(self, keep=True):
        from bot.skills.skill_registry import DeepScanSkill
        calls = []

        async def _sweep(engine, **kw):
            calls.append(kw.get("timeframe"))
            await asyncio.sleep(0.02)
            kw["_meta"].update(top=[{"symbol": "BTC/USDT", "score": 3}], keep=keep)
            return "DEEP SCAN text"
        skill = DeepScanSkill()
        skill._sweep = _sweep
        return skill, calls

    def test_one_run_per_timeframe(self):
        skill, calls = self._skill()
        eng = SimpleNamespace(_last_deepscan_hits=None)

        async def go():
            return await asyncio.gather(
                *[skill.execute(eng, timeframe="4h") for _ in range(8)],
                skill.execute(eng, timeframe="1h"))
        out = _run(go())
        assert sorted(calls) == ["1h", "4h"]
        assert sum("Joined a scan" in t for t in out) == 7
        assert eng._last_deepscan_hits == [{"symbol": "BTC/USDT", "score": 3}]

    def test_a_budget_cut_partial_is_not_kept(self):
        skill, calls = self._skill(keep=False)
        eng = SimpleNamespace(_last_deepscan_hits=None)

        async def go():
            await skill.execute(eng, timeframe="4h")
            await skill.execute(eng, timeframe="4h")
        _run(go())
        assert calls == ["4h", "4h"]


# ── one card per group chat ──────────────────────────────────────────────

def _scan_host(dispatch_delay=0.05, fail=False):
    from bot.skills.scan_commands import ScanCommands
    dispatched = []
    sent = []

    async def dispatch(name, engine, **kw):
        dispatched.append(kw.get("user_id"))
        await asyncio.sleep(dispatch_delay)
        if fail:
            raise RuntimeError("venue down")
        return "MARKET SCANNER text"

    async def _send(update, text, **kw):
        sent.append((update.effective_chat.id, update.effective_user.id, text))
    host = SimpleNamespace(
        engine=SimpleNamespace(_last_scan_signals=None),
        registry=SimpleNamespace(dispatch=dispatch),
        _send=_send, _get_tg_id=lambda u: str(u.effective_user.id),
        _lang=lambda u: "en",
        _render_scan_signals_card=AsyncMock(return_value=False))
    fn = getattr(ScanCommands._cmd_scan, "__wrapped__", ScanCommands._cmd_scan)
    return host, fn, dispatched, sent


def _upd(chat_id, user_id, chat_type="supergroup"):
    u = MagicMock()
    u.effective_chat = MagicMock()
    u.effective_chat.id = chat_id
    u.effective_chat.type = chat_type
    u.effective_chat.send_chat_action = AsyncMock()
    u.effective_user = MagicMock()
    u.effective_user.id = user_id
    return u


class TestGroupChats:
    def test_five_members_together_get_one_card(self):
        host, fn, dispatched, sent = _scan_host()
        ctx = SimpleNamespace(args=[])

        async def go():
            await asyncio.gather(*[fn(host, _upd(-100, uid), ctx) for uid in range(1, 6)])
        _run(go())
        assert len(dispatched) == 1
        results = [t for _, _, t in sent if "MARKET SCANNER text" in t]
        running = [t for _, _, t in sent if "already running" in t]
        assert len(results) == 1 and len(running) == 4

    def test_a_member_after_the_card_is_pointed_at_it(self):
        host, fn, dispatched, sent = _scan_host(dispatch_delay=0)
        ctx = SimpleNamespace(args=[])

        async def go():
            await fn(host, _upd(-100, 1), ctx)
            await fn(host, _upd(-100, 2), ctx)
        _run(go())
        assert len(dispatched) == 1
        assert "still current" in sent[-1][2] and "Scroll up" in sent[-1][2]

    def test_private_chats_are_not_collapsed(self):
        host, fn, dispatched, sent = _scan_host(dispatch_delay=0)
        ctx = SimpleNamespace(args=[])

        async def go():
            await asyncio.gather(fn(host, _upd(1, 1, "private"), ctx),
                                 fn(host, _upd(2, 2, "private"), ctx))
        _run(go())
        assert len(dispatched) == 2
        assert sum("MARKET SCANNER text" in t for _, _, t in sent) == 2

    def test_a_failed_scan_frees_the_chat(self):
        host, fn, dispatched, sent = _scan_host(dispatch_delay=0, fail=True)
        ctx = SimpleNamespace(args=[])

        async def go():
            for uid in (1, 2):
                try:
                    await fn(host, _upd(-100, uid), ctx)
                except RuntimeError:
                    pass
        _run(go())
        assert len(dispatched) == 2, "a failed scan locked the chat out"

    def test_a_claim_nobody_settled_goes_stale(self):
        now = [0.0]
        rooms = sf.RoomScans(stale_s=180, clock=lambda: now[0])
        assert rooms.claim(-1, "market", 45)[0] == "run"
        assert rooms.claim(-1, "market", 45)[0] == "running"
        now[0] = 181
        assert rooms.claim(-1, "market", 45)[0] == "run"


# ── force scan ───────────────────────────────────────────────────────────

class TestForceScan:
    def _engine(self):
        from bot.core.engine import RuneClawEngine
        eng = RuneClawEngine()
        eng.detach_state_persistence()
        runs = []

        async def _locked(max_symbols=None, lightweight=False):
            runs.append(max_symbols)
            await asyncio.sleep(0.03)
            return {"signals": 7, "ideas": 2, "auto_confirmed": 0, "pending": 2,
                    "cleared_pending": 0}
        eng._force_scan_locked = _locked
        return eng, runs

    def test_a_second_caller_joins_the_first(self):
        eng, runs = self._engine()

        async def go():
            return await asyncio.gather(eng.force_scan(), eng.force_scan(max_symbols=40))
        a, b = _run(go())
        assert runs == [None]
        assert a["signals"] == b["signals"] == 7
        assert b.get("joined") is True and not a.get("joined")

    def test_forcescan_never_reads_skipped_as_zero_found(self):
        from bot.skills.engine_ops_commands import EngineOpsCommands
        sent = []

        async def _send(update, text, **kw):
            sent.append(text)
        skipped = {"signals": 0, "ideas": 0, "auto_confirmed": 0, "pending": 0,
                   "cleared_pending": 0, "skipped": "scan_already_running"}
        host = SimpleNamespace(_send=_send, engine=SimpleNamespace(
            force_scan=AsyncMock(return_value=skipped)))
        fn = getattr(EngineOpsCommands._cmd_forcescan, "__wrapped__",
                     EngineOpsCommands._cmd_forcescan)
        _run(fn(host, MagicMock(), MagicMock()))
        assert "already running" in sent[-1]
        assert "Signals found" not in sent[-1]

    def test_a_joined_scan_says_so(self):
        from bot.skills.engine_ops_commands import EngineOpsCommands
        sent = []

        async def _send(update, text, **kw):
            sent.append(text)
        joined = {"signals": 7, "ideas": 2, "auto_confirmed": 0, "pending": 2,
                  "cleared_pending": 0, "joined": True}
        host = SimpleNamespace(_send=_send, engine=SimpleNamespace(
            force_scan=AsyncMock(return_value=joined)))
        fn = getattr(EngineOpsCommands._cmd_forcescan, "__wrapped__",
                     EngineOpsCommands._cmd_forcescan)
        _run(fn(host, MagicMock(), MagicMock()))
        assert "joined the scan already running" in sent[-1]
        assert "Signals found: <b>7</b>" in sent[-1]


# ── full scan ────────────────────────────────────────────────────────────

class TestFullScan:
    def test_two_callers_share_one_bounded_read(self, monkeypatch):
        from bot.skills import scan_skill
        live = [0]
        peak = [0]
        calls = []

        async def _scan_symbol(exchange, sym, analyzer):
            calls.append(sym)
            live[0] += 1
            peak[0] = max(peak[0], live[0])
            await asyncio.sleep(0.005)
            live[0] -= 1
            return None     # nothing readable: the read ends at the verdict
        monkeypatch.setattr(scan_skill, "_scan_symbol", _scan_symbol)

        async def _get_exchange():
            return object()
        eng = SimpleNamespace(scanner=SimpleNamespace(_get_exchange=_get_exchange),
                              analyzer=None)

        def _update():
            msg = SimpleNamespace(edit_text=AsyncMock())
            return SimpleNamespace(message=SimpleNamespace(
                reply_text=AsyncMock(return_value=msg)), effective_chat=None), msg

        (u1, m1), (u2, m2) = _update(), _update()

        async def go():
            await asyncio.gather(
                scan_skill._scan_batch(u1, MagicMock(), eng, 10, False, False),
                scan_skill._scan_batch(u2, MagicMock(), eng, 10, False, False))
        _run(go())
        assert len(calls) == len(scan_skill.UNIVERSE), "two callers read the universe twice"
        assert peak[0] <= 10, peak[0]
        assert m1.edit_text.await_count == m2.edit_text.await_count == 1
