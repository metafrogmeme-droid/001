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

THE LINTER ITSELF HAD THE DEFECT IT EXISTS TO CATCH

Rules 1-4 name four specific source files. Not one of them named anything under
`bot/web/`, and neither did the companion test
(`test_command_audience_matches_permission.py`, which reads only
`bot/skills/telegram_handler.py`). So the entire HTTP surface — 53 routes,
including the ones that sign transactions and bind trading policy — was outside
every structural check in the repo. That is the same shape as the bug: a control
that works, applied to a set that does not include the dangerous member. It
showed up for real when web chat turned out to execute any registry skill by
name with no authorisation at all.

Three things were needed to cover that surface, and each is a general capability
rather than a one-off:

* `selector="aiohttp-route"` — the trigger is no longer "this regex appears in a
  function body". For a web module the interesting set is "functions reachable
  from the outside", which is decided by `add_get`/`add_post` registration, not
  by anything inside the function. The trigger regex then matches the route
  label, e.g. ``POST /trade/propose``.
* `resolve_calls=True` — `handle_trade_propose` never calls `_guard_user`; it
  calls `_propose_from_text`, which does. A body-only search reports that as
  unguarded, and the "fix" is a redundant second check. False positives are not
  a neutral cost here: they teach people to reach for `exclude_functions`, and
  an over-excluded rule is a blind one.
* `mode="forbid"` — some surfaces are guarded by *placement* rather than by a
  call. `dashboard_server.py` authenticates in middleware keyed on the `/api/`
  prefix, so the property to enforce is "no route outside `/api/`", which no
  guard-call search can express.

Plus a coverage check (`_route_module_coverage`) asserting that every module in
the repo which registers HTTP routes is named by at least one rule. Adding rules
fixes today's blind spot; that check is what stops the next module from being
born blind — which is how this one happened.

LIMITS, STATED

`resolve_calls` answers "can the guard be reached from here", not "is the guard
always executed". A helper that guards on one branch and not another reads as
guarded. Closing that needs path-sensitive analysis; this is a reachability
linter and claiming more would be the same overstatement the audit keeps
finding. Depth is bounded (`_CALL_DEPTH`) and only bare-name calls are followed,
so a guard hidden three helpers deep behind a method call is a false positive —
which is the safe direction.

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
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


