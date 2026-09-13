"""A stated risk:reward is checked against ITS OWN setup's levels, or not at all.

`computed_ratio` read the FIRST entry/stop/target on the page (`search`, not
`finditer`) and `correct_stated_rr` rewrote EVERY stated ratio to that one
number. A reply carrying two setups — a BTC long at 2.0 and an ETH short at
3.0, both true — came out with the second ratio overwritten by the first
setup's, and `_chat_ret` wrote an `rr_corrected` audit line saying a lie had
been fixed. The same shape on one setup with a ladder of targets (TP1/TP2):
the ratio "to TP2" was rewritten to the TP1 figure. And a two-row table was
read as its first row, so the second row's cell was rewritten to the first
row's ratio.

A level stated with two distinct values is two trades (`AMBIGUOUS`), and no
ratio on such a page is attributable to either. The page is then read one
paragraph at a time, each against its own levels, and a table one ROW at a
time, each against its own cells; a paragraph holding two setups is left
exactly as written.

Red herrings: a single setup that repeats a level with the SAME value is
still one setup and still corrected; a wrong ratio inside a two-setup
paragraph is left alone (nothing says which setup it is for); a multi-row
table's correct rows stay; "target 2.50" on a two-dollar coin is a level,
not a ladder.
"""
from __future__ import annotations

from bot.nlp.rr_honesty import AMBIGUOUS, computed_ratio, correct_stated_rr, levels
from tests.test_rr_honesty import LIVE_2026_09_01

TWO_TRUE = ("**Setup 1 — BTC long**\nEntry: 60000\nStop Loss: 59000\nTake Profit: 62000\nR:R 2.00\n\n"
            "**Setup 2 — ETH short**\nEntry: 3000\nStop Loss: 3100\nTake Profit: 2700\nR:R 3.00")

TABLE_TWO_ROWS = ("Symbol | Side | Entry | Stop Loss | Take Profit | R:R\n"
                  "-------|------|-------|-----------|-------------|-----\n"
                  "BTC | LONG | 60000 | 59000 | 62000 | 1:2.00\n"
                  "ETH | SHORT | 3000 | 3100 | 2700 | 1:4.00")


class TestTwoSetupsInProse:
    def test_two_true_ratios_are_left_alone(self):
        out, n = correct_stated_rr(TWO_TRUE)
        assert n == 0 and out == TWO_TRUE

    def test_the_page_holds_two_trades_so_no_one_ratio_is_the_pages(self):
        assert levels(TWO_TRUE) is AMBIGUOUS
        assert computed_ratio(TWO_TRUE) is None

    def test_a_wrong_second_ratio_is_corrected_against_its_own_levels(self):
        text = TWO_TRUE.replace("R:R 3.00", "R:R 4.00")
        out, n = correct_stated_rr(text)
        assert n == 1
        assert "R:R 2.00" in out and "R:R 3.00" in out and "4.00" not in out

    def test_a_wrong_first_ratio_is_corrected_against_its_own_levels(self):
        text = TWO_TRUE.replace("R:R 2.00", "R:R 2.50")
        out, n = correct_stated_rr(text)
        assert n == 1 and "R:R 2.00" in out and "R:R 3.00" in out and "2.50" not in out

    def test_both_wrong_each_corrected_to_its_own(self):
        text = TWO_TRUE.replace("R:R 2.00", "R:R 1.50").replace("R:R 3.00", "R:R 5.00")
        out, n = correct_stated_rr(text)
        assert n == 2
        assert out.index("R:R 2.00") < out.index("R:R 3.00")
        assert "1.50" not in out and "5.00" not in out

    def test_a_bold_heading_splits_without_a_blank_line(self):
        text = TWO_TRUE.replace("\n\n", "\n").replace("R:R 3.00", "R:R 4.00")
        out, n = correct_stated_rr(text)
        assert n == 1 and "R:R 3.00" in out and "R:R 2.00" in out

    def test_a_blank_line_alone_splits(self):
        text = ("BTC long: Entry 60000, Stop Loss 59000, Take Profit 62000, R:R 2.50\n\n"
                "ETH short: Entry 3000, Stop Loss 3100, Take Profit 2700, R:R 3.00")
        out, n = correct_stated_rr(text)
        assert n == 1 and "R:R 2.00" in out and "R:R 3.00" in out

    def test_a_numbered_setup_heading_splits_too(self):
        text = ("Setup 1\nEntry: 60000\nStop Loss: 59000\nTake Profit: 62000\nR:R 2.50\n"
                "Setup 2\nEntry: 3000\nStop Loss: 3100\nTake Profit: 2700\nR:R 3.00")
        out, n = correct_stated_rr(text)
        assert n == 1 and "R:R 2.00" in out and "R:R 3.00" in out

    def test_two_setups_in_one_paragraph_are_left_alone_even_when_one_is_wrong(self):
        # RED HERRING: nothing on the page says which setup the ratio is for.
        text = ("BTC long: entry 60000, stop 59000, target 62000, R:R 2.50; "
                "ETH short: entry 3000, stop 3100, target 2700, R:R 3.00")
        out, n = correct_stated_rr(text)
        assert n == 0 and out == text

    def test_the_correction_is_idempotent(self):
        text = TWO_TRUE.replace("R:R 3.00", "R:R 4.00")
        once, n1 = correct_stated_rr(text)
        twice, n2 = correct_stated_rr(once)
        assert n1 == 1 and n2 == 0 and once == twice


