"""A signed dollar figure is spelled ``+$209.66``, never ``$+209.66``.

Every surface used to print ``f"${v:+,.2f}"``, which puts the sign after the
currency, and a reader outside called it out on 2026-10-19. One helper,
``bot/formatters/money.py::signed_usd``, spells the figure for every surface a
person reads; this file drives the helper, drives one card through the text
spy, and scans the surfaces for the old shape so a new site cannot bring it
back. The scan is two-way: a site left on the old shape would be listed with
a reason, and a listed site that moves onto the helper must be delisted.
"""
from __future__ import annotations

import os
import re

from bot.formatters import signal_card as SC
from bot.formatters.money import signed_usd
from tests.png_text import strings
from tests.source_scan import code_only

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

#: ``${x:+.2f}`` / ``${x:+,.2f}``: the sign inside the number, after the ``$``.
OLD_SHAPE = re.compile(r"\$\{[^{}]+:\+,?\.\d+f\}")

#: The whole bot, scanned whole: the Telegram text, the cards, the war room,
#: the monitors, the backtest reports and the journal lines alike.
SURFACES = ("bot",)

#: Sites that keep the old shape, with the reason a reviewer can check.
#: Two-way: a count that changes fails here, so a site moved onto the helper is
#: delisted in the same commit. Empty since the sweep reached the backtest
#: reports and the exchange-sync journal line too.
LEFT_AS_THEY_WERE: dict = {}


def _py_files(rel):
    top = os.path.join(ROOT, rel)
    for dirpath, _dirs, files in os.walk(top):
        if "__pycache__" in dirpath:
            continue
        for f in sorted(files):
            if f.endswith(".py"):
                yield os.path.relpath(os.path.join(dirpath, f), ROOT)


def _hits(rel):
    src = code_only(open(os.path.join(ROOT, rel), encoding="utf-8").read())
    return len(OLD_SHAPE.findall(src))


class TestTheHelper:
    def test_the_sign_comes_before_the_dollar(self):
        assert signed_usd(209.66) == "+$209.66"
        assert signed_usd(-44) == "-$44.00"
        assert signed_usd(0) == "+$0.00", "a measured break-even is a plus, as a statement prints it"
        assert signed_usd(-0.004) == "-$0.00", "the sign is the number's, not the rounding's"

    def test_grouping_and_places_are_the_callers(self):
        assert signed_usd(1418.05) == "+$1,418.05"
        assert signed_usd(1418.05, grouped=False) == "+$1418.05"
        assert signed_usd(0.4821, places=4) == "+$0.4821"
        assert signed_usd("-12.5") == "-$12.50", "a string figure from a record is still a number"


class TestTheCardsSpellItSo:
    POSITION = {"symbol": "B/USDT:USDT", "direction": "SHORT", "is_live": False, "entry": 0.12479, "now": 0.1211,
                "pnl_pct": 14.78, "pnl_usd": 209.66, "net_pnl": 207.86, "fees": 1.79, "size_usd": 1418.05,
                "leverage": 5, "hold_time": "6.5h", "rr": 0.6, "sl": 0.13095, "tp": 0.11555}

    def test_the_position_card_prints_the_sign_before_the_dollar(self):
        strs = strings(SC.render_position_card, self.POSITION)
        assert "  (+$209.66)" in strs, strs
        assert "+$207.86  (fees $1.79)" in strs, strs
        assert not [s for s in strs if "$+" in s or "$-" in s], strs

    def test_and_a_loss_the_same_way(self):
        lost = strings(SC.render_position_card, dict(self.POSITION, pnl_pct=-3.1, pnl_usd=-44.0, net_pnl=-45.79))
        assert "  (-$44.00)" in lost, lost
        assert "-$45.79  (fees $1.79)" in lost, lost
        assert not [s for s in lost if "$+" in s or "$-" in s], lost


class TestNoSurfaceGoesBack:
    def test_the_surfaces_carry_only_the_listed_sites(self):
        found = {}
        for rel in (f for top in SURFACES for f in _py_files(top)):
            n = _hits(rel)
            if n:
                found[rel] = n
        assert found == LEFT_AS_THEY_WERE, (
            "sign-after-dollar sites changed; move a new one onto signed_usd, "
            "or delist one that moved:\n" + "\n".join(f"  {k}: {v}" for k, v in sorted(found.items())))

    def test_the_scan_sees_the_shape_it_forbids(self):
        """Both arms: a scan that matched nothing would pass on an empty regex."""
        assert OLD_SHAPE.search('f"net ${x:+,.2f}"') and OLD_SHAPE.search('f"${v:+.4f}"')
        assert not OLD_SHAPE.search('f"{signed_usd(x)}"') and not OLD_SHAPE.search('f"${x:,.2f}"')

    def test_the_helper_itself_is_not_scanned_as_a_hit(self):
        """The helper's docstring quotes the old shape; ``code_only`` blanks it."""
        assert _hits("bot/formatters/money.py") == 0
