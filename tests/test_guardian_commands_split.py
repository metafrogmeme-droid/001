"""The second slice out of the handler: the Guardian command group.

`bot/skills/guardian_commands.py` holds /policy, /twin, /sentinel, /escape,
/guardian, /approvals, /xray, the policy-bind callback and the two sync HTTP
helpers the X-ray commands run through `to_thread`, as a MIXIN the handler
class inherits. Their behaviour is covered where it always was
(`test_guardian_chat_tools`, `test_policy_mode_reports_the_bind`,
`test_escape_plan_failure_is_not_a_flat_book`); this file pins the SPLIT:

  1. the ten definitions live in the mixin, and the handler reaches the very
     same objects through inheritance — and still REGISTERS them, because a
     method that moved out of the file and out of `build_app` at the same
     time would be present, tested, and dispatched by nothing (#999);
  2. the mixin defines nothing the host defines. Its host contract is a set
     of declarations under TYPE_CHECKING, and every one of them names a
     method the handler really has, with the parameters it really takes — a
     stub for a method the handler lacks type-checks fine and fails at the
     first tap;
  3. every `self.<name>(` the mixin calls resolves on the handler, which is
     `test_handler_methods_exist` pointed at the new file;
  4. the mixin imports nothing from the handler. The user-facing exception
     scrubber it needs moved to bot/utils/exc_text.py, and the handler
     re-exports it under the old name so the call sites and suites that
     reach `_safe_exc_text` through the handler did not move;
  5. two cards are driven through a bare host, so an import that resolved to
     a stale copy of `t`, `CONFIG` or `html` shows here rather than in
     production — and the three-outcome honesty of /twin survives the move.
"""
from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import bot.skills.guardian_commands as gc
import bot.skills.telegram_handler as th
import bot.utils.exc_text as ex
from bot.skills.guardian_commands import GuardianCommands
from bot.skills.telegram_handler import TelegramHandler
from tests.source_scan import code_only

MIXIN = Path(__file__).resolve().parents[1] / "bot" / "skills" / "guardian_commands.py"
HANDLER = Path(__file__).resolve().parents[1] / "bot" / "skills" / "telegram_handler.py"

MOVED = ("_cmd_policy", "_cmd_twin", "_cmd_sentinel", "_cmd_escape", "_web_get_json",
         "_web_post_json", "_cmd_approvals", "_cmd_xray", "_cmd_guardian",
         "_apply_policy_callback")
COMMANDS = ("policy", "twin", "sentinel", "escape", "guardian", "approvals", "xray")

HANDLER_SRC = code_only(HANDLER.read_text(encoding="utf-8"))
MIXIN_SRC = code_only(MIXIN.read_text(encoding="utf-8"))


def _mixin_class() -> ast.ClassDef:
    for node in ast.parse(MIXIN.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.ClassDef) and node.name == "GuardianCommands":
            return node
    raise AssertionError("GuardianCommands is not a top-level class of the mixin module")


def _split_body(cls: ast.ClassDef):
    """(methods defined for real, {stub name: parameter names}) — the second
    set being everything declared under `if TYPE_CHECKING:`."""
    real, stubs = set(), {}
    for stmt in cls.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            real.add(stmt.name)
        elif isinstance(stmt, ast.If) and getattr(stmt.test, "id", "") == "TYPE_CHECKING":
            for sub in stmt.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    stubs[sub.name] = [a.arg for a in sub.args.args]
                elif isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
                    stubs[sub.target.id] = None
    return real, stubs


# ── 1. moved, reached, registered ─────────────────────────────────────────

def test_the_handler_reaches_the_mixins_own_objects():
    for name in MOVED:
        assert getattr(TelegramHandler, name) is getattr(GuardianCommands, name), name
        assert name not in vars(TelegramHandler), f"{name} is defined twice"


def test_the_definitions_left_the_handler():
    for name in MOVED:
        assert f"def {name}(" in MIXIN_SRC, name
        assert f"def {name}(" not in HANDLER_SRC, name


def test_the_mixin_defines_exactly_the_group():
    real, _ = _split_body(_mixin_class())
    assert real == set(MOVED), sorted(real ^ set(MOVED))


def test_the_handler_still_registers_every_command_and_the_callback():
    build = inspect.getsource(TelegramHandler.build_app)
    for cmd in COMMANDS:
        assert f'("{cmd}", self._cmd_{cmd})' in build, (
            f"/{cmd} moved out of the file and out of build_app — present, unreached")
    flat = " ".join(HANDLER_SRC.split())
    assert "self._apply_policy_callback(update, data)" in flat, (
        "the policy confirm/cancel buttons no longer reach the bind")


# ── 2. the host contract is declarations, and they are true ──────────────

def test_the_host_contract_names_only_what_the_handler_provides():
    _, stubs = _split_body(_mixin_class())
    assert stubs, "the TYPE_CHECKING block is empty — the contract is undeclared"
    init = inspect.getsource(TelegramHandler.__init__)
    for name, params in stubs.items():
        if params is None:
            # an attribute: the handler's __init__ must set it
            assert re.search(rf"self\.{re.escape(name)}\s*=", init), (
                f"{name} is declared as provided by the host and __init__ never sets it")
            continue
        assert name in vars(TelegramHandler), (
            f"{name} is declared as provided by the host and the handler does not define it")
        real = list(inspect.signature(getattr(TelegramHandler, name)).parameters)
        assert params == real, f"{name}: stub takes {params}, the handler takes {real}"


