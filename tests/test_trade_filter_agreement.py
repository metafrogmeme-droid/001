"""Telegram and the website must count the same trades.

Production, 2026-07-28. The same account, the same day:

    Telegram /portfolio   -10.74   95 trades   57%
    Website Portfolio     +10.19   50 trades   46%

Not sync lag — opposite signs. The two were measuring different SETS and both
called the result "Net PnL". Telegram excluded orphan-adopted and
diagnostic-injected positions and closes that were never trades (cancelled,
expired, price-drift, rejected); the website summed every CLOSED row it held.

The website could not have filtered even if it wanted to: its schema stores
neither `close_reason` nor `trade_id`. So the rule is applied at the SOURCE —
the sync sends only countable trades, and the website's plain SUM is right by
construction, with no second implementation to drift.

The rule had also been written out inline at four call sites in one file,
which is how definitions diverge in the first place. It has one home now.
"""

import re
from pathlib import Path

from bot.utils.trade_filter import (
    NON_TRADE_CLOSE_REASONS,
    ORPHAN_PREFIXES,
    adopted,
    countable,
    is_adopted,
    is_countable,
)


class _T:
    def __init__(self, trade_id="T-1", close_reason="take_profit"):
        self.trade_id = trade_id
        self.close_reason = close_reason


# ── the rule ──────────────────────────────────────────────────────────────

def test_a_real_trade_counts():
    assert is_countable(_T("T-1", "take_profit"))
    assert is_countable(_T("T-2", "stop_loss"))


def test_adopted_and_injected_positions_do_not_count():
    """They are not decisions the engine made, so they are not its record."""
    for prefix in ORPHAN_PREFIXES:
        t = _T(f"{prefix}-42", "take_profit")
        assert is_adopted(t)
        assert not is_countable(t)


def test_closes_where_no_position_existed_do_not_count():
    """An order that never became a position has no P&L and no outcome —
    counting it dilutes the win rate with events that were never trades."""
    for reason in NON_TRADE_CLOSE_REASONS:
        assert not is_countable(_T("T-9", reason))


def test_the_reason_match_is_case_and_space_insensitive():
    for reason in ("CANCELLED", " Expired ", "Price_Drift"):
        assert not is_countable(_T("T-9", reason))


def test_an_unreadable_reason_is_COUNTED():
    """Fail-safe toward counting. Under-reporting a real loss is worse than
    including an oddity, and a missing reason is not evidence of nothing."""
    assert is_countable(_T("T-9", ""))
    assert is_countable(_T("T-9", None))

    class _Bare:
        pass
    assert is_countable(_Bare())


def test_the_two_subsets_partition_cleanly():
    rows = [_T("T-1", "tp"), _T("TI-adopted-1", "tp"), _T("T-2", "cancelled"),
            _T("TI-injected-3", "sl")]
    assert len(countable(rows)) == 1
    assert len(adopted(rows)) == 2          # cancelled is excluded but not adopted
    # nothing is in both
    assert not set(id(x) for x in countable(rows)) & set(id(x) for x in adopted(rows))


def test_empty_and_none_are_safe():
    assert countable([]) == []
    assert countable(None) == []
    assert adopted(None) == []


# ── both surfaces use it ──────────────────────────────────────────────────

def test_the_sync_sends_only_countable_trades():
    """The website's schema has no close_reason and no trade_id, so this is
    the ONLY place the rule can be applied for it."""
    src = Path("bot/utils/website_sync.py").read_text(encoding="utf-8")
    assert "from bot.utils.trade_filter import countable" in src
    assert re.search(r"for t in _countable\(closed_trades\)", src), \
        "the closed-trade list sent to the website must be filtered"


def test_telegram_reads_the_same_definition_rather_than_its_own_copy():
    src = Path("bot/skills/telegram_handler.py").read_text(encoding="utf-8")
    assert "from bot.utils.trade_filter import ORPHAN_PREFIXES" in src
    assert "from bot.utils.trade_filter import NON_TRADE_CLOSE_REASONS" in src
    # the inline literals must be gone — a second copy is how they diverged
    assert '_ORPHAN_PREFIXES = ("TI-adopted", "TI-injected")' not in src
    assert '{"canceled", "cancelled", "expired", "price_drift", "rejected"}' not in src


def test_the_website_schema_genuinely_cannot_filter_itself():
    """Pins WHY the rule belongs at the source: if these columns are ever
    added, the constraint changes and this decision should be revisited."""
    db = Path("app/db.js").read_text(encoding="utf-8")
    trades = re.search(r"CREATE TABLE IF NOT EXISTS trades \((.*?)\n      \)", db, re.S)
    assert trades, "the trades table must exist"
    body = trades.group(1)
    assert "close_reason" not in body
    assert "trade_id" not in body
