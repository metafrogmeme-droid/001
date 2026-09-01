"""A failed balance read must not render as a complete account statement.

RC-2026-015. `LiveExecutor.fetch_balance()` answers a failure with

    {"error": str(exc), "free": 0, "used": 0, "total": 0, "holdings": []}

and `/livebalance` consumed it with `bal.get("total", 0)` and friends, so a
rejected API key, an IP allowlist miss, a bad nonce or a venue 5xx printed
`Cash $0.00 · Used $0.00 · Equity $0.00 · NET $0.00` — with no error text.
CLAUDE.md's first rule, on the card that says what the account is worth.

The root cause is why it never looked broken: `_get_exchange()` assigns
`self._exchange` BEFORE the fetch, so `fetch_balance`'s honest error branch is
reached only when exchange CONSTRUCTION fails — never for a credential the
venue rejects, which is the case an operator actually hits.

OMIT, NOT GUARD, per the table in CLAUDE.md: this is a COMPOSITE card whose
realized PnL, fees, trade count and exposure come from the local store and the
executor's own book and are perfectly readable while the venue is unreachable.
Throwing would blank all of it to report one dead source.
"""
from __future__ import annotations

import pytest

from bot.formatters.live_balance import (
    UNKNOWN,
    money,
    read_balance,
    render_balance_block,
    scrub_reason,
)

SEP = "-" * 16
_SECRET = "bg_9f3d17c4TESTKEYd0not5hip"


def _err(msg="apikey does not exist"):
    """Exactly what fetch_balance's except branch returns."""
    return {"error": msg, "free": 0, "used": 0, "total": 0, "holdings": []}


def _ok(free=1000.0, used=250.0, total=1500.0, holdings=None):
    return {"free": free, "used": used, "total": total,
            "holdings": [] if holdings is None else holdings}


def _block(bal, exposure=0.0, equity=None):
    r = read_balance(bal)
    if equity is None:
        equity = r.total
    return "\n".join(render_balance_block(r, exposure=exposure,
                                          equity=equity, sep=SEP))


# ── the reading ───────────────────────────────────────────────────────────

def test_a_failed_read_is_not_a_reading():
    assert read_balance(_err()).venue_answered is False


def test_a_failed_read_leaves_every_figure_absent():
    r = read_balance(_err())
    assert r.free is None and r.used is None and r.total is None, (
        "the zeros in the error dict were carried through as measurements"
    )


def test_holdings_are_absent_not_empty_on_a_failed_read():
    """`[]` is a measurement: it says the venue answered and you hold no spot."""
    assert read_balance(_err()).holdings is None


def test_a_successful_read_keeps_an_empty_holdings_list():
    assert read_balance(_ok()).holdings == []


@pytest.mark.parametrize("bad", [{}, {"holdings": None}, {"holdings": "BTC"},
                                 {"holdings": {}}])
def test_a_venue_that_reported_no_holdings_list_is_not_an_empty_portfolio(bad):
    """Found by a mutation that survived: this is the SUCCESS path.

    The venue answered, so the figures are real, but `holdings` was absent or
    not a list. Defaulting it to [] states "you hold no spot" on the strength
    of a field that was never read -- the same claim as the $0.00 statement,
    one section down the card.
    """
    r = read_balance({"free": 1.0, "used": 0.0, "total": 1.0, **bad})
    assert r.venue_answered is True
    assert r.holdings is None, "an unread holdings field became an empty portfolio"


def test_a_measured_zero_survives():
    """0.0 is a real reading — the account is fully deployed."""
    r = read_balance(_ok(free=0.0, used=0.0, total=0.0))
    assert r.venue_answered is True
    assert r.free == 0.0 and r.total == 0.0


def test_a_venue_that_did_not_report_the_balance_coin_reads_as_absent():
    """RC-2026-017 made `free` three-valued upstream; it must survive here."""
    r = read_balance(_ok(free=None))
    assert r.venue_answered is True
    assert r.free is None
    assert r.total == 1500.0, "one absent field must not blank the readable ones"


