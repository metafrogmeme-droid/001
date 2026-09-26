"""Every reader of the live closed-trade record asks whether it read in full.

`LiveExecutor` loads its closed-trade file row by row. A row it cannot read
is kept verbatim for the next save and left out of `closed_positions`; a file
that will not parse at all loads as `[]`. Either way it sets
`closed_trades_read_failed`. A card that prints a total, a count or a win
rate over the list without asking prints part of the record as the whole of
it, and over `[]` it prints "no closed trades yet" about a file that failed.

Before this rule, three of fourteen readers asked. `/classpf` told a caller
whose file would not parse that they had no closed trades, the risk and status
panes printed the realized total of every close ever under "Daily PnL", and
the post-mortem reviewed "your last trade" from whatever rows had read.

THE RULE. In `bot/` (the executor's own module aside, which writes the flag),
a function that reads `closed_positions` (as an attribute, or as the string
handed to `getattr`) must ask in itself or an enclosing function: read
`closed_trades_read_failed`, or call `closed_record_partial` or
`record_unreadable` (the website sync's reading), under any import alias.
Otherwise the function is a row in `tests/closed_record_reads_baseline.txt`
with its reason. Two-way, as `known_failures.txt` is: a new unasked reader
fails, and a row the rule no longer finds fails.

What it does not check, stated: that the answer is RENDERED. A function can
ask and ignore the answer; the drives below read what each card says. And a
list handed on from a function that read it is the caller's reading, so the
callee is not a site.
"""
from __future__ import annotations

import ast
import asyncio
import types
from pathlib import Path

import pytest

from bot.formatters.realized_totals import CLOSED_RECORD_UNREAD

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "tests" / "closed_record_reads_baseline.txt"
EXEMPT_FILES = {"bot/core/live_executor.py"}
FLAG = "closed_trades_read_failed"
HELPERS = {"closed_record_partial", "record_unreadable"}
RECORD = "closed_positions"


def _reads_record(node: ast.AST) -> bool:
    if isinstance(node, ast.Attribute) and node.attr == RECORD:
        return isinstance(node.ctx, ast.Load)
    return isinstance(node, ast.Constant) and node.value == RECORD


def _helper_names(tree: ast.AST) -> set[str]:
    """Every name a helper is bound to in this tree, aliases included."""
    names = set(HELPERS)
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            for a in n.names:
                if a.name in HELPERS:
                    names.add(a.asname or a.name)
    return names


def _asks(fn: ast.AST, helpers: set[str]) -> bool:
    for n in ast.walk(fn):
        if isinstance(n, ast.Attribute) and n.attr == FLAG:
            return True
        if isinstance(n, ast.Constant) and n.value == FLAG:
            return True
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name) and f.id in helpers:
                return True
            if isinstance(f, ast.Attribute) and f.attr in helpers:
                return True
    return False


def unasked_readers(sources: dict[str, str]) -> set[str]:
    """``path::qualname`` for every function that reads the record and does
    not ask, in itself or an enclosing function."""
    found: set[str] = set()
    for rel, src in sources.items():
        tree = ast.parse(src)
        helpers = _helper_names(tree)

        def visit(node: ast.AST, chain: tuple, names: tuple) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    own = [n for n in ast.walk(child) if _reads_record(n)
                           and not _inside_nested_def(child, n)]
                    new_chain = chain + (child,)
                    qual = ".".join(names + (child.name,))
                    if own and not any(_asks(f, helpers) for f in new_chain):
                        found.add(f"{rel}::{qual}")
                    visit(child, new_chain, names + (child.name,))
                elif isinstance(child, ast.ClassDef):
                    visit(child, chain, names + (child.name,))
                else:
                    visit(child, chain, names)

        visit(tree, (), ())
    return found


def _inside_nested_def(fn: ast.AST, target: ast.AST) -> bool:
    """True when ``target`` sits in a def nested in ``fn``: that def is its
    own site, charged to itself and not to the enclosing function."""
    for n in ast.walk(fn):
        if n is fn or not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for m in ast.walk(n):
            if m is target:
                return True
    return False


