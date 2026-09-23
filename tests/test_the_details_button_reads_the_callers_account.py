"""The Details button's fallback read the OPERATOR's venue account, whoever tapped.

`pos_details_<ident>` looks the position up in the caller's own records first --
`_caller_executor(update)` for the live book, `user_portfolios.get(user_id)` for
the paper one -- and when neither holds it, it asks the VENUE directly, because
local tracking can go stale while the exchange still holds the position
(`test_pos_details_stale_sync.py` records that incident). The venue it asked was
`self.engine.live_executor._get_exchange()`: the operator's, for every caller.

`_caller_executor`'s own docstring says why that is wrong in so many words --
*"For the VIEW/CLOSE layer that fallback would leak the operator's positions to
a non-operator user, so here we return None in that case"* -- and the branch
computed exactly that `None` four lines above and then walked past it. The
isolation's whole point is the `None`, and the fallback ran precisely when the
isolation had fired: `pos_match is None` is what a caller with no live book
always reaches.

**The close branch, four hundred lines down, already had it right**, with a
comment naming the reason: *"Use the caller's executor/exchange so this can't
reach into the operator's (or another user's) account."* The tracked lookup in
this branch was converted and the fallback beside it was not -- *fixing two
left the third*, in one handler.

**Reachable only under PER_USER_LIVE_ENABLED, and that is the configuration
the isolation exists for.** With it off every caller resolves to the shared
operator executor on purpose (single-account mode), so reading the operator's
venue there is the caller's own account and nothing changes -- driven below.
With it on, a caller with no linked account whose paper position has closed
(a stale card is the ordinary case) or who taps a crafted `pos_details_BTCUSDT`
was rendered the operator's position -- entry, quantity, leverage and the
margin in dollars -- under a card headed LIVE, with a Close button tagged to
them.

**And one row is not the class.** Every read of `engine.live_executor` in a
surface that serves a caller is now declared in
`tests/operator_account_reads_baseline.txt` with its reason, two-way: a new
read fails until somebody says why the operator's account is the right one to
read there, and a row whose read has gone fails as stale. A row claiming the
read sits behind an ADMIN gate is checked against the function's own body, so
the reason cannot outlive the gate; a helper's row names the function that is
its only caller, and that is checked too.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib
from types import SimpleNamespace

import bot.config as bot_config
from bot.formatters import signal_card
from bot.skills import callback_handler as cb_mod
from bot.skills import chart_renderer
from bot.skills.telegram_handler import TelegramHandler
from tests.source_scan import handler_sources

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASELINE = ROOT / "tests" / "operator_account_reads_baseline.txt"

UID = "424242"
OPERATOR_CHAT = "999999"

#: The operator's untracked venue position. A figure nobody else holds, so its
#: appearance anywhere in what the caller was sent is the leak, whatever
#: wording carries it.
OP_MARGIN = 3150.0
OP_MARK = "$3,150.00"
#: The caller's own untracked position, sized so that no figure on either card
#: coincides with the other's -- the first draft used $250, which is exactly
#: the operator position's P&L at this mark, so "the caller's figure is on the
#: card" was satisfied by the operator's card.
CALLER_MARGIN = 180.0
CALLER_MARK = "$180.00"


class _Exchange:
    """A venue account. Records every `fetch_positions` it is asked."""

    def __init__(self, name, positions):
        self.name = name
        self.positions = positions
        self.asked: list = []

    async def fetch_positions(self):
        self.asked.append(self.name)
        return list(self.positions)

    async def fetch_ticker(self, sym):
        return {"last": 63500.0}


class _Executor:
    def __init__(self, exchange, open_positions=()):
        self._exchange_obj = exchange
        self.open_positions = list(open_positions)
        self.closed_positions: list = []
        self._positions: dict = {}

    async def _get_exchange(self):
        return self._exchange_obj


def _venue_row(margin):
    return {
        "symbol": "BTC/USDT:USDT", "contracts": 0.5, "side": "long",
        "entryPrice": 63000.0, "initialMargin": margin, "leverage": 10,
        "timestamp": None,
    }


def _tap(*, per_user_live, linked, live=True, ident="BTCUSDT"):
    """One Details tap through the REAL dispatcher.

    Returns (what the caller was sent, [which venue accounts were asked]).
    """
    op_ex = _Exchange("operator", [_venue_row(OP_MARGIN)])
    user_ex = _Exchange("caller", [_venue_row(CALLER_MARGIN)])
    operator = _Executor(op_ex)
    mine = _Executor(user_ex)
    sent: list = []

    # The caller's paper book no longer holds the symbol -- the stale card.
    portfolio = SimpleNamespace(open_positions=[], mark_to_market=lambda m: None)

    class _Users:
        def get(self, tid):
            return {"role": "trader", "authorized": True}

        def has_permission(self, tid, perm):
            return True

        def live_trading_revoked(self, *a, **k):
            return False

        def can_trade_live(self, tid):
            return True

        def is_authorized(self, *a, **k):
            return True

        # `test_user_admission.py` holds any double carrying `has_permission`
        # to the four methods the web gateway calls on the real store.
        def is_admitted(self, *a, **k):
            return True

        def permission_denial(self, *a, **k):
            return None

        def get_tier(self, *a, **k):
            return "free"

        def register(self, *a, **k):
            return None

    async def _market_exchange():
        return _Exchange("market", [])

    h = TelegramHandler.__new__(TelegramHandler)
    h.engine = SimpleNamespace(
        live_executor=operator,
        # Mirrors engine._executor_for: a linked caller gets their own
        # executor, everyone else falls back to the operator's.
        _executor_for=lambda uid="": mine if linked else operator,
        user_portfolios=SimpleNamespace(get=lambda uid: portfolio),
        get_exchange=_market_exchange,
    )
    h.users = _Users()
    h._limiter = SimpleNamespace(allow=lambda uid: True)
    h._check_auth = lambda update: True
    h._is_admin = lambda update: False
    h._lang = lambda update: "en"

    async def _send(update, text, **kw):
        sent.append(text)

    async def _send_photo(update, png, caption="", **kw):
        sent.append(caption or "")

    h._send = _send
    h._send_photo = _send_photo

    async def _noop(*a, **k):
        return None

    query = SimpleNamespace(
        data=f"pos_details_{ident}",
        message=SimpleNamespace(edit_reply_markup=_noop, chat_id=int(UID)),
        answer=_noop,
    )
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=int(UID), first_name="X"),
        effective_chat=SimpleNamespace(id=int(UID)),
    )

    async def _analysis(exchange, symbol, timeframe="1h"):
        return {"price": 63500.0}

    # The card and the chart are PICTURES of the same figures the text card
    # carries, and the chart fetches its own candles over the network. Both
    # answer nothing here, so the branch takes its text path and every figure
    # it chose to show is in `sent`.
    async def _no_chart(*a, **k):
        return None

    orig_live = type(bot_config.CONFIG).is_live
    orig_fad = cb_mod.fetch_analysis_data
    orig_card = signal_card.render_position_card
    orig_chart = chart_renderer.build_position_chart
    signal_card.render_position_card = lambda data: None
    chart_renderer.build_position_chart = _no_chart
    prev_pul = bot_config.CONFIG.per_user_live_enabled
    prev_chat = bot_config.CONFIG.telegram.chat_id
    type(bot_config.CONFIG).is_live = lambda self: live
    cb_mod.fetch_analysis_data = _analysis
    # Frozen fields: `object.__setattr__` is the only door, restored in the
    # `finally` because it sits outside monkeypatch's bookkeeping. The
    # operator's chat id is SOMEBODY ELSE -- `_uid_matches` answers True for an
    # empty expected id, which would make every caller the operator and leave
    # `_caller_executor` unable to return None: a fixture that cannot produce
    # the state it names measures nothing.
    object.__setattr__(bot_config.CONFIG, "per_user_live_enabled", per_user_live)
    object.__setattr__(bot_config.CONFIG.telegram, "chat_id", OPERATOR_CHAT)
    try:
        asyncio.new_event_loop().run_until_complete(
            h._handle_callback(update, SimpleNamespace()))
    finally:
        type(bot_config.CONFIG).is_live = orig_live
        cb_mod.fetch_analysis_data = orig_fad
        signal_card.render_position_card = orig_card
        chart_renderer.build_position_chart = orig_chart
        object.__setattr__(bot_config.CONFIG, "per_user_live_enabled", prev_pul)
        object.__setattr__(bot_config.CONFIG.telegram, "chat_id", prev_chat)
    return sent, op_ex.asked + user_ex.asked


class TestTheFallbackReadsTheCallersAccount:
    def test_an_unlinked_caller_is_never_shown_the_operators_position(self):
        sent, asked = _tap(per_user_live=True, linked=False)
        assert "operator" not in asked, (
            "the Details fallback asked the OPERATOR's venue account for a "
            "caller `_caller_executor` had just resolved to no live book")
        assert not any(OP_MARK in s for s in sent), sent

    def test_the_unlinked_caller_is_told_the_position_is_not_there(self):
        # Refusing the operator's book must not turn into silence: the caller
        # still gets an answer about the symbol they tapped.
        sent, _ = _tap(per_user_live=True, linked=False)
        assert sent, "the tap was answered with nothing at all"

    def test_a_linked_caller_still_finds_their_own_untracked_position(self):
        # The fallback exists for stale local tracking, and a linked trader's
        # stale position is exactly what it is for -- on THEIR venue account.
        sent, asked = _tap(per_user_live=True, linked=True)
        assert asked == ["caller"], asked
        assert any(CALLER_MARK in s for s in sent), sent
        assert not any(OP_MARK in s for s in sent), sent

    def test_single_account_mode_is_unchanged(self):
        # PER_USER_LIVE off: every caller's account IS the operator's, on
        # purpose, and the fallback keeps finding the untracked position.
        sent, asked = _tap(per_user_live=False, linked=False)
        assert asked == ["operator"], asked
        assert any(OP_MARK in s for s in sent), sent

    def test_paper_mode_never_asks_a_venue(self):
        _, asked = _tap(per_user_live=True, linked=True, live=False)
        assert asked == [], asked


# ---------------------------------------------------------------------------
# The class: every read of the operator's account in a caller-serving surface.
# ---------------------------------------------------------------------------

def _is_operator_executor(node) -> bool:
    """`<x>.engine.live_executor` or `engine.live_executor`."""
    if not (isinstance(node, ast.Attribute) and node.attr == "live_executor"):
        return False
    v = node.value
    return ((isinstance(v, ast.Attribute) and v.attr == "engine")
            or (isinstance(v, ast.Name) and v.id == "engine"))


def surface_files() -> list:
    """The Telegram handler's files and the web gateway's.

    Derived, not listed: the handler's files come from its MRO, so a mixin
    split out tomorrow is covered before anybody remembers to add it.
    """
    files = [pathlib.Path(p) for p in handler_sources()]
    files += sorted((ROOT / "bot" / "web").glob("*.py"))
    return files


def operator_reads(sources: dict) -> dict:
    """`{"path::function": count}` of reads of the operator's executor.

    An IDENTITY comparison (`ex is self.engine.live_executor`) reads nothing
    from the account -- it is how `_caller_executor` and `/livebalance` tell
    the operator's book from somebody else's -- so it is not a read.
    """
    out: dict = {}
    for rel, src in sources.items():
        tree = ast.parse(src)
        parents: dict = {}
        for p in ast.walk(tree):
            for c in ast.iter_child_nodes(p):
                parents[c] = p
        for n in ast.walk(tree):
            if not _is_operator_executor(n):
                continue
            par = parents.get(n)
            if (isinstance(par, ast.Compare)
                    and all(isinstance(o, (ast.Is, ast.IsNot)) for o in par.ops)):
                continue
            q, fn = n, "<module>"
            while q in parents:
                q = parents[q]
                if isinstance(q, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    fn = q.name
                    break
            key = f"{rel}::{fn}"
            out[key] = out.get(key, 0) + 1
    return out


def read_baseline(text: str) -> dict:
    """`{"path::function": reason}`. A row is `path::function  <reason>`."""
    rows: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, reason = line.partition("  ")
        rows[key.strip()] = reason.strip()
    return rows


def _real_sources() -> dict:
    return {p.relative_to(ROOT).as_posix(): p.read_text()
            for p in surface_files()}


def _functions(src: str) -> dict:
    """Every def in a file by name. A name defined twice is refused, not guessed."""
    out: dict = {}
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.setdefault(node.name, []).append(node)
    return out


def _is_admin_gated(fn) -> bool:
    """The function refuses a non-admin itself: `@guard("admin")`, or a
    `self._is_admin(...)` call in its own body (nested defs not descended)."""
    for dec in fn.decorator_list:
        if (isinstance(dec, ast.Call) and getattr(dec.func, "id", None) == "guard"
                and dec.args and isinstance(dec.args[0], ast.Constant)
                and dec.args[0].value == "admin"):
            return True
    stack = list(fn.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "_is_admin"):
            return True
        stack.extend(ast.iter_child_nodes(node))
    return False


def _callers(sources: dict, name: str) -> set:
    """Functions that call `self.<name>(...)` anywhere in the surface."""
    out: set = set()
    for src in sources.values():
        tree = ast.parse(src)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for n in ast.walk(fn):
                if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                        and n.func.attr == name and fn.name != name):
                    out.add(fn.name)
    return out


def rule_violations(sources: dict, baseline: dict) -> list:
    """Every way the surface and the baseline disagree, as sentences."""
    out: list = []
    reads = operator_reads(sources)
    for key in sorted(reads):
        if key not in baseline:
            out.append(f"{key}: reads the OPERATOR's account and no row says why "
                       f"-- a caller's account is `_caller_executor(update)` or "
                       f"`engine.viewer_executor(uid)`")
    for key, reason in sorted(baseline.items()):
        if key not in reads:
            out.append(f"{key}: stale row -- it no longer reads the operator's "
                       f"account; delete it")
            continue
        if not reason:
            out.append(f"{key}: a row with no reason")
            continue
        path, _, name = key.partition("::")
        defs = _functions(sources[path]).get(name, [])
        if reason.startswith("admin:"):
            if len(defs) != 1 or not _is_admin_gated(defs[0]):
                out.append(f"{key}: the row says the read is admin-gated and the "
                           f"function carries no admin gate")
        elif reason.startswith("via "):
            # The OWNER reads nothing itself, so it has no row of its own --
            # a row for it would be stale. Its gate is read off its body.
            owner = reason[len("via "):].split(":", 1)[0].strip()
            callers = _callers(sources, name)
            owner_defs = _functions(sources[path]).get(owner, [])
            if callers != {owner}:
                out.append(f"{key}: the row says only {owner} calls it; "
                           f"callers are {sorted(callers)}")
            elif len(owner_defs) != 1 or not _is_admin_gated(owner_defs[0]):
                out.append(f"{key}: {owner} carries no admin gate")
    return out


class TestEveryOperatorReadIsDeclared:
    def test_the_surface_agrees_with_the_baseline(self):
        problems = rule_violations(_real_sources(),
                                   read_baseline(BASELINE.read_text()))
        assert not problems, "\n".join(problems)

    def test_the_details_branch_is_not_on_it(self):
        # The defect this file exists for. `_handle_callback` answers every
        # button for every admitted caller; nothing in it may read the
        # operator's account on a caller's behalf.
        reads = operator_reads(_real_sources())
        assert not any(k.endswith("::_handle_callback") for k in reads), reads

    def test_the_walk_reaches_the_surfaces_it_claims(self):
        # A walk that reads nothing passes every assertion while checking none.
        names = {p.name for p in surface_files()}
        assert {"telegram_handler.py", "callback_handler.py",
                "user_gateway.py"} <= names, names
        assert len(operator_reads(_real_sources())) >= 5


class TestTheRuleItselfBites:
    """Driven on planted source: the real tree answers nothing for most of
    these, and a rule no input can reach is a claim that there is a check."""

    PLANT = (
        "class H:\n"
        "    async def _cmd_a(self, update):\n"
        "        if not self._is_admin(update):\n"
        "            return\n"
        "        return self.engine.live_executor.open_positions\n"
        "    async def _cmd_b(self, update):\n"
        "        return self.engine.live_executor.open_positions\n"
        "    def _helper(self):\n"
        "        return self.engine.live_executor._venue\n"
        "    async def _cmd_c(self, update):\n"
        "        if not self._is_admin(update):\n"
        "            return\n"
        "        return self._helper()\n"
        "    def _same(self, ex):\n"
        "        return ex is self.engine.live_executor\n"
    )

    def _run(self, baseline, plant=None):
        return rule_violations({"p.py": plant or self.PLANT}, baseline)

    GOOD = {
        "p.py::_cmd_a": "admin: planted",
        "p.py::_cmd_b": "market: planted",
        "p.py::_helper": "via _cmd_c: planted",
    }

    def test_a_declared_surface_is_clean(self):
        assert self._run(dict(self.GOOD)) == []

    def test_a_row_for_the_owner_itself_is_stale(self):
        # _cmd_c reads nothing; its gate is read off its body, never a row.
        base = dict(self.GOOD, **{"p.py::_cmd_c": "admin: planted"})
        assert any("_cmd_c: stale row" in p for p in self._run(base))

    def test_a_helper_whose_owner_lost_its_gate_is_reported(self):
        plant = self.PLANT.replace(
            "    async def _cmd_c(self, update):\n"
            "        if not self._is_admin(update):\n"
            "            return\n",
            "    async def _cmd_c(self, update):\n")
        assert plant != self.PLANT
        assert any("_helper: _cmd_c carries no admin gate" in p
                   for p in self._run(dict(self.GOOD), plant))

    def test_an_undeclared_read_is_reported(self):
        base = dict(self.GOOD)
        base.pop("p.py::_cmd_b")
        assert any("_cmd_b: reads the OPERATOR" in p for p in self._run(base))

    def test_a_stale_row_is_reported(self):
        base = dict(self.GOOD, **{"p.py::_gone": "market: x"})
        assert any("_gone: stale row" in p for p in self._run(base))

    def test_an_identity_comparison_is_not_a_read(self):
        assert "p.py::_same" not in operator_reads({"p.py": self.PLANT})

    def test_an_admin_row_over_an_ungated_function_is_reported(self):
        base = dict(self.GOOD, **{"p.py::_cmd_b": "admin: says so"})
        assert any("_cmd_b: the row says the read is admin-gated" in p
                   for p in self._run(base))

    def test_a_helper_reached_from_an_ungated_caller_is_reported(self):
        plant = self.PLANT + (
            "    async def _cmd_d(self, update):\n"
            "        return self._helper()\n")
        problems = self._run(dict(self.GOOD), plant)
        assert any("_helper: the row says only _cmd_c calls it" in p
                   for p in problems), problems

    def test_a_guard_decorator_is_an_admin_gate(self):
        # The command-gate vocabulary has two spellings and every real row here
        # happens to use the in-body one, so the decorator is planted: a
        # mutation that stopped reading it survived the first round.
        plant = ("class H:\n"
                 "    @guard(\"admin\")\n"
                 "    async def _cmd_f(self, update):\n"
                 "        return self.engine.live_executor.open_positions\n"
                 "    @guard(\"portfolio\")\n"
                 "    async def _cmd_g(self, update):\n"
                 "        return self.engine.live_executor.open_positions\n")
        problems = rule_violations({"p.py": plant},
                                   {"p.py::_cmd_f": "admin: planted",
                                    "p.py::_cmd_g": "admin: planted"})
        assert problems == ["p.py::_cmd_g: the row says the read is "
                            "admin-gated and the function carries no admin "
                            "gate"], problems

    def test_a_bare_engine_name_is_read_too(self):
        # The web gateway holds the engine as a local, not `self.engine`, so
        # the read there is spelled `engine.live_executor`. No such read
        # exists today, which is exactly why it is planted: a spelling the
        # rule cannot see is one the next web handler walks through.
        plant = ("async def handle(request):\n"
                 "    engine = request.app['engine']\n"
                 "    return engine.live_executor.open_positions\n")
        assert operator_reads({"w.py": plant}) == {"w.py::handle": 1}

    def test_a_reasonless_row_is_reported(self):
        base = dict(self.GOOD, **{"p.py::_cmd_b": ""})
        assert any("_cmd_b: a row with no reason" in p for p in self._run(base))

    def test_a_nested_admin_check_does_not_gate_the_outer_function(self):
        plant = (
            "class H:\n"
            "    async def _cmd_e(self, update):\n"
            "        def inner():\n"
            "            return self._is_admin(update)\n"
            "        return self.engine.live_executor.open_positions\n")
        problems = rule_violations({"p.py": plant},
                                   {"p.py::_cmd_e": "admin: claimed"})
        assert any("_cmd_e: the row says the read is admin-gated" in p
                   for p in problems), problems
