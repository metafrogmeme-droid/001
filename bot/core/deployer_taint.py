"""The last two facts `assess_deployer` reads, and the reason they are lists.

    funded_by_mixer        weight 2.0
    reused_rug_bytecode    HARD — a match alone makes the deployer known_bad

Neither can be computed. Both are lookups against curated reference data, which
is why they stayed unread while everything else got a source: the mechanism is
small and the *list* is the work.

THE RULE THAT GOVERNS BOTH: AN EMPTY LIST ANSWERS `None`, NEVER `False`.

This is the whole file. `funded_by_mixer: False` is scored as a PASSING check —
it adds to the evidence that lets a deployer be called clean. Returning it from
a list with nothing in it would mean "we checked and found no mixer" when the
truth is "we checked nothing", and that is CLAUDE.md's opening sentence wearing
a denylist. So a list that is empty, missing, malformed, or undated produces
`unknown`, and the dossier reports the gap instead of certifying past it.

The same rule points the other way for the hard check. A bytecode match makes
somebody `known_bad` on its own, so a corpus entry has to be worth that: exact,
attributable, and DISTINCTIVE.

    THE TRAP THAT WOULD DESTROY THIS CHECK

    A plain OpenZeppelin ERC-20 compiles to near-identical bytecode for
    thousands of honest tokens and for every scammer who never edited the
    template. Putting a generic template's hash in the corpus would mark all of
    them `known_bad` — the single most damaging output this codebase can
    produce, applied indiscriminately.

    Nothing in code can look at a hash and tell you whether it is distinctive.
    So the loader demands that a human said so, per entry, in writing: an
    `example` contract that used it and a `note` saying why it is not a generic
    template. An entry without both is refused at load, loudly. The bar is
    deliberately higher than for mixers, because the consequence is.

WHAT IS SHIPPED

The lists in `config/` are EMPTY, and that is a deliberate delivery, not an
unfinished one. Populating them means naming specific addresses as sanctioned
mixers and specific bytecode as rug templates — accusations that have to come
from an authoritative source with a retrieval date, not from anybody's memory.
`load_list` reads whatever is dropped in; until something is, both checks
honestly answer `unknown` and nothing about the current output changes.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_DIR = os.path.join(_HERE, "config")

MIXER_LIST = os.path.join(CONFIG_DIR, "mixer_addresses.json")
RUG_BYTECODE_LIST = os.path.join(CONFIG_DIR, "rug_bytecode.json")

_ADDR_RE = re.compile(r"^0x[0-9a-f]{40}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def load_list(path: str, kind: str) -> dict:
    """A curated list, or an empty one — never a partially-trusted one.

    Returns ``{entries: {key: entry}, source, retrieved, rejected: [...]}``.
    ``entries`` empty means every caller must answer `unknown`.

    An unreadable or malformed file is an EMPTY list rather than an exception:
    a missing reference file must not take the whole dossier down, and an empty
    list already fails safe by answering `unknown` everywhere.
    """
    out: dict = {"entries": {}, "source": None, "retrieved": None, "rejected": []}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        return out
    except Exception as exc:                                      # noqa: BLE001
        logger.warning("taint list %s unreadable: %s", path, exc)
        return out
    if not isinstance(raw, dict):
        return out

    # Provenance is mandatory. A denylist that accuses people, with no record of
    # where it came from or when, cannot be audited by the person it accuses.
    source = raw.get("source")
    retrieved = raw.get("retrieved")
    entries = raw.get("entries")
    if not isinstance(entries, list) or not entries:
        return out
    if not source or not retrieved:
        logger.warning("taint list %s has entries but no source/retrieved — "
                       "refusing to trust it", path)
        return out

    out["source"], out["retrieved"] = source, retrieved
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        key, why = _validate(entry, kind)
        if key is None:
            out["rejected"].append(why)
            logger.warning("taint list %s rejected an entry: %s", path, why)
            continue
        out["entries"][key] = entry
    return out


def _validate(entry: dict, kind: str) -> tuple:
    """`(key, None)` for an entry worth trusting, else `(None, reason)`."""
    if kind == "mixer":
        addr = str(entry.get("address") or "").strip().lower()
        if not _ADDR_RE.match(addr):
            return None, f"not an address: {entry.get('address')!r}"
        if not entry.get("source"):
            return None, f"{addr} has no per-entry source"
        return addr, None

    if kind == "bytecode":
        h = str(entry.get("hash") or "").strip().lower()
        if not _HASH_RE.match(h):
            return None, f"not a sha256 hex digest: {entry.get('hash')!r}"
        # The two fields that make a HARD verdict auditable. See the trap in
        # this module's docstring: nothing in code can tell a rug template from
        # a generic one, so a human has to have said which, on the record.
        if not entry.get("example"):
            return None, f"{h[:12]}… has no example contract"
        if not entry.get("note"):
            return None, f"{h[:12]}… has no note saying why it is distinctive"
        return h, None

    return None, f"unknown list kind {kind!r}"


# ── bytecode normalisation ────────────────────────────────────────────────

def strip_metadata(runtime_hex: str) -> Optional[str]:
    """Runtime bytecode with solc's trailing CBOR metadata removed.

    The same source compiled twice — different path, different solc patch —
    produces identical logic and a different metadata blob, so hashing the raw
    code would miss every match that matters. The last two bytes encode the
    metadata length, which is how every verification service does this.

    None when the input is not usable hex; an unreadable code blob is not an
    empty one, and `''` would hash to a real digest that could collide with a
    corpus entry for "no code".
    """
    if not isinstance(runtime_hex, str):
        return None
    code = runtime_hex.strip().lower()
    if code.startswith("0x"):
        code = code[2:]
    if not code or len(code) % 2 or not re.fullmatch(r"[0-9a-f]+", code):
        return None
    if len(code) >= 4:
        declared = int(code[-4:], 16) * 2          # bytes → hex chars
        # +4 for the length suffix itself. A declared length that does not fit
        # means this contract has no metadata blob (vyper, hand-written asm),
        # so the code is already normalised.
        if 0 < declared + 4 <= len(code):
            code = code[: len(code) - declared - 4]
    return code or None


def bytecode_hash(runtime_hex: str) -> Optional[str]:
    """sha256 of the normalised runtime bytecode, or None if unreadable."""
    norm = strip_metadata(runtime_hex)
    if norm is None:
        return None
    return hashlib.sha256(norm.encode("ascii")).hexdigest()


# ── the two checks ────────────────────────────────────────────────────────

def check_funding(funding_sources: Any, mixers: Optional[dict] = None) -> dict:
    """`{value, matched, list_size}` — `value` is True / False / None.

    None whenever the answer would be unsupported: no list, or no funding
    history to check against it. `False` is only ever "we had both, and none of
    the counterparties is listed".
    """
    lst = mixers if mixers is not None else load_list(MIXER_LIST, "mixer")
    size = len(lst.get("entries") or {})
    if not size:
        return {"value": None, "matched": None, "list_size": 0}
    if not funding_sources:
        # An empty funding history is a read we did not get, not a wallet that
        # was never funded — every deployer was funded by something.
        return {"value": None, "matched": None, "list_size": size}
    for addr in funding_sources:
        key = str(addr or "").strip().lower()
        if key in lst["entries"]:
            return {"value": True, "matched": key, "list_size": size}
    return {"value": False, "matched": None, "list_size": size}


def check_bytecode(code_hash: Optional[str], corpus: Optional[dict] = None) -> dict:
    """`{value, matched, list_size}` for the HARD check.

    Same shape, same rule, higher stakes: a True here is a `known_bad` verdict
    on its own, and a False is a claim that we compared against a corpus that
    was actually there.
    """
    lst = corpus if corpus is not None else load_list(RUG_BYTECODE_LIST, "bytecode")
    size = len(lst.get("entries") or {})
    if not size:
        return {"value": None, "matched": None, "list_size": 0}
    key = str(code_hash or "").strip().lower()
    if not _HASH_RE.match(key):
        return {"value": None, "matched": None, "list_size": size}
    if key in lst["entries"]:
        return {"value": True, "matched": key, "list_size": size}
    return {"value": False, "matched": None, "list_size": size}


def taint_facts(funding_sources: Any, code_hash: Optional[str],
                mixers: Optional[dict] = None,
                corpus: Optional[dict] = None) -> dict:
    """The two facts, omitted rather than guessed.

    Keys are ABSENT when unknown — `assess_deployer` reads a missing key as
    `unknown` and a present `None` the same way, but absent keeps the feature
    map honest about what was actually supplied.
    """
    out: dict = {}
    fund = check_funding(funding_sources, mixers)
    if fund["value"] is not None:
        out["funded_by_mixer"] = fund["value"]
    code = check_bytecode(code_hash, corpus)
    if code["value"] is not None:
        out["reused_rug_bytecode"] = code["value"]
    return out
