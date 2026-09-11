"""An adopted position's record was built out of `or` chains ending in literals.

`adopt_exchange_positions` turns an exchange position the bot did not open into
a durable `LivePosition`. Every field it reads came from a chain like::

    entry_price = float(info.get("openPriceAvg") or p.get("entryPrice")
                        or info.get("averageOpenPrice") or 0)
    margin      = float(info.get("margin") or info.get("im")
                        or p.get("initialMargin") or p.get("collateral") or 0)
    leverage    = int(float(info.get("leverage") or p.get("leverage") or 1))
    opened_at   = datetime.fromtimestamp(ts / 1000) if ts else datetime.now(UTC)

Every rung is a genuine fallback and the LAST one is not: it is what the field
becomes when no source stated it. Driven against a row carrying only a symbol,
a side and a size, the record came out

    entry_price = 0.0        a price
    cost_usd    = 0.0        a margin
    leverage    = 1          printed as fact and multiplied into the ROE
    opened_at   = now()      renders as "just opened"

THE RENDERER ALREADY REFUSES TWO OF THESE AND COULD NOT FIRE.
`bot/formatters/orphan_position.py` names them in its own docstring — "the age
`0.0` renders as '0m'", "absent leverage printed `1x`" — and was hardened
against both. A guard against inventing a value is worthless once the WRITER
has filled the hole: a stored 1 is indistinguishable from a spot position, and
a stamped now() is indistinguishable from a fresh fill.

AND THE SILENCE WAS THE EXPENSIVE PART. The safety-default block is gated
`and entry_price > 0`, correctly — a 3% stop off an entry of 0 is a stop at 0.
But that block also contains `_place_sl_tp`, its retry, and the UNPROTECTED
alert, so an unreadable entry took none of them. The position was adopted with
no stop, no attempt to place one, and nothing said so: the one case where
nothing could be read was the one case that reported nothing.

THE RED HERRING, planted below: a venue that reports a genuine ZERO must keep
it. `or` skips a measured 0.0 because it is falsy — `read_amount` does not —
and a fix that turned every zero into "unknown" would be the same defect
pointing the other way.
"""

from __future__ import annotations

import pytest

from bot.core.order_state import first_reading

# ── the reader ────────────────────────────────────────────────────────────

class TestFirstReadingIsThreeValued:
    def test_it_takes_the_first_source_that_states_a_value(self):
        a, b = {"x": None}, {"y": "5.5"}
        assert first_reading((a, "x"), (b, "y")) == 5.5

    def test_nothing_stated_is_none_not_a_literal(self):
        a = {"openPriceAvg": "", "entryPrice": None}
        assert first_reading((a, "openPriceAvg"), (a, "entryPrice")) is None

    def test_a_measured_zero_is_kept(self):
        """THE RED HERRING. `or` skips 0.0 because it is falsy and falls to the
        next source; a venue that truthfully reports zero must be believed."""
        a, b = {"margin": 0.0}, {"im": "99"}
        assert first_reading((a, "margin"), (b, "im")) == 0.0

    @pytest.mark.parametrize("junk", ["n/a", "", None, object(), float("nan")])
    def test_junk_falls_through(self, junk):
        a, b = {"k": junk}, {"k": "7"}
        assert first_reading((a, "k"), (b, "k")) == 7.0

    def test_no_sources_at_all(self):
        assert first_reading() is None

    def test_a_non_dict_payload_does_not_raise(self):
        assert first_reading(("not a dict", "k"), ({"k": "3"}, "k")) == 3.0


# ── the record, driven through the real adoption path ─────────────────────

def _executor(monkeypatch, rows, tmp_path):
    from bot.config import CONFIG
    from bot.core.live_executor import LiveExecutor
    monkeypatch.setattr(type(CONFIG), "is_live", lambda self: True)

    class _Fx:
        async def fetch_positions(self, *a, **k):
            return rows

        async def fetch_open_orders(self, *a, **k):
            return []

        async def create_order(self, *a, **k):
            raise RuntimeError("venue refuses the safety stop")

        async def cancel_order(self, *a, **k):
            return {}

    ex = LiveExecutor(state_dir=str(tmp_path))
    ex._exchange = _Fx()

    async def _get():
        return ex._exchange

    monkeypatch.setattr(ex, "_get_exchange", _get)

    async def _noop(*a, **k):
        return (None, None)

    monkeypatch.setattr(ex, "_place_sl_tp", _noop)
    return ex


UNDER_REPORTED = [{
    "symbol": "NFLX/USDT:USDT", "side": "short", "contracts": 3.0,
    "info": {"symbol": "NFLXUSDT", "holdSide": "short", "totalQty": "3"},
}]


@pytest.mark.asyncio
async def test_an_unstated_leverage_is_not_recorded_as_1x(monkeypatch, tmp_path):
    """1 is a real leverage — a spot position has it — so storing it for a
    field the venue never sent is a claim, and it is the exact claim
    `orphan_position.py` was hardened to stop printing."""
    ex = _executor(monkeypatch, UNDER_REPORTED, tmp_path)
    await ex.adopt_exchange_positions()
    pos = next(iter(ex._positions.values()))
    assert pos.leverage != 1, "an absent leverage must not become 1x"
    assert "leverage" in getattr(pos, "adoption_unread", ())


