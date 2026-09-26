"""The web positions panel shows the caller's own live book, or none.

`GET /gateway/positions` resolved its executor with `_executor_for`, the
ORDER-PLACEMENT reading. Under PER_USER_LIVE_ENABLED that answers the
operator's executor for a caller with no linked keys ("so behaviour never
silently breaks" -- right for placing an order, wrong for reading one), so a
non-operator's positions panel listed the operator's live positions, symbol,
direction and stop protection, as their own. `viewer_executor` is the reading
every other card already uses: it answers None there, and the panel says no
executor is attached to the account.

These drive the real handler with the engine's real `_executor_for`,
`viewer_executor` and `_is_operator_user`, so the test fails if either the
handler or the view reading drifts.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

import bot.core.engine as eng_mod
import bot.web.user_gateway as ug
from bot.config import CONFIG
from bot.core.engine import RuneClawEngine

STRANGER = "222"
OPERATOR = "111"


class _Users:
    def get(self, uid):
        return {"role": "admin"} if str(uid) == OPERATOR else {"role": "trader"}


def _live(symbol: str) -> SimpleNamespace:
    return SimpleNamespace(
        trade_id=f"T-{symbol}", symbol=symbol, direction="LONG", entry_price=100.0,
        quantity=1.0, cost_usd=10.0, stop_loss=95.0, take_profit=110.0, leverage=10,
        sl_order_id="sl-1", tp_order_id="tp-1", status="open", opened_at=None,
        strategy_type="swing", signal_type="momentum_confluence")


def _engine(operator_positions):
    eng = SimpleNamespace(live_executor=SimpleNamespace(open_positions=operator_positions),
                          _user_store=_Users(), _user_executors={},
                          user_portfolios=SimpleNamespace(get=lambda _u: None))
    for name in ("_executor_for", "viewer_executor", "_is_operator_user"):
        setattr(eng, name, getattr(RuneClawEngine, name).__get__(eng))
    return eng


def _per_user(monkeypatch, on: bool):
    class _Cfg:
        per_user_live_enabled = on

        def is_live(self):
            return True

        def __getattr__(self, name):
            return getattr(CONFIG, name)
    cfg = _Cfg()
    monkeypatch.setattr(eng_mod, "CONFIG", cfg)
    monkeypatch.setattr(ug, "CONFIG", cfg)
    store = SimpleNamespace(get=lambda _u: None, get_venue=lambda _u: "bitget")
    monkeypatch.setattr("bot.core.exchange_credentials.get_credential_store", lambda: store)


def _drive(monkeypatch, eng, tg_id):
    app = web.Application()
    app["engine"] = eng
    app["tg_handler"] = SimpleNamespace(users=SimpleNamespace(register=lambda *a, **k: None))
    monkeypatch.setattr(ug, "_guard_user", lambda *a, **k: None)
    req = make_mocked_request("GET", f"/positions?telegram_id={tg_id}", app=app)
    resp = asyncio.run(ug.handle_positions(req))
    return resp.status, json.loads(resp.text)


def test_a_caller_with_no_keys_is_not_shown_the_operators_book(monkeypatch):
    _per_user(monkeypatch, True)
    eng = _engine([_live("BTC/USDT:USDT")])
    assert eng._executor_for(STRANGER) is eng.live_executor, "the fallback this guards"
    status, body = _drive(monkeypatch, eng, STRANGER)
    assert status == 200
    assert body["positions"] == [] and body["count"] == 0
    assert body["book_read"] is False, "no executor is attached to this account"


def test_the_operator_still_reads_the_operators_book(monkeypatch):
    _per_user(monkeypatch, True)
    eng = _engine([_live("BTC/USDT:USDT")])
    status, body = _drive(monkeypatch, eng, OPERATOR)
    assert status == 200 and body["book_read"] is True
    assert [p["symbol"] for p in body["positions"]] == ["BTC/USDT:USDT"]


def test_a_linked_caller_reads_their_own_book(monkeypatch):
    _per_user(monkeypatch, True)
    eng = _engine([_live("BTC/USDT:USDT")])
    own = SimpleNamespace(open_positions=[_live("ETH/USDT:USDT")])
    eng._executor_for = lambda _u: own           # their OWN executor, keys linked
    status, body = _drive(monkeypatch, eng, STRANGER)
    assert [p["symbol"] for p in body["positions"]] == ["ETH/USDT:USDT"]


def test_single_account_mode_is_unchanged(monkeypatch):
    """With per-user live off there is one live account, and every read card
    shows it -- `viewer_executor`'s documented single-account behaviour. This
    slice changes which reading the panel asks, not that decision."""
    _per_user(monkeypatch, False)
    eng = _engine([_live("BTC/USDT:USDT")])
    status, body = _drive(monkeypatch, eng, STRANGER)
    assert body["book_read"] is True and body["count"] == 1



def _order_placement_reads(source: str) -> list[int]:
    """Line numbers of every `<x>._executor_for(...)` call in `source` whose
    answer is READ, from code only, so a comment naming the call is not one.

    One use is not a read, and the web-live gate needs it: asking which
    account an ORDER would run on, to refuse the operator's. That question
    belongs to the order-placement reading, and its answer is compared by
    identity (`is None`, `is engine.live_executor`) and never opened. So a call
    bound to a name whose every use is an `is`/`is not` comparison is exempt,
    the operator-account ratchet's own rule ("an identity comparison is not a
    read"). Any other use of that name -- an attribute, an argument, an `==`
    that can run `__eq__` -- counts as a read of the book.
    """
    import ast

    from tests.source_scan import code_only
    tree = ast.parse(code_only(source))
    parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}

    def scope(node):
        while node in parents:
            node = parents[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return node
        return tree

    def identity_only(call) -> bool:
        bound = parents.get(call)
        if not (isinstance(bound, ast.Assign) and len(bound.targets) == 1
                and isinstance(bound.targets[0], ast.Name)):
            return False
        name = bound.targets[0].id
        uses = [n for n in ast.walk(scope(call))
                if isinstance(n, ast.Name) and n.id == name
                and isinstance(n.ctx, ast.Load)]
        return bool(uses) and all(
            isinstance(parents.get(u), ast.Compare)
            and all(isinstance(op, (ast.Is, ast.IsNot)) for op in parents[u].ops)
            for u in uses)

    return [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "_executor_for" and not identity_only(n)]


def test_no_web_gateway_read_asks_the_order_placement_reading():
    """A rule over the class, not the one site. `bot/web/` places no order
    through `_executor_for` -- orders reach the venue through
    `engine.confirm_trade` -- so a call to it under `bot/web/` whose answer is
    opened can only be a READ, and a read answered by the order-placement
    reading is this defect: the operator's book, handed to a caller with no
    keys. The operator-account ratchet looks for `engine.live_executor`, and
    this spelling walked past it. The web-live gate's identity check is the
    one exempt use (see `_order_placement_reads`)."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    hits = [f"{path.relative_to(root)}:{line}"
            for path in sorted((root / "bot" / "web").rglob("*.py"))
            for line in _order_placement_reads(path.read_text(encoding="utf-8"))]
    assert hits == [], hits