@dataclass
class Rule:
    """A guard, and the shape of function that must call it."""
    name: str
    files: list[str]              # globs, repo-relative
    trigger: str                  # regex — see `selector` for what it matches against
    guard: str                    # regex — ...and is satisfied by this
    why: str                      # what a violation actually costs
    language: str = "python"      # "python" | "js"
    # "body-regex"     — trigger matched against each function body
    # "aiohttp-route"  — one candidate per registered aiohttp route
    # "express-router" — one candidate per Express route MODULE (whole file)
    selector: str = "body-regex"
    mode: str = "require-guard"   # "require-guard" | "forbid"
    resolve_calls: bool = False   # follow same-file helper calls when hunting the guard
    exclude_functions: list[str] = field(default_factory=list)
    min_sites: int = 1            # fewer candidates than this means the rule went stale


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
    Rule(
        name="web-route-auth",
        files=["bot/web/user_gateway.py"],
        language="python",
        selector="aiohttp-route",
        resolve_calls=True,
        trigger=r".",                 # every registered route is a candidate
        # `_is_admin_id` must appear as an early-return GATE, not merely be
        # mentioned. It is also a display predicate — `_trade_mode` calls it to
        # print "live" or "paper" — and accepting a bare mention marked a route
        # guarded whose only authorisation had been deleted. The other three are
        # purpose-built gates that return a response, so a call is enough.
        guard=(r"_guard_user\(|_web_skill_denied\(|_policy_op_guard\("
               r"|if not _is_admin_id\([^\n]*\)\s*:"),
        min_sites=40,
        # PUBLIC BY DESIGN — each of these says so in its own docstring, and each
        # was re-read when this rule was written rather than taken on trust.
        # Anything added here must be public-safe BY CONSTRUCTION, not merely
        # low-risk: no account, no user store, no dollar figure in scope.
        exclude_functions=[
            "handle_public_chat",         # LLM chat with is_admin=False, public=True
            "handle_proofofpnl_public",   # the sealed statement; open verification is the point
            "handle_leaderboard_public",  # anonymous, independently re-verifiable rows
            "handle_strategies_public",   # preset catalogue: design + regime, no numbers
            "handle_agent_card_public",   # ERC-8004 card already inside the public bundle
            "handle_share_card",          # PNG from three clamped query params
        ],
        why=("An HTTP route that reaches no authorisation decision. This surface holds "
             "the signer, the Authority Envelope, intent-policy binding and trade "
             "confirmation; `_guard_user` is the only thing between a request body's "
             "`telegram_id` field and those. Web chat already shipped once with NO check "
             "at all, so this is a repeat, not a hypothetical. If the new route is "
             "genuinely public, add it to exclude_functions with the reason — do not "
             "widen the guard regex."),
    ),
    Rule(
        name="web-skill-dispatch",
        files=["bot/web/*.py"],
        language="python",
        resolve_calls=True,
        trigger=r"skill\.execute\(",
        guard=r"_web_skill_denied\(",
        min_sites=1,
        why=("Executing a registry skill by name from a web request without the role + "
             "tier check. `_guard_user` alone does not close this: it is called without "
             "a `command`, so its role branch is dead, and the $RCLAW tier gate is not "
             "in it at all. That combination let a self-provisioned website account run "
             "`halt` against the shared circuit breaker and every paid feature for free."),
    ),
    Rule(
        name="express-route-auth",
        files=["app/routes/*.js"],
        language="js",
        selector="express-router",
        trigger=r".",                 # every route module is a candidate
        # `optionalAuth` counts: it identifies without refusing, for a surface
        # that must stay anonymously reachable while showing an anonymous caller
        # less. A module using it is asserted to redact by
        # app/test/guardian_redaction.test.js — reachability is this linter's
        # question, and what gets served is that test's.
        guard=r"authMiddleware|optionalAuth",
        min_sites=180,                # 228 routes across 76 modules today
        # DELIBERATELY PUBLIC OR SEPARATELY AUTHENTICATED. Each was read before
        # being listed; the reason is the entry.
        exclude_functions=[
            # `public_*` is the naming convention for the open surface, and each
            # module's header states what makes its payload public-safe.
            "public_agent", "public_chat", "public_flight", "public_invite",
            "public_leaderboard", "public_letter", "public_proofofpnl",
            "public_status", "public_strategies", "public_user_strategies",
            "sync",        # bot<->web channel; WEB_GATEWAY_SECRET, not a user session
            "track",       # mounted under /api/public
            "mcp",         # its header enumerates the public-data tools it exposes
            "tool8257",    # ERC-8257 manifest over the SAME read-only registry as /mcp
            "stream",      # a "refresh now" ping, no payload
            "frame",       # Farcaster frame endpoints, public by protocol
            "call",        # /call/<key> verify links printed in public scan cards
            "spot", "market", "macro", "patterns", "signals", "insight",
            "today", "feed", "gas", "dapps", "allowances",
            # ^ market data and read-only chain lookups: no account is in scope,
            #   the inputs are a symbol or an address the caller already has.
        ],
        why=("An Express route module under /api/ that never applies authMiddleware. "
             "This is the SECOND HTTP surface in this repo — 228 routes, larger than "
             "the aiohttp gateway — and until this rule existed nothing structural "
             "looked at it, which is exactly the blind spot that let web chat run any "
             "skill unauthenticated. If the new module is genuinely public, add it to "
             "exclude_functions with the reason."),
    ),
    Rule(
        name="dashboard-route-placement",
        files=["bot/web/dashboard_server.py"],
        language="python",
        selector="aiohttp-route",
        mode="forbid",
        # Authentication here is `auth_middleware`, which keys on the path prefix
        # `/api/`. Nothing in a handler body indicates whether it is covered, so
        # no guard-call search can see this; the enforceable property is the
        # PLACEMENT. Trigger = any route NOT under /api/.
        trigger=r"^\w+ (?!/api/)",
        guard="",
        min_sites=8,
        exclude_functions=[
            "handle_index",              # HTML shell, carries no data
            "handle_performance_chart",  # ditto; fetches /api/* with the Bearer token
            "handle_health",             # liveness probe, constant response
            "handle_ready",              # readiness, boolean only
            "handle_metrics",            # documented non-financial counters only
        ],
        why=("A dashboard route outside `/api/` is NOT authenticated — auth_middleware "
             "only guards that prefix, and the handlers carry no check of their own. "
             "These endpoints serve AGGREGATE multi-user state (every user's positions, "
             "equity, rejections, LLM cost). Either register it under /api/, or add it "
             "to exclude_functions with a reason it is safe unauthenticated."),
    ),
]


@lru_cache(maxsize=None)
def _lines(src: str) -> tuple[str, ...]:
    return tuple(src.splitlines(keepends=True))


@lru_cache(maxsize=None)
def _parse(src: str) -> ast.Module:
    return ast.parse(src)


