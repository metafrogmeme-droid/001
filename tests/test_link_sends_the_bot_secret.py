"""The server-side gate is only half of /link; the bot has to send the header.

`POST /api/auth/validate-token` now requires `X-Bot-Secret`. If the bot does
not send it, every `/link` returns 403 and the user is told their token is bad
— a message pointing at the one thing that is fine.

This is the repository's own rule about a guard that is not reached, in the
other direction: the guard is reached, and the only legitimate caller is locked
out. No JavaScript test can see it, because the property lives in the Python
caller.

Two assertions, because the pair is what makes it safe:

  * the header is BUILT — a unit test on the request object, so a rename or a
    dropped line fails here rather than in production; and
  * the header is built from `BOT_SYNC_SECRET`, the same variable
    `bot/utils/website_sync.py` has used for every other bot->web call. A
    second secret for one route is a second thing to rotate and a second thing
    to forget.

The missing-secret case is asserted too, and it asserts a LOG rather than a
silent skip: a bot that cannot authenticate must say so with the cause, or the
403 downstream reads as the user's fault.
"""

from __future__ import annotations

import io
import tokenize
from pathlib import Path

import pytest

MIDDLEWARE = Path(__file__).resolve().parent.parent / "bot" / "skills" / "user_middleware.py"


def _code_only(path: Path) -> str:
    """Source with comments and docstrings blanked, offsets preserved.

    The change is explained in prose that names the very header being asserted,
    so an unstripped scan matches the explanation rather than the code. Four
    false failures in this repo have come from exactly that.
    """
    text = path.read_text(encoding="utf-8")
    doomed, prev = [], None
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                doomed.append((tok.start, tok.end))
                continue
            if tok.type == tokenize.STRING and prev in (
                    None, tokenize.NEWLINE, tokenize.NL,
                    tokenize.INDENT, tokenize.DEDENT):
                doomed.append((tok.start, tok.end))
                continue
            prev = tok.type
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return text
    grid = [list(line) for line in text.splitlines(keepends=True)]
    for (srow, scol), (erow, ecol) in doomed:
        for row in range(srow, erow + 1):
            if not 1 <= row <= len(grid):
                continue
            line = grid[row - 1]
            for i in range(scol if row == srow else 0,
                           min(ecol if row == erow else len(line), len(line))):
                if line[i] != "\n":
                    line[i] = " "
    return "".join("".join(r) for r in grid)


def test_the_link_call_sends_the_bot_secret_header():
    code = _code_only(MIDDLEWARE)
    assert "X-Bot-Secret" in code, (
        "the /link caller does not send X-Bot-Secret, so every /link will be "
        "refused 403 by the website and the user will be told their token is "
        "invalid — pointing at the one thing that is not wrong"
    )


def test_the_header_reuses_BOT_SYNC_SECRET_and_not_a_second_credential():
    code = _code_only(MIDDLEWARE)
    assert "BOT_SYNC_SECRET" in code, (
        "the /link caller authenticates with something other than "
        "BOT_SYNC_SECRET — the variable every other bot->web call already "
        "uses (bot/utils/website_sync.py). A second secret is a second thing "
        "to rotate and a second thing to forget."
    )


def test_a_missing_secret_is_logged_rather_than_sent_blank():
    """Blank-but-present is the worst of the three states.

    An empty `X-Bot-Secret` fails the constant-time comparison exactly like a
    wrong one, so the operator sees "invalid bot secret" for what is actually
    "the bot has no secret configured". The caller must notice and say so.
    """
    code = _code_only(MIDDLEWARE)
    i = code.find("X-Bot-Secret")
    assert i != -1
    window = code[max(0, i - 1200):i + 1200]
    assert "log.error" in window or "log.warning" in window, (
        "nothing reports an unset BOT_SYNC_SECRET near the /link call, so a "
        "misconfigured bot produces a 403 that reads as a bad user token"
    )


@pytest.mark.parametrize("var", ["BOT_SYNC_SECRET"])
def test_the_secret_value_is_never_logged(var):
    """Redaction, per the repo rule that secrets never reach user-facing text."""
    code = _code_only(MIDDLEWARE)
    for line in code.splitlines():
        if ("log." in line or "print(" in line) and var in line:
            assert "getenv" not in line and "environ" not in line, (
                f"a log line interpolates {var} itself rather than naming it: "
                f"{line.strip()}"
            )
