"""The marketing forwarder posts to PUBLIC Telegram groups, in dollars.

Its own module docstring says "no sensitive data. Public-facing marketing
content only." Nothing enforced that, and two of the five post methods carried
dollar amounts straight out:

    post_trade_closed(msg)      "PnL: +$12.3456 (+1.23%)"     — every close
    post_daily_report(summary)  "Net PnL: $+412.90"
                                "Best: BTC (+$88.10)"
                                "Worst: SOL (-$41.20)"

The forwarder is default-ON — `self._enabled = True` in __init__ and
`data.get("enabled", True)` on load — and groups are AUTO-DETECTED the first
time the bot sees a message in one. So this is not a configuration a careful
operator avoids; it is the default path.

    §4: PUBLIC SURFACES CARRY PERCENT, RATIO AND COUNT. PRICES ARE A PUBLIC
    MARKET FACT AND STAY. P&L IN DOLLARS IS NEITHER.

The prices are the difficulty. `post_signal` and `post_trade_opened` publish
`Entry: $63,500.0000` and `Stop Loss: $62,800.0000`, and those are correct —
a scrubber that cannot tell a price from a P&L breaks the two posts that were
already right. A dollar amount alone does not say which it is; its LABEL does.

Two layers, and the order matters:

  the CALLERS compose public text — percent from close_data, counts and the
  win rate for the day;
  `_post` scrubs what is left and logs CRITICAL when it removes anything,
  because a scrubber that quietly cleans up after its callers just moves the
  bug somewhere nobody reads.

Two further defects fell out of the same six lines of the daily report:

  the win rate divided by EVERY close while W/L came from the SCORED ones, so
  an unpriced close was outside the pair and inside the rate — two numbers on
  one line disagreeing about one day;
  a day with no closes posted "W/L: 0/0 | Win Rate: 0%", and 0% is a claim
  that everything lost.
"""
from __future__ import annotations

import asyncio
import re

import pytest

from bot.marketing.public_text import public_close_line, scrub_money


class TestPricesSurviveBecauseTheyArePublicFacts:
    """Break this and post_signal / post_trade_opened lose their content."""

    @pytest.mark.parametrize("line", [
        "Entry: <code>$63,500.0000</code>",
        "Stop Loss: <code>$62,800.0000</code> (1.1%)",
        "Take Profit: <code>$65,200.0000</code> (2.7%)",
        "Exit: ~$63,700.0000",
        "Price: $1.23",
        "Liq: $58,000",
    ])
    def test_a_price_label_keeps_its_amount(self, line):
        assert scrub_money(line) == (line, 0)

    def test_a_whole_signal_post_is_untouched(self):
        post = (
            "\U0001f4e1 <b>RUNECLAW SIGNAL</b>\n\n"
            "\U0001f7e2 LONG <b>BTC/USDT</b>\n\n"
            "Entry: <code>$63,500.0000</code>\n"
            "Stop Loss: <code>$62,800.0000</code> (1.1%)\n"
            "Take Profit: <code>$65,200.0000</code> (2.7%)\n"
            "R:R: <code>2.4x</code>\n"
            "Confidence: <code>72%</code>"
        )
        assert scrub_money(post) == (post, 0)


