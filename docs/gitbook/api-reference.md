# API Reference

This document covers the core data models and programmatic interfaces in RUNECLAW. All models are Pydantic v2 `BaseModel`s in the default (lax) mode and are mutable. Field bounds and model validators reject, for example, a negative price, a non-finite entry, stop or target, and a stop or target on the wrong side of entry.

## Data Models

All models are defined in `bot/utils/models.py`.

### MarketSignal

Emitted by the market scanner for each detected opportunity.

```python
class MarketSignal(BaseModel):
    symbol: str                               # e.g. "BTC/USDT"
    price: float                              # Current price, >= 0
    change_pct_24h: float | None = None       # 24h change %; None when the venue reported none
    volume_usd_24h: float                     # 24h turnover in USD, >= 0
    volume_spike: bool = False                # True when volume_spike_ratio > 2.0
    volume_spike_ratio: float | None = None   # 24h turnover over its rolling average; None until measured
    momentum_score: float = 0.0               # [-1.0, 1.0] momentum heuristic
    timestamp: datetime                       # Defaults to now (UTC)
    asset_category: str = "Crypto"            # Crypto | Metal | Commodity | ETF | Pre-IPO | Stock
```

`volume_spike_ratio` is `None` until the scanner has three earlier scans of the symbol, and whenever the turnover is under `MIN_SPIKE_NOTIONAL_USD` (`MarketScanner._measure_volume`). `None` does not count as a spike.

### TradeIdea

A trade thesis produced by the analyzer, or a ticket a person typed. Selected fields; the full list is in `bot/utils/models.py::TradeIdea`.

```python
class TradeIdea(BaseModel):
    id: str                        # e.g. "TI-a1b2c3d4"
    asset: str                     # e.g. "BTC/USDT"
    direction: Direction           # LONG or SHORT
    entry_price: float
    stop_loss: float               # Must be below entry for LONG, above for SHORT
    take_profit: float             # Must be above entry for LONG, below for SHORT
    confidence: float              # [0.0, 1.0]; a measurement, a stamp (1.0 on a typed ticket)
                                   # or a calibrated figure. Read displayed_confidence
    blended_confidence_raw: float | None = None   # The analyzer's blend before calibration
    reasoning: str                 # The thesis behind the call
    signals_used: list[str] = []   # Every indicator key the analysis computed,
                                   # not the votes behind the call
    source: str = "unknown"        # Who produced it: "analyzer", "manual", ...
    order_type: str = "market"     # "market" or "limit"
    strategy_type: str = "swing"   # "scalp" | "intraday" | "swing" | "position"
    signal_type: str = "momentum_confluence"
    confluence_score: float | None = None
    entry_typed: bool = False      # True when the person typed the entry (Limit button)
    market_at_signal: float | None = None   # Market price the analysis was made at
    timestamp: datetime
```

`bot/core/signal_confidence.py::displayed_confidence` is the reading every card prints for `confidence`. The votes behind a call are on the idea's confluence breakdown, which `explain_trade` prints.

**Computed property:**
```python
@property
def risk_reward_ratio(self) -> float:
    risk = abs(self.entry_price - self.stop_loss)
    reward = abs(self.take_profit - self.entry_price)
    return round(reward / risk, 2) if risk > 0 else 0.0
```

### RiskCheck

Result of the pre-trade risk evaluation.

```python
class RiskCheck(BaseModel):
    trade_id: str
    verdict: RiskVerdict                 # APPROVED or REJECTED
    position_size_usd: float = 0.0       # A MARGIN at the sizing leverage, not a notional
    position_pct: float = 0.0            # That margin as % of the equity it was sized on
    daily_loss_pct: float = 0.0          # The day's loss the daily-loss check compared
    drawdown_pct: float = 0.0            # The book's deepest-ever drawdown (its max_drawdown_pct)
    checks_passed: list[str] = []
    checks_failed: list[str] = []
    reason: str = ""                     # The failed checks joined by "; ", or a pass summary
    timestamp: datetime
    size_basis: str = ""                 # Which step decided position_size_usd
    size_path: list[str] = []            # Every sizing step that led there
    base_multiplier: float | None = None # Product of the multipliers applied after the base size
    base_ceiling_usd: float | None = None   # Lowest ceiling after them (half-Kelly, the cap)
    intent_policy: dict | None = None    # Compiled-policy evaluation, when one was consulted
    authority: dict | None = None        # Authority-envelope decision, when one was consulted
```

