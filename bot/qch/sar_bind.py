"""The SaR scheme ids, flag bits, and signer timelock, read from SaR.

QCH does not keep a second copy of those numbers. A file that cannot be
read makes the bind unreadable. It does not invent 16, 32, or 7 days.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_CHAINS = _ROOT / "tools" / "sar-oracle" / "runtime" / "chains.js"
_VERIFIER = _ROOT / "tools" / "sar-oracle" / "src" / "SaRVerifier.sol"

_SCHEME = re.compile(
    r"(\d+) ECDSA, (\d+) ML-DSA Solidity, (\d+) WOTS, (\d+) ML-DSA precompile"
)
_UNAVAILABLE = re.compile(r"if \(id == (\d+) \|\| id == (\d+)\) revert SchemeUnavailable")
_TIMELOCK = re.compile(r"uint256 public constant TIMELOCK = (\d+) days")
_PEG = re.compile(r"pegDeviation:\s*(\d+)")
_CANARY = re.compile(r"quantumCanary:\s*(\d+)")


def _text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def scheme_table() -> dict | None:
    """Scheme ids from the SaRVerifier comment, plus the ids that revert."""
    text = _text(_VERIFIER)
    if text is None:
        return None
    found = _SCHEME.search(text)
    refused = _UNAVAILABLE.search(text)
    if found is None or refused is None:
        return None
    ecdsa, mldsa_sol, wots, mldsa_pre = (int(found.group(i)) for i in range(1, 5))
    unavailable = frozenset({int(refused.group(1)), int(refused.group(2))})
    if ecdsa in unavailable or wots in unavailable:
        return None
    if mldsa_sol not in unavailable or mldsa_pre not in unavailable:
        return None
    return {
        "ecdsa": ecdsa,
        "mldsa_sol": mldsa_sol,
        "wots": wots,
        "mldsa_pre": mldsa_pre,
        "unavailable": unavailable,
        "source": "tools/sar-oracle/src/SaRVerifier.sol",
    }


def flag_bits() -> dict | None:
    """SaR packet bits. Peg is 16 and the quantum canary is 32 in that file."""
    text = _text(_CHAINS)
    if text is None:
        return None
    peg = _PEG.search(text)
    canary = _CANARY.search(text)
    if peg is None or canary is None:
        return None
    return {
        "peg": int(peg.group(1)),
        "canary": int(canary.group(1)),
        "source": "tools/sar-oracle/runtime/chains.js",
    }


def timelock_sec() -> int | None:
    """SaRVerifier's signer timelock, in seconds. Unreadable is not 7 days."""
    text = _text(_VERIFIER)
    if text is None:
        return None
    found = _TIMELOCK.search(text)
    if found is None:
        return None
    days = int(found.group(1))
    if days <= 0:
        return None
    return days * 86400
