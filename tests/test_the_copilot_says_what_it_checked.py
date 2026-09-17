"""THE SECOND OPINION SAYS WHAT IT LOOKED AT.

Driven on the tree before this slice, a clean long — R:R 3, stop 2%, margin on
the ticket — with nothing else readable came back:

    ✅ Looks disciplined (score 100/100)
    R:R 3 · stop 2% · target 6%

Three of the co-pilot's five subjects never ran and the card said nothing, on
the block a person reads immediately before confirming a real order. That is
``integrity_veto.assess({})`` verbatim.

And two of those three had no writer at all. ``engine_bias`` and
``existing_exposure`` appear nowhere in the tree outside ``review()``'s
signature and the two gateway lines that read them off the request body — and
``app/routes/webtrade.js``, the only caller of that endpoint, posts a fixed
six-key body carrying neither. Both branches were covered, by tests that call
``review()`` with the kwargs; a socket with no cable, proved in a place no
production caller can reach.

What this file drives, in order of what a wrong answer would cost:
  * a review that could not look at everything never says ``clear``;
  * the score carries its own span, so 100/100 over two checks and 100/100 over
    four are not one number;
  * a READ zero equity is a FLAG, not a gap — a measurement is not an absence;
  * ``flat`` is a measurement and ``None`` is a book nobody read;
  * the gateway READS the three inputs and refuses to take two of them from
    the client;
  * in live mode the equity is the LIVE book, and the exposure comes off the
    same executor;
  * a manual idea is not the engine's bias.
"""
from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.core import copilot_context as cc
from bot.core import trade_copilot as cp


def _long(**over):
    t = {"direction": "LONG", "symbol": "BTC", "entry": 100.0, "sl": 98.0, "tp": 106.0}
    t.update(over)
    return t


def _all_read(trade=None, **over):
    """A review with every subject supplied — the only shape that may be `clear`."""
    kw = {"equity_usd": 1000.0, "engine_bias": "long", "existing_exposure": "flat"}
    kw.update(over)
    return cp.review(trade or _long(margin=50.0), **kw)


# ── the review says what it checked ───────────────────────────────────────

class TestNothingUnreadableIsCleared:
    def test_a_ticket_with_nothing_else_readable_is_partial_not_clear(self):
        r = cp.review(_long(margin=5000.0))
        assert r["verdict"] == cp.VERDICT_PARTIAL
        assert r["verdict"] != cp.VERDICT_CLEAR
        txt = cp.human_readable(r)
        assert "Looks disciplined" not in txt

    def test_clear_needs_every_subject_looked_at(self):
        r = _all_read()
        assert r["verdict"] == cp.VERDICT_CLEAR
        assert r["unchecked"] == []
        assert set(r["checks"]) == set(cp.ALL_CHECKS)
        assert all(v == "ok" for v in r["checks"].values())

    def test_dropping_one_input_drops_clear(self):
        # One subject at a time: each is load-bearing for the word.
        for missing in ("equity_usd", "engine_bias", "existing_exposure"):
            r = _all_read(**{missing: None})
            assert r["verdict"] == cp.VERDICT_PARTIAL, missing
            assert [u["name"] for u in r["unchecked"]], missing

    def test_a_flag_outranks_a_gap_and_the_gap_is_still_printed(self):
        # Something found AND something not looked at. `caution` is the louder
        # fact and must win, but folding the two would LOSE the coverage list.
        r = cp.review(_long(tp=101.0))          # R:R 0.5 -> a flag
        assert r["verdict"] == cp.VERDICT_CAUTION
        assert [u["name"] for u in r["unchecked"]]
        txt = cp.human_readable(r)
        assert "Reward:risk" in txt and "Not checked" in txt


class TestTheScoreCarriesItsSpan:
    def test_two_hundreds_over_different_coverage_are_not_one_number(self):
        thin = cp.review(_long())                       # 2 of 4 ran
        full = _all_read()                              # 4 of 4 ran
        assert thin["score"] == full["score"] == 100
        assert thin["score_basis"] == {"applied": 2, "total": 4}
        assert full["score_basis"] == {"applied": 4, "total": 4}
        assert cp.score_line(thin) != cp.score_line(full)

    def test_the_span_is_in_the_sentence(self):
        assert "2 of the 4" in cp.score_line(cp.review(_long()))
        assert "all 4" in cp.score_line(_all_read())

    def test_exposure_is_covered_but_never_scored(self):
        # It only ever adds a note, so counting it would make the basis a count
        # of something other than what the score is over.
        assert "existing_exposure" in cp.ALL_CHECKS
        assert "existing_exposure" not in cp.SCORED_CHECKS
        with_expo = _all_read()
        without = _all_read(existing_exposure=None)
        assert with_expo["score_basis"]["applied"] == without["score_basis"]["applied"]


