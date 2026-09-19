# MCP Integration

RUNECLAW is designed as a **standalone trading agent** but its architecture maps naturally to the **Model Context Protocol (MCP)** used by the Bitget Agent Hub.

---

## Which surface answers your call

There are **two** MCP pieces in this repository and only one of them is on the
network. Reading them as one thing is what this page used to do, and it cost an
integrator every call they made.

| | what it is | can an agent call it? |
|---|---|---|
| `app/routes/mcp.js` | MCP Streamable HTTP at **`POST /mcp`**, JSON-RPC, built on the libraries behind the public site | **Yes.** This is the surface. |
| `bot/mcp/server.py` | an in-process adapter that wraps the bot's skill registry as typed tools | **No.** Nothing serves it over HTTP. |

**Ask the server, do not read a list.** `POST /mcp` answers `tools/list`, and
that answer is the catalogue — every name, description and `inputSchema`, as of
the build you are talking to. A list typed into this page would be a second,
staler copy of something already enumerable, which is the same reason no count
appears anywhere below.

```bash
curl -sS -X POST https://<host>/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

> **The `runeclaw_*` names in the next section are NOT callable there.** This
> page used to present them as the live surface — the status row read
> "Implemented -- `bot/mcp/server.py`, live over JSON-RPC at `POST /mcp`" and
> the paragraph under it said `app/routes/mcp.js` mounts it. Driven,
> `app/routes/mcp.js` names neither this module nor any `runeclaw_*` tool, and
> each of the nine answers `Unknown tool`. `app/test/the_published_mcp_tools_are_tools_the_route_answers.test.js`
> drives every tool name this page and `agent_card.json` publish against
> `tools/list`, so a name either surface advertises is one the route answers.

---

## What is MCP?

The Model Context Protocol is a standard interface that allows AI agents to expose their capabilities as structured tools. An MCP-compatible agent publishes a set of tools (functions) that other systems -- including the Bitget Agent Hub -- can discover and invoke.

Each tool has:
- A **name** (e.g. `runeclaw_scan`)
- A **description** (what it does)
- A **schema** (what inputs it accepts)
- A **handler** (the function that executes it)

---

## The in-process skill adapter (`bot/mcp/server.py`)

RUNECLAW's internal skill registry maps directly to MCP tools. Each skill is a self-contained async function that takes structured input and returns structured output via Pydantic models. `TOOL_CATALOGUE` is that mapping, and the table below is it, row for row.

**This adapter has no HTTP door, and what it would take to give it one is the
interesting part.** Three questions have to be answered first, and none is a
wiring line:

- **Who is the caller?** `call_tool` takes one shared bearer token and passes no
  identity to any skill, so every read is the OPERATOR's book. That is the
  defect `viewer_executor` and `live_view(user_id)` were written to close on
  six surfaces; it would arrive here through a door nobody had pointed at.
- **`POST /mcp` is unauthenticated**, and driven, `runeclaw_portfolio` renders
  six dollar figures and `runeclaw_risk` two. Account dollars do not go on a
  public payload — percent, ratio and count only — so mounting this catalogue
  there as written would break that rule on two rows the moment it shipped.
- **Which tools may answer at all?** `runeclaw_analyze` and `runeclaw_fullscan`
  each spend live venue fetches per call; the sweep is batched with a pause
  between batches. On an unauthenticated endpoint that is a cost an anonymous
  caller chooses for you.

| MCP Tool | Internal Skill | Description |
|----------|---------------|-------------|
| `runeclaw_scan` | `scan_market` | Scan the exchange for top movers and volume anomalies |
| `runeclaw_analyze` | `analyze_asset` | Run AI + technical analysis on a specific asset, generate a trade idea |
| `runeclaw_risk` | `check_risk` | Current risk metrics, drawdown and circuit-breaker status |
| `runeclaw_portfolio` | `get_portfolio` | Paper-portfolio summary: balance, equity, win rate, PnL |
| `runeclaw_explain` | `explain_trade` | Explain a pending or historical trade idea |
| `runeclaw_macro` | `macro_calendar` | Macro-event calendar: current risk state and upcoming events |
| `runeclaw_shield` | built in (`_shield_evaluate`) | Fail-closed risk checks on a caller-supplied trade proposal; the checks that ran are named in the reply |
| `runeclaw_fullscan` | built in (`_fullscan`) | Ranked signals across the scan universe (`quick`, `deep`, `swing`, `scalp`) |
| `runeclaw_backtest` | `run_backtest` | Backtest on synthetic data (`bars`, capped at 5000, and `seed`) |

`runeclaw_execute` is deliberately absent, and this table used to list it. The
catalogue's own comment says why: an execution tool reachable by any agent
holding the MCP token turns `runeclaw_analyze` → execute into a fully
autonomous loop.

**There is no switch that re-enables it, and this paragraph used to name one.**
It said re-enabling was "gated behind `MCP_ALLOW_EXECUTE=true` and caller auth",
which an operator reads as *set this variable and execution comes back*. Driven,
that name has no reader anywhere in the tree — setting it does nothing at all.
What exists is a decision with the same three parts as the door question above:
who the caller is, what confirmation means with no chat to confirm in, and what
the audit record of an agent-initiated order looks like. The flag arrives with
the code that reads it.

`tests/test_mcp_doc_matches_the_code.py` checks this table against
`TOOL_CATALOGUE` row by row, so a tool added to one and not the other fails the
build.

---

## Architecture Alignment

RUNECLAW's skill registry pattern was designed with MCP compatibility in mind:

```text
MCP Client (Agent Hub / External AI)
 |
 v
 MCP Tool Layer ← thin adapter, maps tool calls to skills
 |
 v
 Skill Registry ← existing RUNECLAW skill system
 |
 v
 RuneClaw Engine ← orchestrator with full state
 |
 ┌───┼───┐
 v v v
