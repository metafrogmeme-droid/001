"""Every card that offers an idea with a Confirm button carries the same context.

The analyze card, the pushed signal caption and `/latest_signal` carried the
person's own live record in the idea's class and whether the turn is confirmed.
Five other cards offered a Confirm button without them: `/scan`'s rows, `/scan
SYM`, a hand-typed `/trade` ticket, a staged chat draft and the drift re-offer.
A stock tap from any of those was as uninformed as before.

One reading, `idea_context_lines`, is what every card takes. It is driven here
on real closed records; each card is then driven with that reading replaced by
a recorder, which says what the card was handed and proves the card prints what
comes back, escaped, and only where it offers the idea.
"""
from __future__ import annotations

import ast
import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

import pytest

from bot.core.entry_timing import TURN_NOT_CONFIRMED
from bot.core.live_executor import LiveExecutor, LivePosition
from bot.formatters import idea_context
from bot.formatters.idea_context import class_lines_for, idea_context_lines
from bot.utils.models import Direction, TradeIdea

OP = "1001"
STOCK, ETF, CRYPTO = "TSLA/USDT:USDT", "QQQ/USDT:USDT", "BTC/USDT:USDT"


def _close(symbol, pnl, reason="TP HIT", n=[0]):
    n[0] += 1
    return LivePosition(
        trade_id=f"C{n[0]}", symbol=symbol, direction="LONG", entry_price=100.0,
        quantity=1.0, cost_usd=20.0, stop_loss=97.0, take_profit=106.0, leverage=5,
        status="closed", opened_at=datetime.now(UTC) - timedelta(hours=3),
        close_reason=reason, pnl_usd=pnl)


def _engine(tmp_path, closes, turns=None):
    ex = LiveExecutor(state_dir=str(tmp_path))
    ex._closed_trades = list(closes)
    return NS(live_view=lambda uid: {"scope": "operator", "executor": ex},
              _is_operator_user=lambda uid: str(uid) == OP,
              _pending_turn=dict(turns or {}))


def _idea(asset=STOCK):
    return TradeIdea(asset=asset, direction=Direction.LONG, entry_price=100.0,
                     stop_loss=97.0, take_profit=106.0, confidence=0.7,
                     reasoning="x", source="unknown")


# ── the one reading ─────────────────────────────────────────────────────────

def test_it_is_the_class_record_then_the_timing(tmp_path):
    idea = _idea()
    closes = [_close(STOCK, 2.0) for _ in range(3)] + [_close(STOCK, -1.0, "SL HIT") for _ in range(9)]
    eng = _engine(tmp_path, closes, {idea.id: (TURN_NOT_CONFIRMED, "awaiting bullish trigger bar", "1h")})
    lines = idea_context_lines(eng, OP, idea)
    assert lines == [
        "\U0001f4c8 Stock on your live book: 3 of 12 won · PF 0.67",
        "⏱ Entry timing (1h, closed bars): turn not confirmed yet (awaiting bullish trigger bar)",
    ]
    # Somebody else: the operator's record is not theirs; the timing is the idea's.
    assert idea_context_lines(eng, "2002", idea) == lines[1:]
    # An idea never analysed, for nobody: nothing to say.
    assert idea_context_lines(eng, None, _idea()) == []


def test_a_failing_read_is_an_empty_list_not_a_failed_card():
    assert idea_context_lines(NS(), OP, None) == []


def test_a_scan_gets_one_line_per_class_in_first_seen_order(tmp_path):
    closes = ([_close(STOCK, 2.0) for _ in range(10)]
              + [_close(CRYPTO, -1.0, "SL HIT") for _ in range(10)])
    eng = _engine(tmp_path, closes)
    lines = class_lines_for(eng, OP, [CRYPTO, STOCK, "NVDA/USDT:USDT", "ETH/USDT:USDT", ETF])
    # Crypto first (seen first), one Stock line for two stocks, ETF silent.
    assert lines == ["\U0001f4b0 Crypto on your live book: 0 of 10 won · PF 0.00",
                     "\U0001f4c8 Stock on your live book: 10 of 10 won · PF —"]
    # ETF has no record and says nothing; nobody gets nothing.
    assert class_lines_for(eng, None, [STOCK]) == []


# ── each card, through a recorder ───────────────────────────────────────────

class _Recorder:
    LINE = "CTX <b>line</b>"

    def __init__(self):
        self.calls = []

    def __call__(self, engine, user_id, idea):
        self.calls.append((str(user_id), getattr(idea, "id", None)))
        return [self.LINE]


ESCAPED = "CTX &lt;b&gt;line&lt;/b&gt;"