def _segment(src: str, node: ast.AST) -> str:
    """`ast.get_source_segment` without the quadratic blowup.

    The stdlib version re-splits the ENTIRE source on every call. Over
    telegram_handler.py that is 58 of this script's 60 seconds — enough that
    nobody runs it, and a lint gate nobody runs is not a gate. Splitting once
    per file and slicing by lineno takes the whole run to well under a second.
    Line-granular rather than column-granular, which for whole-function bodies
    differs only in leading indentation.
    """
    end = getattr(node, "end_lineno", None)
    if end is None:
        return ""
    return "".join(_lines(src)[node.lineno - 1:end])


def _py_functions(src: str):
    for node in ast.walk(_parse(src)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            seg = _segment(src, node)
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


_ROUTE_METHODS = ("add_get", "add_post", "add_put", "add_delete", "add_patch")

# How many helper hops `resolve_calls` follows. Three covers
# handler -> _propose_from_text -> _guard_user with a hop to spare. Unbounded
# recursion would drag half the module into every body and turn the rule into a
# rubber stamp, which is worse than a false positive.
_CALL_DEPTH = 3


def _aiohttp_routes(src: str) -> list[tuple[str, str]]:
    """[(``METHOD /path``, handler function name)] for every registered route.

    Registration is the only honest definition of "reachable from outside" —
    a web handler's own body says nothing about whether anyone can call it.
    """
    routes = []
    for node in ast.walk(_parse(src)):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in _ROUTE_METHODS
                and node.args):
            continue
        try:
            path = ast.literal_eval(node.args[0])
        except (ValueError, SyntaxError):
            path = "<computed>"
        target = node.args[-1]
        name = (target.id if isinstance(target, ast.Name)
                else target.attr if isinstance(target, ast.Attribute) else "<computed>")
        routes.append((f"{node.func.attr[4:].upper()} {path}", name))
    return routes


_EXPRESS_ROUTE = re.compile(r"router\.(get|post|put|delete|patch)\(", re.M)


def _express_routes(src: str) -> int:
    """How many HTTP routes this Express router module declares."""
    return len(_EXPRESS_ROUTE.findall(src))


def _py_called_names(node: ast.AST) -> set[str]:
    """Names this function DELEGATES to: `return f(...)` / `return await f(...)`.

    Not every call — only a call that is the entire return value. That is the
    delegation the rule needs to see (`handle_trade_propose` ends with
    `return _propose_from_text(...)`, and the guard lives in there), and
    excluding everything else is not a nicety:

    Following all calls to depth 3 made this rule green when the guard had been
    DELETED. `_propose_from_text` also calls `_idea_payload`, which calls
    `_trade_mode`, which mentions `_is_admin_id` to choose the word "live" or
    "paper" for a label. A real call path, a real textual match, and a display
    helper — so the closure said "guarded" while nothing guarded anything. Blob
    matching over a transitive closure grows the haystack faster than the
    signal; `return f(...)` keeps only edges where the callee IS the rest of the
    handler.

    Bare names only: matching `obj.foo(...)` on the attribute would resolve
    `self.users.get` to a module-level `get`. A wrong edge invents a guard that
    is not there; a missing edge only costs a false positive.
    """
    out: set[str] = set()
    for ret in (n for n in ast.walk(node) if isinstance(n, ast.Return)):
        value = ret.value
        if isinstance(value, ast.Await):
            value = value.value
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
            out.add(value.func.id)
    return out


def _reachable_source(name: str, nodes: dict, src: str, depth: int = _CALL_DEPTH) -> str:
    """A function's source plus that of the helpers it calls, to `depth` hops."""
    seen: set[str] = set()
    out: list[str] = []
    frontier = [(name, depth)]
    while frontier:
        fn, left = frontier.pop()
        if fn in seen or fn not in nodes:
            continue
        seen.add(fn)
        out.append(_segment(src, nodes[fn]))
        if left > 0:
            frontier += [(c, left - 1) for c in _py_called_names(nodes[fn]) if c not in seen]
    return "\n".join(out)