def _tree_sources() -> dict[str, str]:
    out = {}
    for p in sorted((ROOT / "bot").rglob("*.py")):
        rel = p.relative_to(ROOT).as_posix()
        if rel in EXEMPT_FILES:
            continue
        src = p.read_text(encoding="utf-8")
        if RECORD in src:
            out[rel] = src
    return out


def _baseline_rows(text: str) -> dict[str, str]:
    rows = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, reason = line.partition("#")
        rows[key.strip()] = reason.strip()
    return rows


def reasonless(text: str) -> list[str]:
    return [k for k, r in _baseline_rows(text).items() if not r]


# ── the rule over the tree ──────────────────────────────────────────────

def test_every_unasked_reader_is_a_baselined_row():
    found = unasked_readers(_tree_sources())
    rows = _baseline_rows(BASELINE.read_text(encoding="utf-8"))
    new = sorted(found - set(rows))
    assert not new, (
        "These read executor.closed_positions and never ask whether the "
        "record read in full (closed_record_partial / closed_trades_read_failed):\n  "
        + "\n  ".join(new))


def stale_rows(found: set[str], text: str) -> list[str]:
    return sorted(set(_baseline_rows(text)) - found)


def test_a_baselined_row_the_rule_no_longer_finds_is_stale():
    found = unasked_readers(_tree_sources())
    stale = stale_rows(found, BASELINE.read_text(encoding="utf-8"))
    assert not stale, "stale rows in closed_record_reads_baseline.txt:\n  " + "\n  ".join(stale)


def test_a_stale_row_is_named():
    # Against an honest baseline the test above can pass whatever the rule
    # does, so the rule is driven on a planted row too.
    text = "bot/a.py::f  # still read\nbot/b.py::g  # fixed since\n"
    assert stale_rows({"bot/a.py::f"}, text) == ["bot/b.py::g"]


def test_every_baselined_row_carries_its_reason():
    assert not reasonless(BASELINE.read_text(encoding="utf-8"))


def test_the_rule_reads_real_readers_at_all():
    # A walk that found nothing would pass the two tests above vacuously.
    src = _tree_sources()
    assert len(src) >= 8, sorted(src)


# ── the rule, driven on planted source ──────────────────────────────────

def _found(code: str) -> set[str]:
    return unasked_readers({"bot/x.py": code})


@pytest.mark.parametrize("code, expect", [
    ("def f(ex):\n    return len(ex.closed_positions)\n", {"bot/x.py::f"}),
    ("def f(ex):\n    return getattr(ex, 'closed_positions', [])\n", {"bot/x.py::f"}),
    ("def f(ex):\n    rows = ex.closed_positions\n"
     "    return rows, ex.closed_trades_read_failed\n", set()),
    ("from bot.formatters.realized_totals import closed_record_partial as p\n"
     "def f(ex):\n    return ex.closed_positions, p(ex)\n", set()),
    ("def f(ex):\n    from bot.utils.website_sync import record_unreadable\n"
     "    return ex.closed_positions if not record_unreadable(ex) else None\n", set()),
    ("def f(ex):\n    return getattr(ex, 'closed_trades_read_failed', 0), ex.closed_positions\n",
     set()),
    # An enclosing function that asks covers a nested reader...
    ("def f(ex):\n    bad = ex.closed_trades_read_failed\n"
     "    def g():\n        return ex.closed_positions\n    return g, bad\n", set()),
    # ...and a nested reader is charged to itself, not to the outer function.
    ("def f(ex):\n    def g():\n        return ex.closed_positions\n    return g\n",
     {"bot/x.py::f.g"}),
    ("class C:\n    def m(self, ex):\n        return ex.closed_positions\n",
     {"bot/x.py::C.m"}),
    # Writing the attribute is not reading the record.
    ("def f(ex):\n    ex.closed_positions = []\n", set()),
    # A helper name that is not one of the readings asks nothing.
    ("def f(ex):\n    return ex.closed_positions, closed_record_whole(ex)\n",
     {"bot/x.py::f"}),
])
def test_the_rule_on_planted_source(code, expect):
    assert _found(code) == expect


def test_a_row_with_no_reason_is_named():
    text = "bot/a.py::f  # a reason\nbot/b.py::g\nbot/c.py::h   #   \n"
    assert reasonless(text) == ["bot/b.py::g", "bot/c.py::h"]


# ── the cards, driven ───────────────────────────────────────────────────

