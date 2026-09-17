"""THE SCRUBBER'S UNIT WAS A LINE AND THE CARDS' UNIT IS A FIELD.

Every rule in `public_text` is about ONE label and ONE value — "a $-amount
survives only on a line whose label is a known PRICE label" — and it was
applied to a whole LINE, while `live_executor`'s close card puts three
labelled fields on one of them:

    PnL: -$0.1354 (-1.55% margin / -0.31% notional, 5x) | Fees: $0.14 | Hold: 0m

Driven against the live ARBUSDT close of 2026-09-17, that published as

    PnL: (-1.55% margin / -0.31% notional, 5x) | Fees: | Hold: 0m

— the bare `Fees:` this module's own `_strip_field` docstring calls "its own
small dishonesty ... it announces a number the reader cannot see". The rule was
there; the granularity was not, because the drop-the-label branch only fires
when the WHOLE line empties and here one field of three did.

THE OTHER DIRECTION IS WORSE AND WAS SILENT. `_is_price_line` anchors at the
start of what it is given, so a price label acquitted every figure after it:

    Exit: $0.4198 | PnL: -$0.1354      ->  0 removed, published verbatim

and `scrub_money`'s own docstring says a count of zero means "the caller
already composed public text" — so the CRITICAL log in `_post`, which exists
precisely to name a caller composing private text, does not fire either. The
backstop reporting success over the leak. `/broadcast` reaches it with
arbitrary admin text today, and the module header's promise that "a future
post method that invents a new money field is therefore scrubbed by default
rather than leaking until someone notices" was false for any field standing
after a price one.

So: a line is cut into the fields the cards compose, each field is judged by
ITS OWN label, and a field that loses its value loses its label with it.

The two posts that were already correct are the reason this is delicate —
`post_signal` and `post_trade_opened` publish `Entry:`/`Stop Loss:`/`Take
Profit:` and those are public market facts. They are driven here verbatim.
"""
from __future__ import annotations

import asyncio

import pytest

from bot.marketing.public_text import scrub_money


class _Bot:
    def __init__(self):
        self.sent: list[str] = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append(text)


def _forwarder(tmp_path, monkeypatch):
    import bot.marketing.channel_forwarder as cf
    monkeypatch.setattr(cf, "_CONFIG_PATH", tmp_path / "channel.json")
    f = cf.ChannelForwarder()
    f.set_bot(_Bot())
    f.add_group(-100123)
    return f


def _capture_critical(monkeypatch) -> list:
    import bot.marketing.channel_forwarder as cf
    seen: list[str] = []
    real = cf.system_log.critical

    def spy(msg, *a, **kw):
        seen.append(msg % a if a else msg)
        return real(msg, *a, **kw)

    monkeypatch.setattr(cf.system_log, "critical", spy)
    return seen


# The money line of `live_executor`'s close card, as it composes it.
ARB_CLOSE_LINE = ("PnL: -$0.1354 (-1.55% margin / -0.31% notional, 5x) "
                  "| Fees: $0.14 | Hold: 0m")


