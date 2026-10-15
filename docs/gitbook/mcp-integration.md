# MCP Integration

RUNECLAW serves a public **Model Context Protocol (MCP)** endpoint at `https://www.humanoid-traders.com/mcp`: read-only market and track-record tools, Guardian safety checks on input you supply, and paper-only Arena tools. `/.well-known/mcp.json` describes it.

---

## Which surface answers your call

There are **two** MCP pieces in this repository and only one of them is on the
network.

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
curl -sS -X POST https://www.humanoid-traders.com/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

> **The `runeclaw_*` names in the next section are NOT callable there.**
> `app/routes/mcp.js` names neither the adapter nor any `runeclaw_*` tool, and each
> of them answers `Unknown tool`. `app/test/the_published_mcp_tools_are_tools_the_route_answers.test.js`
> drives every tool name this page and `agent_card.json` publish against
> `tools/list`, so a name either surface advertises is one the route answers.

---

## What is MCP?

The Model Context Protocol is a standard interface that allows AI agents to expose their capabilities as structured tools. An MCP-compatible agent publishes a set of tools (functions) that other systems -- agent frameworks, assistants, other bots -- can discover and invoke.

Each tool has:
- A **name** (e.g. `get_signals`)
- A **description** (what it does)
- A **schema** (what inputs it accepts)
- A **handler** (the function that executes it)

---

## The in-process skill adapter (`bot/mcp/server.py`)

This adapter wraps part of RUNECLAW's internal skill registry as MCP tools. Each skill is an async `execute()` that takes keyword arguments and returns text. `TOOL_CATALOGUE` is that mapping, and the table below is it, row for row.

**This adapter has no HTTP door, and what it would take to give it one is the
interesting part.** Three questions have to be answered first, and none is a
wiring line:

- **Who is the caller?** `call_tool` takes one shared bearer token and passes no
  identity to any skill, so every read would describe the operator's account
  rather than the caller's.
- **`POST /mcp` answers its read tools without authentication** (only the
  paper-only `arena_*` tools take an Arena key), and driven, `runeclaw_portfolio` renders
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
| `runeclaw_explain` | `explain_trade` | Explain one pending trade idea by id (side, confidence, the votes that drove it, the model's rationale); omit the id to list the engine's pending ideas. Not a closed or expired idea |
| `runeclaw_macro` | `macro_calendar` | Macro-event calendar: current risk state and upcoming events |
| `runeclaw_shield` | built in (`_shield_evaluate`) | Fail-closed risk checks on a caller-supplied trade proposal; the checks that ran are named in the reply |
| `runeclaw_fullscan` | built in (`_fullscan`) | Ranked signals across the scan universe (`quick`: the first symbols only; `deep`: the whole universe) |
| `runeclaw_backtest` | `run_backtest` | Backtest on synthetic data (`bars`, capped at 5000, and `seed`) |

`runeclaw_execute` is deliberately absent. The catalogue's own comment says why: an execution tool reachable by any agent
holding the MCP token turns `runeclaw_analyze` → execute into a fully
autonomous loop.

**There is no switch that re-enables it.** No setting brings execution back
(`MCP_ALLOW_EXECUTE` has no reader in the code). What exists is a decision with the same three parts as the door question above:
who the caller is, what confirmation means with no chat to confirm in, and what
the audit record of an agent-initiated order looks like. The flag arrives with
the code that reads it.

`tests/test_mcp_doc_matches_the_code.py` checks this table against
`TOOL_CATALOGUE` row by row, so a tool added to one and not the other fails the
build.

---

## Inside the adapter

How a call would travel through `bot/mcp/server.py`:

```text
MCP client (a Python caller; no HTTP door)
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

`runeclaw_shield` and `runeclaw_fullscan` are built into the adapter (`_shield_evaluate` refuses a proposal that states no confidence; `_fullscan` sweeps in batches with a pause between them); the other tools map to `BaseSkill.execute()`. Before dispatch, `call_tool` checks the bearer token, checks required parameters, coerces types, validates the symbol format, clamps `bars` and checks `mode`.

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

Registry tools call `skill.execute(engine, **params)` and return its text inside the adapter's envelope, `{"status", "tool", "result"}`.

---

## Data flow inside the in-process adapter (not served)

```text
call_tool("runeclaw_analyze", {"symbol": "BTC/USDT"}, auth_token=...)
 |
 v
 AnalyzeAssetSkill.execute(engine, symbol="BTC/USDT")
 |
 v
 Engine: fetch ticker and candles → indicators → rule-engine thesis → TradeIdea
 |
 v
 RiskEngine: the pre-trade risk gate
 |
 v
 { "status": "success", "tool": "runeclaw_analyze", "result": "…" }
```

Inside this adapter, `runeclaw_analyze` runs the analysis `/analyze` runs, risk gate included. No call to `POST /mcp` reaches it.

---

## Integration Status

| Component | Status |
|-----------|--------|
| Skill registry (internal) | Implemented |
| Pydantic models for the bot's ideas and risk results (`bot/utils/models.py`) | Implemented |
| Async execution model | Implemented |
| MCP surface over HTTP (`app/routes/mcp.js`) | **Implemented** -- MCP Streamable HTTP at `POST /mcp`; ask it `tools/list` |
| In-process skill adapter (`bot/mcp/server.py`) | **Written, not served** -- no HTTP door; see the three questions above |
| Agent-directory registration | Not done. Discovery today: `/.well-known/mcp.json` and `agent_card.json` |

The adapter is written, not planned, and it is also not what answers your call.
`bot/mcp/server.py` builds JSON Schema tool definitions from `TOOL_CATALOGUE`
and dispatches `call_tool` into the skill registry — in this process, for a
caller that would have to be constructed in Python. Nothing outside the tests
constructs it.

`app/routes/mcp.js` does not mount it: the route serves its own table, built on
the libraries behind the public site.

**A SUBSET of registered skills is in the catalogue, not all of them.** The
registry is larger, and that is a deliberate gap rather than an oversight: a
skill reachable by an unauthenticated agent is a different security question
from one reachable by an operator on Telegram.

> No count appears in this table, on purpose: the registry and the catalogue are
> both enumerable at runtime, and a number typed into a document is a second,
> staler copy of something already knowable.

---

## Not done

1. `POST /mcp` is not registered with any agent directory. An agent finds it
   today through `/.well-known/mcp.json` and `agent_card.json`.
2. Nothing serves `bot/mcp/server.py`. Serving it needs the three door
   questions above answered first, or it is retired. Widening `TOOL_CATALOGUE`
   toward the registry comes after that, because the catalogue reaches no
   caller.

No tool on `POST /mcp` reaches the engine's fail-closed risk gate, because none
can place an order: read tools serve what the site already publishes, Guardian
tools compute on the caller's input, and the `arena_*` tools trade paper. The
risk gate runs on every idea the engine generates and again on every confirm,
from Telegram or the website. `POST /mcp` keeps no per-call audit log; it
applies a per-IP limit of 60 requests a minute.

**This surface makes no confirmation promise.** "require human confirmation for any trade execution (even via MCP)" is not a promise the code keeps:
no MCP tool places an order, and the engine's own rule depends on configuration.
`bot/config.py` defaults `auto_confirm_live_enabled` to True, and the shipped
`.env.example` turns it off (`AUTO_CONFIRM_LIVE_ENABLED=false`,
`AUTO_CONFIRM_THRESHOLD=1.0`). With it on, a live order nobody pressed is placed
only when a committed eligibility record exists for the running strategy
(`bot/core/live_eligibility.py`), and none ships. The pair that IS true by
default: simulation mode is on and live trading is off until an operator
switches it on.
