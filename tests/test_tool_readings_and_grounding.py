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
from bot.skills.chat_runtime import _chat_ret


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


def test_the_telegram_send_path_appends_the_footer():
    src = Path("bot/skills/telegram_handler.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_handle_message":
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Call)
                        and isinstance(sub.func, ast.Name)
                        and sub.func.id == "telegram_read_from_html"):
                    found = True
    assert found, "the send path does not append the reading footer"
