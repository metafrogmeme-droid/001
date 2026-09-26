"""The /analyze ladder prints a sub-dollar setup at the precision it was set at.

Driven: a DOGE setup at entry 0.1234, stop 0.1209, target 0.1284 printed
`TP $0.13 (+$0.00)`, `IN $0.12`, `SL $0.12 (-$0.00)` -- two levels that read
the same and two distances that read as nothing. That text is the web
answer, the Telegram caption and the model's transcript. It reads through
the file's own adaptive `_price` now, for the levels and the distances.
"""
from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace

import pytest

from bot.skills import skill_registry as sr
from bot.utils.models import Direction, TradeIdea


def _card(entry, sl, tp, direction=Direction.LONG, last=None, no_idea=False):
    idea = None if no_idea else TradeIdea(
        asset="DOGE/USDT", direction=direction, entry_price=entry,
        stop_loss=sl, take_profit=tp, confidence=0.72,
        reasoning="[rule|TREND_UP|x|swing|C=0.66] test", order_type="market")

    class _Ex:
        async def fetch_ticker(self, sym):
            return {"last": last or entry, "percentage": 1.2, "quoteVolume": 5e6}

    class _Scanner:
        async def _get_exchange(self):
            return _Ex()

        async def _get_futures_exchange(self):
            return _Ex()

    async def _an(sig, **kw):
        return idea

    eng = SimpleNamespace(scanner=_Scanner(), _analyze_signal=_an, _pending_ideas={},
                          _pending_atr={}, analyzer=SimpleNamespace(_last_rejection_diag=None),
                          journal=None, _engine_pending_ids=lambda: set())
    return asyncio.run(sr.AnalyzeAssetSkill().execute(eng, symbol="DOGE/USDT"))


def _ladder(card: str) -> dict:
    pre = card.split("<pre>")[1].split("</pre>")[0]
    out = {}
    for label in ("TP", "IN", "SL"):
        line = next(ln for ln in pre.splitlines() if f" {label} " in ln)
        out[label] = re.findall(r"\$[\d,]+\.\d+", line)
    return out


def test_a_sub_dollar_long_keeps_its_levels_apart():
    lad = _ladder(_card(0.1234, 0.1209, 0.1284))
    assert lad["TP"][0] == "$0.1284" and lad["IN"] == ["$0.1234"] and lad["SL"][0] == "$0.1209"
    assert lad["TP"][1] == "$0.005000" and lad["SL"][1] == "$0.002500", (
        "a distance of half a cent is not $0.00")


def test_a_sub_dollar_short_keeps_its_levels_apart():
    lad = _ladder(_card(0.1234, 0.1259, 0.1184, direction=Direction.SHORT))
    assert (lad["SL"][0], lad["IN"][0], lad["TP"][0]) == ("$0.1259", "$0.1234", "$0.1184")


def test_a_sub_cent_token_is_not_rounded_to_zero():
    lad = _ladder(_card(0.000123, 0.000120, 0.000130))
    assert lad["IN"] == ["$0.000123"] and lad["SL"][0] == "$0.000120"
    assert all(v != "$0.00" for vals in lad.values() for v in vals)


@pytest.mark.parametrize("entry,sl,tp", [(63000.0, 62000.0, 65000.0), (2.3456, 2.3, 2.44)])
def test_a_dollar_priced_setup_reads_as_before(entry, sl, tp):
    lad = _ladder(_card(entry, sl, tp))
    assert lad["IN"] == [f"${entry:,.2f}"]
    # The level at two decimals as before; the DISTANCE through the same
    # adaptive reading, so a sub-dollar distance is not rounded to cents.
    assert lad["TP"] == [f"${tp:,.2f}", sr._price(tp - entry)]


def test_the_no_setup_card_prints_the_price_at_its_precision():
    card = _card(0.1234, 0.1209, 0.1284, no_idea=True)
    assert "- Price: <code>$0.1234</code>" in card
