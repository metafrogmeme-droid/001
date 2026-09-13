""""Is the bot running?" is a question about the ENGINE.

The web aliased the `status` intent to `get_portfolio` and dispatched it at
confidence 1.0, so it was answered with the account card — which carries no
halt, breaker, tick, drawdown, mode or market-bias claim of any kind — and
gated under the `portfolio` permission rather than its own. Aliasing a
question to the nearest answer is a confident wrong answer; `get_orders` is
the same lesson one intent over.

There was no seam to answer it with, so this is the "when there is no seam,
make one" case: `status_card_text` is `/status`'s reading, and both surfaces
render it.

And the reading itself had the leak `viewer_executor` exists to close.
`_cmd_status` read the CALLER's equity one line above the OPERATOR's executor,
so a viewer saw their own equity beside somebody else's open-position count —
and Daily PnL was a single ratio spanning two accounts, the operator's dollars
over the caller's equity. The fixture below is deliberately ASYMMETRIC: a
symmetric one is a fixture that cannot tell the two books apart.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

OPERATOR = "111"
CALLER = "999"


class _Ex:
    def __init__(self, opens, closes, venue="Bitget"):
        self.open_positions = opens
        self.closed_positions = closes
        # DIFFERENT venues, deliberately. The first draft gave both books
        # "Bitget" and a mutation reading the venue off `live_executor`
        # survived every assertion — a symmetric fixture is a fixture that
        # cannot tell the two books apart, which is this file's own docstring.
        self._venue = NS(display_name=venue, quote="USDT")


def _closed(pnl):
    from datetime import datetime, timezone
    return NS(pnl_usd=pnl, close_price=1.0, entry_price=1.0,
              closed_at=datetime.now(timezone.utc), symbol="BTC/USDT",
              quantity=1.0, direction="long", fill_source="venue")


class _Engine:
    """Two books that disagree about everything a symmetric one would hide."""

    def __init__(self, *, per_user: bool):
        self.operator = _Ex(["a"] * 7, [_closed(4242.0)], venue="Bitget")
        self.mine = _Ex(["z"], [_closed(1.0)], venue="Bybit")
        self._per_user = per_user
        self.live_executor = self.operator
        self.risk = NS(drawdown_status=lambda: {},
                       streak_state=lambda: {"latched": False})
        self.pending_ideas = []
        self.user_portfolios = NS(get=lambda uid: NS(
            snapshot=lambda: NS(equity_usd=10_000.0, open_positions=0,
                                daily_pnl=0.0, max_drawdown_pct=0.0)))
        self._analyze_capacity = None
        self.scanner = NS(_session_dropped=None)
        self._last_phase_timeout = None
        self._last_tick_error = None

    def live_view(self, user_id="", max_age_s=900.0):
        if not self._per_user:
            return {"scope": "operator", "executor": self.operator}
        if str(user_id) == OPERATOR:
            return {"scope": "operator", "executor": self.operator}
        if str(user_id) == CALLER:
            return {"scope": "own", "executor": self.mine}
        return {"scope": "none", "executor": None}

    async def resolve_display_equity(self, user_id=""):
        return 555.0, "live"

    def phase_headroom(self):
        return None

    def position_watch(self):
        return None


def _handler(engine):
    from bot.skills.start_commands import StartCommands
    h = StartCommands.__new__(StartCommands)
    h.engine = engine
    h._status_market_bias = lambda: "NEUTRAL"
    h._tick_liveness = lambda e: (False, None)
    h._tick_age_s = lambda e: None
    return h


def _card(engine, user_id, *, surface="telegram", live=False):
    h = _handler(engine)
    with patch("bot.skills.start_commands.CONFIG") as c:
        c.simulation_mode = not live
        c.is_live.return_value = live
        c.risk.max_drawdown_pct = 10.0
        c.risk.max_daily_loss_pct = 5.0
        return asyncio.run(h.status_card_text(user_id, "en", surface=surface))


def _line(card, label):
    for ln in card.splitlines():
        if label in ln:
            return ln
    raise AssertionError(f"no {label!r} line in:\n{card}")


# ── 1. the leak ─────────────────────────────────────────────────────────────

def test_the_caller_sees_their_own_open_positions_not_the_operators():
    # RED HERRING: the equity IS the caller's and always was, so a card that
    # reads right at a glance was mixing two accounts in one Capital block.
    card = _card(_Engine(per_user=True), CALLER, live=True)
    assert "1" == _line(card, "Open Positions").split(":")[-1].strip()
    assert "7" not in _line(card, "Open Positions")


def test_the_daily_figure_is_one_accounts_dollars_over_its_own_equity():
    """It was the operator's $4242 over the caller's $555 — a single ratio
    spanning two books, printed beside a daily-loss cap."""
    card = _card(_Engine(per_user=True), CALLER, live=True)
    pnl = _line(card, "Daily PnL")
    assert "+0.2%" in pnl, pnl              # 1.00 / 555.00
    assert "764" not in pnl, pnl            # 4242.00 / 555.00


def test_the_operator_still_sees_their_own_book():
    card = _card(_Engine(per_user=True), OPERATOR, live=True)
    assert "7" == _line(card, "Open Positions").split(":")[-1].strip()


def test_a_caller_with_no_account_to_view_is_told_so_not_told_zero():
    """`scope: none` is not a flat book. Zero open positions is a reading; no
    account is the absence of one, and they are different sentences."""
    card = _card(_Engine(per_user=True), "nobody", live=True)
    assert "unavailable" in _line(card, "Open Positions")
    assert "0" not in _line(card, "Open Positions").split(":")[-1]


def test_a_genuinely_flat_book_still_prints_zero():
    """The other half, and the one a three-valued fix usually breaks."""
    eng = _Engine(per_user=True)
    eng.mine.open_positions = []
    card = _card(eng, CALLER, live=True)
    assert "0" == _line(card, "Open Positions").split(":")[-1].strip()


def test_the_venue_line_names_the_callers_own_venue_only_where_the_door_exists():
    tg = _card(_Engine(per_user=True), CALLER, live=True, surface="telegram")
    web = _card(_Engine(per_user=True), CALLER, live=True, surface="web")
    assert "/venue to switch" in tg
    # The CALLER's venue. Both books answered "Bitget" in the first draft, so
    # a line read off the operator's executor was indistinguishable.
    assert "Bybit" in tg and "Bitget" not in tg, tg
    assert "Bybit" in web and "/venue" not in web, (
        "a card that names a command is claiming the command does something")


def test_the_headline_names_the_venue_the_card_describes():
    """It was the literal "Bitget" on a product that routes to Bybit and BingX
    too, printed three sections above the real venue. A name nobody read is a
    claim nobody checked."""
    card = _card(_Engine(per_user=True), CALLER, live=True)
    head = card.splitlines()[2]
    assert "Bybit" in head and "Bitget" not in head, head


def test_the_headline_omits_a_venue_it_could_not_read():
    eng = _Engine(per_user=True)
    card = _card(eng, "nobody", live=True)
    head = card.splitlines()[2]
    assert "Bybit" not in head and "Bitget" not in head, head


# ── 2. the seam is the card, and the command is four lines ──────────────────

def test_the_command_renders_the_seam_and_nothing_else():
    """A second copy of the reading is a second answer. `_cmd_status` may
    gather nothing of its own."""
    import ast
    import inspect
    import textwrap

    from bot.skills.start_commands import StartCommands
    src = textwrap.dedent(inspect.getsource(StartCommands._cmd_status))
    assert "status_card_text" in src
    tree = ast.parse(src)
    # ATTRIBUTES, not only calls: `self.engine.live_executor` is a read, and a
    # mutation that put it back survived a version of this that looked at
    # `Call.func` alone.
    reads = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    reads |= {ast.unparse(n.func) for n in ast.walk(tree)
              if isinstance(n, ast.Call)}
    assert "live_executor" not in reads, reads
    assert not {r for r in reads if "entry_gate" in r or "live_view" in r}


# ── 3. the web answers the question it was asked ────────────────────────────

def _web(monkeypatch, role="viewer", denial=None):
    from bot.nlp.conversation_store import ConversationStore
    from bot.nlp.intent_router import IntentRouter
    from bot.web import user_gateway as ug
    monkeypatch.setattr(ug, "_guard_user", lambda *a, **kw: None)
    monkeypatch.setattr(ug, "_is_admin_id", lambda h, uid: False)
    monkeypatch.setattr(ug, "build_profile_note", lambda p: "")
    eng = _Engine(per_user=True)
    asked: list[str] = []
    h = NS(intent_router=IntentRouter(),
           registry=NS(get=lambda n: asked.append(n) or None),
           conversations=ConversationStore(),
           users=NS(get_tier=lambda uid: "elite", is_authorized=lambda uid: True,
                    get_role=lambda uid: role, get=lambda uid: {"role": role},
                    permission_denial=lambda uid, perm: denial),
           _llm_chat=None,
           status_card_text=_handler(eng).status_card_text)
    return ug, h, eng, asked


def _turn(ug, h, eng, text):
    import json

    async def _json():
        return {"telegram_id": CALLER, "text": text}

    req = NS(app={"tg_handler": h, "engine": eng}, json=_json, headers={},
             remote="1.2.3.4")
    with patch("bot.skills.start_commands.CONFIG") as c:
        c.simulation_mode = False
        c.is_live.return_value = True
        c.risk.max_drawdown_pct = 10.0
        c.risk.max_daily_loss_pct = 5.0
        resp = asyncio.run(ug._chat_turn(req))
    return resp, json.loads(resp.text)


@pytest.mark.parametrize("text", ["status", "bot status", "is the bot running",
                                  "engine status", "whats the status"])
def test_the_web_answers_status_with_the_engine_card(monkeypatch, text):
    # RED HERRING: the portfolio card reads like an answer — it has an equity
    # figure and a position count. It makes no engine claim at all.
    ug, h, eng, asked = _web(monkeypatch)
    _resp, body = _turn(ug, h, eng, text)
    assert body["intent"] == "status"
    for must in ("Engine", "State", "Mode", "Market Bias", "Pending Ideas"):
        assert must in body["reply_html"], (text, must, body["reply_html"][:400])
    assert "get_portfolio" not in asked, (
        "the question was answered by the account card again")


def test_the_web_records_the_status_card_as_status(monkeypatch):
    ug, h, eng, _asked = _web(monkeypatch)
    _turn(ug, h, eng, "status")
    rec = "\n".join(m.content for m in h.conversations.get_recent(CALLER, limit=10)
                    if m.role == "assistant")
    assert "[status] result:" in rec
    assert "[get_portfolio]" not in rec


def test_the_web_gates_status_under_its_own_permission(monkeypatch):
    """`pending` holds `help` and NOT `status`, so answering a routed intent
    above the permission check would remove the gate. A website signup is
    auto-provisioned `paper`, which DOES hold it — the gate is the only thing
    between a stranger and the engine card."""
    ug, h, eng, _asked = _web(monkeypatch, role="pending", denial="role")
    resp, body = _turn(ug, h, eng, "status")
    assert resp.status == 403
    assert body["error"] == "insufficient_permissions"
    rec = "\n".join(m.content for m in h.conversations.get_recent(CALLER, limit=10)
                    if m.role == "assistant")
    assert "[status] NOT RUN" in rec and "role" in rec


def test_a_status_reading_that_raises_is_not_answered_with_a_card(monkeypatch):
    ug, h, eng, _asked = _web(monkeypatch)

    async def _boom(*a, **kw):
        raise RuntimeError("engine down")

    h.status_card_text = _boom
    resp, body = _turn(ug, h, eng, "status")
    assert resp.status == 200
    assert "nothing was measured" in body["reply_html"].lower()
    rec = "\n".join(m.content for m in h.conversations.get_recent(CALLER, limit=10)
                    if m.role == "assistant")
    assert "[status] FAILED" in rec


def test_status_left_the_alias_table(monkeypatch):
    """Guards the guard: every assertion above passes trivially if the intent
    stopped reaching the branch at all."""
    import inspect

    from bot.web import user_gateway as ug
    src = inspect.getsource(ug._chat_turn)
    i = src.index("_INTENT_ALIASES = {")
    assert '"status"' not in src[i:src.index("}", i)]
