#!/usr/bin/env python3
"""Is the guard REACHED? — a linter for one specific, recurring defect.

THE DEFECT

Three times in this audit, a guard was written correctly and some call site
simply never received it:

    assertDevnet              5 of 9 presale commands never called it. Two of
                              them were `deposit` (sends buyer SOL) and
                              `liquidity` (creates the irreversible LP lock).
    assertKeyfilePermissions  present in one keypair loader, absent from the
                              other — the one that signs the presale.
    _token_gate_blocks        8 of 12 dispatch sites. Typing "scalp" in natural
                              language reached the paid scan for free, and the
                              web dashboard bypassed the paywall entirely.

Every one of them was found by writing a small script that parses source and
asks whether the guard is *called*, not whether it *works*.

WHY ORDINARY TESTS CANNOT FIND THIS

A test exercises a path. If the path is guarded, the test passes and confirms
the guard works. If the path is unguarded, no test was written for it — that is
what "somebody forgot" means. So the entire test suite can be green while half
the call sites are open, and every one of those three defects lived in a
codebase with a passing suite.

The question "does this guard work?" and "is this guard reached?" are different
questions, and only the first has ever been asked by a unit test.

WHY A LINTER RATHER THAN A FOURTH HAND-WRITTEN SCRIPT

The three detectors total ~320 lines of near-identical parsing. Adding a fourth
guard meant writing a fourth. Here a rule is a dozen lines of config, so the
cost of protecting the NEXT guard is small enough that it actually happens —
which is the only property that matters, since the failure mode is precisely
that people do not do the extra work.

This does not replace those three tests. They assert things beyond reachability
(that the check refuses a 0644 keypair, that `elite` buys something, that a
malformed override is an error) and are worth keeping. This is where rule four
goes.

Usage:
    python3 scripts/guard_lint.py                 # check every rule
    python3 scripts/guard_lint.py --list          # show the rules
    python3 scripts/guard_lint.py --rule tier-gate
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


@dataclass
class Rule:
    """A guard, and the shape of function that must call it."""
    name: str
    files: list[str]              # globs, repo-relative
    trigger: str                  # regex — a function containing this NEEDS the guard
    guard: str                    # regex — ...and is satisfied by this
    why: str                      # what a violation actually costs
    language: str = "python"      # "python" | "js"
    exclude_functions: list[str] = field(default_factory=list)
    min_sites: int = 1            # fewer matches than this means the rule went stale


RULES: list[Rule] = [
    Rule(
        name="cluster-guard",
        files=["token/presale/genesis_presale.mjs"],
        language="js",
        trigger=r"makeUmi\(",
        guard=r"assertDevnet",
        min_sites=9,
        why=("A command that opens an RPC connection without verifying the cluster by genesis "
             "hash can sign against mainnet from draft tooling. `deposit` sends buyer SOL; "
             "`liquidity` creates the never-claim LP lock, which is irreversible."),
    ),
    Rule(
        name="keyfile-permissions",
        files=["token/scripts/lib.mjs", "token/presale/genesis_lib.mjs"],
        language="js",
        trigger=r"function loadKeypair",
        guard=r"assertKeyfilePermissions",
        min_sites=2,
        why=("Loading a group- or world-readable keypair. One plaintext file holds mint, "
             "metadata, presale and LP authority; KEYPAIR_PATH is operator-supplied, so the "
             "check belongs at the load site."),
    ),
    Rule(
        name="kill-switch",
        files=["bot/core/live_executor.py"],
        language="python",
        trigger=r"await exchange\.create_order\(",
        guard=r"trading_halted\(\)",
        min_sites=1,
        # EXCLUSIONS ARE THE LOAD-BEARING PART OF THIS RULE.
        #
        # The first draft flagged all seven order sites, including _place_sl_tp,
        # _partial_close, _update_exchange_sl and _close_position_inner. Those
        # REDUCE exposure, and "fixing" them to satisfy this linter would stop
        # stop-losses from being placed during a halt — the exact opposite of
        # what a kill switch is for, and strictly worse than the defect the rule
        # exists to catch. A guard rule that pressures someone into a dangerous
        # change is not a safety control.
        #
        # So: only OPENING paths must consult the switch. Anything that reduces
        # or protects a position keeps working while halted, by design.
        exclude_functions=[
            "_place_sl_tp",           # places the protective stop — must run while halted
            "_partial_close",         # reduces exposure
            "_update_exchange_sl",    # tightens the stop
            "_close_position_inner",  # exits
            "_create_order_idempotent",  # transport helper; the normal open path is
                                         # gated upstream at engine.py's last-mile check
            "trading_halted",         # the guard itself, quoted in its own docstring
        ],
        why=("An order-opening path in the executor that does not consult the kill "
             "switch. /halt and every automatic breaker leave resting limit orders in "
             "place, and _check_open_positions keeps running while halted so exits are "
             "still managed — so a drift fallback could open NEW exposure on an account "
             "somebody had already stopped. If the new function REDUCES exposure, add it "
             "to exclude_functions instead; do not gate it."),
    ),
    Rule(
        name="tier-gate",
        files=["bot/skills/telegram_handler.py"],
        language="python",
        trigger=(r'dispatch\("(?:pro_scan|deepscan|patterns|analyze_asset|run_backtest'
                 r'|walk_forward|optimize|learning)"'),
        guard=r"_token_gate_blocks|_pane_gate_blocks",
        min_sites=10,
        why=("A gated skill dispatched without a gate check is a paid feature given away. "
             "This is how typing \"scalp\" bypassed the paywall that /scalp enforced."),
    ),
]


def _py_functions(src: str):
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            seg = ast.get_source_segment(src, node)
            if seg:
                yield node.name, seg


_JS_FN = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)", re.M)


def _js_functions(src: str):
    """Top-level `function name(...)` bodies, split at the next declaration.

    Deliberately not a JS parser. The rules here target files whose functions
    are declared this way, and `min_sites` catches the case where that stops
    being true — a rule that silently matches nothing is worse than no rule,
    so it fails loudly instead.
    """
    marks = [(m.start(), m.group(1)) for m in _JS_FN.finditer(src)]
    for i, (pos, name) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(src)
        yield name, src[pos:end]


def check_rule(rule: Rule) -> tuple[list[str], int]:
    """Return (violations, number of trigger sites examined)."""
    violations: list[str] = []
    sites = 0
    trigger = re.compile(rule.trigger)
    guard = re.compile(rule.guard)
    for pattern in rule.files:
        for path in sorted(REPO.glob(pattern)):
            src = path.read_text(encoding="utf-8")
            walker = _py_functions if rule.language == "python" else _js_functions
            try:
                functions = list(walker(src))
            except SyntaxError as exc:
                violations.append(f"{path}: could not parse ({exc})")
                continue
            for name, body in functions:
                if name in rule.exclude_functions or not trigger.search(body):
                    continue
                sites += 1
                if not guard.search(body):
                    rel = path.relative_to(REPO)
                    violations.append(f"{rel}:{name} triggers {rule.name} but never calls the guard")
    return violations, sites


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rule", help="check only this rule")
    ap.add_argument("--list", action="store_true", help="list the rules and exit")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    rules = RULES
    if args.rule:
        rules = [r for r in RULES if r.name == args.rule]
        if not rules:
            print(f"unknown rule {args.rule!r}; known: {', '.join(r.name for r in RULES)}",
                  file=sys.stderr)
            return 2
    if args.list:
        for r in rules:
            print(f"{r.name:<22} {r.guard:<40} in {', '.join(r.files)}")
        return 0

    failures, report = 0, []
    for rule in rules:
        violations, sites = check_rule(rule)
        # A rule matching nothing has stopped protecting anything, and would sit
        # green forever. That is the same silent-no-op failure the guards
        # themselves had, so it is an error, not a note.
        if sites < rule.min_sites:
            violations.append(
                f"rule '{rule.name}' found only {sites} trigger site(s), expected >= "
                f"{rule.min_sites} — the code moved and this rule is no longer looking at it")
        report.append({"rule": rule.name, "sites": sites, "violations": violations})
        if violations:
            failures += 1
            print(f"\n✗ {rule.name}  ({sites} site(s) examined)")
            for v in violations:
                print(f"    {v}")
            print(f"    WHY: {rule.why}")
        else:
            print(f"✓ {rule.name}  ({sites} site(s), all guarded)")

    if args.json:
        print(json.dumps(report, indent=2))
    if failures:
        print(f"\n{failures} rule(s) violated. A guard that is not reached is not a guard.",
              file=sys.stderr)
        return 1
    print(f"\nAll {len(rules)} guard rule(s) reached at every trigger site.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
