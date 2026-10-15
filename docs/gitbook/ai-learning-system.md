# AI Learning System

RUNECLAW's learning code (`bot/learning/`) records what the bot decided and what happened, and
turns part of that record into observations and improvement proposals. The **8 modules** on this
page are advisory: none of them changes a trade, and none can override the risk engine.

Three other learners in `bot/learning` do act on the decision path, before the risk gate reads an
idea: **setup expectancy** (`SETUP_EXPECTANCY_ENABLED`, on by default) nudges an idea's
confidence by at most ±0.05 from the bot's own record on similar setups, and **confidence
calibration** and **voter-weight learning** (both off by default) can reshape it. All three are
refit automatically every 25 closed trades (`LEARNING_AUTO_REFIT_ENABLED`, on by default;
`bot/learning/auto_refit.py`). They change an idea's confidence, never the gate: the idea still
has to pass every risk check.

**Try it:** send `/learn` to [@HTRUNECLAW_bot](https://t.me/HTRUNECLAW_bot).

---

## The 8 modules

### 1. Experience Memory

Decisions are written to the learning store: every idea the risk gate rejects, every confirmed
live order (placed, failed or unverified), and paper fills when `LEARN_CALIBRATION_FROM_PAPER` is
on (default off). Each closed trade adds an outcome row (paper closes too, while
`LEARN_FROM_PAPER_CLOSES` is on, the default). This is the record the other learners read, for
example setup expectancy's "how have longs on this symbol in this regime done before?".

**Implementation:** `bot/learning/experience.py` -- `ExperienceMemory`, stored as append-only
JSONL (`decision_memory.jsonl`). A decision row holds:
- Symbol, direction, confidence and its basis
- Confluence score and each confluence voter's vote
- Entry, stop, target, R:R and size
- The risk verdict, with the checks passed and failed and the rejection reason
- The decision and the mode (paper or live)

An outcome row holds the symbol, direction, market regime and realized P&L of a closed trade.

---

### 2. Reflection Engine

When the risk gate rejects an idea, the engine records a reflection on that rejection: what was
expected, what happened, and that the rejection should be checked against the later price.

A post-trade reflection is built too (`LearningOrchestrator.review_trade`: was the signal valid,
was the sizing right, did the stop and target behave), and it would generate an improvement
proposal from each lesson. Nothing calls it yet. Reflections may recommend, never auto-apply.

**Implementation:** `bot/learning/reflection.py` -- `ReflectionEngine`, producing
`ReflectionMemory` records with:
- What was expected and what happened
- Whether the signal, the risk decision, the sizing and the stop/target behaved (unknown until
  measured)
- The lesson and a recommended improvement

---

### 3. Strategy Evaluator

Risk-adjusted scoring with **S/A/B/C/D tiers**. A strategy is **never promoted on profit
alone**: the tier comes from a safety score first.

**Scoring dimensions:**
- Safety score (from drawdown, profit factor, false-positive rate and sample size)
- Sharpe-like ratio
- Maximum drawdown
- Win rate
- False-positive rate
- Overfitting flag (fewer than 30 trades with a win rate above 80%), which holds the strategy at
  tier C

Tiers are shown on `/learn` for a person to review; the scanner does not read them. The scoring
(`LearningOrchestrator.score_strategy`) has no caller yet, so no scorecard is produced in
operation.

**Implementation:** `bot/learning/strategy_eval.py` -- `StrategyEvaluator`.

---

### 4. Pattern Learner

Groups the closes in the learning store by the condition each row recorded: the market regime
(TREND_UP, RANGE, CHOP, …), a macro state other than NORMAL, or low volatility. A group is
what happened under that condition, not a pattern that predicts it, and nothing reads it to make
a decision: it is printed by the operator's `/patterns` card and nowhere else.

Observations only. **Patterns may NEVER override the risk engine** (enforced by a Pydantic
validator on the `may_override_risk` field, which must always be `False`).

**Implementation:** `bot/learning/patterns.py` -- `PatternLearner.outcome_groups`. Records
`PatternRecord` objects with:
- The dimension the group is cut on and the group's value on it
- Wins, losses and flat closes, and the win rate over scored closes
- The 95% Wilson interval on that win rate
- `is_experimental` flag for groups with fewer than 20 scored closes

No confidence and no return figure is computed for a group: the store's outcome rows carry P&L
in dollars with no stop or size, from every account's closes.

---

### 5. Macro Learner

Stores how BTC reacted to a macro event (FOMC, CPI, NFP, PCE and others) 5 minutes, 30 minutes,
4 hours and 24 hours after it, with the event's forecast, actual and surprise, and averages those
reactions per event type. **Context only -- it never creates a trade signal.** Nothing feeds it
events yet (`MacroLearner.record_event` has no caller).

**Implementation:** `bot/learning/macro_learner.py` -- `MacroLearner`, storing
`MacroEventMemory` records.

---

### 6. Model Comparer

Built to compare the rule engine's direction and confidence with an LLM's on each trade, and to
track agreement and accuracy over time. Nothing records a comparison yet
(`ModelComparer.record_comparison` has no caller), and the token optimizer does not read it. A
comparison row can mark a strong disagreement as `NO_TRADE_UNCERTAIN`; that is a label on the
row, not a trading action.

**Implementation:** `bot/learning/model_compare.py` -- `ModelComparer`.

---

### 7. Prompt Optimizer

Built to version prompts, score each version's use (JSON validity, reasoning clarity, false
signals, cost, latency) and refuse unsafe wording. No prompt version is registered yet
(`register_version` and `record_usage` have no caller).

