"""Every door that reaches `confirm_trade` answers the live-permission question.

`engine.confirm_trade` is paper-or-live by `CONFIG.is_live()` and by nothing
else — not by the name of the caller, not by the name of the skill. So every
call site is a potential live-money door, and H-18 is the check that keeps a
caller who may not trade live from opening a real position on the SHARED
OPERATOR account.

**The guard that stood here was satisfied by its own comment.**
`test_web_and_scan_authorization.py::test_scan_confirm_checks_live_permission`
asserted `"_can_trade_live" in fn` over RAW source — and the H-18 comment six
lines above the refusal spells `_can_trade_live`. Driven, DELETING the refusal
outright left it green. So did `if False and ...`. Mutating all three Telegram
sites at once left 137 targeted tests passing, `guard_lint` 12/12, `red_team`
30/30 refused and `authority_red_team` 12/12 denied; only `mypy_gate` reacted,
and only as a baseline IMPROVEMENT (`union-attr: 180 -> 177`) whose documented
remedy is one `--update`, i.e. one command re-records it and nobody learns an
auth gate went missing.

That is this repo's own FALSE-PASS trap — *"a comment that quotes the string it
forbids is indistinguishable from the code doing it"* — landing on an
authorization gate. Everything here reads `code_only()` for exactly that
reason, and `test_a_comment_alone_does_not_satisfy_the_rule` plants the trap to
prove the reading is immune to it.

**And it covered one site of seven.** An AST walk finds seven `confirm_trade`
calls. A list of the three somebody happened to name is the `/setllm`
ten-of-eleven shape, so the rule is DERIVED from the calls themselves and a
door added tomorrow fails by name rather than inheriting the permission.

Two instruments, and the division is the one this repo keeps arriving at: the
DRIVES below are the proof that the refusal RUNS (a scan cannot see
reachability, which is the one thing it is being asked about), and the RULE
says where — it is the backstop that makes the drives' narrowness safe.
"""

from __future__ import annotations

import ast
import pathlib
import re
import types
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.ext import ContextTypes

import bot.config as bot_config
import bot.skills.scan_skill as scan_skill
from tests.source_scan import code_only

REPO = pathlib.Path(__file__).resolve().parents[1]
BASELINE = REPO / "tests" / "confirm_trade_gate_baseline.txt"

#: Names that ANSWER the live-permission question for a call site. Each is a
#: reading with its own refusal, not a word that merely appears near one:
#:
#:   _can_trade_live          the H-18 per-user live permission itself
#:   _is_admin_id             the web bridge's admin reading
#:   _web_live_decision       the fail-closed web live gate
#:   _authorize_web_live_trade  the enforce-mode Authority Envelope
#:   _auto_confirm_batch      the tick path's own gate (`auto_confirm_refusal`)
#:   _auto_confirm_suppressed the force-scan path's copy of that same reading
#:
#: Widening this tuple LOOSENS the rule, so a new entry is a decision: it must
#: name something that can REFUSE, and `test_every_gate_name_can_refuse` drives
#: that rather than taking the name's word for it.
GATE_READINGS = (
    "_can_trade_live",
    "_is_admin_id",
    "_web_live_decision",
    "_authorize_web_live_trade",
    "_auto_confirm_batch",
    "_auto_confirm_suppressed",
)


# ── The reading ───────────────────────────────────────────────────────────


def _enclosing_functions(tree: ast.AST, node: ast.AST) -> list:
    """The function scope chain above `node`, innermost first."""
    parents: dict = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    chain, cur = [], node
    while cur in parents:
        cur = parents[cur]
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            chain.append(cur)
    return chain


def _rel(path: pathlib.Path, root: pathlib.Path) -> str:
    """Repo-relative where possible; root-relative for a planted tree."""
    for base in (REPO, root.parent):
        try:
            return str(path.relative_to(base))
        except ValueError:
            continue
    return str(path)


def confirm_trade_sites(root: pathlib.Path | None = None) -> list[dict]:
    """Every `<recv>.confirm_trade(...)` call under `bot/`, with its gate.

    Read from `code_only()` source: a COMMENT naming a gate is not a gate, and
    that distinction is the entire reason this file exists.
    """
    root = root or (REPO / "bot")
    out: list[dict] = []
    for path in sorted(root.rglob("*.py")):
        try:
            src = code_only(path.read_text())
            tree = ast.parse(src)
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "confirm_trade"):
                continue
            chain = _enclosing_functions(tree, node)
            scope = "\n".join(ast.unparse(fn) for fn in chain)
            out.append({
                "path": _rel(path, root),
                "line": node.lineno,
                "func": chain[0].name if chain else "<module>",
                "gates": tuple(g for g in GATE_READINGS if g in scope),
            })
    return out


