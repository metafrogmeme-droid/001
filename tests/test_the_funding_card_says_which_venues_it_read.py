"""`/funding` answered "check the symbol" for a venue that was down.

    rates: dict[str, float] = {}
    try:  ... rates["bitget"] = float(home)
    except Exception: pass
    try:  rates.update(await CROSS_VENUE.rates_for(base))
    except Exception: pass
    if not rates:
        "No funding data found for BTC on any connected venue - check the symbol."

Two swallowed reads, one sentence, three different facts behind it: a venue
that timed out, a venue that errored, and a base that genuinely has no perp.
And it NAMES A CAUSE -- pointing the reader at the one thing that may be
perfectly correct. That is this repository's opening example, "a 503 shown as
'No venues found'", with a remedy attached.

Beneath it the card headed itself "funding across venues" over whichever
venues had answered, and `divergence()` then computed a spread over that same
partial set and printed "Spread across N venues". A venue-CONCENTRATION
warning derived from venues nobody read is `Scanned 40/115 · Errors 0`, one
card over.

THE AGGREGATOR COULD NOT SAY WHY A VENUE WAS MISSING and its own docstring
admitted it: "missing venues simply absent". `_venue_map` returned one dict
for a fetch that failed, a fetch that returned nothing, and a map kept from an
earlier fetch -- so `states_for` is the reading now, `rates_for` is that
reading with the reasons dropped (ONE walk, for the two callers that only want
numbers), and `_venue_map` was DELETED rather than kept as a wrapper: after
the change it had no caller, and a function nobody calls is a claim that
somebody needs it.
"""
from __future__ import annotations

import asyncio

import pytest

from bot.core.cross_venue import CrossVenueFunding, VenueFunding, funding_reading
from bot.formatters.market_cards import render_funding

READ = [VenueFunding("bitget", 0.0001, "read"),
        VenueFunding("bybit", 0.0009, "read"),
        VenueFunding("hyperliquid", -0.0006, "read")]


def card(rows) -> str:
    return render_funding("BTC", funding_reading(rows))


class TestTheConfidentNegativeIsGone:
    def test_no_venue_read_never_blames_the_symbol(self):
        out = card([VenueFunding(v, None, "unread")
                    for v in ("bitget", "bybit", "hyperliquid")])
        assert "check the symbol" not in out, (
            "not one venue answered, so the card has no evidence whatever "
            "about whether this base has a perp:\n" + out)
        # The sentence is "None of the 3 venues could be read", so the
        # per-ROW phrasing ("could not be read") is not in this branch at all.
        # The first draft asserted that spelling and failed on correct code:
        # check whether the assertion or the code is wrong, first.
        assert "None of the 3 venues could be read" in out
        assert "nothing was measured" in out.lower()

    def test_every_venue_answering_and_none_listing_it_IS_the_symbol(self):
        # The one case where the old sentence was right. It survives, alone.
        out = card([VenueFunding(v, None, "not_listed")
                    for v in ("bitget", "bybit", "hyperliquid")])
        assert "check the symbol" in out
        assert "All 3 venues answered" in out

    def test_the_two_are_different_sentences(self):
        down = card([VenueFunding("bybit", None, "unread")])
        gone = card([VenueFunding("bybit", None, "not_listed")])
        assert down != gone

    def test_nobody_asked_is_its_own_answer(self):
        out = card([])
        assert "No venue was asked" in out
        assert "check the symbol" not in out


class TestThePartialIsLabelled:
    def test_the_header_counts_priced_against_asked(self):
        out = card([READ[0], READ[1], VenueFunding("hyperliquid", None, "unread")])
        # THE HEADER LINE, not anywhere on the card. The first draft asserted
        # `"2 of 3 venues" in out` and the mutation that makes the header read
        # `{asked} of {asked}` SURVIVED it -- the SPREAD row says "2 of 3
        # venues" too, so the assertion passed for a reason unrelated to the
        # rule it names, which is how a round reports coverage it lacks.
        head = out.splitlines()[0]
        assert "2 of 3 venues" in head, head

    def test_an_unread_venue_is_NAMED_not_omitted(self):
        # A card headed "2 of 3" that does not say WHICH is missing leaves the
        # reader to guess between a venue that is down and a coin that is not
        # listed there, and those have different remedies.
        out = card([READ[0], READ[1], VenueFunding("hyperliquid", None, "unread")])
        assert "hyperliquid" in out and "could not be read" in out

    def test_an_unlisted_venue_says_so_rather_than_could_not_be_read(self):
        out = card([READ[0], VenueFunding("bybit", None, "not_listed")])
        assert "no perp listed here" in out
        assert "could not be read" not in out

    def test_the_spread_states_its_own_denominator(self):
        out = card([READ[0], READ[1], VenueFunding("hyperliquid", None, "unread")])
        assert "Spread across 2 of 3 venues" in out, (
            "a spread over the venues that happened to answer, printed as a "
            "spread across venues, is a partial total printed as whole:\n" + out)

    def test_one_priced_venue_is_not_a_spread(self):
        out = card([READ[0], VenueFunding("bybit", None, "unread")])
        assert "Spread" not in out
        assert "no spread to compare" in out

    def test_a_stale_rate_is_marked_as_a_memory(self):
        out = card([VenueFunding("bitget", 0.0002, "stale"),
                    VenueFunding("bybit", None, "unread")])
        assert "(last known)" in out, out

    def test_a_read_rate_carries_no_memory_tag(self):
        assert "(last known)" not in card(READ)


