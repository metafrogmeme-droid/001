"""Hyperliquid user-signed action decoder.

Covers approveAgent, approveBuilderFee, withdraw3, usdSend, spotSend,
sendAsset, usdClassTransfer, convertToMultiSigUser. Facts taken from the
public Hyperliquid docs: agents can trade but not withdraw; a builder fee
is signed by the main wallet; the documented caps are 0.1% perps and 1%
spot; agent expiry is 'valid_until <ms>' in agentName (max 180 days).
No network call.
"""
from __future__ import annotations

import re

from .evm import fmt_time
from .labels import counterparty_signals
from .signals import sig
from .util import norm_addr, to_int


def _pct(rate: str) -> float:
    s = str(rate).strip()
    return float(s[:-1]) if s.endswith("%") else float(s) * 100


def decode_hl_action(obj, now=None):
    a = obj.get("action", obj)
    t = a.get("type", "")
    chain = a.get("hyperliquidChain", "?")
    signals, summary, fields = [], [], {"type": t, "hyperliquidChain": chain,
                                         "signatureChainId": a.get("signatureChainId")}
    chain_id = a.get("signatureChainId", "?")
    summary.append(
        f"Hyperliquid '{t}' action on {chain} (signed with signatureChainId {chain_id})."
    )
    if chain == "Mainnet":
        signals.append(sig("HL_MAINNET", "info", "Targets Hyperliquid mainnet (real funds)."))

    if t == "approveAgent":
        agent = norm_addr(a.get("agentAddress", ""))
        name = a.get("agentName") or ""
        m = re.search(r"valid_until\s+(\d+)", name)
        fields.update(agent=agent, agentName=name)
        shown = name or "unnamed"
        summary.append(
            f"Approves API/agent wallet {agent} (name '{shown}') "
            "to place and cancel trades for your account."
        )
        signals.append(sig(
            "HL_AGENT_CAN_TRADE", "attention",
            "An agent wallet cannot withdraw, but it can trade your whole account "
            "(including into losses).",
        ))
        if m:
            vu = int(m.group(1))
            fields["valid_until_ms"] = vu
            summary.append(f"Expires {fmt_time(vu // 1000)}.")
            if now and vu / 1000 - now > 180 * 86400:
                signals.append(sig(
                    "HL_AGENT_EXPIRY_OVER_LIMIT", "attention",
                    "Requested expiry is more than 180 days away "
                    "(above the documented maximum).",
                ))
        else:
            signals.append(sig(
                "HL_AGENT_NO_CUSTOM_EXPIRY", "attention",
                "No 'valid_until' set, so the agent keeps the default expiry "
                "instead of a short one you chose.",
            ))
        if not name:
            signals.append(sig(
                "HL_UNNAMED_AGENT_REPLACES", "info",
                "An unnamed agent replaces your existing unnamed agent.",
            ))
        _, s = counterparty_signals(agent, "agent")
        signals += [x for x in s if x["code"] in ("COUNTERPARTY_ON_PUBLIC_SCAM_LIST",)]
    elif t == "approveBuilderFee":
        b = norm_addr(a.get("builder", ""))
        rate = a.get("maxFeeRate", "0%")
        pct = _pct(rate)
        fields.update(builder=b, maxFeeRate=rate, max_fee_pct=pct)
        summary.append(f"Lets builder {b} add a fee of up to {pct:g}% on orders it sends for you.")
        signals.append(sig("HL_BUILDER_FEE_APPROVAL", "attention" if pct > 0.01 else "info",
                           "Builder fees are charged on every fill the builder routes for you until you revoke them."))
        if pct >= 0.1:
            signals.append(sig("HL_BUILDER_FEE_AT_OR_ABOVE_PERP_CAP", "attention",
                               f"{pct:g}% is at or above the 0.1% perps cap (spot allows up to 1%)."))
        _, s = counterparty_signals(b, "builder")
        signals += [x for x in s if x["code"] == "COUNTERPARTY_ON_PUBLIC_SCAM_LIST"]
    elif t in ("withdraw3", "withdraw", "usdSend", "spotSend", "sendAsset"):
        dest = norm_addr(a.get("destination", ""))
        amt = a.get("amount", "?")
        tok = a.get("token", "USDC")
        fields.update(destination=dest, amount=amt, token=tok)
        verb = {"withdraw3": "Withdraws", "withdraw": "Withdraws", "usdSend": "Sends", "spotSend": "Sends",
                "sendAsset": "Sends"}[t]
        where = "to the Arbitrum address" if t.startswith("withdraw") else "to the Hyperliquid account"
        summary.append(f"{verb} {amt} {tok} {where} {dest}.")
        signals.append(sig(
            "HL_FUNDS_LEAVE_ACCOUNT", "attention",
            "This moves funds out of your account and cannot be cancelled once "
            "submitted. Check the destination is yours.",
        ))
        _, s = counterparty_signals(dest, "destination")
        signals += [x for x in s if x["code"] == "COUNTERPARTY_ON_PUBLIC_SCAM_LIST"]
        try:
            if float(str(amt)) >= 10000:
                signals.append(sig("LARGE_TRANSFER", "attention", f"Large amount: {amt}."))
        except ValueError:
            pass
    elif t == "convertToMultiSigUser":
        summary.append("Converts your account to a multisig account controlled by the listed signers.")
        signals.append(sig(
            "HL_ACCOUNT_CONTROL_CHANGE", "attention",
            "Changes who controls the account. If the signers are not yours, you lose control.",
        ))
        fields["signers"] = a.get("signers")
    elif t == "usdClassTransfer":
        summary.append(f"Moves {a.get('amount')} USDC between your perps and spot balances (toPerp={a.get('toPerp')}).")
        signals.append(sig("HL_INTERNAL_TRANSFER", "info", "Internal move between your own balances."))
    else:
        signals.append(sig("HL_UNKNOWN_ACTION", "attention", f"Action type '{t}' is not decoded by this prototype."))
    if a.get("time") or a.get("nonce"):
        ts = to_int(a.get("time") or a.get("nonce"))
        fields["nonce_ms"] = ts
    return {"kind": "hyperliquid_action", "summary": summary, "fields": fields, "signals": signals}


_HL_TYPED = {"HyperliquidTransaction:Withdraw": "withdraw3", "HyperliquidTransaction:UsdSend": "usdSend",
             "HyperliquidTransaction:SpotSend": "spotSend", "HyperliquidTransaction:ApproveAgent": "approveAgent",
             "HyperliquidTransaction:ApproveBuilderFee": "approveBuilderFee",
             "HyperliquidTransaction:SendAsset": "sendAsset",
             "HyperliquidTransaction:UsdClassTransfer": "usdClassTransfer",
             "HyperliquidTransaction:ConvertToMultiSigUser": "convertToMultiSigUser"}


def decode_hl_typed(td, now=None):
    pt = td.get("primaryType", "")
    act = dict(td.get("message", {}))
    act["type"] = _HL_TYPED.get(pt, pt)
    act.setdefault("signatureChainId", hex(to_int(td.get("domain", {}).get("chainId", 0))))
    r = decode_hl_action({"action": act}, now)
    r["summary"].insert(0, "EIP-712 typed data from the Hyperliquid signing domain (HyperliquidSignTransaction).")
    return r
