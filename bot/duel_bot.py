"""The Daily Duel's own Telegram bot: a second process, a second token, no engine.

    python3 -m bot.duel_bot

WHAT IT IS. A thin Telegram front for the duel the website scores. It reads
today's card and records a call through the website's bot channel
(`bot/utils/duel_pull.py`, the same route the main bot's /duel uses), renders
with the same formatter (`bot/formatters/duel_card.py`), and links a player to
their website account by posting the link token to `/api/auth/validate-token`
with the bot secret. It runs beside the main bot on the same box, on its own
BotFather token, so a player who never wants a trading bot in their chat can
still play the duel.

WHAT IT IS NOT. It is not the bot. It builds no engine, opens no venue, holds
no positions, and touches NONE of the main bot's state: not its SQLite store,
not its JSON user store, not its PID file, not its audit chain. CLAUDE.md:
"a process that is not the bot is a reader of the bot's state". This one is
not even that; everything it knows it asks the website for, per request.

THE TOKEN IS ITS OWN. Two processes polling one Telegram token answer each
other's 409 getUpdates conflict forever, and the main bot's watchdog would
restart its poller against this one every thirty seconds. So a DUEL_BOT_TOKEN
equal to TELEGRAM_BOT_TOKEN is refused at boot, as a missing one is, with exit
status 78 (EX_CONFIG): the systemd unit's RestartPreventExitStatus, so a box
that has not created the bot yet gets one loud line in its journal and a
stopped unit, not a fifteen-second restart loop.

HEALTH ON :8081. `active (running)` says a process exists; it does not say
the poller is alive. /health answers 200 only while polling is running and no
stop has been requested, so the launcher's port check and the status script
ask the question that matters.

No dollar amount appears anywhere: a duel has no stake to report.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, cast

log = logging.getLogger("runeclaw.duel_bot")

#: EX_CONFIG. The unit's RestartPreventExitStatus is this number; a test holds
#: the two to one reading.
EXIT_NOT_CONFIGURED = 78
HEALTH_HOST_DEFAULT = "127.0.0.1"
HEALTH_PORT_DEFAULT = 8081
#: Per chat: calls, seconds.
COMMAND_LIMIT = (20, 60.0)
LINK_LIMIT = (5, 600.0)
#: How often the poller is checked, and revived if it stopped while we were
#: not stopping (the main bot's watchdog, in miniature).
WATCHDOG_SEC = 30
REPO_ROOT = Path(__file__).resolve().parents[1]

_TOKEN_SHAPE = re.compile(r"\d{6,12}:[A-Za-z0-9_-]{20,}")


# ── configuration, read once and refused loudly ─────────────────────────────

def _clean_secret(raw: Any) -> str:
    """The value as `bot.config._env_secret` reads it: stripped, unquoted."""
    v = str(raw if raw is not None else "").strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        v = v[1:-1].strip()
    return v


def token_problem(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """Why DUEL_BOT_TOKEN cannot be used, or None. Names the variable, never
    the value: this line reaches a journal."""
    e = env if env is not None else os.environ
    tok = _clean_secret(e.get("DUEL_BOT_TOKEN", ""))
    if not tok:
        return ("DUEL_BOT_TOKEN is not set. Create the Daily Duel bot in BotFather and "
                "put its token in the bot's .env; the duel bot does not start without it.")
    if tok == _clean_secret(e.get("TELEGRAM_BOT_TOKEN", "")):
        return ("DUEL_BOT_TOKEN is the same token as TELEGRAM_BOT_TOKEN. Two processes "
                "polling one token conflict forever (409 getUpdates); the duel bot "
                "needs its own BotFather token.")
    if not _TOKEN_SHAPE.fullmatch(tok):
        return "DUEL_BOT_TOKEN does not have the shape of a Telegram bot token."
    return None


def channel_problem(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """Why the website's bot channel cannot be used, or None."""
    e = env if env is not None else os.environ
    if not _clean_secret(e.get("BOT_SYNC_SECRET", "")):
        return ("BOT_SYNC_SECRET is not set. The duel bot reads the card and records "
                "calls through the website's bot channel, which refuses a caller "
                "without it.")
    return None


