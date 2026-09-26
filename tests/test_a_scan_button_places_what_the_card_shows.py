"""A scan card's ✅ places the trade the card shows, and nothing else.

Driven on 2026-09-26: `/fullscan` printed SOL/USDT LONG with a pullback entry
of 121.827, a stop of 119.708 (1.7%) and a target of 125.005 (2.6%), and
sealed those levels as the provable call. Its ✅ carried
`scan_confirm:SOL/USDT:LONG:122.1158...` -- the symbol, the side and the SCAN
PRICE -- and the tap built a MARKET order at 122.116 with a flat 3% stop and a
6% target, at a stamped confidence of 0.6, and replied "✅ SOL/USDT LONG
EXECUTED". `/fullscan SOL` printed the 1h analyzer's SHORT with "Risk: ✅
APPROVED" under a button carrying the 4h scan row's LONG, and offered the
button when the analyzer had no idea at all.

The card now registers each row's own idea (a limit at the entry it prints,
with its stop and target, at the row's own score) as the caller's pending
idea, and the button is `confirm:<id>:<owner>`: the one door with the owner
check, the H-18 live permission and the drift re-offer. An old
`scan_confirm:` / `scan_limit:` payload is refused and places nothing.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import bot.config as bot_config
from bot.skills import scan_skill
from bot.utils.models import Direction, TradeIdea
from bot.utils.user_store import ROLE_PERMISSIONS

UID = "424242"
H4 = 4 * 3600 * 1000


def _uptrend(n: int = 100, base: float = 100.0) -> list:
    """A clean 4h uptrend whose last row is a CLOSED bar (so the forming-bar
    drop keeps every row)."""
    last_closed = (int(time.time() * 1000) // H4) * H4 - H4
    t0 = last_closed - (n - 1) * H4
    out, p = [], base
    for i in range(n):
        o, c = p, p * 1.002
        out.append([t0 + i * H4, o, max(o, c) * 1.003, min(o, c) * 0.997, c, 1000.0 + i])
        p = c
    return out


class _Exchange:
    def __init__(self, rows):
        self.rows = rows

    async def fetch_ohlcv(self, sym, tf, limit=100):
        return self.rows[-limit:]


def _engine(**kw):
    registered = []
    e = SimpleNamespace(
        _pending_ideas={}, _pending_atr={}, _engine_idea_ids=set(), analyzer=None,
        scanner=SimpleNamespace(_get_exchange=AsyncMock(return_value=_Exchange(_uptrend()))),
        _register_engine_idea=lambda idea: registered.append(idea),
        registered_as_engine=registered)
    for k, v in kw.items():
        setattr(e, k, v)
    return e


def _drive_batch(monkeypatch, engine, *, blocked=False):
    """The real `_scan_batch`, text path, over a one-symbol universe."""
    import bot.core.trade_gate as trade_gate
    import bot.formatters.signal_card as signal_card

    monkeypatch.setattr(scan_skill, "UNIVERSE", ["SOL/USDT"])
    monkeypatch.setattr(scan_skill, "_push_scan_to_dashboard", lambda *a, **k: None)
    monkeypatch.setattr(scan_skill, "_build_scan_payload", lambda *a, **k: {})
    monkeypatch.setattr(
        trade_gate, "entry_gate",
        lambda e, c="": {"blocked": blocked, "unknown": False,
                         "reasons": ["kill switch engaged"] if blocked else []})

    def _no_png(*_a, **_k):
        raise RuntimeError("no Pillow here")
    monkeypatch.setattr(signal_card, "render_scan_results_card", _no_png)

    msg = SimpleNamespace(edit_text=AsyncMock(), delete=AsyncMock())
    update = SimpleNamespace(
        message=SimpleNamespace(reply_text=AsyncMock(return_value=msg)),
        effective_user=SimpleNamespace(id=int(UID)),
        effective_chat=SimpleNamespace(id=int(UID)))
    context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock(),
                                                  send_photo=AsyncMock()), bot_data={})
    asyncio.run(scan_skill._scan_batch(update, context, engine, top_n=10,
                                       patterns=False, ai=False))
    final = msg.edit_text.await_args_list[-1]
    return final.args[0], final.kwargs.get("reply_markup")


def _buttons(kb):
    return [(b.text, b.callback_data) for row in kb.inline_keyboard for b in row]


def _card_level(text: str, label: str) -> float:
    line = next(ln for ln in text.splitlines() if ln.strip().startswith(label))
    return float(line.split("<code>$")[1].split("</code>")[0].replace(",", ""))


# ── 1. The button is the card's idea ────────────────────────────────────


def test_the_button_names_the_idea_whose_levels_the_card_prints(monkeypatch):
    engine = _engine()
    text, kb = _drive_batch(monkeypatch, engine)

    (idea_id, idea), = engine._pending_ideas.items()
    assert _buttons(kb) == [("✅ SOL", f"confirm:{idea_id}:{UID}"),
                            ("Limit", f"setlimit:{idea_id}:{UID}"),
                            ("Skip", f"reject:{idea_id}:{UID}")]
    # The card prints six significant figures; the idea holds the row's own.
    assert _card_level(text, "Entry:") == pytest.approx(idea.entry_price, rel=1e-5)
    assert _card_level(text, "SL:") == pytest.approx(idea.stop_loss, rel=1e-5)
    assert _card_level(text, "TP:") == pytest.approx(idea.take_profit, rel=1e-5)
    assert idea.direction is Direction.LONG and idea.asset == "SOL/USDT"


def test_the_pullback_entry_is_placed_as_a_limit_and_the_card_says_so(monkeypatch):
    engine = _engine()
    text, _kb = _drive_batch(monkeypatch, engine)
    (idea,) = engine._pending_ideas.values()
    assert idea.order_type == "limit", (
        "the card's entry is a pullback away from the price; a market order "
        "fills at the price, which is a different entry")
    assert "(limit, on a pullback)" in text
    assert "places the entry shown, as a limit order" in text


def test_the_stop_and_target_are_the_rows_atr_levels_not_flat_percents(monkeypatch):
    from bot.formatters.drift_offer import STOP_PCT, TARGET_PCT

    engine = _engine()
    _drive_batch(monkeypatch, engine)
    (idea_id, idea), = engine._pending_ideas.items()
    atr = engine._pending_atr[idea_id]
    scan_price = idea.entry_price + 0.3 * atr
    assert idea.stop_loss == pytest.approx(scan_price - 2.5 * atr)
    assert idea.take_profit == pytest.approx(scan_price + 3.0 * atr)
    assert idea.stop_loss != pytest.approx(scan_price * (1 - STOP_PCT))
    assert idea.take_profit != pytest.approx(scan_price * (1 + TARGET_PCT))


def test_the_idea_is_the_callers_and_never_the_engines(monkeypatch):
    """A person's idea: the autonomous auto-confirm reads ownership first."""
    engine = _engine()
    _drive_batch(monkeypatch, engine)
    assert engine._pending_ideas and engine.registered_as_engine == []
    assert engine._engine_idea_ids == set()
    (idea,) = engine._pending_ideas.values()
    assert idea.source == "scan_skill"