def _baseline_rows(path: pathlib.Path | None = None) -> dict[str, str]:
    """`path::func` -> reason. A row with no reason is not a row.

    The file is a PARAMETER rather than a module global read through a patch:
    `tests/` has no `__init__.py`, so this module exists twice in `sys.modules`
    and a string-target monkeypatch would edit the copy nobody is running.
    """
    rows: dict[str, str] = {}
    key = None
    for raw in (path or BASELINE).read_text().splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if not raw.startswith(" ") and "::" in raw:
            head, _, reason = raw.partition("—")
            key = head.strip()
            rows[key] = reason.strip()
        elif key:
            rows[key] += " " + raw.strip()
    return rows


def _ungated(sites: list[dict]) -> set[str]:
    return {f"{s['path']}::{s['func']}" for s in sites if not s["gates"]}


# The three rules are FUNCTIONS of (sites, rows) rather than expressions inside
# their assertions, because on a healthy tree each answers the empty list and a
# mutation of it changes no verdict. The mutation round reported exactly that:
# emptying any of them survived a green suite. They are driven on planted
# inputs below, where each is the only thing in play.

def new_ungated(sites: list[dict], rows: dict) -> list[str]:
    """Doors with no gate that nobody has written a reason for."""
    return sorted(_ungated(sites) - set(rows))


def stale_rows(sites: list[dict], rows: dict) -> list[str]:
    """Baseline rows that no longer name an ungated door."""
    return sorted(set(rows) - _ungated(sites))


def reasonless_rows(rows: dict) -> list[str]:
    """Rows whose reason is too short to be one."""
    return sorted(k for k, v in rows.items() if len(v) < 40)


# ── The rule, over the real tree ──────────────────────────────────────────


def test_no_new_ungated_confirm_trade_door():
    new = new_ungated(confirm_trade_sites(), _baseline_rows())
    assert not new, (
        "These reach engine.confirm_trade with no live-permission reading "
        "anywhere in their enclosing scope chain:\n  "
        + "\n  ".join(new)
        + f"\n\nEither gate them (one of {', '.join(GATE_READINGS)}) or add a row "
          f"to {BASELINE.name} WITH the reason it is safe.")


def test_the_baseline_has_no_stale_rows():
    stale = stale_rows(confirm_trade_sites(), _baseline_rows())
    assert not stale, (
        "These baseline rows no longer name an ungated door — the site gained a "
        "gate or went away. Delete them in the SAME commit, so a stale row "
        "cannot hide the next ungated door:\n  " + "\n  ".join(stale))


def test_every_baseline_row_carries_a_reason():
    rows = _baseline_rows()
    assert rows, "the baseline parsed to nothing — did its format change?"
    reasonless = reasonless_rows(rows)
    assert not reasonless, (
        "A row with no reason is an acquittal nobody can check:\n  "
        + "\n  ".join(reasonless))


def test_the_known_live_doors_are_all_gated():
    """The three doors a caller can actually reach today, named.

    Derived membership rather than a hand-written list of what is gated: every
    site NOT in the baseline must carry a gate, which the first test enforces.
    This one pins that the live-facing doors are present at all, so a door
    that silently STOPS calling confirm_trade (and starts calling something
    else) does not read as a pass.

    There were FOUR, and the fourth stopped on purpose: `scan_skill`'s
    `callback_confirm_reject` placed a market order at the scan price with a
    flat 3%/6% the card never showed. The scan card's ✅ is `confirm:<id>` on
    the card's own registered idea now, through `_handle_callback` below, and
    an old `scan_confirm:` payload is refused
    (`test_an_old_scan_payload_reaches_no_confirm_in_any_mode`).
    """
    by_func = {f"{s['path']}::{s['func']}": s for s in confirm_trade_sites()}
    assert "bot/skills/scan_skill.py::callback_confirm_reject" not in by_func, (
        "the scan card's old door reaches confirm_trade again")
    for key in ("bot/skills/telegram_handler.py::_handle_message",
                "bot/skills/callback_handler.py::_handle_callback",
                "bot/web/user_gateway.py::handle_trade_confirm"):
        assert key in by_func, f"{key} no longer reaches confirm_trade — did it move?"
        assert "_can_trade_live" in by_func[key]["gates"], (
            f"{key} reaches confirm_trade without the H-18 reading")


