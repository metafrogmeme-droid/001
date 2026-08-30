"""A model's weights are frozen in the past, so the price has to be injected.

"BTC is around $48k" is a memory, not a quote, and no amount of retraining
fixes tomorrow's price. Scans already read correctly because their prompts are
built from live indicators. Chat was the gap: `_build_chat_system_prompt`
already injected the portfolio, the equity and the engine state, and stopped
short of the one number a user asks about most.

THE EMPTY CASE IS THE POINT OF THE WHOLE THING. A block that simply disappears
when the feed goes quiet leaves the model to answer from its weights, in the
same confident voice, with a number from training. That is not a neutral
degradation — it is the failure being fixed, arriving by a different door. So a
dead feed produces a LOUDER block than a live one, and this file spends more
assertions on that state than on the happy path.

FRESHNESS IS NOT OPTIONAL EITHER. `ws_feed.get_snapshot` applies the same
staleness rule as `get_prices` — including "an unreadable timestamp is stale" —
so a silently-stalled feed yields nothing rather than a confident old price.
The timestamp is printed so the reader can judge the age instead of taking
"live" on trust.

WHAT THIS DELIBERATELY DOES NOT CLAIM. `PriceTick.change_pct_24h` is built with
`_float(..., 0.0)`, so a field the exchange never sent and a market that did
not move are the same value. Where absent and zero can be told apart they must
be; here the structure has already collapsed them, so the change is shown only
when non-zero. That suppresses a genuine 0.00% — a real cost, accepted, because
asserting flatness that may be fabricated is the worse of the two inside a
prompt whose whole job is to stop fabrication. Making the field Optional at the
tick level is the actual fix and it reaches into stop logic.
"""

from __future__ import annotations

import datetime as _dt
import pathlib
import re


from bot.core.ws_feed import PriceTick
from bot.skills.telegram_handler import TelegramHandler

ROOT = pathlib.Path(__file__).resolve().parent.parent
UTC = _dt.timezone.utc


def _tick(sym, last, chg=0.0, age_sec=0.0):
    return PriceTick(
        symbol=sym, last=last, bid=last, ask=last, volume_24h=1.0,
        change_pct_24h=chg,
        timestamp=_dt.datetime.now(UTC) - _dt.timedelta(seconds=age_sec),
    )


class _Feed:
    """A feed honouring the same max_age contract the real one does."""

    def __init__(self, ticks):
        self._t = ticks

    def get_snapshot(self, max_age_sec=None):
        if not max_age_sec:
            return dict(self._t)
        now = _dt.datetime.now(UTC)
        return {s: t for s, t in self._t.items()
                if (now - t.timestamp).total_seconds() <= max_age_sec}


def _handler(feed):
    h = TelegramHandler.__new__(TelegramHandler)
    h.engine = type("E", (), {"ws_feed": feed})()
    return h


def _block(ticks):
    return _handler(_Feed(ticks))._live_ticker_block()


# ── the prices reach the prompt ─────────────────────────────────────────────

def test_live_prices_are_in_the_prompt():
    out = _block({"BTC/USDT": _tick("BTC/USDT", 61432.10, 0.021),
                  "ETH/USDT": _tick("ETH/USDT", 2984.55, -0.008)})
    assert "61,432.10" in out and "2,984.55" in out
    assert "+2.1% 24h" in out and "-0.8% 24h" in out


def test_the_snapshot_is_timestamped():
    """"Live" is a claim about age. Printing the time lets a reader judge it
    rather than trust the word."""
    out = _block({"BTC/USDT": _tick("BTC/USDT", 61432.10)})
    assert re.search(r"as of \d{2}:\d{2}:\d{2} UTC", out), out


def test_the_model_is_told_not_to_go_beyond_the_list():
    out = _block({"BTC/USDT": _tick("BTC/USDT", 61432.10)})
    assert "State ONLY these prices" in out
    assert "not listed" in out and "scan" in out
    assert "Never recall a price from memory" in out


def test_prices_keep_their_significant_digits():
    """`rstrip("0")` on a 4dp format turned $61,432.10 into $61,432.1 and
    $141.20 into $141.2 — a price that reads as though a digit was lost."""
    out = _block({"BTC/USDT": _tick("BTC/USDT", 61432.10),
                  "SOL/USDT": _tick("SOL/USDT", 141.20),
                  "PUMP/USDT": _tick("PUMP/USDT", 0.003012)})
    assert "$61,432.10" in out
    assert "$141.20" in out
    assert "$0.003012" in out, "a sub-dollar token lost its precision"


# ── the empty case, which is the one that matters ───────────────────────────

def test_a_dead_feed_produces_a_louder_block_not_a_missing_one():
    """THE WHOLE POINT. Omitting the section when the feed is quiet hands the
    question back to the model's weights, and it answers from training data in
    exactly the same confident voice."""
    out = _block({})
    assert out.strip(), "the block vanished — the model is free to invent again"
    assert "NONE AVAILABLE" in out
    assert "do not state" in out.lower() and "estimate" in out.lower()
    assert "recall" in out.lower()


