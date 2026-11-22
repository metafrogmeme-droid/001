"""The AudaceFi publisher's switches and its fixed destination.

Read from the environment here rather than as `bot/config.py` fields, so
adding them moves none of the line citations `.env.example`'s generated block
carries. The flag defaults OFF, which is why it is not a safety flag and is
not in that block.

The gateway's address is a CONSTANT, not a setting: the loopback host and
port the dry-run gateway listens on. Nothing here can point the publisher at
another machine.
"""

from __future__ import annotations

from bot.config import _env_bool, _env_float_bounded

GATEWAY_HOST = "127.0.0.1"
GATEWAY_PORT = 8787
GATEWAY_PATH = "/signals"
#: The only mode this package has. Sent as a header so the gateway can refuse
#: anything else.
MODE = "dry_run"

#: A signal lives at most this long. The setting may shorten it, never lengthen it.
MAX_SIGNAL_TTL_MS = 60_000
#: Wire-size cap on one encoded signal.
MAX_SIGNAL_BYTES = 512
#: Full allocation, in basis points of whatever the gateway allocates.
FULL_ALLOCATION_BPS = 10_000


def audacefi_dry_run_on() -> bool:
    """``AUDACEFI_DRY_RUN_ENABLED`` -- default off."""
    return _env_bool("AUDACEFI_DRY_RUN_ENABLED", False)


def audacefi_signal_ttl_ms() -> int:
    """``AUDACEFI_SIGNAL_TTL_S`` in ms (default 60 s), clamped to [1 s, 60 s]."""
    seconds = _env_float_bounded("AUDACEFI_SIGNAL_TTL_S", 60.0, 1.0,
                                 MAX_SIGNAL_TTL_MS / 1000.0)
    return min(int(seconds * 1000), MAX_SIGNAL_TTL_MS)


def audacefi_long_target_bps() -> int:
    """``AUDACEFI_LONG_TARGET_BPS`` (default 10000), clamped to [0, 10000]."""
    return int(_env_float_bounded("AUDACEFI_LONG_TARGET_BPS", float(FULL_ALLOCATION_BPS),
                                  0.0, float(FULL_ALLOCATION_BPS)))