class TestEveryOtherDollarAmountLeaves:
    @pytest.mark.parametrize("line,expect", [
        ("Net PnL: <code>$+412.90</code>", ""),
        ("Equity: $10,000", ""),
        ("Balance: $ 250.00", ""),
        ("Best: <code>BTC</code> (+$88.10)", "Best: <code>BTC</code>"),
        ("Worst: <code>SOL</code> (-$41.20)", "Worst: <code>SOL</code>"),
    ])
    def test_the_amount_is_removed(self, line, expect):
        out, removed = scrub_money(line)
        assert removed >= 1
        assert out == expect
        assert "$" not in out

    def test_a_label_only_line_is_dropped_not_left_bare(self):
        # "Net PnL:" with nothing after it announces a number the reader
        # cannot see, which is its own small dishonesty.
        out, _ = scrub_money("Net PnL: <code>$412.90</code>")
        assert out.strip() == ""

    def test_a_line_that_still_says_something_survives(self):
        out, removed = scrub_money("PnL: +$12.3456 (+1.23%) | Hold: 42m")
        assert removed == 1
        assert "$" not in out
        assert "+1.23%" in out and "42m" in out

    def test_an_unknown_label_fails_CLOSED(self):
        # The posture that matters for a surface nobody re-reviews: a future
        # post method inventing a money field is scrubbed by default rather
        # than leaking until somebody notices.
        out, removed = scrub_money("Realized carry: $91.44")
        assert removed == 1 and "$" not in out

    def test_the_reconcile_close_message_verbatim(self):
        # The exact string live_executor builds and the forwarder used to post.
        msg = ("RECONCILED LONG BTC/USDT (take_profit)\n"
               "Entry: $63,500.0000 -> Exit: ~$63,700.0000\n"
               "PnL: +$12.3456 (+1.23%) | Hold: 42m")
        out, removed = scrub_money(msg)
        assert removed == 1, "only the PnL figure — the prices are facts"
        assert "$63,500.0000" in out and "$63,700.0000" in out
        assert "12.3456" not in out
        assert "+1.23%" in out


class TestTheCloseIsToldInPercent:
    def test_a_win_reads_as_a_percentage(self):
        line = public_close_line({
            "symbol": "BTC/USDT:USDT", "direction": "LONG",
            "reason": "take_profit", "pnl_pct": 1.23, "pnl_pct_margin": 6.15,
            "hold_time": "42m", "pnl_usd": 12.3456, "size_usd": 1000.0})
        assert "BTCUSDT" in line and "LONG" in line
        assert "+1.23%" in line and "+6.15%" in line and "42m" in line
        assert "$" not in line
        assert "12.34" not in line and "1000" not in line

    def test_the_leveraged_figure_is_omitted_when_it_adds_nothing(self):
        line = public_close_line({
            "symbol": "ETH/USDT", "direction": "SHORT", "reason": "",
            "pnl_pct": -0.5, "pnl_pct_margin": -0.5, "hold_time": ""})
        assert line.count("%") == 1
        assert "-0.50%" in line

    @pytest.mark.parametrize("bad", [
        None, {}, {"symbol": "BTC"}, {"symbol": "BTC", "pnl_pct": None},
        {"pnl_pct": 1.0}, {"symbol": "BTC", "pnl_pct": float("nan")},
        {"symbol": "BTC", "pnl_pct": float("inf")},
    ])
    def test_an_unmeasurable_close_returns_None_rather_than_zero(self, bad):
        # None hands the forwarder the private text and lets the scrubber
        # handle it — worse-looking, never wrong. A 0.00% on a close whose
        # percentage was never computed is the sentence this repo's doctrine
        # opens with.
        assert public_close_line(bad) is None

    def test_a_scratch_is_neither_colour(self):
        line = public_close_line({"symbol": "BTC", "direction": "LONG",
                                  "pnl_pct": 0.0, "hold_time": "1h"})
        assert "\U0001f7e2" not in line and "\U0001f534" not in line
        assert "+0.00%" in line


class _Bot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append(text)


def _capture_critical(monkeypatch) -> list:
    import bot.marketing.channel_forwarder as cf
    seen: list[str] = []
    real = cf.system_log.critical

    def spy(msg, *a, **kw):
        seen.append(msg % a if a else msg)
        return real(msg, *a, **kw)

    monkeypatch.setattr(cf.system_log, "critical", spy)
    return seen


def _forwarder(tmp_path, monkeypatch):
    import bot.marketing.channel_forwarder as cf
    monkeypatch.setattr(cf, "_CONFIG_PATH", tmp_path / "channel.json")
    f = cf.ChannelForwarder()
    f.set_bot(_Bot())
    f.add_group(-100123)
    return f