class TestTheReadingIsTheOnlyArithmetic:
    def test_the_spread_delegates_rather_than_recomputing(self, monkeypatch):
        # A second max-minus-min here would be a second answer about what a
        # spread is. Patch the ONE definition and see the reading follow.
        monkeypatch.setattr(CrossVenueFunding, "divergence",
                            staticmethod(lambda r, home_rate=None:
                                         {"spread": 0.4242, "mean": 0.0,
                                          "venues": 99}))
        assert funding_reading(READ).spread["venues"] == 99

    def test_the_spread_is_over_priced_rows_only(self):
        rows = [*READ, VenueFunding("kraken", None, "unread")]
        assert funding_reading(rows).spread["venues"] == 3

    @pytest.mark.parametrize("rows,verdict", [
        (READ, "priced"),
        ([VenueFunding("a", None, "unread")], "nothing_read"),
        ([VenueFunding("a", None, "not_listed")], "not_listed"),
        ([], "no_venues"),
        # A read venue beside an unread one is PRICED: one honest number is a
        # reading, and the card labels what is missing beside it.
        ([READ[0], VenueFunding("a", None, "unread")], "priced"),
        # ...and an unread venue beside an unlisted one is NOTHING READ, never
        # "not listed": one venue could not be asked, so the set is not closed.
        ([VenueFunding("a", None, "unread"),
          VenueFunding("b", None, "not_listed")], "nothing_read"),
    ])
    def test_the_verdict(self, rows, verdict):
        assert funding_reading(rows).verdict == verdict


class TestTheAggregatorKeepsTheReasons:
    """`states_for` over a planted cache -- no network, no ccxt."""

    @staticmethod
    def _cache(maps, outcomes):
        c = CrossVenueFunding(ttl_seconds=600.0)
        c._maps.update(maps)
        c._outcomes.update(outcomes)
        # Fresh for every venue named, so nothing re-fetches.
        import time
        for v in set(maps) | set(outcomes):
            c._fetched_at[v] = time.monotonic()
        return c

    def test_a_listed_base_on_a_fresh_map_is_read(self):
        c = self._cache({"bybit": {"BTC": 0.001}}, {"bybit": "ok"})
        rows = {r.venue: r for r in asyncio.run(c.states_for("BTC"))}
        assert rows["bybit"].state == "read" and rows["bybit"].rate == 0.001

    def test_an_absent_base_on_a_FRESH_map_is_not_listed(self):
        c = self._cache({"bybit": {"ETH": 0.001}}, {"bybit": "ok"})
        rows = {r.venue: r for r in asyncio.run(c.states_for("BTC"))}
        assert rows["bybit"].state == "not_listed"

    def test_a_FRESH_but_EMPTY_map_is_unread_not_not_listed(self):
        # A fetch that SUCCEEDED and parsed no rows at all is `ok` by
        # `_outcomes`, so without the empty-map half every base on that venue
        # came back `not_listed` -- a confident negative about the venue's
        # LISTINGS, assembled from a parse that found nothing. Found by
        # re-reading the diff; the mutation that drops it changes no verdict
        # without this fixture, because every other planted map has rows.
        c = self._cache({"bybit": {}}, {"bybit": "ok"})
        rows = {r.venue: r for r in asyncio.run(c.states_for("BTC"))}
        assert rows["bybit"].state == "unread"

    def test_an_absent_base_on_a_STALE_map_is_unread(self):
        # What we hold is a memory of an earlier listing; a coin listed since
        # would be absent from it for a reason that is not the venue's answer.
        c = self._cache({"bybit": {"ETH": 0.001}}, {"bybit": "failed"})
        rows = {r.venue: r for r in asyncio.run(c.states_for("BTC"))}
        assert rows["bybit"].state == "unread"

    def test_a_listed_base_on_a_STALE_map_is_stale_not_read(self):
        c = self._cache({"bybit": {"BTC": 0.001}}, {"bybit": "failed"})
        rows = {r.venue: r for r in asyncio.run(c.states_for("BTC"))}
        assert rows["bybit"].state == "stale" and rows["bybit"].rate == 0.001

    def test_a_venue_with_no_map_and_a_failed_fetch_is_unread(self):
        # Driven through the REAL `_venue_map_state` failure path rather than
        # a planted cache: an empty `_fetched_at` makes `_is_fresh` False, so
        # the first draft of this test reached ccxt for real and asserted
        # against whatever the network did. The two facts it distinguishes --
        # never attempted, and attempted with nothing to show -- are
        # deliberately ONE state, because we hold nothing either way.
        c = CrossVenueFunding(ttl_seconds=600.0)

        async def no_exchange(_v):
            raise RuntimeError("venue unreachable")
        c._exchange = no_exchange                       # type: ignore[assignment]
        rows = asyncio.run(c.states_for("BTC"))
        assert rows and all(r.state == "unread" for r in rows)
        assert all(c._outcomes[r.venue] == "failed" for r in rows)

    def test_the_backoff_window_does_not_read_as_success(self):
        # A failed fetch backs `_fetched_at` off by only 3/4 of the TTL, so
        # `_is_fresh` is True over a map nobody could refresh. Reading
        # freshness as success there reports a memory as a measurement.
        import time
        c = CrossVenueFunding(ttl_seconds=600.0)
        c._maps["bybit"] = {"BTC": 0.001}
        c._outcomes["bybit"] = "failed"
        c._fetched_at["bybit"] = time.monotonic() - 600.0 * 0.75
        assert c._is_fresh("bybit"), "precondition: still inside the backoff"
        assert c._state_of("bybit") == "stale"

    def test_rates_for_is_states_for_with_the_reasons_dropped(self):
        # ONE walk. The two existing callers keep the dict they had.
        c = self._cache({"bybit": {"BTC": 0.001}, "hyperliquid": {}},
                        {"bybit": "ok", "hyperliquid": "ok"})
        assert asyncio.run(c.rates_for("BTC")) == {"bybit": 0.001}

    def test_a_gather_that_raises_answers_unread_for_every_venue(self):
        c = CrossVenueFunding(ttl_seconds=600.0)

        async def boom(_v):
            raise RuntimeError("venue")
        c._venue_map_state = boom                       # type: ignore[assignment]
        rows = asyncio.run(c.states_for("BTC"))
        assert rows and all(r.state == "unread" for r in rows)


