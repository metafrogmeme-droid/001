"""Telegram text for a heuristic finding.

Medium and above only. The sentences name what was read. They do not
name a verdict. Dynamic text is escaped for Telegram HTML.
"""

from __future__ import annotations

DISCLAIMER = "Public on-chain signal only. This is not a verdict."
_REVOKE_NOTE = (
    "If this was not you, an unsigned revoke is below for you to sign "
    "in your own wallet. This service does not sign it."
)

_SENTENCES = {
    "PERMIT_SUBMITTED_BY_THIRD_PARTY": (
        "A permit or approval for this address was submitted by a different sender."
    ),
    "UNLIMITED_AMOUNT": "The amount on that permission is the maximum the token records.",
    "THIRD_PARTY_PULL": "Tokens left this address in a transaction this address did not send.",
    "PERMIT_AND_PULL_SAME_TX": "The permission and a pull of tokens are in the same transaction.",
    "MULTI_ASSET_SWEEP": "More than one asset left in that pull.",
    "SPLIT_PAYOUT_PATTERN": (
        "The pull paid exactly two recipients, and the smaller share is between 10 and 35 percent."
    ),
    "APPROVAL_TO_EOA": "This address approved an address that has no contract code.",
    "APPROVAL_FOR_ALL_TO_UNKNOWN": (
        "This address set an operator on a collection, and that operator is not on the router list."
    ),
    "VANITY_ZERO_PATTERN": "One address in the transaction starts with 0000 and ends with 0000.",
    "DELEGATION_TO_LISTED_SWEEPER": "This address delegated to a delegate on the published sweeper list.",
    "DELEGATION_TO_UNKNOWN_CONTRACT": (
        "This address delegated to a delegate that is not on the known-delegate list."
    ),
    "ZERO_VALUE_PULL": (
        "A zero-value transfer was recorded from this address in a transaction this address did not send."
    ),
    "LOOKALIKE_INBOUND": (
        "An inbound transfer came from an address that shares the first four and last four "
        "characters with one this address has paid."
    ),
    "LOOKALIKE_OF_PAST_RECIPIENT": (
        "The counterparty shares the first four and last four characters with an address this wallet has paid."
    ),
    "SPOOFED_TRANSFER_SPRAY": "One transaction recorded transfers from many addresses that did not send it.",
    "OUTBOUND_NOT_SENT_BY_YOU": (
        "A transfer is recorded from this address in a transaction this address did not send."
    ),
    "DUST_FROM_LOOKALIKE": (
        "A very small inbound transfer came from an address that resembles one this wallet has paid."
    ),
    "POISONED_RECIPIENT_SEND": (
        "This address sent value to a counterparty whose first contact was a zero-value or dust transfer "
        "it did not send."
    ),
    "NATIVE_ETH_INTERNAL_PULL": (
        "An internal native-coin transfer left this address toward an address that is not on the quiet list."
    ),
}

ALERT_CAP = 5


def alerts_of(findings: object) -> list[dict]:
    """Medium and high findings, in the order they were scored."""
    if not isinstance(findings, list):
        return []
    out = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        strength = finding.get("signal_strength")
        if strength == "medium" or strength == "high":
            out.append(finding)
    return out


def _esc(value: object) -> str:
    text = str(value)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_alert(finding: dict) -> str:
    """One HTML message. Rule ids stay so a reader can audit the score."""
    chain = finding.get("chain_id")
    lines = ["<b>Claw Watch heuristic</b>", _esc(f"chain {chain}")]
    tx_hash = finding.get("tx_hash")
    if isinstance(tx_hash, str) and tx_hash:
        lines.append(_esc(tx_hash))
    score = finding.get("score")
    strength = finding.get("signal_strength")
    lines.append(_esc(f"signal {strength} score {score}"))
    signals_raw = finding.get("signals")
    signals = signals_raw if isinstance(signals_raw, list) else []
    for sig in signals:
        if not isinstance(sig, dict):
            continue
        rule_raw = sig.get("rule")
        rule = rule_raw if isinstance(rule_raw, str) else ""
        sentence = _SENTENCES.get(rule, "A public on-chain signal was recorded.")
        weight = sig.get("weight")
        lines.append(_esc(f"{rule} ({weight}): {sentence}"))
    revokes_raw = finding.get("revokes")
    revokes = revokes_raw if isinstance(revokes_raw, list) else []
    if revokes:
        lines.append(_REVOKE_NOTE)
        for rev in revokes:
            if not isinstance(rev, dict):
                continue
            kind = rev.get("kind")
            if kind == "authorization":
                lines.append("unsigned 7702 clear: delegate 0x" + "0" * 40 + ", signature empty")
                continue
            to = rev.get("to")
            data_raw = rev.get("data")
            data = data_raw if isinstance(data_raw, str) else ""
            lines.append(_esc(f"unsigned tx to {to} data {data[:10]}"))
    lines.append(DISCLAIMER)
    text = "\n".join(lines)
    if len(text) > 3500:
        text = text[:3400] + "\n…\n" + DISCLAIMER
    return text