@pytest.mark.parametrize("junk", [None, [], "", 0, "balance"])
def test_anything_that_is_not_a_balance_is_not_a_reading(junk):
    assert read_balance(junk).venue_answered is False


# ── the rendering ─────────────────────────────────────────────────────────

def test_a_failed_read_prints_no_dollar_figures_for_the_venue():
    out = _block(_err())
    for label in ("Cash", "Used", "Equity"):
        line = next(ln for ln in out.splitlines() if ln.startswith(f"- {label}"))
        assert UNKNOWN in line, f"{label} rendered a figure from a failed read: {line!r}"
        assert "$" not in line


def test_a_failed_read_says_so_in_words():
    out = _block(_err())
    assert "Could not read" in out
    assert "not available" in out


def test_the_venue_reason_is_shown_so_the_operator_can_act():
    out = _block(_err("bitget 40037 apikey does not exist"))
    assert "40037" in out, "the operator cannot fix what they are not told"


def test_exposure_survives_a_failed_venue_read_and_is_labelled_as_ours():
    """The executor's own book is readable when the venue is not."""
    out = _block(_err(), exposure=825.50)
    line = next(ln for ln in out.splitlines() if "Exposure" in ln)
    assert "$825.50" in line
    assert "bot-tracked" in line, (
        "a figure that did not come from the venue must not sit in a block "
        "the reader takes as the venue's"
    )


def test_a_good_read_still_renders_the_real_numbers():
    """The honest fix must not blank what was measured."""
    out = _block(_ok(), exposure=100.0)
    assert "$1,000.00" in out and "$1,500.00" in out
    assert UNKNOWN not in out


def test_used_shows_the_higher_of_venue_and_book_when_both_are_readings():
    out = _block(_ok(used=250.0), exposure=900.0)
    assert "- Used: <code>$900.00</code>" in out


def test_used_is_unknown_rather_than_exposure_when_the_venue_is_unreadable():
    """`max(used, exposure)` on a failed read printed OUR number as THEIRS."""
    out = _block(_err(), exposure=900.0)
    used_line = next(ln for ln in out.splitlines() if ln.startswith("- Used"))
    assert UNKNOWN in used_line and "900" not in used_line


def test_an_unreported_cash_figure_does_not_blank_equity():
    out = _block(_ok(free=None))
    assert "- Cash: <code>unknown</code>" in out
    assert "$1,500.00" in out


# ── money() ───────────────────────────────────────────────────────────────

def test_money_never_formats_an_absence_as_a_number():
    assert money(None) == UNKNOWN
    assert money(0.0) == "$0.00", "a measured zero is a real figure"
    assert money(1234.5) == "$1,234.50"


# ── the reason must never carry a credential ──────────────────────────────

def test_a_key_in_the_query_string_never_reaches_the_card():
    raw = f"bitget GET https://api.bitget.com/api/v2/account?apiKey={_SECRET}&x=1 failed"
    out = scrub_reason(raw)
    assert _SECRET not in out, f"the API key reached the operator's screen: {out!r}"
    assert "api.bitget.com" in out, "which venue failed is the diagnostic"


def test_the_scrubbed_reason_is_what_the_block_renders():
    out = _block(_err(f"auth failed https://api.bitget.com/x?apiKey={_SECRET}"))
    assert _SECRET not in out


def test_the_reason_is_bounded():
    assert len(scrub_reason("x" * 5000)) <= 140


# ── the card itself, driven end to end ────────────────────────────────────

