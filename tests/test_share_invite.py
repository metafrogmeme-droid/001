"""The share button, and the account size it must never publish.

The community layer was never missing — arena, duel, leaderboard, copy, feed
and frame are 35 web endpoints. The BOT exposed one community command,
`switch_inline_query` appeared nowhere, `/start` discarded its deep-link
payload, and `render_share_card` (a PNG renderer that already carries a privacy
contract in its docstring) was reachable only from the web gateway. The door
was missing, not the room.

THE RISK THIS FILE EXISTS FOR. CLAUDE.md: *no dollar amounts on public,
community, leaderboard or marketplace payloads — percent, ratio and count
only.* The operator's own close card is private and correctly reads
`PnL: $+0.15`. A button that FORWARDED that card would publish account size
into a group chat in one tap, with no warning and no way to retract it.

So a share is a re-render and never a forward, and `assert_no_money` runs
inside the producer using the audited `_MONEY` pattern from
bot/marketing/public_text — not a second copy, which would drift.

RED HERRING, planted in TestTheRedHerring: `pnl_pct = 0.0`. A break-even close
is a real, measured result and must still be shareable; an UNREADABLE one must
produce no button at all. `(x or 0)` renders them identically, and `>= 0` would
additionally call the unreadable one a win.
"""
from __future__ import annotations

import pytest

from bot.formatters.share_invite import (MoneyLeak, assert_no_money,
                                         close_share_button, close_share_text,
                                         invite_link, parse_start_payload,
                                         telegram_share_url, valid_ref_code)

# The real TAO close from the session that prompted this.
#
# THIS FIXTURE WAS ALWAYS SELF-CONSISTENT AND NOBODY READ IT THAT WAY. The
# published `pnl_pct_margin` is 9.47, and the account made $0.15 on $1.88 of
# margin, which is 7.98% — the 1.49-point gap being its $0.03 of fees over that
# same margin (1.60%, and the remainder is the fixture's own rounding). 9.47 is
# `price_move x leverage`, computed by `close_pct` from two prices, with no fee
# in it. At 20x a limit-in/market-out round trip costs (0.02 + 0.06)% x 20 =
# 1.6% of margin, so that gap is not noise; it is the constant, and the share
# sheet published the flattering side of it.
TAO = {"symbol": "TAO/USDT:USDT", "direction": "LONG", "pnl_pct": 0.4736,
       "pnl_pct_margin": 9.47, "pnl_pct_margin_net": 7.98,
       "leverage": 20, "hold_time": "1.8h",
       "pnl_usd": 0.15, "size_usd": 1.88, "fees": 0.03, "entry": 204.82,
       "exit": 205.79}


class TestNoDollarFigureCanEscape:
    """The guard is in the producer because the leak is unretractable."""

    def test_the_share_text_carries_no_money(self):
        text = close_share_text(TAO, invite_link("bot", "abc"))
        assert "$" not in text, text
        for forbidden in ("0.15", "1.88", "0.03"):
            assert forbidden not in text, f"{forbidden} is an account figure"

    def test_percent_and_leverage_survive(self):
        """Scrubbing must not gut the card — percent is explicitly allowed."""
        text = close_share_text(TAO)
        assert "+7.98%" in text and "20×" in text and "TAOUSDT" in text

    def test_assert_no_money_actually_fires(self):
        """A guard nobody has driven to failure is not a guard."""
        with pytest.raises(MoneyLeak):
            assert_no_money("closed +9.47% ($0.15)")
        with pytest.raises(MoneyLeak):
            assert_no_money("net $1,234.56")

    def test_it_uses_the_audited_pattern_not_a_second_one(self):
        """Two definitions of 'what counts as money' drift. Same regex object."""
        from bot.formatters import share_invite
        from bot.marketing.public_text import _MONEY
        assert share_invite._MONEY is _MONEY

    def test_the_share_url_is_guarded_too(self):
        """URL-encoding hides a '$' from a naive eyeball check, so the guard
        runs before encoding rather than on the finished link."""
        with pytest.raises(MoneyLeak):
            telegram_share_url("I made $500 today", "https://t.me/bot")

    def test_the_private_card_still_shows_dollars(self):
        """Control. If the operator's own card lost its dollars, this file
        would be 'passing' by having broken the thing it protects."""
        assert TAO["pnl_usd"] == 0.15, "the private payload keeps its figures"


class TestTheRedHerring:
    def test_a_measured_break_even_is_still_shareable(self):
        data = dict(TAO, pnl_pct=0.0, pnl_pct_margin=0.0,
                    pnl_pct_margin_net=0.0, pnl_usd=0.0)
        text = close_share_text(data)
        assert text is not None, "0.00% is a real result, not a missing one"
        assert "+0.00%" in text

    def test_an_unreadable_close_produces_no_button_at_all(self):
        data = dict(TAO, pnl_pct=None, pnl_pct_margin=None,
                    pnl_pct_margin_net=None, pnl_usd=None)
        assert close_share_text(data) is None
        assert close_share_button(data, "bot") is None

    def test_break_even_is_not_labelled_a_win(self):
        """`>= 0` would call it one, and would call an unreadable close one too."""
        btn = close_share_button(dict(TAO, pnl_pct=0.0, pnl_pct_margin=0.0,
                                      pnl_pct_margin_net=0.0, pnl_usd=0.0), "bot")
        assert btn["text"] == "📣 Share", btn["text"]

    def test_a_real_win_is(self):
        assert close_share_button(TAO, "bot")["text"] == "📣 Share this win"

    def test_a_loss_is_shareable_but_not_a_win(self):
        btn = close_share_button(dict(TAO, pnl_pct=-1.0, pnl_pct_margin=-20.0,
                                      pnl_pct_margin_net=-21.6, pnl_usd=-0.41), "bot")
        assert btn is not None and btn["text"] == "📣 Share"

    def test_a_nan_percent_is_unreadable_not_zero(self):
        data = dict(TAO, pnl_pct=float("nan"), pnl_pct_margin=float("nan"),
                    pnl_pct_margin_net=float("nan"), pnl_usd=float("nan"))
        assert close_share_text(data) is None


