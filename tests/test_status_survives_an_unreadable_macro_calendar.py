"""/status went dark when the macro calendar could not answer.

`self.engine.macro_calendar.evaluate()` sat bare at the top of the command,
before any try. Every other read on the card is guarded or tri-state; this
one raised straight into the global error handler, and the operator saw
"Something broke on my end" instead of the equity, positions, drawdown and
tick lines the card exists to show. Found by driving the command against an
engine whose reads fail one at a time.

The seam is `_status_market_bias`: the level when the calendar answers, an
explicit "unread" when it cannot -- never "Normal" from a failed read.
"""
from __future__ import annotations

import io
import tokenize
from pathlib import Path
from types import SimpleNamespace

from bot.skills.telegram_handler import TelegramHandler
from bot.utils.i18n import t

ROOT = Path(__file__).resolve().parent.parent


def _host(calendar):
    h = TelegramHandler.__new__(TelegramHandler)
    h.engine = SimpleNamespace(macro_calendar=calendar)
    return h


def test_a_calendar_that_answers_gives_the_level():
    cal = SimpleNamespace(evaluate=lambda: SimpleNamespace(state=SimpleNamespace(value="risk_off")))
    assert _host(cal)._status_market_bias() == "Risk Off"


def test_a_calendar_that_raises_gives_unread_not_normal():
    def boom():
        raise RuntimeError("calendar file missing")
    out = _host(SimpleNamespace(evaluate=boom))._status_market_bias()
    assert out == t("val_bias_unread", "en")
    assert "unread" in out.lower()
    assert "normal" not in out.lower(), "a failed read must not print a level"


def test_a_missing_calendar_is_unread_too():
    out = _host(None)._status_market_bias()
    assert "unread" in out.lower()


def test_the_key_is_translated():
    for lang in ("en", "zh"):
        assert t("val_bias_unread", lang) != "val_bias_unread"


def _code_only(path: Path) -> str:
    src = path.read_text(encoding="utf-8")
    return " ".join(tok.string for tok in tokenize.generate_tokens(io.StringIO(src).readline)
                    if tok.type != tokenize.COMMENT)


def test_status_reads_the_bias_through_the_seam():
    code = _code_only(ROOT / "bot" / "skills" / "telegram_handler.py")
    i = code.find("async def _cmd_status")
    body = code[i:code.find("async def ", i + 10)]
    assert "self . _status_market_bias ( )" in body, "/status must read the bias through the seam"
    assert "macro_calendar . evaluate ( )" not in body, "the bare, unguarded calendar read is back"
    assert "market_bias = _bias" in body
