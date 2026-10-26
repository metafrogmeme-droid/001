"""Solana transaction decoder (base64 wire format, legacy + v0).

Signals: System Assign (account owner change), SPL SetAuthority, Approve,
durable nonce, AuthorizeNonceAccount, CloseAccount, transfers. No RPC.
Address-lookup-table contents are flagged and not resolved.
"""
from __future__ import annotations

import base64
import struct

from .signals import sig
from .util import b58encode

SYSTEM = "11111111111111111111111111111111"
TOKEN = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN22 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
COMPUTE = "ComputeBudget111111111111111111111111111111"
KNOWN_PROGRAMS = {SYSTEM: "System Program", TOKEN: "SPL Token", TOKEN22: "SPL Token-2022",
                  COMPUTE: "Compute Budget", "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL": "Associated Token Account",
                  "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4": "Jupiter v6",
                  "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr": "Memo"}
AUTH_TYPES = {0: "MintTokens", 1: "FreezeAccount", 2: "AccountOwner", 3: "CloseAccount"}


def _cu16(b, i):
    val, shift = 0, 0
    while True:
        x = b[i]
        i += 1
        val |= (x & 0x7F) << shift
        if not x & 0x80:
            return val, i
        shift += 7


def parse_tx(b64: str):
    b = base64.b64decode(b64)
    n, i = _cu16(b, 0)
    i += 64 * n
    version = "legacy"
    if b[i] & 0x80:
        version = f"v{b[i] & 0x7F}"
        i += 1
    nsig = b[i]
    i += 3
    nk, i = _cu16(b, i)
    keys = [b58encode(b[i + 32 * k: i + 32 * (k + 1)]) for k in range(nk)]
    i += 32 * nk
    blockhash = b58encode(b[i:i + 32])
    i += 32
    ni, i = _cu16(b, i)
    ixs = []
    for _ in range(ni):
        pid = b[i]
        i += 1
        na, i = _cu16(b, i)
        accs = list(b[i:i + na])
        i += na
        dl, i = _cu16(b, i)
        data = b[i:i + dl]
        i += dl
        ixs.append({"program_index": pid, "accounts": accs, "data": data})
    lookups = 0
    if version != "legacy" and i < len(b):
        lookups, i = _cu16(b, i)
    return {"version": version, "num_signers": nsig, "keys": keys, "blockhash": blockhash,
            "instructions": ixs, "address_table_lookups": lookups}


def _k(keys, idx):
    return keys[idx] if idx < len(keys) else f"<lookup #{idx}>"


