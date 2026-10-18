"""/start shows the paper book beside the live account.

18 October: with the engine live and the operator's own trading set to
paper (`/golive OFF`), the card read "Active | LIVE", the live equity, and
"Trading: Paper", with no figure for the paper book at all.
`paper_book_line` is that figure, rendered under the live lines (it names
the book; whether a confirm lands there is `confirm_is_practice`'s answer,
not this card's); a paper card already shows the paper book as its equity and gets no
second line.

Three readings, both arms: a read equity, an unreadable one, a partial one;
a book with closed trades shows its win rate, one without says so rather
than 0%.
"""
from __future__ import annotations

import ast
import inspect
import textwrap
from types import SimpleNamespace as NS

from bot.skills.start_commands import paper_book_line
from bot.utils.i18n import t


def _state(**kw):
    base = dict(equity_usd=10_250.5, open_positions=2, total_trades=8, win_rate=0.625,
                unpriced_positions=0)
    base.update(kw)
    return NS(**base)


def test_a_read_book_shows_equity_open_and_its_record():
    line = paper_book_line(_state())
    assert "Paper book" in line and "$10,250.50" in line
    assert "open <code>2</code>" in line and "62%" in line and "of 8" in line


def test_a_book_with_no_closed_trades_says_so_not_zero_percent():
    line = paper_book_line(_state(total_trades=0, win_rate=0.0))
    assert "no closed trades yet" in line and "0%" not in line


def test_an_absent_record_is_unread_not_empty():
    assert "record unread" in paper_book_line(_state(total_trades=None))
    assert "no closed trades" not in paper_book_line(_state(total_trades=None))
    assert "win rate unread" in paper_book_line(_state(win_rate=None))


def test_an_unreadable_equity_is_said_and_a_partial_one_says_partial():
    assert "unread" in paper_book_line(_state(equity_usd=None))
    assert "$" not in paper_book_line(_state(equity_usd=float("nan")))
    line = paper_book_line(_state(unpriced_positions=1))
    assert "partial: 1 unpriced" in line


def test_the_card_renders_it_under_the_win_rate_in_both_templates():
    kw = dict(name="op", status_icon="🟢", status_label="Active", status_label_zh="活躍",
              mode="LIVE", equity="$7.70", filled=0, pending_str="", pending_str_zh="",
              win_rate="53%", tier="Admin", trade_mode="📝 Paper", trade_mode_zh="模擬",
              web_url="https://x", time="11:25 UTC", paper_str=paper_book_line(_state()))
    for lang in ("en", "zh"):
        body = t("welcome_ready", lang, **kw)
        assert "Paper book" in body, lang
        assert body.index("53%") < body.index("Paper book") < body.index("Admin"), lang
    assert "Paper book" not in t("welcome_ready", "en", **{**kw, "paper_str": ""})


def test_the_handler_adds_it_on_a_live_card_only():
    """The line is chosen inside a 400-line handler; read where it is built
    and that it is passed to the template."""
    from bot.skills.telegram_handler import TelegramHandler
    src = textwrap.dedent(inspect.getsource(TelegramHandler._cmd_start))
    tree = ast.parse(src)
    assigns = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
               and any(isinstance(tg, ast.Name) and tg.id == "paper_str" for tg in n.targets)]
    assert len(assigns) == 1
    v = assigns[0].value
    assert isinstance(v, ast.IfExp) and "paper_book_line" in ast.unparse(v.body)
    assert ast.unparse(v.test) == "mode == 'LIVE'" and ast.unparse(v.orelse) == "''"
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "t"
             and n.args and isinstance(n.args[0], ast.Constant) and n.args[0].value == "welcome_ready"]
    assert calls and any(k.arg == "paper_str" for k in calls[0].keywords)
