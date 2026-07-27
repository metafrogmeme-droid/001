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

THEN THE SAME THING AGAIN, ONE LAYER OUT

`app/` is the LARGEST HTTP surface in this repo — 228 Express routes against
the aiohttp gateway's 53 — and no rule named it. `express-route-auth` fixed
that at MODULE granularity, and was itself too coarse: it matches the string
`authMiddleware` anywhere in a file, so one authenticated route makes fourteen
public ones look fine. Express is why that matters — `router.use(mw)` applies
only to routes declared AFTER it, so a route above that line silently skips the
middleware inside a module that reads as guarded. Fifteen routes are in exactly
that position today (all deliberate, all now individually listed).

`express-mixed-module-routes` therefore works per ROUTE, implementing the real
semantics: guarded iff a `router.use(auth…)` precedes the route's position, or
the middleware is attached to that route itself. It only examines modules that
authenticate SOMETHING — a wholly-public module is justified once, as a module,
and re-listing its ~100 routes would bury the case that matters.

COMMENTS ARE NOT CODE

Mutation-testing that rule found the linter had the defect it exists to catch:
commenting out `router.use(authMiddleware)` left it GREEN, because the regex
matched the text inside the comment. A guard a comment can satisfy would pass on
a reverted fix with the explanation left behind. JS sources are now stripped
first — blanking to spaces, not deleting, because the whole rule turns on
whether a route sits before or after the `router.use` and re-indexing would
corrupt that. It strengthened the older rules too: a commented-out
`assertDevnet` is now caught.

FOUR SURFACES, NOT ONE — AND THE COVERAGE CHECK WAS THE THING THAT WAS BLIND

`_route_module_coverage` exists to stop a surface being born unwatched. It
looked for aiohttp registrations under `bot/` only, so it could not see:

    api_bridge.py             19 FastAPI routes at the repo ROOT
    bot/api/auth_routes.py     7 FastAPI routes — the auth API
    bot/api/lab.py             3 FastAPI routes
    app/auth.js               30 Express routes — login, OAuth, wallet linking
    app/server.js             37 Express routes

370 routes across four frameworks, and 96 of them outside every rule. The
meta-check written to prevent this exact failure had the exact failure.

It now scans the whole repository, keyed by file extension, and — this is the
part that mattered — keyed on the PATH LITERAL rather than the receiver's name.
`app` and `router` are conventions, not requirements: `bot/api/auth_routes.py`
uses `@auth_router.post("/register")`, `bot/api/lab.py` uses `@lab_router.get`,
and a JS module doing `const r = express.Router()` escaped entirely. That
assumption had to be removed from THREE separate places in this file. A first
argument beginning with `/` is what makes a call a route; `map.get('key')` is
not one.

Two rules follow from covering those surfaces: `fastapi-route-auth` (the guard
lives in the SIGNATURE, since FastAPI authenticates via `Depends(...)`) and
`server-js-serves-pages-not-data`, a forbid rule — server.js is exempt as a
wholly-public page server, so a data route added there would be checked by
nothing at all. That gap was found by mutation, not by reading.

LIMITS, STATED

`resolve_calls` answers "can the guard be reached from here", not "is the guard
always executed". A helper that guards on one branch and not another reads as
guarded. Closing that needs path-sensitive analysis; this is a reachability
linter and claiming more would be the same overstatement the audit keeps
finding. Depth is bounded (`_CALL_DEPTH`) and only bare-name calls are followed,
so a guard hidden three helpers deep behind a method call is a false positive —
which is the safe direction.