# ── The rule, over PLANTED trees ──────────────────────────────────────────
#
# On the real tree every door but one is gated, so a mutation of the RULE
# changes no verdict there. A rule the real inputs cannot reach is a claim that
# there is a check, so each branch is measured where it is the only thing in
# play.


def _plant(tmp_path: pathlib.Path, body: str) -> list[dict]:
    pkg = tmp_path / "bot"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "planted.py").write_text(body)
    return confirm_trade_sites(pkg)


def test_prose_alone_does_not_satisfy_the_rule(tmp_path):
    """THE defect, planted: the gate named in prose and absent from the code.

    The mutation round corrected this test. Its first draft planted a COMMENT
    and claimed that reading raw source would fail it — and swapping
    `code_only()` for `path.read_text()` SURVIVED, because the scope is built
    with `ast.unparse`, which drops comments by construction. A comment fixture
    could never have discriminated the two readings, so the assertion named a
    branch it could not reach.

    A DOCSTRING is what tells them apart: `ast.unparse` keeps it and
    `code_only` blanks it. Both are planted, because the comment case is still
    the shape the ORIGINAL guard died of (it used `ast.get_source_segment`,
    which returns raw text comments and all) and is worth holding.
    """
    comment_only = _plant(tmp_path, '''
async def door(self, engine, uid):
    # H-18: this path checks _can_trade_live before it confirms.
    return await engine.confirm_trade("t1", user_id=uid)
''')
    assert comment_only[0]["gates"] == (), "a COMMENT satisfied the rule"

    docstring_only = _plant(tmp_path, '''
async def door(self, engine, uid):
    """H-18: this path checks _can_trade_live before it confirms."""
    return await engine.confirm_trade("t1", user_id=uid)
''')
    assert docstring_only[0]["gates"] == (), (
        "a DOCSTRING naming _can_trade_live satisfied the rule — the reading is "
        "back on raw source and the false pass is back with it")


def test_a_real_gate_in_the_same_function_satisfies_it(tmp_path):
    sites = _plant(tmp_path, '''
async def door(self, engine, uid):
    if not self._can_trade_live(uid):
        return "refused"
    return await engine.confirm_trade("t1", user_id=uid)
''')
    assert sites[0]["gates"] == ("_can_trade_live",)


def test_a_gate_in_an_OUTER_function_satisfies_it(tmp_path):
    """The chain is walked, not just the innermost scope.

    scan_skill's own gate sits in the enclosing handler, so a rule that read
    only the innermost function would accuse the site it was written for.
    """
    sites = _plant(tmp_path, '''
async def outer(self, engine, uid):
    if not self._can_trade_live(uid):
        return "refused"
    async def inner():
        return await engine.confirm_trade("t1", user_id=uid)
    return await inner()
''')
    assert sites[0]["gates"] == ("_can_trade_live",)


def test_a_gate_in_a_SIBLING_function_does_not_satisfy_it(tmp_path):
    """A gate somewhere else in the file is not a gate on this path.

    The quiet direction: a file-level scan would acquit every door in any file
    that gates one of them.
    """
    sites = _plant(tmp_path, '''
async def gated(self, uid):
    if not self._can_trade_live(uid):
        return "refused"

async def ungated(self, engine, uid):
    return await engine.confirm_trade("t1", user_id=uid)
''')
    ungated = [s for s in sites if s["func"] == "ungated"]
    assert ungated and ungated[0]["gates"] == ()


def test_two_calls_in_one_file_are_two_sites(tmp_path):
    """One-of-N: a second door in a gated file is still its own door."""
    sites = _plant(tmp_path, '''
async def a(self, engine, uid):
    if not self._can_trade_live(uid):
        return "refused"
    return await engine.confirm_trade("t1", user_id=uid)

async def b(self, engine, uid):
    return await engine.confirm_trade("t2", user_id=uid)
''')
    assert len(sites) == 2
    assert {s["func"]: bool(s["gates"]) for s in sites} == {"a": True, "b": False}