class _Row:
    def __init__(self, pnl, symbol="BTC/USDT", closed_at=None, reason="TP HIT", tid="T-1"):
        from datetime import datetime, timezone
        self.pnl_usd = pnl
        self.symbol = symbol
        self.trade_id = tid
        self.close_reason = reason
        self.closed_at = closed_at or datetime.now(timezone.utc)
        self.direction = "LONG"
        self.entry_price = 100.0
        self.close_price = 110.0
        self.commission = None
        self.gross_pnl = pnl


def _book(rows, partial):
    return types.SimpleNamespace(closed_positions=rows, open_positions=[],
                                 closed_trades_read_failed=partial)


def _run(coro):
    return asyncio.run(coro)


def _classpf(executor):
    from bot.skills.portfolio_commands import PortfolioCommands
    from tests.test_the_record_cards_read_the_callers_book import Stand
    me = Stand({"scope": "own", "executor": executor, "balance": None,
                "total": None, "age_s": None})
    _run(PortfolioCommands._cmd_classpf(me, object(), object()))
    return "\n".join(me.sent)


def test_classpf_does_not_call_an_unreadable_file_an_empty_history():
    said = _classpf(_book([], partial=True))
    assert CLOSED_RECORD_UNREAD in said
    assert "No closed live trades yet" not in said


def test_classpf_on_a_really_empty_record_still_says_so():
    said = _classpf(_book([], partial=False))
    assert "No closed live trades yet" in said and CLOSED_RECORD_UNREAD not in said


def test_classpf_says_a_partial_record_above_the_classes():
    said = _classpf(_book([_Row(5.0)], partial=True))
    assert CLOSED_RECORD_UNREAD in said
    assert said.index(CLOSED_RECORD_UNREAD) < said.index("Crypto")


def test_classpf_scores_an_unpriced_close_neither_way():
    said = _classpf(_book([_Row(5.0, tid="a"), _Row(None, tid="b")], partial=False))
    line = next(x for x in said.splitlines() if "Crypto" in x)
    # One win of one priced close: 100%, not the 50% the zero made it.
    assert "WR 100%" in line and "1 unpriced" in line, line
    assert "net $+5.00" in line


def test_classpf_prints_no_profit_factor_over_no_loss():
    said = _classpf(_book([_Row(5.0, tid="a"), _Row(3.0, tid="b")], partial=False))
    line = next(x for x in said.splitlines() if "Crypto" in x)
    assert "PF <b>—</b>" in line and "∞" not in line, line


def test_classpf_class_nobody_could_price_has_no_figures():
    said = _classpf(_book([_Row(None, tid="a")], partial=False))
    line = next(x for x in said.splitlines() if "Crypto" in x)
    assert "WR —" in line and "net —" in line and "PF <b>—</b>" in line, line


def test_classpf_orders_classes_by_net_and_unpriced_last():
    from bot.core.market_scanner import category_for_symbol
    rows = [_Row(-4.0, symbol="XAG/USDT:USDT", tid="a"), _Row(9.0, symbol="BTC/USDT", tid="b"),
            _Row(None, symbol="AAPL/USDT:USDT", tid="c")]
    cats = [category_for_symbol(r.symbol) for r in rows]
    assert len(set(cats)) == 3, cats      # three classes, or the order says nothing
    said = _classpf(_book(rows, partial=False))
    body = [x for x in said.splitlines() if "<b>" in x and "trades ·" in x]
    order = [next(c for c in cats if f"<b>{c}</b>" in x) for x in body]
    assert order == [cats[1], cats[0], cats[2]], (order, cats)


def test_the_postmortem_does_not_call_an_unreadable_file_no_trades():
    from bot.core.trade_postmortem import postmortem_for
    text = postmortem_for(_book([], partial=True), None)
    assert "could not be read" in text and "No closed live trades" not in text


def test_the_postmortem_scopes_its_last_trade_to_what_read():
    from bot.core.trade_postmortem import postmortem_for
    text = postmortem_for(_book([_Row(5.0)], partial=True), None)
    assert CLOSED_RECORD_UNREAD in text and "most recent one that could be read" in text
    whole = postmortem_for(_book([_Row(5.0)], partial=False), None)
    assert CLOSED_RECORD_UNREAD not in whole