@pytest.mark.asyncio
async def test_the_unread_fields_are_named_on_the_record(monkeypatch, tmp_path):
    ex = _executor(monkeypatch, UNDER_REPORTED, tmp_path)
    await ex.adopt_exchange_positions()
    pos = next(iter(ex._positions.values()))
    unread = set(getattr(pos, "adoption_unread", ()))
    assert {"entry_price", "margin", "leverage", "opened_at"} <= unread


@pytest.mark.asyncio
async def test_an_unreadable_entry_no_longer_adopts_in_silence(monkeypatch, tmp_path):
    """THE FINDING. The `and entry_price > 0` guard is right; what was wrong is
    that the UNPROTECTED alert lived inside the block it gates, so the one case
    where nothing could be read was the one case that reported nothing."""
    ex = _executor(monkeypatch, UNDER_REPORTED, tmp_path)
    await ex.adopt_exchange_positions()
    pos = next(iter(ex._positions.values()))
    assert getattr(pos, "unprotected", False) is True, (
        "a position adopted with no stop and no way to size one must say so")


@pytest.mark.asyncio
async def test_a_fully_reported_position_is_unchanged(monkeypatch, tmp_path):
    """The property the fix must not break: when the venue states everything,
    nothing is marked unknown and nothing is alerted."""
    rows = [{
        "symbol": "BTC/USDT:USDT", "side": "long", "contracts": 0.5,
        "timestamp": 1_700_000_000_000,
        "info": {"symbol": "BTCUSDT", "holdSide": "long", "totalQty": "0.5",
                 "openPriceAvg": "60000", "margin": "1500", "leverage": "20"},
    }]
    ex = _executor(monkeypatch, rows, tmp_path)
    await ex.adopt_exchange_positions()
    pos = next(iter(ex._positions.values()))
    assert pos.entry_price == 60000.0
    assert pos.cost_usd == 1500.0
    assert pos.leverage == 20
    assert not getattr(pos, "adoption_unread", ())
    assert pos.opened_at.year == 2023


@pytest.mark.asyncio
async def test_a_venue_stated_zero_margin_is_believed(monkeypatch, tmp_path):
    """THE RED HERRING AT THE RECORD LEVEL. `or` would skip a real 0.0 and fall
    through; it must be recorded, and must NOT be listed as unread."""
    rows = [{
        "symbol": "BTC/USDT:USDT", "side": "long", "contracts": 0.5,
        "timestamp": 1_700_000_000_000,
        "info": {"symbol": "BTCUSDT", "holdSide": "long", "totalQty": "0.5",
                 "openPriceAvg": "60000", "margin": 0.0, "leverage": "20"},
    }]
    ex = _executor(monkeypatch, rows, tmp_path)
    await ex.adopt_exchange_positions()
    pos = next(iter(ex._positions.values()))
    assert pos.cost_usd == 0.0
    assert "margin" not in getattr(pos, "adoption_unread", ())


@pytest.mark.asyncio
async def test_a_junk_price_does_not_cost_the_rows_behind_it(monkeypatch, tmp_path):
    """WHAT `first_reading` BUYS BEYOND THE STORED VALUE, and it took a
    surviving mutation to find it.

    Restoring `float(info.get("openPriceAvg") or p.get("entryPrice") or 0)`
    changes no recorded number: when no source states a price, `_entry` is None
    and both spellings store 0.0. So the first draft of this file did not kill
    it, and that is the right verdict for the value.

    The difference is what a NON-NUMERIC source does. `"n/a"` is truthy, so the
    `or` chain selects it and `float("n/a")` RAISES — out of the per-position
    body and into the sweep's handler, taking every position behind it. A
    venue's own under-reporting is exactly where a differently-shaped field
    turns up, so this is the case with two positions in it.
    """
    rows = [
        {"symbol": "NFLX/USDT:USDT", "side": "short", "contracts": 3.0,
         "info": {"symbol": "NFLXUSDT", "holdSide": "short", "totalQty": "3",
                  "openPriceAvg": "n/a"}},
        {"symbol": "BTC/USDT:USDT", "side": "long", "contracts": 0.5,
         "timestamp": 1_700_000_000_000,
         "info": {"symbol": "BTCUSDT", "holdSide": "long", "totalQty": "0.5",
                  "openPriceAvg": "60000", "margin": "1500", "leverage": "20"}},
    ]
    ex = _executor(monkeypatch, rows, tmp_path)
    await ex.adopt_exchange_positions()
    symbols = {p.symbol for p in ex._positions.values()}
    assert "BTC/USDT:USDT" in symbols, (
        "a junk field on one row must not abort the sweep for the rows behind it")
    assert "NFLX/USDT:USDT" in symbols
    btc = next(p for p in ex._positions.values() if p.symbol.startswith("BTC"))
    assert btc.entry_price == 60000.0