def test_a_reasonless_planted_row_fails_the_reason_rule(tmp_path):
    """The reason rule, where it is the only thing in play.

    Against the real baseline every row carries a reason, so a mutation of this
    rule changes no verdict there.
    """
    planted = tmp_path / "baseline.txt"
    planted.write_text("# header\nbot/x.py::door — short\n")
    rows = _baseline_rows(planted)
    assert rows == {"bot/x.py::door": "short"}
    assert len(rows["bot/x.py::door"]) < 40, (
        "the planted row was supposed to be too short to be a reason")


def test_every_gate_name_can_refuse():
    """A name in GATE_READINGS must be something that REFUSES, not a word.

    Widening the tuple loosens the rule, so the membership is driven: each name
    must be defined in the tree and its body must contain a refusal shape. A
    name that merely reads and returns a figure would acquit every door it
    appears near.
    """
    src = "\n".join(
        code_only(p.read_text())
        for p in (REPO / "bot").rglob("*.py")
        if p.is_file())
    for name in GATE_READINGS:
        assert re.search(rf"def {re.escape(name)}\b", src), (
            f"{name} is in GATE_READINGS and is defined nowhere in bot/ — a "
            f"gate vocabulary that names a function nobody wrote acquits every "
            f"door that spells it")


# ── The drives: the refusal RUNS ──────────────────────────────────────────
#
# The rule above proves a gate is PRESENT in the scope. Only a drive proves it
# is REACHED, and reachability is the one thing the old guard was being asked
# about. Both arms every time: a refusal-only assertion passes just as happily
# against a handler that does nothing at all.


def _scan_confirm_fixture(result_text: str = "EXECUTED"):
    """An OLD scan card's ✅: `scan_confirm:<sym>:<dir>:<price>`, which carries
    none of the levels the card showed."""
    query = MagicMock()
    query.data = "scan_confirm:DYDX/USDT:LONG:0.20462"
    query.answer = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    query.message = MagicMock()
    query.message.reply_text = AsyncMock()

    update = MagicMock()
    update.callback_query = query
    update.effective_user = types.SimpleNamespace(id=999)

    engine = MagicMock()
    engine._pending_ideas = {}
    engine._pending_atr = {}
    engine.confirm_trade = AsyncMock(return_value=result_text)

    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.bot_data = {"engine": engine}
    return update, context, engine, query


def _handler_stub(*, admin: bool, live_ok: bool):
    h = MagicMock()
    h._is_admin = MagicMock(return_value=admin)
    h._can_trade_live = MagicMock(return_value=live_ok)
    h._live_refusal_key = MagicMock(return_value="live_not_enabled")
    h._lang = MagicMock(return_value="en")
    return h


def _sent(query) -> str:
    return "\n".join(
        str(c.args[0]) if c.args else str(c.kwargs.get("text", ""))
        for c in query.message.reply_text.call_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [True, False])
@pytest.mark.parametrize("admin,live_ok", [(False, False), (False, True), (True, False)])
async def test_an_old_scan_payload_reaches_no_confirm_in_any_mode(live, admin, live_ok,
                                                                  monkeypatch):
    """The old door is CLOSED for everybody: it places nothing whoever taps it,
    in either mode, and says so."""
    update, context, engine, query = _scan_confirm_fixture()
    context.bot_data["telegram_handler"] = _handler_stub(admin=admin, live_ok=live_ok)
    monkeypatch.setattr(type(bot_config.CONFIG), "is_live", lambda self: live)

    await scan_skill.callback_confirm_reject(update, context)

    assert engine.confirm_trade.await_count == 0
    assert "nothing was placed" in _sent(query)


# The H-18 drives for the door the scan card's money goes through NOW -- the
# `confirm:` branch of `_handle_callback`, tapped with the id of the idea the
# card registered. They used to drive `scan_confirm:`, which is refused above.


