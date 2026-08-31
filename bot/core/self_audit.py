"""
RUNECLAW — nightly LLM self-audit (advisory only, human merge gate).

Once a night the bot reads its own evidence — recent closed trades, the
shadow book's per-gate price tags, governor/throttle state — and asks the
LLM: "given THIS, which of the allowlisted knobs would you turn?" Every
proposal is then MEASURED on a frozen benchmark before the operator sees
it: the report that lands in Telegram carries the rationale, the measured
delta, and the exact env diff to apply.

Nothing is ever applied automatically. The audit cannot invent knobs (a
fixed allowlist with type/bounds validation), cannot exceed bounds, and
cannot skip measurement — an unmeasured proposal is reported as such and
marked NOT VERIFIED. The human is the merge gate, exactly like a PR.

Cost/safety posture: runs at a configured quiet hour, at most once per
~24h (persisted stamp survives restarts), skips silently when no LLM is
configured, caps proposals, runs benchmark subprocesses sequentially, and
every stage fails open — a broken audit can never touch trading.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess  # nosec B404 — fixed argv, no shell, own runner module
import sys
import time
from typing import Any, Callable, Optional

from bot.utils.atomic_write import atomic_write_json

logger = logging.getLogger(__name__)

_STATE_DIR = os.environ.get("RUNECLAW_STATE_DIR", "data")
DEFAULT_STATE_FILE = os.path.join(_STATE_DIR, "self_audit_state.json")

_MIN_INTERVAL_SEC = 20 * 3600.0   # once a night, restart-proof
_BACKTEST_TIMEOUT_SEC = 900.0     # per benchmark run

# The ONLY knobs the audit may propose. Everything else the LLM suggests
# is dropped at validation. Bounds are deliberately tighter than the
# config's own env bounds — the audit proposes tweaks, not regime changes.
ALLOWED_FLAGS: dict[str, dict[str, Any]] = {
    "EQUITY_THROTTLE_ENABLED": {"type": "bool"},
    "ENTRY_TIMING_ENABLED": {"type": "bool"},
    "STRUCTURE_TRAIL_ENABLED": {"type": "bool"},
    "CANDLE_ENTRY_VETO_ENABLED": {"type": "bool"},
    "REENTRY_COOLDOWN_SECONDS": {"type": "float", "min": 0, "max": 14400},
    "TREND_UP_SIZE_MULT": {"type": "float", "min": 0.3, "max": 1.2},
    "LIVE_PERF_REDUCE_WINRATE": {"type": "float", "min": 0.25, "max": 0.55},
    "LIVE_PERF_REDUCE_MULT": {"type": "float", "min": 0.25, "max": 0.75},
    "VOLATILITY_GUARD_ATR_PCT": {"type": "float", "min": 0.03, "max": 0.15},
    "SYMBOL_LOSS_STREAK_THRESHOLD": {"type": "float", "min": 2, "max": 6},
    # Liquidity-guard knobs (order_flow): the audit found these measured
    # nightly but untunable by the self-audit. The executor's spread ceiling
    # DERIVES from OF_MAX_SPREAD_BPS (2x) unless overridden, so proposing a
    # tighter/looser guard moves both layers coherently.
    "OF_MAX_SPREAD_BPS": {"type": "float", "min": 10, "max": 200},
    "OF_MIN_DEPTH_USD": {"type": "float", "min": 500, "max": 50_000},
}

_SYSTEM_PROMPT = """You are the nightly self-audit of RUNECLAW, a live \
crypto perpetuals trading bot. You receive the bot's own recent evidence \
and may propose changes ONLY to the allowlisted environment flags given, \
within their bounds. Propose a change only when the evidence supports it; \
an empty list is a good answer. In the evidence, `null` means NOT MEASURED — \
never treat it as zero, and never infer a result from a figure that is null \
or from a summary carrying an `error` key. `summary.scored` is how many \
closes could be priced; when it is small or zero the record is too thin to \
support any proposal. Respond with STRICT JSON only — an array \
of at most {max_proposals} objects, no prose, no markdown fences:
[{{"flag": "<ALLOWLISTED_FLAG>", "value": <bool|number>, \
"rationale": "<one sentence tied to the evidence>"}}]"""


#: Where each allowlisted flag's value actually lives once CONFIG has
#: resolved it. Kept BESIDE the bounds rather than derived, because a flag
#: whose path nobody wrote is a flag whose no-op check silently stops
#: running — the exact failure this table exists to end. A missing or
#: unresolvable entry fails
#: tests/test_self_audit_reports_what_it_measured.py, so the table cannot
#: quietly fall behind ALLOWED_FLAGS.
FLAG_CONFIG_PATHS: dict[str, str] = {
    "EQUITY_THROTTLE_ENABLED": "risk.equity_throttle_enabled",
    "ENTRY_TIMING_ENABLED": "execution.entry_timing_enabled",
    "STRUCTURE_TRAIL_ENABLED": "trailing.structure_trail_enabled",
    "CANDLE_ENTRY_VETO_ENABLED": "analyzer.candle_entry_veto_enabled",
    "REENTRY_COOLDOWN_SECONDS": "risk.reentry_cooldown_seconds",
    "TREND_UP_SIZE_MULT": "risk.trend_up_size_mult",
    "LIVE_PERF_REDUCE_WINRATE": "risk.live_perf_reduce_winrate",
    "LIVE_PERF_REDUCE_MULT": "risk.live_perf_reduce_mult",
    "VOLATILITY_GUARD_ATR_PCT": "risk.volatility_guard_atr_pct",
    "SYMBOL_LOSS_STREAK_THRESHOLD": "risk.symbol_loss_streak_threshold",
    "OF_MAX_SPREAD_BPS": "order_flow:max_spread_bps",
    "OF_MIN_DEPTH_USD": "order_flow:min_top_depth_usd",
}


def effective_value(flag: str, env: Optional[dict] = None):
    """The value in force for ``flag``: env if set, else CONFIG's resolved one.

    Returns ``None`` when neither can be read — and that is a real third
    state, not a stand-in for "unset". An unreadable current value means the
    no-op check cannot be made, so the proposal is allowed through and
    measured rather than dropped on a comparison nobody performed.
    """
    if env:
        raw = env.get(flag)
        if raw is not None and str(raw).strip() != "":
            return str(raw).strip()
    path = FLAG_CONFIG_PATHS.get(flag)
    if not path:
        return None
    # The two liquidity knobs are NOT on CONFIG. OrderFlowConfig lives in
    # bot/core/order_flow.py and is constructed per-analyzer rather than held
    # as a singleton, so the value in force is what a fresh construction reads
    # from the environment — which is exactly what the analyzer got.
    if path.startswith("order_flow:"):
        try:
            from bot.core.order_flow import OrderFlowConfig
            return getattr(OrderFlowConfig(), path.split(":", 1)[1])
        except Exception:
            return None
    try:
        from bot.config import CONFIG
        node: Any = CONFIG
        for part in path.split("."):
            node = getattr(node, part)
        return node
    except Exception:
        return None


def _same_value(current: Any, norm: str, kind: str) -> bool:
    """Compare a live config value with a normalised proposal string."""
    try:
        if kind == "bool":
            if isinstance(current, bool):
                return ("1" if current else "0") == norm
            return str(current).strip().lower() in (
                ("1", "true", "yes", "on") if norm == "1"
                else ("0", "false", "no", "off"))
        return abs(float(current) - float(norm)) < 1e-9
    except Exception:
        # Unreadable is not "different". Saying they differ would let a
        # genuine no-op through on a failed comparison; saying they match
        # would drop a real proposal. Neither is safe to assert, so the
        # caller treats None-ish as "could not check" and measures it.
        return False


def validate_proposals(raw: list, current_env: Optional[dict] = None,
                       max_proposals: int = 2) -> list[dict]:
    """Filter LLM proposals to allowlisted flags with in-bounds values.

    Drops: unknown flags, wrong types, out-of-bounds values, duplicates,
    and no-ops (value equals the current env setting when provided).
    Pure — no I/O — so the gate is unit-testable."""
    out: list[dict] = []
    seen: set[str] = set()
    env = current_env if current_env is not None else {}
    for p in (raw or []):
        try:
            flag = str(p.get("flag", "")).strip().upper()
            spec = ALLOWED_FLAGS.get(flag)
            if spec is None or flag in seen:
                continue
            value = p.get("value")
            if spec["type"] == "bool":
                if not isinstance(value, bool):
                    continue
                norm = "1" if value else "0"
            else:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                v = float(value)
                if not (spec["min"] <= v <= spec["max"]):
                    continue
                norm = f"{v:g}"
            # NO-OP CHECK, against the value actually IN FORCE.
            #
            # This used to read `os.environ` alone, so a flag left at its
            # code default had `env.get(flag) is None` and skipped the check
            # entirely. On 2026-08-31 the audit proposed
            # SYMBOL_LOSS_STREAK_THRESHOLD=3 — which config.py:431 already
            # defaults to — measured no change, and printed
            # "⬜ measured +3.14% (+0.00pp vs baseline)" under a rationale
            # promising it would "raise win rate toward 0.50". A change that
            # is not a change, reported as one that was tried and did not
            # help.
            #
            # Absent from the environment is not absent from the CONFIG. The
            # comparison is against the resolved value now.
            cur = effective_value(flag, env)
            if cur is not None and _same_value(cur, norm, spec["type"]):
                continue  # no-op
            seen.add(flag)
            out.append({"flag": flag, "value": norm,
                        "rationale": str(p.get("rationale", ""))[:240]})
            if len(out) >= max_proposals:
                break
        except Exception:
            continue
    return out


def parse_llm_json(text: str) -> list:
    """Extract the first JSON array from an LLM response, tolerating
    markdown fences and surrounding prose. Returns [] when unparseable."""
    try:
        m = re.search(r"\[.*\]", text or "", re.DOTALL)
        if not m:
            return []
        data = json.loads(m.group(0))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _benchmark_root():
    """Where the frozen snapshots live. Resolved per call — they moved out from
    under the data/ symlink so they could be committed (see
    bot.backtest.snapshot.benchmark_root); the old path still resolves."""
    from bot.backtest.snapshot import benchmark_root
    return benchmark_root()


def _parse_metrics(stdout: str) -> dict:
    """Pull the runner's headline metrics out of its stdout."""
    out: dict[str, float] = {}
    pats = {"return_pct": r"Total Return:\s*([+-]?[\d.]+)%",
            "trades": r"Total Trades:\s*(\d+)",
            "pf": r"Profit Factor:\s*([\d.]+|inf)",
            "max_dd_pct": r"Max Drawdown:\s*([\d.]+)%"}
    for key, pat in pats.items():
        m = re.search(pat, stdout or "")
        if m:
            try:
                out[key] = float(m.group(1))
            except ValueError:
                pass
    return out


