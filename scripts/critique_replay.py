#!/usr/bin/env python3
"""Replay the pre-trade critique over a frozen benchmark's approved ideas.

`bot/core/critique.py` can HALT a trade a person has already confirmed, on four
heuristic concerns, and the backtest executes every idea the risk gate
approves without asking it. So nobody has measured what the halt would have
removed. This runs the benchmark command's own path (`runner._run_portfolio`,
`--honest --walk-forward N` over a frozen `--dataset`), records every idea the
risk gate APPROVED at the moment it was approved, asks
`TradeCritique.evaluate` about it with the open-position count of the book at
that bar, and joins the verdict to the position the idea became.

What it cannot replay, said so a reader does not take the count for more:

* the MACRO concern. There is no historical macro state, so it is asked with
  `macro_context=None` and abstains; every concern count here is a lower bound
  by that one rule.
* the confirm-time book. Live asks at the tap, which is after the approval;
  here it is the approval bar, one bar before an honest `next_open` fill.

It changes nothing about whether the critique halts. It writes nothing but
stdout and, with `--output`, the JSON it prints: the runner's validation
recorder is replaced with a no-op for this process, and the backtest keeps its
own temporary risk state. Frozen data only; no network.

    python3 scripts/critique_replay.py --dataset benchmark/majors_1h
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def critique_row(idea: Any, open_count: int) -> dict:
    """What the critique says about one approved idea, as data.

    ``sub_floor_after_adjustment`` is the confirm path's second refusal: the
    engine adds the concerns' confidence adjustment to the idea and, when the
    result no longer clears the floor, rejects a confirm no person made (a
    person's tap proceeds). Asked the way the engine asks it, on a copy.
    """
    from types import SimpleNamespace

    from bot.core.critique import TradeCritique
    from bot.risk.confidence_floor import clears_confidence_floor

    res = TradeCritique().evaluate(
        idea, None, SimpleNamespace(open_positions=int(open_count)), None)
    sub_floor = False
    if res.confidence_adjustment != 0:
        moved = max(0.0, min(1.0, float(idea.confidence) + res.confidence_adjustment))
        sub_floor = not clears_confidence_floor(
            idea.model_copy(update={"confidence": moved}))
    return {
        "trade_id": str(getattr(idea, "id", "")),
        "asset": str(getattr(idea, "asset", "")),
        "open_count": int(open_count),
        "verdict": res.verdict,
        "rules": list(res.rules),
        "confidence_adjustment": round(float(res.confidence_adjustment), 4),
        "sub_floor_after_adjustment": sub_floor,
    }


def summarize(rows: Iterable[dict], fills: Iterable[Any]) -> dict:
    """Join critique rows to the positions they became, and compare groups.

    A row whose idea never became a position (a limit that expired, a fill
    the balance refused) is counted as not filled, never as a loss. Each
    group's figures are `runner.pooled_stats` over that group's fill rows,
    the reading the benchmark's own pooled line uses.
    """
    from bot.backtest.runner import pooled_stats

    rows = list(rows)
    fills = list(fills)
    by_id: dict[str, list] = {}
    for f in fills:
        by_id.setdefault(str(f.trade_id), []).append(f)

    seen: dict[str, int] = {}
    for r in rows:
        seen[r["trade_id"]] = seen.get(r["trade_id"], 0) + 1
    duplicated = sorted(k for k, n in seen.items() if n > 1)

    rule_counts: dict[str, int] = {}
    concern_hist: dict[str, int] = {}
    for r in rows:
        for rule in r["rules"]:
            rule_counts[rule] = rule_counts.get(rule, 0) + 1
        k = str(len(r["rules"]))
        concern_hist[k] = concern_hist.get(k, 0) + 1

    def _group(pred) -> dict:
        members = [r for r in rows if pred(r)]
        filled = [r for r in members if r["trade_id"] in by_id]
        group_fills = [f for r in filled for f in by_id[r["trade_id"]]]
        return {"approved": len(members),
                "not_filled": len(members) - len(filled),
                "positions": pooled_stats(group_fills)}

    unmatched = sorted(set(by_id) - set(seen))
    return {
        "approved_ideas": len(rows),
        "duplicated_trade_ids": duplicated,
        "positions_without_a_critique_row": len(unmatched),
        "concern_count_histogram": dict(sorted(concern_hist.items())),
        "rule_counts": dict(sorted(rule_counts.items())),
        "halted": _group(lambda r: r["verdict"] == "HALT"),
        "warned": _group(lambda r: r["verdict"] == "WARN"),
        "passed": _group(lambda r: r["verdict"] == "PASS"),
        "not_halted": _group(lambda r: r["verdict"] != "HALT"),
        "sub_floor_after_adjustment": _group(
            lambda r: r["sub_floor_after_adjustment"] is True),
    }


async def replay(dataset: str, folds: int) -> dict:
    """Run the benchmark path once with the critique asked at each approval."""
    from bot.backtest import portfolio_engine, runner
    from bot.backtest import snapshot as _snap
    from bot.risk.risk_engine import RiskEngine

    rows: list[dict] = []
    # An approval whose book could not be read is counted, never dropped: the
    # heat rule would have had nothing to count, so its verdict is unknown.
    unread_books = [0]
    captured: dict[str, Optional[list]] = {"folds": None}

    real_register = RiskEngine.register_pending_intent
    real_wf = portfolio_engine.portfolio_walk_forward
    real_record = runner._record_walk_forward_validations

    def _register(self, idea):
        # Called once per idea the gate APPROVED, between approval and fill.
        try:
            open_count = int(self._portfolio.snapshot().open_positions)
        except Exception:
            unread_books[0] += 1
        else:
            rows.append(critique_row(idea, open_count))
        return real_register(self, idea)

    async def _wf(*a, **k):
        out = await real_wf(*a, **k)
        captured["folds"] = out
        return out

    args = runner.build_parser().parse_args(
        ["--dataset", dataset, "--honest", "--walk-forward", str(folds)])
    runner._apply_honest_fidelity(args)
    if not args.symbols.strip():
        args.symbols = ",".join(_snap.load_manifest_multi(dataset).get("symbols", {}))

    RiskEngine.register_pending_intent = _register  # type: ignore[method-assign]
    portfolio_engine.portfolio_walk_forward = _wf  # type: ignore[assignment]
    runner._record_walk_forward_validations = lambda *a, **k: None  # type: ignore[assignment]
    try:
        await runner._run_portfolio(args)
    finally:
        RiskEngine.register_pending_intent = real_register  # type: ignore[method-assign]
        portfolio_engine.portfolio_walk_forward = real_wf  # type: ignore[assignment]
        runner._record_walk_forward_validations = real_record  # type: ignore[assignment]

    fold_rows = captured["folds"]
    if fold_rows is None:
        raise SystemExit("the walk-forward never ran; nothing was measured")
    fills = [t for f in fold_rows for t in f.get("_trades", [])]
    out = summarize(rows, fills)
    out.update({"dataset": dataset, "folds": folds, "honest": True,
                "approvals_with_an_unread_book": unread_books[0],
                "data_source": runner.frozen_source(_snap.load_manifest_multi(dataset)),
                "code_sha": runner._code_sha(),
                "unreplayed": ["macro (no historical macro state; abstains)"]})
    return out


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--dataset", default="benchmark/majors_1h")
    p.add_argument("--walk-forward", type=int, default=6)
    p.add_argument("--output", default="")
    a = p.parse_args(argv)
    out = asyncio.run(replay(a.dataset, a.walk_forward))
    text = json.dumps(out, indent=2, default=str)
    print("\nCRITIQUE REPLAY")
    print(text)
    if a.output:
        Path(a.output).write_text(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
