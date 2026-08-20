"""An unreadable 24h move became a published SHORT trade plan.

THE CHAIN. `skill_registry.py` built every market signal with

    change_pct_24h=float(ticker.get("percentage", 0) or 0)

which is CLAUDE.md's banned shape twice over — `.get(k, 0)` and `or 0` — so a
venue that reported no percentage produced a measured 0.0. The scan payload
then read that:

    "dir": "LONG" if sig.change_pct_24h > 0 else "SHORT"

and `_build_scan_payload` turned the label into a `book_side`, a market-bias
headline, and a full entry / stop / target card published to the dashboard.
Not a mislabelled row: a trade plan with the stop on a side nobody computed.

`bot/core/analyzer.py` was cured of exactly this in an earlier pass and could
not reach here — the scan path builds its own signals.

WHY IT WAS DEFERRED, AND WHAT UNBLOCKED IT. The producer carried a note:
"KNOWN DEFECT, DELIBERATELY NOT FIXED HERE ... `dir` is a two-valued contract:
_build_scan_payload compares it against "LONG" in six places, including
`book_side = "BID" if dir == "LONG" else "ASK"`, so a third value would
silently become the short side there — the same defect one level down.
Widening it needs those consumers audited first."

That audit found six sites that resolved "not LONG" as SHORT and four that
were already safe and are deliberately untouched:

    SAFE  regime         explicit == "LONG" / == "SHORT", else NEUTRAL
    SAFE  swing filter   == "LONG" excludes unknowns, which is correct
    SAFE  _scan_symbol   decides from readable RSI/SMA — a real verdict,
                         not an absence wearing one
    SAFE  movers filter  abs(x) > 3.0 excludes a manufactured zero anyway

The rule the fixes follow: a side is a claim. Where a claim is optional the
value is omitted; where a claim is structural — an entry, a stop, a target are
all placed RELATIVE to a side — the whole row is dropped and the drop is
disclosed.
"""

from __future__ import annotations

import pathlib
from types import SimpleNamespace

import pytest

from bot.skills.scan_coverage import no_direction_note
from bot.skills.scan_skill import _build_scan_payload
from bot.skills.skill_registry import DIR_UNKNOWN, _chg, _dir_from_change, _maybe_pct, _spark
from bot.utils.models import MarketSignal

ROOT = pathlib.Path(__file__).resolve().parent.parent


def row(sym="BTC/USDT", direction="LONG", **over):
    r = {"sym": sym, "price": 100.0, "dir": direction, "score": 0.8, "rsi": 55.0,
         "atr": 2.0, "vol_ratio": 1.5, "patterns": []}
    r.update(over)
    return r


def _engine():
    return SimpleNamespace(
        risk=SimpleNamespace(trading_blocked_by=None, circuit_breaker_active=False),
        health=SimpleNamespace(snapshot=lambda: SimpleNamespace(status="HEALTHY")),
    )


# ── the producers stop manufacturing a zero ─────────────────────────────────

class TestAbsenceSurvivesTheProducer:
    @pytest.mark.parametrize("raw", [None, "", "null", float("nan"), float("inf"), {}])
    def test_an_unreportable_percentage_is_none_not_zero(self, raw):
        assert _maybe_pct(raw) is None, f"{raw!r} became a number"

    @pytest.mark.parametrize("raw,want", [(0, 0.0), (0.0, 0.0), ("0", 0.0),
                                          (-3.5, -3.5), ("2.25", 2.25)])
    def test_a_reported_number_survives_including_a_real_zero(self, raw, want):
        # `or 0` swallowed a genuine reported 0.0 as well, so even the readable
        # flat case arrived indistinguishable from the unreadable one.
        assert _maybe_pct(raw) == want

    def test_the_model_accepts_a_missing_move(self):
        sig = MarketSignal(symbol="X/USDT", price=1.0, volume_usd_24h=1.0)
        assert sig.change_pct_24h is None


# ── the direction contract ──────────────────────────────────────────────────

class TestDirection:
    def test_an_unreadable_move_is_not_a_short(self):
        assert _dir_from_change(None) == DIR_UNKNOWN

    def test_a_genuinely_flat_move_is_not_a_short_either(self):
        # The second defect in the same expression: `0.0 > 0` is False, so a
        # symbol that really did not move was labelled SHORT.
        assert _dir_from_change(0.0) == DIR_UNKNOWN

    @pytest.mark.parametrize("chg,want", [(2.5, "LONG"), (0.01, "LONG"),
                                          (-2.5, "SHORT"), (-0.01, "SHORT")])
    def test_a_real_move_still_decides(self, chg, want):
        assert _dir_from_change(chg) == want


# ── the six consumers ───────────────────────────────────────────────────────

