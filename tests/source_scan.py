"""Helper for tests that assert on SOURCE rather than behaviour.

Source-scanning is the right tool for a narrow class of claim a unit test
cannot reach — that a guard is *reached* at every call site, that a cap is
configurable, that a particular string is not rendered anywhere. It has one
recurring failure mode, which cost five debugging rounds in a single week:

    # never render "Engine live" from stream silence
    assert "Engine live" not in SRC          # <- fails on this very comment

    # an error raised by a recorder must not replace the timeout
    assert block.index("_rec(") < block.index("raise")   # matches "raised"

A comment that names the thing it forbids is indistinguishable from the code
doing it when you scan raw text. So: scan CODE, read prose separately.

`code_only()` BLANKS comments and docstrings in place rather than removing
them, so every remaining character keeps its original offset. That matters:
these tests do substring checks and ordering checks on the result, and a
helper that re-joined tokens would silently break `"foo(a, b)" in block`.
"""

from __future__ import annotations

import io
import tokenize

__all__ = ["code_only"]

#: Token types after which a bare string literal may be a docstring.
#:
#: NL is deliberately ABSENT, and that omission is load-bearing. NEWLINE ends
#: a logical line; NL is the newline *inside* brackets. Treating NL as a
#: statement boundary made every key in a multi-line dict literal look like a
#: docstring, so this helper silently blanked them:
#:
#:     self._last_phase_timeout = {
#:         "phase": what,        ->        : what,
#:
#: Which is worse than the bug it exists to fix: an assertion that a key is
#: PRESENT would fail mysteriously, and one that a key is ABSENT would pass
#: for the wrong reason.
_DOCSTRING_MAY_FOLLOW = frozenset({
    tokenize.INDENT, tokenize.DEDENT, tokenize.NEWLINE,
})

#: Brackets that opened. A docstring is a statement, and a statement cannot
#: occur inside a collection literal or an argument list — so depth > 0 means
#: any string is a value, whatever preceded it.
_OPEN, _CLOSE = "([{", ")]}"


def code_only(text: str) -> str:
    """Python source with comments and docstrings replaced by blanks.

    Newlines inside a blanked span are preserved, so line numbers and every
    unblanked offset are unchanged.

    Falls back to the original text if the source does not tokenize — a
    syntax error is the caller's problem to report, not this helper's to hide
    behind an empty string.
    """
    lines = text.splitlines(keepends=True)
    spans: list[tuple[int, int, int, int]] = []
    prev, depth = None, 0
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            drop = tok.type == tokenize.COMMENT or (
                tok.type == tokenize.STRING
                and depth == 0
                and (prev is None or prev in _DOCSTRING_MAY_FOLLOW))
            if drop:
                spans.append((*tok.start, *tok.end))
            if tok.type == tokenize.OP:
                if tok.string in _OPEN:
                    depth += 1
                elif tok.string in _CLOSE:
                    depth = max(0, depth - 1)
            if tok.type != tokenize.COMMENT:
                prev = tok.type
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return text

    for srow, scol, erow, ecol in spans:
        for row in range(srow, erow + 1):
            i = row - 1
            if i >= len(lines):
                continue
            line = lines[i]
            start = scol if row == srow else 0
            end = ecol if row == erow else len(line)
            keep_nl = line[end:] if row == erow else ""
            body = line[start:end]
            # Blank the span but keep any newline it spanned, so the shape of
            # the file — and therefore every later offset — is untouched.
            lines[i] = (line[:start] + "".join(
                "\n" if ch == "\n" else " " for ch in body) + keep_nl)
    return "".join(lines)
