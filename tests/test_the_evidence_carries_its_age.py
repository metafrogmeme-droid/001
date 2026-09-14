"""The chat's evidence carries its age, and the rules beside each block
describe that block.

Audit B4 / B7 / B10 (2026-09-12), three shapes of the same claim:

1. LIVE MARKET stamped the moment the PROMPT was built while admitting ticks
   up to 90 s old, and the comment on that bound said "the age is printed
   either way" — nothing printed it, so an 80-second-old price read as this
   second's. Every row carries its own age now.
2. The paper book's mark had no time on it — restored from disk, and marked
   only when a command happened to read the book — and the chat printed it
   bare, exactly like a live mark; the snapshot valued an unpriced position
   at its entry, so its equity was a partial printed whole. `paper_mark`
   answers (price, age), the prompt refreshes the book from the same fresh
   snapshot the ticker prints and says how old each mark is, and the
   snapshot counts what it held at cost so the equity line can say so.
3. Four rules contradicted the blocks beside them: "only cite … ACTIVE
   POSITIONS / RECENT CLOSED TRADES" under three more blocks that carry
   prices; "tap Close or Cancel" on the web, which has no such card; "the
   get_orders tool asks the exchange — call it" on turns that hold no tools;
   and PENDING TRADE IDEAS turning an engine with no queue into "the bot is
   not about to place anything" and dropping a 0.0 entry from the row.

Plant the state, read what the model is told.
"""
from __future__ import annotations

import datetime as _dt
import re
import time
from types import SimpleNamespace as NS
from unittest.mock import patch

import bot.config
from bot.core.ws_feed import PriceTick
from bot.risk.portfolio import PortfolioTracker
from bot.skills.chat_runtime import _CHAT_CANNOT_ACT_RULE, cannot_act_rule
from bot.skills.telegram_handler import TelegramHandler as H
from bot.skills.telegram_handler import (
    _live_positions_block,
    _mark_age_words,
    _paper_position_row,
    _unpriced_note,
)
from bot.utils.models import Direction, TradeExecution
from tests.test_chat_prompt_describes_only_the_callers_book import _handler as _prompt_handler
from tests.test_the_paper_prompt_is_as_honest_as_the_live_one import _paper_engine, _paper_pf, _paper_pos

UTC = _dt.timezone.utc


# ── 1. every price row carries its own age ─────────────────────────────────

def _tick(sym, last, chg=0.0, age_sec=0.0):
    return PriceTick(symbol=sym, last=last, bid=last, ask=last, volume_24h=1.0,
                     change_pct_24h=chg,
                     timestamp=_dt.datetime.now(UTC) - _dt.timedelta(seconds=age_sec))


class _Feed:
    def __init__(self, ticks, raw=False):
        self._t, self._raw = ticks, raw

    def get_snapshot(self, max_age_sec=None):
        if self._raw or not max_age_sec:
            return dict(self._t)
        now = _dt.datetime.now(UTC)
        return {s: t for s, t in self._t.items()
                if (now - t.timestamp).total_seconds() <= max_age_sec}


def _block(ticks, raw=False):
    h = H.__new__(H)
    h.engine = NS(ws_feed=_Feed(ticks, raw=raw))
    return h._live_ticker_block()


def test_every_price_row_carries_its_own_age():
    out = _block({"BTC/USDT": _tick("BTC/USDT", 61432.10, 0.021, age_sec=3),
                  "ETH/USDT": _tick("ETH/USDT", 2984.55, age_sec=80)})
    btc = next(line for line in out.splitlines() if "BTC/USDT" in line)
    eth = next(line for line in out.splitlines() if "ETH/USDT" in line)
    assert re.search(r"— [2-5] s ago$", btc), btc
    assert re.search(r"— (79|80|81|82) s ago$", eth), eth
    assert "each price carries its own age" in out and "none older than 90 s" in out
    assert re.search(r"as of \d{2}:\d{2}:\d{2} UTC", out), "the build time is still stated, as what it is"
    assert "State ONLY these prices, each as of its own age" in out
    assert "only as of that timestamp" not in out


def test_a_fresh_tick_says_just_now_and_an_unreadable_time_says_unknown():
    assert "— just now" in _block({"BTC/USDT": _tick("BTC/USDT", 61432.10)})
    # The real snapshot excludes a tick whose time cannot be read; a feed
    # that hands one through is answered with "unknown", never a number.
    odd = NS(last=100.0, change_pct_24h=0.0, timestamp="not a time")
    out = _block({"SOL/USDT": odd}, raw=True)
    assert "SOL/USDT" in out and "— age unknown" in out


# ── 2. the paper mark carries its age, and the snapshot counts what it held at cost ──

def _tracker():
    return PortfolioTracker(initial_balance=1000.0)


