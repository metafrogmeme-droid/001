"""Tool results carry a reading, and a figure the tools did not return is not
treated as measured.

The card the skill already built stays the prose. The line in front of it
says whether that prose was read. Grounding checks figures with a unit
against those readings and publishes a rate per model. It annotates a reply
only once that rate is already low. Until then the words stand.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from bot.nlp.grounding import (
    annotation_on,
    check_reply,
    note,
    public_line,
    rate,
    reset,
    snapshot,
    telegram_read_from_html,
    tools_footer,
)
from bot.nlp.tool_reading import parse_reading, render_reading
from bot.skills.chat_runtime import _chat_ret, compose_telegram_answer


@pytest.fixture(autouse=True)
def _clean_ledger():
    reset()
    yield
    reset()


def test_a_read_card_is_not_a_second_numeric_claim():
    text = render_reading("get_portfolio", "read", "Equity $10,000")
    row = parse_reading(text)
    assert row["read_state"] == "read"
    assert row["source"] == "get_portfolio"
    assert row["value"] is None and row["unit"] is None
    assert row["as_of"]


def test_unread_supports_nothing_including_zero_and_a_read_zero_does():
    unread = render_reading("get_portfolio", "unread", "Nothing was measured.")
    read_zero = render_reading("get_portfolio", "read", "Today 0.00%")
    assert check_reply("You are flat at 0.00%.", [unread]) == (1, ["0.00%"])
    assert check_reply("You are flat at 0.00%.", [read_zero]) == (1, [])


def test_a_scalar_the_tool_measured_grounds_the_reply():
    text = render_reading("costs", "read", "the card names no percent",
                          value=12.5, unit="pct")
    assert check_reply("Spend is 12.5% of the cap.", [text]) == (1, [])
    assert check_reply("Spend is 99%.", [text]) == (1, ["99%"])


def test_dollars_in_the_card_ground_the_same_dollars():
    text = render_reading("get_portfolio", "read", "Equity $10,000")
    assert check_reply("Equity is $10,000.", [text]) == (1, [])
    assert check_reply("Equity is $10,050.", [text]) == (1, [])
    assert check_reply("Equity is $12,000.", [text]) == (1, ["$12,000"])


def test_the_rate_is_absent_until_a_figure_is_checked():
    assert rate("runeclaw-chat8b") is None
    assert public_line(snapshot()) == "figures: not measured"
    assert public_line(None) == "figures: not measured"
    note("runeclaw-chat8b", 4, 0)
    assert rate("runeclaw-chat8b") == 0.0
    assert public_line(snapshot()) == (
        "runeclaw-chat8b: 0 of 4 unverified (0%)")


def test_annotation_stays_off_until_the_rate_is_below_the_line():
    cfg = type("C", (), {"provider": type("P", (), {"value": "ollama"})(),
                         "model": "runeclaw-chat8b"})()
    events = [{"name": "get_portfolio", "ok": True, "ms": 3,
               "result": render_reading("get_portfolio", "read", "Equity $10")}]
    plain, meta = _chat_ret("You made 99% today.", cfg, True, events,
                            is_admin=True)
    assert plain == "You made 99% today."
    assert "result" not in meta["tools"][0]
    assert meta["read_from"] == "read: get_portfolio"
    assert rate("runeclaw-chat8b") == 1.0

    reset()
    note("runeclaw-chat8b", 1000, 0)
    annotated, _meta = _chat_ret("You made 99% today.", cfg, True, events,
                                 is_admin=True)
    assert annotated.startswith("You made 99% today.")
    assert "Unverified figure: 99%" in annotated

    reset()
    note("runeclaw-chat8b", 100, 2)
    held, _meta = _chat_ret("You made 99% today.", cfg, True, events,
                            is_admin=True)
    assert held == "You made 99% today."
    assert not annotation_on("runeclaw-chat8b", True)

    reset()
    note("runeclaw-chat8b", 1000, 20)
    free, _meta = _chat_ret("You made 99% today.", cfg, True, events,
                            is_admin=False)
    assert "Unverified figure: 99%" in free
    assert not annotation_on("runeclaw-chat8b", True)


def test_a_failed_tool_is_marked_in_the_footer_and_the_html_is_escaped():
    assert tools_footer([
        {"name": "get_portfolio", "ok": True},
        {"name": "whynot", "ok": False},
    ]) == "read: get_portfolio, whynot\u2717"
    assert telegram_read_from_html({}) == ""
    html = telegram_read_from_html({"read_from": "read: <script>"})
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_the_telegram_send_path_sends_the_composed_answer():
    """The handler's one remaining line: the message it sends IS the
    composed answer. The scan this replaced looked for the footer call's
    name, which survives the `+=` being dropped."""
    src = Path("bot/skills/telegram_handler.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    handler = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.AsyncFunctionDef) and n.name == "_handle_message")
    assigns = [n for n in ast.walk(handler) if isinstance(n, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == "_final" for t in n.targets)]
    assert len(assigns) == 1
    call = assigns[0].value
    assert isinstance(call, ast.Call) and getattr(call.func, "id", "") == "compose_telegram_answer"
    assert [ast.unparse(a) for a in call.args] == ["answer", "_meta"]
    # Nothing rebinds or extends it before it is sent.
    assert not [n for n in ast.walk(handler) if isinstance(n, ast.AugAssign)
                and getattr(n.target, "id", "") == "_final"]


class TestTheComposedTelegramAnswer:
    META = {"read_from": "read: get_portfolio, get_news\u2717"}

    def test_a_turn_that_read_something_carries_the_footer(self):
        out = compose_telegram_answer("Your equity is $10,000.", self.META)
        assert out == ("Your equity is $10,000.\n\n<i>read: get_portfolio, "
                       "get_news\u2717</i>")

    def test_a_turn_that_read_nothing_carries_none(self):
        assert compose_telegram_answer("Hello there.", {}) == "Hello there."
        assert compose_telegram_answer("Hello there.", None) == "Hello there."

    def test_a_substantive_reply_is_headed_and_still_footed(self):
        long = "x" * 120
        out = compose_telegram_answer(long, self.META)
        assert out.startswith("\u2694\ufe0f <b>RUNECLAW</b>\n")
        assert out.endswith("</i>") and "read: get_portfolio" in out

    def test_a_social_reply_is_not_headed(self):
        long = "x" * 120
        assert compose_telegram_answer(long, {}, is_social=True) == long

    def test_text_is_escaped_and_the_models_html_is_kept(self):
        assert compose_telegram_answer("1 < 2 & 3", {}) == "1 &lt; 2 &amp; 3"
        assert compose_telegram_answer("<b>up</b> today", {}) == "<b>up</b> today"


class TestAClockIsNotARatio:
    """`\\s*:\\s*` read "16:23" as the ratio 16:23 and "Day 1: 25%" as the
    ratio "1: 25", so a correct reply was scored one-in-three unverified and
    a fabricated 25% was never checked."""

    CARD = ["Equity $10,000 · Today +0.5%"]

    def test_a_time_of_day_is_not_checked(self):
        reply = "As of 16:23 UTC your equity is $10,000, up 0.5% today."
        assert check_reply(reply, self.CARD) == (2, [])

    @pytest.mark.parametrize("reply", [
        "Checked at 9:30, equity $10,000.",
        "Since 14:05 your equity is $10,000.",
        "Equity $10,000 at 16:23:05.",
        "Equity $10,000 (09:15 am).",
        "Equity $10,000, stamped 16:23:05.",   # a seconds run, no clock word
    ])
    def test_other_clock_shapes_are_not_checked(self, reply):
        assert check_reply(reply, self.CARD) == (1, [])

    def test_a_list_label_does_not_swallow_the_percent_after_it(self):
        assert check_reply("Day 1: 25% of the plan. Equity $10,000.", self.CARD) == (2, ["25%"])

    @pytest.mark.parametrize("reply, shown", [
        ("R:R is 1:2.5 here.", "1:2.5"), ("Target 3:1 on this one.", "3:1"),
    ])
    def test_a_ratio_written_tight_is_still_checked(self, reply, shown):
        assert check_reply(reply, self.CARD) == (1, [shown])
        assert check_reply(reply, [f"R:R {shown}"]) == (1, [])