def test_a_blocked_gate_registers_nothing(monkeypatch):
    engine = _engine()
    text, kb = _drive_batch(monkeypatch, engine, blocked=True)
    assert kb is None and engine._pending_ideas == {}
    assert "New entries are refused" in text


# ── 2. The confidence is the row's score, not a stamp ────────────────────


def _row(score, **kw):
    r = {"sym": "SOL/USDT", "dir": "LONG", "price": 100.0, "atr": 1.0,
         "entry": 99.7, "sl": 97.5, "tp": 103.0, "score": score}
    r.update(kw)
    return r


@pytest.mark.parametrize("score", [0.91, 0.72, 0.64])
def test_the_idea_carries_the_score_the_card_prints(score):
    from bot.risk.quality_ladder import quality_reading

    idea, why = scan_skill.scan_row_idea(_row(score))
    assert why == "" and idea is not None
    assert idea.confidence == score, "the old button stamped 0.6 on every row"
    reading = quality_reading(idea)
    assert reading.measured and reading.confidence == score


def test_a_row_under_the_confidence_floor_is_not_offered():
    """The stamp 0.6 cleared the 0.60 floor by construction; a real 0.42
    does not, and a button whose only answer is that refusal is not offered.
    The floor is the risk gate's own reading, not a number written here."""
    from bot.risk.confidence_floor import min_confidence_for

    rows = [dict(_row(0.42), sym="SOL/USDT"), dict(_row(0.80), sym="ETH/USDT")]
    floor = min_confidence_for(TradeIdea(
        asset="SOL/USDT", direction=Direction.LONG, entry_price=99.7,
        stop_loss=97.5, take_profit=103.0, confidence=0.42, reasoning="x"))
    assert 0.42 < floor <= 0.80
    engine = _engine()
    scan_skill.register_scan_offers(engine, rows)
    assert rows[0]["idea_id"] is None and "confidence floor" in rows[0]["no_offer"]
    assert rows[1]["idea_id"] in engine._pending_ideas
    header, buttons = scan_skill.scan_action_rows(rows, {"blocked": False}, UID)
    assert [b[0][1] for b in buttons] == [f"confirm:{rows[1]['idea_id']}:{UID}"]
    assert f"Not offered: SOL: score 42% is under the {floor:.0%} confidence floor" in header


