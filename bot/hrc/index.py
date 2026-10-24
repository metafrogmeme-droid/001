"""The 0x812 borrow-lend reserve word, and what may calibrate the curve.

The layout is the eight uint64 words in Hyperliquid's L1Read.sol, ABI-encoded
as eight 32-byte words (256 bytes). Each word is an integer: basis points or
raw token units. Utilization in the struct is the ratio, floored. The rate
we keep is the curve on the raw totals, and only after the floored quote
sits inside the truncation tolerance.

Testnet bytes may be decoded. They are synthetic, so they are not a
calibration. Calibration requires a mainnet reading whose ``synthetic`` flag
is false.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from bot.hrc.model import (
    borrow_apy,
    quote_matches,
    truncated_utilization_bps,
    utilization_of,
)

# Dict, not a typing alias: a non-dict is refused at runtime, and the
# annotation names the same thing the body checks.
Reading = dict

PRECOMPILE = "0x0000000000000000000000000000000000000812"
WORD_BYTES = 32
WORD_COUNT = 8
# Names follow L1Read.sol. oracle_px is stored and unused: its scale is not
# a rate input. The 1e8 raw scale is what the 23 Oct 2026 mainnet word
# showed against the info API, to the accrual between two reads.
_FIELDS = (
    "borrow_rate_bps",
    "supply_rate_bps",
    "balance",
    "utilization_bps",
    "oracle_px",
    "ltv_bps",
    "supplied",
    "borrowed",
)


class CalibrationRefused(Exception):
    """A reading that must not move the curve. The class is the public text."""

    pass


def decode_precompile(data: bytes) -> Optional[dict]:
    """Eight uint64 words, or None when the return is not that layout.

    A short buffer, a long buffer, and a word that does not fit in uint64
    are the same answer: this is not a precompile sample. None is not a
    sample of zeros.
    """
    if not isinstance(data, (bytes, bytearray)) or len(data) != WORD_BYTES * WORD_COUNT:
        return None
    words = []
    for i in range(WORD_COUNT):
        word = int.from_bytes(data[i * WORD_BYTES:(i + 1) * WORD_BYTES], "big")
        if word >= 2**64:
            return None
        words.append(word)
    return dict(zip(_FIELDS, words))


def accept_sample(decoded: Optional[dict]) -> Optional[dict]:
    """The formula rate for a sample whose quote and utilization word agree.

    The utilization word must be the floor of borrowed/supplied. The rate
    word must sit within the truncation tolerance of the curve on the raw
    ratio. The rate returned is that curve, not the truncated quote.
    """
    if not isinstance(decoded, dict):
        return None
    supplied = decoded.get("supplied")
    borrowed = decoded.get("borrowed")
    rate_bps = decoded.get("borrow_rate_bps")
    util_word = decoded.get("utilization_bps")
    if type(supplied) is not int or type(borrowed) is not int:
        return None
    if type(rate_bps) is not int or type(util_word) is not int:
        return None
    floored = truncated_utilization_bps(supplied, borrowed)
    if floored is None or floored != util_word:
        return None
    util = utilization_of(supplied, borrowed)
    if not quote_matches(util, rate_bps):
        return None
    rate = borrow_apy(util)
    if rate is None:
        return None
    return {
        "supplied": supplied,
        "borrowed": borrowed,
        "utilization_bps": floored,
        "rate_bps": rate_bps,
        "formula_rate": rate,
        "oracle_px": decoded.get("oracle_px"),
        "unit": "raw",
    }


def calibrate(reading: Reading) -> dict:
    """The mainnet formula rate. Synthetic and non-mainnet readings refuse.

    ``reading`` is a decoded-and-accepted sample plus ``network`` and
    ``synthetic``. A missing flag is not a claim that the data is real.
    """
    if not isinstance(reading, dict):
        raise CalibrationRefused()
    if reading.get("synthetic") is not False:
        raise CalibrationRefused()
    if reading.get("network") != "mainnet":
        raise CalibrationRefused()
    rate = reading.get("formula_rate")
    if not isinstance(rate, Decimal):
        raise CalibrationRefused()
    return {
        "network": "mainnet",
        "synthetic": False,
        "formula_rate": rate,
        "utilization_bps": reading.get("utilization_bps"),
    }