def test_a_stale_feed_counts_as_no_feed():
    """A silently-stalled feed must not serve a confident old price."""
    old = TelegramHandler.CHAT_TICKER_MAX_AGE_SEC + 60
    out = _block({"BTC/USDT": _tick("BTC/USDT", 61432.10, age_sec=old)})
    assert "NONE AVAILABLE" in out, "a stale tick was quoted as live"
    assert "61,432" not in out


def test_a_missing_feed_object_says_so_rather_than_raising():
    h = TelegramHandler.__new__(TelegramHandler)
    h.engine = type("E", (), {})()          # no ws_feed at all
    assert "NONE AVAILABLE" in h._live_ticker_block()


def test_a_feed_that_raises_says_so_rather_than_raising():
    class _Broken:
        def get_snapshot(self, max_age_sec=None):
            raise RuntimeError("feed exploded")

    assert "NONE AVAILABLE" in _handler(_Broken())._live_ticker_block()


def test_a_zero_price_is_not_a_price():
    out = _block({"BTC/USDT": _tick("BTC/USDT", 0.0),
                  "ETH/USDT": _tick("ETH/USDT", 2984.55)})
    assert "BTC/USDT" not in out, "a zero price was quoted as a price"
    assert "2,984.55" in out


def test_every_price_unusable_reads_as_no_data():
    out = _block({"BTC/USDT": _tick("BTC/USDT", 0.0)})
    assert "NONE AVAILABLE" in out


# ── the 24h change is not fabricated ────────────────────────────────────────

def test_a_zero_change_is_omitted_rather_than_printed_as_flat():
    """`change_pct_24h` collapses "the exchange sent nothing" and "it did not
    move" into 0.0. Printing 0.00% asserts the second; omitting asserts
    neither, which is all this data can support."""
    out = _block({"SOL/USDT": _tick("SOL/USDT", 141.20, 0.0)})
    assert "$141.20" in out
    assert "0.0% 24h" not in out and "+0.0%" not in out


def test_a_ratio_feed_is_not_multiplied_into_nonsense():
    """Bitget sends 0.021 for +2.1%. A feed already emitting 2.1 must not be
    scaled to 210%."""
    assert "+2.1% 24h" in _block({"BTC/USDT": _tick("BTC/USDT", 61432.0, 0.021)})
    assert "+2.1% 24h" in _block({"BTC/USDT": _tick("BTC/USDT", 61432.0, 2.1)})


# ── wiring and bounds ───────────────────────────────────────────────────────

def test_the_block_is_actually_in_the_prompt():
    """Every test above calls `_live_ticker_block` directly, and none of them
    prove the prompt includes it."""
    from tests.source_scan import code_only

    src = code_only((ROOT / "bot" / "skills" / "telegram_handler.py")
                    .read_text(encoding="utf-8"))
    i = src.index("def _build_chat_system_prompt")
    body = src[i:src.index("async def _llm_chat", i)]
    assert "self._live_ticker_block()" in body, (
        "the chat prompt no longer carries live prices — the model is back to "
        "quoting the ones in its weights")


def test_the_list_is_bounded_and_leads_with_majors():
    ticks = {f"T{i}/USDT": _tick(f"T{i}/USDT", 10.0 + i) for i in range(30)}
    ticks["BTC/USDT"] = _tick("BTC/USDT", 61432.10)
    out = _block(ticks)
    rows = [ln for ln in out.splitlines() if ln.startswith("  ")]
    assert len(rows) <= TelegramHandler.CHAT_TICKER_MAX, (
        "the prompt turned into a price list")
    assert rows[0].strip().startswith("BTC/USDT"), (
        "the majors are not leading, so a full feed can push BTC out entirely")


def test_the_chat_freshness_bound_is_looser_than_stop_logic_but_real():
    """A conversation is not an exit decision, so the window is wider — but
    "live" still has to mean something."""
    assert 0 < TelegramHandler.CHAT_TICKER_MAX_AGE_SEC <= 300


def test_the_feed_accessor_keeps_the_same_staleness_rule_as_get_prices():
    """Divergence here would mean the price chat shows and the price the stop
    logic uses could come from different eras."""
    from tests.source_scan import code_only

    src = code_only((ROOT / "bot" / "core" / "ws_feed.py").read_text(encoding="utf-8"))
    snap = src[src.index("def get_snapshot"):src.index("def get_prices")]
    assert "max_age_sec" in snap and "continue" in snap, (
        "get_snapshot no longer filters on tick age")
    assert snap.count("tick.timestamp.timestamp()") == 1, (
        "get_snapshot stopped comparing the tick's own timestamp")