class TestAMeasurementIsNotAGap:
    def test_a_read_zero_equity_is_a_flag_not_an_unchecked(self):
        r = cp.review(_long(margin=500.0), equity_usd=0.0,
                      engine_bias="long", existing_exposure="flat")
        assert r["checks"]["size_vs_equity"] == "flag"
        assert "size_vs_equity" not in [u["name"] for u in r["unchecked"]]
        assert any("nothing for" in f["msg"] for f in r["flags"])
        assert r["verdict"] == cp.VERDICT_CAUTION

    def test_an_unreadable_equity_is_a_gap_not_a_flag(self):
        r = cp.review(_long(margin=500.0), equity_usd=None)
        assert r["checks"]["size_vs_equity"] == "unchecked"
        assert not any("nothing for" in f["msg"] for f in r["flags"])

    def test_no_margin_and_no_equity_are_different_sentences(self):
        no_margin = cp.review(_long())
        no_equity = cp.review(_long(margin=500.0))
        def _why(rev):
            return [u["reason"] for u in rev["unchecked"] if u["name"] == "size_vs_equity"][0]
        assert _why(no_margin) != _why(no_equity)
        assert "margin" in _why(no_margin)

    def test_a_small_share_is_small_never_zero(self):
        # `{:.0f}` printed "Margin is 0% of equity" for a real 0.5% stake, on
        # the line whose job is to say how much of the account is at risk. And
        # 0.5 is `:.0f`'s own edge -- it rounds to "0" -- so the rule has to
        # test what the FORMAT prints rather than a threshold guessed beside it.
        half = cp.review(_long(margin=50.0), equity_usd=10_000.0)
        assert any("<1% of equity" in n for n in half["notes"]), half["notes"]
        # A margin of zero really is zero and says so.
        none = cp.review(_long(margin=0.0), equity_usd=10_000.0)
        assert any("is 0% of equity" in n for n in none["notes"])

    def test_flat_is_read_and_None_is_not(self):
        flat = cp.review(_long(), existing_exposure="flat")
        unread = cp.review(_long(), existing_exposure=None)
        assert flat["checks"]["existing_exposure"] == "ok"
        assert any("No open position" in n for n in flat["notes"])
        assert unread["checks"]["existing_exposure"] == "unchecked"


class TestTheReasonIsTheCallersOwn:
    def test_the_callers_sentence_wins(self):
        r = cp.review(_long(margin=500.0), unread={
            "size_vs_equity": "your live balance was not read recently"})
        assert any(u["reason"] == "your live balance was not read recently"
                   for u in r["unchecked"])

    def test_a_subject_nobody_explained_gets_a_neutral_one_never_a_guess(self):
        r = cp.review(_long(margin=500.0))
        why = {u["name"]: u["reason"] for u in r["unchecked"]}
        assert why["engine_bias"] == cp._NOT_SUPPLIED["engine_bias"]
        # And the module never invents a cause: nothing in the default text
        # claims a venue, a key or an account state.
        for text in cp._NOT_SUPPLIED.values():
            assert "could not" not in text


class TestTheInvalidTicketSaysNothingElseRan:
    def test_every_subject_is_reported_unchecked_with_one_reason(self):
        r = cp.review({"direction": "LONG", "symbol": "BTC",
                       "entry": 100, "sl": 101, "tp": 110})
        assert r["verdict"] == cp.VERDICT_INVALID
        assert [u["name"] for u in r["unchecked"]] == list(cp.ALL_CHECKS)
        assert len({u["reason"] for u in r["unchecked"]}) == 1
        # No score was computed, so none is reported. `0` would read as a
        # measured zero on a review that measured nothing.
        assert r["score"] is None


