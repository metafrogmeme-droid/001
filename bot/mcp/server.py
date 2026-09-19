"""
RUNECLAW MCP tool ADAPTER over the skill registry -- in-process, no HTTP door.

This module wraps each registered skill as a typed MCP tool with JSON Schema
input definitions, dispatches incoming tool calls to ``skill.execute()``, and
returns structured JSON responses.

**IT IS NOT WHAT SERVES ``POST /mcp``, AND THE PUBLISHED DOC SAID IT WAS.**
``docs/gitbook/mcp-integration.md`` -- the page an agent developer reads before
integrating, published on GitBook -- carried the row

    MCP tool adapter layer | Implemented -- bot/mcp/server.py, live over
    JSON-RPC at POST /mcp

and the sentence "``app/routes/mcp.js`` mounts it at ``POST /mcp``". Driven,
``app/routes/mcp.js`` contains no reference to this module and none to any
``runeclaw_*`` name; it serves its own table of thirty-four tools built on the
public site's libraries. Every one of the nine names the doc and
``agent_card.json`` advertise answers::

    {"code": -32602, "message": "Unknown tool: runeclaw_scan"}

Nothing constructs ``RuneClawMCPServer`` outside tests. The one production
import of this file reads ``_MCP_AUTH_TOKEN`` to assert the constructor refuses
to start without it -- a check about a constant, not a caller.

**THE SURFACE IS SAFE BECAUSE THE DOCUMENT IS WRONG ABOUT IT**, which is why
this is recorded here rather than wired. ``POST /mcp`` is mounted with no auth
(``app/server.js``; ``routes/mcp.js`` says so in its own comments), and driven,
``runeclaw_portfolio`` renders six dollar figures and ``runeclaw_risk`` two --
the OPERATOR's book, because ``call_tool`` takes one shared bearer token and
passes no caller identity to any skill. Mounting this catalogue there as the
doc claimed would publish account dollars on an unauthenticated route, against
the rule that only percent, ratio and count go on a public payload, and would
hand every anonymous caller the operator's book: the leak ``viewer_executor``
and ``live_view(user_id)`` were written to close, arriving through a door
nobody had pointed at. Three things have to be decided before it gets a door --
who the caller is, what a per-caller read means with one shared token, and
which tools may answer at all -- and none of them is a wiring line.

What IS true of this file is checked by
``tests/test_the_mcp_adapter_says_what_it_does.py``; what the doc and the agent
card may claim is checked, against the route itself, by
``app/test/the_published_mcp_tools_are_tools_the_route_answers.test.js``.

Usage::

    from bot.mcp.server import RuneClawMCPServer

    server = RuneClawMCPServer()
    tools  = await server.list_tools()
    result = await server.call_tool("runeclaw_scan", {}, auth_token=...)
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import traceback
from dataclasses import dataclass
from typing import Any, Optional

from bot.core.engine import RuneClawEngine
from bot.skills.skill_registry import (
    SkillRegistry,
    build_default_registry,
)
from bot.utils.exc_text import _safe_exc_text
from bot.utils.logger import audit, system_log

# C5 FIX: bearer token authentication for MCP tool calls.
# Set MCP_AUTH_TOKEN in .env to require callers to authenticate.
_MCP_AUTH_TOKEN: str = os.environ.get("MCP_AUTH_TOKEN", "")

# SEC-H3 FIX: strict symbol format validator for MCP entry points.
_SYMBOL_RE = re.compile(r'^[A-Z0-9]{1,15}(/[A-Z0-9]{1,15})?$')

def _scan_universe() -> tuple[str, ...]:
    """`scan_skill.UNIVERSE`, read once, or empty when it cannot be read.

    Imported inside the function because `scan_skill` pulls the engine in and
    this module is imported by a fail-closed constructor check. An empty tuple
    is an absence the description then STATES rather than a count it invents.
    """
    try:
        from bot.skills.scan_skill import UNIVERSE
        return tuple(UNIVERSE)
    except Exception:  # noqa: BLE001 -- an unreadable universe is not zero
        return ()


def _universe_phrase() -> str:
    """How many symbols the sweep covers, or that nobody could count them."""
    n = len(_FULLSCAN_UNIVERSE)
    return f"{n}-symbol" if n else "whole-universe (size not readable here)"


# The universe `_fullscan` really sweeps, COUNTED rather than typed into the
# description beside it. It is 67 today and that number was correct -- but it
# is a number in prose that a list decides, which is the part that rots first;
# `deepscan_universe_size()` exists one skill over for exactly this, and
# `DeepScanSkill.description` carried a stale "67+ symbols" against a universe
# of 115 for years. This is NOT that universe: `scan_skill.UNIVERSE` and
# `DEEPSCAN_UNIVERSE + TRADFI_PERPETUALS` are two different lists and the two
# counts differ legitimately.
_FULLSCAN_UNIVERSE = _scan_universe()

# `_fullscan` branches on "quick" and nothing else, so these two words are the
# whole vocabulary. Read by the argument validator in `call_tool` and by the
# catalogue description above, so the accepted set and the advertised set
# cannot drift apart.
_FULLSCAN_MODES: frozenset[str] = frozenset({"quick", "deep"})

# The two bounds `_fullscan` slices with. Named because the description beside
# them stated both by hand -- "'quick' (top 10 symbols, top 10 signals)" -- and
# a number typed beside the code that decides it is the same second copy as the
# universe size one line up, at one digit's scale. The guard that found this was
# written for the universe count and fired on these instead.
_QUICK_SYMBOLS = 10
_QUICK_SIGNALS = 10
_DEEP_SIGNALS = 20


# ---------------------------------------------------------------------------
# Tool definition helpers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MCPToolParam:
    """Single parameter in an MCP tool's input schema."""

    name: str
    type: str  # JSON Schema type (e.g. "string", "integer")
    description: str
    required: bool = True
    default: Any = None