def run_benchmark(dataset: str, env_overrides: Optional[dict] = None) -> dict:
    """Run one frozen-benchmark backtest in a subprocess and return its
    headline metrics ({} on any failure). Blocking — callers off-load."""
    try:
        env = dict(os.environ)
        env.update({k: str(v) for k, v in (env_overrides or {}).items()})
        proc = subprocess.run(  # nosec B603 — fixed argv, no shell
            [sys.executable, "-m", "bot.backtest.runner",
             "--dataset", str(_benchmark_root() / dataset),
             "--honest"],
            capture_output=True, text=True, env=env,
            timeout=_BACKTEST_TIMEOUT_SEC)
        return _parse_metrics(proc.stdout)
    except Exception as exc:
        logger.warning("self-audit benchmark run failed: %s", exc)
        return {}


class SelfAudit:
    """Nightly evidence -> LLM proposals -> measured verdicts -> report."""

    def __init__(self, state_file: Optional[str] = None,
                 run_backtest: Optional[Callable[..., dict]] = None) -> None:
        self.state_file = state_file or DEFAULT_STATE_FILE
        self._run_backtest = run_backtest or run_benchmark
        self._running = False
        self._pending: list[dict] = []
        self._last_report: str = ""

    # ── persistence ───────────────────────────────────────────────
    def _load_state(self) -> dict:
        try:
            with open(self.state_file, encoding="utf-8") as f:
                return dict(json.load(f))
        except Exception:
            return {}

    def _save_state(self, state: dict) -> None:
        try:
            atomic_write_json(self.state_file, state, indent=None)
        except Exception as exc:
            logger.debug("self-audit state save failed: %s", exc)

    def last_report(self) -> str:
        if self._last_report:
            return self._last_report
        return str(self._load_state().get("last_report", ""))

    def drain_pending(self) -> list[dict]:
        out, self._pending = self._pending, []
        return out

    # ── scheduling ────────────────────────────────────────────────
    def due(self, now_ts: Optional[float] = None) -> bool:
        """True when the audit should run: inside the configured UTC hour
        and at least ~24h since the last run (persisted)."""
        from bot.config import CONFIG
        if not getattr(CONFIG, "self_audit_enabled", False) or self._running:
            return False
        now = float(now_ts if now_ts is not None else time.time())
        hour = int(time.gmtime(int(now)).tm_hour)
        if hour != int(getattr(CONFIG, "self_audit_hour_utc", 4)):
            return False
        last = float(self._load_state().get("last_run_ts", 0) or 0)
        return (now - last) >= _MIN_INTERVAL_SEC

    def maybe_spawn(self, engine, now_ts: Optional[float] = None) -> bool:
        """Fire-and-forget the nightly run when due. Never raises."""
        try:
            if not self.due(now_ts):
                return False
            asyncio.get_running_loop().create_task(self.run(engine))
            return True
        except Exception as exc:
            logger.debug("self-audit spawn skipped: %s", exc)
            return False

    # ── evidence ──────────────────────────────────────────────────
    def gather_evidence(self, engine) -> dict:
        """Snapshot the bot's own recent record. Every piece fail-open."""
        ev: dict[str, Any] = {}
        try:
            from bot.utils.win_rate import (
                pnl_stats, profit_factor, trade_pnl, win_stats,
            )
            ex = getattr(engine, "live_executor", None)
            closed = list(getattr(ex, "_closed_trades", []) or [])[-40:]
            trades = []
            for t in closed:
                p = trade_pnl(t)
                trades.append({
                    "symbol": getattr(t, "symbol", "?"),
                    "dir": getattr(t, "direction", "?"),
                    "strategy": getattr(t, "strategy_type", "") or "",
                    # None, not 0: a close whose P&L the record cannot price is
                    # not a break-even close. `null` reaches the model as an
                    # absence; 0 reaches it as a measurement.
                    "net_pnl": None if p is None else round(p, 3),
                    "reason": str(getattr(t, "close_reason", "") or "")[:40],
                })
            ev["closed_trades"] = trades
            ws = win_stats(closed)
            ps = pnl_stats(closed)
            pf = profit_factor(closed)
            ev["summary"] = {
                "n": ws["total"],
                # The window every figure below is computed over. It travels
                # with them because "60% of 20" and "60% of the 12 we could
                # price" are different readings and only this tells them apart.
                "scored": ws["scored"],
                "unpriced": ws["unscored"],
                "win_rate": None if ws["rate"] is None else round(ws["rate"], 3),
                "net_pnl": None if ps["total"] is None else round(ps["total"], 2),
                "pf": None if pf is None else round(pf, 2),
            }
        except Exception as exc:
            # An unreadable record is not an empty one. `[]` here told the
            # model "the bot has closed no trades" — a confident, false
            # statement about a live account, fed straight into a prompt that
            # proposes config changes off it.
            logger.warning("self-audit evidence unreadable: %s", exc)
            ev["closed_trades"] = None
            ev["summary"] = {"error": "closed_trade_record_unreadable"}
        try:
            from bot.core.shadow_book import SHADOW_BOOK
            ev["shadow_gates"] = SHADOW_BOOK.gate_report()
            ev["shadow_counts"] = SHADOW_BOOK.counts()
        except Exception:
            pass
        try:
            risk = getattr(engine, "risk", None)
            if risk is not None:
                ev["governor"] = risk.live_performance_state()
                ev["throttle"] = risk.equity_throttle_state()
        except Exception:
            pass
        return ev

    # ── the run ───────────────────────────────────────────────────
    async def run(self, engine) -> Optional[str]:
        """Full audit cycle. Returns the report text, or None if skipped."""
        from bot.config import CONFIG
        if self._running:
            return None
        self._running = True
        try:
            analyzer = getattr(engine, "analyzer", None)
            cfg = analyzer._resolve_llm_config() if analyzer else None
            client = (analyzer._build_client_for_config(cfg)
                      if analyzer and cfg else None)
            if client is None or cfg is None:
                logger.info("self-audit skipped: no LLM configured")
                return None

            evidence = self.gather_evidence(engine)
            max_props = int(getattr(CONFIG, "self_audit_max_proposals", 2))
            bounds = {k: ({"type": "bool"} if v["type"] == "bool" else
                          {"type": "number", "min": v["min"], "max": v["max"]})
                      for k, v in ALLOWED_FLAGS.items()}
            user_prompt = (
                "ALLOWLISTED FLAGS AND BOUNDS:\n"
                + json.dumps(bounds, indent=1)
                + "\n\nCURRENT ENV OVERRIDES (unset = default):\n"
                + json.dumps({k: os.environ.get(k) for k in ALLOWED_FLAGS
                              if os.environ.get(k) is not None})
                + "\n\nEVIDENCE:\n" + json.dumps(evidence, default=str)[:12000])

            from bot.llm.provider import llm_complete
            text = await llm_complete(
                client, cfg,
                _SYSTEM_PROMPT.format(max_proposals=max_props),
                user_prompt)
            proposals = validate_proposals(
                parse_llm_json(text),
                current_env={k: os.environ.get(k) for k in ALLOWED_FLAGS},
                max_proposals=max_props)

            dataset = str(getattr(CONFIG, "self_audit_dataset", "alts_1h"))
            baseline: dict = {}
            results: list[dict] = []
            if proposals:
                baseline = await asyncio.to_thread(self._run_backtest, dataset)
            for p in proposals:
                measured = await asyncio.to_thread(
                    self._run_backtest, dataset, {p["flag"]: p["value"]})
                results.append({**p, "measured": measured})

            report = self.render_report(evidence, results, baseline, dataset)
            self._last_report = report
            self._pending.append({"report": report, "ts": time.time()})
            self._save_state({"last_run_ts": time.time(),
                              "last_report": report})
            return report
        except Exception as exc:
            logger.warning("self-audit run failed: %s", exc)
            return None
        finally:
            self._running = False

    # ── reporting ─────────────────────────────────────────────────
    @staticmethod
    def render_report(evidence: dict, results: list[dict],
                      baseline: dict, dataset: str) -> str:
        s = evidence.get("summary") or {}
        lines = ["\U0001f9fe <b>Nightly self-audit</b>", "─" * 16]
        if s.get("error"):
            # Say so. A missing line reads as "no trades yet", which is the
            # same false claim the empty list used to make one layer down.
            lines.append("Live window: <b>could not be read</b> — no win rate, "
                         "net or PF is shown because none was measured.")
        elif s.get("n"):
            pf = s.get("pf")
            wr = s.get("win_rate")
            net = s.get("net_pnl")
            if net is not None and net == 0:
                net = 0.0        # a -0.0 total formats as "$-0.00", which reads
                                 # as a small loss rather than break-even
            unpriced = int(s.get("unpriced", 0) or 0)
            lines.append(f"Live window: {s['n']} closes"
                         + (f" · win {wr*100:.0f}%" if wr is not None else
                            " · win —")
                         + (f" · PF {pf}" if pf is not None else " · PF —")
                         + (f" · net ${net:,.2f}" if net is not None
                            else " · net —"))
            if unpriced:
                lines.append(f"<i>{s.get('scored', 0)} of {s['n']} closes carry "
                             f"a recorded P&amp;L; {unpriced} do not and are "
                             f"scored neither way.</i>")
        gates = evidence.get("shadow_gates") or {}
        worst = next(iter(gates.items()), None)
        if worst and worst[1].get("net_r", 0) > 0.5:
            # `[:28]` with no marker. The 2026-08-31 card printed
            # "LIQUIDITY: LIQUIDITY: spread" — 28 characters exactly, cut mid
            # -name, and it read as a finished phrase so nothing said it had
            # been cut. Gate keys are categories now (shadow_book
            # .gate_category), so this rarely bites; when it does it says so.
            _g = str(worst[0])
            _g = _g if len(_g) <= 28 else _g[:27] + "…"
            lines.append(f"Shadow book: <code>{_g}</code> is the "
                         f"costliest gate (net {worst[1]['net_r']:+.1f}R "
                         f"over {worst[1]['n']} blocked trades)")
        if not results:
            lines.append("")
            lines.append("No changes proposed — the evidence supports the "
                         "current configuration. (An empty audit is a pass, "
                         "not a failure.)")
            return "\n".join(lines)
        base_ret = baseline.get("return_pct")
        # Trade count included: without it a reader cannot see that a
        # candidate's "39tr" is the baseline's own figure repeated.
        base_s = (f"{base_ret:+.2f}% / PF {baseline.get('pf', '?')}"
                  + (f" / {int(baseline['trades'])}tr"
                     if baseline.get("trades") is not None else "")
                  if base_ret is not None else "unavailable")
        lines.append(f"\nBenchmark <code>{dataset}</code> baseline: {base_s}")
        for r in results:
            m = r.get("measured") or {}
            ret = m.get("return_pct")
            if ret is None or base_ret is None:
                verdict = "⬜ NOT VERIFIED (benchmark run failed)"
            elif _identical_run(m, baseline):
                # EVERY headline figure matched the baseline's, to the digit.
                # That is not a measurement of the change; it is the benchmark
                # failing to distinguish it, and the two have to read
                # differently. On 2026-08-31 both proposals rendered
                # "⬜ measured +3.14% (+0.00pp vs baseline) · PF 1.87 · 39tr"
                # — the baseline's own numbers, presented as a verdict on a
                # change that had been tried.
                #
                # Two things produce this signature and neither is "neutral":
                # a proposal that was not a change (now dropped upstream), and
                # a knob whose trigger the dataset never enters.
                # LIVE_PERF_REDUCE_WINRATE 0.40->0.35 only binds when win rate
                # sits in (0.35, 0.40] AND the window is net-positive, so a
                # benchmark running at PF 1.87 never reaches it. The live book
                # was at 25% and net-negative, where it binds constantly. A
                # null from the wrong instrument is not evidence of no effect.
                verdict = ("⬜ NOT DISTINGUISHED — this benchmark returned the "
                           "baseline's figures unchanged, so it did not "
                           "exercise the change. That is not evidence the "
                           "change is neutral in live.")
            else:
                delta = ret - base_ret
                icon = "\U0001f7e9" if delta > 0 else (
                    "\U0001f7e5" if delta < 0 else "⬜")
                verdict = (f"{icon} measured {ret:+.2f}% "
                           f"({delta:+.2f}pp vs baseline) · "
                           f"PF {m.get('pf', '?')} · {int(m.get('trades', 0))}tr")
            lines.append(f"\n<b>{r['flag']}={r['value']}</b>\n"
                         f"  {r['rationale']}\n"
                         f"  {verdict}\n"
                         f"  Apply: <code>{r['flag']}={r['value']}</code> "
                         f"(env + restart) — nothing auto-applied")
        return "\n".join(lines)



def _identical_run(measured: dict, baseline: dict) -> bool:
    """True when a candidate run matched the baseline on EVERY headline figure.

    Compared across return, PF and trade count rather than return alone: a
    change that shifts the trade count while landing on the same return is a
    real difference the report must not swallow, and `+0.00pp` on its own
    cannot tell the two apart.

    A missing figure on either side makes this False — unknown is not
    identical, and claiming a match from an absent number is the failure this
    whole branch exists to end.
    """
    for k in ("return_pct", "pf", "trades"):
        a, b = measured.get(k), baseline.get(k)
        if a is None or b is None:
            return False
        try:
            if abs(float(a) - float(b)) > 1e-9:
                return False
        except (TypeError, ValueError):
            return False
    return True


# Shared singleton (same pattern as SHADOW_BOOK / catalog watch).
SELF_AUDIT = SelfAudit()
