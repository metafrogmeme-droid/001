"""Every OUTCOME record said `exit_price: null`, and the price was right there.

`outcome_event_payload` read `_get(pos, "exit_price")`. `LivePosition` names
that field **close_price** — it has no `exit_price` attribute at all — so
getattr returned the default and the recorder wrote a null. 119 records by
2026-08-07, every one of them, into a log that is hash-chained from genesis
and therefore cannot be amended after the fact.

The value was measured, present on the object, and one wrong key away. That
makes this worse than an absent measurement: the chain asserts "no exit price
recorded" for closes whose exit price the executor knew exactly. Nothing
downstream can tell that apart from a close that genuinely could not be
priced, which is the distinction §2's fills-at-trigger-price guarantee rests
on — and the only independent record of it is the one saying null.

Not recoverable for anything already written. Fixed forward.
"""
from __future__ import annotations

from types import SimpleNamespace as NS


from bot.guardian.flight_recorder import outcome_event_payload


def _live_position(**over):
    """The real shape: LivePosition carries close_price, never exit_price."""
    base = dict(trade_id="TI-1", symbol="XLM/USDT", direction="SHORT",
                pnl_usd=0.33, close_price=0.2814, entry_price=0.2851,
                close_reason="tp")
    base.update(over)
    return NS(**base)


class TestThePriceReachesTheChain:
    def test_close_price_is_recorded_as_the_exit_price(self):
        payload = outcome_event_payload(_live_position())
        assert payload["exit_price"] == 0.2814, (
            "the executor knew the fill price and the chain stored null"
        )

    def test_the_rest_of_the_payload_still_lands(self):
        p = outcome_event_payload(_live_position())
        assert p["decision_id"] == "TI-1"
        assert p["symbol"] == "XLM/USDT"
        assert p["direction"] == "SHORT"
        assert p["pnl_usd"] == 0.33
        assert p["entry_price"] == 0.2851
        assert p["close_reason"] == "tp"

    def test_a_dict_caller_using_exit_price_still_works(self):
        # Some callers pass a mapping keyed exit_price; that path predates the
        # live one and must not regress.
        p = outcome_event_payload({"trade_id": "TI-2", "symbol": "BTC/USDT",
                                   "direction": "LONG", "pnl_usd": 1.0,
                                   "exit_price": 61234.5, "entry_price": 61000.0,
                                   "close_reason": "sl"})
        assert p["exit_price"] == 61234.5

    def test_exit_price_wins_when_both_are_present(self):
        # An explicit exit_price is the more specific claim.
        p = outcome_event_payload(_live_position(exit_price=1.5, close_price=2.5))
        assert p["exit_price"] == 1.5

    def test_an_unpriced_close_is_still_null_not_zero(self):
        # The honest case must survive the fix: no price is None, never 0.0,
        # because 0.0 is a price and "we could not read one" is not.
        p = outcome_event_payload(_live_position(close_price=None))
        assert p["exit_price"] is None

    def test_a_recorded_zero_is_kept(self):
        # 0.0 is falsy and real. `is None`, not falsiness.
        p = outcome_event_payload(_live_position(close_price=0.0))
        assert p["exit_price"] == 0.0

    def test_it_never_raises_on_a_broken_position(self):
        # The recorder is fail-open by design — it must never break a close.
        for bad in (None, NS(), {}, NS(close_price="not-a-number")):
            outcome_event_payload(bad)      # type: ignore[arg-type]


class TestTheFieldNameIsNotGuessedAgain:
    def test_live_position_really_has_close_price_and_not_exit_price(self):
        """Pins the assumption this fix rests on. If LivePosition ever renames
        the field, this fails here rather than silently writing nulls into an
        append-only chain for another 119 closes."""
        from bot.core.live_executor import LivePosition
        fields = getattr(LivePosition, "__dataclass_fields__", {})
        assert "close_price" in fields
        assert "exit_price" not in fields, (
            "if LivePosition gained exit_price, revisit outcome_event_payload"
        )