class TestLeverageGoesWithTheNumber:
    """+0.47% of price became +7.98% on margin at 20×. Publishing the second
    alone reads as skill and is what a reader compares to their own unlevered
    results. Percent breaks no dollar rule and can still mislead.

    Leverage multiplies the FEE as well as the move, which is why the figure
    here is 7.98 and not the 9.47 this class was written against: a 20x round
    trip costs 1.6-2.4% of margin before the position does anything."""

    def test_the_underlying_move_and_leverage_are_stated(self):
        text = close_share_text(TAO)
        assert "20×" in text and "+0.47%" in text

    def test_an_unlevered_close_is_not_cluttered_with_1x(self):
        text = close_share_text(dict(TAO, leverage=1, pnl_pct_margin=0.47,
                                     pnl_pct_margin_net=0.45, pnl_pct=0.47))
        assert "1×" not in text

    def test_the_headline_matches_the_operators_own_card(self):
        """A share quoting +0.47% would look like a different trade to the
        person who posted it — so the share is on the operator's own basis.

        That card used to show +9.47% and both now show +7.98%: the basis was
        always right and the NUMBER on it was gross. The property this test
        exists for is unchanged and is now checkable against a card that is
        also correct, rather than against one that agreed by being wrong in
        the same way.
        """
        assert "+7.98%" in close_share_text(TAO)

    def test_leverage_is_omitted_when_the_move_is_unknown(self):
        """Cannot state '20× on a ?% move' — so it states neither."""
        text = close_share_text(dict(TAO, pnl_pct=None))
        assert "20×" not in text and "+7.98%" in text


class TestTheInviteLink:
    def test_a_valid_code_becomes_a_deep_link(self):
        assert invite_link("HTRUNECLAW_bot", "ab12") == \
            "https://t.me/HTRUNECLAW_bot?start=ref_ab12"

    def test_an_at_prefix_is_tolerated(self):
        assert invite_link("@HTRUNECLAW_bot").endswith("/HTRUNECLAW_bot")

    def test_an_unknown_username_yields_None_not_a_guess(self):
        """A hardcoded handle keeps minting a plausible link after a rename —
        one that sends people to whoever claimed the old name."""
        assert invite_link(None, "ab12") is None
        assert invite_link("", "ab12") is None

    @pytest.mark.parametrize("bad", ["", None, "has space", "a" * 80, "semi;colon"])
    def test_a_malformed_code_is_dropped_not_embedded(self, bad):
        """Telegram silently drops an invalid start payload, so the link would
        look fine and attribute nothing."""
        assert valid_ref_code(bad) is False
        link = invite_link("bot", bad)
        assert link == "https://t.me/bot", link

    def test_the_share_button_works_without_any_invite(self):
        """No username configured must not cost the share button."""
        btn = close_share_button(TAO, None)
        assert btn is not None and "t.me/share/url" in btn["url"]


class TestStartPayloadParsing:
    def test_a_ref_payload_is_read(self):
        assert parse_start_payload("ref_ab12cd") == "ab12cd"

    @pytest.mark.parametrize("payload", [None, "", "hello", "ref_", "xref_ab",
                                         "ref_has space", "deeplink_ab12"])
    def test_anything_else_is_not_a_referral(self, payload):
        assert parse_start_payload(payload) is None


class TestAttributionIsWriteOnce:
    def _store(self, tmp_path):
        from bot.utils.user_store import UserStore
        store = UserStore(tmp_path / "users.json")
        store.register(4242, name="Ann")
        return store

    def test_the_first_referrer_is_recorded(self, tmp_path):
        store = self._store(tmp_path)
        assert store.record_referrer(4242, "alice") is True
        assert store.get(4242)["referred_by"] == "alice"

    def test_a_later_link_cannot_rewrite_it(self, tmp_path):
        """Without write-once the field is farmable: resend your own link to an
        existing user and take the credit."""
        store = self._store(tmp_path)
        store.record_referrer(4242, "alice")
        assert store.record_referrer(4242, "mallory") is False
        assert store.get(4242)["referred_by"] == "alice"

    def test_self_referral_is_refused(self, tmp_path):
        store = self._store(tmp_path)
        assert store.record_referrer(4242, "4242") is False
        assert "referred_by" not in store.get(4242)

    def test_an_unknown_user_records_nothing(self, tmp_path):
        assert self._store(tmp_path).record_referrer(9999, "alice") is False

    def test_it_survives_a_reload(self, tmp_path):
        from bot.utils.user_store import UserStore
        store = self._store(tmp_path)
        store.record_referrer(4242, "alice")
        assert UserStore(tmp_path / "users.json").get(4242)["referred_by"] == "alice"


def test_the_share_url_opens_telegrams_own_picker():
    """`t.me/share/url` needs no inline mode, no BotFather change and no image
    host. `switch_inline_query` needs all three, and would have shipped a
    button opening an empty picker until someone enabled inline mode."""
    url = telegram_share_url("closed +9.47%", "https://t.me/bot?start=ref_x")
    assert url.startswith("https://t.me/share/url?")
    assert "url=" in url and "text=" in url
    assert "%2B9.47%25" in url or "9.47" in url
