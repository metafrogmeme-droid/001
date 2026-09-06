"""
Boot-health helpers — make an env-wipe LOUD and keep the Telegram poller ALIVE.

Two failure modes bit the platform and both were silent:

  1. A redeploy that wiped .env brought the process up MISSING its secrets. The
     bot exited on the first missing var it happened to check, hiding the rest —
     so recovery was a guessing game. ``env_preflight`` names EVERY missing
     critical/important var at once, in one loud line.

  2. The engine has a supervise-and-restart loop, but the Telegram updater did
     not: if polling stalled (a 409 getUpdates conflict from two instances
     overlapping on a redeploy, a network blip, a transient Telegram error) the
     bot went silent and never recovered until a full restart.
     ``poller_should_restart`` is the pure predicate the watchdog ticks on.

  3. ``main.py`` has a complete graceful shutdown — stop the monitor, clean up
     the dashboard runner, stop the updater and the application, then
     ``engine.stop()`` — in a ``finally`` that NOTHING COULD REACH under
     systemd. No SIGTERM handler was installed anywhere in the tree, and
     Python's default action for SIGTERM terminates the process outright: no
     exception, so no ``finally``. ``systemctl restart`` sends SIGTERM (the
     unit sets no ``KillSignal``), so on every production restart the whole
     path was skipped.

     That is also where failure mode 2 comes from. An old process that never
     calls ``updater.stop()`` leaves its long poll open at Telegram, so the
     next instance collides — the 409 the watchdog was built to recover from
     is a symptom of this, not an independent fault.

     ``install_stop_handlers`` arms the signals and REPORTS which it armed;
     ``engine_should_restart`` keeps the supervise loop from fighting an
     intentional stop, exactly as ``poller_should_restart`` does for polling.

Everything here is a PURE function of its inputs (no os.environ, no telegram
imports) so the boot path stays unit-testable and the wiring in main.py is a
thin shell.
"""

from __future__ import annotations

from typing import Iterable, Mapping

# Without this the bot cannot start at all — Telegram never connects.
CRITICAL_ENV: tuple[str, ...] = ("TELEGRAM_BOT_TOKEN",)

# The bot still trades without these, but a whole surface silently breaks.
# The effect is stated per variable and PRINTED beside the name, because the
# bare name invited the wrong conclusion: "WEB_CREDS_KEY missing" — described
# here, until 2026-09-06, as "decrypting stored per-user exchange keys" — was
# read as "the exchange keys users linked are sitting unencrypted". They are
# not. Every key linked with /connect or the website is Fernet-encrypted under
# the MASTER key (RUNECLAW_SECRETS_KEY / data/.exchange_secret.key), a different
# secret. WEB_CREDS_KEY only opens the AES-GCM envelope the WEBSITE wraps a
# submission in for the bot to pull; without it the site refuses the form, so
# nothing is queued and nothing is left in the clear either.
IMPORTANT_ENV_EFFECT: dict[str, str] = {
    "BOT_SYNC_SECRET": "the website's dashboard sync is rejected",
    "WEB_GATEWAY_SECRET": "web chat and web trade answer 503",
    "WEB_CREDS_KEY": ("the website's exchange-key connect form is off; keys already "
                      "linked stay encrypted under the master key"),
    "DASHBOARD_TOKEN": "the aggregate /api/* dashboard gate is fail-closed (403)",
}
IMPORTANT_ENV: tuple[str, ...] = tuple(IMPORTANT_ENV_EFFECT)


def missing_env(names: Iterable[str], env: Mapping[str, str]) -> list[str]:
    """Names from ``names`` that are absent or blank in ``env`` (order-preserving)."""
    return [n for n in names if not str(env.get(n, "")).strip()]


def env_preflight(env: Mapping[str, str]) -> dict[str, list[str]]:
    """Classify the environment once, loudly. Returns
    ``{"critical": [...], "important": [...]}`` — the missing names in each tier.

    Pure: the caller decides whether a missing critical var is fatal (telegram
    mode) or merely logged (other modes), and does the logging."""
    return {
        "critical": missing_env(CRITICAL_ENV, env),
        "important": missing_env(IMPORTANT_ENV, env),
    }


def format_preflight(report: Mapping[str, list[str]]) -> str:
    """One human line summarizing a preflight report, for a boot log."""
    crit = report.get("critical") or []
    imp = report.get("important") or []
    if not crit and not imp:
        return "env preflight: all critical and important secrets present."
    parts = []
    if crit:
        parts.append("MISSING CRITICAL (bot cannot run): " + ", ".join(crit))
    if imp:
        # Name AND effect: the name alone was misread once (see IMPORTANT_ENV).
        parts.append("missing important (a web surface will be degraded): "
                     + "; ".join(f"{n} — {IMPORTANT_ENV_EFFECT.get(n, 'a web surface breaks')}"
                                 for n in imp))
    return "env preflight — " + " | ".join(parts)


def poller_should_restart(running: bool, stopping: bool) -> bool:
    """True when the Telegram updater is NOT running yet we are not shutting
    down — i.e. polling stalled and the watchdog must revive it. Never restart
    during an intentional shutdown (that would fight ``updater.stop()``)."""
    return (not running) and (not stopping)


#: Signals a supervisor uses to ask for a graceful stop. SIGTERM is what
#: systemd, Docker and a bare ``kill`` send; SIGINT is Ctrl-C. Both are armed,
#: and SIGINT deliberately so: relying on KeyboardInterrupt propagating out of
#: ``loop.run_until_complete`` leaves the main task pending and unfinalised, so
#: the ``finally`` does not reliably run there either.
STOP_SIGNAL_NAMES: tuple[str, ...] = ("SIGTERM", "SIGINT")


def install_stop_handlers(loop, on_stop, *, signal_module=None,
                          names: Iterable[str] = STOP_SIGNAL_NAMES) -> list[str]:
    """Arm ``on_stop(name)`` for each stop signal. Returns the names ARMED.

    The return value is the point. A shutdown path that is silently not armed
    is the same defect one level up from the one this fixes — the caller must
    be able to say "graceful stop is armed for SIGTERM, SIGINT" or, just as
    loudly, that it is armed for nothing. An empty list is a real answer and
    never an error.

    ``loop.add_signal_handler`` is Unix-only and raises ``NotImplementedError``
    on Windows and ``RuntimeError`` off the main thread; each signal is armed
    independently so one refusal cannot cost the others.
    """
    import signal as _signal
    sig_mod = signal_module or _signal
    armed: list[str] = []
    for name in names:
        signum = getattr(sig_mod, name, None)
        if signum is None:
            continue  # not a signal on this platform — nothing to arm
        try:
            loop.add_signal_handler(signum, on_stop, name)
        except (NotImplementedError, RuntimeError, ValueError, OSError):
            continue
        armed.append(name)
    return armed


def format_stop_handlers(armed: Iterable[str]) -> str:
    """One line an operator can read. Never claims a stop that is not armed."""
    armed = list(armed)
    if not armed:
        return ("graceful stop: NOT ARMED for any signal — a supervisor stop "
                "will kill this process without running shutdown")
    return "graceful stop armed for: " + ", ".join(armed)


def engine_should_restart(stopping: bool) -> bool:
    """True when the engine task ended and we are NOT shutting down.

    The supervise loop treats a clean engine exit as "shouldn't happen,
    restart". During an intentional stop that is exactly wrong: it would spawn
    a fresh engine while the shutdown path is tearing the old one down. Same
    shape, and same reason, as ``poller_should_restart``.
    """
    return not stopping