class TestTheRealCard:
    """A source scan cannot tell a guard that is PRESENT from one that is
    REACHED, so this drives the shipped handler."""

    ADMIN = "6307156912"

    @classmethod
    def _handler(cls):
        from bot.core.engine import RuneClawEngine
        from bot.skills.telegram_handler import TelegramHandler
        h = TelegramHandler(RuneClawEngine())
        # Seeded, or the handler diverts into new-user onboarding and never
        # reaches the card. The first draft of this file missed that, and the
        # "no $0.00" assertion PASSED against a welcome message -- a test that
        # would have gone green on the unfixed code. Every test below now
        # asserts the card actually rendered before asserting anything about
        # what it says.
        h.users.seed_admin(cls.ADMIN)
        return h

    def _render(self, bal, monkeypatch):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        h = self._handler()
        sent: list[str] = []

        exec_stub = MagicMock()
        exec_stub.fetch_balance = AsyncMock(return_value=bal)
        exec_stub._get_exchange = AsyncMock(return_value=MagicMock())
        exec_stub.open_positions = []
        exec_stub.closed_positions = []
        exec_stub.total_exposure_usd = 0.0
        monkeypatch.setattr(h.engine, "balance_view_executor",
                            lambda *_a, **_k: exec_stub)
        monkeypatch.setattr(h, "_get_tg_id", lambda *_a, **_k: self.ADMIN)
        monkeypatch.setattr(h, "_reply", AsyncMock(
            side_effect=lambda *a, **k: sent.append(
                next((x for x in a if isinstance(x, str)), ""))), raising=False)

        update, ctx = MagicMock(), MagicMock()
        update.effective_user = MagicMock(id=int(self.ADMIN))
        update.effective_user.first_name = "Op"
        update.message = MagicMock(reply_text=AsyncMock(
            side_effect=lambda *a, **k: sent.append(a[0] if a else "")))
        update.callback_query = None
        ctx.args = []
        asyncio.run(h._cmd_livebalance(update, ctx))
        return "\n".join(sent)

    @staticmethod
    def _assert_is_the_card(out):
        """Absence assertions are worthless on a page that is not the card."""
        assert "Balance" in out, (
            f"this is not the /livebalance card, so nothing below means "
            f"anything:\n{out[:400]}"
        )

    def test_the_shipped_card_does_not_print_a_zero_statement(self, monkeypatch):
        """Anchored to the venue's OWN lines, not to the whole card.

        The first draft asserted `"$0.00" not in out` and failed on a correct
        card: with an empty trade store the realized PnL really is $0.00 and
        with no open positions the exposure really is $0.00. Both are
        measurements. Asserting a short string is absent across a whole page
        is the assertion this repo has now watched misfire four times — the
        figures that must not be invented are the four the VENUE supplies.
        """
        out = self._render(_err(), monkeypatch)
        self._assert_is_the_card(out)
        for label in ("Cash", "Used", "Equity"):
            line = next(ln for ln in out.splitlines()
                        if ln.startswith(f"- {label}:"))
            assert UNKNOWN in line and "$" not in line, (
                f"a failed balance read still states {label}: {line!r}"
            )
        net = next(ln for ln in out.splitlines() if "NET:" in ln)
        assert UNKNOWN in net and "$" not in net, (
            f"the headline account value was invented from a failed read: {net!r}"
        )

    def test_the_shipped_card_still_names_the_venue_failure(self, monkeypatch):
        out = self._render(_err("bitget 40037 apikey does not exist"), monkeypatch)
        self._assert_is_the_card(out)
        assert "Could not read" in out and "40037" in out

    def test_the_shipped_card_keeps_the_parts_that_are_readable(self, monkeypatch):
        """OMIT, not guard: one dead source must not blank the rest."""
        out = self._render(_err(), monkeypatch)
        self._assert_is_the_card(out)
        assert "Net PnL" in out, "the local trade store is readable and was dropped"
        assert "trades" in out

    def test_the_shipped_card_survives_an_unreported_cash_figure(self, monkeypatch):
        """`f\"${free:,.2f}\"` on RC-2026-017's None raised TypeError, and the
        outer except swallowed the ENTIRE card — including the PnL, trade
        count and exposure that were perfectly readable."""
        out = self._render(_ok(free=None), monkeypatch)
        self._assert_is_the_card(out)
        assert "$1,500.00" in out, (
            f"an absent cash figure took the whole card down:\n{out}"
        )
