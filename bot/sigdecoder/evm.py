"""EVM decoders: EIP-712 typed data (Permit, DAI permit, Permit2, Seaport, EIP-3009, unknown),
EIP-7702 authorization tuples, raw personal_sign / eth_sign messages, common calldata.

Output is public signals. This module does not call a network.
"""
from __future__ import annotations

import datetime

from .labels import BAD_7702, GOOD_7702, SCAM, counterparty_signals, label
from .signals import sig
from .util import (
    MAX_UINT48,
    MAX_UINT160,
    MAX_UINT256,
    int_field,
    norm_addr,
    selector,
    to_int,
    typed_data_digest,
)

PERMIT2 = "0x000000000022d473030f116ddee9f6b43ac78ba3"
DAY = 86400


def fmt_time(ts):
    try:
        return datetime.datetime.fromtimestamp(int(ts), datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (OverflowError, ValueError, OSError):
        return f"timestamp {ts}"


def fmt_amount(v, unlimited_at=None):
    v = to_int(v)
    if unlimited_at and v >= unlimited_at:
        return "UNLIMITED"
    if v >= MAX_UINT256 // 2:
        return "UNLIMITED"
    return str(v)


def deadline_signals(ts, now, what="signature"):
    out = []
    ts = to_int(ts)
    if ts == 0:
        out.append(sig("NO_DEADLINE", "attention", f"The {what} has no deadline (0)."))
    elif ts >= MAX_UINT48 or ts - now > 3650 * DAY:
        out.append(sig("NO_PRACTICAL_DEADLINE", "attention", f"The {what} effectively never expires ({ts})."))
    elif ts - now > 30 * DAY:
        out.append(sig("LONG_DEADLINE", "attention",
                       f"The {what} stays valid until {fmt_time(ts)} (more than 30 days away)."))
    elif ts < now:
        out.append(sig("DEADLINE_IN_PAST", "info", f"The {what} deadline {fmt_time(ts)} is already in the past."))
    return out


def _result(kind, summary, fields, signals, digest=None):
    r = {"kind": kind, "summary": summary, "fields": fields, "signals": signals}
    if digest:
        r["eip712_digest"] = digest
    return r


# ---------------- typed data ----------------

def decode_typed_data(td, now):
    dom = td.get("domain", {})
    pt = td.get("primaryType", "")
    msg = td.get("message", {})
    name = str(dom.get("name", ""))
    vc = norm_addr(dom.get("verifyingContract", ""))
    chain = dom.get("chainId")
    try:
        digest = typed_data_digest(td)
    except Exception:  # noqa: BLE001 - digest is best effort
        digest = None
    head = [f"Typed-data signature '{pt}' for '{name or '?'}' on chainId {to_int(chain) if chain is not None else '?'}"
            + (f", contract {vc} ({label(vc) or 'unlabelled'})" if vc.startswith("0x") else "") + "."]

    if name == "HyperliquidSignTransaction" or pt.startswith("HyperliquidTransaction:"):
        from .hl import decode_hl_typed
        return decode_hl_typed(td, now)
    if vc == PERMIT2 or name == "Permit2":
        return _permit2(pt, msg, now, head, digest)
    if name.lower().startswith("seaport") or pt == "OrderComponents":
        return _seaport(msg, now, head, digest)
    if pt in ("TransferWithAuthorization", "ReceiveWithAuthorization"):
        r = decode_3009(dict(msg, token=vc, primaryType=pt, chainId=chain), now)
        r["summary"] = head + r["summary"]
        r["eip712_digest"] = digest
        return r
    if pt == "Permit" and "holder" in msg and "allowed" in msg:
        return _dai_permit(msg, now, head, digest, vc)
    if pt == "Permit" and "spender" in msg:
        return _erc2612(msg, now, head, digest, vc)
    return _unknown_typed(pt, msg, now, head, digest)


def _amount_text(obj, key, unlimited_at=None):
    """A formatted amount, or None when the field is not in the payload."""
    raw = int_field(obj, key)
    if raw is None:
        return None
    return fmt_amount(raw, unlimited_at)


def _absent_amount(signals, what):
    signals.append(sig(
        "AMOUNT_NOT_IN_PAYLOAD", "attention",
        f"The {what} is not in this request, so no amount was read.",
    ))


def _erc2612(msg, now, head, digest, token):
    spender = norm_addr(msg["spender"])
    lab, s = counterparty_signals(spender, "spender")
    amt = _amount_text(msg, "value")
    signals = list(s)
    if amt is None:
        _absent_amount(signals, "approval amount")
        shown = "absent"
    else:
        shown = amt
        if amt == "UNLIMITED":
            signals.append(sig("UNLIMITED_AMOUNT", "attention", "The approval amount is unlimited."))
    signals += deadline_signals(msg.get("deadline", 0), now, "permit")
    signals.append(sig(
        "OFFCHAIN_APPROVAL", "info",
        "This is a gasless approval: nothing happens on-chain until someone submits it, "
        "so wallets and revoke tools do not see it yet.",
    ))
    summary = head + [
        f"Lets {spender} ({lab or 'unlabelled'}) spend {shown} of token {token} "
        f"({label(token) or 'unlabelled'}) from {norm_addr(msg.get('owner', ''))}.",
        f"Deadline: {fmt_time(to_int(msg.get('deadline', 0)))}.",
    ]
    return _result(
        "eip2612_permit", summary,
        {"spender": spender, "token": token, "amount": shown, "deadline": to_int(msg.get("deadline", 0))},
        signals, digest,
    )


def _dai_permit(msg, now, head, digest, token):
    spender = norm_addr(msg["spender"])
    lab, s = counterparty_signals(spender, "spender")
    allowed = msg.get("allowed") in (True, "true", 1, "1")
    signals = list(s)
    if allowed:
        signals.append(sig(
            "UNLIMITED_AMOUNT", "attention",
            "DAI-style permit with allowed=true grants an UNLIMITED approval.",
        ))
    signals += deadline_signals(msg.get("expiry", 0), now, "permit")
    verb = "Grants" if allowed else "Removes"
    token_label = label(token) or "unlabelled"
    summary = head + [
        f"{verb} unlimited spending of {token} ({token_label}) "
        f"to {spender} ({lab or 'unlabelled'}).",
    ]
    return _result("dai_permit", summary, {"spender": spender, "token": token,
                   "amount": "UNLIMITED" if allowed else "0", "expiry": to_int(msg.get("expiry", 0))}, signals, digest)


def _permit2(pt, msg, now, head, digest):
    signals: list = []
    summary: list[str] = list(head)
    spender = norm_addr(msg.get("spender", ""))
    lab, s = counterparty_signals(spender, "spender")
    signals += s
    tokens: list[dict] = []
    fields: dict = {"spender": spender, "tokens": tokens}
    if pt in ("PermitSingle", "PermitBatch"):
        details = msg["details"] if isinstance(msg["details"], list) else [msg["details"]]
        for d in details:
            amt = _amount_text(d, "amount", MAX_UINT160)
            exp = to_int(d.get("expiration", 0))
            tok = norm_addr(d.get("token", ""))
            shown = "absent" if amt is None else amt
            fields["tokens"].append({"token": tok, "amount": shown, "expiration": exp})
            summary.append(
                f"Permit2 allowance: {spender} ({lab or 'unlabelled'}) may pull {shown} of {tok} "
                f"({label(tok) or 'unlabelled'}) until {fmt_time(exp)}."
            )
            if amt is None:
                _absent_amount(signals, f"Permit2 allowance for {tok}")
            elif amt == "UNLIMITED":
                signals.append(sig("UNLIMITED_AMOUNT", "attention", f"Unlimited Permit2 allowance for {tok}."))
            signals += deadline_signals(exp, now, f"Permit2 allowance for {tok}")
        signals += deadline_signals(msg.get("sigDeadline", 0), now, "signature")
        if len(details) > 1:
            signals.append(sig(
                "MULTI_TOKEN_APPROVAL", "attention",
                f"One signature approves {len(details)} tokens at once "
                "(a common pattern in Permit2 batch drains).",
            ))
    elif "TransferFrom" in pt:
        perm = msg.get("permitted")
        perms = perm if isinstance(perm, list) else [perm]
        for p in perms:
            amt = _amount_text(p, "amount")
            tok = norm_addr(p.get("token", ""))
            shown = "absent" if amt is None else amt
            fields["tokens"].append({"token": tok, "amount": shown})
            summary.append(
                f"Permit2 one-time transfer: {spender} ({lab or 'unlabelled'}) may move {shown} of {tok} "
                f"({label(tok) or 'unlabelled'}) to a recipient it chooses at execution time."
            )
            if amt is None:
                _absent_amount(signals, f"transfer amount for {tok}")
            elif amt == "UNLIMITED":
                signals.append(sig("UNLIMITED_AMOUNT", "attention", f"Unlimited transfer amount for {tok}."))
        signals.append(sig(
            "RECIPIENT_CHOSEN_BY_SPENDER", "attention" if not lab else "info",
            "With Permit2 SignatureTransfer the spender picks the final recipient; "
            "the signature does not name it.",
        ))
        signals += deadline_signals(msg.get("deadline", 0), now, "signature")
        if len(perms) > 1:
            signals.append(sig("MULTI_TOKEN_APPROVAL", "attention", f"One signature covers {len(perms)} tokens."))
        if "witness" in msg:
            summary.append("Includes a witness (an order attached to the transfer, e.g. UniswapX).")
    else:
        return _unknown_typed(pt, msg, now, head, digest)
    signals.append(sig(
        "OFFCHAIN_APPROVAL", "info",
        "Gasless signature: revoke tools only see it once it is used on-chain.",
    ))
    return _result("permit2_" + pt, summary, fields, signals, digest)


def _seaport(msg, now, head, digest):
    offerer = norm_addr(msg.get("offerer", ""))
    signals: list = []
    summary: list[str] = list(head)
    offer = msg.get("offer", [])
    cons = msg.get("consideration", [])
    itypes = {0: "ETH", 1: "ERC20", 2: "ERC721", 3: "ERC1155", 4: "ERC721-criteria", 5: "ERC1155-criteria"}
    for o in offer:
        kind = itypes.get(to_int(o.get("itemType", 0)), "?")
        summary.append(
            f"You give: {kind} {norm_addr(o.get('token', ''))} "
            f"id {o.get('identifierOrCriteria')} amount {o.get('startAmount')}."
        )
    to_offerer = 0
    missing_amount = False
    third = set()
    for c in cons:
        rec = norm_addr(c.get("recipient", ""))
        raw_amt = int_field(c, "startAmount")
        kind = itypes.get(to_int(c.get("itemType", 0)), "?")
        if raw_amt is None:
            missing_amount = True
            _absent_amount(signals, "consideration amount")
            shown = "absent"
        else:
            shown = str(raw_amt)
            if rec == offerer:
                to_offerer += raw_amt
        summary.append(
            f"Paid out: {shown} {kind} {norm_addr(c.get('token', ''))} to {rec} "
            f"({label(rec) or ('you' if rec == offerer else 'unlabelled')})."
        )
        if rec != offerer:
            third.add(rec)
    if offer and to_offerer == 0 and not missing_amount:
        signals.append(sig(
            "ORDER_PAYS_YOU_NOTHING", "attention",
            "This listing sends your items away and pays you nothing "
            "(the classic Seaport listing-signature drain).",
        ))
    for t in sorted(third):
        if label(t):
            continue
        _, s = counterparty_signals(t, "payout recipient")
        signals += [x for x in s if x["code"] != "COUNTERPARTY_ON_ALLOWLIST"]
    signals += deadline_signals(msg.get("endTime", 0), now, "order")
    if len(offer) > 1:
        signals.append(sig("MULTI_ITEM_ORDER", "attention", f"One signature lists {len(offer)} items."))
    return _result("seaport_order", summary, {"offerer": offerer, "offer_items": len(offer),
                   "paid_to_offerer_raw": str(to_offerer)}, signals, digest)


def _unknown_typed(pt, msg, now, head, digest):
    signals = [sig(
        "UNKNOWN_TYPED_DATA", "attention",
        f"'{pt}' is not a type this decoder knows. Read every field before signing.",
    )]
    summary = list(head)
    for k, v in msg.items():
        if isinstance(v, str) and v.lower().startswith("0x") and len(v) == 42 and k.lower() in (
                "spender", "operator", "to", "recipient", "delegate", "receiver", "taker"):
            lab, s = counterparty_signals(norm_addr(v), k)
            signals += s
            summary.append(f"{k}: {norm_addr(v)} ({lab or 'unlabelled'})")
        if isinstance(v, (str, int)) and k.lower() in ("value", "amount"):
            try:
                if fmt_amount(v) == "UNLIMITED":
                    signals.append(sig("UNLIMITED_AMOUNT", "attention", f"Field '{k}' is effectively unlimited."))
            except ValueError:
                pass
    return _result("typed_data_unknown", summary, {"primaryType": pt}, signals, digest)


# ---------------- EIP-3009 ----------------

def decode_3009(a, now):
    to = norm_addr(a.get("to", ""))
    frm = norm_addr(a.get("from", ""))
    tok = norm_addr(a.get("token", "") or "")
    raw_val = int_field(a, "value")
    va, vb = to_int(a.get("validAfter", 0)), to_int(a.get("validBefore", 0))
    pt = a.get("primaryType", "TransferWithAuthorization")
    lab, s = counterparty_signals(to, "recipient")
    signals = list(s)
    signals += deadline_signals(vb, now, "authorization")
    dec = int(a.get("decimals", 6))
    if raw_val is None:
        _absent_amount(signals, "transfer value")
        human_txt = "absent"
        val_txt = "absent"
    else:
        human = raw_val / 10**dec
        human_txt = f"{human:,.6f}"
        val_txt = str(raw_val)
        if human >= 1000:
            signals.append(sig("LARGE_TRANSFER", "attention", f"Moves {human:,.2f} tokens in one signature."))
    signals.append(sig(
        "DIRECT_TRANSFER_SIGNATURE", "info",
        "This signature IS the payment: anyone holding it can submit it once, "
        "no further approval needed.",
    ))
    if pt == "ReceiveWithAuthorization":
        signals.append(sig(
            "RECEIVE_ONLY_BY_RECIPIENT", "info",
            "Only the recipient contract can submit this (front-run protected).",
        ))
    summary = [
        f"EIP-3009 {pt}: sends {human_txt} of token {tok or '?'} ({label(tok) or 'unlabelled'}) "
        f"from {frm} to {to} ({lab or 'unlabelled'}).",
        f"Valid from {fmt_time(va)} until {fmt_time(vb)}. Nonce {a.get('nonce')}.",
    ]
    return _result(
        "eip3009", summary,
        {"from": frm, "to": to, "token": tok, "value": val_txt, "validAfter": va, "validBefore": vb},
        signals,
    )


# ---------------- EIP-7702 ----------------

def decode_7702(auth, now=None):
    auths = auth if isinstance(auth, list) else [auth]
    signals, summary, fields = [], [], []
    for a in auths:
        chain = to_int(a.get("chainId", 0))
        dele = norm_addr(a.get("address", a.get("delegate", "")))
        nonce = to_int(a.get("nonce", 0))
        fields.append({"chainId": chain, "delegate": dele, "nonce": nonce})
        if dele == "0x" + "0" * 40:
            summary.append(f"EIP-7702: CLEARS your account's delegation on chainId {chain} (back to a plain EOA).")
            signals.append(sig("DELEGATION_CLEARED", "info", "Resets delegation to none."))
            continue
        where = "EVERY chain (chainId 0)" if chain == 0 else f"chainId {chain}"
        summary.append(
            f"EIP-7702: your account will run the code at {dele} "
            f"({label(dele) or 'unlabelled'}) on {where}, account nonce {nonce}."
        )
        signals.append(sig(
            "FULL_ACCOUNT_CONTROL", "attention",
            "Delegated code gets full control of the account: it can move every "
            "token and ETH without further signatures.",
        ))
        if chain == 0:
            signals.append(sig(
                "CHAINID_ZERO_ALL_CHAINS", "attention",
                "chainId 0 means this authorization can be replayed on every EVM "
                "chain where your nonce matches.",
            ))
        if dele in BAD_7702:
            signals.append(sig(
                "DELEGATE_ON_PUBLIC_DRAINER_LABEL", "attention",
                f"Delegate carries a public Etherscan label: {BAD_7702[dele]}.",
            ))
        elif dele in GOOD_7702:
            signals.append(sig(
                "DELEGATE_KNOWN_WALLET", "info",
                f"Delegate matches a public wallet delegator label: {GOOD_7702[dele]}.",
            ))
        else:
            signals.append(sig(
                "DELEGATE_UNKNOWN", "attention",
                "Delegate is not a known wallet delegator in our public lists.",
            ))
        if dele in SCAM:
            signals.append(sig(
                "COUNTERPARTY_ON_PUBLIC_SCAM_LIST", "attention",
                "Delegate appears on the public Scam Sniffer address list.",
            ))
    return _result("eip7702_authorization", summary, {"authorizations": fields}, signals)


# ---------------- raw messages ----------------

def decode_message(m, now=None):
    data = m.get("data", "")
    method = m.get("method", "personal_sign")
    signals, summary = [], []
    raw = None
    if isinstance(data, str) and data.startswith("0x"):
        try:
            raw = bytes.fromhex(data[2:])
        except ValueError:
            raw = None
    if method == "eth_sign":
        signals.append(sig("ETH_SIGN_METHOD", "attention",
                           "eth_sign signs raw hashes (it can authorise a transaction). Most wallets removed it."))
    if raw is not None and len(raw) == 32:
        signals.append(sig("BLIND_HASH", "attention",
                           "You are asked to sign an opaque 32-byte hash. You cannot see what it authorises."))
        summary.append(f"Signs the 32-byte value {data} (no readable content).")
    else:
        text = raw.decode("utf-8", "replace") if raw is not None else str(data)
        if raw is not None and "\ufffd" in text:
            signals.append(sig("NON_TEXT_MESSAGE", "attention", "Message is binary, not readable text."))
        summary.append("Message text: " + text[:300].replace("\n", " | "))
        if "wants you to sign in with your Ethereum account" in text:
            dom = text.split(" wants you")[0].strip()
            summary.append(f"Sign-In with Ethereum request from domain '{dom}'. Check it matches the site you are on.")
            signals.append(sig("SIWE_LOGIN", "info", "Login message (SIWE). It does not move funds by itself."))
    length = len(raw) if raw is not None else len(str(data))
    return _result("raw_message", summary, {"method": method, "length": length}, signals)


# ---------------- calldata (used by the Incident Pack) ----------------

SEL = {
    selector("approve(address,uint256)"): "approve",
    selector("transfer(address,uint256)"): "transfer",
    selector("transferFrom(address,address,uint256)"): "transferFrom",
    selector("setApprovalForAll(address,bool)"): "setApprovalForAll",
    selector("increaseAllowance(address,uint256)"): "increaseAllowance",
    selector("increaseApproval(address,uint256)"): "increaseAllowance",  # older OZ/Zeppelin name
    selector("permit(address,address,uint256,uint256,uint8,bytes32,bytes32)"): "permit",
    selector("approve(address,address,uint160,uint48)"): "permit2_approve",
    selector("lockdown((address,address)[])"): "permit2_lockdown",
}


def _word(data, i):
    return data[4 + 32 * i: 4 + 32 * (i + 1)]


def decode_calldata(to, data_hex, value=0):
    to = norm_addr(to)
    d = bytes.fromhex(data_hex[2:] if data_hex.startswith("0x") else data_hex) if data_hex else b""
    if len(d) < 4:
        native = to_int(value) if value not in (None, "") else 0
        kind = "native_transfer" if native else "empty"
        return {"fn": kind, "to": to, "value": str(value), "signals": []}
    fn = SEL.get("0x" + d[:4].hex())
    out = {"fn": fn or ("0x" + d[:4].hex()), "contract": to, "contract_label": label(to), "signals": []}
    try:
        if fn in ("approve", "increaseAllowance"):
            sp = "0x" + _word(d, 0)[12:].hex()
            amt = int.from_bytes(_word(d, 1), "big")
            out.update(spender=sp, amount=fmt_amount(amt))
            out["spender_label"], s = counterparty_signals(sp, "spender")
            out["signals"] += s
            if out["amount"] == "UNLIMITED":
                out["signals"].append(sig("UNLIMITED_AMOUNT", "attention", "Unlimited on-chain approval."))
        elif fn == "transfer":
            out.update(recipient="0x" + _word(d, 0)[12:].hex(), amount=str(int.from_bytes(_word(d, 1), "big")))
        elif fn == "transferFrom":
            out.update(sender="0x" + _word(d, 0)[12:].hex(), recipient="0x" + _word(d, 1)[12:].hex(),
                       amount=str(int.from_bytes(_word(d, 2), "big")))
        elif fn == "setApprovalForAll":
            op = "0x" + _word(d, 0)[12:].hex()
            on = int.from_bytes(_word(d, 1), "big") == 1
            out.update(operator=op, approved=on)
            if on:
                out["operator_label"], s = counterparty_signals(op, "operator")
                out["signals"] += s
                out["signals"].append(sig(
                    "ALL_NFTS_APPROVAL", "attention",
                    "Approves the operator for EVERY NFT in this collection.",
                ))
        elif fn == "permit":
            sp = "0x" + _word(d, 1)[12:].hex()
            out.update(
                owner="0x" + _word(d, 0)[12:].hex(),
                spender=sp,
                amount=fmt_amount(int.from_bytes(_word(d, 2), "big")),
            )
            out["spender_label"], s = counterparty_signals(sp, "spender")
            out["signals"] += s
        elif fn == "permit2_approve":
            sp = "0x" + _word(d, 1)[12:].hex()
            out.update(token="0x" + _word(d, 0)[12:].hex(), spender=sp,
                       amount=fmt_amount(int.from_bytes(_word(d, 2), "big"), MAX_UINT160))
            out["spender_label"], s = counterparty_signals(sp, "spender")
            out["signals"] += s
    except Exception as e:  # noqa: BLE001
        out["decode_error"] = type(e).__name__
    return out