def decode_solana(b64: str, now=None):
    tx = parse_tx(b64)
    keys = tx["keys"]
    signers = keys[: tx["num_signers"]]
    signals, summary, ixout = [], [], []
    n_ix = len(tx["instructions"])
    summary.append(
        f"Solana {tx['version']} transaction, fee payer {keys[0]}, "
        f"{len(signers)} signer(s), {n_ix} instruction(s)."
    )
    if tx["address_table_lookups"]:
        n_tables = tx["address_table_lookups"]
        signals.append(sig(
            "SOL_ADDRESS_LOOKUP_TABLES", "info",
            f"Uses {n_tables} address lookup table(s); "
            "some accounts cannot be resolved offline.",
        ))
    for n, ix in enumerate(tx["instructions"]):
        prog = _k(keys, ix["program_index"])
        acc = [_k(keys, a) for a in ix["accounts"]]
        d = ix["data"]
        desc = {"program": prog, "program_label": KNOWN_PROGRAMS.get(prog), "accounts": acc}
        if prog == SYSTEM and len(d) >= 4:
            code = struct.unpack_from("<I", d)[0]
            if code == 1:
                owner = b58encode(d[4:36])
                desc["ix"] = "Assign"
                summary.append(f"#{n} System Assign: changes the OWNER PROGRAM of account {acc[0]} to {owner}.")
                if owner != SYSTEM:
                    signals.append(sig(
                        "SOL_ACCOUNT_OWNER_REASSIGN", "attention",
                        f"Account {acc[0]} would be handed to program {owner}. "
                        "Owner changes are irreversible; wallet simulations may show "
                        "no balance change (Reviewer, Dec 2025).",
                    ))
            elif code == 2:
                lam = struct.unpack_from("<Q", d, 4)[0]
                desc["ix"] = "Transfer"
                summary.append(f"#{n} SOL transfer: {lam/1e9:.9f} SOL from {acc[0]} to {acc[1]}.")
            elif code == 4:
                desc["ix"] = "AdvanceNonceAccount"
                authority = acc[2] if len(acc) > 2 else "?"
                summary.append(
                    f"#{n} Advance durable nonce account {acc[0]} (authority {authority})."
                )
                if n == 0:
                    signals.append(sig(
                        "SOL_DURABLE_NONCE", "attention",
                        "Uses a durable nonce: this signed transaction does not expire "
                        "after ~1 minute and can be submitted days or weeks later "
                        "(the Drift Apr 2026 mechanism).",
                    ))
            elif code == 7:
                newauth = b58encode(d[4:36])
                desc["ix"] = "AuthorizeNonceAccount"
                summary.append(f"#{n} Hands control of nonce account {acc[0]} to {newauth}.")
                signals.append(sig(
                    "SOL_NONCE_AUTHORITY_CHANGE", "attention",
                    "Changes who controls a durable nonce account.",
                ))
            elif code == 6:
                desc["ix"] = "InitializeNonceAccount"
                summary.append(f"#{n} Creates a durable nonce account {acc[0]} with authority {b58encode(d[4:36])}.")
                signals.append(sig(
                    "SOL_NONCE_ACCOUNT_CREATED", "info",
                    "Creates a durable nonce account (used to pre-sign transactions for later).",
                ))
            elif code == 10:
                desc["ix"] = "AssignWithSeed"
                signals.append(sig(
                    "SOL_ACCOUNT_OWNER_REASSIGN", "attention",
                    "AssignWithSeed changes an account's owner program.",
                ))
            else:
                desc["ix"] = f"system#{code}"
        elif prog in (TOKEN, TOKEN22) and d:
            code = d[0]
            if code == 6:
                at = AUTH_TYPES.get(d[1], str(d[1]))
                has = d[2] == 1
                new = b58encode(d[3:35]) if has else None
                desc["ix"] = f"SetAuthority({at})"
                summary.append(f"#{n} Token SetAuthority {at} on {acc[0]} -> {new or 'none'}.")
                if at in ("AccountOwner", "CloseAccount") and new and new not in signers:
                    signals.append(sig(
                        "SOL_TOKEN_ACCOUNT_OWNER_CHANGE", "attention",
                        f"Token account {acc[0]} {at} moves to {new}, which is not a "
                        "signer of this transaction. You would lose control of the tokens in it.",
                    ))
            elif code in (4, 13):
                amt = struct.unpack_from("<Q", d, 1)[0]
                dele = acc[1] if code == 4 else acc[2]
                desc["ix"] = "Approve" if code == 4 else "ApproveChecked"
                summary.append(f"#{n} Token Approve: delegate {dele} may move {amt} base units from {acc[0]}.")
                unlimited = " Amount is effectively unlimited." if amt >= 2**63 else ""
                signals.append(sig(
                    "SOL_TOKEN_DELEGATE", "attention" if amt >= 2**63 else "info",
                    "Grants a token delegate (revocable with Revoke)." + unlimited,
                ))
            elif code in (3, 12):
                amt = struct.unpack_from("<Q", d, 1)[0]
                dst = acc[1] if code == 3 else acc[2]
                desc["ix"] = "Transfer" if code == 3 else "TransferChecked"
                summary.append(f"#{n} Token transfer of {amt} base units from {acc[0]} to {dst}.")
            elif code == 9:
                desc["ix"] = "CloseAccount"
                summary.append(f"#{n} Closes token account {acc[0]}, rent goes to {acc[1]}.")
                if acc[1] not in signers:
                    signals.append(sig(
                        "SOL_CLOSE_TO_OTHER", "info",
                        "Rent from the closed account goes to an address that is not a signer.",
                    ))
            else:
                desc["ix"] = f"token#{code}"
        elif prog == COMPUTE:
            desc["ix"] = "ComputeBudget"
        else:
            desc["ix"] = "unknown"
            if prog not in KNOWN_PROGRAMS:
                signals.append(sig(
                    "SOL_UNKNOWN_PROGRAM", "info",
                    f"Calls program {prog}, which is not in our small known-program list.",
                ))
        ixout.append(desc)
    # de-duplicate identical signals
    seen, uniq = set(), []
    for s in signals:
        key = (s["code"], s["text"])
        if key not in seen:
            seen.add(key)
            uniq.append(s)
    return {"kind": "solana_transaction", "summary": summary,
            "fields": {"version": tx["version"], "signers": signers, "fee_payer": keys[0], "instructions": ixout,
                       "recent_blockhash_or_nonce": tx["blockhash"]}, "signals": uniq}