The per-trade number of checks is the length of `checks_passed` and `checks_failed` on the record.

### TradeExecution

A paper (practice) book's position record, open or closed. It is created only by `PortfolioTracker.open_position` (and restored from a saved book). Live positions are `LivePosition` dataclasses in `bot/core/live_executor.py`, whose `pnl_usd` is `None` when a close could not be priced.

```python
class TradeExecution(BaseModel):
    trade_id: str
    asset: str
    direction: Direction           # LONG or SHORT
    entry_price: float
    quantity: float                # margin * leverage / entry_price
    stop_loss: float
    take_profit: float
    status: TradeStatus = TradeStatus.PENDING   # Paper rows are written EXECUTED, and stay so when closed
    pnl: float = 0.0               # Net of commission once closed; 0.0 on an open row is not a result
    gross_pnl: float = 0.0         # Before commission
    commission: float = 0.0        # Entry + exit commission
    exit_price: float | None = None    # Set on close
    is_paper: bool = True
    leverage: float = 1.0
    entry_atr: float = 0.0         # ATR at entry (0 = not recorded)
    strategy_type: str = "swing"
    signal_type: str = "momentum_confluence"
    venue: str = "bitget"
    fill_label: str = ""           # "PRACTICE" on a self-admitted account's row
    opened_at: datetime
    closed_at: datetime | None = None   # Set on close
```

A row is closed when `exit_price` and `closed_at` are set; `status` does not change on close.

### PortfolioState

Point-in-time snapshot of one book (`PortfolioTracker.snapshot()`). See [Paper Trading](paper-trading.md) for what each figure covers.

```python
class PortfolioState(BaseModel):
    balance_usd: float
    equity_usd: float
    open_positions: int
    total_trades: int
    unpriced_positions: int = 0          # Open positions valued at entry for want of a price
    win_rate: float = 0.0                # Share 0-1 of closed trades with pnl > 0
    total_pnl: float = 0.0               # Net of commission
    total_gross_pnl: float = 0.0
    total_commission: float = 0.0
    daily_pnl: float = 0.0               # Today's realized + open positions' unrealized
    max_drawdown_pct: float = 0.0        # Deepest ever; never recovers
    current_drawdown_pct: float = 0.0    # Peak versus now; recovers with equity
    portfolio_exposure_pct: float = 0.0  # Reserved; not populated
    operating_cost_usd: float = 0.0      # Reserved; not populated
    cost_per_trade: float = 0.0          # Reserved; not populated
    net_of_cost_equity: float = 0.0      # Reserved; not populated
    timestamp: datetime
```

## Enums

```python
class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"

class RiskVerdict(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"

class TradeStatus(str, Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    EXECUTED = "EXECUTED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"

class AgentState(str, Enum):
    IDLE = "IDLE"
    SCANNING = "SCANNING"
    ANALYZING = "ANALYZING"
    RISK_CHECK = "RISK_CHECK"
    CONFIRMING = "CONFIRMING"
    EXECUTING = "EXECUTING"
    MONITORING = "MONITORING"
    COOLING_DOWN = "COOLING_DOWN"
    HALTED = "HALTED"
```

## Engine API

The `RuneClawEngine` class (`bot/core/engine.py`) is the central orchestrator.

An engine built outside the bot process is a reader of the bot's state. Call `detach_state_persistence()` on it first, or it writes the bot's state files. `bot/main.py::run_cli` does this.

### Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `await run()` | `None` | Start the continuous scan-analyze-monitor loop |
| `await stop()` | `None` | Stop the engine and close exchange connections |
| `await confirm_trade(trade_id, user_id="")` | `str` | Confirm a pending idea. Re-checks risk, then opens it on that person's practice book or places a live order on the account `user_id` trades. Returns the text shown to the person |
| `reject_trade(trade_id)` | `str` | Discard a pending idea |
| `confirm_is_practice(user_id)` | `bool` | Whether this person's confirm opens on their practice book |
| `risk_for(user_id="", venue="")` | `RiskEngine` | The risk engine whose breakers apply to this caller's live trading |
| `practice_risk_for(user_id)` | `RiskEngine` | The risk engine bound to this person's practice book |
| `viewer_executor(user_id="")` | `LiveExecutor` or `None` | The live account this caller may view; `None` means none |
| `detach_state_persistence()` | `None` | Make this engine a reader that never writes the bot's state |

`user_id` decides whose book a call reads or writes. An empty `user_id` is an unattended caller, not the operator: it has no practice book, and a live confirm without a person needs `AUTO_CONFIRM_LIVE_ENABLED` and an eligibility record for the running strategy.

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `pending_ideas` | `list[TradeIdea]` | Currently pending trade ideas |
| `portfolio` | `PortfolioTracker` | The engine's own paper book |
| `user_portfolios` | `MultiUserPortfolio` | Per-person practice books; `user_portfolios.get(user_id)` returns one |
| `risk` | `RiskEngine` | The operator's (shared) risk engine |
| `live_executor` | `LiveExecutor` | The operator's live executor |
| `state` | `AgentState` | Current pipeline stage |
| `state_history` | `list[StateTransition]` | Recent state transitions |

## Skill Interface

All skills implement the `BaseSkill` abstract class:

```python
class BaseSkill(ABC):
    name: str = "unnamed"
    description: str = ""

    @abstractmethod
    async def execute(self, engine: RuneClawEngine, **kwargs) -> str:
        """Run the skill and return a human-readable result string."""
```

### Registering a Custom Skill

```python
from bot.skills.skill_registry import BaseSkill, SkillRegistry

class MySkill(BaseSkill):
    name = "my_skill"
    description = "Does something useful"

    async def execute(self, engine, **kwargs):
        return "Result"

registry = SkillRegistry()
registry.register(MySkill())
```

`await registry.dispatch(name, engine, **kwargs)` runs a skill by name and returns an error string, rather than raising, when no skill has that name.

## Audit Logger

The `audit()` function writes structured JSON to the appropriate channel:

```python
from bot.utils.logger import audit, trade_log

audit(
    trade_log,
    "Trade idea generated",
    action="analyze",
    reasoning="RSI oversold + volume spike",
    result="BUY BTC",
    data={"confidence": 0.82}
)
```

### Log Entry Format

```json
{
  "ts": "2026-05-15T14:30:00.123456Z",
  "level": "INFO",
  "channel": "runeclaw.trade",
  "message": "Trade idea generated",
  "prev_hash": "9f2c…",
  "action": "analyze",
  "reasoning": "RSI oversold + volume spike",
  "result": "BUY BTC",
  "data": {"confidence": 0.82}
}
```

`prev_hash` is the SHA-256 of the previous line this process wrote to the same file (`GENESIS` for its first), so editing or deleting a line breaks the chain from that point. Values that look like secrets are redacted before a line is written.

### Log Channels

| Channel | Logger | File |
|---------|--------|------|
| Trade | `trade_log` | `logs/trade.jsonl` |
| Risk | `risk_log` | `logs/risk.jsonl` |
| System | `system_log` | `logs/system.jsonl` |
| Scan | `scan_log` | `logs/scan.jsonl` |

Each file rotates at 10 MB and keeps 5 backups. INFO and above is also written to stderr. Separately, the engine seals decisions in a hash-chained `logs/audit_chain.jsonl` (`bot/utils/audit_chain.py::AuditChain`).