def test_a_row_whose_levels_are_not_a_trade_is_not_offered():
    rows = [_row(0.8, sl=99.7)]            # stop AT the entry
    engine = _engine()
    scan_skill.register_scan_offers(engine, rows)
    assert rows[0]["idea_id"] is None and engine._pending_ideas == {}
    header, buttons = scan_skill.scan_action_rows(rows, {"blocked": False}, UID)
    assert buttons == [] and "No actions" in header
    assert "its levels do not make a valid trade" in header


def test_an_unreadable_score_is_not_offered():
    idea, why = scan_skill.scan_row_idea(_row(float("nan")))
    assert idea is None and why == "its scan score could not be read"


def test_the_atr_the_levels_came_from_travels_to_the_re_check():
    rows = [_row(0.8, atr=1.25)]
    engine = _engine()
    scan_skill.register_scan_offers(engine, rows)
    assert engine._pending_atr[rows[0]["idea_id"]] == 1.25


def test_an_unreadable_atr_is_not_stored_as_the_re_checks_atr():
    rows = [_row(0.8, atr=None)]
    engine = _engine()
    scan_skill.register_scan_offers(engine, rows)
    assert rows[0]["idea_id"] in engine._pending_ideas
    assert engine._pending_atr == {}


# ── 3. The tap goes through the one confirm door ─────────────────────────


async def _noop(*a, **k):
    return None


def _tap_confirm(engine, data, *, live_ok=True, admin=False, live=False):
    """One tap through the REAL `_handle_callback` dispatcher."""
    from bot.skills.telegram_handler import TelegramHandler

    replies: list = []
    seen: list = []

    async def _confirm(trade_id, user_id=""):
        seen.append((engine._pending_ideas.get(trade_id), user_id))
        engine._pending_ideas.pop(trade_id, None)
        return "✅ LIVE LONG SOL/USDT filled"

    engine.confirm_trade = _confirm
    engine.live_executor = SimpleNamespace(_positions={})

    class _Users:
        def get(self, tid):
            return {"role": "trader", "authorized": True}

        def has_permission(self, tid, perm):
            return perm in ROLE_PERMISSIONS["trader"]

        def is_authorized(self, *a, **k):
            return True

        def is_admitted(self, *a, **k):
            return True

        def permission_denial(self, *a, **k):
            return None

        def get_tier(self, *a, **k):
            return "free"

        def register(self, *a, **k):
            return None

    h = TelegramHandler.__new__(TelegramHandler)
    h.engine = engine
    h.users = _Users()
    h.forwarder = SimpleNamespace(post_trade_opened=_noop)
    h._limiter = SimpleNamespace(allow=lambda uid: True)
    h._check_auth = lambda update: True
    h._is_admin = lambda update: admin
    h._can_trade_live = lambda tg_id: live_ok
    h._live_refusal_key = lambda: "live_not_enabled"
    h._lang = lambda update: "en"

    async def _send(update, text, **kw):
        replies.append(text)

    h._send = _send
    query = SimpleNamespace(
        data=data, answer=_noop,
        message=SimpleNamespace(edit_reply_markup=_noop, chat_id=int(UID)))
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=int(UID), first_name="X"),
        effective_chat=SimpleNamespace(id=int(UID)))
    orig = type(bot_config.CONFIG).is_live
    type(bot_config.CONFIG).is_live = lambda self: live
    try:
        asyncio.new_event_loop().run_until_complete(
            h._handle_callback(update, SimpleNamespace()))
    finally:
        type(bot_config.CONFIG).is_live = orig
    return replies, seen