def test_a_mark_set_in_this_process_carries_its_age():
    t = _tracker()
    assert t.paper_mark("BTC/USDT") == (None, None)
    t.mark_to_market({"BTC/USDT": 100.0, "ETH/USDT": 0.0})
    price, age = t.paper_mark("BTC/USDT")
    assert price == 100.0 and age is not None and 0 <= age < 5
    assert t.paper_mark("ETH/USDT") == (None, None), "a zero price is not a mark"


def test_a_restored_mark_without_a_time_is_of_unknown_age():
    t = _tracker()
    t._load_from_state_dict({"balance": 1000.0, "last_prices": {"BTC/USDT": 100.0}})
    assert t.paper_mark("BTC/USDT") == (100.0, None)
    u = _tracker()
    u._load_from_state_dict({"balance": 1000.0, "last_prices": {"BTC/USDT": 100.0},
                             "last_price_at": {"BTC/USDT": "yesterday"}})
    assert u.paper_mark("BTC/USDT") == (100.0, None), "junk is an unknown age, not a fresh one"


def test_the_mark_time_survives_a_save_and_a_load(tmp_path):
    t = _tracker()
    t.mark_to_market({"BTC/USDT": 100.0})
    t.save_state(str(tmp_path / "p.json"))
    u = _tracker()
    assert u.load_state(str(tmp_path / "p.json"))
    price, age = u.paper_mark("BTC/USDT")
    assert price == 100.0 and age is not None and age < 5
    v = _tracker()
    v._load_from_state_dict({"balance": 1000.0, "last_prices": {"BTC/USDT": 100.0},
                             "last_price_at": {"BTC/USDT": time.time() - 3600}})
    _, age = v.paper_mark("BTC/USDT")
    assert 3590 < age < 3700, "an hour-old mark reads as an hour old, not as now"


def _position(asset="BTC/USDT", entry=100.0):
    return TradeExecution.model_construct(
        trade_id=f"T-{asset}", asset=asset, direction=Direction.LONG,
        entry_price=entry, quantity=1.0, leverage=1, stop_loss=entry * 0.9,
        take_profit=entry * 1.2)


def test_the_snapshot_counts_the_positions_it_held_at_cost():
    t = _tracker()
    t._positions["T-1"] = _position("BTC/USDT")
    t._positions["T-2"] = _position("ETH/USDT", 10.0)
    s = t.snapshot()
    assert s.unpriced_positions == 2 and s.open_positions == 2
    t.mark_to_market({"BTC/USDT": 110.0})
    assert t.snapshot().unpriced_positions == 1
    t.mark_to_market({"ETH/USDT": 9.0})
    assert t.snapshot().unpriced_positions == 0


def test_the_paper_row_prints_the_marks_age():
    pos = _paper_pos(entry_price=0.1, quantity=10.0)
    assert "MARK $0.1200 (marked 3 s ago)" in _paper_position_row(pos, 0.12, mark_age_sec=3)
    assert "MARK $0.1200 (marked just now)" in _paper_position_row(pos, 0.12, mark_age_sec=0.2)
    assert "(mark is 10 min old — NOT current)" in _paper_position_row(pos, 0.12, mark_age_sec=600)
    assert "(mark is 2.0 h old — NOT current)" in _paper_position_row(pos, 0.12, mark_age_sec=7200)
    assert ("(mark age unknown — a price restored from a previous run; treat it as "
            "NOT current)") in _paper_position_row(pos, 0.12, mark_age_sec=None)
    bare = _paper_position_row(pos, 0.12)
    assert "MARK $0.1200" in bare and "(mark" not in bare, "no age claimed unless one was read"
    gone = _paper_position_row(pos, None, mark_age_sec=3)
    assert "MARK UNAVAILABLE" in gone and "(marked" not in gone


def test_the_age_words_turn_on_the_ticker_blocks_own_bound():
    assert _mark_age_words(H.CHAT_TICKER_MAX_AGE_SEC) == f"marked {H.CHAT_TICKER_MAX_AGE_SEC} s ago"
    assert _mark_age_words(H.CHAT_TICKER_MAX_AGE_SEC + 1) == "mark is 1 min old — NOT current"
    assert _mark_age_words("soon") == "mark age unknown"


def _prompt_for(pf, *, marks=None, surface="telegram", handler=None):
    h = handler or _prompt_handler(_paper_engine(pf))
    if marks is not None:
        h._chat_marks = lambda: marks
    with patch.object(type(bot.config.CONFIG), "is_live", return_value=False):
        return H._build_chat_system_prompt(h, "u1", surface=surface)


def test_the_prompt_refreshes_the_paper_book_and_prints_the_age():
    marked = []
    pf = _paper_pf(open_positions=[_paper_pos(asset="DOGE/USDT")])
    pf.paper_mark = lambda asset: (0.11, 4.0) if asset == "DOGE/USDT" else (None, None)
    pf.mark_to_market = lambda prices: marked.append(dict(prices))
    out = _prompt_for(pf, marks={"DOGE/USDT": 0.11, "BTC/USDT": 60000.0})
    assert marked == [{"DOGE/USDT": 0.11}], "the held assets are marked, and only those"
    assert "MARK $0.1100 (marked 4 s ago)" in out