class TestTheRatioIsNeverAbsentAfterGeometry:
    def test_no_valid_geometry_yields_a_zero_risk(self):
        # The `if risk > 0 else None` this module used to carry could not fire,
        # and the two renderers disagreed about whether it could. Driven over a
        # grid rather than asserted from reading: the day the geometry gate
        # loosens, this fails instead of the card dividing by zero.
        prices = [0.0, 1.0, 50.0, 99.0, 100.0, 101.0, 1e9]
        seen = 0
        for e in prices:
            for sl in prices:
                for tp in prices:
                    for d in ("LONG", "SHORT"):
                        r = cp.review({"direction": d, "symbol": "X",
                                       "entry": e, "sl": sl, "tp": tp})
                        if r["verdict"] == cp.VERDICT_INVALID:
                            continue
                        seen += 1
                        assert r["rr"] is not None
                        assert abs(e - sl) > 0
        assert seen > 0, "the grid produced no valid geometry; it proves nothing"


class TestOneLabelTable:
    def test_the_text_renderer_prints_the_table_not_the_identifier(self):
        txt = cp.human_readable(cp.review(_long()))
        assert "size vs equity" in txt
        assert "size_vs_equity" not in txt

    def test_every_covered_subject_has_a_label(self):
        assert set(cp.CHECK_LABELS) == set(cp.ALL_CHECKS)

    def test_each_unchecked_row_carries_its_label(self):
        for r in (cp.review(_long()),
                  cp.review({"direction": "LONG", "symbol": "B",
                             "entry": 100, "sl": 101, "tp": 110})):
            for u in r["unchecked"]:
                assert u["label"] == cp.CHECK_LABELS[u["name"]]


# ── the reading: one book, and the engine's own lean ──────────────────────

class _Portfolio:
    def __init__(self, equity, positions):
        self._e, self.open_positions = equity, positions

    def snapshot(self):
        return SimpleNamespace(equity_usd=self._e)


class _Registry:
    def __init__(self, mapping):
        self._m = mapping

    def get(self, uid):
        return self._m[str(uid)]


def _pos(symbol, direction, status="open"):
    return SimpleNamespace(symbol=symbol, direction=direction, status=status)


def _idea(asset, direction, source="scan", age_s=10.0):
    return SimpleNamespace(asset=asset, direction=direction, source=source,
                           timestamp=datetime.now(UTC) - timedelta(seconds=age_s))


class _RaisingBook:
    """An executor whose position listing raises while its balance reads fine.

    Two reads, one account: on the live branch they fail independently, and a
    fixture that cannot produce that state cannot tell one reason from two.
    """

    @property
    def open_positions(self):
        raise RuntimeError("venue refused the position listing")


class _Engine:
    """Asymmetric on purpose: a stub that answers plausibly for both books
    agrees with a reading that consults the wrong one."""

    def __init__(self, *, paper_equity=10_000.0, paper_positions=(),
                 live_total=None, live_positions=(), scope="own", ideas=None,
                 live_positions_raise=False):
        self.user_portfolios = _Registry(
            {"u1": _Portfolio(paper_equity, list(paper_positions))})
        self._scope = scope
        self._live_total = live_total
        self._live_ex = (_RaisingBook() if live_positions_raise
                         else SimpleNamespace(open_positions=list(live_positions)))
        self._pending_ideas = dict(ideas or {})
        self.equity_calls = 0

    async def get_user_live_equity(self, user_id=""):
        self.equity_calls += 1
        return None

    def live_view(self, user_id="", max_age_s=900.0):
        if self._scope == "none":
            return {"scope": "none", "executor": None, "balance": None,
                    "total": None, "age_s": None}
        return {"scope": self._scope, "executor": self._live_ex,
                "balance": {}, "total": self._live_total, "age_s": 1.0}


# `bot.config.CONFIG` is a FROZEN dataclass, so the mode is swapped by replacing
# the name this module reads rather than by writing through it: `setattr` on the
# instance raises, and `object.__setattr__` would put the write outside
# monkeypatch's bookkeeping — the shape that leaked a gateway secret into 40
# later tests.
def _cfg(is_live: bool, ttl: float = 300.0):
    return SimpleNamespace(is_live=lambda: is_live, pending_idea_ttl=ttl)


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(cc, "CONFIG", _cfg(True))


@pytest.fixture
def paper(monkeypatch):
    monkeypatch.setattr(cc, "CONFIG", _cfg(False))


def _ctx(engine, uid="u1", symbol="BTC"):
    # The loop is CLOSED. Leaving one open per call leaks a file descriptor per
    # test and the warning it emits is exactly the kind a suite learns to skip.
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(cc.ticket_context(engine, uid, symbol))
    finally:
        loop.close()