def _confirm_tap(*, admin: bool, live_ok: bool, live: bool,
                 answer: object = "\u2705 LIVE LONG DYDX/USDT filled"):
    """One tap of a scan card's ✅ through the REAL dispatcher. `answer` is
    what `confirm_trade` returns, or an exception it raises."""
    import asyncio

    from bot.skills import scan_skill as _ss
    from bot.skills.telegram_handler import TelegramHandler
    from bot.utils.user_store import ROLE_PERMISSIONS

    uid = "999"
    rows = [{"sym": "DYDX/USDT", "dir": "LONG", "price": 0.20462, "atr": 0.004,
             "entry": 0.2034, "sl": 0.1946, "tp": 0.2166, "score": 0.8}]
    engine = types.SimpleNamespace(_pending_ideas={}, _pending_atr={},
                                   live_executor=types.SimpleNamespace(_positions={}))
    _ss.register_scan_offers(engine, rows)
    _hdr, buttons = _ss.scan_action_rows(rows, {"blocked": False}, uid)
    data = buttons[0][0][1]
    engine.confirm_trade = (AsyncMock(side_effect=answer) if isinstance(answer, Exception)
                            else AsyncMock(return_value=answer))

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

    async def _noop(*a, **k):
        return None

    replies: list = []
    h = TelegramHandler.__new__(TelegramHandler)
    h.engine = engine
    h.users = _Users()
    h.forwarder = types.SimpleNamespace(post_trade_opened=_noop)
    h._limiter = types.SimpleNamespace(allow=lambda u: True)
    h._check_auth = lambda update: True
    h._is_admin = MagicMock(return_value=admin)
    h._can_trade_live = MagicMock(return_value=live_ok)
    h._live_refusal_key = lambda: "live_not_enabled"
    h._lang = lambda update: "en"

    async def _send(update, text, **kw):
        replies.append(text)

    h._send = _send
    query = types.SimpleNamespace(
        data=data, answer=_noop,
        message=types.SimpleNamespace(edit_reply_markup=_noop, chat_id=int(uid)))
    update = types.SimpleNamespace(
        callback_query=query,
        effective_user=types.SimpleNamespace(id=int(uid), first_name="X"),
        effective_chat=types.SimpleNamespace(id=int(uid)))
    orig = type(bot_config.CONFIG).is_live
    type(bot_config.CONFIG).is_live = lambda self: live
    try:
        asyncio.new_event_loop().run_until_complete(
            h._handle_callback(update, types.SimpleNamespace()))
    finally:
        type(bot_config.CONFIG).is_live = orig
    return engine, h, "\n".join(replies)


def test_live_mode_refuses_a_caller_who_may_not_trade_live():
    """The arm that matters: refused, and confirm_trade never awaited."""
    engine, _h, said = _confirm_tap(admin=False, live_ok=False, live=True)
    assert engine.confirm_trade.await_count == 0, (
        "a caller who may not trade live reached confirm_trade from a scan card "
        "— on the default config that is a real order on the SHARED OPERATOR "
        "account")
    assert "\U0001f512" in said, "no refusal was shown to the caller"


def test_live_mode_lets_a_permitted_caller_through():
    """The other arm. Without it, a handler that does nothing passes above."""
    engine, _h, _said = _confirm_tap(admin=False, live_ok=True, live=True)
    assert engine.confirm_trade.await_count == 1, (
        "a permitted caller was refused — the gate is refusing everybody, which "
        "a refusal-only assertion cannot tell from working")


def test_an_admin_is_not_refused():
    engine, _h, _said = _confirm_tap(admin=True, live_ok=False, live=True)
    assert engine.confirm_trade.await_count == 1


def test_paper_mode_does_not_consult_the_gate():
    """The gate is a LIVE-mode reading; paper must be byte-identical to before."""
    engine, h, _said = _confirm_tap(admin=False, live_ok=False, live=False)
    assert engine.confirm_trade.await_count == 1
    assert h._can_trade_live.call_count == 0, (
        "paper mode consulted the live permission — the gate moved out from "
        "under its own `if is_live()`")


def test_the_growth_rule_names_an_ungated_door(tmp_path):
    """Driven on planted input: on the real tree this answers [] either way."""
    sites = [{"path": "bot/x.py", "func": "door", "gates": ()},
             {"path": "bot/y.py", "func": "gated", "gates": ("_can_trade_live",)}]
    assert new_ungated(sites, {}) == ["bot/x.py::door"]
    assert new_ungated(sites, {"bot/x.py::door": "a reason"}) == []


def test_the_staleness_rule_names_a_row_that_stopped_applying():
    sites = [{"path": "bot/x.py", "func": "door", "gates": ("_can_trade_live",)}]
    assert stale_rows(sites, {"bot/x.py::door": "was ungated once"}) == [
        "bot/x.py::door"]
    assert stale_rows(sites, {}) == []


def test_the_reason_rule_names_a_row_with_no_reason():
    assert reasonless_rows({"bot/x.py::door": "short"}) == ["bot/x.py::door"]
    assert reasonless_rows({"bot/x.py::door": "x" * 40}) == []