def duel_token(env: Optional[Mapping[str, str]] = None) -> str:
    e = env if env is not None else os.environ
    return _clean_secret(e.get("DUEL_BOT_TOKEN", ""))


def load_env() -> None:
    """The repo's .env, as the main bot reads it, without overriding a value
    the process was started with (so an operator can try a token in a shell)."""
    try:
        from dotenv import load_dotenv
    except Exception:  # pragma: no cover - the dependency is pinned
        return
    load_dotenv(dotenv_path=REPO_ROOT / ".env", override=False)


# ── a small per-chat limiter ────────────────────────────────────────────────

class RateLimit:
    """`calls` per `seconds`, per key, in memory. A refusal is a refusal of
    this process only; the website keeps its own limits on the channel."""

    def __init__(self, calls: int, seconds: float, clock: Callable[[], float] = time.monotonic):
        self.calls = int(calls)
        self.seconds = float(seconds)
        self._clock = clock
        self._hits: dict[str, list[float]] = {}

    def admit(self, key: Any) -> bool:
        now = self._clock()
        k = str(key)
        hits = [t for t in self._hits.get(k, []) if now - t < self.seconds]
        if len(hits) >= self.calls:
            self._hits[k] = hits
            return False
        hits.append(now)
        self._hits[k] = hits
        return True


# ── the website's link endpoint ─────────────────────────────────────────────

def link_request(token: str, chat_id: str, env: Optional[Mapping[str, str]] = None) -> urllib.request.Request:
    """The POST that binds a website account to this chat. The bot secret
    proves the caller is a bot of ours (`app/auth.js` linkBotAuth); it is read
    at call time so a repaired .env takes effect without a restart."""
    from bot.utils.site_url import site_url
    e = env if env is not None else os.environ
    body = json.dumps({"token": str(token), "chat_id": str(chat_id)}).encode()
    return urllib.request.Request(
        f"{site_url()}/api/auth/validate-token",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "RUNECLAW-DuelBot/1.0",
            "X-Bot-Secret": _clean_secret(e.get("BOT_SYNC_SECRET", "")),
        },
        method="POST",
    )


def validate_link(token: str, chat_id: str) -> tuple[Optional[int], Optional[dict]]:
    """(status, body). status None: the request never completed."""
    req = link_request(token, chat_id)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode())
        except Exception:
            return exc.code, None
    except Exception as exc:
        log.error("validate-token unreachable: %s", type(exc).__name__)
        return None, None


