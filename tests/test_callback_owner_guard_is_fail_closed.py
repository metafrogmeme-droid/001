"""A callback's owner tag decides authorization, so its ABSENCE must deny.

`_uid_matches` is documented "Returns True if ... expected_uid is empty/None
(allow all)". That semantic is right for the auto-scan broadcast it was written
for, where `CONFIG.telegram.chat_id` may hold several ids and an untagged
button is legitimately for everyone. It is exactly wrong for a per-user trade
button, where an untagged payload cannot have come from a real button at all.

`confirm:` and `reject:` were retrofitted accordingly and say so at
`telegram_handler.py:14060-14063` — "Every legitimate confirm button is built as
`confirm:<id>:<uid>`, so a missing owner tag means a crafted/replayed callback
— deny rather than allow." `setlimit:` was missed. It called the same predicate
through `if expected_uid and not self._uid_matches(...)`, so the `and` short-
circuited on the one payload shape the guard exists to catch.

What that reached: `setlimit:` looks the trade up in
`engine._pending_ideas` (`bot/core/engine.py:508`), a single global
`dict[str, TradeIdea]` with no owner field and no caller filter on the read. So
an untagged crafted payload disclosed another user's asset, direction, entry,
stop and target, and armed `_pending_limit_input[caller]` against their trade —
with `engine.confirm_trade` performing no ownership check of its own. Unlike
`pos_close_`, which is fail-open on the same shape BY DESIGN and says so
(`:13673-13676`), `setlimit:` has no second layer: `pos_close_` resolves the
position through `user_portfolios.get(user_id)` and `_caller_executor(update)`,
both keyed by the caller.

All four `setlimit:` construction sites (`:2797`, `:8440`, `:9578`, `:11483`)
emit the uid, so denying the untagged form breaks no real button.

The three branches now share one predicate rather than three hand-written
conditions, because the defect was drift between copies of the same rule.
"""

from __future__ import annotations

import re

import pytest

from bot.skills.telegram_handler import TelegramHandler
from tests.source_scan import handler_sources

# The three branches live in _handle_callback, in the callback mixin since
# the handler split; the scans below read every file the handler class is
# made of rather than telegram_handler.py by path.


class TestTheOwnerPredicate:
    """`_callback_owner_ok` — the seam the three branches now agree on."""

    def test_an_absent_owner_tag_is_denied(self):
        # THE defect. A crafted `setlimit:TI-123` with no third field used to
        # sail through, because `expected_uid and ...` is False-y and the
        # negation of False is not a denial.
        assert TelegramHandler._callback_owner_ok("555", None) is False
        assert TelegramHandler._callback_owner_ok("555", "") is False

    def test_a_mismatched_owner_is_denied(self):
        assert TelegramHandler._callback_owner_ok("555", "999") is False

    def test_the_owner_is_allowed(self):
        assert TelegramHandler._callback_owner_ok("555", "555") is True

    def test_a_comma_list_still_admits_each_member(self):
        # The auto-scan case `_uid_matches` was built for: several chat ids
        # share one broadcast button. Narrowing the empty case must not narrow
        # this one too.
        assert TelegramHandler._callback_owner_ok("555", "111,555,999") is True
        assert TelegramHandler._callback_owner_ok("777", "111,555,999") is False

    def test_an_unidentifiable_caller_is_denied(self):
        assert TelegramHandler._callback_owner_ok(None, "555") is False
        assert TelegramHandler._callback_owner_ok(None, None) is False


def _code_only(text: str) -> str:
    """Source with comments and docstrings blanked.

    The branches below are described in prose that quotes the very expression
    being forbidden, so an unstripped scan matches the explanation and fails on
    a file that is correct. CLAUDE.md records four separate false failures from
    exactly this.
    """
    import io
    import tokenize

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


def test_no_trade_callback_branch_reintroduces_the_fail_open_shape():
    """A source scan, because the property is 'every call site', not 'this call'.

    CLAUDE.md allows exactly this case — a guard being REACHED at every site is
    a shape no unit test can see. The predicate above is tested by behaviour;
    this asserts nobody hand-rolls the condition again next to it.
    """
    code = "\n".join(_code_only(p.read_text(encoding="utf-8")) for p in handler_sources())
    offenders = [
        (i + 1, ln.strip())
        for i, ln in enumerate(code.splitlines())
        if re.search(r"if\s+expected_uid\s+and\s+not\s+self\._uid_matches", ln)
    ]
    assert not offenders, (
        "a trade callback branch guards ownership with `if expected_uid and "
        "not _uid_matches(...)`, which allows the untagged payload the guard "
        f"exists to deny — use _callback_owner_ok: {offenders}"
    )


@pytest.mark.parametrize("action", ["setlimit", "confirm", "reject"])
def test_each_trade_callback_branch_consults_the_shared_predicate(action):
    """All three must decide the same way, since they drifted once already."""
    code = "\n".join(_code_only(p.read_text(encoding="utf-8")) for p in handler_sources())
    start = code.index(f'data.startswith("{action}:")')
    window = code[start:start + 2000]
    assert "_callback_owner_ok(" in window, (
        f"the `{action}:` branch no longer consults _callback_owner_ok — "
        "the three ownership checks have drifted apart again"
    )
