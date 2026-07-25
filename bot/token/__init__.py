"""$RCLAW token utilities for RUNECLAW.

Currently exposes the token-tier gate (:mod:`bot.token.tier_gate`) — a DRAFT,
feature-flagged-OFF mechanism that maps an on-chain $RCLAW stake to an access
tier for premium features. See ``docs/TOKEN_ROADMAP.md`` for the full design.

The whole package is inert unless ``TOKEN_TIER_GATE_ENABLED`` is set AND a mint
is configured — importing it has no side effects.
"""

from . import tier_gate  # noqa: F401

__all__ = ["tier_gate"]