class TestAFieldThatLosesItsValueLosesItsLabel:
    """The reported defect: a bare `Fees:` on a public channel."""

    def test_the_live_close_line_carries_no_bare_label(self):
        out, removed = scrub_money(ARB_CLOSE_LINE)
        assert removed == 2, "the P&L and the fee, neither of them a price"
        assert "$" not in out
        # The whole point. Not `Fees:`, not `Fees`, not an empty gap where a
        # figure was — the field is gone.
        assert "Fees" not in out, out
        # And the fields that still say something are untouched.
        assert "-1.55% margin" in out and "Hold: 0m" in out

    @pytest.mark.parametrize("line,expect", [
        # Every field emptied -> the line goes, as it always did.
        ("PnL: -$0.1354 | Fees: $0.14", ""),
        # The FIRST field emptied: the separator goes with it, so the survivor
        # does not arrive behind a leading `| `.
        ("Net PnL: $+412.90 | Trades: 4", "Trades: 4"),
        # The LAST field emptied: the separator before it goes too.
        ("Trades: 4 | Net PnL: $+412.90", "Trades: 4"),
        # A middle field emptied.
        ("Trades: 4 | Net PnL: $+412.90 | Risk: Healthy",
         "Trades: 4 | Risk: Healthy"),
        # A parenthesised figure leaves the parens behind, and the name stays.
        ("Best: BTC (+$88.10) | Worst: ETH (-$12.00)", "Best: BTC | Worst: ETH"),
    ])
    def test_the_field_goes_not_just_the_figure(self, line, expect):
        out, _ = scrub_money(line)
        assert out == expect

    def test_a_single_field_line_behaves_exactly_as_before(self):
        # Most lines in this product have one field, and this granularity
        # change must be invisible to them.
        assert scrub_money("Net PnL: <code>$412.90</code>")[0] == ""
        assert scrub_money("Realized carry: $91.44") == ("", 1)

    def test_an_emptied_line_is_dropped_not_left_blank_inside_a_card(self):
        # `_strip_line` answers None rather than "" so the caller drops the
        # line. With "" the line survives as a blank one, which on a
        # multi-line public card is a gap where a figure used to be — visible,
        # and the thing the drop-the-label rule exists to avoid saying.
        out, _ = scrub_money("CLOSED LONG ARB/USDT\nRealized: $12.00\nHold: 0m")
        assert out == "CLOSED LONG ARB/USDT\nHold: 0m", out
        assert "\n\n" not in out

    def test_a_field_with_no_money_is_returned_BYTE_FOR_BYTE(self):
        # The scrubber removes money and touches nothing else. Without this
        # branch a no-money field goes through `_strip_field`, which collapses
        # runs of whitespace and trims trailing punctuation — reformatting a
        # public card it has no business editing. The double space is what
        # tells the two apart; no assertion about the P&L can.
        out, removed = scrub_money("PnL: $1.00 | Verified: \u2705  CONFIRMED")
        assert removed == 1
        assert out == "Verified: \u2705  CONFIRMED", out


class TestAPriceLabelAcquitsItsOwnFieldAndNothingElse:
    """The silent half: a leak the count reported as a clean post."""

    @pytest.mark.parametrize("line,keep,drop", [
        ("Exit: $0.4198 | PnL: -$0.1354", "$0.4198", "0.1354"),
        ("Entry: $0.4211 -> Exit: $0.4198 | Net: -$0.13", "$0.4211", "-$0.13"),
        # THE ARROW IS A FIELD BOUNDARY IN ITS OWN RIGHT. With a pipe in the
        # line the pipe alone would do the work here, so the input that
        # measures the arrow is one where the non-price field follows it
        # DIRECTLY -- the close card already composes `Entry: $X -> Exit: $Y`,
        # so a card growing a third arrow-joined field is exactly the "future
        # post method invents a money field" case the module header promises
        # to scrub by default.
        ("Entry: $0.4211 \u2192 Net: -$0.13", "$0.4211", "-$0.13"),
        ("Entry: $0.4211 -> Realized: $12.00", "$0.4211", "12.00"),
        ("Mark: $63,500 | Realized: $12.00", "$63,500", "12.00"),
    ])
    def test_the_figure_after_the_price_field_is_removed(self, line, keep, drop):
        out, removed = scrub_money(line)
        assert removed >= 1, "this used to be 0, which reads as a clean post"
        assert keep in out, out
        assert drop not in out, out

    def test_the_backstop_now_names_the_caller(self, tmp_path, monkeypatch):
        # `removed == 0` suppressed the CRITICAL that exists to say a caller
        # composed private text. The leak and the silence were one bug.
        f = _forwarder(tmp_path, monkeypatch)
        seen = _capture_critical(monkeypatch)
        asyncio.run(f.post_custom("Exit: $0.4198 | PnL: -$0.1354"))
        assert seen, "a leak the backstop reports as success is two defects"
        assert "PUBLIC" in seen[0]
        assert "0.1354" not in f._bot.sent[0]

    def test_broadcast_is_the_reachable_door(self, tmp_path, monkeypatch):
        # `/broadcast` hands arbitrary admin text to `post_custom`, so this is
        # not a hypothetical about a future post method.
        f = _forwarder(tmp_path, monkeypatch)
        asyncio.run(f.post_custom("\U0001f4e2 Exit: $0.4198 | Net PnL: $-0.13"))
        sent = f._bot.sent[0]
        assert "$0.4198" in sent
        assert "0.13" not in sent.split("$0.4198")[1]


