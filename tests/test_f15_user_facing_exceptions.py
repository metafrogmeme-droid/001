"""F-15 held on ten handlers and not on the twelve beside them.

`_safe_exc_text` exists because ten command handlers were sending
`html.escape(str(exc))` straight into a reply. Its own docstring says why
that is not enough:

    escaping stops MARKUP injection and does nothing whatever about a
    credential. A ccxt error carries the request URL, an LLM provider error
    can echo the Authorization header, and a KeyError on config names the key
    it could not find.

The helper was written, wired into ten sites, and TWELVE others kept sending
the raw exception — including /balance, /closeall, the account overview and
BOTH trade-execution failure paths. Every one of those is a ccxt call, which
is the exact class the docstring names.

    THE HELPER WAS NEVER THE FIX. APPLYING IT EVERYWHERE WAS, AND "EVERYWHERE"
    WAS NEVER ENUMERATED.

Same shape as §4 on the public routes: a hard line held by convention at the
sites someone happened to think of, with nothing checking the set. #1037
closed that one by enumerating the surface; this closes F-15 the same way.

WHY THIS MATCHES SENDS AND NOT LOGS

system_log lines are not user-facing and are redacted at the logger's own
chokepoint (`_redact_string` in bot/utils/logger.py). Flagging them here
would produce noise that gets silenced by exemptions until the check protects
nothing — the failure mode this file is written to avoid.
"""
from __future__ import annotations

import re

from tests.source_scan import code_only

HANDLER = "bot/skills/telegram_handler.py"

#: An exception interpolated into an f-string. `{exc}`, `{e}`, `{str(exc)}`,
#: and the escape-only form the docstring calls out by name.
RAW_EXC = re.compile(
    r"\{\s*(?:str\(\s*)?e(?:xc)?\s*\)?\s*\}"
    r"|html\.escape\(\s*str\(\s*e(?:xc)?\s*\)\s*\)"
)


def _src() -> str:
    return code_only(open(HANDLER, encoding="utf-8").read())


def _send_calls(src: str) -> list[tuple[int, str]]:
    """(line number, text) for every `self._send(...)` call.

    Spans several physical lines: the offending sites are mostly written as
    `await self._send(update,\\n    f"...{exc}")`, so a single-line scan finds
    none of them and reports a clean file. Read to the balanced paren.
    """
    out = []
    for m in re.finditer(r"self\._send\(", src):
        i = m.end()
        depth, j = 1, i
        while j < len(src) and depth:
            if src[j] == "(":
                depth += 1
            elif src[j] == ")":
                depth -= 1
            j += 1
        out.append((src.count("\n", 0, m.start()) + 1, src[m.start():j]))
    return out


class TestNoRawExceptionReachesAUser:
    def test_the_scanner_finds_the_send_calls(self):
        # A check that inspects nothing passes everything.
        calls = _send_calls(_src())
        assert len(calls) > 50, f"only {len(calls)} _send calls found"

    def test_it_spans_multiple_lines(self):
        # The offending sites were mostly `_send(update,\n  f"...{exc}")`.
        # A single-line regex reports the file clean.
        assert any("\n" in c for _, c in _send_calls(_src()))

    def test_no_send_interpolates_a_raw_exception(self):
        offenders = [ln for ln, call in _send_calls(_src())
                     if RAW_EXC.search(call)]
        assert offenders == [], (
            "F-15: these _send calls put an unredacted exception in front of "
            f"a user — use _safe_exc_text(exc). Lines: {offenders}"
        )

    def test_the_helper_is_actually_used(self):
        # The complement: the check above also passes on a file that sends no
        # exception detail at all, which would be a different regression —
        # the operator losing every diagnostic.
        assert _src().count("_safe_exc_text(") >= 20

    def test_the_pattern_can_fail(self):
        # Before any other property matters.
        for bad in ('self._send(update, f"x: {exc}")',
                    'self._send(update, f"x: {e}")',
                    'self._send(update, f"x: {str(exc)}")',
                    'self._send(update, f"x: {html.escape(str(exc))}")'):
            assert RAW_EXC.search(bad), bad

    def test_the_pattern_does_not_flag_the_fix(self):
        for good in ('self._send(update, f"x: {_safe_exc_text(exc)}")',
                     'self._send(update, f"x: {_safe_exc_text(e)}")'):
            assert not RAW_EXC.search(good), good

    def test_it_does_not_flag_ordinary_interpolation(self):
        # `{entry}`, `{equity}` and friends must not trip it, or the check
        # gets silenced by exemptions until it protects nothing.
        for good in ('self._send(update, f"entry {entry} exit {exit_price}")',
                     'self._send(update, f"{symbol} at {price}")'):
            assert not RAW_EXC.search(good), good


class TestTheRiskiestPathsSpecifically:
    """Named because these are ccxt calls, the class the docstring calls out.

    A ccxt error carries the request URL; these are the handlers most likely
    to raise one in front of a user.
    """

    def test_balance_close_all_and_trade_execution_are_redacted(self):
        src = _src()
        for phrase in ("Balance fetch failed",
                       "Close all failed",
                       "Trade execution failed",
                       "Account overview failed"):
            i = src.index(phrase)
            window = src[i:i + 200]
            assert "_safe_exc_text(" in window, f"{phrase} sends a raw error"

    def test_both_trade_execution_paths_are_covered(self):
        # There are two identical sites. Fixing one and leaving its twin is
        # the exact shape this session kept finding.
        src = _src()
        assert src.count("Trade execution failed") == 2
        for m in re.finditer("Trade execution failed", src):
            assert "_safe_exc_text(" in src[m.start():m.start() + 200]


class TestTheHelperStillScrubsWhatItClaims:
    def test_it_redacts_a_key_and_keeps_the_diagnostic(self):
        from bot.skills.telegram_handler import _safe_exc_text
        out = _safe_exc_text(RuntimeError(
            "bitget GET https://api.bitget.com/v2/acct?apiKey=SECRET123 failed"))
        assert "SECRET123" not in out
        assert "api.bitget.com" in out, "which venue is the diagnostic"

    def test_it_escapes_markup(self):
        from bot.skills.telegram_handler import _safe_exc_text
        assert "<b>" not in _safe_exc_text(RuntimeError("<b>x</b>"))

    def test_it_never_raises(self):
        from bot.skills.telegram_handler import _safe_exc_text

        class Hostile(Exception):
            def __str__(self):
                raise RuntimeError("nope")

        assert _safe_exc_text(Hostile()) == ""
