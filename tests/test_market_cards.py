"""The four commands that never got a card, and the anchor they never printed.

`/sweep`, `/zones`, `/squeeze`, `/holdtime` sit consecutively in
telegram_handler.py — one batch, written without either house convention. They
were the only 4 of 134 commands replying with bare `reply_text` and no HTML,
AND the only market commands with no `@guard`, so they bypassed the F-2
allowlist while three of them spend an exchange call per invocation.

THE DEFECT, from the real /zones source:

    price = float(closes[-1])
    dist = abs(price - z.midpoint) / price * 100
    f"Strength: {z.strength:.0%}  Dist: {dist:.1f}%"

The price is computed, used, and never printed; `abs()` then throws away the
sign. So a supply/demand map — a thing whose entire content is what sits above
you and what sits below — rendered as an unordered list of unsigned distances
from a number the reader never sees. Working from a production screenshot, the
price had to be back-solved from three zones' distances before the card could
be read at all.

RED HERRING, planted in TestTheRedHerring: a zone with `strength=0.0`. A zone
measured at zero strength and a zone whose strength could not be computed are
different facts, and `getattr(z, "strength", 0)` renders them identically. The
bar is what distinguishes them, so it must not draw an empty bar for unknown.
"""
from __future__ import annotations

import re
from types import SimpleNamespace as N

import pytest

from bot.formatters.market_cards import (money, render_holdtime, render_no_sweeps,
                                         render_no_zones, render_squeeze,
                                         render_squeeze_unavailable, render_sweeps,
                                         render_zones)

PRICE = 64840.0


def plain(card: str) -> str:
    """Card with tags stripped — assert on what the user reads, not markup."""
    return re.sub(r"<[^>]+>", "", card)


def _zone(low, high, strength, status="tested", retests=1, vol=2.0):
    return N(zone_type="demand" if high < PRICE else "supply",
             zone_low=low, zone_high=high, strength=strength, status=status,
             retests=retests, departure_pct=0.5, volume_ratio=vol,
             midpoint=(low + high) / 2)


# The real zones from the production screenshot that started this.
REAL_ZONES = [
    _zone(64260.00, 64314.28, 0.63, "fresh", 0, 1.8),
    _zone(64300.00, 64598.69, 0.60, "tested", 1, 2.1),
    _zone(64956.01, 64985.00, 0.55, "fresh", 0, 0.2),
    _zone(64043.01, 64331.00, 0.50, "tested", 13, 2.7),
    _zone(65016.91, 65380.84, 0.50, "tested", 24, 4.9),
]


class TestThePriceIsOnTheCard:
    """The anchor. Every number on these cards is relative to it, and it was
    the one number missing."""

    def test_zones_prints_the_price(self):
        assert "64,840" in plain(render_zones("BTC/USDT", PRICE, REAL_ZONES))

    def test_sweeps_prints_the_price(self):
        sig = N(sweep_type="bullish_sweep", level_price=64100.0, depth_pct=0.4,
                reversal_strength=0.7, volume_ratio=2.2, confidence=0.66,
                suggested_entry=64200.0, suggested_sl=64000.0)
        assert "64,840" in plain(render_sweeps("BTC/USDT", PRICE, [sig]))

    def test_squeeze_prints_the_price(self):
        sig = N(is_squeezing=True, squeeze_bars=4, squeeze_fired=False,
                fire_direction="", bb_width_pct=1.2, bb_width_percentile=8.0,
                momentum=0.3, confidence=0.5, description="Coiling.")
        assert "64,840" in plain(render_squeeze("BTC/USDT", PRICE, sig))

    def test_an_unreadable_price_is_said_not_faked(self):
        """Absent is never a measurement — least of all the anchor itself."""
        card = plain(render_zones("BTC/USDT", None, REAL_ZONES))
        assert "unavailable" in card.lower()
        assert "0" not in card.split("\n")[1], "a missing price must not read as 0"


class TestDistanceCarriesItsSign:
    def test_zones_above_are_positive_and_below_negative(self):
        card = plain(render_zones("BTC/USDT", PRICE, REAL_ZONES))
        assert "+" in card and "-" in card
        supply_line = [ln for ln in card.split("\n") if "65,017" in ln][0]
        demand_line = [ln for ln in card.split("\n") if "64,260" in ln][0]
        assert "+" in supply_line, "a zone above the price must read positive"
        assert "-" in demand_line, "a zone below the price must read negative"

    def test_the_old_card_could_not_express_this(self):
        """Control: `abs()` collapses both sides onto one number. Without this
        the assertion above could pass against a renderer that got lucky."""
        z_above, z_below = REAL_ZONES[4], REAL_ZONES[0]
        old_above = abs(PRICE - z_above.midpoint) / PRICE * 100
        old_below = abs(PRICE - z_below.midpoint) / PRICE * 100
        assert old_above > 0 and old_below > 0, (
            "both sides rendered positive — which is exactly why the sign had "
            "to be reintroduced")


