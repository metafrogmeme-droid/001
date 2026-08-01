"""
RUNECLAW -- AI Trading Command Core
Entry point for both Telegram bot mode and CLI testing mode.

Usage:
    python -m bot.main --mode telegram   # Start Telegram bot
    python -m bot.main --mode cli        # Interactive CLI for testing
    python -m bot.main --mode scan       # One-shot market scan
    python -m bot.main --mode backtest   # Run backtest with synthetic data
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys

from bot.config import CONFIG
from bot.core.engine import RuneClawEngine
from bot.skills.skill_registry import build_default_registry
from bot.skills.telegram_handler import TelegramHandler
from bot.utils.logger import audit, system_log

_PID_FILE = os.path.join(os.environ.get("RUNECLAW_STATE_DIR", "data"), "runeclaw.pid")


def _banner() -> str:
    bitget_env = ("DEMO trading (BITGET_SANDBOX=true)"
                  if CONFIG.exchange.sandbox else "PRODUCTION")
    banner = (
        "\n"
        "  ╔══════════════════════════════════════╗\n"
        "  ║   RUNECLAW  --  AI Trading Core      ║\n"
        "  ║   Bitget AI Base Camp · S1           ║\n"
        "  ╚══════════════════════════════════════╝\n"
        f"  Mode: {'SIMULATION' if CONFIG.simulation_mode else 'LIVE'}\n"
        f"  Live Trading: {'ENABLED' if CONFIG.live_trading_enabled else 'DISABLED'}\n"
        f"  Bitget environment: {bitget_env}\n"
        f"  Paper Balance: ${CONFIG.paper_balance_usd:,.2f}\n"
    )
    if CONFIG.exchange.sandbox and CONFIG.is_live():
        banner += (
            "\n"
            "  ⚠️  WARNING: LIVE trading is enabled with BITGET_SANDBOX=true.\n"
            "  All orders go to Bitget DEMO trading — live production keys\n"
            "  will fail (40006 Invalid ACCESS_KEY / 40099 wrong environment)\n"
            "  and NO real orders or stop-losses will be placed. If this bot\n"
            "  should trade real money, set BITGET_SANDBOX=false and restart.\n"
        )
    return banner



#: Substrings that mean THE VENUE REJECTED OUR CREDENTIALS, as opposed to
#: "the venue could not be reached". Bitget codes plus the generic words every
#: exchange uses for a signature/key problem.
_AUTH_REJECTION_MARKERS = (
    "40006", "40012", "40099", "40009", "40037",
    "access_key", "accesskey", "apikey", "api key", "api_key",
    "passphrase", "signature", "unauthorized", "authentication failed",
    "invalid key", "permission denied", "forbidden",
)


def _is_auth_rejection(err: str) -> bool:
    """True when the venue rejected the CREDENTIALS, not merely the request.

    On 2026-08-01 a live bot reported "STARTUP: exchange auth FAILED — open
    positions cannot be protected" and halted new entries. The venue string
    was:

        bitget GET .../api/v3/market/instruments?category=USDC-FUTURES

    A PUBLIC market-data endpoint, for a product category this bot does not
    trade. `fetch_balance()` is authenticated, but ccxt calls load_markets()
    first, so a transient failure fetching instruments propagated out and was
    classified as a credential rejection. The operator's own /start a minute
    later read equity $388.33 and two open positions -- both authenticated
    calls, both fine.

    The cost of conflating them is asymmetric and runs BOTH ways, which is why
    this is a classifier and not a loosened guard:

      calling a network blip an auth failure  -> entries halted while the
          account is healthy, for as long as it takes someone to notice;
      calling an auth failure a network blip  -> a live position with no stop
          and nobody told.

    So: an explicit auth marker halts. Anything else is treated as
    reachability. AMBIGUITY STILL HALTS -- see the caller, which only takes
    the non-halting path when it can positively identify a transport failure.
    """
    low = (err or "").lower()
    return any(m in low for m in _AUTH_REJECTION_MARKERS)


#: Substrings that positively identify a TRANSPORT/reachability failure. Kept
#: as an allowlist rather than "not an auth marker", so an unrecognised error
#: still fails closed and halts.
_TRANSPORT_MARKERS = (
    "timeout", "timed out", "connection", "econnreset", "econnrefused",
    "network", "temporarily unavailable", "service unavailable",
    "bad gateway", "gateway timeout", "dns", "ssl", "certificate",
    "/market/instruments", "load_markets", "loadmarkets",
    "502", "503", "504", "429", "too many requests",
)


def _is_transport_failure(err: str) -> bool:
    """True when the error names a reachability problem and NOT a credential.

    Positive identification only. An error this cannot place is not treated as
    transport -- it keeps the old fail-closed behaviour.
    """
    low = (err or "").lower()
    if _is_auth_rejection(low):
        return False
    return any(m in low for m in _TRANSPORT_MARKERS)


async def _credential_preflight(engine, bot) -> None:
    """Authenticate against the venue at boot and alert loudly on failure.

    Live trading needs valid exchange credentials to place the stops that
    protect open positions. On 2026-07-14 a redeploy wiped .env; the keys
    were re-entered with quotes/whitespace (or the wrong sandbox flag), so
    auth failed 40006 and a live AMD position sat unprotected — the failure
    only surfaced when a stop couldn't be placed, minutes later. This runs
    an authenticated balance fetch at startup and, on failure, sends the
    exact actionable diagnosis to the admin chat. Never raises; never
    blocks startup (a broken-cred bot must still monitor open positions)."""
    try:
        if CONFIG.simulation_mode or not CONFIG.live_trading_enabled:
            return  # paper/sim: no venue auth to check
        if not (CONFIG.exchange.api_key and CONFIG.exchange.api_secret):
            _msg = ("\U0001f6a8 <b>STARTUP: no exchange API key configured</b>\n"
                    "Live trading is enabled but BITGET_API_KEY / "
                    "BITGET_API_SECRET are empty — the bot cannot place or "
                    "protect live orders. Check your .env.")
            engine.set_live_auth_status(False, "no API key configured")
        else:
            # Call fetch_balance DIRECTLY — get_live_equity swallows the venue
            # error and returns None, so its exception (and this function's
            # whole 40006 diagnosis) was previously unreachable (audit HIGH).
            # fetch_balance returns {"error": "..."} on auth failure, which we
            # classify here.
            try:
                bal = await engine.live_executor.fetch_balance()
            except Exception as _auth_exc:
                bal = {"error": str(_auth_exc)}
            _err = bal.get("error") if isinstance(bal, dict) else None
            if not _err and isinstance(bal, dict) and float(bal.get("total", 0) or 0) > 0:
                audit(system_log,
                      "Credential preflight OK — venue authenticated",
                      action="cred_preflight", result="OK")
                engine.set_live_auth_status(True)
                return
            if not _err:
                _msg = ("\U0001f6a8 <b>STARTUP: venue returned an empty "
                        "balance</b>\nAuthenticated but equity is 0 — check "
                        "the account is funded and the sandbox flag matches "
                        "the key.")
                # Auth SUCCEEDED (equity just 0) — a funding issue, not an auth
                # failure, so do NOT halt entries here; sizing/risk handle zero
                # equity on their own.
                engine.set_live_auth_status(True)
            else:
                _e = str(_err)
                _hint = ""
                if "40006" in _e or "ACCESS_KEY" in _e.upper():
                    _hint = ("\n<b>40006 = venue rejects the API key.</b> Check, "
                             "in order:\n"
                             "1. No quotes/spaces around the key in .env "
                             "(BITGET_API_KEY=abc, not \"abc\").\n"
                             f"2. BITGET_SANDBOX is {CONFIG.exchange.sandbox} — "
                             "a LIVE key needs it <code>false</code>; a demo "
                             "key needs it <code>true</code>.\n"
                             "3. The key wasn't regenerated/deleted on Bitget.")
                elif "40099" in _e or "environment" in _e.lower():
                    _hint = ("\n<b>40099 = wrong environment.</b> The key type "
                             "and BITGET_SANDBOX disagree — a production key "
                             f"needs BITGET_SANDBOX=false (currently "
                             f"{CONFIG.exchange.sandbox}).")
                elif "passphrase" in _e.lower() or "40012" in _e:
                    _hint = ("\n<b>Passphrase mismatch.</b> BITGET_PASSPHRASE "
                             "must match the one set when the key was created.")
                if _is_transport_failure(_e):
                    # The venue could not be REACHED. That says nothing about
                    # the credentials, and halting entries on it is a false
                    # negative that costs trades while the account is healthy.
                    #
                    # fetch_balance() is authenticated, but ccxt calls
                    # load_markets() first -- so a failure fetching the PUBLIC
                    # instruments endpoint lands in the same except clause as a
                    # rejected key. On 2026-08-01 that reported "auth FAILED"
                    # and halted entries while /start read equity and two open
                    # positions a minute later.
                    #
                    # Entries are NOT halted here. The venue-auth probe re-runs,
                    # and every order path still has its own fail-closed checks.
                    _msg = (f"\u26a0\ufe0f <b>STARTUP: could not reach the "
                            f"venue</b>\nThis is a transport failure, not a "
                            f"credential rejection \u2014 the key was never "
                            f"judged. New entries are NOT halted; open "
                            f"positions keep being monitored.\n"
                            f"Venue said: <code>{_e[:160]}</code>\n"
                            f"<i>If this repeats, check connectivity to the "
                            f"venue before touching the API key.</i>")
                    audit(system_log,
                          f"Credential preflight: transport failure, not auth "
                          f"({_e[:120]})",
                          action="cred_preflight", result="UNREACHABLE")
                else:
                    _msg = (f"\U0001f6a8 <b>STARTUP: exchange auth FAILED</b>\n"
                            f"Live trading is on but the venue rejected "
                            f"authentication — open positions cannot be "
                            f"protected until this is fixed.\n"
                            f"Venue said: <code>{_e[:160]}</code>{_hint}")
                    # Mark operator auth DOWN → the pre-execute gate halts new
                    # live entries (open positions stay monitored) until a
                    # restart with fixed credentials re-runs this preflight OK.
                    #
                    # Reached for an explicit auth marker AND for anything this
                    # cannot classify: an unrecognised error still fails closed,
                    # because an unprotected live position costs more than a
                    # missed entry.
                    engine.set_live_auth_status(False, _e[:120])
        audit(system_log, f"Credential preflight FAILED: {_msg[:200]}",
              action="cred_preflight", result="FAIL", level=logging.CRITICAL)
        _admin = CONFIG.telegram.chat_id or ""
        _ids = [i.strip() for i in
                (CONFIG.telegram.admin_ids or "").split(",") if i.strip()]
        for _target in ([_admin] if _admin else []) + _ids:
            try:
                await bot.send_message(chat_id=_target, text=_msg,
                                       parse_mode="HTML")
            except Exception:
                continue
    except Exception as exc:
        try:
            audit(system_log, f"Credential preflight error: {exc}",
                  action="cred_preflight", result="ERROR")
        except Exception:
            pass


async def _per_user_credential_preflight(engine, bot) -> None:
    """Probe every LINKED per-user account's venue auth at boot and alert on
    failure (live-readiness audit C3).

    A user whose Bitget key was revoked/regenerated otherwise surfaces only when
    their first stop fails to place — the naked-position failure mode the
    operator preflight was built to prevent. This marks each failing account's
    auth DOWN so the pre-execute gate halts new entries on it (open positions
    stay monitored). No-op unless per-user live trading is enabled. Never
    raises; never blocks startup."""
    try:
        if (CONFIG.simulation_mode or not CONFIG.live_trading_enabled
                or not getattr(CONFIG, "per_user_live_enabled", False)):
            return
        from bot.core.exchange_credentials import get_credential_store
        ids = get_credential_store().user_ids()
        if not ids:
            return
        failures = []
        unreachable: list = []
        for uid in ids:
            try:
                ex = engine._executor_for(uid)
            except Exception:
                ex = None
            if ex is None or ex is engine.live_executor:
                continue  # no usable keys / fell back to operator — skip
            try:
                bal = await ex.fetch_balance()
            except Exception as _exc:
                bal = {"error": str(_exc)}
            _err = bal.get("error") if isinstance(bal, dict) else None
            if _err:
                # SAME CLASSIFICATION AS THE OPERATOR PATH ABOVE. This branch
                # had none, and it is the worse place to lack it: it halts a
                # USER's entries and tells them to re-<code>/connect</code> --
                # to re-enter their API credentials -- which does nothing for a
                # transport failure and asks them to handle a secret for no
                # reason.
                #
                # The operator path was fixed first and this was left behind
                # for an hour, in the same file. Fixing a surface is not
                # finishing a defect; the question is always which OTHER
                # surface makes the same claim.
                if _is_transport_failure(str(_err)):
                    engine.set_live_auth_status(True, user_id=uid)
                    unreachable.append((uid, str(_err)[:100]))
                else:
                    engine.set_live_auth_status(False, str(_err)[:120],
                                                user_id=uid)
                    failures.append((uid, str(_err)[:100]))
            else:
                engine.set_live_auth_status(True, user_id=uid)
        if unreachable:
            # Reported, not silenced -- a repeated blip is worth knowing about
            # -- but nobody is told to touch a key and no entries are halted.
            _u = "\n".join(f"• <code>{u}</code>: {e}" for u, e in unreachable)
            audit(system_log,
                  f"Per-user preflight: {len(unreachable)} account(s) "
                  f"unreachable (transport, not auth)",
                  action="cred_preflight_users", result="UNREACHABLE")
            _note = ("\u26a0\ufe0f <b>STARTUP: %d linked account(s) could not "
                     "be reached</b>\nTransport failure, not a credential "
                     "rejection \u2014 their keys were never judged and their "
                     "entries are NOT halted:\n%s"
                     % (len(unreachable), _u))
            _admin0 = CONFIG.telegram.chat_id or ""
            _ids0 = [i.strip() for i in
                     (CONFIG.telegram.admin_ids or "").split(",") if i.strip()]
            for _t in ([_admin0] if _admin0 else []) + _ids0:
                try:
                    await bot.send_message(chat_id=_t, text=_note,
                                           parse_mode="HTML")
                except Exception:
                    continue
        if failures:
            _lines = "\n".join(f"• <code>{u}</code>: {e}" for u, e in failures)
            _msg = ("\U0001f6a8 <b>STARTUP: %d linked account(s) failed venue "
                    "auth</b>\nNew live entries on these accounts are halted "
                    "until they re-<code>/connect</code>:\n%s"
                    % (len(failures), _lines))
            _admin = CONFIG.telegram.chat_id or ""
            _ids = [i.strip() for i in
                    (CONFIG.telegram.admin_ids or "").split(",") if i.strip()]
            for _target in ([_admin] if _admin else []) + _ids:
                try:
                    await bot.send_message(chat_id=_target, text=_msg,
                                           parse_mode="HTML")
                except Exception:
                    continue
    except Exception as exc:
        try:
            audit(system_log, f"Per-user credential preflight error: {exc}",
                  action="cred_preflight_users", result="ERROR")
        except Exception:
            pass


def run_telegram() -> None:
    """Start the Telegram bot with the trading engine."""
    # ── PID-file guard: prevent duplicate instances ──
    os.makedirs(os.path.dirname(_PID_FILE) or ".", exist_ok=True)
    if os.path.exists(_PID_FILE):
        try:
            old_pid = int(open(_PID_FILE).read().strip())
            # Check if the old process is still running
            os.kill(old_pid, 0)  # signal 0 = just check, don't kill
            # Process exists — kill it before starting fresh
            print(f"  Killing stale bot instance (PID {old_pid})...")
            os.kill(old_pid, signal.SIGTERM)
            import time
            time.sleep(3)
            # Force kill if still alive
            try:
                os.kill(old_pid, 0)
                os.kill(old_pid, signal.SIGKILL)
                time.sleep(1)
            except ProcessLookupError:
                pass
        except (ProcessLookupError, ValueError):
            pass  # Process already dead, stale PID file
        except PermissionError:
            print("  WARNING: Cannot kill existing process. May cause Telegram conflicts.")

    # Write our PID
    with open(_PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    def _cleanup_pid():
        try:
            if os.path.exists(_PID_FILE):
                stored = int(open(_PID_FILE).read().strip())
                if stored == os.getpid():
                    os.unlink(_PID_FILE)
        except Exception:
            pass

    import atexit
    atexit.register(_cleanup_pid)

    engine = RuneClawEngine()
    handler = TelegramHandler(engine)
    app = handler.build_app()

    audit(system_log, "Starting Telegram bot", action="startup", result="TELEGRAM")
    audit(system_log,
          "Bitget environment: "
          + ("DEMO trading (BITGET_SANDBOX=true)"
             if CONFIG.exchange.sandbox else "PRODUCTION"),
          action="startup", result="ENV")
    print(_banner())
    print("  Telegram bot is running. Press Ctrl+C to stop.\n")

    # Start the background engine loop alongside the Telegram polling
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def _run_all() -> None:
        engine_task = asyncio.create_task(engine.run())

        # Start live dashboard web server
        dashboard_runner = None
        try:
            from aiohttp import web as _web
            from bot.web.dashboard_server import create_app as _create_dash
            # Pass the Telegram handler so the web user gateway (/gateway/* —
            # website chat + manual trades) mounts alongside the dashboard.
            dashboard_app = _create_dash(engine, tg_handler=handler)
            dashboard_runner = _web.AppRunner(dashboard_app)
            await dashboard_runner.setup()
            # RC-AUD-017: the dashboard /api/* surface exposes AGGREGATE
            # multi-user state, so the bind host is configurable. Default is
            # "0.0.0.0" because the production deployment runs the bot in Docker
            # behind an nginx proxy in a SEPARATE container that reaches the
            # dashboard over the docker network — a 127.0.0.1 default would break
            # that. The real protection is the mandatory DASHBOARD_TOKEN gate on
            # /api/* (fail-closed: 403 when the token is unset). Operators who run
            # the bot directly on a host can set DASHBOARD_BIND_HOST=127.0.0.1 to
            # restrict the dashboard to localhost.
            _dash_host = os.environ.get("DASHBOARD_BIND_HOST", "0.0.0.0")
            _site = _web.TCPSite(dashboard_runner, _dash_host, 8080)
            await _site.start()
            print(f"  Live Dashboard: http://{_dash_host}:8080\n")
        except Exception as exc:
            print(f"  Dashboard server skipped: {exc}\n")

        try:
            await app.initialize()
            await app.start()
            await app.updater.start_polling(drop_pending_updates=True)
            # Start proactive alert monitor (Move 2)
            await handler.start_monitor(app.bot)
            # Credential preflight: authenticate ONCE at boot so a bad key
            # (Bitget 40006 — the 2026-07-14 env-wipe incident) screams here
            # instead of silently surfacing only when a stop fails to place.
            # Fail-open: never blocks startup — a live position still needs
            # monitoring even with broken creds — but it MUST alert loudly.
            await _credential_preflight(engine, app.bot)
            # Per-user auth sweep (C3): probe each LINKED account so a revoked or
            # regenerated user key surfaces here at boot, not when their first
            # protective stop fails to place. No-op unless per-user live is on.
            await _per_user_credential_preflight(engine, app.bot)

            # Poller-supervision watchdog. The engine has a restart loop (below)
            # but the Telegram updater did NOT — if polling stalled (a 409
            # getUpdates conflict from two instances overlapping on a redeploy, a
            # network blip, a transient Telegram error) the bot went silent and
            # never recovered until a full restart. This ticks the updater's
            # health and revives polling if it has stopped while we are NOT
            # shutting down. Fully fail-open; never raises into the boot path.
            poller_state = {"stopping": False}

            async def _poller_watchdog() -> None:
                from bot.core.boot_health import poller_should_restart
                interval = int(os.environ.get("POLLER_WATCHDOG_SEC", "30") or 30)
                while not poller_state["stopping"]:
                    await asyncio.sleep(interval)
                    try:
                        running = bool(getattr(app.updater, "running", False))
                        if poller_should_restart(running, poller_state["stopping"]):
                            audit(system_log,
                                  "Telegram poller stopped unexpectedly — restarting polling",
                                  action="poller_restart", result="RECOVERING")
                            print("  WARNING: Telegram poller stalled, restarting polling...")
                            await app.updater.start_polling(drop_pending_updates=True)
                    except Exception as exc:  # never let the watchdog die
                        system_log.debug("poller watchdog tick failed: %s", exc)

            watchdog_task = asyncio.create_task(_poller_watchdog())

            # Keep running forever — restart engine if it crashes
            while True:
                try:
                    await engine_task
                    # Engine exited cleanly — shouldn't happen, restart
                    audit(system_log, "Engine loop exited unexpectedly, restarting",
                          action="engine_restart", result="RESTARTING")
                    print("  WARNING: Engine exited, restarting in 5s...")
                    await asyncio.sleep(5)
                    engine_task = asyncio.create_task(engine.run())
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    audit(system_log, f"Engine crashed: {exc}",
                          action="engine_crash", result="ERROR")
                    print(f"  ERROR: Engine crashed: {exc}. Restarting in 10s...")
                    await asyncio.sleep(10)
                    engine_task = asyncio.create_task(engine.run())
        finally:
            # Tell the watchdog we are shutting down BEFORE stopping the updater,
            # so it never fights the intentional stop by re-starting polling.
            try:
                poller_state["stopping"] = True
                watchdog_task.cancel()
            except NameError:
                pass  # boot failed before the watchdog was created
            await handler.stop_monitor()
            if dashboard_runner:
                await dashboard_runner.cleanup()
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
            await engine.stop()

    try:
        loop.run_until_complete(_run_all())
    except KeyboardInterrupt:
        print("\nShutting down...")


async def run_cli() -> None:
    """Interactive CLI for testing skills without Telegram."""
    engine = RuneClawEngine()
    registry = build_default_registry()

    print(_banner())
    print("  CLI Mode -- type a skill name or 'quit' to exit.")
    print(f"  Available: {', '.join(s.split(' --')[0] for s in registry.list_skills())}\n")

    while True:
        try:
            raw = input("runeclaw> ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if raw in ("quit", "exit", "q"):
            break

        parts = raw.split()
        skill_name = parts[0] if parts else ""
        kwargs = {}
        if len(parts) > 1:
            # Simple key=value or positional arg as symbol
            if "=" in parts[1]:
                for p in parts[1:]:
                    k, v = p.split("=", 1)
                    kwargs[k] = v
            else:
                kwargs["symbol"] = f"{parts[1].upper()}/USDT"

        skill = registry.get(skill_name)
        if skill is None:
            print(f"  Unknown skill: {skill_name}")
            continue

        try:
            result = await skill.execute(engine, **kwargs)
            print(f"\n{result}\n")
        except Exception as exc:
            print(f"  Error: {exc}")

    await engine.stop()
    print("Goodbye.")


async def run_scan() -> None:
    """One-shot market scan for quick testing."""
    engine = RuneClawEngine()
    registry = build_default_registry()
    print(_banner())
    result = await registry.dispatch("scan_market", engine)  # type: ignore
    print(result)
    await engine.stop()


async def run_backtest() -> None:
    """Run a backtest with synthetic data (or CSV/API via the runner module)."""
    from bot.backtest.data_loader import DataLoader
    from bot.backtest.engine import BacktestEngine
    from bot.backtest.models import BacktestConfig
    from bot.backtest.runner import _format_result_summary

    print(_banner())
    print("  Backtest Mode -- generating synthetic data and replaying...\n")

    config = BacktestConfig(
        symbol="BTC/USDT",
        timeframe="1h",
        initial_balance=CONFIG.paper_balance_usd,
    )

    bars = DataLoader.generate_synthetic(bars=720, seed=42)
    print(f"  Generated {len(bars)} synthetic 1h bars (30 days)")
    print(f"  Price range: ${min(b.close for b in bars):,.2f} – ${max(b.close for b in bars):,.2f}\n")

    engine = BacktestEngine(config)
    try:
        result = await engine.run(bars)
    finally:
        engine.cleanup()
    print(_format_result_summary(result))


def main() -> None:
    parser = argparse.ArgumentParser(description="RUNECLAW AI Trading Command Core")
    parser.add_argument(
        # DEFAULT IS telegram, and that is an incident fix rather than a
        # preference. It used to be "cli": a deploy that forgot the flag
        # started run_cli(), found no TTY, and EXITED ZERO. The process
        # reported success, the bot was not running, and nothing in the
        # deploy output said so -- the operator hit this on ~15 consecutive
        # redeploys because `git reset --hard` kept restoring a launcher
        # missing the flag.
        #
        # scripts/health_check.sh already assumed telegram
        # (RUNECLAW_MODE:-telegram) and docker-compose.yml passes it
        # explicitly. Two of the three entry points already agreed; the
        # default was the odd one out, and it was the one a bare invocation
        # got. `--mode cli` still works for anyone who wants the REPL.
        "--mode", choices=["telegram", "cli", "scan", "backtest"],
        default="telegram",
        help="Run mode: telegram (bot), cli (interactive), scan (one-shot), backtest")
    args = parser.parse_args()

    if args.mode == "telegram":
        # Loud env preflight: name EVERY missing secret at once (not just the
        # first check that trips), so a wiped-.env redeploy is diagnosed in one
        # line instead of a guessing game. The secrets vault (config.py) has
        # already run its self-heal by now, so this reflects the post-restore
        # state. Critical-missing is fatal; important-missing degrades a web
        # surface but the bot still trades, so we log and continue.
        from bot.core.boot_health import env_preflight, format_preflight
        _pf = env_preflight(os.environ)
        _msg = format_preflight(_pf)
        if _pf["critical"]:
            print(f"ERROR: {_msg}")
            audit(system_log, _msg, action="startup", result="ENV_MISSING")
            sys.exit(1)
        if _pf["important"]:
            print(f"WARNING: {_msg}")
            audit(system_log, _msg, action="startup", result="ENV_DEGRADED")
        run_telegram()
    elif args.mode == "scan":
        asyncio.run(run_scan())
    elif args.mode == "backtest":
        asyncio.run(run_backtest())
    else:
        asyncio.run(run_cli())


if __name__ == "__main__":
    main()
