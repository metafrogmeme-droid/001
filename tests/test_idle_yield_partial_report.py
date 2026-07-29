"""A yield report missing its largest row must not look complete.

Flagged in #978 and deliberately left alone there, on the grounds that the
right fix was not determinable from the code. That was too pessimistic —
reading `build_report` settles it:

    if futures_free_usdt > 0:
        idle["USDT"] = (futures_free_usdt, "futures free")

0.0 does not render "$0.00". It OMITS the row, which is the omit-never-invent
pattern correctly applied — and complete and correct in PAPER mode, where
there is no live futures margin to report at all.

But `_engine_free_usdt()` also returned 0.0 when the LIVE balance cache was
empty or unreadable, and there the omission is not correct. Free futures
margin is usually the largest idle balance the operator has; dropping it
silently produces a report of "your idle capital" that is missing most of it,
with nothing on screen to say so. The operator then reads a complete-looking
answer and acts on it — including through /stake, which moves money.

So: 0.0 keeps meaning "nothing there", None now means "could not read", and a
report built from None says which leg it is missing.
"""

from unittest.mock import patch

import pytest

from bot.core.yield_radar import YieldReport, build_report


# ── the report ────────────────────────────────────────────────────────────

def test_incomplete_is_separate_from_error():
    """`error` means nothing was produced. `incomplete` means something was,
    and it is missing a leg — the more dangerous of the two, because rows on
    screen read as an answer."""
    r = YieldReport()
    assert r.error == "" and r.incomplete == ""
    assert "incomplete" in YieldReport.__dataclass_fields__


def _catalog(*_a, **_k):
    return {"USDT": {"flexible": 5.0, "flexible_id": "p1", "fixed": None,
                     "fixed_terms": []}}


@pytest.fixture
def _no_spot():
    with patch("bot.core.yield_radar.fetch_savings_catalog", _catalog), \
         patch("bot.core.yield_radar.fetch_spot_idle", lambda *_a, **_k: {}):
        yield


def test_an_unreadable_margin_marks_the_report_incomplete(_no_spot):
    rep = build_report(object(), None)
    assert rep.incomplete, "a missing leg must be stated"
    assert "could not be read" in rep.incomplete
    assert not rep.error, "rows were still produced — this is not a hard failure"


def test_a_genuine_zero_does_not(_no_spot):
    """PAPER mode has no live futures margin. Omitting the row is the whole
    truth, and a warning there would be noise that teaches people to ignore
    warnings."""
    rep = build_report(object(), 0.0)
    assert rep.incomplete == ""


def test_a_real_balance_still_produces_the_row(_no_spot):
    rep = build_report(object(), 5000.0)
    assert rep.incomplete == ""
    assert any(r.coin == "USDT" and r.source.startswith("futures") for r in rep.rows)


def test_an_unreadable_margin_never_invents_the_row(_no_spot):
    rep = build_report(object(), None)
    assert not any("futures" in r.source for r in rep.rows), (
        "omit, never invent — the alternative to a missing number is not a "
        "guessed one")


def test_the_spot_legs_are_unaffected():
    """Only the futures leg is unknown; the rest of the report is real and
    must still be shown."""
    with patch("bot.core.yield_radar.fetch_savings_catalog", _catalog), \
         patch("bot.core.yield_radar.fetch_spot_idle",
               lambda *_a, **_k: {"USDT": 900.0}):
        rep = build_report(object(), None)
    assert rep.incomplete
    assert any(r.coin == "USDT" for r in rep.rows), "spot idle is still known"
    assert "Spot holdings are complete" in rep.incomplete


# ── the source of the value ───────────────────────────────────────────────

def _handler_src() -> str:
    from pathlib import Path
    return Path("bot/skills/telegram_handler.py").read_text(encoding="utf-8")


def test_paper_mode_reports_zero_not_unknown():
    src = _handler_src()
    block = src[src.index("def _engine_free_usdt"):]
    block = block[:block.index("async def _cmd_weblive")]
    assert "if not CONFIG.is_live():" in block and "return 0.0" in block, (
        "paper mode genuinely has no live futures margin; calling that "
        "unknown would cry wolf on every paper report")


def test_a_live_empty_cache_is_unknown_not_zero():
    src = _handler_src()
    block = src[src.index("def _engine_free_usdt"):]
    block = block[:block.index("async def _cmd_weblive")]
    assert "if not cache:" in block and "return None" in block
    assert "except Exception:\n            return None" in block, (
        "an exception reading the cache is also 'we do not know'")


def test_a_missing_free_key_is_unknown_too():
    src = _handler_src()
    block = src[src.index("def _engine_free_usdt"):]
    block = block[:block.index("async def _cmd_weblive")]
    assert 'free = cache.get("free")' in block
    assert "return None if free is None else float(free or 0)" in block, (
        "a cache present but without a 'free' key is not a balance of zero")


# ── it reaches the operator ───────────────────────────────────────────────

def test_the_stake_plan_states_the_missing_leg():
    """/stake is a money path. A plan built on a partial balance must say so
    ABOVE the plan, not in a log."""
    src = _handler_src()
    i = src.index("⚡ <b>Stake plan")
    block = src[i - 1800:i + 400]
    assert "_incomplete" in block
    assert "lines.append(f\"⚠️ <i>{html.escape(_incomplete)}</i>\")" in src


def test_nothing_stakeable_is_qualified_when_the_balance_was_partial():
    """"Nothing stakeable" is a claim about the balance. Only make it when the
    balance was fully read."""
    src = _handler_src()
    i = src.index("🟡 Nothing stakeable right now")
    block = src[i - 400:i + 500]
    assert "_why" in block, (
        "an unreadable margin could be exactly the balance that WOULD have "
        "been stakeable")


def test_the_engine_cache_is_only_populated_in_live_mode():
    """The premise the paper-mode branch rests on, checked rather than
    assumed."""
    from pathlib import Path
    engine = Path("bot/core/engine.py").read_text(encoding="utf-8")
    block = engine[engine.index("async def get_live_equity"):]
    block = block[:block.index("self._live_balance_cache = bal") + 200]
    assert "if not CONFIG.is_live():" in block and "return None" in block


def test_the_signature_admits_none():
    import inspect
    sig = inspect.signature(build_report)
    assert "Optional" in str(sig.parameters["futures_free_usdt"].annotation), (
        "the type must say what the value can be, or the next caller passes "
        "0.0 for unknown again")


def test_the_idleyield_report_states_it_too():
    """Both money surfaces, not just /stake. /idleyield is where the operator
    reads "here is your idle capital" in the first place."""
    src = _handler_src()
    i = src.index("💤→💸 Idle-Yield Optimizer")
    assert "_incomplete" in src[i - 900:i + 400]
    j = src.index("🟡 No idle assets above the dust floor")
    assert "_incomplete" in src[j:j + 300], (
        '"no idle assets" is a claim about the whole balance')