class TestTheMapIsOrderedByPrice:
    def test_supply_above_demand_below_split_at_the_price(self):
        card = plain(render_zones("BTC/USDT", PRICE, REAL_ZONES))
        lines = card.split("\n")
        i_supply = next(i for i, ln in enumerate(lines) if "SUPPLY" in ln and "▲" in ln)
        i_price = next(i for i, ln in enumerate(lines) if "── price ──" in ln)
        i_demand = next(i for i, ln in enumerate(lines) if "DEMAND" in ln and "▼" in ln)
        assert i_supply < i_price < i_demand

    def test_rows_descend_in_price_not_strength(self):
        """The old card sorted by strength, interleaving supply and demand and
        destroying the only structure a S/D map has."""
        card = plain(render_zones("BTC/USDT", PRICE, REAL_ZONES))
        body = [ln for ln in card.split("\n") if "–" in ln and "%" in ln]
        firsts = [float(ln.strip().split("–")[0].replace(",", "")) for ln in body]
        assert firsts == sorted(firsts, reverse=True), firsts


class TestTheRedHerring:
    """A measured 0% and an uncomputable strength must not look alike."""

    def test_zero_strength_draws_an_empty_bar(self):
        card = plain(render_zones("X/USDT", PRICE, [_zone(100.0, 110.0, 0.0)]))
        row = [ln for ln in card.split("\n") if "100" in ln][0]
        assert "░" in row and "█" not in row
        assert "0%" in row, "a measured zero is a number and should print as one"

    def test_unknown_strength_draws_dots_not_a_bar(self):
        card = plain(render_zones("X/USDT", PRICE, [_zone(100.0, 110.0, None)]))
        row = [ln for ln in card.split("\n") if "100" in ln][0]
        assert "·" in row, "unknown must be visually distinct from zero"
        assert "░" not in row and "█" not in row
        assert "0%" not in row, "an uncomputed strength must never print as 0%"

    def test_the_two_rows_differ(self):
        zero = plain(render_zones("X/USDT", PRICE, [_zone(100.0, 110.0, 0.0)]))
        unknown = plain(render_zones("X/USDT", PRICE, [_zone(100.0, 110.0, None)]))
        assert zero != unknown

    def test_a_nan_strength_is_unknown_not_a_number(self):
        card = plain(render_zones("X/USDT", PRICE, [_zone(100.0, 110.0, float("nan"))]))
        assert "nan" not in card.lower()
        assert "·" in card


class TestPrecisionMatchesMagnitude:
    @pytest.mark.parametrize("value,expected", [
        (64260.0, "64,260"), (64.26, "64.26"), (0.5, "0.5000"),
        (0.00001234, "0.000012"), (None, "—"),
    ])
    def test_money_scales(self, value, expected):
        assert money(value) == expected

    def test_btc_loses_the_four_decimal_noise(self):
        """`$64,260.0000` was every row of the production card."""
        card = plain(render_zones("BTC/USDT", PRICE, REAL_ZONES))
        assert "64,260.0000" not in card
        assert "64,260" in card

    def test_a_sub_cent_symbol_keeps_its_digits(self):
        """The same format served both ends badly; check the other end too."""
        card = plain(render_zones("PEPE/USDT", 0.0000123,
                                  [_zone(0.0000120, 0.0000125, 0.5)]))
        assert "0.000012" in card


class TestSweepsDoNotInventADirection:
    def _sig(self, kind):
        return N(sweep_type=kind, level_price=64100.0, depth_pct=0.4,
                 reversal_strength=0.7, volume_ratio=2.2, confidence=0.66,
                 suggested_entry=64200.0, suggested_sl=64000.0)

    def test_an_unrecognised_type_is_not_silently_bearish(self):
        """`"UP" if "bullish" in s.sweep_type else "DOWN"` made every type that
        was neither read as a downside sweep."""
        card = plain(render_sweeps("BTC/USDT", PRICE, [self._sig("equilibrium_probe")]))
        assert "▼" not in card
        assert "EQUILIBRIUM_PROBE" in card

    def test_bullish_and_bearish_still_differ(self):
        up = plain(render_sweeps("B/USDT", PRICE, [self._sig("bullish_sweep")]))
        down = plain(render_sweeps("B/USDT", PRICE, [self._sig("bearish_sweep")]))
        assert "▲" in up and "▼" in down

    def test_suggested_levels_are_labelled_as_suggestions(self):
        """Entry/SL beside a real level invites reading them as live orders."""
        card = plain(render_sweeps("BTC/USDT", PRICE, [self._sig("bullish_sweep")]))
        assert "suggested" in card.lower()
        assert "not live orders" in card.lower()