class TestOneSetupIsStillOneSetup:
    def test_a_level_repeated_with_the_same_value_is_still_corrected(self):
        # RED HERRING: the header and the check list both say Entry 100.
        text = ("Entry: 100.00\nStop Loss: 97.00\nTake Profit: 103.50\nRisk:Reward: 1.25\n"
                "Check list: entry 100.00 confirmed, stop 97.00 set, target 103.50")
        out, n = correct_stated_rr(text)
        assert n == 1 and "Risk:Reward: 1.17" in out
        assert levels(text) == (100.0, 97.0, 103.5, None)

    def test_a_level_repeated_with_a_different_value_is_two_trades(self):
        text = ("Entry: 100.00\nStop Loss: 97.00\nTake Profit: 103.50\nRisk:Reward: 1.25\n"
                "Alternative entry 101.00 if it breaks out first")
        out, n = correct_stated_rr(text)
        assert n == 0 and out == text
        assert levels(text) is AMBIGUOUS

    def test_the_reading_carries_the_direction(self):
        assert levels("Direction: SHORT\nEntry: 185.50\nStop Loss: 189.89\nTake Profit: 172.14") == (
            185.5, 189.89, 172.14, "SHORT")
        assert levels("") is None and levels("R:R 2.0 by my read") is None


class TestALadderOfTargets:
    def test_a_ratio_to_the_second_target_is_not_rewritten_to_the_first(self):
        text = ("Entry: 60000\nStop Loss: 59000\nTP1: 62000\nTP2: 64000\n"
                "R:R 1:4.00 to TP2")
        out, n = correct_stated_rr(text)
        assert n == 0 and out == text
        assert computed_ratio(text) is None and levels(text) is AMBIGUOUS

    def test_every_spelling_of_a_second_target(self):
        for ladder in ("target 2 at 64000", "Take Profit 2: 64000", "TP 2: 64000", "TP-2: 64000"):
            text = f"Entry: 60000\nStop Loss: 59000\nTake Profit: 62000\n{ladder}\nR:R 4.00"
            out, n = correct_stated_rr(text)
            assert n == 0 and out == text, ladder

    def test_a_price_that_starts_with_a_digit_is_a_level_not_a_ladder(self):
        # RED HERRING: "target 2.50" on a two-dollar coin.
        text = "Entry at 2.30, stop at 2.20, target 2.50, R:R 3.00"
        out, n = correct_stated_rr(text)
        assert n == 1 and "R:R 2.00" in out


class TestAMultiRowTable:
    def test_each_row_is_checked_against_its_own_cells(self):
        out, n = correct_stated_rr(TABLE_TWO_ROWS)
        assert n == 1
        assert "BTC | LONG | 60000 | 59000 | 62000 | 1:2.00" in out
        assert "ETH | SHORT | 3000 | 3100 | 2700 | 1:3.00" in out
        assert levels(TABLE_TWO_ROWS) is AMBIGUOUS

    def test_a_side_cell_that_contradicts_the_geometry_leaves_the_row_alone(self):
        # Told LONG with the target below entry: unreadable, so the wrong
        # 1:4.00 stays rather than being "corrected" from a guessed sign.
        text = TABLE_TWO_ROWS.replace("ETH | SHORT", "ETH | LONG")
        out, n = correct_stated_rr(text)
        assert n == 0 and "| 1:4.00" in out

    def test_prose_beside_a_two_row_table_names_no_row(self):
        text = TABLE_TWO_ROWS + "\n\nThe ETH short offers a 1:4.00 R:R."
        out, n = correct_stated_rr(text)
        assert n == 1
        assert "2700 | 1:3.00" in out and "offers a 1:4.00 R:R" in out

    def test_a_single_row_table_with_a_side_column_reads_its_direction(self):
        text = ("Side | Entry | Stop Loss | Take Profit | R:R\n---|---|---|---|---\n"
                "SHORT | 3000 | 3100 | 2700 | 1:4.00")
        assert levels(text) == (3000.0, 3100.0, 2700.0, "SHORT")
        out, n = correct_stated_rr(text)
        assert n == 1 and "2700 | 1:3.00" in out

    def test_a_ladder_column_is_ambiguous(self):
        text = ("Entry | Stop Loss | Take Profit 1 | Take Profit 2 | R:R\n---|---|---|---|---\n"
                "60000 | 59000 | 62000 | 64000 | 1:4.00")
        out, n = correct_stated_rr(text)
        assert n == 0 and out == text and levels(text) is AMBIGUOUS

    def test_the_live_one_row_table_is_still_one_setup(self):
        # RED HERRING: the 2026-09-01 shape keeps both its corrections.
        out, n = correct_stated_rr(LIVE_2026_09_01)
        assert n == 2 and "$61,000 | 1:1.50" in out and "for a 1:1.50 R:R" in out


class TestTheAuditTrail:
    """`_chat_ret` logs `rr_corrected` for what it corrected — a two-setup
    reply whose ratios are both true must produce no such line."""

    def _events(self, monkeypatch):
        from bot.skills import chat_runtime as rt
        events = []
        monkeypatch.setattr(rt, "audit", lambda *a, **kw: events.append(kw.get("action")))
        return rt, events

    def test_no_correction_is_logged_for_two_true_ratios(self, monkeypatch):
        rt, events = self._events(monkeypatch)
        assert rt._chat_ret(TWO_TRUE, None, False) == TWO_TRUE
        assert "rr_corrected" not in events

    def test_a_real_correction_in_a_two_setup_reply_is_logged_once(self, monkeypatch):
        rt, events = self._events(monkeypatch)
        out = rt._chat_ret(TWO_TRUE.replace("R:R 3.00", "R:R 4.00"), None, False)
        assert "R:R 3.00" in out and events.count("rr_corrected") == 1
