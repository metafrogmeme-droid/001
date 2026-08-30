"""Live-but-unreadable equity must not be evaluated as if it were paper.

`_evaluate_locked` takes `live_equity: Optional[float] = None`, and three gates
branch on `if live_equity is not None and live_equity > 0`:

    :1033  position SIZING          else -> sizing_equity = state.equity_usd
    :1413  DAILY-LOSS breaker       else -> _daily_pnl   = state.daily_pnl
    :1475  DRAWDOWN breaker         else -> _cur_dd      = state.current_drawdown_pct

One parameter, two meanings. `None` says "paper mode" and it also says "live,
but the balance read failed" — and the caller cannot tell the gate which,
because `bot/core/engine.py:5325` collapses them itself::

    live_eq = self._live_balance_cache.get("total", 0.0) if (CONFIG.is_live() and self._live_balance_cache) else None

So on a live account whose equity read failed — a timeout, a venue error, a rate
limit, or a cache that has not filled yet — all three gates silently measure the
PAPER book. The comment above the daily-loss branch states exactly why that is
worthless:

    the paper snapshot's daily_pnl is ~0 because live fills never touch the
    paper portfolio

~0 daily PnL over paper equity is ~0% loss, and the paper drawdown is ~0%, so
neither breaker trips no matter what the real account is doing. Meanwhile the
sizing branch keeps sizing against paper equity, which its own comment says the
live fix exists to prevent: "sizing $2K positions against $10K paper when the
real account has $50".

The direction matters. Failing towards *not halting* and *sizing larger* is the
direction that spends money, on the control this file describes as deciding how
much real money is lost before the bot stops.

The fix is a third case, not a third patch: the caller says whether this is a
live evaluation, and a live evaluation with no readable equity is REFUSED —
mirroring the fail-closed `Portfolio state unavailable` rejection already at the
top of the same function. An unmeasurable drawdown is not a passing one.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine, RiskVerdict
from bot.utils.models import Direction, TradeIdea


def _engine(balance: float = 10_000.0) -> RiskEngine:
    state = os.path.join(tempfile.mkdtemp(prefix="rc-liveq-"), "risk.json")
    return RiskEngine(PortfolioTracker(initial_balance=balance), state_file=state)


def _idea() -> TradeIdea:
    return TradeIdea(id="TI-liveq", asset="BTC/USDT", direction=Direction.LONG,
                     entry_price=100.0, stop_loss=95.0, take_profit=110.0,
                     confidence=0.9, risk_reward_ratio=2.0, reasoning="t")


@pytest.mark.parametrize("unreadable", [None, 0.0, -1.0])
def test_live_evaluation_with_unreadable_equity_is_refused(unreadable):
    """The whole point. Live + no equity must not fall through to paper.

    `0.0` and a negative are included because the branch tests
    `live_equity > 0`, so a venue that answers with a zero balance, or a cache
    holding `.get("total", 0.0)` on a payload with no total, lands in the same
    else as `None`.
    """
    eng = _engine()
    check = eng.evaluate(_idea(), live_equity=unreadable, live_mode=True)

    assert check.verdict == RiskVerdict.REJECTED, (
        f"a LIVE evaluation with live_equity={unreadable!r} was not refused; "
        "the daily-loss and drawdown breakers just measured the paper book, "
        "which carries ~0 PnL by construction, and sizing used paper equity"
    )
    assert any("LIVE_EQUITY" in c for c in check.checks_failed), (
        f"rejected, but not for this reason: {check.checks_failed}"
    )


def test_the_refusal_names_the_cause_without_inventing_a_number():
    """The operator has to be able to tell this from a real risk rejection.

    'unreadable' and 'you are down 6%' are different events and must not read
    the same, or the next person tunes a limit that was never the problem.
    """
    eng = _engine()
    check = eng.evaluate(_idea(), live_equity=None, live_mode=True)

    reason = (check.reason or "").lower()
    assert "equity" in reason, f"reason does not name the cause: {check.reason!r}"
    # It must not manufacture a measurement it does not have.
    assert "0.0%" not in reason and "0%" not in reason, (
        f"the refusal reports a percentage it could not measure: {check.reason!r}"
    )


def test_live_evaluation_with_readable_equity_is_unaffected():
    """The control. A readable live equity must gate exactly as before."""
    eng = _engine()
    check = eng.evaluate(_idea(), live_equity=5_000.0, live_mode=True)
    assert check.verdict != RiskVerdict.REJECTED or not any(
        "LIVE_EQUITY" in c for c in check.checks_failed), (
        "a readable live equity was refused by the unreadable-equity guard"
    )


def test_paper_evaluation_still_uses_the_paper_book():
    """The other control, and the one that would break real users.

    Paper mode passes `live_equity=None` legitimately — that is not a failed
    read, it is the absence of a live account. It must keep evaluating against
    the paper portfolio, unchanged.
    """
    eng = _engine()
    check = eng.evaluate(_idea(), live_equity=None, live_mode=False)
    assert not any("LIVE_EQUITY" in c for c in (check.checks_failed or [])), (
        "paper-mode evaluation was refused for want of live equity"
    )


def test_the_default_is_the_old_behaviour():
    """`live_mode` defaults False, so every existing caller is byte-identical.

    The guard is opt-in at the call site because only the caller knows which of
    the two situations `None` means. A default of True would refuse every paper
    evaluation in the suite and in production.
    """
    eng = _engine()
    check = eng.evaluate(_idea(), live_equity=None)
    assert not any("LIVE_EQUITY" in c for c in (check.checks_failed or []))


# ── The guard has to be REACHED, not merely present ────────────────────────
#
# The guard defaults `live_mode=False`, so it does nothing until a caller opts
# in. A guard nothing reaches is not a guard — this repository's own words, and
# the reason scripts/guard_lint.py exists. The two engine call sites that pass
# `live_equity` are the ones that must pass `live_mode` too, and a unit test
# cannot see that: it is a property of the call sites, not of a call.

def _engine_source() -> str:
    """engine.py with comments and docstrings blanked.

    The call sites are explained in prose that names the very parameter being
    asserted, so an unstripped scan matches the explanation.
    """
    import io
    import tokenize
    from pathlib import Path

    text = (Path(__file__).resolve().parent.parent / "bot" / "core" / "engine.py").read_text(encoding="utf-8")
    doomed, prev = [], None
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                doomed.append((tok.start, tok.end))
                continue
            if tok.type == tokenize.STRING and prev in (
                    None, tokenize.NEWLINE, tokenize.NL, tokenize.INDENT, tokenize.DEDENT):
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


def test_every_risk_evaluate_that_passes_live_equity_also_passes_live_mode():
    import re

    code = _engine_source()
    # Each `.evaluate(` call, flattened, that mentions live_equity.
    calls = re.findall(r"\.evaluate\((?:[^()]|\([^()]*\))*\)", code, re.S)
    live_calls = [" ".join(c.split()) for c in calls if "live_equity=" in c]
    assert live_calls, "no risk .evaluate(live_equity=...) call sites found — has the API moved?"
    missing = [c for c in live_calls if "live_mode=" not in c]
    assert not missing, (
        "a risk evaluation passes live_equity but not live_mode, so an "
        "unreadable live equity there still falls through to the paper book:\n  "
        + "\n  ".join(c[:160] for c in missing)
    )