class TestSqueezeSaysWhichOfThreeStates:
    def _sig(self, **kw):
        base = dict(is_squeezing=False, squeeze_bars=0, squeeze_fired=False,
                    fire_direction="", bb_width_pct=1.0, bb_width_percentile=20.0,
                    momentum=0.0, confidence=0.4, description="")
        base.update(kw)
        return N(**base)

    def test_the_three_states_are_distinguishable(self):
        none = plain(render_squeeze("B/USDT", PRICE, self._sig()))
        coiling = plain(render_squeeze("B/USDT", PRICE,
                                       self._sig(is_squeezing=True, squeeze_bars=5)))
        fired = plain(render_squeeze("B/USDT", PRICE,
                                     self._sig(squeeze_fired=True, fire_direction="up")))
        assert len({none, coiling, fired}) == 3
        assert "No squeeze" in none
        assert "5 bars" in coiling
        assert "FIRED UP" in fired

    def test_no_trailing_space_artefact(self):
        """`f"Status: {status} {direction}"` left a dangling space when not fired."""
        card = plain(render_squeeze("B/USDT", PRICE, self._sig()))
        for line in card.split("\n"):
            assert line == line.rstrip(), repr(line)

    def test_uncomputable_is_not_the_same_as_no_squeeze(self):
        card = plain(render_squeeze("B/USDT", PRICE, None)).lower()
        assert "not computable" in card
        assert "no squeeze" not in card

    def test_the_unavailable_card_says_which_it_is(self):
        card = plain(render_squeeze_unavailable("B/USDT")).lower()
        assert "not the same as" in card, (
            "'cannot compute' reads as 'no squeeze' unless the card says "
            "otherwise, and they are opposite trading conclusions")


class TestAbsenceIsAReadingNotAnError:
    def test_no_zones_says_the_scan_ran(self):
        assert "not a failure" in plain(render_no_zones("BTC/USDT")).lower()

    def test_no_sweeps_names_the_window_it_looked_at(self):
        assert "100 candles" in plain(render_no_sweeps("BTC/USDT"))

    @pytest.mark.parametrize("render", [render_no_zones, render_no_sweeps,
                                        render_squeeze_unavailable])
    def test_the_symbol_is_escaped(self, render):
        assert "<script>" not in render("<script>x</script>")


class TestHoldtimeIsWrappedNotRewritten:
    def test_existing_summary_survives_verbatim(self):
        """summary() already prints 'insufficient data' per strategy rather
        than '0 trades, 0% WR'. That is correct; do not touch it."""
        text = "HOLD-TIME ANALYTICS\n\nSCALP: insufficient data"
        card = plain(render_holdtime(text))
        assert "insufficient data" in card
        assert "0%" not in card

    def test_empty_input_is_stated_not_blank(self):
        assert "No analytics" in plain(render_holdtime(""))

    def test_html_in_the_summary_cannot_break_the_card(self):
        assert "<b>" not in plain(render_holdtime("a <b>b</b> c")).replace("&lt;", "<")


class TestTheCommandsAreGuardedNow:
    """The batch missed `@guard` as well as the card, and post-F-2 that means a
    caller the allowlist refuses could still spend an exchange call."""

    EXPECTED = {"sweep": "scan", "zones": "scan", "squeeze": "scan",
                "holdtime": "journal"}

    def _decorators(self):
        import ast
        import re as _re

        from tests.source_scan import handler_sources
        # Every file the handler class is made of: /holdtime lives in the
        # portfolio mixin since the handler split (the registration is still
        # in build_app), and a decorator map built from telegram_handler.py
        # alone reported a moved command as carrying no guard at all.
        srcs = [p.read_text(encoding="utf-8") for p in handler_sources()]
        reg = dict(_re.findall(r'\("([a-z_]+)",\s*(?:self\.)?(_cmd_[a-z_]+)\)', "\n".join(srcs)))
        decs = {}
        for src in srcs:
            for n in ast.walk(ast.parse(src)):
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    decs[n.name] = [ast.unparse(d) for d in n.decorator_list]
        return {c: decs.get(h, []) for c, h in reg.items()}

    @pytest.mark.parametrize("command", sorted(EXPECTED))
    def test_each_carries_a_guard(self, command):
        decs = self._decorators()[command]
        assert any("guard" in d for d in decs), f"/{command} is ungated: {decs}"

    @pytest.mark.parametrize("command", sorted(EXPECTED))
    def test_the_permission_string_is_one_a_role_actually_holds(self, command):
        """Inventing a permission grants the command to NO role but admin —
        the exact bug user_store.py documents for exposure/networth/research,
        where four commands the catalogue advertised were silently admin-only."""
        from bot.utils.user_store import ROLE_PERMISSIONS
        perm = self.EXPECTED[command]
        holders = [r for r, p in ROLE_PERMISSIONS.items() if perm in p]
        assert "trader" in holders and "viewer" in holders, (
            f"{perm!r} is held by {holders} — /{command} would be invisible to "
            "the roles the catalogue advertises it to")
