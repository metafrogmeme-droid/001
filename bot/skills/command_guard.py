"""`@guard("<permission>")` — the command auth gate as a decorator, in a leaf.

Moved out of bot/skills/telegram_handler.py alongside the second slice of the
handler split, so a command group that lives in a mixin can carry the same
decorator the handler's own commands do. Without this the first guarded
command to leave the file would have had to gate inline, which is a second
pattern for the tests that DERIVE the permission tables to learn to read —
and those tests read the decorator by name, from every file in the handler's
MRO (`tests/source_scan.handler_sources()`).

The decorator is a shape, not a policy. It calls `self._guard(update,
permission, ctx)` before the body and returns early on refusal; `_guard`
itself — allowlist, registration, role permission, rate limit — stays on the
handler, which is why this module imports nothing.
"""
from __future__ import annotations

import functools


def guard(command: str = ""):
    """Decorator for command handlers: run the auth / rate-limit / role-permission
    gate (``self._guard``) before the body, returning early if it fails.

    Replaces the copy-pasted ``if not await self._guard(update, "..."): return``
    prelude. Equivalent in every way — the gate still runs first and still
    short-circuits — but the permission string now lives in one visible place per
    command instead of two boilerplate lines inside each body. Handlers that must
    run logic BEFORE the gate (e.g. a ``update.message`` null-check) keep the
    inline call instead.
    """
    def _decorate(func):
        @functools.wraps(func)
        async def _wrapped(self, update, ctx, *args, **kwargs):
            # ctx is forwarded so a refusal can reach the operator: a person the
            # allowlist turns away otherwise gets a dead end, and nobody learns
            # they showed up.
            if not await self._guard(update, command, ctx):
                return
            return await func(self, update, ctx, *args, **kwargs)
        return _wrapped
    return _decorate