def test_the_web_live_gate_is_the_identity_use_and_is_still_seen():
    """The exemption is not a hole the size of the file: the gate's call is
    the one it acquits, and it still counts as a call."""
    import ast
    from pathlib import Path

    from tests.source_scan import code_only
    src = (Path(__file__).resolve().parents[1] / "bot" / "web"
           / "user_gateway.py").read_text(encoding="utf-8")
    calls = [n for n in ast.walk(ast.parse(code_only(src)))
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "_executor_for"]
    assert len(calls) == 1
    assert _order_placement_reads(src) == []


@pytest.mark.parametrize("body,flagged", [
    ("    ex = engine._executor_for(t)\n"
     "    if ex is None:\n        return 1\n"
     "    if ex is engine.live_executor:\n        return 2\n", False),
    ("    ex = engine._executor_for(t)\n"
     "    if ex is None:\n        return 1\n"
     "    return ex.open_positions\n", True),
    ("    ex = engine._executor_for(t)\n"
     "    return show(ex)\n", True),
    ("    ex = engine._executor_for(t)\n"
     "    return ex == engine.live_executor\n", True),
    ("    ex = engine._executor_for(t)\n"
     "    if ex is not None:\n        pass\n"
     "    ex = other()\n    return ex.balance\n", True),
    ("    return engine._executor_for(t).open_positions\n", True),
    ("    ex = engine._executor_for(t)\n", True),
    ("    a, b = engine._executor_for(t)\n"
     "    return a is None\n", True),
])
def test_only_a_pure_identity_use_is_acquitted(body, flagged):
    planted = "def h(t):\n" + body
    assert bool(_order_placement_reads(planted)) is flagged, planted


def test_a_read_of_the_same_name_elsewhere_is_not_this_calls_read():
    # Uses are counted in the call's own function: `ex` in another function
    # is another variable.
    planted = ("def h(t):\n"
               "    ex = engine._executor_for(t)\n"
               "    return ex is None\n"
               "def g(ex):\n"
               "    return ex.open_positions\n")
    assert _order_placement_reads(planted) == []


def test_the_rule_sees_a_planted_call_and_not_a_comment():
    """Driven on planted source, because the real tree has no instance of what
    the rule forbids, and a rule no input reaches is a claim of a check."""
    planted = ("async def h(request):\n"
               "    # engine._executor_for(tg_id) is the wrong reading here\n"
               "    ex = engine._executor_for(tg_id)\n"
               "    other = engine.viewer_executor(tg_id)\n")
    assert _order_placement_reads(planted) == [3]