class TestThePostsThatWereAlreadyCorrect:
    """A scrubber that cannot tell a price from a P&L breaks these two."""

    @pytest.mark.parametrize("line", [
        "Entry: <code>$63,500.0000</code>",
        "Stop Loss: <code>$62,000.0000</code> (2.4%)",
        "Take Profit: <code>$66,000.0000</code> (3.9%)",
        # Both sides of the arrow are prices, so the whole line survives.
        "Entry: $0.4211 → Exit: $0.4198",
        "Entry: $63,500.0000 -> Exit: ~$63,700.0000",
    ])
    def test_a_price_line_is_untouched_and_counts_zero(self, line):
        out, removed = scrub_money(line)
        assert out == line, out
        assert removed == 0, "a price kept is not a removal"

    def test_a_whole_signal_post_is_untouched(self):
        msg = ("\U0001f4e1 <b>RUNECLAW SIGNAL</b>\n\n"
               "\U0001f7e2 LONG <b>BTC/USDT</b>\n\n"
               "Entry: <code>$63,500.0000</code>\n"
               "Stop Loss: <code>$62,000.0000</code> (2.4%)\n"
               "Take Profit: <code>$66,000.0000</code> (3.9%)\n"
               "R:R: <code>3.1x</code>\n")
        out, removed = scrub_money(msg)
        assert out == msg
        assert removed == 0

    def test_a_line_with_no_money_is_returned_byte_for_byte(self):
        line = "Trades: 4 | W/L: 2/1 | Win Rate: 67% | Risk: Healthy"
        assert scrub_money(line) == (line, 0)


class TestTheWholeCloseCardAsTheForwarderPostsIt:
    def test_the_arb_close_end_to_end(self, tmp_path, monkeypatch):
        # The fallback path: `public_close_line` answers None for a close it
        # cannot tell in percent, and the private card is what gets scrubbed.
        f = _forwarder(tmp_path, monkeypatch)
        private = ("CLOSED LONG ARB/USDT (leverage overshoot)\n"
                   "Entry: $0.4211 → Exit: $0.4198\n"
                   + ARB_CLOSE_LINE + "\n"
                   "Verified: ✅ CONFIRMED")
        asyncio.run(f.post_trade_closed(private))
        sent = f._bot.sent[0]
        assert "$0.4211" in sent and "$0.4198" in sent, "prices are facts"
        assert "0.1354" not in sent and "$0.14" not in sent
        assert "Fees" not in sent, "the label without its figure"
        assert "-1.55% margin" in sent and "Verified" in sent


class TestTheBoundIsStatedRatherThanGuessedAt:
    """What the field rule does NOT parse, said out loud.

    A field's label is what stands before its first colon. A SECOND label
    nested inside one field is not parsed — no producer in this tree writes
    one — so `Entry: $0.42 (cost $33.84)` keeps both figures. Recorded as a
    driven fact so the day a card grows that shape, this says so rather than
    the card quietly publishing a margin figure.
    """

    def test_a_nested_label_inside_one_field_is_not_parsed(self):
        out, removed = scrub_money("Entry: $0.42 (cost $33.84)")
        assert removed == 0 and out == "Entry: $0.42 (cost $33.84)"

    def test_but_separating_it_into_its_own_field_does_catch_it(self):
        out, removed = scrub_money("Entry: $0.42 | cost: $33.84")
        assert removed == 1
        assert "$0.42" in out and "33.84" not in out
