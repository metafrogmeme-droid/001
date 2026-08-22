"""
RUNECLAW Backtest Validation Gate — has this strategy ever been shown to work?

WHAT THIS FILE USED TO SAY ABOUT ITSELF, and why it could not just be wired:

    NOTE: Stub implementation — not yet wired to backtest harness.
    record_validation() must be called manually; no automated pipeline invokes it.
    The engine (bot/core/engine.py) does not consult this gate before executing
    trades.

Three facts made "just consult it" impossible, and each is fixed here rather
than papered over:

1. **Storage was in-memory only.** Every restart lost every validation. A gate
   that blocks unvalidated strategies plus storage that empties on restart is a
   trading halt on every redeploy — and one nobody would attribute to this file.
   Validations persist now.

2. **`is_validated()` answered `False` for a strategy nobody had ever tested**,
   collapsing "tested and failed" into "never tested". Those are different
   facts and only one of them is evidence. The verdict is three-valued now, and
   `get_validation_status` no longer reports `sharpe: 0.0` for a strategy that
   was never measured — `0.0` is a real, achievable Sharpe, and printing it for
   an absent reading is the `.get("pnl", 0)` shape this repo has a table about.

3. **Nothing recorded anything.** A gate with no recorder either blocks
   everything or is decorative. `bot/backtest/runner.py` records automatically
   now, so the gate has data by the time anything asks it.

MODE, and why the default is `shadow`. Same three modes as
`bot/guardian/integrity_veto.py` and `authority.py`, for the same reason: a
control that starts enforcing on the day it lands is a control whose first
production signal is a refused trade. `off` skips it entirely, `shadow`
observes and records what it WOULD have rejected, `enforce` rejects. The risk
engine's hook is gated by `CONFIG.risk.validation_gate_enabled` (default OFF),
so this file is inert until an operator opts in, and then observes before it
blocks.

VETO-ONLY. Like the integrity veto, the verdict can only ever tighten. There is
deliberately no "this strategy is good, size up" — a passed backtest is the
absence of one reason to refuse, not a reason to trade.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from bot.compat import UTC

log = logging.getLogger("runeclaw.validation_gate")

VALID_MODES = ("off", "shadow", "enforce")

#: The three answers. `NEVER_TESTED` is NOT a synonym for `FAILED` — one is an
#: absent measurement and the other is a measured negative, and an operator
#: reading "unvalidated" deserves to know which.
PASSED = "passed"
FAILED = "failed"
NEVER_TESTED = "never_tested"

#: Minimum evidence for a verdict at all. Below this the sample is too small to
#: mean anything, so the result is a measured FAILED rather than a pass on a
#: handful of lucky trades.
MIN_TRADES = 10
MIN_SHARPE = 0.6


@dataclass
class ValidationResult:
    """Stored result of a strategy validation run."""
    strategy_name: str
    sharpe: float
    max_drawdown: float
    win_rate: float
    total_trades: int
    walk_forward_score: float
    validated_at: str
    passed: bool


class BacktestValidationGate:
    """Records backtest validations and answers whether a strategy has one.

    Thread-safe. Persists to ``<state_dir>/validations.json`` when given a path,
    because an in-memory gate silently changes policy at every restart.
    """

    def __init__(self, path: Optional[str | Path] = None) -> None:
        self._lock = threading.Lock()
        self._validations: dict[str, ValidationResult] = {}
        self._path = Path(path) if path else None
        if self._path:
            self._load()

    # ── Persistence ──────────────────────────────────────────
    #
    # Fail-open on read, fail-loud-but-harmless on write. A corrupt file must
    # not stop the bot booting; it must also not silently masquerade as "no
    # strategy has ever been validated", so the load logs what it could not read.

    def _load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return                                   # first run: genuinely empty
        except Exception as exc:                     # noqa: BLE001
            log.warning("validation store unreadable (%s) — starting empty. "
                        "Every strategy will read NEVER_TESTED until it is "
                        "re-validated; that is an absent record, not a failed "
                        "one.", exc)
            return
        if not isinstance(raw, dict):
            log.warning("validation store is not an object — ignoring it.")
            return
        for name, row in raw.items():
            try:
                self._validations[str(name)] = ValidationResult(**row)
            except Exception:                        # noqa: BLE001
                log.warning("validation record for %r is malformed — skipped. "
                            "It reads NEVER_TESTED, not FAILED.", name)

    def _save(self) -> None:
        if not self._path:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(
                json.dumps({k: asdict(v) for k, v in self._validations.items()},
                           indent=2, sort_keys=True),
                encoding="utf-8")
            tmp.replace(self._path)                  # atomic
        except Exception as exc:                     # noqa: BLE001
            log.warning("could not persist validations (%s) — this run's "
                        "results will not survive a restart.", exc)

    # ── Recording ────────────────────────────────────────────

    def record_validation(
        self,
        strategy_name: str,
        sharpe: float,
        max_drawdown: float,
        win_rate: float,
        total_trades: int,
        walk_forward_score: float,
    ) -> None:
        """Store a validation result for a strategy."""
        with self._lock:
            passed = self._check_pass(sharpe, max_drawdown, win_rate, total_trades)
            self._validations[strategy_name] = ValidationResult(
                strategy_name=strategy_name,
                sharpe=sharpe,
                max_drawdown=max_drawdown,
                win_rate=win_rate,
                total_trades=total_trades,
                walk_forward_score=walk_forward_score,
                validated_at=datetime.now(UTC).isoformat(),
                passed=passed,
            )
            self._save()

    @staticmethod
    def _check_pass(
        sharpe: float,
        max_drawdown: float,
        win_rate: float,
        total_trades: int,
        min_sharpe: float = MIN_SHARPE,
    ) -> bool:
        """Does the strategy meet minimum validation criteria?

        Sharpe and sample size only, and that is DELIBERATE rather than an
        oversight: `max_drawdown` and `win_rate` are recorded and displayed but
        do not gate, because a drawdown threshold that has never been calibrated
        against this book would reject strategies for a number nobody chose. A
        criterion nobody picked is worse than one criterion honestly applied.
        They are stored so a future threshold can be set from real distributions
        rather than guessed.
        """
        return sharpe >= min_sharpe and total_trades >= MIN_TRADES

    # ── Queries ──────────────────────────────────────────────

    def verdict(self, strategy_name: str, min_sharpe: float = MIN_SHARPE) -> str:
        """PASSED / FAILED / NEVER_TESTED — never a two-way collapse.

        The old `is_validated()` returned a bare False for both "we measured it
        and it was bad" and "nobody has ever measured it". A gate acting on that
        cannot tell a rejected strategy from an unexamined one, and neither can
        the operator reading its output.
        """
        with self._lock:
            v = self._validations.get(strategy_name)
        if v is None:
            return NEVER_TESTED
        return PASSED if (v.passed and v.sharpe >= min_sharpe) else FAILED

    def is_validated(self, strategy_name: str, min_sharpe: float = MIN_SHARPE) -> bool:
        """Back-compat: True only for PASSED.

        Kept because callers and tests exist. New code should use `verdict()` —
        this signature cannot express the distinction that matters.
        """
        return self.verdict(strategy_name, min_sharpe) == PASSED

    def has_any_records(self) -> bool:
        """Is this gate reading anything at all?

        THE `integrity_veto.is_reading()` SEAM, for the same reason. An empty
        store makes every strategy NEVER_TESTED, which is correct per-strategy
        and misleading as a headline: "0/0 validated" over a gate whose store
        failed to load reads as a verdict on the strategies rather than on the
        store. Callers that DISPLAY or ENFORCE should ask this first; callers
        that score per-strategy do not need to.
        """
        with self._lock:
            return bool(self._validations)

    def get_validation_status(self, strategy_name: str) -> dict:
        """Full validation status for a strategy."""
        with self._lock:
            v = self._validations.get(strategy_name)
        if v is None:
            return {
                "validated": False,
                "verdict": NEVER_TESTED,
                # NOT 0.0. A Sharpe of zero is a real, achievable result and
                # printing it for a strategy nobody measured is the
                # `.get("pnl", 0)` shape — an absent reading rendered as a
                # measurement. None forces every caller to decide what to show.
                "sharpe": None,
                "badge": "NEVER TESTED",
                "last_validated": None,
                "details": {},
            }
        badge = "VALIDATED ✓" if v.passed else "UNVALIDATED ✗"
        return {
            "validated": v.passed,
            "verdict": PASSED if v.passed else FAILED,
            "sharpe": v.sharpe,
            "badge": badge,
            "last_validated": v.validated_at,
            "details": {
                "max_drawdown": v.max_drawdown,
                "win_rate": v.win_rate,
                "total_trades": v.total_trades,
                "walk_forward_score": v.walk_forward_score,
            },
        }

    def get_all_validations(self) -> dict[str, dict]:
        """All strategies' validation status."""
        # Snapshot keys under the lock, then resolve each status WITHOUT holding
        # the lock.  get_validation_status() re-acquires self._lock, and
        # threading.Lock is non-reentrant, so calling it from inside the lock
        # here deadlocked the calling thread permanently.
        with self._lock:
            names = list(self._validations)
        return {name: self.get_validation_status(name) for name in names}

    # ── Formatting ───────────────────────────────────────────

    def format_for_telegram(self) -> str:
        """War Room styled validation status card."""
        with self._lock:
            validations = dict(self._validations)

        if not validations:
            return (
                "<b>🧪 VALIDATION GATE</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━\n\n"
                "<i>No strategies validated yet.</i>"
            )

        lines = [
            "<b>🧪 VALIDATION GATE</b>",
            "━━━━━━━━━━━━━━━━━━━━━",
            "",
            "<pre>",
            f" {'STRATEGY':<18}{'SHARPE':>7}{'WR':>6}{'TRADES':>7} STATUS",
            f" {'─'*18}{'─'*7}{'─'*6}{'─'*7}{'─'*12}",
        ]

        for name, v in sorted(validations.items()):
            badge = "✓" if v.passed else "✗"
            icon = "🟢" if v.passed else "🔴"
            lines.append(
                f" {icon} {name:<16}"
                f"{v.sharpe:>7.2f}"
                f"{v.win_rate*100:>5.0f}%"
                f"{v.total_trades:>7}"
                f"  {badge}"
            )

        lines.append("</pre>")

        passed = sum(1 for v in validations.values() if v.passed)
        total = len(validations)
        lines.append(
            f"\n<b>{passed}/{total}</b> strategies validated"
        )

        return "\n".join(lines)

    def format_badge(self) -> str:
        """Short inline badge for the War Room dashboard."""
        with self._lock:
            validations = dict(self._validations)

        if not validations:
            return "🧪 <code>NO VALIDATIONS</code>"

        passed = sum(1 for v in validations.values() if v.passed)
        total = len(validations)

        if passed == total and total > 0:
            return f"🧪 <code>ALL VALIDATED ({passed}/{total})</code>"
        if passed > 0:
            return f"🧪 <code>PARTIAL ({passed}/{total})</code>"
        return f"🧪 <code>NONE VALIDATED (0/{total})</code>"


# ── Process-wide gate ────────────────────────────────────────
#
# One store, so the recorder (backtest) and the reader (risk engine) are looking
# at the same records. Without this they would each hold their own dict and the
# gate would report NEVER_TESTED for everything the backtest had just validated.

_GATE: Optional[BacktestValidationGate] = None
_GATE_LOCK = threading.Lock()


def get_validation_gate() -> BacktestValidationGate:
    """The shared gate, backed by ``<state>/validations.json``."""
    global _GATE
    with _GATE_LOCK:
        if _GATE is None:
            state = os.getenv("RUNECLAW_STATE_DIR", "data")
            _GATE = BacktestValidationGate(Path(state) / "validations.json")
        return _GATE