@dataclass(frozen=True)
class MCPToolDef:
    """Declarative definition of one MCP tool."""

    mcp_name: str
    skill_name: str
    description: str
    params: tuple[MCPToolParam, ...] = ()


# ---------------------------------------------------------------------------
# Static tool catalogue -- maps MCP tool names to skill names + schemas
# ---------------------------------------------------------------------------

TOOL_CATALOGUE: tuple[MCPToolDef, ...] = (
    MCPToolDef(
        mcp_name="runeclaw_scan",
        skill_name="scan_market",
        description="Scan the exchange for top movers and volume anomalies.",
    ),
    MCPToolDef(
        mcp_name="runeclaw_analyze",
        skill_name="analyze_asset",
        description="Run AI analysis on a specific asset and generate a trade idea.",
        params=(
            MCPToolParam(
                name="symbol",
                type="string",
                description="Trading pair symbol, e.g. 'BTC/USDT'.",
                required=True,
            ),
        ),
    ),
    MCPToolDef(
        mcp_name="runeclaw_risk",
        skill_name="check_risk",
        description="Show current risk metrics, drawdown, and circuit-breaker status.",
    ),
    MCPToolDef(
        mcp_name="runeclaw_portfolio",
        skill_name="get_portfolio",
        description="Show paper-portfolio summary: balance, equity, win rate, PnL.",
    ),
    # SECURITY: runeclaw_execute intentionally excluded from MCP.
    # Exposing trade execution over MCP would bypass the human-confirmation
    # gate, violating the fail-closed design.  An agent on the Hub could
    # call runeclaw_analyze → runeclaw_execute fully autonomously.
    # See: Audit finding A (MCP execute bypass).
    #
    # THIS COMMENT NAMED A SWITCH AND NOTHING READS IT. It used to end
    # "Re-enable only behind MCP_ALLOW_EXECUTE=true AND with caller auth", and
    # `docs/gitbook/mcp-integration.md` repeated the name to an operator as the
    # gate to set. Driven, `MCP_ALLOW_EXECUTE` has no reader anywhere in the
    # tree: setting it does nothing at all, which is the `/vault` hint shape
    # pointed at an environment variable -- a surface naming a command that
    # does nothing. The flag arrives with the code that reads it. Until then
    # what exists is a DECISION, not a knob, and the decision has three parts
    # and no default: who the caller is (`call_tool` takes one shared bearer
    # token and passes no identity to any skill), what confirmation means with
    # no chat to confirm in, and what the audit record of an agent-initiated
    # order looks like.
    MCPToolDef(
        mcp_name="runeclaw_explain",
        skill_name="explain_trade",
        description="Explain a pending or historical trade idea.",
        params=(
            MCPToolParam(
                name="trade_id",
                type="string",
                description="The trade ID to explain. Omit to list pending ideas.",
                required=False,
                default="",
            ),
        ),
    ),
    MCPToolDef(
        mcp_name="runeclaw_macro",
        skill_name="macro_calendar",
        description="Show macro-event calendar: current risk state and upcoming events.",
    ),
    MCPToolDef(
        mcp_name="runeclaw_shield",
        skill_name="_shield_evaluate",
        description=(
            "RUNECLAW Shield: fail-closed risk checks on a trade proposal. "
            "Any external agent can call this to get an immutable safety "
            "decision. Returns approved/rejected with check details — the "
            "checks that actually ran are named in the response."
        ),
        params=(
            MCPToolParam(
                name="symbol", type="string",
                description="Trading pair, e.g. 'BTC/USDT'.",
            ),
            MCPToolParam(
                name="direction", type="string",
                description="Trade direction: 'long' or 'short'.",
            ),
            MCPToolParam(
                name="entry_price", type="number",
                description="Proposed entry price.",
            ),
            MCPToolParam(
                name="stop_loss", type="number",
                description="Stop loss price.",
            ),
            MCPToolParam(
                name="take_profit", type="number",
                description="Take profit price.",
            ),
            MCPToolParam(
                name="confidence", type="number",
                description="Signal confidence 0.0-1.0.",
                required=False,
                default=0.65,
            ),
        ),
    ),
    MCPToolDef(
        mcp_name="runeclaw_fullscan",
        skill_name="_fullscan",
        # AN ACCEPTANCE IS A CLAIM, and this one advertised four modes over two
        # behaviours. `_fullscan` branches on `mode == "quick"` and nothing
        # else, so 'swing' and 'scalp' ran the identical whole-universe sweep
        # and the reply echoed `"mode": "scalp"` back over it -- a card headed
        # with a strategy nobody ran, which is `ProScanSkill`'s own recorded
        # defect (`MODE_CFG.get(mode, MODE_CFG["intraday"])`, a scalp request
        # rendered as an intraday card with no marker) one adapter over. Those
        # two modes are `pro_scan`'s and reaching them from here would be a new
        # dispatch, not a wording fix, so the vocabulary narrows to what this
        # function does.
        description=(
            f"Run a full {_universe_phrase()} market scan across the "
            "RUNECLAW scan universe. Returns ranked signals with RSI, volume, "
            "chart patterns, and scores."
        ),
        params=(
            MCPToolParam(
                name="mode", type="string",
                description=(
                    f"Scan mode: 'quick' (top {_QUICK_SYMBOLS} symbols, top "
                    f"{_QUICK_SIGNALS} signals) or 'deep' (the whole universe, "
                    f"top {_DEEP_SIGNALS})."
                ),
                required=False,
                default="quick",
            ),
        ),
    ),
    MCPToolDef(
        mcp_name="runeclaw_backtest",
        skill_name="run_backtest",
        description="Run a backtest with synthetic data.",
        params=(
            MCPToolParam(
                name="bars",
                type="integer",
                description="Number of OHLCV bars to generate (max 5000).",
                required=False,
                default=720,
            ),
            MCPToolParam(
                name="seed",
                type="integer",
                description="Random seed for reproducible synthetic data.",
                required=False,
                default=42,
            ),
        ),
    ),
)


