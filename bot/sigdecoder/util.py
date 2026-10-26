"""Small helpers: keccak, base58, EIP-712 hashing.

Keccak is the one already in ``bot.hrc.keccak``. A second implementation
would be a second digest. Nothing here opens a socket.
"""
from __future__ import annotations

import re
from typing import Optional

from bot.hrc.keccak import keccak256

MAX_UINT256 = 2**256 - 1
MAX_UINT160 = 2**160 - 1
MAX_UINT48 = 2**48 - 1

__all__ = [
    "MAX_UINT160",
    "MAX_UINT256",
    "MAX_UINT48",
    "b58encode",
    "int_field",
    "keccak256",
    "norm_addr",
    "selector",
    "to_int",
    "typed_data_digest",
]


def selector(signature: str) -> str:
    return "0x" + keccak256(signature.encode()).hex()[:8]


_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58encode(raw: bytes) -> str:
    n = int.from_bytes(raw, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    pad = len(raw) - len(raw.lstrip(b"\0"))
    return "1" * pad + (out or "")


def to_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        return int(text, 16) if text.lower().startswith("0x") else int(text)
    raise ValueError("not an int")


def int_field(obj: object, key: str) -> Optional[int]:
    """The integer stored under ``key``, or None when the field is absent.

    Absent is not zero. A present ``0`` is a measured zero and comes back as 0.
    """
    if not isinstance(obj, dict) or key not in obj:
        return None
    raw = obj[key]
    if raw is None or raw == "":
        return None
    return to_int(raw)


def norm_addr(addr: object) -> str:
    if not isinstance(addr, str) or not re.fullmatch(r"0x[0-9a-fA-F]{40}", addr.strip()):
        return str(addr)
    return addr.strip().lower()


def _deps(primary: str, types: dict, found: Optional[set] = None) -> set:
    found = found if found is not None else set()
    if primary in found or primary not in types:
        return found
    found.add(primary)
    for field in types[primary]:
        base = field["type"].split("[")[0]
        _deps(base, types, found)
    return found


def encode_type(primary: str, types: dict) -> str:
    deps = _deps(primary, types)
    deps.discard(primary)
    out = ""
    for name in [primary] + sorted(deps):
        out += f"{name}(" + ",".join(f"{field['type']} {field['name']}" for field in types[name]) + ")"
    return out


def type_hash(primary: str, types: dict) -> bytes:
    return keccak256(encode_type(primary, types).encode())


def _enc_value(typ: str, val: object, types: dict) -> bytes:
    if typ in types:
        if not isinstance(val, dict):
            raise ValueError("struct value is not an object")
        return hash_struct(typ, val, types)
    if typ.endswith("]"):
        base = typ[: typ.rindex("[")]
        if not isinstance(val, list):
            raise ValueError("array value is not a list")
        return keccak256(b"".join(_enc_value(base, item, types) for item in val))
    if typ == "string":
        if not isinstance(val, str):
            raise ValueError("string value is not text")
        return keccak256(val.encode())
    if typ == "bytes":
        if not isinstance(val, str):
            raise ValueError("bytes value is not hex text")
        return keccak256(bytes.fromhex(val[2:] if val.startswith("0x") else val))
    if typ == "address":
        if not isinstance(val, str):
            raise ValueError("address value is not text")
        return int(val, 16).to_bytes(32, "big")
    if typ == "bool":
        return (1 if val in (True, "true", 1, "1") else 0).to_bytes(32, "big")
    if typ.startswith("bytes"):
        if not isinstance(val, str):
            raise ValueError("fixed bytes value is not hex text")
        raw = bytes.fromhex(val[2:] if val.startswith("0x") else val)
        return raw.ljust(32, b"\0")
    if typ.startswith("uint"):
        return to_int(val).to_bytes(32, "big")
    if typ.startswith("int"):
        return (to_int(val) % 2**256).to_bytes(32, "big")
    raise ValueError("unsupported type")


def hash_struct(primary: str, data: dict, types: dict) -> bytes:
    enc = type_hash(primary, types)
    for field in types[primary]:
        enc += _enc_value(field["type"], data.get(field["name"]), types)
    return keccak256(enc)


def typed_data_digest(td: dict) -> str:
    types = dict(td["types"])
    if "EIP712Domain" not in types:
        order = [
            ("name", "string"),
            ("version", "string"),
            ("chainId", "uint256"),
            ("verifyingContract", "address"),
            ("salt", "bytes32"),
        ]
        domain = td.get("domain", {})
        types["EIP712Domain"] = [{"name": name, "type": typ} for name, typ in order if name in domain]
    domain_hash = hash_struct("EIP712Domain", td.get("domain", {}), types)
    message = hash_struct(td["primaryType"], td["message"], types)
    return "0x" + keccak256(b"\x19\x01" + domain_hash + message).hex()