class TestTheBoundaryEnforcesItRegardlessOfCaller:
    """The guarantee belongs where every post passes, not at each call site."""

    def test_a_caller_that_forgets_is_still_scrubbed(self, tmp_path, monkeypatch):
        f = _forwarder(tmp_path, monkeypatch)
        asyncio.run(f.post_custom("Net PnL: $+412.90\nEntry: $63,500.0000"))
        sent = f._bot.sent[0]
        assert "412.90" not in sent
        assert "$63,500.0000" in sent

    def test_the_removal_is_reported_not_silently_swallowed(
            self, tmp_path, monkeypatch):
        # `system_log` does not propagate to the root logger, so caplog sees
        # nothing and a caplog-based assertion here would fail against WORKING
        # code. Record on the logger itself.
        f = _forwarder(tmp_path, monkeypatch)
        seen = _capture_critical(monkeypatch)
        asyncio.run(f.post_custom("Net PnL: $1.00"))
        assert seen, "a scrubber that cleans up quietly just relocates the bug"
        assert "PUBLIC" in seen[0]

    def test_a_clean_post_logs_nothing(self, tmp_path, monkeypatch):
        # A CRITICAL on every healthy post is how a real one gets skipped.
        f = _forwarder(tmp_path, monkeypatch)
        seen = _capture_critical(monkeypatch)
        asyncio.run(f.post_custom("Trades: 4 | Win Rate: 75%"))
        assert not seen

    def test_the_close_post_still_picks_the_right_emoji(
            self, tmp_path, monkeypatch):
        # The win/loss emoji used to key off "+$" — the very substring §4
        # removes — so every winning trade would have posted the losing icon.
        f = _forwarder(tmp_path, monkeypatch)
        asyncio.run(f.post_trade_closed(public_close_line({
            "symbol": "BTC", "direction": "LONG", "pnl_pct": 1.23,
            "hold_time": "1h"})))
        assert "\U0001f3c6" in f._bot.sent[0], "a win posted as a loss"
        asyncio.run(f.post_trade_closed(public_close_line({
            "symbol": "BTC", "direction": "LONG", "pnl_pct": -1.23,
            "hold_time": "1h"})))
        assert "\U0001f4c9" in f._bot.sent[1]


class TestTheCallersWereFixedAndNotOnlyTheBackstop:
    """Source checks: the scrubber must not be the only thing standing."""

    @staticmethod
    def _handler_src():
        # Every file the handler class is made of: /daily_report lives in the
        # portfolio mixin since the handler split, and a scan of one file
        # reads the move as the forward block vanishing.
        from tests.source_scan import code_only, handler_sources
        return "\n".join(code_only(p.read_text(encoding="utf-8")) for p in handler_sources())

    @classmethod
    def _daily_forward_block(cls):
        """Everything between the PRIVATE reply and the PUBLIC forward.

        Anchored on both ends deliberately. The first version of this walked
        back from the call to the last "forwarder" in the window, which lands
        on `self.forwarder.` — eleven characters — and passed happily with the
        dollars restored. A guard that cannot reach the defect it is named for
        is worse than none, because it reports safety.
        """
        src = cls._handler_src()
        start = src.index("rendered = wr_daily_report(data)")
        return src[start:src.index("post_daily_report(", start)]

    def test_the_guard_actually_spans_the_composition(self):
        # The mutation that exposed the broken anchor, kept as an assertion.
        block = self._daily_forward_block()
        assert len(block) > 600
        assert "_lines" in block and "Win Rate" in block

    def test_the_daily_report_forward_carries_no_dollar_format(self):
        assert "$" not in self._daily_forward_block(), (
            "the public daily summary is composing a dollar amount again"
        )

    def test_the_close_forward_does_not_pass_the_private_text_alone(self):
        src = self._handler_src()
        i = src.index("post_trade_closed(")
        call = src[i:i + 120]
        assert "public_close_line" in call, (
            "the private close text is being forwarded to public groups"
        )

    def test_the_zero_close_day_does_not_post(self):
        block = self._daily_forward_block()
        assert re.search(r"if\s+today_trades\s*>\s*0", block), (
            "a day with no closes posts a 0% win rate, which claims they lost"
        )

    def test_the_win_rate_denominator_is_the_scored_set(self):
        block = self._daily_forward_block()
        assert "wins / today_trades" not in block, (
            "the rate counts closes the W/L pair excludes — one line, two "
            "different denominators"
        )
        assert '_ws.get("rate")' in block or "_ws['rate']" in block