class TestOneBook:
    def test_paper_mode_reads_the_paper_book(self, paper):
        eng = _Engine(paper_equity=10_000.0,
                      paper_positions=[_pos("BTC/USDT:USDT", "LONG")])
        ctx = _ctx(eng)
        assert ctx["book"] == cc.BOOK_PAPER
        assert ctx["equity_usd"] == 10_000.0
        assert ctx["exposure"] == cc.SIDE_LONG
        assert eng.equity_calls == 0      # no venue fetch on a paper ticket

    def test_live_mode_reads_the_LIVE_book_not_the_paper_baseline(self, live):
        # The defect: `user_portfolios.get(tg_id).snapshot().equity_usd` for a
        # ticket that opens a real position. The two books are far apart here so
        # a reading of the wrong one is a different number, not a coincidence.
        eng = _Engine(paper_equity=10_000.0,
                      paper_positions=[_pos("BTC/USDT:USDT", "SHORT")],
                      live_total=250.0,
                      live_positions=[_pos("BTC/USDT:USDT", "LONG")])
        ctx = _ctx(eng)
        assert ctx["book"] == cc.BOOK_LIVE
        assert ctx["equity_usd"] == 250.0
        assert ctx["exposure"] == cc.SIDE_LONG      # the LIVE executor's side
        assert eng.equity_calls == 1                # the cache was refreshed

    def test_a_caller_with_no_live_account_gets_no_figure_at_all(self, live):
        eng = _Engine(paper_equity=10_000.0, scope="none")
        ctx = _ctx(eng)
        assert ctx["book"] == cc.BOOK_NONE
        assert ctx["equity_usd"] is None
        assert ctx["exposure"] is None
        assert "size_vs_equity" in ctx["unread"]

    def test_the_balance_and_the_positions_have_their_own_reasons(self, live):
        # One sentence for both was the first draft: `book_why` was the
        # EQUITY's reason and the exposure row borrowed it, so a venue that
        # refused the position listing would have been reported as a balance
        # that "was not read recently" — a wrong cause on the card, which is
        # this slice's own subject one field over.
        eng = _Engine(live_total=500.0, live_positions_raise=True)
        ctx = _ctx(eng)
        assert ctx["equity_usd"] == 500.0
        assert "size_vs_equity" not in ctx["unread"]
        assert "open positions could not be read" in ctx["unread"]["existing_exposure"]
        # And the other way round: a balance nobody could read beside a book
        # that read clean.
        other = _ctx(_Engine(live_total=None, live_positions=[]))
        assert "not read recently" in other["unread"]["size_vs_equity"]
        assert "existing_exposure" not in other["unread"]

    def test_an_unread_live_balance_is_named_not_zeroed(self, live):
        eng = _Engine(live_total=None, live_positions=[])
        ctx = _ctx(eng)
        assert ctx["equity_usd"] is None
        assert "not read recently" in ctx["unread"]["size_vs_equity"]
        # The positions still read: one source failing must not blank the rest.
        assert ctx["exposure"] == cc.SIDE_FLAT
        assert "existing_exposure" not in ctx["unread"]


class TestTheExposureIsReadOrRefused:
    def test_an_empty_book_is_flat_and_flat_is_a_measurement(self, paper):
        assert _ctx(_Engine(paper_positions=[]))["exposure"] == cc.SIDE_FLAT

    def test_a_row_whose_side_this_build_cannot_read_refuses(self, paper):
        eng = _Engine(paper_positions=[_pos("BTC/USDT:USDT", "SIDEWAYS")])
        ctx = _ctx(eng)
        assert ctx["exposure"] is None
        assert "cannot read" in ctx["unread"]["existing_exposure"]

    def test_both_sides_held_is_refused_never_netted(self, paper):
        eng = _Engine(paper_positions=[_pos("BTC/USDT:USDT", "LONG"),
                                       _pos("BTC/USDT:USDT", "SHORT")])
        ctx = _ctx(eng)
        assert ctx["exposure"] is None
        assert "BOTH" in ctx["unread"]["existing_exposure"]

    def test_the_two_refusals_are_not_one_sentence(self, paper):
        # They are different facts and only one of them is a failed read.
        # "could not be read" is FALSE of a book holding both sides: it WAS
        # read, and it is two sides. Both leave the check unrun, which is why
        # a single sentence for both looks harmless until somebody acts on it.
        unreadable = _ctx(_Engine(paper_positions=[_pos("BTC/USDT:USDT", "SIDEWAYS")]))
        hedged = _ctx(_Engine(paper_positions=[_pos("BTC/USDT:USDT", "LONG"),
                                               _pos("BTC/USDT:USDT", "SHORT")]))
        assert (unreadable["unread"]["existing_exposure"]
                != hedged["unread"]["existing_exposure"])

    def test_another_symbol_does_not_decide_this_one(self, paper):
        eng = _Engine(paper_positions=[_pos("ETH/USDT:USDT", "SHORT")])
        assert _ctx(eng)["exposure"] == cc.SIDE_FLAT

    def test_a_closed_row_is_not_an_open_position(self, paper):
        eng = _Engine(paper_positions=[_pos("BTC/USDT:USDT", "LONG", status="closed")])
        assert _ctx(eng)["exposure"] == cc.SIDE_FLAT

    def test_an_unreadable_symbol_is_not_flat(self, paper):
        ctx = _ctx(_Engine(paper_positions=[]), symbol="!!")
        assert ctx["exposure"] is None
        assert "symbol" in ctx["unread"]["existing_exposure"]


