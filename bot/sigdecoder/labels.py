"""Public label lists bundled with this package.

The lists are a snapshot of public address labels. They are read from this
package only. There is no live fetch and no crawler path: a missing file
is unreadable, and unreadable is not "not on the list".
"""
from __future__ import annotations

import json
import os

from .signals import sig

_HERE = os.path.dirname(__file__)
_DATA = os.path.join(_HERE, "data")

LISTS_READ = False
LISTS_ERROR: str | None = None

ALLOW: dict[str, str] = {}
GOOD_7702: dict[str, str] = {}
BAD_7702: dict[str, str] = {}
SCAM: set[str] = set()

# Extra well-known public addresses (fee recipients / tokens) used only for labelling.
EXTRA = {
    "0x0000a26b00c1f0df003000390027140000faa719": "OpenSea fee recipient",
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": "USDC (Ethereum)",
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": "USDC (Base)",
    "0xdac17f958d2ee523a2206206994597c13d831ec7": "USDT (Ethereum)",
    "0x6b175474e89094c44da98b954eedeac495271d0f": "DAI (Ethereum)",
    "0x514910771af9ca656af840dff83e8264ecf986ca": "LINK (Ethereum)",
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": "WETH (Ethereum)",
}


def _load(name: str):
    path = os.path.join(_DATA, name)
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _install() -> None:
    global LISTS_READ, LISTS_ERROR, ALLOW, GOOD_7702, BAD_7702, SCAM
    try:
        lists = _load("lists.json")
        sniffed = _load("scamsniffer_address.json")
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        LISTS_READ = False
        LISTS_ERROR = type(exc).__name__
        ALLOW, GOOD_7702, BAD_7702, SCAM = {}, {}, {}, set()
        return
    if not isinstance(lists, dict) or not isinstance(sniffed, list):
        LISTS_READ = False
        LISTS_ERROR = "LabelShape"
        ALLOW, GOOD_7702, BAD_7702, SCAM = {}, {}, {}, set()
        return
    allow = lists.get("allow_spenders_and_routers") or {}
    good = lists.get("known_good_7702_delegates") or {}
    bad = lists.get("known_bad_7702_delegates") or {}
    if not isinstance(allow, dict) or not isinstance(good, dict) or not isinstance(bad, dict):
        LISTS_READ = False
        LISTS_ERROR = "LabelShape"
        ALLOW, GOOD_7702, BAD_7702, SCAM = {}, {}, {}, set()
        return
    ALLOW = {str(k).lower(): str(v) for k, v in allow.items()}
    GOOD_7702 = {str(k).lower(): str(v) for k, v in good.items()}
    BAD_7702 = {str(k).lower(): str(v) for k, v in bad.items()}
    SCAM = {str(a).lower() for a in sniffed if isinstance(a, str)}
    LISTS_READ = True
    LISTS_ERROR = None


_install()


def label(addr: str):
    if not LISTS_READ:
        return None
    key = (addr or "").lower()
    if key in ALLOW:
        return ALLOW[key]
    if key in GOOD_7702:
        return GOOD_7702[key]
    if key in BAD_7702:
        return BAD_7702[key]
    if key in EXTRA:
        return EXTRA[key]
    return None


def counterparty_signals(addr: str, role: str):
    """Return (label, [signals]) for a spender, recipient, or operator."""
    key = (addr or "").lower()
    if not LISTS_READ:
        return None, [sig(
            "LISTS_UNREADABLE",
            "attention",
            "The bundled public label lists could not be read, so this address was not compared to them.",
        )]
    out = []
    lab = label(key)
    if key in SCAM:
        out.append(sig(
            "COUNTERPARTY_ON_PUBLIC_SCAM_LIST",
            "attention",
            f"The {role} {key} appears on the public Scam Sniffer address list.",
        ))
    if key in BAD_7702:
        out.append(sig(
            "COUNTERPARTY_ON_PUBLIC_DRAINER_LABEL",
            "attention",
            f"The {role} {key} carries a public Etherscan label: {BAD_7702[key]}.",
        ))
    if key in ALLOW:
        out.append(sig(
            "COUNTERPARTY_ON_ALLOWLIST",
            "info",
            f"The {role} matches a public allow-list entry: {ALLOW[key]}. "
            "A match is a public signal, not a verdict.",
        ))
    elif key == "0x" + "0" * 40:
        pass
    elif not lab:
        out.append(sig(
            "COUNTERPARTY_NOT_ON_ALLOWLIST",
            "attention",
            f"The {role} {key} is not on the bundled public list of known protocols.",
        ))
    return lab, out