def check_rule(rule: Rule) -> tuple[list[str], int, int]:
    """Return (violations, trigger sites examined, candidates discovered).

    `candidates` is what the staleness floor is measured against: for a route
    rule that is every registered route (so moving the routes elsewhere fails),
    for a body rule it is the set of trigger matches (so the trigger going stale
    fails). Both answer "is this rule still looking at anything?".
    """
    violations: list[str] = []
    sites = discovered = 0
    trigger = re.compile(rule.trigger)
    guard = re.compile(rule.guard) if rule.mode == "require-guard" else None
    for pattern in rule.files:
        for path in sorted(REPO.glob(pattern)):
            rel = path.relative_to(REPO)
            src = path.read_text(encoding="utf-8")
            try:
                if rule.selector == "express-router":
                    # One candidate per FILE. Express mounts a whole router at a
                    # prefix and guards it with `router.use(authMiddleware)` at
                    # the top, so the module — not the individual handler — is
                    # the unit that is or is not protected.
                    n = _express_routes(src)
                    if not n:
                        continue
                    discovered += n
                    candidates = ([(f"{rel.name} ({n} route(s))", rel.stem, src)]
                                  if trigger.search(rel.name) else [])
                elif rule.selector == "aiohttp-route":
                    nodes = {n.name: n for n in ast.walk(_parse(src))
                             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
                    routes = _aiohttp_routes(src)
                    discovered += len(routes)
                    candidates = [
                        (f"{label} ({name})", name,
                         _reachable_source(name, nodes, src) if rule.resolve_calls
                         else (_segment(src, nodes[name]) if name in nodes else ""))
                        for label, name in routes if trigger.search(label)
                    ]
                else:
                    walker = _py_functions if rule.language == "python" else _js_functions
                    nodes = ({n.name: n for n in ast.walk(_parse(src))
                              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
                             if rule.language == "python" else {})
                    candidates = []
                    for name, body in walker(src):
                        if not trigger.search(body):
                            continue
                        discovered += 1
                        resolved = (_reachable_source(name, nodes, src)
                                    if rule.resolve_calls and rule.language == "python" else body)
                        candidates.append((name, name, resolved))
            except SyntaxError as exc:
                violations.append(f"{rel}: could not parse ({exc})")
                continue

            for label, name, body in candidates:
                if name in rule.exclude_functions:
                    continue
                sites += 1
                if guard is None:
                    violations.append(
                        f"{rel}:{label} matches {rule.name} and is not on the exempt list")
                elif not guard.search(body or ""):
                    violations.append(
                        f"{rel}:{label} triggers {rule.name} but never reaches the guard")
    return violations, sites, discovered


def _route_module_coverage() -> list[str]:
    """Every module that registers HTTP routes must be named by some rule.

    This is the check that would have caught the linter's own blind spot. Rules
    name files explicitly, so a new web module is invisible until somebody
    remembers to add one — and "somebody remembers" is precisely the assumption
    that failed in each of the defects this script exists for.

    "Named by a ROUTE rule", not by any rule. The first version accepted any
    match, and `web-skill-dispatch` globs `bot/web/*.py` — so a new module
    dropped in that directory counted as covered while nothing examined a single
    one of its routes. A coverage check that can be satisfied by an unrelated
    rule measures nothing.
    """
    watched = {p.resolve() for r in RULES if r.selector == "aiohttp-route"
               for pat in r.files for p in REPO.glob(pat)}
    problems = []
    for path in sorted(REPO.glob("bot/**/*.py")):
        try:
            src = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not any(m + "(" in src for m in _ROUTE_METHODS):
            continue
        try:
            routes = _aiohttp_routes(src)
        except SyntaxError:
            continue
        if routes and path.resolve() not in watched:
            problems.append(
                f"{path.relative_to(REPO)} registers {len(routes)} HTTP route(s) but no "
                f"guard rule names it — that surface is unchecked")
    return problems


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
        violations, sites, discovered = check_rule(rule)
        # A rule matching nothing has stopped protecting anything, and would sit
        # green forever. That is the same silent-no-op failure the guards
        # themselves had, so it is an error, not a note.
        if discovered < rule.min_sites:
            violations.append(
                f"rule '{rule.name}' found only {discovered} candidate(s), expected >= "
                f"{rule.min_sites} — the code moved and this rule is no longer looking at it")
        report.append({"rule": rule.name, "sites": sites, "discovered": discovered,
                       "violations": violations})
        if violations:
            failures += 1
            print(f"\n✗ {rule.name}  ({sites} site(s) of {discovered} candidate(s))")
            for v in violations:
                print(f"    {v}")
            print(f"    WHY: {rule.why}")
        else:
            ok = "all exempt" if rule.mode == "forbid" else "all guarded"
            print(f"✓ {rule.name}  ({sites} site(s) of {discovered} candidate(s), {ok})")

    # Only meaningful over the full rule set — a --rule run has deliberately
    # narrowed it, so the answer would always be "uncovered" and mean nothing.
    if not args.rule:
        uncovered = _route_module_coverage()
        report.append({"rule": "route-module-coverage", "violations": uncovered})
        if uncovered:
            failures += 1
            print("\n✗ route-module-coverage")
            for u in uncovered:
                print(f"    {u}")
            print("    WHY: a web module no rule names is exactly how the entire HTTP "
                  "surface stayed outside every structural check in this repo.")
        else:
            print("✓ route-module-coverage  (every route-registering module has a rule)")

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