def fetch_board() -> tuple[Optional[int], Optional[dict]]:
    """This month's public board: no session, no secret, counts only."""
    from bot.utils.site_url import site_url
    req = urllib.request.Request(
        f"{site_url()}/api/public/duel/board",
        headers={"Accept": "application/json", "User-Agent": "RUNECLAW-DuelBot/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except Exception as exc:
        log.error("board unreachable: %s", type(exc).__name__)
        return None, None


# ── sentences ───────────────────────────────────────────────────────────────

def link_hint(url: str) -> str:
    return ("Link your RUNECLAW website account to play.\n\n"
            f"1. Register or sign in at {url}\n"
            "2. Open the dashboard and copy your link token\n"
            "3. Send /link <token> here, in this private chat\n\n"
            "Your calls and record are the same on the website and in the Pi app.")


def start_text(url: str) -> str:
    return ("⚔️ *Daily Duel*\n"
            "Call the market before the Claw does. A card a day in four categories — "
            "direction, back or fade the Claw, range, bigger mover — and the market "
            "settles each call 24h after you make it. Free, no stake; counts and "
            "percent only.\n\n"
            "/duel — today's card\n"
            "/me — your record\n"
            "/board — this month's board\n"
            "/link <token> — link your website account (needed to play)\n"
            "/help — this list\n\n"
            f"The same game on the web: {url}/duel")


def record_text(data: Optional[dict]) -> str:
    """The player's record from the card payload. A failed read is a failed
    read, never a blank record."""
    from bot.formatters.duel_card import accuracy_line, marks_line
    if not data:
        return "Your record could not be read just now — it is unchanged. Try again in a moment."
    lines = ["⚔️ *Your Daily Duel record*", accuracy_line(data.get("accuracy")),
             marks_line(data.get("marks"))]
    streak = (data.get("streak") or {}).get("current")
    if streak is not None:
        lines.append(f"Streak: 🔥 {int(streak)} day{'s' if int(streak) != 1 else ''}")
    open_n = data.get("open")
    if open_n is not None:
        lines.append(f"Open on today's card: {int(open_n)} — /duel")
    return "\n".join(lines)


def board_text(status: Optional[int], body: Optional[dict]) -> str:
    """The public board as sentences. Counts and percent; no amount of anything."""
    if status != 200 or not body or not isinstance(body.get("rows"), list):
        return "The board could not be read just now. This is on our side."
    rows = body["rows"][:10]
    lines = [f"🏆 *Daily Duel board* — {body.get('season', 'this month')}"]
    if not rows:
        lines.append("Nobody is ranked yet.")
    for r in rows:
        pct = r.get("accuracy_pct")
        pct_s = f"{pct:g}%" if isinstance(pct, (int, float)) else "—"
        lines.append(f"{r.get('rank', '?')}. {r.get('handle', '?')} — {pct_s} · {r.get('marks', '—')} marks")
    warming = body.get("warming_up")
    if isinstance(warming, int) and warming > 0:
        lines.append(f"{warming} more still warming up — {body.get('min_calls', '?')} settled calls to be ranked.")
    return "\n".join(lines)


def link_outcome_text(status: Optional[int], body: Optional[dict], url: str) -> str:
    """Every status is its own sentence, and none of them reads as success
    unless the website said so."""
    from bot.utils.i18n import t
    if status == 200 and body and body.get("user_id") is not None:
        return "Linked. Send /duel for today's card."
    if status == 404:
        return t("link_token_invalid", "en", url=url)
    if status == 409:
        return "This Telegram is already linked to a RUNECLAW account. Send /duel to play."
    if status in (401, 403):
        return ("The website refused this bot's link request. The operator must check "
                "BOT_SYNC_SECRET on the duel bot; nothing was linked.")
    if status is None:
        return t("link_unreachable", "en")
    return t("link_validate_failed", "en")


def health_body(state: Mapping[str, Any]) -> tuple[int, dict]:
    """(status, body). 200 only while polling and not stopping: a process
    that exists but no longer polls is not a healthy bot."""
    ok = bool(state.get("polling")) and not bool(state.get("stopping"))
    return (200 if ok else 503), {
        "ok": ok,
        "service": "runeclaw-duel",
        "polling": bool(state.get("polling")),
        "stopping": bool(state.get("stopping")),
        "updates_seen": int(state.get("updates", 0)),
        "started_at": state.get("started_at"),
    }


# ── the stale-instance check ────────────────────────────────────────────────

def pid_path(env: Optional[Mapping[str, str]] = None) -> str:
    e = env if env is not None else os.environ
    return os.path.join(e.get("RUNECLAW_STATE_DIR", "data"), "runeclaw-duel.pid")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _cmdline(pid: int) -> bytes:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes()
    except Exception:
        return b""


def stale_duel_pid(path: str, *, alive: Callable[[int], bool] = _alive,
                   cmdline: Callable[[int], bytes] = _cmdline) -> Optional[int]:
    """The PID of an earlier duel bot still running, or None.

    Only a process whose command line names THIS module is ever returned. A
    PID file is a number, and a number can be reused by any process after a
    reboot — including the main bot. The main bot kills whatever its PID file
    names; this one refuses to, and a stale number that now belongs to
    something else is left alone and overwritten.
    """
    try:
        pid = int(Path(path).read_text(encoding="utf-8").strip())
    except Exception:
        return None
    if pid <= 0 or pid == os.getpid() or not alive(pid):
        return None
    return pid if b"bot.duel_bot" in cmdline(pid) else None


def stop_stale(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(30):
            time.sleep(0.1)
            if not _alive(pid):
                return
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except PermissionError:
        log.warning("could not stop the earlier duel bot (pid %s); Telegram may report a conflict", pid)


# ── the bot ─────────────────────────────────────────────────────────────────

class DuelBot:
    """Handlers over injected readers, so a test drives them without a network."""

    def __init__(self, *, fetch_card: Optional[Callable[..., Any]] = None,
                 place_pick: Optional[Callable[..., Any]] = None,
                 fetch_board: Optional[Callable[..., Any]] = None,
                 validate_link: Optional[Callable[..., Any]] = None,
                 site_url: Optional[str] = None, clock: Callable[[], float] = time.monotonic):
        self._fetch_card = fetch_card
        self._place_pick = place_pick
        self._fetch_board = fetch_board
        self._validate_link = validate_link
        self._site_url: Optional[str] = site_url
        self.commands = RateLimit(*COMMAND_LIMIT, clock=clock)
        self.links = RateLimit(*LINK_LIMIT, clock=clock)
        self.state: dict[str, Any] = {
            "polling": False, "stopping": False, "updates": 0,
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    # readers, resolved late so .env is loaded before duel_pull reads it
    def fetch_card(self, tg_id: str):
        if self._fetch_card is None:
            from bot.utils.duel_pull import fetch_card
            self._fetch_card = cast(Callable[..., Any], fetch_card)
        return cast(Callable[..., Any], self._fetch_card)(tg_id)

    def place_pick(self, tg_id: str, round_id: int, pick: str, chips: Optional[int] = None):
        if self._place_pick is None:
            from bot.utils.duel_pull import place_pick
            self._place_pick = cast(Callable[..., Any], place_pick)
        return cast(Callable[..., Any], self._place_pick)(tg_id, round_id, pick, chips)

    def board(self):
        reader = self._fetch_board if self._fetch_board is not None else fetch_board
        return reader()

    def validate(self, token: str, chat_id: str):
        checker = self._validate_link if self._validate_link is not None else validate_link
        return checker(token, chat_id)

    def url(self) -> str:
        if self._site_url is not None:
            return self._site_url
        from bot.utils.site_url import site_url
        return str(site_url())

    # ── sending ─────────────────────────────────────────────────────────────

    @staticmethod
    async def _reply(update, text: str, markup=None) -> None:
        """Through the outbound scrubber, as Markdown; plain when Telegram
        refuses the markup, so a card is never lost to a stray underscore."""
        from bot.utils.outbound import reply_safe
        msg = getattr(update, "effective_message", None) or getattr(update, "message", None)
        if msg is None:
            return
        safe = reply_safe(text)
        try:
            await msg.reply_text(safe, parse_mode="Markdown", reply_markup=markup)
        except Exception as exc:
            log.warning("markdown reply refused (%s); sending plain", type(exc).__name__)
            await msg.reply_text(safe, reply_markup=markup)

    @staticmethod
    def _tg_id(update) -> str:
        user = getattr(update, "effective_user", None)
        return str(getattr(user, "id", "") or "")

    @staticmethod
    def _chat_id(update) -> str:
        chat = getattr(update, "effective_chat", None)
        return str(getattr(chat, "id", "") or "")

    @staticmethod
    def markup(rows):
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        built = [[InlineKeyboardButton(label, callback_data=data) for label, data in row] for row in rows]
        return InlineKeyboardMarkup(built) if built else None

    @classmethod
    def keyboard(cls, rounds, chips=None):
        from bot.formatters.duel_card import button_rows
        return cls.markup(button_rows(rounds, chips))

    @staticmethod
    def as_shown(update, card):
        """The card as this chat may see it: a public room is offered no
        chips and shown no stack (`duel_card.for_room`)."""
        from bot.formatters.duel_card import for_room
        from bot.skills.public_room import is_public_room
        return for_room(card) if is_public_room(update) else card

    async def _limited(self, update) -> bool:
        if self.commands.admit(self._chat_id(update)):
            return False
        await self._reply(update, "Slow down a little — try again in a minute.")
        return True

    # ── commands ────────────────────────────────────────────────────────────

    async def cmd_start(self, update, context) -> None:
        if await self._limited(update):
            return
        await self._reply(update, start_text(self.url()))

    async def cmd_help(self, update, context) -> None:
        await self.cmd_start(update, context)

    async def cmd_duel(self, update, context) -> None:
        if await self._limited(update):
            return
        from bot.formatters.duel_card import render_card
        status, data = await asyncio.to_thread(self.fetch_card, self._tg_id(update))
        if status == 404:
            await self._reply(update, link_hint(self.url()))
            return
        if status != 200 or not data:
            await self._reply(update, render_card(None))
            return
        await self._redraw(update, data)

    async def _redraw(self, update, card) -> None:
        """The card and its keyboard, as this chat may see them."""
        from bot.formatters.duel_card import chips_block, render_card
        shown = self.as_shown(update, card)
        await self._reply(update, render_card(shown), self.keyboard(shown.get("rounds"), chips_block(shown)))

    async def cmd_me(self, update, context) -> None:
        if await self._limited(update):
            return
        status, data = await asyncio.to_thread(self.fetch_card, self._tg_id(update))
        if status == 404:
            await self._reply(update, link_hint(self.url()))
            return
        await self._reply(update, record_text(data if status == 200 else None))

    async def cmd_board(self, update, context) -> None:
        if await self._limited(update):
            return
        status, body = await asyncio.to_thread(self.board)
        await self._reply(update, board_text(status, body))

    async def cmd_link(self, update, context) -> None:
        """/link <token> — private chats only, and the website decides."""
        from bot.skills.public_room import is_public_room, private_only_notice
        from bot.utils.i18n import t
        if is_public_room(update):
            handle = getattr(getattr(context, "bot", None), "username", None)
            msg = getattr(update, "effective_message", None)
            if msg is not None:
                await msg.reply_text(private_only_notice("en", handle, "link"), parse_mode="HTML")
            return
        if await self._limited(update):
            return
        args = list(getattr(context, "args", None) or [])
        if not args:
            await self._reply(update, t("link_prompt", "en", url=self.url()))
            return
        chat_id = self._chat_id(update)
        if not self.links.admit(chat_id):
            await self._reply(update, "Too many link attempts — wait ten minutes and try again.")
            return
        status, body = await asyncio.to_thread(self.validate, str(args[0]).strip(), chat_id)
        await self._reply(update, link_outcome_text(status, body, self.url()))

    async def on_duel_callback(self, update, context) -> None:
        """A tap on a round's button: `duel:<round_id>:<pick>`, with a fourth
        part for chips: `:c` asks how many to put, `:<n>` puts them."""
        from bot.formatters.duel_card import CHOOSE, chips_prompt, parse_callback_data, render_pick_result
        from bot.skills.public_room import is_public_room
        query = getattr(update, "callback_query", None)
        if query is None:
            return
        try:
            await query.answer()
        except Exception as exc:
            log.debug("callback answer failed: %s", type(exc).__name__)
        parsed = parse_callback_data(str(getattr(query, "data", "") or ""))
        if parsed is None:
            return
        if await self._limited(update):
            return
        round_id, pick, chips = parsed
        tg = self._tg_id(update)
        if chips is not None and is_public_room(update):
            # A room is offered no chips; a token that carries them anyway
            # (forwarded, or typed) records nothing here.
            await self._reply(update, "Chips are put in a private chat with me. Nothing was recorded.")
            return
        if chips == CHOOSE:
            # Ask before recording: chips ride with the call or not at all.
            c_status, card = await asyncio.to_thread(self.fetch_card, tg)
            if c_status == 404:
                await self._reply(update, link_hint(self.url()))
                return
            prompt = chips_prompt(card, round_id, pick) if c_status == 200 and card else None
            if prompt is None:
                await self._reply(update, "That call cannot take chips now. Nothing was recorded; "
                                          "here is today's card.")
                if c_status == 200 and card:
                    await self._redraw(update, card)
                return
            text, rows = prompt
            await self._reply(update, text, self.markup(rows))
            return
        status, result = await asyncio.to_thread(
            self.place_pick, tg, round_id, pick, chips if isinstance(chips, int) else None)
        if status == 404:
            await self._reply(update, link_hint(self.url()))
            return
        await self._reply(update, render_pick_result(result, status))
        # Redraw: the called round loses its buttons and the stance appears.
        c_status, card = await asyncio.to_thread(self.fetch_card, tg)
        if c_status == 200 and card:
            await self._redraw(update, card)

    async def on_any_update(self, update, context) -> None:
        self.state["updates"] = int(self.state.get("updates", 0)) + 1

    # ── wiring ──────────────────────────────────────────────────────────────

    COMMANDS = (("start", "cmd_start"), ("help", "cmd_help"), ("duel", "cmd_duel"),
                ("me", "cmd_me"), ("board", "cmd_board"), ("link", "cmd_link"))

    def build_app(self, token: str):
        from telegram import Update
        from telegram.ext import Application, CallbackQueryHandler, CommandHandler, TypeHandler
        app = Application.builder().token(token).build()
        # Every update counts toward liveness before any handler sees it.
        app.add_handler(TypeHandler(Update, self.on_any_update), group=-1)
        for name, attr in self.COMMANDS:
            app.add_handler(CommandHandler(name, getattr(self, attr)))
        app.add_handler(CallbackQueryHandler(self.on_duel_callback, pattern=r"^duel:"))
        return app


# ── the process ─────────────────────────────────────────────────────────────

async def _start_health(bot: DuelBot, host: str, port: int):
    from aiohttp import web

    async def health(request):
        status, body = health_body(bot.state)
        return web.json_response(body, status=status)

    app = web.Application()
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    return runner


async def serve(app, bot: DuelBot, *, host: str = HEALTH_HOST_DEFAULT, port: int = HEALTH_PORT_DEFAULT) -> None:
    from bot.core.boot_health import format_stop_handlers, install_stop_handlers, poller_should_restart
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()

    def _request_stop(name: str) -> None:
        if bot.state["stopping"]:
            return
        bot.state["stopping"] = True
        log.info("%s received — stopping", name)
        stop.set()

    armed = install_stop_handlers(loop, _request_stop)
    log.info(format_stop_handlers(armed))

    runner = None
    try:
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        bot.state["polling"] = True
        runner = await _start_health(bot, host, port)
        log.info("Daily Duel bot polling; health on http://%s:%s/health", host, port)
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=WATCHDOG_SEC)
            except asyncio.TimeoutError:
                pass
            try:
                running = bool(getattr(app.updater, "running", False))
                bot.state["polling"] = running
                if poller_should_restart(running, bot.state["stopping"]):
                    log.warning("poller stopped unexpectedly — restarting polling")
                    await app.updater.start_polling(drop_pending_updates=True)
                    bot.state["polling"] = True
            except Exception as exc:  # the watchdog never dies
                log.debug("watchdog tick failed: %s", type(exc).__name__)
    finally:
        bot.state["polling"] = False
        for step in (
            lambda: app.updater.stop() if getattr(app.updater, "running", False) else None,
            app.stop, app.shutdown,
            (runner.cleanup if runner is not None else None),
        ):
            if step is None:
                continue
            try:
                res = step()
                if asyncio.iscoroutine(res):
                    await res
            except Exception as exc:
                log.debug("shutdown step failed: %s", type(exc).__name__)


def run_duel_bot(argv: Optional[list[str]] = None) -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    load_env()
    problem = token_problem() or channel_problem()
    if problem:
        print(f"ERROR: {problem}", file=sys.stderr)
        log.error(problem)
        sys.exit(EXIT_NOT_CONFIGURED)

    path = pid_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    stale = stale_duel_pid(path)
    if stale is not None:
        log.info("stopping the earlier duel bot (pid %s) before polling", stale)
        stop_stale(stale)
    Path(path).write_text(str(os.getpid()), encoding="utf-8")

    port = int(os.environ.get("DUEL_HEALTH_PORT", str(HEALTH_PORT_DEFAULT)) or HEALTH_PORT_DEFAULT)
    host = os.environ.get("DUEL_HEALTH_HOST", HEALTH_HOST_DEFAULT) or HEALTH_HOST_DEFAULT
    bot = DuelBot()
    app = bot.build_app(duel_token())
    try:
        asyncio.run(serve(app, bot, host=host, port=port))
    finally:
        try:
            if Path(path).read_text(encoding="utf-8").strip() == str(os.getpid()):
                os.unlink(path)
        except Exception:
            pass


if __name__ == "__main__":  # pragma: no cover - the entry point
    run_duel_bot()
