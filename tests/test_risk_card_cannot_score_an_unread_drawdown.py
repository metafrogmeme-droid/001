"""/risk scored HEALTHY 100% from a drawdown nobody could read.

THE DEBT THIS PAYS. When the drawdown-backstop block was extracted, `/risk`
was left alone on purpose and the reason was written into CLAUDE.md: its
verdict comes from `entry_gate` rather than from this number, and making it
honest ripples into a scoring renderer, so "fix it with that renderer, not
before". This is that fix.

TWO HALVES, both defaulting toward calm.

The READ substituted a different book. `drawdown_status()` is "best-effort;
returns empty on any error", and the caller seeded its fallback from
`state.max_drawdown_pct` — the PAPER snapshot — then overwrote it only if the
status came back. So a failed read published the paper number as the ENFORCED
drawdown, which is verbatim what `drawdown_status()`'s own comment records
having already cost once: "an operator could read ~0% from a gate that was
refusing trades at 9%".

The RENDERER scored it. `dd = data.get("current_drawdown", 0.0)` made an
absent reading a measured 0% drawdown, so `healthy = 0.0 < ddl` came out True
and the card printed "HEALTHY · Health 100%". The two comments already in that
function describe this exact contradiction — they were about a high-water mark
erased by a restart; this is the reading never arriving at all. Same output,
different door.

THE RED HERRING, planted in every test below: a genuinely flat book. 0.0% is a
measurement — it must still score HEALTHY, still reach 100%, and still paint
the tile green. A fix that treats every zero as unknown is the mirror defect,
and it would land on the most common state the bot is ever in.
"""

from __future__ import annotations

import re

import pytest

from bot.warroom.warroom_bot import render_risk

BASE = {
    "daily_loss_limit": 5.0,
    "drawdown_limit": 10.0,
    "max_open_trades": 5,
    "open_trades": 1,
    "leverage_cap": 5,
    "trading_blocked_by": "",
}


def card(**over) -> str:
    data = dict(BASE)
    data.update(over)
    return re.sub(r"<[^>]+>", "", render_risk(data)["text"])


# ── the unreadable drawdown ─────────────────────────────────────────────────

class TestAnUnreadDrawdownIsNotHealth:
    @pytest.mark.parametrize("dd", [None, "", "n/a"])
    def test_it_does_not_score_healthy(self, dd):
        out = card(current_drawdown=dd)
        assert "HEALTHY" not in out, (
            f"an unreadable drawdown ({dd!r}) scored the card as healthy:\n{out}")

    @pytest.mark.parametrize("dd", [None, "", "n/a"])
    def test_it_does_not_score_a_hundred_percent(self, dd):
        out = card(current_drawdown=dd)
        assert "100%" not in out, (
            "the most reassuring number on the card came from no reading")

    def test_it_says_which_thing_is_unknown(self):
        out = card(current_drawdown=None)
        assert "UNKNOWN" in out
        assert "unreadable" in out, (
            "the status names no cause, so a reader cannot tell an unreadable "
            "gauge from a merely quiet one")

    def test_it_does_not_claim_a_warning_either(self):
        """WARNING asserts the drawdown is OUTSIDE its cap. That is a
        measurement too, and it is not available either."""
        out = card(current_drawdown=None)
        assert "WARNING" not in out

    def test_the_gauge_prints_no_number(self):
        out = card(current_drawdown=None)
        line = next(ln for ln in out.splitlines() if "Drawdown" in ln)
        assert not re.search(r"\d+\.\d+%", line), f"a percentage appeared: {line}"

    def test_a_missing_key_behaves_like_an_unreadable_one(self):
        data = dict(BASE)          # no current_drawdown at all
        out = re.sub(r"<[^>]+>", "", render_risk(data)["text"])
        assert "HEALTHY" not in out and "100%" not in out


# ── the red herring ─────────────────────────────────────────────────────────

class TestAMeasuredFlatBookIsStillAMeasurement:
    def test_zero_still_scores_healthy(self):
        out = card(current_drawdown=0.0)
        assert "HEALTHY" in out
        assert "UNKNOWN" not in out

    def test_zero_still_reaches_full_health(self):
        assert "100%" in card(current_drawdown=0.0)

    def test_zero_still_draws_its_gauge(self):
        line = next(ln for ln in card(current_drawdown=0.0).splitlines()
                    if "Drawdown" in ln)
        assert "0.0%" in line

    def test_a_real_drawdown_still_scores_and_warns(self):
        assert "HEALTHY" in card(current_drawdown=3.2)
        out = card(current_drawdown=12.0)      # past the 10% cap
        assert "WARNING" in out and "HEALTHY" not in out


# ── a blocked engine still wins ─────────────────────────────────────────────

class TestBlockedOutranksUnknown:
    def test_a_blocked_engine_says_blocked_even_when_the_gauge_is_unread(self):
        """BLOCKED is a measured fact from `entry_gate`, and it is the more
        actionable one. It must not be displaced by the new arm."""
        out = card(current_drawdown=None, trading_blocked_by="daily_loss")
        assert "BLOCKED (daily_loss)" in out
        assert "UNKNOWN" not in out

    def test_a_blocked_engine_still_scores_zero(self):
        out = card(current_drawdown=None, trading_blocked_by="drawdown")
        assert "0%" in out, "a halted engine must not show an empty score"


# ── the read stops substituting a different book ────────────────────────────

def test_the_caller_no_longer_seeds_from_the_paper_snapshot():
    from tests.source_scan import code_only
    src = code_only(open("bot/skills/telegram_handler.py", encoding="utf-8").read())
    assert "_dd_now = round(state.max_drawdown_pct, 2)" not in src, (
        "a failed read of the enforced drawdown falls back to the paper "
        "snapshot again, which is what drawdown_status() was changed to stop")
    assert "_dd_now = None" in src


class TestThePngTileDoesNotPaintAnUnreadDrawdownGreen:
    """DRIVEN, not scanned. The first version of this asserted the old
    expression was absent from the handler — and passed against a mutation
    that reintroduced it under a different variable name two lines up. The
    decision now lives in a helper that can simply be called."""

    @pytest.mark.parametrize("dd", [None, "", "n/a", True, False])
    def test_an_unreadable_value_is_muted_and_dashed(self, dd):
        from bot.formatters.drawdown_card import drawdown_tile
        text, colour = drawdown_tile(dd)
        assert colour == "gray", f"{dd!r} painted the tile {colour}"
        assert text == "--"

    def test_a_measured_flat_book_stays_green(self):
        from bot.formatters.drawdown_card import drawdown_tile
        assert drawdown_tile(0.0) == ("0.0%", "green")

    def test_a_real_drawdown_is_red(self):
        from bot.formatters.drawdown_card import drawdown_tile
        assert drawdown_tile(3.2) == ("3.2%", "red")

    def test_the_handler_routes_through_it(self):
        from tests.source_scan import code_only
        src = code_only(open("bot/skills/telegram_handler.py", encoding="utf-8").read())
        assert "drawdown_tile(dd)" in src
        assert '"color": "red" if dd > 0 else "green"' not in src