def test_a_tap_confirms_the_registered_idea_with_the_cards_levels(monkeypatch):
    engine = _engine()
    text, kb = _drive_batch(monkeypatch, engine)
    (idea_id, idea), = engine._pending_ideas.items()
    confirm = next(d for _t, d in _buttons(kb) if d.startswith("confirm:"))

    replies, seen = _tap_confirm(engine, confirm)

    (placed, user), = seen
    assert placed is idea and user == UID
    assert (placed.entry_price, placed.stop_loss, placed.take_profit,
            placed.order_type) == (idea.entry_price, idea.stop_loss,
                                   idea.take_profit, "limit")


def test_a_caller_who_may_not_trade_live_is_refused_at_the_scan_cards_door(monkeypatch):
    """H-18, on the door the scan card's money now goes through."""
    engine = _engine()
    _t, kb = _drive_batch(monkeypatch, engine)
    confirm = next(d for _t, d in _buttons(kb) if d.startswith("confirm:"))
    replies, seen = _tap_confirm(engine, confirm, live_ok=False, live=True)
    assert seen == [], "a caller without live authority reached confirm_trade"
    assert any("\U0001f512" in r for r in replies)


def test_another_person_cannot_tap_the_callers_scan_button(monkeypatch):
    engine = _engine()
    _t, kb = _drive_batch(monkeypatch, engine)
    confirm = next(d for _t, d in _buttons(kb) if d.startswith("confirm:"))
    stranger = confirm.rsplit(":", 1)[0] + ":999"
    replies, seen = _tap_confirm(engine, stranger)
    assert seen == [] and any("Access denied" in r for r in replies)


# ── 4. An old button places nothing ─────────────────────────────────────


def _old_tap(data):
    query = SimpleNamespace(data=data, answer=AsyncMock(),
                            edit_message_reply_markup=AsyncMock(),
                            message=SimpleNamespace(reply_text=AsyncMock()))
    engine = _engine(confirm_trade=AsyncMock(return_value="filled"))
    context = SimpleNamespace(bot_data={"engine": engine, "telegram_handler": SimpleNamespace()})
    update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=int(UID)))
    return query, engine, context, update


@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize("data", ["scan_confirm:SOL/USDT:LONG:122.11588271895928",
                                  "scan_limit:SOL/USDT:LONG:122.11588271895928"])
def test_an_old_scan_button_is_refused_and_places_nothing(data, live, monkeypatch):
    monkeypatch.setattr(type(bot_config.CONFIG), "is_live", lambda self: live)
    query, engine, context, update = _old_tap(data)
    asyncio.run(scan_skill.callback_confirm_reject(update, context))
    assert engine.confirm_trade.await_count == 0
    assert engine._pending_ideas == {}, "nothing is registered for a card that no longer exists"
    said = query.message.reply_text.await_args.args[0]
    assert "nothing was placed" in said and "EXECUTED" not in said
    assert "<b>SOL/USDT</b>" in said
    query.edit_message_reply_markup.assert_awaited_once_with(reply_markup=None)