class TestTheScanPayloadDoesNotPickASide:
    def test_book_side_is_absent_rather_than_ask(self):
        p = _build_scan_payload([row(direction=DIR_UNKNOWN)], _engine())
        assert p["symbols"]["BTCUSDT"]["book_side"] is None, (
            'an undetermined direction was placed on the ask')

    def test_a_real_short_still_gets_the_ask(self):
        p = _build_scan_payload([row(direction="SHORT")], _engine())
        assert p["symbols"]["BTCUSDT"]["book_side"] == "ASK"
        p = _build_scan_payload([row(direction="LONG")], _engine())
        assert p["symbols"]["BTCUSDT"]["book_side"] == "BID"

    def test_no_entry_card_is_published_without_a_direction(self):
        """THE ONE THAT MATTERS. Entry, stop and both targets are placed
        relative to a side; the `else` arm put the stop ABOVE the price and the
        targets below it. There is no honest card to build, so none is."""
        p = _build_scan_payload([row(direction=DIR_UNKNOWN)], _engine())
        syms = [c["symbol"] for c in p["entry_cards"]]
        assert "BTC" not in syms, f"a levelled card was published: {p['entry_cards']}"

    def test_a_directional_row_still_gets_its_card(self):
        p = _build_scan_payload([row(direction="LONG")], _engine())
        assert [c for c in p["entry_cards"] if c["symbol"] == "BTC"], (
            "the guard removed a card it should have kept")

    def test_the_market_bias_headline_does_not_count_unknowns_as_shorts(self):
        rows = [row("BTC/USDT", "LONG"), row("ETH/USDT", DIR_UNKNOWN),
                row("SOL/USDT", DIR_UNKNOWN)]
        p = _build_scan_payload(rows, _engine())
        # 1 long, 0 shorts, 2 undetermined. `len - longs` made that 1 vs 2 and
        # published a SHORT market bias off two symbols nobody could read.
        assert "SHORT" not in p["key_call"], (
            f"an unread market was called short: {p['key_call']}")

    def test_a_genuinely_short_market_is_still_called_short(self):
        rows = [row("BTC/USDT", "SHORT"), row("ETH/USDT", "SHORT"),
                row("SOL/USDT", "LONG")]
        assert "SHORT" in _build_scan_payload(rows, _engine())["key_call"]

    def test_the_payload_never_raises_on_a_mixed_board(self):
        rows = [row("BTC/USDT", "LONG"), row("ETH/USDT", "SHORT"),
                row("SOL/USDT", DIR_UNKNOWN)]
        p = _build_scan_payload(rows, _engine())
        assert len(p["symbols"]) == 3, "a row vanished from the status table"


# ── dropped is not the same as hidden ───────────────────────────────────────

class TestTheDropIsDisclosed:
    def test_it_says_how_many_had_no_direction(self):
        note = no_direction_note([row(), row()])
        assert "2 symbols" in note
        assert "missing data, not a missing setup" in note

    def test_it_is_silent_when_every_row_had_one(self):
        # A banner on every healthy scan trains the reader to skip the one
        # that matters — the reason coverage_note is empty on a whole pass.
        assert no_direction_note([]) == ""

    def test_it_reads_correctly_for_exactly_one(self):
        assert "1 symbol had" in no_direction_note([row()])

    def test_the_scan_card_carries_it(self):
        from tests.source_scan import code_only
        src = code_only((ROOT / "bot" / "skills" / "scan_skill.py")
                        .read_text(encoding="utf-8"))
        assert src.count("no_direction_note(_no_direction)") >= 2, (
            "rows are dropped from the setup list and nothing says so")


# ── the display helpers ─────────────────────────────────────────────────────

class TestTheTelegramSurfacesDoNotInventANumber:
    def test_an_unread_move_prints_a_dash_not_plus_zero(self):
        assert _chg(None) == "—"

    def test_a_real_flat_move_still_prints_zero(self):
        # 0.0 is a real, measured, break-even move and keeps its own rendering.
        assert _chg(0.0) == "+0.0%"

    def test_the_arrow_for_unread_differs_from_the_arrow_for_flat(self):
        assert _spark(None) != _spark(0.0), (
            "'no percentage reported' and 'did not move' render identically")

    def test_the_bullish_bearish_tally_no_longer_subtracts(self):
        from tests.source_scan import code_only
        src = code_only((ROOT / "bot" / "skills" / "skill_registry.py")
                        .read_text(encoding="utf-8"))
        assert "bearish = len(top) - bullish" not in src, (
            "every row that is not provably bullish is being counted bearish")
        assert 'bearish = sum(1 for s in top if s.change_pct_24h is not None' in src


# ── the web boundary ────────────────────────────────────────────────────────

def test_the_shared_direction_chip_can_decline():
    """`up = x === 'LONG'` renders every other value as a confident SHORT, and
    the chip is used on positions and signals alike. The bot now emits a third
    value, so the renderer has to be able to not pick a side."""
    src = (ROOT / "app" / "public" / "js" / "app.js").read_text(encoding="utf-8")
    i = src.index("function dirChip")
    body = src[i:i + 900]
    assert "no direction" in body, "dirChip still resolves everything to a side"
    assert "'SHORT' || d === 'SELL'" in body or '"SHORT"' in body, (
        "SHORT must be named explicitly, not inferred from not-LONG")
