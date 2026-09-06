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

__all__ = ["code_only", "handler_sources", "js_code_only", "segment_reader"]


def js_code_only(src: str) -> str:
    """The same rule for JavaScript: drop `//` and `/* */`, keep strings intact.

    Python tests read `app/` source for cross-language claims — that the
    website refuses a submission it cannot protect, that a route selects the
    columns the bot indexes by name — and those routes describe themselves in
    prose above the code (`Body: { acks: [{ user_id, action, ok }] }`), so an
    unstripped scan finds the description and calls it the implementation.

    Offsets are NOT preserved here, unlike `code_only`: a `//` comment is
    removed rather than blanked. Index comparisons must therefore be computed
    on the RESULT, never against the original source.
    """
    out = []
    i, n = 0, len(src)
    quote = None
    while i < n:
        c = src[i]
        if quote:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(src[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "'\"`":
            quote = c
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            out.append(" ")
            continue
        out.append(c)
        i += 1
    return "".join(out)


def handler_sources() -> list:
    """Every file that contributes methods to TelegramHandler, handler first.

    The handler is being split into mixins one command group at a time, and
    the derivation tests — which permission each `@guard` names, which
    commands gate on `_is_admin`, whether every `self.` call resolves — read
    SOURCE FILES. A scan pointed at telegram_handler.py alone stops seeing a
    command the moment it moves, and it stops silently: no decorator found
    means no permission derived means nothing to check. Derived from the
    class's MRO rather than listed, so the next slice is covered before
    anyone remembers to add it. Paths, not text: each caller keeps its own
    parse and its own cache.
    """
    import inspect
    from pathlib import Path

    from bot.skills.telegram_handler import TelegramHandler
    out: list = []
    for cls in TelegramHandler.__mro__:
        if cls is object:
            continue
        path = Path(inspect.getsourcefile(cls) or "").resolve()
        if path not in out:
            out.append(path)
    return out


def segment_reader(source: str):
    """Return ``seg(node)`` — a node's source text, splitting `source` ONCE.

    Byte-identical to ``ast.get_source_segment(source, node)``; the only thing
    that changes is how many times the source is split.

    ``ast.get_source_segment`` calls ``_splitlines_no_ff(source)`` on EVERY
    call, so scanning a file node-by-node re-splits the whole file per node.
    That is quadratic and it is not academic — measured 2026-08-21 on
    ``bot/skills/telegram_handler.py``, 13,575 lines and 260 function nodes:

        ast.get_source_segment over every node : 29.60s
        split once, slice per node             :  0.004s   (7,184x)

    Two tests were spending that 30s each and dying at the 60s pytest-timeout
    under full-suite load, which the CI gate then filed as "passes alone
    (flaky/order-dependent)". True, and a green build over two real timeouts.

    The body below is CPython's ``get_source_segment`` with the split hoisted
    out of the loop — including the ``.encode()`` round-trips, because
    ``col_offset`` is a UTF-8 BYTE offset and this file is full of emoji. Naive
    character slicing silently cuts the wrong place on any line with one.
    ``padded=`` is not supported; nothing here uses it.

    `tests/test_source_segment_reader.py` asserts equivalence against the
    stdlib for every node of several real files, so a CPython change in either
    direction shows up as a failure rather than as a subtly different segment.
    """
    lines = _splitlines_no_ff(source)

    def seg(node):
        try:
            if node.end_lineno is None or node.end_col_offset is None:
                return None
            lineno = node.lineno - 1
            end_lineno = node.end_lineno - 1
            col_offset = node.col_offset
            end_col_offset = node.end_col_offset
        except AttributeError:
            return None

        if end_lineno == lineno:
            return lines[lineno].encode()[col_offset:end_col_offset].decode()

        first = lines[lineno].encode()[col_offset:].decode()
        last = lines[end_lineno].encode()[:end_col_offset].decode()
        middle = lines[lineno + 1:end_lineno]
        return "".join([first, *middle, last])

    return seg


def _splitlines_no_ff(source: str) -> list[str]:
    """Split like the Python parser does, not like ``str.splitlines()``.

    Copied from ``ast`` rather than imported: it is private there, and
    ``str.splitlines()`` is NOT a substitute — it also breaks on form feed,
    vertical tab and \\x1c-\\x1e, which the parser treats as ordinary
    characters. Using it would shift every line index after the first such
    character and hand back the wrong function body.
    """
    idx = 0
    lines = []
    next_line = ''
    while idx < len(source):
        c = source[idx]
        next_line += c
        idx += 1
        # Keep \r\n together
        if c == '\r' and idx < len(source) and source[idx] == '\n':
            next_line += '\n'
            idx += 1
        if c in '\r\n':
            lines.append(next_line)
            next_line = ''
    if next_line:
        lines.append(next_line)
    return lines

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