def test_the_dead_map_reader_is_gone():
    # Once `rates_for` went through `states_for`, `_venue_map` had no caller.
    # Keeping it as a one-line wrapper would have been a function nobody
    # needs, which `test_no_new_unreachable_functions` reports next run.
    assert not hasattr(CrossVenueFunding, "_venue_map")


class TestTheHandlerIsWired:
    """The card is correct and the HANDLER must reach it.

    A scan of either file cannot see whether the reading travels, so this
    drives the real `_cmd_funding` -- through its `@guard`, with a `_guard`
    that admits -- against a planted engine, and reads what was sent.
    """

    @staticmethod
    def _run(home_rate, home_raises=False, cross=()):
        import types

        from bot.core import cross_venue as cv
        from bot.skills.market_commands import MarketCommands

        sent: list[str] = []

        class _Ex:
            async def fetch_funding_rate(self, sym):
                if home_raises:
                    raise RuntimeError("bitget refused: key=SECRETVALUE")
                return {"fundingRate": home_rate}

        class _Scanner:
            async def _get_futures_exchange(self):
                return _Ex()

        async def _send(u, text, **kw):
            sent.append(text)

        async def _guard(u, perm="", ctx=None):
            return True

        host = types.SimpleNamespace(
            engine=types.SimpleNamespace(scanner=_Scanner()),
            _send=_send, _guard=_guard)

        async def _states(_self, _sym):
            return list(cross)

        orig = cv.CrossVenueFunding.states_for
        cv.CrossVenueFunding.states_for = _states        # type: ignore[assignment]
        try:
            asyncio.run(MarketCommands._cmd_funding(
                host, object(), types.SimpleNamespace(args=["BTC"])))
        finally:
            cv.CrossVenueFunding.states_for = orig       # type: ignore[assignment]
        assert len(sent) == 1, f"expected one card, got {len(sent)}"
        return sent[0]

    def test_a_read_home_rate_reaches_the_card(self):
        out = self._run(0.0001, cross=[VenueFunding("bybit", 0.0009, "read")])
        assert "bitget" in out and "2 of 2 venues" in out

    def test_a_home_venue_that_RAISED_is_unread_not_unlisted(self):
        # The cross venues ANSWER and do not list it, so the home venue's
        # exception is the ONLY thing standing between this card and "check
        # the symbol". With a second unread row in the fixture the mutation
        # that reads the home exception as `not_listed` survived: the verdict
        # was `nothing_read` either way, and the fixture could not tell.
        out = self._run(None, home_raises=True,
                        cross=[VenueFunding("bybit", None, "not_listed")])
        assert "check the symbol" not in out, (
            "the home venue threw; nothing was read anywhere, so the symbol "
            "is not the thing to check:\n" + out)

    def test_a_home_venue_that_ANSWERED_with_no_rate_is_not_listed(self):
        out = self._run(None, cross=[VenueFunding("bybit", None, "not_listed")])
        assert "check the symbol" in out, out

    def test_the_venue_rejection_text_never_reaches_the_card(self):
        out = self._run(None, home_raises=True,
                        cross=[VenueFunding("bybit", None, "unread")])
        assert "SECRETVALUE" not in out and "key=" not in out, (
            "a venue rejection can echo request params into a chat bubble")


def test_the_command_is_guarded():
    # The gate half of the same slice, driven where the card half is: the
    # command spent a live venue fetch per invocation for a caller the bot
    # had never admitted.
    from tests.command_gates import command_gates
    assert command_gates()["funding"] == "guard"