class TestTheEngineBiasIsTheEnginesOwn:
    def test_a_manual_idea_is_the_CALLERS_ticket_not_the_engines_lean(self, paper):
        # The whole correctness of this reading. `manual_trade.build_manual_idea`
        # stamps source="manual", so counting one tells somebody they are
        # "aligned with the engine's long bias" about their own earlier ticket.
        eng = _Engine(ideas={"a": _idea("BTC/USDT:USDT", "LONG", source="manual")})
        ctx = _ctx(eng)
        assert ctx["engine_bias"] is None
        assert "no open idea on BTC" in ctx["unread"]["engine_bias"]

    def test_a_scan_idea_is(self, paper):
        eng = _Engine(ideas={"a": _idea("BTC/USDT:USDT", "SHORT")})
        assert _ctx(eng)["engine_bias"] == cc.SIDE_SHORT

    def test_an_idea_older_than_the_ttl_is_not_current(self, paper, monkeypatch):
        monkeypatch.setattr(cc, "CONFIG", _cfg(False, ttl=300.0))
        eng = _Engine(ideas={"a": _idea("BTC/USDT:USDT", "SHORT", age_s=301.0)})
        assert _ctx(eng)["engine_bias"] is None

    def test_the_most_recent_idea_wins(self, paper):
        eng = _Engine(ideas={"old": _idea("BTC/USDT:USDT", "LONG", age_s=100.0),
                             "new": _idea("BTC/USDT:USDT", "SHORT", age_s=5.0)})
        assert _ctx(eng)["engine_bias"] == cc.SIDE_SHORT

    def test_another_symbols_idea_is_not_this_symbols_bias(self, paper):
        eng = _Engine(ideas={"a": _idea("ETH/USDT:USDT", "SHORT")})
        assert _ctx(eng)["engine_bias"] is None

    def test_an_engine_with_no_idea_book_is_unreadable_not_neutral(self, paper):
        eng = _Engine()
        eng._pending_ideas = None
        ctx = _ctx(eng)
        assert ctx["engine_bias"] is None
        assert "could not be read" in ctx["unread"]["engine_bias"]


# ── the wiring: the gateway READS, and the client does not get to assert ──

SECRET = "s" * 32
HDRS = {"X-Gateway-Secret": SECRET}


class _Users:
    def register(self, tg, name="", auto_role=""):
        return {"authorized": True, "role": "trader"}

    def get(self, tg):
        return {"authorized": True, "role": "trader"}

    def permission_denial(self, tg, cmd):
        return None

    def is_admitted(self, tg):
        return True


class _Handler:
    def __init__(self):
        self.users = _Users()
        self._limiter = SimpleNamespace(allow=lambda key: True)

    def _allowlist_ids(self):
        return set()


@contextlib.asynccontextmanager
async def _gateway(engine):
    from bot.web import user_gateway as ug
    app = ug.build_gateway(engine, _Handler())
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


@pytest.fixture
def secret(monkeypatch):
    from bot.web import user_gateway as ug
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)


_TICKET = {"telegram_id": "u1", "direction": "LONG", "symbol": "BTC",
           "entry": 100.0, "sl": 98.0, "tp": 106.0, "margin": 50.0}