def test_the_postmortem_on_a_miss_says_the_row_may_not_have_read():
    from bot.core.trade_postmortem import postmortem_for
    text = postmortem_for(_book([_Row(5.0)], partial=True), None, symbol="ETH")
    assert "may be one of the rows that could not be read" in text


def test_the_status_card_says_its_day_is_partial():
    from bot.formatters.rich_cards import render_status_card
    kw = dict(mode="LIVE", active=True, equity=100.0, open_positions=0, daily_pnl=1.0,
              drawdown=0.0, max_drawdown=7.0, market_bias="neutral")
    assert CLOSED_RECORD_UNREAD in render_status_card(**kw, record_partial=True)
    assert CLOSED_RECORD_UNREAD not in render_status_card(**kw)


def test_the_translated_sentence_is_the_english_cards_sentence():
    from bot.utils import i18n
    assert i18n.t("closed_record_unread", "en") == CLOSED_RECORD_UNREAD
    missing = [c for c in i18n.SUPPORTED_LANGS
               if not i18n._STRINGS["closed_record_unread"].get(c)]
    assert not missing, missing


# ── the risk and status panes: "Daily PnL" is the day's ────────────────────

def _risk_skill(rows, partial, mode="risk"):
    import re
    from types import SimpleNamespace as NS
    from unittest import mock

    from bot.config import CONFIG
    from bot.skills import skill_registry as SR
    from tests.test_a_card_reads_the_callers_own_breaker_and_drawdown import CALLER, _Risk
    risk = _Risk(0.4)
    state = NS(max_drawdown_pct=0.0, equity_usd=10000.0, open_positions=0,
               total_trades=0, daily_pnl=0.0, win_rate=None)
    pf = NS(snapshot=lambda: state, open_positions=[])
    caller_ex = NS(open_positions=[], closed_positions=rows,
                   closed_trades_read_failed=partial)

    async def eq(uid):
        return 250.0

    engine = NS(risk=risk, risk_for=lambda uid: risk,
                user_portfolios=NS(get=lambda uid, *a: pf),
                cost=NS(snapshot=lambda: NS(llm_cost_usd=0.0, infra_cost_usd=0.0,
                                            operating_cost_usd=0.0)),
                viewer_executor=lambda uid: caller_ex if uid == CALLER else None,
                get_effective_equity_async=eq, _halted=False,
                macro_calendar=NS(evaluate=lambda: NS(state=NS(value="NORMAL"))),
                live_auth_healthy=lambda uid: True, _live_auth_detail={})
    with mock.patch.object(type(CONFIG), "is_live", lambda self: True):
        out = asyncio.run(SR.CheckRiskSkill().execute(engine, user_id=CALLER, mode=mode))
    return re.sub(r"<[^>]+>", "", out)


def _old_and_today():
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    return [_Row(50.0, closed_at=now - timedelta(days=30), tid="old"),
            _Row(5.0, closed_at=now, tid="new")]


@pytest.mark.parametrize("mode", ["risk", "status"])
def test_daily_pnl_on_the_risk_panes_is_the_days_closes(mode):
    card = _risk_skill(_old_and_today(), partial=False, mode=mode)
    line = next(x for x in card.splitlines() if "Daily PnL" in x)
    assert "+5.00" in line and "55.00" not in line, line


@pytest.mark.parametrize("mode", ["risk", "status"])
def test_the_risk_panes_say_when_the_record_is_partial(mode):
    assert CLOSED_RECORD_UNREAD in _risk_skill(_old_and_today(), partial=True, mode=mode)
    assert CLOSED_RECORD_UNREAD not in _risk_skill(_old_and_today(), partial=False, mode=mode)


# ── /daily_report: a partial record is not posted, and a flat close is priced ─

def test_a_partial_record_is_said_on_the_card_and_not_posted(monkeypatch):
    from tests.test_the_daily_report_is_the_days import _live_report
    rows = [_Row(5.0)]

    async def go():
        from bot.skills import portfolio_commands as pc
        orig = pc.closed_record_partial
        monkeypatch.setattr(pc, "closed_record_partial", lambda ex: True)
        try:
            return await _live_report(monkeypatch, rows)
        finally:
            monkeypatch.setattr(pc, "closed_record_partial", orig)
    said, posted = asyncio.run(go())
    assert CLOSED_RECORD_UNREAD in said
    assert posted == []