def test_the_old_refusal_escapes_the_symbol_it_quotes():
    query, _e, context, update = _old_tap("scan_confirm:<b>X:LONG:1")
    asyncio.run(scan_skill.callback_confirm_reject(update, context))
    said = query.message.reply_text.await_args.args[0]
    assert "&lt;b&gt;X" in said and "<b><b>X" not in said


def test_a_skip_still_says_skipped():
    query, engine, context, update = _old_tap("scan_reject:SOL/USDT")
    asyncio.run(scan_skill.callback_confirm_reject(update, context))
    assert "skipped" in query.message.reply_text.await_args.args[0]
    assert engine.confirm_trade.await_count == 0


# ── 5. /fullscan SYMBOL: the button is the analyzer's idea ───────────────


def _drive_single(monkeypatch, idea, *, rich_data=True):
    import bot.skills.scan_skill as ss

    if not rich_data:
        async def _none(*a, **k):
            return None
        monkeypatch.setattr(ss, "fetch_analysis_data", _none)

    rows_1h = _uptrend(100)

    class _Ex:
        async def fetch_ohlcv(self, sym, tf, limit=100):
            return (_uptrend(100) if tf == "4h" else rows_1h)[-limit:]

        async def fetch_order_book(self, sym, limit=20):
            return {"bids": [[100, 5]], "asks": [[100.1, 5]]}

    async def _an(sig, **kw):
        return idea

    from bot.utils.models import RiskVerdict
    engine = _engine(_analyze_signal=_an,
                     risk=SimpleNamespace(evaluate=lambda i, atr=None: SimpleNamespace(
                         verdict=RiskVerdict.APPROVED, reason="ok")))
    engine.scanner = SimpleNamespace(_get_exchange=AsyncMock(return_value=_Ex()))
    msg = SimpleNamespace(edit_text=AsyncMock())
    update = SimpleNamespace(message=SimpleNamespace(reply_text=AsyncMock(return_value=msg)),
                             effective_user=SimpleNamespace(id=int(UID)))
    asyncio.run(ss._scan_single(update, SimpleNamespace(), engine, "SOL/USDT"))
    final = msg.edit_text.await_args_list[-1]
    return engine, final.args[0], final.kwargs.get("reply_markup")


def test_the_single_symbol_button_is_the_analyzers_short(monkeypatch):
    short = TradeIdea(asset="SOL/USDT", direction=Direction.SHORT, entry_price=122.40,
                      stop_loss=123.30, take_profit=120.60, confidence=0.66,
                      reasoning="[RULE_ENGINE|RANGE|x|intraday|C=0.40] fade",
                      order_type="limit")
    engine, text, kb = _drive_single(monkeypatch, short)
    assert "SHORT" in text
    assert _buttons(kb) == [("✅ Confirm", f"confirm:{short.id}:{UID}"),
                            ("❌ Reject", f"reject:{short.id}:{UID}")]
    assert engine._pending_ideas == {short.id: short}
    assert short.id in engine._pending_atr, "the ATR the card's verdict used"


def test_no_analyzer_idea_no_button(monkeypatch):
    engine, text, kb = _drive_single(monkeypatch, None)
    assert kb is None and engine._pending_ideas == {}
    assert "No setup from the analyzer, so there is nothing to place." in text


def test_an_idea_the_card_does_not_show_is_not_offered(monkeypatch):
    """Without the rich data the fallback card prints the scan row, not the
    analyzer's setup, so its button would place a trade the card never showed."""
    short = TradeIdea(asset="SOL/USDT", direction=Direction.SHORT, entry_price=122.40,
                      stop_loss=123.30, take_profit=120.60, confidence=0.66,
                      reasoning="[RULE_ENGINE|RANGE|x|intraday|C=0.40] fade")
    engine, text, kb = _drive_single(monkeypatch, short, rich_data=False)
    assert kb is None and engine._pending_ideas == {}
    assert "setup could not be shown here, so it is not offered" in text