# ---------------------------------------------------------------------------
# Response envelope
# ---------------------------------------------------------------------------

@dataclass
class MCPResponse:
    """Standardised response envelope returned by every tool call."""

    status: str  # "success" or "error"
    tool: str
    result: Any

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "tool": self.tool, "result": self.result}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

class RuneClawMCPServer:
    """
    Model Context Protocol server that wraps the RUNECLAW skill registry.

    The server is stateful: it owns a single ``RuneClawEngine`` instance and
    routes incoming ``call_tool`` requests to the matching skill via the
    ``SkillRegistry``.

    Lifecycle::

        server = RuneClawMCPServer()        # creates engine + registry
        tools  = await server.list_tools()   # JSON tool definitions
        resp   = await server.call_tool("runeclaw_scan", {})
        await server.shutdown()              # graceful cleanup
    """

    def __init__(
        self,
        engine: RuneClawEngine | None = None,
        registry: SkillRegistry | None = None,
    ) -> None:
        # C5 HARDENED: fail-closed -- refuse to start without auth token
        if not _MCP_AUTH_TOKEN:
            raise RuntimeError(
                "MCP_AUTH_TOKEN is not set. The MCP server refuses to start "
                "without authentication. Set MCP_AUTH_TOKEN in your .env file."
            )

        self._engine = engine or RuneClawEngine()
        self._registry = registry or build_default_registry()
        self._tool_index: dict[str, MCPToolDef] = {
            t.mcp_name: t for t in TOOL_CATALOGUE
        }

        audit(
            system_log,
            f"MCP server initialised with {len(self._tool_index)} tools",
            action="mcp_init",
        )

    # -- public API ---------------------------------------------------------

    async def list_tools(self) -> list[dict[str, Any]]:
        """Return MCP tool definitions with JSON Schema ``inputSchema``."""
        tools: list[dict[str, Any]] = []
        for tdef in TOOL_CATALOGUE:
            tools.append(self._build_tool_schema(tdef))
        return tools

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        auth_token: str | None = None,
    ) -> dict[str, Any]:
        """
        Dispatch an MCP tool call to the corresponding skill.

        Parameters
        ----------
        name:
            The MCP tool name, e.g. ``"runeclaw_scan"``.
        arguments:
            Key-value arguments matching the tool's ``inputSchema``.
        auth_token:
            Bearer token for authentication (required when MCP_AUTH_TOKEN is set).

        Returns
        -------
        dict
            ``{"status": "success"|"error", "tool": "<name>", "result": "..."}``
        """
        arguments = arguments or {}

        # --- C5 FIX: authenticate caller (always enforced) -----------------
        # Constructor already refuses to start without MCP_AUTH_TOKEN.
        # This check is unconditional — no fail-open path exists.
        import hmac
        if not auth_token or not hmac.compare_digest(auth_token, _MCP_AUTH_TOKEN):
            audit(
                system_log,
                f"MCP auth rejected for tool '{name}'",
                action="mcp_auth_fail",
            )
            return MCPResponse(
                status="error",
                tool=name,
                result="Authentication required. Provide a valid auth_token.",
            ).to_dict()

        # --- lookup --------------------------------------------------------
        tdef = self._tool_index.get(name)
        if tdef is None:
            return MCPResponse(
                status="error",
                tool=name,
                result=f"Unknown tool '{name}'. Available: {list(self._tool_index)}",
            ).to_dict()

        skill = self._registry.get(tdef.skill_name)
        if skill is None and not tdef.skill_name.startswith("_"):
            return MCPResponse(
                status="error",
                tool=name,
                result=f"Skill '{tdef.skill_name}' is not registered.",
            ).to_dict()

        # --- validate required params -------------------------------------
        missing = [
            p.name
            for p in tdef.params
            if p.required and p.name not in arguments
        ]
        if missing:
            return MCPResponse(
                status="error",
                tool=name,
                result=f"Missing required parameters: {missing}",
            ).to_dict()

        # --- apply defaults for optional params ---------------------------
        # Coercion runs before the execute try/except below; a bad numeric
        # argument (e.g. bars="abc") would otherwise raise an unhandled
        # ValueError that bypasses the structured error envelope + redaction.
        kwargs: dict[str, Any] = {}
        try:
            for p in tdef.params:
                if p.name in arguments:
                    kwargs[p.name] = self._coerce(arguments[p.name], p.type)
                elif p.default is not None:
                    kwargs[p.name] = p.default
        except (ValueError, TypeError) as exc:
            return MCPResponse(
                status="error",
                tool=name,
                result=f"Invalid argument type: {exc}",
            ).to_dict()

        # --- SEC-H3 FIX: validate symbol parameters ----------------------
        if "symbol" in kwargs and isinstance(kwargs["symbol"], str):
            if not _SYMBOL_RE.match(kwargs["symbol"]):
                return MCPResponse(
                    status="error",
                    tool=name,
                    result="Invalid symbol format. Expected e.g. 'BTC/USDT'.",
                ).to_dict()

        # --- MCP-2: enforce documented bounds (resource-amplification guard) --
        # An authenticated caller could otherwise pass an unbounded `bars` count
        # or an unrecognized `mode` that silently triggers the full 67-symbol scan.
        if "bars" in kwargs and isinstance(kwargs["bars"], int):
            kwargs["bars"] = max(1, min(kwargs["bars"], 5000))
        # The accepted set is `_FULLSCAN_MODES`, which is what `_fullscan`
        # branches on -- a second copy here is a second answer about which
        # modes exist, and the copy that used to sit in this literal accepted
        # two the function does nothing with.
        if "mode" in kwargs and isinstance(kwargs["mode"], str):
            if kwargs["mode"].lower() not in _FULLSCAN_MODES:
                return MCPResponse(
                    status="error",
                    tool=name,
                    result=("Invalid mode. Expected one of: "
                            + ", ".join(sorted(_FULLSCAN_MODES)) + "."),
                ).to_dict()
            kwargs["mode"] = kwargs["mode"].lower()

        # --- execute -------------------------------------------------------
        try:
            # Special built-in tools that bypass the skill registry
            if tdef.skill_name == "_shield_evaluate":
                result_text = await self._shield_evaluate(**kwargs)
            elif tdef.skill_name == "_fullscan":
                result_text = await self._fullscan(**kwargs)
            else:
                result_text = await skill.execute(self._engine, **kwargs)
            audit(
                system_log,
                f"MCP tool '{name}' executed successfully",
                action="mcp_call",
                result="ok",
                data={"tool": name, "kwargs": kwargs},
            )
            return MCPResponse(
                status="success", tool=name, result=result_text
            ).to_dict()

        except Exception as exc:  # noqa: BLE001
            tb = traceback.format_exc()
            # C3 FIX: redact secrets from traceback before logging
            from bot.utils.logger import _redact_string
            tb = _redact_string(tb)
            audit(
                system_log,
                f"MCP tool '{name}' failed: {exc}",
                action="mcp_call",
                result="error",
                data={"tool": name, "traceback": tb},
            )
            # THE REDACTION WAS THREE LINES UP AND POINTED AT THE OTHER STRING.
            # `_redact_string` scrubbed the traceback for the LOG and the
            # caller's copy was a bare f-string of the exception, so a ccxt
            # error carrying `https://api.bitget.com/...?apiKey=...` reached
            # whoever called the tool verbatim. `quant_skill._safe_reason`
            # recorded the identical shape -- a docstring promising "never a
            # key, never a URL with a token" over a trim and a truncation --
            # and the cure is the same one table: `_safe_exc_text` reads
            # `secret_shapes` and knows the bot-token shape the key=value
            # redactor does not.
            return MCPResponse(
                status="error",
                tool=name,
                result=f"Skill execution failed: {_safe_exc_text(exc)}",
            ).to_dict()

    async def shutdown(self) -> None:
        """Graceful shutdown hook (close exchange connections, etc.)."""
        audit(system_log, "MCP server shutting down", action="mcp_shutdown")

    # -- RUNECLAW Shield: standalone risk evaluation -----------------------

    async def _shield_evaluate(
        self, symbol: str, direction: str, entry_price: float,
        stop_loss: float, take_profit: float,
        confidence: Optional[float] = None,
    ) -> str:
        """Run the RUNECLAW Shield fail-closed risk checks on a trade proposal.

        `confidence` used to default to 0.65. The Shield's own confidence floor
        defaults to 0.60, so a caller that simply omitted the argument was
        handed a passing grade on the gate — and the reply echoed it back as
        `"confidence": 0.65`, indistinguishable from a caller who had actually
        measured it. An MCP client is a program: omitting a field is the most
        ordinary thing it does.

        Refused rather than defaulted, because this endpoint's whole promise is
        in its name. A fail-closed check that invents its own input is not one.
        """
        from bot.utils.models import Direction, TradeIdea, RiskVerdict

        if confidence is None:
            return json.dumps({
                "approved": False,
                "verdict": "REJECTED",
                "confidence": None,
                "reason": ("No confidence supplied. The Shield gates on a "
                           "confidence floor, so it cannot evaluate a proposal "
                           "that does not state one — and will not assume a "
                           "value that happens to clear it."),
                "failed_checks": ["CONFIDENCE: not supplied"],
            }, default=str)

        dir_enum = Direction.LONG if direction.lower() == "long" else Direction.SHORT
        idea = TradeIdea(
            asset=symbol, direction=dir_enum, entry_price=entry_price,
            stop_loss=stop_loss, take_profit=take_profit,
            confidence=confidence, reasoning="MCP Shield evaluation",
            source="mcp_shield",
        )
        # Compute ATR from stop distance as proxy
        atr = abs(entry_price - stop_loss) * 3 if entry_price > 0 else None
        result = self._engine.risk.evaluate(idea, atr=atr)
        approved = result.verdict == RiskVerdict.APPROVED
        return json.dumps({
            "approved": approved,
            "verdict": result.verdict.value,
            "confidence": round(confidence, 3),
            "risk_reward": round(idea.risk_reward_ratio, 2),
            "position_size_usd": result.position_size_usd,
            "checks_passed": len(result.checks_passed),
            "checks_failed": len(result.checks_failed),
            "failed_checks": result.checks_failed,
            "reason": result.reason,
        }, default=str)

    async def _fullscan(self, mode: str = "quick") -> str:
        """Run full 67-symbol scan and return structured results."""
        from bot.skills.scan_skill import UNIVERSE, _scan_symbol

        exchange = await self._engine.scanner._get_exchange()
        results = []
        symbols = UNIVERSE[:_QUICK_SYMBOLS] if mode == "quick" else UNIVERSE
        batch_size = 10
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i + batch_size]
            tasks = [_scan_symbol(exchange, sym) for sym in batch]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in batch_results:
                if isinstance(r, dict) and r is not None:
                    # Serialize pattern dicts for JSON
                    if "patterns" in r:
                        r["patterns"] = [
                            {"name": p["name"], "signal": p["signal"],
                             "confidence": round(p.get("confidence", 0), 2)}
                            for p in r["patterns"][:3]
                        ]
                    results.append(r)
            if i + batch_size < len(symbols):
                await asyncio.sleep(1.0)

        results.sort(key=lambda r: r.get("score", 0), reverse=True)
        top = (results[:_DEEP_SIGNALS] if mode != "quick"
               else results[:_QUICK_SIGNALS])
        return json.dumps({
            "mode": mode,
            "total_scanned": len(results),
            "signals": top,
        }, default=str)

    # -- internal helpers ---------------------------------------------------

    @staticmethod
    def _build_tool_schema(tdef: MCPToolDef) -> dict[str, Any]:
        """Build a single MCP tool definition dict with ``inputSchema``."""
        properties: dict[str, Any] = {}
        required: list[str] = []

        for p in tdef.params:
            prop: dict[str, Any] = {
                "type": p.type,
                "description": p.description,
            }
            if p.default is not None:
                prop["default"] = p.default
            properties[p.name] = prop

            if p.required:
                required.append(p.name)

        input_schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
        }
        if required:
            input_schema["required"] = required
        # Allow no additional properties for strict validation
        input_schema["additionalProperties"] = False

        return {
            "name": tdef.mcp_name,
            "description": tdef.description,
            "inputSchema": input_schema,
        }

    @staticmethod
    def _coerce(value: Any, json_type: str) -> Any:
        """Best-effort coercion of an argument to its declared JSON type."""
        if json_type == "integer":
            return int(value)
        if json_type == "number":
            return float(value)
        if json_type == "boolean":
            if isinstance(value, str):
                return value.lower() in ("true", "1", "yes")
            return bool(value)
        return value  # string or unknown -- pass through