def test_the_stubs_have_no_bodies():
    """A body under TYPE_CHECKING would still be dead at runtime — the
    danger is the reader believing the mixin implements what it declares."""
    cls = _mixin_class()
    for stmt in cls.body:
        if isinstance(stmt, ast.If) and getattr(stmt.test, "id", "") == "TYPE_CHECKING":
            for sub in stmt.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    assert all(isinstance(b, ast.Expr) for b in sub.body), sub.name


def test_the_mixin_never_shadows_the_redaction_chokepoint():
    assert "_send" not in vars(GuardianCommands)
    assert "_is_admin" not in vars(GuardianCommands)
    assert "_lang" not in vars(GuardianCommands)


# ── 3. every self-call resolves ──────────────────────────────────────────

def test_every_self_method_call_in_the_mixin_is_defined_on_the_handler():
    src = inspect.getsource(GuardianCommands)
    called = set(re.findall(r"self\.(\w+)\(", src))
    assert called, "the scan found no calls — extractor broken"
    assigned = set(re.findall(r"self\.(\w+)\s*=", src))
    missing = sorted(c for c in called if c not in set(dir(TelegramHandler)) | assigned)
    assert missing == [], f"called on self in the mixin, defined nowhere: {missing}"


# ── 4. no import back into the handler ───────────────────────────────────

def test_the_mixin_imports_nothing_from_the_handler():
    tree = ast.parse(MIXIN.read_text(encoding="utf-8"))
    for node in ast.walk(tree):          # lazy imports inside methods included
        if isinstance(node, ast.ImportFrom):
            assert "telegram_handler" not in (node.module or ""), ast.unparse(node)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert "telegram_handler" not in alias.name, ast.unparse(node)


def test_safe_exc_text_is_one_object_under_every_name():
    assert th._safe_exc_text is ex._safe_exc_text
    assert gc._safe_exc_text is ex._safe_exc_text
    assert th._TG_TOKEN_RE is ex._TG_TOKEN_RE
    assert "def _safe_exc_text(" not in HANDLER_SRC
    assert "def _safe_exc_text(" in code_only(inspect.getsource(ex))


# ── 5. the cards render through the mixin ────────────────────────────────

def _host(sent: list, admin: bool = True, lang: str = "en") -> TelegramHandler:
    h = TelegramHandler.__new__(TelegramHandler)

    async def _send(update, text, reply_markup=None, edit=False):
        sent.append(str(text))

    h._send = _send
    h._is_admin = lambda update: admin
    h._lang = lambda update: lang
    return h


def _update():
    return SimpleNamespace(effective_user=SimpleNamespace(id=1),
                           effective_chat=SimpleNamespace(id=1),
                           message=SimpleNamespace(text="/guardian"), callback_query=None)


@pytest.mark.asyncio
async def test_the_guardian_console_renders_through_the_mixin():
    sent: list = []
    h = _host(sent)
    h.engine = SimpleNamespace(guardian_status=lambda: {
        "posture": "low",
        "flags": {"intent_policy": True, "firewall": False, "digital_twin": True},
        "chain": {"ok": True, "length": 12}, "policy": {"label": "x"},
        "twin": {"risk": "none", "position_count": 2},
        "sentinel": {"risk": "low"}, "escape": {"risk": "none"}})
    await h._cmd_guardian(_update(), SimpleNamespace(args=[]))
    assert len(sent) == 1
    card = sent[0]
    assert "Guardian console" in card and "LOW" in card
    assert "12 entries" in card and "verified" in card
    assert "policy set" in card


@pytest.mark.asyncio
async def test_a_non_admin_is_refused_in_their_own_language():
    sent: list = []
    h = _host(sent, admin=False, lang="zh")
    h.engine = SimpleNamespace()          # must not be touched on the refusal path
    for cmd in ("_cmd_policy", "_cmd_twin", "_cmd_sentinel", "_cmd_escape", "_cmd_guardian"):
        await getattr(h, cmd)(_update(), SimpleNamespace(args=[]))
    assert sent == ["\U0001f512 " + th.t("admin_only", "zh")] * 5
    assert th.t("admin_only", "zh") != th.t("admin_only", "en")


@pytest.mark.asyncio
async def test_twin_still_tells_an_unreadable_book_from_a_flat_one():
    """Three outcomes, not two — the property /twin was fixed for, checked
    on the moved code. A planned crash must not read as an empty account."""
    sent: list = []
    h = _host(sent)
    h.engine = SimpleNamespace(run_digital_twin=lambda: None)
    await h._cmd_twin(_update(), SimpleNamespace(args=[]))
    assert "could not read the book" in sent[-1]
    assert "no open positions" not in sent[-1]

    h.engine = SimpleNamespace(run_digital_twin=lambda: {"flat_book": True, "scenarios": []})
    await h._cmd_twin(_update(), SimpleNamespace(args=[]))
    assert "no open positions to stress-test" in sent[-1]
    assert "could not read" not in sent[-1]
