"""Universal signature decoder. Public signals, not a verdict.

The lists are bundled. Importing this package does not sign and does not
open a network connection.
"""
from __future__ import annotations

from bot.sigdecoder.core import (
    ASSISTED,
    DISCLAIMER,
    MAX_INPUT_CHARS,
    InputTooLarge,
    decode,
    decode_text,
    render_text,
)
from bot.sigdecoder.evm import decode_calldata
from bot.sigdecoder.flag import FLAG, flag_on

__all__ = [
    "ASSISTED",
    "DISCLAIMER",
    "FLAG",
    "MAX_INPUT_CHARS",
    "InputTooLarge",
    "decode",
    "decode_calldata",
    "decode_text",
    "flag_on",
    "render_text",
]