def test_a_book_that_only_has_a_price_map_reads_as_an_unknown_age():
    pf = _paper_pf(open_positions=[_paper_pos(asset="DOGE/USDT")],
                   last_prices={"DOGE/USDT": 0.11})
    out = _prompt_for(pf)
    assert "MARK $0.1100 (mark age unknown" in out and "NOT current" in out


def test_a_snapshot_that_cannot_be_read_marks_nothing_and_the_prompt_still_renders():
    pf = _paper_pf(open_positions=[_paper_pos(asset="DOGE/USDT")],
                   last_prices={"DOGE/USDT": 0.11})

    def _never(prices):
        raise AssertionError("a book must not be marked from a snapshot nobody read")

    pf.mark_to_market = _never
    h = _prompt_handler(_paper_engine(pf))

    def _boom():
        raise RuntimeError("feed down")

    h._chat_marks = _boom
    out = _prompt_for(pf, handler=h)
    assert "MARK $0.1100" in out


def test_the_equity_line_says_when_positions_were_held_at_cost():
    pf = _paper_pf(open_positions=[_paper_pos(asset="DOGE/USDT")])
    snap = pf.snapshot()
    pf.snapshot = lambda: NS(**{**vars(snap), "unpriced_positions": 1, "open_positions": 1})
    out = _prompt_for(pf)
    assert ("1 of 1 open position(s) have no mark and are held at cost, so this "
            "equity is PARTIAL") in out
    assert "held at cost" not in _prompt_for(_paper_pf(open_positions=[_paper_pos()]))
    assert _unpriced_note(NS(unpriced_positions=0, open_positions=3)) == ""
    assert "2 of 3" in _unpriced_note(NS(unpriced_positions=2, open_positions=3))


# ── 3. the rules describe the blocks beside them ───────────────────────────

def test_the_web_prompt_names_telegrams_positions_card_as_the_close_door():
    assert cannot_act_rule("telegram") is _CHAT_CANNOT_ACT_RULE
    web = cannot_act_rule("web")
    assert "positions card in the Telegram bot" in web and "nothing in this web chat can" in web
    assert "open it on the positions card and tap Close or Cancel." not in web
    assert web.count("\n") == _CHAT_CANNOT_ACT_RULE.count("\n"), "one sentence swapped, nothing else"
    pf = _paper_pf()
    assert web in _prompt_for(pf, surface="web")
    assert _CHAT_CANNOT_ACT_RULE not in _prompt_for(pf, surface="web")
    assert _CHAT_CANNOT_ACT_RULE in _prompt_for(pf, surface="telegram")


def test_the_cite_rule_names_every_block_that_carries_a_price():
    rule = H._CHAT_SYSTEM_PROMPT.split("Only cite", 1)[1].split("\n", 1)[0]
    for block in ("LIVE MARKET", "ACTIVE POSITIONS", "UNFILLED LIMIT ORDERS",
                  "RECENT CLOSED TRADES", "PENDING TRADE IDEAS"):
        assert block in rule, block
    assert "ACTIVE POSITIONS / RECENT CLOSED TRADES sections" not in H._CHAT_SYSTEM_PROMPT


def test_the_orders_sentence_does_not_order_a_call_the_turn_may_not_have():
    out = _live_positions_block(NS(open_positions=[NS(
        symbol="SOL/USDT", status="pending_fill", direction="LONG",
        entry_price=140.0, stop_loss=135.0, take_profit=150.0,
        quantity=1.0, cost_usd=14.0, leverage=10)]))
    assert "call it when it is offered on this turn" in out
    assert "call it before telling" not in out
    assert "the bot's own record and not confirmed" in out


def _ideas(engine_attrs):
    return H._pending_ideas_block.__get__(NS(engine=NS(**engine_attrs)))()


def test_an_engine_with_no_queue_is_not_an_empty_queue():
    assert "could not be read" in _ideas({})
    assert "could not be read" in _ideas({"pending_ideas": None})
    out = _ideas({"pending_ideas": []})
    assert "none queued by the bot" in out and "could not be read" not in out


def test_an_idea_whose_entry_is_zero_says_so_instead_of_vanishing():
    zero = NS(direction=NS(value="LONG"), asset="ARB/USDT", entry_price=0.0, confidence=0.7,
              source="scan_skill")
    real = NS(direction=NS(value="SHORT"), asset="OP/USDT", entry_price=1.5, confidence=0.6,
              source="scan_skill")
    bare = NS(direction=NS(value="LONG"), asset="SUI/USDT", confidence=0.5, source="scan_skill")
    out = _ideas({"pending_ideas": [zero, real, bare]})
    assert "ARB/USDT, entry NOT ON RECORD" in out
    assert "OP/USDT, entry $1.5000" in out
    sui = next(line for line in out.splitlines() if "SUI/USDT" in line)
    assert "entry" not in sui, "a record with no such field claims nothing about it"