def test_scan_sym_carries_it_where_it_offers_the_idea(monkeypatch):
    from tests.test_a_scan_button_places_what_the_card_shows import UID, _drive_single

    rec = _Recorder()
    monkeypatch.setattr(idea_context, "idea_context_lines", rec)
    idea = TradeIdea(asset="SOL/USDT", direction=Direction.SHORT, entry_price=122.40,
                     stop_loss=123.30, take_profit=120.60, confidence=0.66,
                     reasoning="[RULE_ENGINE|RANGE|x|intraday|C=0.40] fade",
                     order_type="limit")
    _engine_, text, kb = _drive_single(monkeypatch, idea)
    assert kb is not None and ESCAPED in text
    assert rec.calls == [(UID, idea.id)]
    # Not offered (no rich data to show the setup): no context either.
    rec.calls.clear()
    _engine_, text, kb = _drive_single(monkeypatch, idea, rich_data=False)
    assert kb is None and ESCAPED not in text and rec.calls == []


def test_scan_rows_carry_one_line_per_class_with_their_buttons(monkeypatch):
    from tests.test_a_scan_button_places_what_the_card_shows import UID, _drive_batch
    from tests.test_a_scan_button_places_what_the_card_shows import _engine as _scan_engine

    seen = []

    def _lines(engine, user_id, symbols):
        seen.append((str(user_id), list(symbols)))
        return ["CTX <b>class</b>"]
    monkeypatch.setattr(idea_context, "class_lines_for", _lines)
    text, kb = _drive_batch(monkeypatch, _scan_engine())
    assert kb is not None and "CTX &lt;b&gt;class&lt;/b&gt;" in text
    assert seen == [(UID, ["SOL/USDT"])]
    # A refused gate offers no button, so it carries no record either.
    seen.clear()
    text, kb = _drive_batch(monkeypatch, _scan_engine(), blocked=True)
    assert seen == [] and "CTX" not in text


def test_a_typed_trade_ticket_carries_it(monkeypatch):
    from tests.test_every_manual_trade_door_shows_the_review import PLANTED, _trade_card

    rec = _Recorder()
    monkeypatch.setattr(idea_context, "idea_context_lines", rec)
    bot = asyncio.run(_trade_card(monkeypatch, PLANTED))
    (card, markup), = bot.sent
    assert card.rstrip().endswith(ESCAPED)
    (uid, iid), = rec.calls
    assert uid == "4242" and f"confirm:{iid}:4242" in str(markup.to_dict())


def test_a_staged_chat_draft_carries_it(monkeypatch):
    from bot.nlp import chat_draft
    from tests.test_chat_draft import _Engine, _stage_handler, _stage_update

    chat_draft._clear_drafts()
    rec = _Recorder()
    monkeypatch.setattr(idea_context, "idea_context_lines", rec)
    monkeypatch.setattr(chat_draft, "market_for",
                        lambda engine, symbol: {"read_state": "read", "price": 100.0,
                                                "atr": 2.0, "as_of": 1})

    async def _review(engine, user_id, trade):
        return {"verdict": "caution", "score_line": "", "flags": [], "notes": [], "unchecked": []}
    monkeypatch.setattr("bot.core.copilot_context.review_ticket", _review)
    engine = _Engine()
    chat_draft.draft_ticket(engine, "4242", "SOL", "LONG")
    draft_id = next(iter(chat_draft._DRAFTS))
    h = _stage_handler(engine, may_trade=True)
    asyncio.run(h._handle_callback(_stage_update(draft_id), None))
    (card, markup), = h.sent
    idea = next(iter(engine._pending_ideas.values()))
    chat_draft._clear_drafts()
    assert card.rstrip().endswith(ESCAPED)
    assert rec.calls == [("4242", idea.id)]


def test_the_drift_re_offer_carries_the_new_idea_s_context_for_the_caller():
    # The re-offer sits inside the confirm callback, past the live gates, the
    # venue read and the re-analysis; `test_a_drift_re_offer_is_not_auto_
    # confirmed` pins that block by its AST. The shape a drive does not reach:
    # the card it sends hands the NEW idea and the caller to the one reading.
    from tests.test_a_drift_re_offer_is_not_auto_confirmed import _drift_retry_blocks

    blocks = [node for rel, node in _drift_retry_blocks() if rel == "bot/skills/callback_handler.py"]
    assert len(blocks) == 1
    calls = [n for n in ast.walk(blocks[0]) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == "idea_context_lines"]
    assert len(calls) == 1
    assert [ast.unparse(a) for a in calls[0].args] == ["self.engine", "_uid", "new_idea"]
    send = [n for n in ast.walk(blocks[0]) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and n.func.attr == "_send"]
    assert any(calls[0] in list(ast.walk(s)) for s in send), "the lines are not on the card it sends"


@pytest.mark.parametrize("path", [
    "bot/skills/skill_registry.py", "bot/skills/alerts_monitor.py",
    "bot/skills/trading_commands.py", "bot/skills/scan_skill.py",
    "bot/skills/callback_handler.py",
])
def test_no_card_composes_the_lines_on_its_own(path):
    # One reading: a card that called the two lines itself could carry one and
    # forget the other, or order them differently from the rest.
    from pathlib import Path

    from tests.source_scan import code_only
    src = code_only((Path(__file__).resolve().parents[1] / path).read_text(encoding="utf-8"))
    assert "class_record_line(" not in src and "entry_timing_line(" not in src, path