def test_a_whole_record_is_still_posted(monkeypatch):
    from tests.test_the_daily_report_is_the_days import _live_report
    said, posted = asyncio.run(_live_report(monkeypatch, [_Row(5.0)]))
    assert CLOSED_RECORD_UNREAD not in said and len(posted) == 1


def test_a_flat_close_is_priced_not_unrecorded():
    from bot.warroom.warroom_bot import render_daily_report
    text = render_daily_report({
        "trades": 2, "wins": 1, "losses": 0, "flat": 1, "net_pnl": 5.0,
        "best_trade": "BTC", "best_pnl": 5.0, "worst_trade": "ETH", "worst_pnl": 0.0,
        "risk_status": "Healthy"})["text"]
    assert "50%" in text and "100%" not in text
    assert "no recorded P&amp;L" not in text
    assert "Flat" in text


def test_the_card_and_the_public_post_agree_about_a_flat_day(monkeypatch):
    from tests.test_the_daily_report_is_the_days import _live_report
    said, posted = asyncio.run(_live_report(monkeypatch, [_Row(5.0, tid="a"), _Row(0.0, tid="b")]))
    assert "50%" in said and "Win Rate: <code>50%</code>" in posted[0], (said, posted)


def test_no_flat_row_when_nothing_was_flat():
    from bot.warroom.warroom_bot import render_daily_report
    text = render_daily_report({"trades": 1, "wins": 1, "losses": 0, "net_pnl": 5.0,
                                "risk_status": "Healthy"})["text"]
    assert "Flat" not in text


# ── /portfolio and /livebalance ─────────────────────────────────────────────

def _portfolio(executor):
    from unittest import mock

    from bot.config import CONFIG
    from bot.skills.portfolio_commands import PortfolioCommands
    from tests.test_the_record_cards_read_the_callers_book import Stand
    me = Stand({"scope": "own", "executor": executor, "balance": None,
                "total": None, "age_s": None})

    async def _no_photo(*a, **kw):
        return False
    me._send_photo = _no_photo
    with mock.patch.object(type(CONFIG), "is_live", lambda self: True):
        _run(PortfolioCommands._cmd_portfolio(me, object(), object()))
    return "\n".join(me.sent)


def test_portfolio_says_a_partial_record_above_its_net():
    said = _portfolio(_book([_Row(5.0)], partial=True))
    assert CLOSED_RECORD_UNREAD in said, said
    net_at = min(i for i in (said.find("Net PnL"), said.find("Net P&amp;L")) if i >= 0)
    assert said.index(CLOSED_RECORD_UNREAD) < net_at


def test_portfolio_on_a_whole_record_says_nothing_about_it():
    assert CLOSED_RECORD_UNREAD not in _portfolio(_book([_Row(5.0)], partial=False))


def _wrap(partial):
    from bot.core.proactive_monitor import ProactiveMonitor
    eng = types.SimpleNamespace(
        _live_balance_cache={"free": 50.0, "equity": 50.0},
        live_executor=types.SimpleNamespace(closed_positions=[_Row(5.0)],
                                            closed_trades_read_failed=partial))
    return ProactiveMonitor(eng)._digest_body("wrap")


def test_the_evening_wrap_says_a_partial_record():
    body = _wrap(True)
    assert "Recent closes" in body, body
    assert CLOSED_RECORD_UNREAD in body
    assert CLOSED_RECORD_UNREAD not in _wrap(False)


def _playbook(partial):
    import re
    from types import SimpleNamespace as NS
    from unittest import mock

    from bot.config import CONFIG
    from bot.skills import skill_registry as SR
    from tests.test_a_card_reads_the_callers_own_breaker_and_drawdown import CALLER, _Risk
    risk = _Risk(0.4)
    state = NS(equity_usd=10000.0, open_positions=0, total_trades=0,
               daily_pnl=0.0, win_rate=None, max_drawdown_pct=0.0)
    pf = NS(snapshot=lambda: state, open_positions=[], closed_trades=[],
            trade_history=[], _positions={})
    caller_ex = NS(open_positions=[], closed_positions=[_Row(5.0)],
                   closed_trades_read_failed=partial)

    async def scan():
        return []

    async def eq(uid):
        return 250.0
    engine = NS(risk=risk, risk_for=lambda uid: risk,
                user_portfolios=NS(get=lambda uid, *a: pf), scanner=NS(scan=scan),
                _pending_ideas={}, viewer_executor=lambda uid: caller_ex,
                get_effective_equity_async=eq,
                live_view=lambda uid: {"executor": caller_ex, "balance": {"free": 100.0}})
    with mock.patch.object(type(CONFIG), "is_live", lambda self: True):
        out = asyncio.run(SR.PlaybookSkill().execute(engine, user_id=CALLER))
    return re.sub(r"<[^>]+>", "", out)


