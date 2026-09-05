"""The read-only tools the chat model may call, derived from the permission table.

WHY. The chat prompt used to say "You cannot run a tool from this chat", and it
was true: the only way a typed sentence reached a skill was a regex in
bot/nlp/intent_router.py scoring 0.8 or more. Everything the regexes missed went
to a model with no tools, which answered account questions from memory or from
three turns ago. The last thirty commits on this surface chased that one shape —
an answer produced from no reading — one prompt block at a time. A tool is the
structural fix: the model asks, the runtime reads, the model answers from the
reading.

WHAT IT CAN REACH, AND WHAT IT CANNOT. Every entry below names a skill that is
ALREADY a key of `SKILL_PERMISSION` — the one table both chat transports consult
— and `tools_for()` offers it only when `permission_for()` names a permission
the caller's role holds. `DANGEROUS_SKILLS` are never offered, whatever the
role: `halt` stays routed to its guarded command, exactly as free text does.
`test_chat_tool_calling.py` pins both invariants, so the model cannot be handed
a tool that a typed sentence could not reach, and a skill added later is
invisible to chat until somebody decides what it needs — the same fail-closed
direction `permission_for()` already takes.

WHY NOT EVERY PERMISSIONED SKILL. `analyze_asset`, `run_backtest`, `deepscan`,
`pro_scan`, `optimize`, `run_strategy` and `walk_forward` are heavy: a full
analysis is an LLM call of its own, a backtest is a subprocess, and the regex
router already dispatches each of them with the card, keyboard and tier gate
that path carries. Offering them here would either time out inside a chat turn
or duplicate a better surface. The model is told to say "analyze BTC" for those.

MEMORY. Every tool that runs is recorded in the conversation store in the same
`[skill] result:` shape `skill_memory` uses for regex-dispatched skills, so a
follow-up two turns later reads what the tool actually said — and
`strip_fabricated_tool_results` keeps doing its job on the model's own text,
because the runtime writes that shape and the model still must not.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from bot.nlp.intent_router import _validate_symbol
from bot.nlp.skill_memory import skill_failure_memory, skill_result_memory
from bot.skills.skill_permissions import DANGEROUS_SKILLS, WEB_CHAT_SKILLS, permission_for
from bot.utils.logger import audit, system_log

_SYMBOL_PARAM = {
    "type": "string",
    "description": ("Trading pair such as BTC/USDT. A bare ticker like BTC is "
                    "accepted and read as the USDT perpetual."),
}
_COUNT_PARAM = {
    "type": "integer",
    "description": "How many rows to return (1-20).",
    "minimum": 1,
    "maximum": 20,
}


def _schema(props: Optional[dict] = None, required: tuple[str, ...] = ()) -> dict:
    out: dict[str, Any] = {"type": "object", "properties": dict(props or {}),
                           "additionalProperties": False}
    if required:
        out["required"] = list(required)
    return out


@dataclass(frozen=True)
class ChatTool:
    """One tool the model may be offered. ``name`` IS the skill name — one
    vocabulary across the router, the permission table and this catalogue."""
    name: str
    description: str
    parameters: dict = field(default_factory=_schema)

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description,
                "parameters": self.parameters}


CHAT_TOOLS: tuple[ChatTool, ...] = (
    ChatTool(
        "get_portfolio",
        "The caller's OWN account as the engine sees it right now: balance, "
        "equity, open positions with entries, stops and unrealised PnL, closed-"
        "trade PnL and win rate. Call this for any question about their money, "
        "positions or performance rather than recalling an earlier turn."),
    ChatTool(
        "check_risk",
        "Current risk status: drawdown against the limit, exposure, circuit "
        "breaker, whether new entries are halted and why."),
    ChatTool(
        "scan_market",
        "Live scan of the exchange for top movers and volume anomalies. Takes a "
        "few seconds. Use it when asked what is moving or for a market overview."),
    ChatTool(
        "macro_calendar",
        "Macro-event calendar: the current risk window and the upcoming "
        "FOMC/CPI/NFP/PCE events with dates."),
    ChatTool(
        "check_event_risk",
        "Macro-event risk for ONE symbol over the next window.",
        _schema({"symbol": _SYMBOL_PARAM}, ("symbol",))),
    ChatTool(
        "costs",
        "Today's LLM/API spend against the daily budget, by category."),
    ChatTool(
        "whynot",
        "Why the engine did not take a trade: the gate that rejected or skipped "
        "it, optionally for one symbol.",
        _schema({"symbol": _SYMBOL_PARAM})),
    ChatTool(
        "rejected_trades",
        "The most recent trade ideas the risk gate rejected, with the reason.",
        _schema({"count": _COUNT_PARAM})),
    ChatTool(
        "patterns",
        "Recurring market patterns the learning system has detected, with "
        "their measured outcomes."),
    ChatTool(
        "proposals",
        "Improvement proposals the learning loop is holding for review."),
    ChatTool(
        "playbook",
        "The engine's execution playbook: the sequence of checks and actions "
        "it runs on every idea."),
    ChatTool(
        "learning",
        "The learning-system dashboard: what has been recorded, reflected on "
        "and learned so far."),
    ChatTool(
        "trade_journal",
        "The caller's most recent closed trades, newest first.",
        _schema({"count": _COUNT_PARAM})),
    ChatTool(
        "compliance_status",
        "Restricted jurisdictions and a summary of the consent ledger."),
)

#: name -> tool, for O(1) lookup at execution time.
_BY_NAME: dict[str, ChatTool] = {t.name: t for t in CHAT_TOOLS}


def tools_for(users, user_id: str, surface: str = "telegram") -> list[ChatTool]:
    """The tools this caller may be offered on this surface.

    A tool is offered only when ALL of these hold, and an unreadable answer to
    any of them withholds the tool rather than granting it:

      * `permission_for(name)` names a permission (None means refuse);
      * the name is not in `DANGEROUS_SKILLS`;
      * on the web, the name is in `WEB_CHAT_SKILLS` (the web's reachable set);
      * `users.permission_denial(user_id, permission)` is None — the caller's
        role holds it and the session is not stale;
      * the $RCLAW tier gate allows it, when that gate is configured.

    ``users`` is the UserStore. When it is missing (a test stand-in, a
    transport with no role store) nothing is offered: a role that cannot be
    read is not a role that holds everything.
    """
    if users is None or not user_id:
        return []
    out: list[ChatTool] = []
    for tool in CHAT_TOOLS:
        perm = permission_for(tool.name)
        if perm is None or tool.name in DANGEROUS_SKILLS:
            continue
        if surface == "web" and tool.name not in WEB_CHAT_SKILLS:
            continue
        try:
            if users.permission_denial(user_id, perm) is not None:
                continue
        except Exception:
            # An unreadable role is not a role that holds the permission.
            continue
        if not _tier_allows(users, user_id, tool.name):
            continue
        out.append(tool)
    return out


def _tier_allows(users, user_id: str, skill_name: str) -> bool:
    """The $RCLAW tier gate, mirrored from the two dispatch sites. Both of
    them let a gate BUG through rather than take the transport down, and so
    does this; a gate VERDICT is honoured."""
    try:
        from bot.token import tier_gate
        allowed, _reason = tier_gate.check_user(users, user_id, skill_name)
        return bool(allowed)
    except Exception as exc:
        system_log.debug("chat tool tier gate check skipped: %s", exc)
        return True


def _normalise_symbol(raw) -> Optional[str]:
    """``btc`` / ``BTC`` / ``btc/usdt`` -> ``BTC/USDT``; garbage -> None.

    Runs the same strict validator the intent router uses so a model-supplied
    string can never reach CCXT unchecked."""
    s = str(raw or "").strip().upper().replace("$", "")
    if not s:
        return None
    if "/" not in s:
        s = f"{s}/USDT"
    return _validate_symbol(s)


def _kwargs_for(name: str, args: dict) -> tuple[dict, Optional[str]]:
    """The skill kwargs for a tool call, or (``{}``, reason) when the
    arguments cannot be honoured. Only the declared parameters pass through;
    anything else the model sends is dropped rather than forwarded."""
    tool = _BY_NAME[name]
    props = tool.parameters.get("properties", {})
    required = set(tool.parameters.get("required", []))
    out: dict[str, Any] = {}
    if "symbol" in props:
        raw = args.get("symbol")
        if raw not in (None, ""):
            sym = _normalise_symbol(raw)
            if sym is None:
                return {}, f"'{str(raw)[:20]}' is not a symbol I can look up."
            out["symbol"] = sym
        elif "symbol" in required:
            return {}, "This tool needs a symbol."
    if "count" in props and args.get("count") is not None:
        try:
            out["count"] = max(1, min(20, int(args["count"])))
        except (TypeError, ValueError):
            pass
    return out, None


async def run_tool(handler, user_id: str, name: str, args: dict,
                   offered: set[str], surface: str = "telegram",
                   timeout: float = 12.0) -> str:
    """Execute one tool call on the caller's behalf and return what the model
    should read.

    Fail-closed on the name: only a name in ``offered`` — the set
    ``tools_for()`` produced for THIS caller on THIS surface — runs. The
    provider loop already refuses names it never offered; this is the same
    check one layer in, so the invariant does not depend on a single caller.

    THREE OUTCOMES, three records, the skill_memory rule: what the tool said,
    that it raised, or that it timed out. Each is written to the conversation
    store before the model's final answer is, so a later turn reads the
    evidence in order. Nothing here invents an answer for a tool that gave
    none.
    """
    if name not in offered or name not in _BY_NAME:
        audit(system_log, f"Chat tool refused: {name!r} not offered",
              action="chat_tool", result="REFUSED", data={"tool": name})
        return ("UNAVAILABLE — that tool is not offered in this conversation, "
                "so nothing ran and nothing was measured.")
    registry = getattr(handler, "registry", None)
    skill = registry.get(name) if registry is not None else None
    if skill is None:
        return ("UNAVAILABLE — this bot has no such tool wired up, so it was "
                "never run. Nothing was measured.")
    kwargs, problem = _kwargs_for(name, args or {})
    if problem:
        return f"NOT RUN — {problem}"
    conversations = getattr(handler, "conversations", None)
    try:
        from bot.core import user_memory_store as _user_memory
        _user_memory.observe(user_id, name, kwargs)
    except Exception:
        pass  # recall is context, never a dependency
    t0 = time.monotonic()
    try:
        result = await asyncio.wait_for(
            skill.execute(handler.engine, user_id=user_id, **kwargs),
            timeout=max(1.0, float(timeout)))
    except asyncio.TimeoutError:
        text = (f"[{name}] TIMED OUT after {int(timeout)}s — the tool did not "
                "answer in time and returned no result. Nothing was measured.")
        _remember(conversations, user_id, text,
                  {"skill": name, "surface": surface, "via": "tool_call",
                   "timed_out": True})
        audit(system_log, f"Chat tool timed out: {name}",
              action="chat_tool", result="TIMEOUT",
              data={"tool": name, "ms": int((time.monotonic() - t0) * 1000)})
        return text
    except Exception as exc:
        _remember(conversations, user_id, skill_failure_memory(name),
                  {"skill": name, "surface": surface, "via": "tool_call",
                   "failed": True})
        system_log.debug("chat tool %s failed: %s", name, exc)
        audit(system_log, f"Chat tool failed: {name}",
              action="chat_tool", result="FAILED", data={"tool": name})
        # The exception text stays in the log: memory feeds the model and the
        # model writes to a user, and a driver message can carry a host.
        raise RuntimeError("tool failed") from None
    record = skill_result_memory(name, result)
    _remember(conversations, user_id, record,
              {"skill": name, "surface": surface, "via": "tool_call"})
    audit(system_log, f"Chat tool ran: {name}",
          action="chat_tool", result="OK",
          data={"tool": name, "args": kwargs,
                "ms": int((time.monotonic() - t0) * 1000)})
    # Hand the model the plain-text body the store recorded (tags to spaces,
    # entities unescaped, truncation announced) minus the memory prefix.
    _prefix, _, body = record.partition("\n")
    if body:
        if "TRUNCATED" in _prefix:
            return f"{_prefix}\n{body}"
        return body
    return record


def _remember(conversations, user_id: str, text: str, metadata: dict) -> None:
    """Append to the conversation store when there is one. The store is the
    only thing that makes a tool's output survive to the next turn, so the
    append is attempted on every outcome; a missing store (a stand-in) is
    simply nowhere to write."""
    if conversations is None or not user_id:
        return
    try:
        conversations.append(user_id, "assistant", text, metadata=metadata)
    except Exception as exc:
        system_log.debug("chat tool memory append skipped: %s", exc)
