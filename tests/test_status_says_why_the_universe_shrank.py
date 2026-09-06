"""/status names the classes the sweep skipped, or says nothing.

The sweep drops stock perps while Wall Street is shut (see
test_sweep_skips_closed_reference_sessions.py). Left unreported, the operator
sees a smaller universe and no reason: "4 of 60" reads as a thinner market,
not a closed one, and the very card that diagnosed the overnight timeouts
would start hiding the fix. The line is a pure renderer, wired into
_cmd_status beside the analyze-budget line, and OMITTED -- never invented --
when the scanner has no record or the record says nothing.
"""
from __future__ import annotations

import io
import tokenize
from pathlib import Path

from bot.formatters.rich_cards import session_skip_line

ROOT = Path(__file__).resolve().parent.parent


def test_a_recorded_drop_is_named_with_its_count_and_reason():
    out = session_skip_line({"Stock": 3})
    assert "Stock ×3" in out
    assert "US session closed" in out


def test_two_classes_are_both_named():
    out = session_skip_line({"Stock": 3, "ETF": 1})
    assert "Stock ×3" in out and "ETF ×1" in out


def test_nothing_dropped_says_nothing():
    """No record (no sweep yet), an empty record, or a zero: all silent.
    A line saying '0 skipped' would be a claim about the market."""
    assert session_skip_line(None) == ""
    assert session_skip_line({}) == ""
    assert session_skip_line({"Stock": 0}) == ""


def test_a_count_that_is_not_a_number_is_not_printed():
    assert session_skip_line({"Stock": "many"}) == ""
    assert session_skip_line({"Stock": None, "ETF": 2}) == \
        "⏸ ETF ×2 skipped this sweep — US session closed (resumes at the open)"


def test_it_renders_in_the_other_language_too():
    out = session_skip_line({"Stock": 3}, "zh")
    assert out and "Stock ×3" in out
    assert "skipped" not in out


def _code_only(path: Path) -> str:
    """Source with comments stripped -- a comment quoting the call form must
    not stand in for the call."""
    src = path.read_text(encoding="utf-8")
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type != tokenize.COMMENT:
            out.append(tok.string)
    return " ".join(out)


def test_status_calls_the_renderer_with_the_scanners_record():
    """Wiring: the seam is only worth anything if /status reaches it, and it
    must be fed the scanner's own record, not a copy that could go stale."""
    from tests.source_scan import handler_sources
    # Every file the handler class is made of: /status is leaving for the
    # start-here mixin, and a scan of one file reads the move as the
    # renderer losing its caller.
    code = "\n".join(_code_only(p) for p in handler_sources())
    i = code.find("def _cmd_status")
    assert i > 0
    body = code[i:i + 40_000]
    j = body.find("session_skip_line (")
    assert j > 0, "/status does not call session_skip_line"
    call = body[j:j + 300]
    assert '"_session_dropped"' in call, "the call must read the scanner's _session_dropped"
    assert body.find("analyze_budget_line (") < j, \
        "keep it beside the analyze-budget line, where the universe size is discussed"
