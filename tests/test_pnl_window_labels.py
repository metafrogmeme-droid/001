"""A red total with no window reads as "today", and today was green.

2026-07-30, the live /portfolio card:

    Realized PnL: 🔴 $-3.25
    Trades: 104 | Win rate: 59%
    Recent:
      🔴 APT -0.68  🟢 UNI +0.95  🟢 WIF +1.50  🟢 MCD +0.71  🟢 UNI +1.08

Every number there is correct. `Realized PnL` sums the executor's closed
trades and `Trades: 104` counts the same filtered set, so the denominators
agree — verified, not assumed. But the figure is LIFETIME and says so
nowhere, sitting directly above a list of five recent closes that are four
green. The exchange put that day at roughly +$6.36. The card invited exactly
one wrong conclusion.

The admin card already rendered the same number as "Realized PnL: $X (104
trades)". Two surfaces, one figure, one of them qualified.

Worse, a third surface labelled the same lifetime tally "Session:" — and
both stores behind that label PERSIST (the live executor's
closed_trades.json under F-14, and the paper portfolio's state file). It
told the operator these reset with the process. They do not: the card read
"Session 62W/42L" minutes after a restart that had closed one trade.

Not a calculation bug — a labelling one. Which is the same family: a claim
the data does not support.
"""
from __future__ import annotations

import pathlib

from bot.utils.i18n import t

REG = pathlib.Path("bot/skills/skill_registry.py").read_text(encoding="utf-8")
HANDLER = pathlib.Path("bot/skills/telegram_handler.py").read_text(encoding="utf-8")


class TestTheWindowIsStated:
    """RENDERED, not grepped. A source scan sees the label; only a render
    sees what sits NEXT to it — and the defect was a lifetime total printed
    directly above a "Recent:" list of green closes."""

    def _card(self, **over):
        from bot.formatters.rich_cards import render_live_portfolio_summary
        base = dict(equity=545.29, open_count=1, exposure=51.18,
                    realized_pnl=-3.25, total_closed=104, win_rate=59.0)
        base.update(over)
        return "\n".join(render_live_portfolio_summary(**base))

    def test_the_live_portfolio_card_names_its_window(self):
        # The exact 2026-07-30 card.
        out = self._card()
        assert "Realized PnL (all-time)" in out, (
            "an unlabelled lifetime total above a 'Recent:' list reads as today"
        )

    def test_a_negative_total_is_never_shown_without_its_window(self):
        # The red number is the one that gets misread, so it is the one the
        # label matters most for.
        out = self._card(realized_pnl=-3.25)
        assert "🔴" in out
        assert "(all-time)" in out

    def test_a_positive_total_carries_the_window_too(self):
        out = self._card(realized_pnl=12.40)
        assert "🟢" in out and "(all-time)" in out

    def test_the_trade_count_is_omitted_rather_than_shown_as_zero(self):
        # No closed trades is not "0 trades at 0% win rate" — that reads as
        # a measured record of failure rather than an absence of one.
        out = self._card(total_closed=0)
        assert "Trades:" not in out
        assert "Win rate" not in out

    def test_the_admin_card_still_qualifies_its_own(self):
        # It always did; the two must not drift apart again.
        assert "({len(closed_trades)} trades)" in REG


class TestSessionMeantLifetime:
    def test_the_label_no_longer_claims_session_scope(self):
        label = t("lbl_session", "en")
        assert "Session" not in label, (
            "both stores behind this label persist across restarts"
        )
        assert "All-time" in label

    def test_the_translation_moved_with_it(self):
        # A label corrected in one language and not the other is the same
        # defect with a smaller audience.
        assert t("lbl_session", "zh") == "累計:"

    def test_both_users_of_the_label_read_persisted_stores(self):
        # The live path sums executor.closed_positions (closed_trades.json,
        # F-14); the paper path reads the portfolio snapshot, whose state
        # file is loaded at init. Neither is session-scoped.
        assert "_load_closed_trades()" in pathlib.Path(
            "bot/core/live_executor.py").read_text(encoding="utf-8")
        assert "_load_state_locked" in pathlib.Path(
            "bot/risk/portfolio.py").read_text(encoding="utf-8")
        assert HANDLER.count("t('lbl_session', lang)") == 2