Scanner Analyzer Risk Engine
```

The MCP adapter is a thin translation layer. It does not add business logic -- it maps MCP tool calls to the existing `BaseSkill.execute()` interface and serializes Pydantic responses back to the caller.

### Skill Interface

Every RUNECLAW skill follows this contract:

```python
class BaseSkill(ABC):
 name: str = "unnamed"
 description: str = ""

 @abstractmethod
 async def execute(self, engine: RuneClawEngine, **kwargs) -> str:
 ...
```

MCP tools call `skill.execute(engine, **params)` and return the string result. Input validation happens via Pydantic at the engine boundary.

---

## Data Flow

```text
MCP Request: { "tool": "runeclaw_analyze", "input": { "symbol": "BTC/USDT" } }
 |
 v
 AnalyzeAssetSkill.execute(engine, symbol="BTC/USDT")
 |
 v
 Engine: fetch candles → compute indicators → LLM thesis → TradeIdea
 |
 v
 RiskEngine: fail-closed gate
 |
 v
 MCP Response: { "result": "LONG BTC/USDT | Confidence 72% | R:R 2.8 | ..." }
```

All inputs are validated. All outputs are structured. The risk gate runs on every analysis regardless of whether the call comes from Telegram, CLI, or MCP.

---

## Integration Status

| Component | Status |
|-----------|--------|
| Skill registry (internal) | Implemented |
| Pydantic schemas at all boundaries | Implemented |
| Async execution model | Implemented |
| MCP surface over HTTP (`app/routes/mcp.js`) | **Implemented** -- MCP Streamable HTTP at `POST /mcp`; ask it `tools/list` |
| In-process skill adapter (`bot/mcp/server.py`) | **Written, not served** -- no HTTP door; see the three questions above |
| Bitget Agent Hub registration | Planned -- pending Agent Hub availability |

The adapter is written, not planned, and it is also not what answers your call.
`bot/mcp/server.py` builds JSON Schema tool definitions from `TOOL_CATALOGUE`
and dispatches `call_tool` into the skill registry — in this process, for a
caller that would have to be constructed in Python. Nothing outside the tests
constructs it.

**That row used to read "Implemented -- `bot/mcp/server.py`, live over JSON-RPC
at `POST /mcp`"**, and the paragraph under it said `app/routes/mcp.js` mounts
it. Both ends of that sentence exist and there is no connection between them:
the route serves its own table, built on the libraries behind the public site.
The guard standing over this page proved the table here and `TOOL_CATALOGUE`
agreed — which they did, exactly — while the sentence a reader acts on was
about a route that had never heard of either.

**A SUBSET of registered skills is in the catalogue, not all of them.** The
registry is larger, and that is a deliberate gap rather than an oversight: a
skill reachable by an unauthenticated agent is a different security question
from one reachable by an operator on Telegram.

> No count appears in this table, and that is on purpose. This row used to read
> "12 skills registered" while the registry held thirty, and the line below used
> to promise "all 12" as MCP tools while nine were exposed. A hand-maintained
> count against a file that changes drifts in one direction — the same defect
> `_TOTAL_RISK_CHECKS = 23` produced against an engine emitting thirty-six
> labels, on eleven surfaces at once. The registry and the catalogue are both
> enumerable at runtime; a number typed into a document is a second, staler
> copy of something already knowable.

---

## Future: Agent Hub Registration

When the Bitget Agent Hub supports MCP tool registration, RUNECLAW will:

1. Register the existing `POST /mcp` surface with the Hub's discovery mechanism
2. Answer the three door questions above for `bot/mcp/server.py`, or retire it —
   widening `TOOL_CATALOGUE` toward the registry is the *second* step, and it is
   worth nothing while the catalogue reaches no caller

Both fail-closed guarantees already hold on every interface today: the risk gate
runs on every analysis whether the call arrives from Telegram, the CLI or MCP,
and every MCP call is logged through the structured audit system.

**One promise was dropped from this list, not moved.** It used to read
"require human confirmation for any trade execution (even via MCP)", and that is
false on shipped defaults — `bot/config.py` sets `auto_confirm_live_enabled` to
True, so a signal clearing the confidence bar places a live order with nobody
pressing anything. The honest pair is the one that IS true by default:
simulation mode is on and live trading is off until an operator switches it on.
The same sentence was corrected on the homepage, the meta description, the
JSON-LD and `llms.txt`; this was the fifth surface carrying it.