**Refuses to register a prompt that:**
- Contains unsafe wording ("guaranteed profit", "risk-free", "ignore risk", "bypass
  confirmation", "auto-execute" and others)
- Has no fail-closed language ("fail-closed", "reject" or "block")

**Implementation:** `bot/learning/prompt_opt.py` -- `PromptOptimizer`. A version stores a
SHA-256 prefix of the prompt text (not the text), its usage scores and two safety flags.

---

### 8. Feedback Collector

Stores a person's feedback on a decision, under one of 12 types: `correct`, `incorrect`,
`too_risky`, `too_conservative`, `unclear_explanation`, `missing_macro_context`,
`missing_risk_reason`, `bad_symbol_choice`, `good_rejection`, `good_explanation`,
`needs_more_evidence`, `needs_doc_update`.

No Telegram command or web route submits feedback yet, confirm and reject taps are not written to
it, and the only reader is the summary on `/learn`. It cannot bypass the risk gate.

**Implementation:** `bot/learning/feedback.py` -- `FeedbackCollector`.

---

## The safety policy

Every improvement proposal is classified by the safety policy (`bot/learning/safety_policy.py`)
when it is created. Nothing in the running bot creates one yet: proposals come from the
post-trade reflection and the prompt optimizer, and neither is called.

### Allowed vs blocked

| Allowed | Blocked |
|---------|---------|
| Improve explanations | Enable live trading |
| Suggest safer filters | Increase leverage |
| Detect recurring mistakes | Remove stop-losses |
| Rank strategies for review | Bypass risk engine |
| Approve docs & tests without human review | Delete audit logs |
| Produce improvement proposals | Claim guaranteed profit |

> **Approved is not applied.** Nothing in `bot/learning` writes a file or edits
> code. The system produces proposals, classifies them, and marks the safe ones
> as needing no human review — a human still has to act on every one.

### Proposal classification

Classification is by keyword: a proposal whose text mentions leverage, bypassing, removing a stop
or a limit, live trading and the like is blocked; wording that weakens safety is blocked as a
compliance risk.

| Classification | Action |
|----------------|--------|
| `SAFE_AUTO_DOCS` | Approved without review — a human still applies it |
| `SAFE_AUTO_TEST` | Approved without review — a human still applies it |
| `HUMAN_REVIEW_REQUIRED` | Queued for admin approval |
| `BLOCKED_RISK_INCREASE` | Automatically rejected |
| `BLOCKED_COMPLIANCE_RISK` | Automatically rejected |

### The hard safety invariant

The `may_override_risk` field on `PatternRecord` is held to `False` by a **Pydantic validator**.
Setting it to anything else raises a validation error, at construction and on later assignment
(`validate_assignment=True`):

```python
model_config = ConfigDict(validate_assignment=True)

@field_validator("may_override_risk")
@classmethod
def must_be_false(cls, v):
    """Safety invariant: patterns may NEVER override the risk engine."""
    if v is not False:
        raise ValueError("may_override_risk must ALWAYS be False")
    return False
```

---

## Learning workflow

What runs today, and what is built but not yet called:

```text
 1. DECIDE     Rule engine decides; the pre-trade risk gate rules       (runs)
 2. LOG        Write the decision to experience memory (append-only)    (runs)
 3. REFLECT    Record a reflection on each rejection                    (runs)
 4. OUTCOME    Record each closed trade's outcome                       (runs)
 5. REFIT      Refit expectancy, calibration, voter weights every 25    (runs)
 6. GROUP      Group closes by condition for /patterns                  (on request)
 7. REVIEW     Post-trade reflection after each close                   (built, not called)
 8. SCORE      Strategy scorecard S/A/B/C/D                             (built, not called)
 9. PROPOSE    Proposals from lessons and prompt scores                 (built, not called)
10. CLASSIFY   Safety policy: approve docs/tests, queue the rest, block (built, not called)
```

The workflow ends at classification. Nothing applies a proposal — no file is written and no code
is edited anywhere in `bot/learning`. An approved proposal is one a human may act on without
further review, not one that has been acted on.

---

## Telegram commands

| Command | Who can run it | Description |
|---------|----------------|-------------|
| `/learn` | viewer, paper, trader, admin | Learning dashboard: module health, learning score, record and feedback counts, proposal counts, and strategy tiers when any exist. Needs the Elite tier when the tier ladder is enforced (off by default) |
| `/patterns` | operator (admin) | The learning store's closes by regime, with win rates and their intervals |
| `/proposals` | paper, trader, admin | The latest improvement proposals and their classification |

---

## Architecture

```text
Trade decision (rule engine + risk gate)
     |
     v
Experience Memory (log)               ← Module 1   (runs)
     |
     +--> Reflection on rejections    ← Module 2   (runs)
     |
     +--> Closed-trade outcomes ----> Setup expectancy / calibration / voter weights
     |                                (confidence only, before the risk gate)
     |
     +--> Pattern Learner (/patterns) ← Module 4
     |
     v
Strategy Evaluator, Macro Learner, Model Comparer,
Prompt Optimizer, Feedback Collector  ← Modules 3, 5-8 (built; not fed yet)
     |
     v
Proposal generation
     |
     v
Safety policy filter  ◄── BLOCKED if risk-increasing
     |
     v
Human review (if required)
     |
     v
Approved (a human applies it)
```

The eight modules cannot modify risk parameters, enable live trading or change execution logic.
The orchestrator (`bot/learning/orchestrator.py`) coordinates them.

---

## Why it is built this way

A learning loop that can change trades is a risk of its own. So the eight modules stay
advisory, the learners that do touch an idea can only shade its confidence before the risk gate
reads it, and every proposal goes through the safety policy and a person.