class TestTheGatewayReadsTheThreeInputs:
    async def test_the_client_cannot_assert_the_bias_or_the_users_own_exposure(
            self, secret, monkeypatch):
        # THE DEFECT WITH THE SIGN FLIPPED. These two were read off the request
        # body and no caller in the tree ever sent them; wiring the reading and
        # leaving the body read would let a client assert the engine's opinion
        # and the caller's own book. A user's exposure is a fact about THEM.
        monkeypatch.setattr(cc, "CONFIG", _cfg(False))
        eng = _Engine(paper_equity=1000.0, paper_positions=[])   # flat, no ideas
        async with _gateway(eng) as c:
            r = await c.post("/trade/copilot",
                             json={**_TICKET, "engine_bias": "long",
                                   "existing_exposure": "long"},
                             headers=HDRS)
            d = await r.json()
        assert r.status == 200
        # The engine has no idea on BTC and the book is flat, so the REVIEW
        # must say so — not repeat what the body claimed.
        assert d["checks"]["engine_bias"] == "unchecked"
        assert not any("Aligned" in n for n in d["notes"])
        assert not any("stacks the position" in n for n in d["notes"])
        assert any("No open position" in n for n in d["notes"])

    async def test_in_live_mode_the_size_check_is_against_the_LIVE_book(
            self, secret, monkeypatch):
        monkeypatch.setattr(cc, "CONFIG", _cfg(True))
        # $50 of margin is 0.5% of the paper baseline and 25% of the live
        # account. Against the paper book that is a benign note; against the
        # book the order really opens on it is the concentration FLAG. Same
        # ticket, same user, opposite advice.
        eng = _Engine(paper_equity=10_000.0, live_total=200.0, live_positions=[])
        async with _gateway(eng) as c:
            d = await (await c.post("/trade/copilot", json=_TICKET,
                                    headers=HDRS)).json()
        assert d["book"] == cc.BOOK_LIVE
        assert any("25% of your equity" in f["msg"] for f in d["flags"]), d["flags"]
        assert d["checks"]["size_vs_equity"] == "flag"
        assert d["verdict"] == cp.VERDICT_CAUTION
        # And the paper figure is what the old handler would have used: a note.
        paper_read = cp.review({k: v for k, v in _TICKET.items()
                                if k != "telegram_id"}, equity_usd=10_000.0)
        assert any("<1% of equity" in n for n in paper_read["notes"])
        assert paper_read["checks"]["size_vs_equity"] == "ok"

    async def test_a_context_that_raises_still_reviews_what_needs_no_book(
            self, secret, monkeypatch):
        async def _boom(*a, **k):
            raise RuntimeError("no book today")
        monkeypatch.setattr(cc, "ticket_context", _boom)
        async with _gateway(_Engine()) as c:
            d = await (await c.post("/trade/copilot", json=_TICKET,
                                    headers=HDRS)).json()
        # Geometry, reward:risk and stop distance need nothing from a book.
        assert d["rr"] == 3.0 and d["checks"]["reward_risk"] == "ok"
        # And the three that do are named, never passed off as checked.
        assert {u["name"] for u in d["unchecked"]} == {
            "size_vs_equity", "engine_bias", "existing_exposure"}
        assert d["verdict"] == cp.VERDICT_PARTIAL

    async def test_the_callers_sentence_reaches_the_card(self, secret, monkeypatch):
        # Without the gateway handing `unread` through, the card falls back to
        # the co-pilot's own neutral wording -- "the equity for this account was
        # not supplied" -- which tells a person nothing about what to do. The
        # reading knows the difference; the wire has to carry it.
        monkeypatch.setattr(cc, "CONFIG", _cfg(True))
        async with _gateway(_Engine(scope="none")) as c:
            d = await (await c.post("/trade/copilot", json=_TICKET,
                                    headers=HDRS)).json()
        why = {u["name"]: u["reason"] for u in d["unchecked"]}
        assert why["size_vs_equity"] == "no live account on this build is linked to you"
        assert why["size_vs_equity"] != cp._NOT_SUPPLIED["size_vs_equity"]
        assert why["size_vs_equity"] in d["human_readable"]

    async def test_the_span_travels_on_the_wire(self, secret, monkeypatch):
        monkeypatch.setattr(cc, "CONFIG", _cfg(False))
        async with _gateway(_Engine()) as c:
            d = await (await c.post("/trade/copilot", json=_TICKET,
                                    headers=HDRS)).json()
        # The browser PRINTS this sentence rather than deriving one from
        # `score_basis` — a figure re-derived in the page is the second reading
        # the seam exists to replace.
        assert d["score_line"] == cp.score_line(d)
        assert "checks" in d["score_line"]