def test_the_playbook_says_its_realized_total_is_partial():
    card = _playbook(True)
    assert "Realized PnL" in card, card
    assert CLOSED_RECORD_UNREAD in card
    assert card.index(CLOSED_RECORD_UNREAD) > card.index("Realized PnL")
    assert CLOSED_RECORD_UNREAD not in _playbook(False)


def _scan_head(partial):
    from bot.config import CONFIG
    from bot.skills.skill_registry import ProScanSkill
    from tests.test_the_same_words_run_the_same_scan import _proscan_engine
    book = types.SimpleNamespace(open_positions=[], closed_positions=[_Row(5.0)],
                                 closed_trades_read_failed=partial)
    engine = _proscan_engine(lambda uid: book)
    object.__setattr__(CONFIG, "is_live", lambda: True)
    try:
        out = asyncio.run(ProScanSkill().execute(engine, mode="swing", user_id="web:1"))
    finally:
        object.__delattr__(CONFIG, "is_live")
    return out.split("Timeframe")[0]


def test_the_scan_header_marks_a_total_over_a_partial_record():
    head = _scan_head(True)
    pnl = next(x for x in head.splitlines() if "PnL:" in x)
    assert "+5.00" in pnl and "record partly unread" in pnl, pnl
    assert "record partly unread" not in _scan_head(False)


def test_the_status_command_says_its_day_is_partial():
    from tests.test_the_status_question_is_answered_by_the_status_card import CALLER as S_CALLER
    from tests.test_the_status_question_is_answered_by_the_status_card import _card, _Engine
    eng = _Engine(per_user=True)
    eng.mine.closed_trades_read_failed = True
    assert CLOSED_RECORD_UNREAD in _card(eng, S_CALLER, live=True)
    eng.mine.closed_trades_read_failed = False
    assert CLOSED_RECORD_UNREAD not in _card(eng, S_CALLER, live=True)


def _start_card(tmp_path, partial):
    from unittest.mock import AsyncMock, patch

    from tests.test_registration_flow import OPERATOR, _handler, _locked_bot, _update
    h = _handler(tmp_path)
    h.users.seed_admin(OPERATOR)
    book = types.SimpleNamespace(open_positions=[], _positions={},
                                 closed_positions=[_Row(5.0)],
                                 closed_trades_read_failed=partial)
    h._caller_executor = lambda update: book
    h.engine.resolve_display_equity = AsyncMock(return_value=(100.0, "live"))
    update, ctx = _update(uid=int(OPERATOR))
    p = _locked_bot()
    try:
        with patch("bot.core.live_readiness.mode_label", lambda: "LIVE"):
            _run(h._cmd_start(update, ctx))
    finally:
        p.stop()
    return h.sent[-1]


def test_start_says_its_win_rate_covers_a_partial_record(tmp_path):
    assert CLOSED_RECORD_UNREAD in _start_card(tmp_path / "a", True)
    assert CLOSED_RECORD_UNREAD not in _start_card(tmp_path / "b", False)


