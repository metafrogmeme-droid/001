"""
RUNECLAW — Counterfactual shadow book.

Every idea a gate REJECTS becomes a paper trade in this ledger, filled
and exited off the live ticker stream the scanner already fetches. Every
gate then carries a live, continuously-updated price tag: a gate whose
blocked trades NET POSITIVE R is eating edge; one whose blocked trades
net negative is saving money. This is the substrate for the equity-curve
throttle and the nightly self-audit — and it retro-answers "should this
gate be on?" questions without a backtest.

Recording-only: nothing here ever places, sizes, or influences a real
order. Zero extra API calls — update() rides the futures ticker map the
scanner fetches each cycle (same pattern as the catalog watch).

Accounting is in R-multiples (risk units), not dollars: a shadow trade
has no real size, so its outcome is (exit − entry) / (entry − stop),
signed by direction. Fill semantics mirror live limit entries: a shadow
trade FILLS only when price touches its entry within the limit-expiry
window; untouched entries become "never_filled" and are excluded — the
same non-fill hygiene the parity report applies to real records.

V1 simplifications (documented, conservative): exits use last-price
ticks (no intrabar wicks — misses some SL and some TP touches alike),
static SL/TP only (no trailing), 7-day hard expiry closed at mark.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
import uuid
from typing import Optional

from bot.utils.atomic_write import atomic_write_json

logger = logging.getLogger(__name__)

_STATE_DIR = os.environ.get("RUNECLAW_STATE_DIR", "data")
DEFAULT_STATE_FILE = os.path.join(_STATE_DIR, "shadow_book.json")

# Mirror live limit-entry expiry (4h) and a hard trade horizon (7d).
FILL_WINDOW_SEC = 14400.0
TRADE_HORIZON_SEC = 7 * 86400.0

_MAX_LIVE = 300      # pending + open cap (newest kept)
_MAX_CLOSED = 2000   # closed-history cap


def _base(symbol: str) -> str:
    s = (symbol or "").upper()
    i = s.find("/")
    return s[:i] if i > 0 else s


def gate_category(gate: str) -> str:
    """The stable bucket a gate string belongs to.

    A gate string carries the READING that tripped it -
    "CONFIDENCE: 0.55 < 0.6 minimum" - so grouping on the raw string splits
    one gate across as many buckets as there were distinct readings. The live
    ledger on 2026-08-31 held the confidence floor as EIGHT separate gates
    (11, 8, 8, 4, 3, 3, 2 and 1 trades).

    That matters because the nightly self-audit reads
    ``next(iter(gate_report()))`` - the single highest-net_r bucket - and
    prints it as "the costliest gate". It named a shard of 4 blocked trades
    and presented it as the whole gate: a partial printed as a total, in the
    scoreboard that decides which gate to loosen on a live trading bot.

    Applied at READ time, not at write. Storage keeps the full string in
    ``gate``, ``gates`` and ``reason``, so nothing is lost and the rows
    ALREADY ON DISK aggregate correctly with no migration - the fix reaches
    backwards, which a write-side change could not.

    Splitting on the first colon also collapses the doubled
    "LIQUIDITY: LIQUIDITY: spread ..." engine.py produced. That is fixed at
    its source as well: a canonicaliser that quietly absorbs a malformed
    input is how the malformed input survives.
    """
    head = str(gate or "").split(":", 1)[0].strip().upper()
    # An unlabelled rejection keeps its OWN bucket rather than joining a
    # neighbour's. Merging it would credit its R to a gate that did not earn
    # it, which is the same misattribution one level down.
    return head or "UNLABELLED"


#: Lower end of a 95% interval, the same bar `readiness.wilson_lower_bound`
#: applies to voter agreement — "the whole interval clear of the null, not its
#: top end". Same z, deliberately, so the two readings mean the same thing.
_Z = 1.96

#: Below this the interval is not trusted whatever it says. An R-multiple is
#: bounded at -1 and unbounded above, so the normal approximation understates
#: the upper tail on a thin sample — and worse, a DEGENERATE one reads as
#: certainty: three blocked trades that all took profit at exactly +1.8R have
#: a sample sd of 0, hence a lower bound of +1.8R, from three trades. The sd
#: is zero because the sample is tiny, not because the effect is sure.
MIN_GATE_TRADES = 10


def mean_r_interval(n: int, sum_r: float, sum_r2: float,
                    z: float = _Z) -> Optional[tuple[float, float]]:
    """The 95% interval for a gate's MEAN R per blocked trade.

    NOT Wilson, and the difference is the point. Wilson is the interval for a
    PROPORTION; R is a continuous, signed magnitude, and the question here is
    "how much per trade", not "how often". Scoring it as a win rate would
    answer a question nobody asked — the report's own `_identical_run` note
    calls that out in as many words: *a null from the wrong instrument is not
    evidence of no effect*. What is shared with `wilson_lower_bound` is the
    DISCIPLINE — the whole interval must clear the null, not its near end —
    and the z, so the two readings mean the same thing.

    BOTH ENDS, because both verdicts are claims. "This gate is eating edge"
    needs the lower end above zero; "this gate is saving money" needs the
    upper end below it, and the `/shadow` scoreboard was painting the second
    one green off a bare total.

    Returns ``None`` when it cannot be computed — fewer than two samples, or a
    variance that will not resolve. ``None`` is *unknown*, and callers must not
    read it as zero: a gate with no interval is a gate with no claim behind it,
    which is a different thing from one measured at break-even.
    """
    if n < 2:
        return None
    try:
        mean = sum_r / n
        # Sample variance from the raw accumulators. The subtraction can go
        # very slightly negative on a near-constant sample (catastrophic
        # cancellation), which is a float artefact and not a negative
        # variance, so it is clamped rather than propagated into sqrt.
        var = max(0.0, (sum_r2 - n * mean * mean) / (n - 1))
        margin = z * math.sqrt(var / n)
        return round(mean - margin, 4), round(mean + margin, 4)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


class ShadowBook:
    """Persistent counterfactual ledger of gate-rejected trades."""

    def __init__(self, state_file: Optional[str] = None) -> None:
        self.state_file = state_file or DEFAULT_STATE_FILE
        self._trades: list[dict] = []
        self._loaded = False

    # ── persistence ───────────────────────────────────────────────
    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            with open(self.state_file, encoding="utf-8") as f:
                self._trades = list(json.load(f).get("trades", []))
        except FileNotFoundError:
            pass
        except Exception as exc:  # corrupt state must never break anything
            logger.warning("shadow_book.json unreadable (%s) — starting fresh", exc)

    def _save(self) -> None:
        try:
            live = [t for t in self._trades if t["status"] in ("pending", "open")]
            done = [t for t in self._trades if t["status"] not in ("pending", "open")]
            self._trades = done[-_MAX_CLOSED:] + live[-_MAX_LIVE:]
            atomic_write_json(self.state_file, {"trades": self._trades},
                              indent=None)
        except Exception as exc:
            logger.debug("shadow book save failed: %s", exc)

    # ── recording ─────────────────────────────────────────────────
    def record_rejection(self, idea, gates, reason: str,
                         ref_price: float = 0.0,
                         now_ts: Optional[float] = None,
                         regime: str = "") -> Optional[dict]:
        """Enter a rejected idea into the ledger. Never raises.

        ``gates`` is the risk check's failed-gate list; the FIRST entry is
        the primary gate charged with the outcome. ``regime`` tags the
        market regime at rejection time so the scoreboard can answer
        "does this gate earn in THIS regime?" — the raw material for
        regime-conditional gating. Degenerate ideas (missing/inverted
        levels) are skipped — nothing to simulate.
        """
        try:
            self._load()
            entry = float(getattr(idea, "entry_price", 0) or 0)
            sl = float(getattr(idea, "stop_loss", 0) or 0)
            tp = float(getattr(idea, "take_profit", 0) or 0)
            direction = getattr(getattr(idea, "direction", None), "value",
                                str(getattr(idea, "direction", "")))
            if entry <= 0 or sl <= 0 or tp <= 0:
                return None
            is_long = direction == "LONG"
            if is_long and not (sl < entry < tp):
                return None
            if (not is_long) and not (tp < entry < sl):
                return None
            gate_list = [str(g) for g in (gates or [])][:5] or ["(unspecified)"]
            now = float(now_ts if now_ts is not None else time.time())
            trade = {
                "id": f"SB-{uuid.uuid4().hex[:8]}",
                "idea_id": str(getattr(idea, "id", "")),
                "symbol": str(getattr(idea, "asset", "")),
                "direction": direction,
                "entry": entry, "sl": sl, "tp": tp,
                "gate": gate_list[0], "gates": gate_list,
                "reason": str(reason or "")[:160],
                "regime": str(regime or "").strip().upper()[:24],
                "strategy_type": str(getattr(idea, "strategy_type", "") or ""),
                "created_ts": now,
                "status": "pending",   # fills only if price touches entry
                "fill_ts": None, "exit_ts": None,
                "exit_price": None, "outcome": None, "r": None,
            }
            # Marketable at record time (entry at/through the current price)
            # fills immediately — mirrors a market/instantly-fillable limit.
            if ref_price > 0:
                if (is_long and ref_price <= entry) or \
                        ((not is_long) and ref_price >= entry):
                    trade["status"] = "open"
                    trade["fill_ts"] = now
            self._trades.append(trade)
            self._save()
            return trade
        except Exception as exc:  # noqa: BLE001
            logger.debug("shadow book record failed: %s", exc)
            return None

    # ── tick update ───────────────────────────────────────────────
    def update(self, tickers: dict, now_ts: Optional[float] = None) -> int:
        """Advance the ledger one tick using a {symbol: ticker} map (the
        scanner's futures map — keys in ccxt form). Returns the number of
        state changes. Never raises."""
        try:
            self._load()
            now = float(now_ts if now_ts is not None else time.time())
            # Index last prices by base for tolerant symbol matching.
            last_by_base: dict[str, float] = {}
            for sym, t in (tickers or {}).items():
                try:
                    px = float((t or {}).get("last") or 0)
                    if px > 0:
                        last_by_base[_base(sym)] = px
                except Exception:
                    continue
            changed = 0
            for tr in self._trades:
                if tr["status"] not in ("pending", "open"):
                    continue
                last = last_by_base.get(_base(tr["symbol"]))
                is_long = tr["direction"] == "LONG"
                if tr["status"] == "pending":
                    if now - tr["created_ts"] > FILL_WINDOW_SEC:
                        tr["status"] = "never_filled"
                        tr["exit_ts"] = now
                        changed += 1
                        continue
                    if last is None:
                        continue
                    if (is_long and last <= tr["entry"]) or \
                            ((not is_long) and last >= tr["entry"]):
                        tr["status"] = "open"
                        tr["fill_ts"] = now
                        changed += 1
                    continue
                # open
                risk = abs(tr["entry"] - tr["sl"])
                if risk <= 0:
                    tr["status"] = "closed"
                    tr["outcome"] = "void"
                    tr["r"] = 0.0
                    tr["exit_ts"] = now
                    changed += 1
                    continue
                if last is not None:
                    hit_sl = last <= tr["sl"] if is_long else last >= tr["sl"]
                    hit_tp = last >= tr["tp"] if is_long else last <= tr["tp"]
                    if hit_sl:      # pessimistic: stop checked first
                        tr.update(status="closed", outcome="sl",
                                  exit_price=tr["sl"], exit_ts=now, r=-1.0)
                        changed += 1
                        continue
                    if hit_tp:
                        r = abs(tr["tp"] - tr["entry"]) / risk
                        tr.update(status="closed", outcome="tp",
                                  exit_price=tr["tp"], exit_ts=now,
                                  r=round(r, 3))
                        changed += 1
                        continue
                if now - (tr["fill_ts"] or tr["created_ts"]) > TRADE_HORIZON_SEC:
                    px = last if last is not None else tr["entry"]
                    signed = (px - tr["entry"]) if is_long else (tr["entry"] - px)
                    tr.update(status="closed", outcome="expired",
                              exit_price=px, exit_ts=now,
                              r=round(signed / risk, 3))
                    changed += 1
            if changed:
                self._save()
            return changed
        except Exception as exc:  # noqa: BLE001
            logger.debug("shadow book update failed: %s", exc)
            return 0

    # ── reporting ─────────────────────────────────────────────────
    def gate_report(self) -> dict:
        """Per-gate scoreboard over CLOSED shadow trades.

        net_r POSITIVE = the gate blocked profitable trades (it is eating
        edge); NEGATIVE = the gate saved money. never_filled excluded.

        `net_r` IS A TOTAL, AND A TOTAL IS NOT AN EFFECT. The nightly card
        read the top of this sort and printed it as "the costliest gate" off
        `net_r > 0.5`, so a live card said `MTF_ALIGNMENT … net +4.1R over 97
        blocked trades` — which is **+0.042R per trade**, a bar cleared by
        noise, on the scoreboard that decides which risk gate to loosen. Same
        defect `voter_weights` had ("62% of 34" is 21 of 34, a coin flip) in a
        second module. `sum_r2` rides along so `mean_r_lower_bound` can put an
        interval on the per-trade figure; `lower_r` is that interval's floor
        and is ``None`` when there is no sample to compute one from.
        """
        self._load()
        out: dict[str, dict] = {}
        for tr in self._trades:
            if tr["status"] != "closed" or tr.get("r") is None:
                continue
            g = out.setdefault(gate_category(tr["gate"]), {
                "n": 0, "wins": 0, "losses": 0, "net_r": 0.0, "sum_r2": 0.0})
            r = float(tr["r"])
            g["n"] += 1
            g["net_r"] = round(g["net_r"] + r, 3)
            g["sum_r2"] = round(g["sum_r2"] + r * r, 6)
            if r > 0:
                g["wins"] += 1
            elif r < 0:
                g["losses"] += 1
        for g in out.values():
            g["avg_r"] = round(g["net_r"] / g["n"], 3) if g["n"] else 0.0
            iv = mean_r_interval(g["n"], g["net_r"], g["sum_r2"])
            g["lower_r"] = None if iv is None else iv[0]
            g["upper_r"] = None if iv is None else iv[1]
            # The one thing a reader has to be able to check without doing the
            # arithmetic: is this gate's verdict established, or is it the top
            # of a sort? Carried on the row so the nightly card, the /shadow
            # scoreboard and the LLM's evidence blob cannot answer it
            # differently. Three values, not two — `None` is "no interval", and
            # a gate with no interval has not been shown to do either thing.
            if iv is None or g["n"] < MIN_GATE_TRADES:
                g["verdict"] = None
            elif iv[0] > 0:
                g["verdict"] = "eating_edge"
            elif iv[1] < 0:
                g["verdict"] = "saving"
            else:
                g["verdict"] = "undistinguished"
        return dict(sorted(out.items(), key=lambda kv: kv[1]["net_r"],
                           reverse=True))

    def gate_regime_report(self) -> dict:
        """Per-(gate, regime) scoreboard over CLOSED shadow trades — the raw
        material for regime-conditional gating. Same sign convention as
        gate_report(): net_r > 0 means the gate blocked winners IN THAT
        REGIME. Trades recorded before regime tagging land in UNKNOWN."""
        self._load()
        out: dict[str, dict[str, dict]] = {}
        for tr in self._trades:
            if tr["status"] != "closed" or tr.get("r") is None:
                continue
            reg = str(tr.get("regime") or "") or "UNKNOWN"
            g = out.setdefault(gate_category(tr["gate"]), {}).setdefault(
                reg, {"n": 0, "net_r": 0.0})
            g["n"] += 1
            g["net_r"] = round(g["net_r"] + float(tr["r"]), 3)
        return out

    def counts(self) -> dict:
        self._load()
        c: dict[str, int] = {}
        for tr in self._trades:
            c[tr["status"]] = c.get(tr["status"], 0) + 1
        return c

    def render_report(self) -> str:
        """Telegram-ready gate scoreboard."""
        rep = self.gate_report()
        c = self.counts()
        lines = ["<b>Shadow book — what the gates cost</b>",
                 "─" * 16,
                 f"Tracked: {c.get('pending', 0)} pending · "
                 f"{c.get('open', 0)} open · {c.get('closed', 0)} closed · "
                 f"{c.get('never_filled', 0)} never filled", ""]
        if not rep:
            lines.append("No closed shadow trades yet — the ledger fills "
                         "as gates reject ideas.")
            return "\n".join(lines)
        lines.append("net R > 0 = the gate is BLOCKING winners "
                     "(⬜ = not distinguishable from noise):")
        by_regime = self.gate_regime_report()
        for gate, g in list(rep.items())[:12]:
            # COLOUR IS A CLAIM, and this one used to be made off a bare total:
            # `net_r > 0.5` painted a gate red on +0.6R over 200 blocked trades
            # (+0.003R each) exactly as it did on +0.6R over 2. The verdict is
            # the interval's now, and a gate with no interval gets the muted
            # icon rather than borrowing either colour.
            icon = {"eating_edge": "\U0001f7e5",
                    "saving": "\U0001f7e9"}.get(g.get("verdict") or "", "⬜")
            row = (f"{icon} <code>{gate[:32]}</code> — {g['n']}tr · "
                   f"net {g['net_r']:+.1f}R · avg {g['avg_r']:+.2f}R")
            if g.get("verdict") is None:
                row += f" · <i>too few to bound (&lt;{MIN_GATE_TRADES}tr)</i>"
            lines.append(row)
            # Regime split: shown only when the gate's verdict actually
            # DIFFERS by regime — the case regime-conditional gating exists
            # for. A gate that's uniformly good/bad stays a single line.
            regs = by_regime.get(gate) or {}
            known = {k: v for k, v in regs.items() if k != "UNKNOWN"}
            if len(known) >= 2:
                nets = [v["net_r"] for v in known.values()]
                if max(nets) > 0 > min(nets):
                    split = " · ".join(
                        f"{reg[:10]} {v['net_r']:+.1f}R({v['n']})"
                        for reg, v in sorted(known.items(),
                                             key=lambda kv: -kv[1]["net_r"]))
                    lines.append(f"   └ by regime: {split}")
        return "\n".join(lines)


# Shared singleton (same pattern as CROSS_VENUE): the engine records into
# it, the scanner ticks it with the cycle's ticker map.
SHADOW_BOOK = ShadowBook()