Dead code is not comments, and is NOT handled: `0 && assertKeyfilePermissions(…)`
still reads as a call, because deciding otherwise means evaluating expressions.
Mutation-tested and left failing on purpose rather than papered over — the
honest boundary of a regex tool is where the regex stops, and a reader who knows
that can rely on the rest.

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
    # "express-route"  — one candidate per Express ROUTE, in mixed modules only
    # "fastapi-route"  — one candidate per @app.<method> route (guard is in the
    #                    SIGNATURE: FastAPI authenticates via Depends(...))
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
        files=["app/routes/*.js", "app/auth.js", "app/server.js"],
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
            "server",      # app/server.js: 29 of its 37 routes are res.sendFile of a
                           # static page, plus /api/version, assetlinks.json, robots.txt
                           # and the leaderboard/arena/trader pages. It is the WEBSITE.
                           # Data lives behind the routers it mounts, each of which is
                           # checked on its own.
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
        name="express-mixed-module-routes",
        files=["app/routes/*.js", "app/auth.js", "app/server.js"],
        language="js",
        selector="express-route",
        trigger=r".",
        guard=r"authMiddleware|optionalAuth|botAuth",
        min_sites=120,
        # PUBLIC ROUTES INSIDE AN OTHERWISE-AUTHENTICATED MODULE.
        #
        # Every one is declared ABOVE its module's `router.use(authMiddleware)`,
        # which in Express means the middleware never runs for it. All fifteen
        # were read and each module's own header states the intent; the reason
        # is the grouping.
        exclude_functions=[
            # Teaching material — learn.js: "teaching material is not a secret".
            # Its per-user routes (/progress, /lessons/:slug/done) carry
            # authMiddleware INLINE, which is why the module looks mixed.
            "learn.js:GET /lessons", "learn.js:GET /lessons/:slug",
            # Paper-trading Arena: virtual funds only, and the leaderboard/tape
            # are the public competitive surface.
            "arena.js:GET /leaderboard", "arena.js:GET /tape",
            "arena.js:GET /trader/:handle", "arena.js:GET /season",
            "arena.js:GET /seasons",
            # OpenSea read-only data; /stats is marked "(public)" in the header
            # and /wallet/:address reflects an address the caller supplied.
            "nft.js:GET /stats", "nft.js:GET /radar", "nft.js:GET /wallet/:address",
            # "public sections" per its header; the operator-sensitive yield
            # payload is a separate admin-gated route and this one exposes only
            # a `has_yield` presence flag.
            "reports.js:GET /",
            # Provable Calls v3 — the daily Merkle seal-root feed is public by
            # design; mirroring it is what makes the timestamp independent.
            "roots.js:GET /", "roots.js:GET /anchor-plan/:day", "roots.js:GET /verify/:day",
            # "public curated radar (no user data, IP-rate-limited)".
            "airdrops.js:GET /",
            # sync.js authenticates with its own botAuth (shared secret). Its
            # one remaining pre-guard route is documented public market data —
            # /portfolio-summary was the OTHER one, and it served the operator's
            # equity to anyone until it was moved onto optionalAuth + the
            # scrubber. That is why this rule now knows the name `botAuth`.
            "sync.js:GET /scan",
            # app/auth.js — the routes that CANNOT require a session, because
            # they are how a session is obtained or recovered. Listed one by one
            # so a new unauthenticated route here has to be argued for.
            "auth.js:POST /register", "auth.js:POST /login",
            "auth.js:POST /forgot-password", "auth.js:POST /reset-password",
            "auth.js:POST /verify-email",
            "auth.js:POST /telegram", "auth.js:POST /google",
            "auth.js:GET /oauth/:provider/start", "auth.js:GET /oauth/:provider/callback",
            "auth.js:POST /validate-token",   # answers "is this token valid" — the
                                              # token IS the credential being checked
            "auth.js:GET /config",            # which sign-in providers are enabled
            "auth.js:POST /wallet/nonce",     # issues a nonce to sign; binds nothing
            "auth.js:POST /wallet/verify",    # proves wallet control, no account write
            # Redeemed FROM THE PHONE, which has no session: a 128-bit single-use
            # code (10-min TTL, issued by the AUTHED /wallet/link-code) carries the
            # account binding, and the request must also carry a signature over a
            # fresh nonce. Refuses if the wallet is already on another account.
            "auth.js:POST /wallet/link-by-code",
        ],
        why=("An unauthenticated route inside a module that authenticates its OTHER routes. "
             "`router.use(mw)` in Express applies only to routes declared AFTER it, so a "
             "route above that line silently skips the middleware while the module reads as "
             "guarded — and express-route-auth cannot see it, because that rule matches the "
             "string `authMiddleware` anywhere in the file. If the route is genuinely public, "
             "add it to exclude_functions with the reason; if not, move it below the "
             "`router.use` or attach the middleware to the route itself."),
    ),
    Rule(
        name="server-js-serves-pages-not-data",
        files=["app/server.js"],
        language="js",
        selector="express-route",
        mode="forbid",
        # server.js is the WEBSITE: 29 of its routes are res.sendFile of a static
        # page. Data belongs in the routers it mounts, each of which is checked
        # on its own — so the enforceable property is that server.js itself
        # registers nothing under /api/. Placement, not a guard call, exactly
        # like dashboard-route-placement.
        trigger=r"^\w+ /api/",
        guard="",
        min_sites=25,
        exclude_functions=[
            # Build metadata, no account in scope.
            "server.js:GET /api/version",
        ],
        why=("app/server.js registered a route under /api/. It is exempt from "
             "express-route-auth as a wholly-public page server, so a data route "
             "added here is checked by nothing at all. Put it in a router under "
             "app/routes/ where the auth rules can see it."),
    ),
    Rule(
        name="fastapi-route-auth",
        files=["api_bridge.py", "bot/api/auth_routes.py", "bot/api/lab.py"],
        language="python",
        selector="fastapi-route",
        trigger=r".",
        # Two dependencies, two apps: api_bridge guards with an operator bearer
        # token, the auth router with a per-user session.
        guard=r"require_dashboard_token|get_current_user_id",
        min_sites=25,
        # A FOURTH HTTP SURFACE, and the one that showed the coverage check
        # itself was too narrow: it looked for aiohttp registrations under
        # `bot/` only, so a FastAPI app at the repo root was invisible to the
        # very check written to stop a surface being born blind.
        exclude_functions=[
            "api_bridge.py:GET /health",         # liveness; counts and flags only
            "api_bridge.py:POST /scan",          # indicators over public market data,
                                                 # rate-limited, symbols regex-validated
            "api_bridge.py:GET /blackswan",      # anomaly state: type, symbol, severity
                                                 # ratio — market microstructure, no account
            "api_bridge.py:GET /patterns/{symbol}",
            "api_bridge.py:GET /insight/{symbol}",
            "api_bridge.py:GET /platform-url",   # a configured public URL
            # Legacy path redirects — one handler, no data.
            "api_bridge.py:GET /warroom", "api_bridge.py:GET /warroom.html",
            "api_bridge.py:GET /live-signals", "api_bridge.py:GET /live-signals.html",
            "api_bridge.py:GET /register", "api_bridge.py:GET /register.html",
            "api_bridge.py:GET /dashboard",
            # bot/api/auth_routes.py — how a session is obtained; cannot require one.
            "auth_routes.py:POST /register", "auth_routes.py:POST /login",
            "auth_routes.py:POST /refresh",   # the refresh TOKEN is the credential
            # bot/api/lab.py — the public Strategy Lab. Bounded by construction
            # rather than by a session: frozen snapshots only, symbols validated
            # against the snapshot, ONE job at a time (409), a submit-gap
            # throttle (429), and every numeric clamped (last_bars 200..6000,
            # balance 100..1e6, confidence 0..1). Reads no account.
            "lab.py:GET /lab/meta", "lab.py:POST /lab/run",
            "lab.py:GET /lab/status/{job_id}",
        ],
        why=("A FastAPI route with no `require_dashboard_token` dependency. This app "
             "reaches the live engine — /confirm places a trade, /portfolio/close "
             "flattens a position, /risk/halt trips the breaker — and the token "
             "dependency fails CLOSED (503) when DASHBOARD_TOKEN is unset, which is "
             "the property to preserve. If the new route is genuinely public, add it "
             "to exclude_functions with the reason."),
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


@lru_cache(maxsize=None)
def _strip_js_comments(src: str) -> str:
    """JS source with `//` and `/* */` comments blanked, LENGTHS PRESERVED.

    Found by mutation-testing: commenting out `router.use(authMiddleware)` left
    the rule GREEN, because the regex matched the text inside the comment. A
    guard that a comment can satisfy is not a guard — the same failure this
    audit found in three of its own tests, arriving here in the linter that
    exists to catch it.

    Comments are replaced with spaces rather than removed so every byte offset
    still lines up: the whole rule turns on whether a route is declared before
    or after the `router.use`, and re-indexing would silently corrupt that.
    """
    out = list(src)
    i, n = 0, len(src)
    quote = None
    while i < n:
        c = src[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "\"'`":
            quote = c
            i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                out[i] = " "
                i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            while i < n and not (src[i] == "*" and i + 1 < n and src[i + 1] == "/"):
                if src[i] != "\n":
                    out[i] = " "
                i += 1
            for k in range(i, min(i + 2, n)):
                out[k] = " "
            i += 2
            continue
        i += 1
    return "".join(out)


# `app.` as well as `router.`: app/server.js registers 37 page routes directly
# on the app, and a rule that only knew `router.` could not see any of them.
_EXPRESS_ROUTE = re.compile(r"(?:router|app)\.(get|post|put|delete|patch)\s*\(", re.M)
# Every auth middleware mounted with router.use in this repo. `botAuth` is
# here because sync.js rolls its own shared-secret check under that name, and a
# rule that only knew the two common names examined none of its 23 routes —
# which is how an unauthenticated equity endpoint sat inside a module whose
# header says "Authenticated via a shared secret".
_AUTH_MW = r"authMiddleware|optionalAuth|botAuth"
_EXPRESS_USE_AUTH = re.compile(rf"router\.use\(\s*({_AUTH_MW})\s*\)")


def _express_routes(src: str) -> int:
    """How many HTTP routes this Express router module declares."""
    return len(_EXPRESS_ROUTE.findall(src))


def _balanced_call(src: str, start: int) -> str:
    """`router.get( ... )` text from `start`, paren-balanced."""
    i = src.index("(", start)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "(":
            depth += 1
        elif src[j] == ")":
            depth -= 1
            if depth == 0:
                return src[i:j + 1]
    return src[i:i + 400]


def _express_route_guards(src: str) -> list[tuple[str, str]]:
    """[(``METHOD /path``, the middleware in effect for it)] per route.

    EXPRESS SEMANTICS, WHICH ARE THE WHOLE POINT. `router.use(mw)` applies only
    to routes declared AFTER it in the file — a route above that line is
    unauthenticated inside a module that reads as guarded. Middleware can also
    be attached per route: `router.get('/x', authMiddleware, handler)`.

    So a route is guarded iff a `router.use(auth…)` precedes its position, or
    the auth middleware appears in its own handler list. The returned "context"
    is exactly that text, so the rule's ordinary guard regex decides.
    """
    covers_from = min([m.start() for m in _EXPRESS_USE_AUTH.finditer(src)] or [None] or [], default=None)
    out = []
    for m in _EXPRESS_ROUTE.finditer(src):
        call = _balanced_call(src, m.start())
        path_m = re.match(r"\(\s*['\"]([^'\"]*)['\"]", call)
        path = path_m.group(1) if path_m else "<computed>"
        # Handler list only — up to the first function body, so middleware named
        # inside a handler's own code cannot masquerade as a route guard.
        handlers = call.split("{", 1)[0]
        context = handlers
        if covers_from is not None and m.start() > covers_from:
            context += "\nrouter.use(authMiddleware)  /* in effect above */"
        out.append((f"{m.group(1).upper()} {path}", context))
    return out


def _fastapi_routes(src: str) -> list[tuple[str, str]]:
    """[(``METHOD /path``, the handler's signature)] for each @app.<method> route.

    The guard lives in the SIGNATURE, not the body: FastAPI authenticates with
    `Depends(...)` on a parameter, so the question "is this route guarded" is
    answered by its parameter list alone.
    """
    out = []
    tree = _parse(src)
    lines = _lines(src)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for d in node.decorator_list:
            # ANY receiver, decided by the path literal. `app` and `router` are
            # conventions: bot/api/auth_routes.py uses `@auth_router.post("/…")`
            # and bot/api/lab.py `@lab_router.get("/…")`, so a receiver-name
            # check missed ten routes including the entire auth API. This is the
            # third place in this file that assumption had to be removed.
            if not (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                    and isinstance(d.func.value, ast.Name)
                    and d.func.attr in ("get", "post", "put", "delete", "patch")
                    and d.args):
                continue
            try:
                path = ast.literal_eval(d.args[0])
            except (ValueError, SyntaxError):
                continue
            if not isinstance(path, str) or not path.startswith("/"):
                continue
            # Signature only — up to the closing paren of the def.
            sig = "".join(lines[node.lineno - 1:node.lineno + 8])
            sig = sig.split("):", 1)[0] if "):" in sig else sig
            out.append((f"{d.func.attr.upper()} {path}", sig))
    return out


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
            # Comments are not code. A rule that a commented-out line can
            # satisfy would pass on a reverted fix with the explanation left
            # behind — which is exactly how E4 slipped through in testing.
            if rule.language == "js":
                src = _strip_js_comments(src)
            try:
                if rule.selector == "fastapi-route":
                    routes = _fastapi_routes(src)
                    discovered += len(routes)
                    candidates = [(f"{rel.name}:{label}", f"{rel.name}:{label}", sig)
                                  for label, sig in routes if trigger.search(label)]
                elif rule.selector == "express-route":
                    # ROUTE granularity, and only for MIXED modules — ones that
                    # authenticate something. A module with no auth anywhere is
                    # a wholly-public surface, already justified entry-by-entry
                    # in express-route-auth's exempt list; re-listing all ~100
                    # of its routes here would bury the case that matters.
                    #
                    # The case that matters is a module that authenticates SOME
                    # routes and not others, because that is the one a reader
                    # assumes is guarded. express-route-auth cannot see it: it
                    # matches the string `authMiddleware` anywhere in the file,
                    # so one authed route makes fourteen public ones look fine.
                    # `mode="forbid"` asks whether a route EXISTS, not whether a
                    # guard was applied, so the mixed-module filter must not
                    # apply to it — otherwise a wholly-public module (exempt at
                    # module level) could gain a data route unseen. Found by
                    # mutation: adding `app.get('/api/secret-equity', …)` to
                    # server.js was invisible to every rule.
                    if rule.mode != "forbid" and not re.search(_AUTH_MW, src):
                        continue
                    routes = _express_route_guards(src)
                    discovered += len(routes)
                    candidates = [(f"{rel.name}:{label}", f"{rel.name}:{label}", ctx)
                                  for label, ctx in routes if trigger.search(label)]
                elif rule.selector == "express-router":
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


# Every way this repo registers an HTTP route. The coverage check below is only
# as good as this table — see its docstring for why that is the point.
_ROUTE_SIGNATURES = {
    # Language-scoped: an Express pattern run over Python matched FastAPI
    # routers AND this file's own regex literals.
    #
    # Both JS and Python entries key on the PATH LITERAL rather than the
    # receiver's name. `router`/`app` are conventions, not requirements —
    # `bot/api/auth_routes.py` uses `@auth_router.post("/register")` and
    # `bot/api/lab.py` uses `@lab_router.get(...)`, so a receiver-name regex
    # missed ten real routes including the entire auth API. A first argument
    # beginning with `/` is what makes it a route; `map.get('key')` is not one.
    ".py": {
        "aiohttp": re.compile(r"\.add_(?:get|post|put|delete|patch)\("),
        "fastapi": re.compile(r"^@\w+\.(?:get|post|put|delete|patch)\(\s*[\"']/", re.M),
    },
    ".js": {"express": re.compile(r"\.(?:get|post|put|delete|patch)\(\s*['\"`]/", re.M)},
    ".mjs": {"express": re.compile(r"\.(?:get|post|put|delete|patch)\(\s*['\"`]/", re.M)},
}
_COVERAGE_SKIP = ("node_modules", "/.git/", "/tests/", "/test/", ".test.",
                  "_test.", "/dist/", "/build/", "/.venv/", "/venv/")
_ROUTE_SELECTORS = ("aiohttp-route", "express-router", "express-route", "fastapi-route")


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
    watched = {p.resolve() for r in RULES if r.selector in _ROUTE_SELECTORS
               for pat in r.files for p in REPO.glob(pat)}
    problems = []
    for path in sorted(REPO.rglob("*")):
        if path.suffix not in (".py", ".js", ".mjs") or not path.is_file():
            continue
        rel = str(path)
        if any(k in rel for k in _COVERAGE_SKIP):
            continue
        try:
            src = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if path.suffix in (".js", ".mjs"):
            # Comments are not code — here too. app/lib/rate_limit.js documents
            # its usage with `router.post('/', rateLimit(…))` in a docblock, and
            # an unstripped scan reported a rate-limiter as an HTTP surface.
            src = _strip_js_comments(src)
        hits = {fw: len(rx.findall(src))
                for fw, rx in _ROUTE_SIGNATURES.get(path.suffix, {}).items()}
        total = sum(hits.values())
        if not total or path.resolve() in watched:
            continue
        kinds = ", ".join(f"{n} {fw}" for fw, n in hits.items() if n)
        problems.append(
            f"{path.relative_to(REPO)} registers HTTP route(s) ({kinds}) but no "
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