def _livebalance(partial):
    from unittest.mock import AsyncMock, MagicMock

    from bot.core.engine import RuneClawEngine
    from bot.skills.telegram_handler import TelegramHandler
    admin = "6307156912"
    h = TelegramHandler(RuneClawEngine())
    h.users.seed_admin(admin)
    sent: list[str] = []
    ex = MagicMock()
    ex.fetch_balance = AsyncMock(return_value={
        "free": 10.0, "used": 5.0, "total": 15.0, "holdings": []})
    ex._get_exchange = AsyncMock(return_value=MagicMock())
    ex.open_positions = []
    ex.closed_positions = [_Row(5.0)]
    ex.closed_trades_read_failed = partial
    h.engine.balance_view_executor = lambda *_a, **_k: ex
    h._get_tg_id = lambda *_a, **_k: admin
    h._reply = AsyncMock(side_effect=lambda *a, **k: sent.append(
        next((x for x in a if isinstance(x, str)), "")))
    update, ctx = MagicMock(), MagicMock()
    update.effective_user = MagicMock(id=int(admin))
    update.effective_user.first_name = "Op"
    update.message = MagicMock(reply_text=AsyncMock(
        side_effect=lambda *a, **k: sent.append(a[0] if a else "")))
    update.callback_query = None
    ctx.args = []
    _run(h._cmd_livebalance(update, ctx))
    return "\n".join(sent)


def test_livebalance_says_its_net_covers_a_partial_record():
    said = _livebalance(True)
    assert "Net PnL" in said, said[:300]
    assert CLOSED_RECORD_UNREAD in said
    assert said.index("Net PnL") < said.index(CLOSED_RECORD_UNREAD)
    assert CLOSED_RECORD_UNREAD not in _livebalance(False)


def test_the_governor_seed_says_when_the_record_is_partial(monkeypatch, caplog):
    import logging

    from bot import config as bot_config
    from bot.core import live_executor
    monkeypatch.setattr(type(bot_config.CONFIG), "is_live", lambda self: True)
    monkeypatch.setattr(live_executor.LiveExecutor, "closed_positions",
                        property(lambda self: []))
    monkeypatch.setattr(live_executor.LiveExecutor, "closed_trades_read_failed",
                        property(lambda self: True))
    from bot.core.engine import RuneClawEngine
    with caplog.at_level(logging.WARNING):
        RuneClawEngine()
    assert any("did not read in full" in r.getMessage() for r in caplog.records)


def test_portfolio_puts_the_partial_record_on_the_picture_caption_too():
    from unittest import mock

    from bot.config import CONFIG
    from bot.skills.portfolio_commands import PortfolioCommands
    from tests.test_the_record_cards_read_the_callers_book import Stand
    captions: list[str] = []

    def drive(partial):
        me = Stand({"scope": "own", "executor": _book([_Row(5.0)], partial),
                    "balance": None, "total": None, "age_s": None})

        async def _photo(update, png, caption):
            captions.append(caption)
            return True
        me._send_photo = _photo
        with mock.patch.object(type(CONFIG), "is_live", lambda self: True), \
                mock.patch("bot.formatters.signal_card.render_stats_card",
                           lambda spec: b"png"):
            _run(PortfolioCommands._cmd_portfolio(me, object(), object()))
        return me.sent
    sent = drive(True)
    assert captions and CLOSED_RECORD_UNREAD in captions[-1], (captions, sent)
    drive(False)
    assert CLOSED_RECORD_UNREAD not in captions[-1]


def test_a_naive_close_time_is_utc_whatever_the_box_zone(monkeypatch):
    """`astimezone` on a naive datetime reads it as LOCAL time, so a naive
    close time is made UTC first. On a box whose zone is UTC the two readings
    agree, so the zone is moved here."""
    import time
    from datetime import datetime, timezone

    from bot.formatters.realized_totals import closes_on_utc_day
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    time.tzset()
    try:
        now = datetime(2026, 9, 26, 1, 0, tzinfo=timezone.utc)
        # 00:30 today, written naive. Read as UTC it is today; read as Tokyo
        # local time it is 15:30 UTC yesterday.
        row = types.SimpleNamespace(closed_at=datetime(2026, 9, 26, 0, 30))
        today, _ = closes_on_utc_day([row], now)
        assert today == [row]
        late = types.SimpleNamespace(closed_at=datetime(2026, 9, 25, 23, 30))
        today, _ = closes_on_utc_day([late], now)
        assert today == []
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()


def test_a_close_stamped_tomorrow_is_not_todays():
    from datetime import datetime, timedelta, timezone

    from bot.formatters.realized_totals import closes_on_utc_day
    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    row = types.SimpleNamespace(closed_at=now + timedelta(days=1))
    assert closes_on_utc_day([row], now) == ([], 0)
