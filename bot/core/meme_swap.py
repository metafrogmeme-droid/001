"""Unsigned Jupiter swap transactions — the build step before any signature.

`meme_executor.plan_swap` answers "may this trade happen". This answers "on what
exact terms, right now" and hands back a transaction the USER signs in their own
wallet. It is the first half of the signing slice; the second half is a browser
page calling `signTransaction` on Phantom/Backpack.

    NOTHING HERE SIGNS, AND NOTHING HERE HOLDS A KEY.

That is not a limitation, it is the custody model every neighbouring file
already states: `meme_executor`'s docstring ("RUNECLAW never holds funds; the
plan is executed by signing on the user's OWN key"), `solana_wallet.js`
("Non-custodial: only ever calls connect() and signMessage()"), and
`/linkwallet`, which VERIFIES an ed25519 signature and has never produced one.
The repo has no Solana signing library at all, and this module does not add one.
A `user_public_key` is required and a private key is never a parameter.

THREE WAYS A SWAP BUILDER LOSES MONEY, AND WHAT IS DONE ABOUT EACH

1. A STALE QUOTE. A quote is a price at a moment. Signing one from ten minutes
   ago executes at a price nobody agreed to, and the wallet UI will happily show
   the old numbers. Every quote here carries an expiry, an expired one is
   refused rather than rebuilt silently, and the expiry travels in the returned
   terms so the signing surface can refuse it too.

2. A STALE AUTHORISATION. `/memeplan` answered a question about the market as it
   was. Reusing that verdict to authorise a swap now is the same defect one
   level up, so the plan is RE-CHECKED here: a plan that is not `allowed`, or
   that has aged past `MAX_PLAN_AGE_S`, cannot be built into a transaction.

3. DOUBLE EXECUTION. Retrying a build is free; retrying a BROADCAST is not.
   This module is deliberately safe to call twice — it holds no state and sends
   nothing — and it stamps each build with an `intent_id` derived from the
   terms, so the signing surface can refuse to send the same intent twice
   instead of discovering the duplicate on-chain.

NETWORK DEFAULTS TO SIMULATION, and mainnet needs a deliberate act. Building a
mainnet transaction requires MEME_EXECUTION_NETWORK=mainnet explicitly. Fail-
closed applies to the network too: an unrecognised value is not mainnet.

    "SIMULATE" DESCRIBES WHAT WE WILL DO WITH THE TRANSACTION.
    IT DOES NOT DESCRIBE THE TRANSACTION.

That distinction is the whole of `signable`, and getting it wrong is how this
module would have cost somebody real money. **Jupiter's v6 quote API is
mainnet-only** — there is no devnet deployment carrying routes — so the bytes
in `unsigned_transaction` are a MAINNET transaction under every value of
MEME_EXECUTION_NETWORK. A wallet asked to sign them does not read our label; it
signs what it is given, against whichever cluster it is pointed at.

So `simulate` cannot mean "a harmless practice transaction". The only honest
meaning available is **"build it, show it, refuse to sign it"**, and that is
what `signable` carries: False on anything but an explicitly named mainnet,
with `not_signable_reason` saying why in words a user can act on.

This is stated as a field rather than left for each caller to derive, because
the browser deriving it independently is precisely the bug: `network` reads as
a safety claim, and a signing surface that trusted the word "simulate" would
hand a real-funds transaction to Phantom under a label promising it was safe.
The policy lives here; the browser fail-closes on the flag being absent.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

SIMULATE, DEVNET, MAINNET = "simulate", "devnet", "mainnet"
VALID_NETWORKS = (SIMULATE, DEVNET, MAINNET)

#: How long a built transaction may sit before the terms are no longer the
#: terms. Jupiter quotes move with the pool; a minute is generous already.
QUOTE_TTL_S = 45.0

#: How stale a `/memeplan` verdict may be and still authorise a build. The plan
#: read liquidity, age and buy/sell counts — all of which move.
MAX_PLAN_AGE_S = 120.0

#: USDC has six decimals on Solana. This is the ONLY conversion done here, and
#: only for buys, where the input leg is USDC. A sell's input is the token
#: itself, whose decimals this module does not know and will not guess.
USDC_DECIMALS = 6

JUP_QUOTE = "https://quote-api.jup.ag/v6/quote"
JUP_SWAP = "https://quote-api.jup.ag/v6/swap"


def network(env: Optional[dict] = None) -> str:
    """The configured network — SIMULATE unless mainnet was asked for by name."""
    e = env if env is not None else os.environ
    raw = str(e.get("MEME_EXECUTION_NETWORK", "")).strip().lower()
    return raw if raw in VALID_NETWORKS else SIMULATE


def _num(x: Any) -> Optional[float]:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and v not in (float("inf"), float("-inf")) else None


def _refuse(reason: str, net: str, **extra) -> dict:
    out = {"buildable": False, "reason": reason, "network": net,
           "unsigned_transaction": None, "terms": None, "intent_id": None,
           "signable": False, "not_signable_reason": reason}
    out.update(extra)
    return out


def signable(net: str) -> tuple[bool, str]:
    """May a transaction built on this network be SIGNED? ``(ok, reason)``.

    Separate from `buildable` on purpose, and the reason is in the module
    docstring: Jupiter v6 is mainnet-only, so a build always carries a mainnet
    transaction and the network label describes our INTENT, not the bytes.
    Only an explicitly named mainnet may be signed; everything else — including
    an unset variable, a typo, and `devnet` — is build-and-review only.
    """
    if net == MAINNET:
        return True, "mainnet — signing sends real funds"
    if net == DEVNET:
        # Naming it beats silently folding it into "simulate": an operator who
        # set devnet expects a devnet transaction, and needs to hear that no
        # such thing came back.
        return False, ("devnet was requested, but Jupiter v6 quotes mainnet only "
                       "— the transaction below is a MAINNET one and will not be "
                       "signed. Review-only.")
    return False, ("simulation mode: the terms and the transaction are real, and "
                   "nothing will be signed or sent. Set "
                   "MEME_EXECUTION_NETWORK=mainnet to enable signing.")


def intent_id(*, user_public_key: str, input_mint: str, output_mint: str,
              amount: int, slippage_bps: int, expires_at: float) -> str:
    """A stable id for THESE terms.

    Two builds of the same swap at the same expiry produce the same id, so a
    signing surface can refuse to broadcast an intent it has already sent. It
    deliberately includes the expiry: a re-quote is a NEW intent, because it is
    a new price, and treating it as a repeat would suppress a legitimate retry.
    """
    blob = json.dumps({"u": user_public_key, "i": input_mint, "o": output_mint,
                       "a": amount, "s": slippage_bps, "e": round(expires_at, 3)},
                      sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


async def build_swap(plan: Optional[dict], *, user_public_key: str = "",
                     amount_base_units: Optional[int] = None,
                     plan_created_at: Optional[float] = None,
                     transport: Optional[Callable] = None,
                     env: Optional[dict] = None,
                     now: Optional[Callable[[], float]] = None) -> dict:
    """An unsigned swap transaction for an ALLOWED plan, or a named refusal.

    Returns ``{buildable, reason, network, unsigned_transaction, terms,
    intent_id}``. ``unsigned_transaction`` is base64 exactly as Jupiter returned
    it — this module never decodes, modifies or signs it.
    """
    clock = now or time.time
    net = network(env)
    plan = plan or {}

    # ── the plan must still authorise this ────────────────────────────────
    if plan.get("allowed") is not True:
        return _refuse("plan is not allowed — nothing to build", net)
    # Defensive, and deliberately absolute: this module exists BECAUSE the
    # planner cannot execute. A plan claiming otherwise is not one this code
    # understands, and the safe reading of a contract violation is to stop.
    if plan.get("would_execute") is not False:
        return _refuse("plan claims it would execute — refusing to build", net)

    if plan_created_at is not None:
        age = clock() - float(plan_created_at)
        if age > MAX_PLAN_AGE_S:
            return _refuse(
                f"plan is {age:.0f}s old (max {MAX_PLAN_AGE_S:.0f}s) — "
                "re-run the preflight; liquidity and exits move", net)

    if not user_public_key:
        return _refuse("no user public key — RUNECLAW signs nothing and needs "
                       "the wallet that will", net)

    jup = plan.get("jupiter_request") or {}
    in_mint = jup.get("inputMint")
    out_mint = jup.get("outputMint")
    slippage_bps = int(_num(jup.get("slippageBps")) or 100)
    if not in_mint or not out_mint:
        return _refuse("plan carries no swap legs", net)

    # ── the amount, in base units, without guessing ───────────────────────
    amount = amount_base_units
    if amount is None:
        size_usd = _num(jup.get("amount_usd"))
        if str(plan.get("side")) == "buy" and size_usd is not None:
            amount = int(round(size_usd * (10 ** USDC_DECIMALS)))
        else:
            # A sell's input leg is the token, and its decimals are not known
            # here. Guessing would size the trade wrong by orders of magnitude.
            return _refuse("sell needs amount_base_units — this module does not "
                           "know the token's decimals and will not assume them",
                           net)
    if amount <= 0:
        return _refuse("amount must be positive", net)

    # ── quote, then transaction ───────────────────────────────────────────
    try:
        quote = await _get(transport, JUP_QUOTE, params={
            "inputMint": in_mint, "outputMint": out_mint,
            "amount": str(amount), "slippageBps": str(slippage_bps)})
    except Exception as exc:                                      # noqa: BLE001
        logger.debug("jupiter quote failed: %s", exc)
        return _refuse(f"quote unavailable: {str(exc)[:80]}", net)
    if not isinstance(quote, dict) or not quote.get("outAmount"):
        return _refuse("no route for this pair and size", net)

    expires_at = clock() + QUOTE_TTL_S
    try:
        swap = await _post(transport, JUP_SWAP, body={
            "quoteResponse": quote,
            "userPublicKey": user_public_key,
            "wrapAndUnwrapSol": True})
    except Exception as exc:                                      # noqa: BLE001
        logger.debug("jupiter swap build failed: %s", exc)
        return _refuse(f"transaction build failed: {str(exc)[:80]}", net)

    tx = (swap or {}).get("swapTransaction")
    if not tx or not isinstance(tx, str):
        return _refuse("jupiter returned no transaction", net)

    terms = {
        "input_mint": in_mint, "output_mint": out_mint,
        "in_amount": str(quote.get("inAmount") or amount),
        "out_amount": str(quote.get("outAmount")),
        "other_amount_threshold": str(quote.get("otherAmountThreshold") or ""),
        "price_impact_pct": _num(quote.get("priceImpactPct")),
        "slippage_bps": slippage_bps,
        "expires_at": expires_at,
        "ttl_s": QUOTE_TTL_S,
    }
    may_sign, sign_reason = signable(net)
    return {
        "buildable": True,
        # The old wording here read "simulation build — nothing will reach
        # mainnet", which was false: Jupiter hands back a mainnet transaction
        # whatever we call the mode. What is true is that WE will not send it.
        "reason": ("mainnet transaction ready to sign" if may_sign
                   else "built for review only — " + sign_reason),
        "network": net,
        "signable": may_sign,
        "not_signable_reason": None if may_sign else sign_reason,
        "unsigned_transaction": tx,
        "terms": terms,
        "intent_id": intent_id(user_public_key=user_public_key,
                               input_mint=in_mint, output_mint=out_mint,
                               amount=amount, slippage_bps=slippage_bps,
                               expires_at=expires_at),
        # Stated, not implied. Every consumer of this dict is one keystroke away
        # from believing something was sent.
        "signed": False,
        "broadcast": False,
    }


def terms_expired(terms: Optional[dict], now: Optional[Callable[[], float]] = None) -> bool:
    """True when these terms may no longer be signed.

    The signing surface asks this again at click time: a page open for two
    minutes holds a quote that stopped being true ninety seconds ago, and the
    wallet will show the stale numbers without complaint.
    """
    exp = _num((terms or {}).get("expires_at"))
    if exp is None:
        return True                       # undateable terms are expired terms
    return (now or time.time)() >= exp


async def _get(transport, url, params):
    if transport is not None:
        return await transport("GET", url, params, None)
    import aiohttp
    async with aiohttp.ClientSession() as s:
        async with s.get(url, params=params,
                         timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status != 200:
                raise RuntimeError(f"HTTP {r.status}")
            return await r.json(content_type=None)


async def _post(transport, url, body):
    if transport is not None:
        return await transport("POST", url, None, body)
    import aiohttp
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=body,
                          timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status != 200:
                raise RuntimeError(f"HTTP {r.status}")
            return await r.json(content_type=None)


def human_readable(build: Optional[dict]) -> str:
    """Plain text: the terms, the network, and what has NOT happened."""
    if not build:
        return "No swap build."
    if not build.get("buildable"):
        return f"✗ cannot build: {build.get('reason')}  [{build.get('network')}]"
    t = build.get("terms") or {}
    impact = t.get("price_impact_pct")
    lines = [
        f"SWAP READY TO SIGN  [{build.get('network')}]",
        f"   in:  {t.get('in_amount')} ({t.get('input_mint')})",
        f"   out: {t.get('out_amount')} ({t.get('output_mint')})",
        f"   slippage: {t.get('slippage_bps')} bps"
        + (f" · price impact {impact:.2%}" if impact is not None else
           " · price impact unknown"),
        f"   intent: {build.get('intent_id')}",
        "   NOT signed, NOT broadcast — you sign this in your own wallet.",
    ]
    if not build.get("signable"):
        # This line used to read "this cannot move mainnet funds", which was the
        # opposite of true: Jupiter v6 is mainnet-only, so the transaction above
        # IS a mainnet one. What is guaranteed is that nothing here will sign it.
        lines.append("   REVIEW ONLY — " + str(build.get("not_signable_reason")))
        lines.append("   The transaction above is a MAINNET transaction "
                     "(Jupiter quotes mainnet only). Do not sign it by hand.")
    return "\n".join(lines)
