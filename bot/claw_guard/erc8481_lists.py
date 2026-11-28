"""ERC-8481 list documents and the ``address.on-list`` reading.

A list is a JSON document a consumer pins by hash. This module normalizes its accounts, hashes it, decides
whether it conforms, and matches an address against it. It reads nothing from the network: the caller hands
in the parsed document, or ``None`` when the fetch failed.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping, Optional

from bot.claw_guard.erc8481_jcs import digest, jcs
from bot.hrc.keccak import keccak256

KIND_ON_LIST = "erc.presign.address.on-list"
LIST_VERSION = "erc-presign-list/1"

_CAIP10 = re.compile(r"([-a-z0-9]{3,8}):([-_a-zA-Z0-9]{1,32}|\*):([-.%a-zA-Z0-9]{1,128})")
_EVM = re.compile(r"0x[0-9a-fA-F]{40}")
_LEADING_ZERO = re.compile(r"0[0-9]+")


def eip55(address: str) -> str:
    """The EIP-55 checksum form of a 0x address."""
    plain = address.lower()[2:]
    hashed = keccak256(plain.encode("ascii")).hex()
    return "0x" + "".join(c.upper() if int(hashed[i], 16) >= 8 else c for i, c in enumerate(plain))


# ---------------------------------------------------------------- bech32 (BIP-173, BIP-350) and base58
_B32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_B32M = 0x2BC830A3
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _polymod(values: list[int]) -> int:
    generator = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
    chk = 1
    for value in values:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ value
        for i in range(5):
            if (top >> i) & 1:
                chk ^= generator[i]
    return chk


def _regroup(data: list[int], source: int, target: int) -> Optional[list[int]]:
    acc, bits, out, mask = 0, 0, [], (1 << target) - 1
    for value in data:
        acc = (acc << source) | value
        bits += source
        while bits >= target:
            bits -= target
            out.append((acc >> bits) & mask)
    if bits >= source or (acc << (target - bits)) & mask:
        return None  # padding that is too long or not zero
    return out


def _segwit_valid(address: str) -> bool:
    if address != address.lower() and address != address.upper():
        return False
    text = address.lower()
    split = text.rfind("1")
    if split < 1 or split + 7 > len(text) or len(text) > 90:
        return False
    hrp, tail = text[:split], text[split + 1 :]
    if any(c not in _B32 for c in tail):
        return False
    data = [_B32.index(c) for c in tail]
    const = _polymod([ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp] + data)
    if const not in (1, _B32M):
        return False
    version, program = data[0], _regroup(data[1:-6], 5, 8)
    if program is None or version > 16 or not 2 <= len(program) <= 40:
        return False
    if version == 0:
        return const == 1 and len(program) in (20, 32)
    return const == _B32M


def _b58decode(text: str) -> Optional[bytes]:
    if not text or any(c not in _B58 for c in text):
        return None
    number = 0
    for c in text:
        number = number * 58 + _B58.index(c)
    body = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    return b"\x00" * (len(text) - len(text.lstrip("1"))) + body


def _b58check_valid(text: str) -> bool:
    raw = _b58decode(text)
    if raw is None or len(raw) < 5:
        return False
    return hashlib.sha256(hashlib.sha256(raw[:-4]).digest()).digest()[:4] == raw[-4:]


def bip122_encoding(address: str) -> str:
    """Inside ``bip122`` an address is bech32 when it is a valid BIP-173 or BIP-350 address, else base58."""
    return "bech32" if _segwit_valid(address) else "base58"


# ---------------------------------------------------------------- accounts
def normalize_account(account: Any, allow_wildcard: bool = True) -> dict[str, Any]:
    """``{"ok": True, "account", "encoding"}`` or ``{"ok": False, "error"}``.

    EVM and bech32 addresses go to lowercase; base58 and the namespaces this module has no rule for are kept
    as written. A mixed-case EVM address must be a valid EIP-55 checksum.
    """
    if not isinstance(account, str):
        return {"ok": False, "error": "not a string"}
    match = _CAIP10.fullmatch(account)
    if not match:
        return {"ok": False, "error": "not CAIP-10"}
    namespace, reference, address = match.groups()
    if reference == "*" and not allow_wildcard:
        return {"ok": False, "error": "wildcard chain reference outside a list entry"}
    if namespace == "eip155":
        if _LEADING_ZERO.fullmatch(reference):
            return {"ok": False, "error": "eip155 chain reference with a leading zero"}
        if not _EVM.fullmatch(address):
            return {"ok": False, "error": "eip155 address is not 0x and 40 hex digits"}
        body = address[2:]
        if body not in (body.lower(), body.upper()) and eip55(address) != address:
            return {"ok": False, "error": "mixed-case EVM address that is not a valid EIP-55 checksum"}
        return {"ok": True, "account": f"{namespace}:{reference}:{address.lower()}", "encoding": "hex"}
    if namespace == "bip122":
        if bip122_encoding(address) == "bech32":
            return {"ok": True, "account": f"{namespace}:{reference}:{address.lower()}", "encoding": "bech32"}
        if address not in (address.lower(), address.upper()) and _segwit_valid(address.lower()):
            return {"ok": False, "error": "mixed-case bech32"}
        if _b58check_valid(address):
            return {"ok": True, "account": account, "encoding": "base58"}
        return {"ok": False, "error": "neither bech32 nor valid base58check"}
    if namespace == "solana":
        raw = _b58decode(address)
        if raw is None or len(raw) != 32:
            return {"ok": False, "error": "solana address is not base58 of 32 bytes"}
        return {"ok": True, "account": account, "encoding": "base58"}
    return {"ok": True, "account": account, "encoding": "as-written"}


# ---------------------------------------------------------------- documents
def canonical_list(document: Any) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """The normalized form (accounts normalized, entries sorted and merged), or ``(None, why)``."""
    if not isinstance(document, dict) or not isinstance(document.get("entries", []), list):
        return None, "not a list document"
    merged: dict[str, dict[str, Any]] = {}
    for entry in document.get("entries", []):
        if not isinstance(entry, dict):
            return None, "entry is not an object"
        normal = normalize_account(entry.get("account"))
        if not normal["ok"]:
            return None, f"entry account: {normal['error']}"
        item = dict(entry)
        item["account"] = normal["account"]
        previous = merged.get(item["account"])
        if previous is not None and jcs(previous) != jcs(item):
            return None, "two entries for one account differ"
        merged[item["account"]] = item
    out = dict(document)
    out["entries"] = [merged[key] for key in sorted(merged)]  # code point order
    return out, None


def list_hash(document: Any) -> tuple[Optional[str], Optional[str]]:
    """``(hash, None)`` over the normalized form, or ``(None, why)``."""
    canonical, error = canonical_list(document)
    if canonical is None:
        return None, error
    return digest(canonical), None


def conforms(document: Any) -> tuple[bool, Optional[str]]:
    if not isinstance(document, dict):
        return False, "not a list document"
    if document.get("version") != LIST_VERSION:
        return False, f"version is not {LIST_VERSION}"
    for member in ("source", "criteria"):
        if not isinstance(document.get(member), str):
            return False, f"{member} is missing"
    entries = document.get("entries", [])
    if "attestation" not in document and isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("evidence"):
                return False, "an entry has no evidence and the list has no attestation"
    canonical, error = canonical_list(document)
    return (canonical is not None), error


def _matching(document: Any, address: str) -> list[dict[str, Any]]:
    canonical, error = canonical_list(document)
    if canonical is None:
        raise ValueError(error)
    normal = normalize_account(address, allow_wildcard=False)
    if not normal["ok"]:
        raise ValueError(normal["error"])
    namespace, reference, body = normal["account"].split(":")
    found = []
    for entry in canonical["entries"]:
        e_namespace, e_reference, e_body = entry["account"].split(":")
        if e_namespace == namespace and e_reference in (reference, "*") and e_body == body:
            found.append(entry)
    return found


def match_value(document: Any, address: str) -> Optional[dict[str, Any]]:
    """The entry that names the address (the exact chain reference wins over ``*``), or ``None``."""
    found = _matching(document, address)
    if not found:
        return None
    exact = [e for e in found if e["account"].split(":")[1] != "*"]
    return (exact or found)[0]


def read_on_list(
    address: str, param: Mapping[str, Any], sources: Mapping[str, Optional[dict[str, Any]]]
) -> dict[str, Any]:
    """The ``status`` and ``value`` or ``reason`` of an ``address.on-list`` finding.

    ``sources`` maps a list uri to its parsed document, or to ``None`` when the fetch failed. A uri that is not
    in ``sources`` is a list this source does not read: ``unsupported``. A document that does not hash to the
    pinned ``hash``, or does not conform, is ``unavailable``, never a negative.
    """
    uri = param.get("uri")
    if not isinstance(uri, str) or uri not in sources:
        return {"status": "not-read", "reason": "unsupported"}
    document = sources[uri]
    if document is None:
        return {"status": "not-read", "reason": "unavailable"}
    pinned, _ = list_hash(document)
    if pinned is None or pinned != param.get("hash") or not conforms(document)[0]:
        return {"status": "not-read", "reason": "unavailable"}
    try:
        return {"status": "read", "value": match_value(document, address)}
    except ValueError:
        return {"status": "not-read", "reason": "unavailable"}
