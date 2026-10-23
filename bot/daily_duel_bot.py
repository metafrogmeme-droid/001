"""Daily Duel, the anonymous Telegram bot. A separate process, a separate token.

    python3 -m bot.daily_duel_bot

One up or down call per duel day, sealed by the website's own duel
(``app/lib/daily_duel_gate.js`` → ``callseal.sealDuelPick``). The cutoff and
the entry come from that module. This process builds no engine, opens no
venue, holds no key and signs nothing.

THE TOKEN IS ITS OWN. ``DAILY_DUEL_BOT_TOKEN``. A missing token, a token that
is not shaped like one, or one equal to ``TELEGRAM_BOT_TOKEN`` or
``DUEL_BOT_TOKEN`` (two pollers on one token conflict forever) exits 78
(EX_CONFIG). ``DAILY_DUEL_ID_SALT`` is required the same way: the anonymous
id is an HMAC, and without a salt the hash would be the Telegram id.

THE TELEGRAM ID IS NOT SEALED. It is hashed in memory and checked against the
receipt before the receipt is stored. The store's key is the hash.

PLAIN BOT. No Mini App, no web_app button.

HEALTH ON :8082. 200 only while polling and not stopping.

APPROVAL HYGIENE. The same process, opt-in, through ``ApprovalDigest``.
``/watch`` stores up to five addresses under the salted chat id, ``/digest``
reads them now, and a weekly loop sends the same note. ``/stop`` deletes the
addresses and the schedule. Reads only: Ethereum, Base, Avalanche C-Chain,
Arbitrum. It signs nothing and holds no wallet key. The digest object does
not depend on this process, so another bot can construct the same one.

What this process does not do yet, and says so: score a call, rank Arena
Season 1, accept an Arena handle code, or fold the receipt into the day's
public root. Anchoring stays at humanoid-traders.com/doge, from the player's
own wallet.
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from bot import scam_or_safe as scam
from bot.approval_digest import ApprovalDigest
from bot.approval_hygiene import DIGEST_HELP, command_argument
from bot.approval_reader import ApprovalReader, ExploitDirectory
from bot.approval_watch_store import ApprovalWatchStore
from bot.daily_duel_gate import DuelGate, GateDown
from bot.daily_duel_store import CallStore, PayloadRefused, payload_carries_id, player_key
from bot.duel_bot import EXIT_NOT_CONFIGURED, RateLimit, _clean_secret

log = logging.getLogger("runeclaw.daily_duel_bot")

HEALTH_HOST_DEFAULT = "127.0.0.1"
HEALTH_PORT_DEFAULT = 8082
COMMAND_LIMIT = (20, 60.0)
WATCHDOG_SEC = 30
REPO_ROOT = Path(__file__).resolve().parents[1]
WEB = "humanoid-traders.com/doge"
_TOKEN_SHAPE = re.compile(r"\d{6,12}:[A-Za-z0-9_-]{20,}")
_SALT_MIN = 16
_CB = re.compile(r"^dd:(ask|seal|back)(?::(long|short))?(?::([A-Z0-9]{2,20}))?$")

WELCOME = (
    "👋 Welcome to Daily Duel by RUNECLAW Arena.\n"
    "\n"
    "Each day you make one call: will the market go up or down?\n"
    "🔒 Your call is sealed before the move, so it can't be changed.\n"
    "🏆 Build your streak and climb the Season 1 leaderboard.\n"
    "\n"
    "Tap /duel to make today's call.\n"
    "Also on the web: humanoid-traders.com/doge"
)

PRIVACY = (
    "We store an anonymous ID to limit one call per day. Your Telegram details "
    "are never sealed or shared, and we never hold your keys."
)

COMING_SCORE = "Your score — how often the call was right — is coming soon. There is no percent here."
COMING_BOARD = (
    "Arena Season 1 standings are coming soon. When they arrive they will show "
    "handles and how often calls were right — counts and percent only."
)
COMING_HANDLE = (
    "Linking an Arena handle with a one-time code from the website is coming soon. "
    "Until then you play anonymously, and no name is sealed with your call."
)
COMING_ROOT = (
    "Folding this call into the day's public root is coming soon. "
    f"The day's root is anchored from your own wallet at {WEB}. This bot never signs."
)
STREAK_UNREAD = (
    "Your streak could not be read just now. It is unchanged. " + COMING_SCORE
)


def token_problem(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """Why DAILY_DUEL_BOT_TOKEN cannot be used, or None. Names the variable,
    never the value."""
    e = env if env is not None else os.environ
    tok = _clean_secret(e.get("DAILY_DUEL_BOT_TOKEN", ""))
    if not tok:
        return ("DAILY_DUEL_BOT_TOKEN is not set. Create the Daily Duel bot in BotFather "
                "and put its token in the environment; this bot does not start without it.")
    main = _clean_secret(e.get("TELEGRAM_BOT_TOKEN", ""))
    other = _clean_secret(e.get("DUEL_BOT_TOKEN", ""))
    if tok == main or (other and tok == other):
        return ("DAILY_DUEL_BOT_TOKEN is the same token as another bot. Two processes "
                "polling one token conflict forever (409 getUpdates); this bot needs "
                "its own BotFather token.")
    if not _TOKEN_SHAPE.fullmatch(tok):
        return "DAILY_DUEL_BOT_TOKEN does not have the shape of a Telegram bot token."
    return None


def salt_problem(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """Why DAILY_DUEL_ID_SALT cannot be used, or None. Never echoes the salt."""
    e = env if env is not None else os.environ
    salt = _clean_secret(e.get("DAILY_DUEL_ID_SALT", ""))
    if not salt:
        return ("DAILY_DUEL_ID_SALT is not set. The anonymous id is an HMAC of the "
                "Telegram id; without a salt this bot does not start.")
    if len(salt) < _SALT_MIN:
        return "DAILY_DUEL_ID_SALT is too short to salt an id."
    return None


def bot_token(env: Optional[Mapping[str, str]] = None) -> str:
    e = env if env is not None else os.environ
    return _clean_secret(e.get("DAILY_DUEL_BOT_TOKEN", ""))


def load_env() -> None:
    try:
        from dotenv import load_dotenv
    except Exception:  # pragma: no cover - the dependency is pinned
        return
    load_dotenv(dotenv_path=REPO_ROOT / ".env", override=False)


def help_text(rules: Optional[Mapping[str, Any]]) -> str:
    """How the duel works. The cutoff and the horizon are the gate's, or the
    sentence says they could not be read. The privacy line is always this one."""
    if rules and rules.get("cutoff_at") and "horizon_hours" in rules:
        when = (
            f"Calls for a duel day close at {rules['cutoff_at']} — the moment the "
            f"UTC day rolls, which is when the /doge card stops taking a direction call. "
            f"A sealed call settles {rules['horizon_hours']} hours after you make it, "
            f"from the entry read then ({rules.get('price_reader')} in {rules.get('price_module')})."
        )
    else:
        when = "The cutoff could not be read just now, so it is not shown. Nothing was assumed."
    return "\n\n".join([
        "Daily Duel — one call each duel day: up or down.",
        when,
        "Your call is sealed before the move and cannot be changed. /duel asks you to confirm before it seals.",
        COMING_ROOT,
        PRIVACY,
        COMING_HANDLE,
        "/duel — today's call\n/result — the latest result\n/streak — your streak and score\n"
        "/leaderboard — Arena Season 1 standings\n/help — this note\n"
        "/watch <address> — opt in a wallet for the weekly approval digest\n"
        "/digest — that digest now\n"
        "/stop — delete every watched address and the digest schedule",
        DIGEST_HELP,
        "Scoring a call and the Season 1 board are coming soon. A missing score is not a zero.",
    ] + ([scam.SCAM_HELP] if scam.flag_on() else []))


def _word(pick: str) -> str:
    if pick == "long":
        return "up"
    if pick == "short":
        return "down"
    return pick


def _payload(row: Mapping[str, Any]) -> dict:
    try:
        body = json.loads(str(row.get("seal_payload") or ""))
    except (TypeError, ValueError):
        return {}
    return body if isinstance(body, dict) else {}


def sealed_text(row: Mapping[str, Any], *, repeat: bool) -> str:
    body = _payload(row)
    lead = ("You already sealed today's call. Here it is — it cannot be changed."
            if repeat else "Sealed. This cannot be changed.")
    entry = body.get("entry_price")
    entry_s = "unreadable" if entry is None else str(entry)
    return "\n".join([
        lead,
        f"{_word(str(body.get('pick') or row.get('pick') or ''))} on {body.get('symbol') or row.get('symbol')} "
        f"for {body.get('day') or row.get('day')}.",
        f"Entry: {entry_s}",
        f"Settles: {body.get('resolves_at') or row.get('resolves_at')}",
        f"Seal: {row.get('seal')}",
        COMING_ROOT,
    ])


def offer_text(offer: Mapping[str, Any]) -> str:
    if offer.get("unreadable") or offer.get("entry_price") is None or not offer.get("symbol"):
        return ("Today's entry could not be read, so no call can be sealed. "
                "Nothing was recorded. Try again in a moment.")
    if not offer.get("callable"):
        return (f"Today's call has closed. The duel day ended at {offer.get('cutoff_at')}. "
                "Nothing was sealed.")
    return "\n".join([
        f"Today ({offer.get('day')}): will {offer.get('symbol')} go up or down?",
        f"Cutoff: {offer.get('cutoff_at')} (the duel day closes then).",
        f"A sealed call settles {offer.get('horizon_hours')} hours later, "
        "from the entry read at the moment you confirm.",
        f"Entry on offer now: {offer.get('entry_price')}",
        "Choose up or down. You confirm before it is sealed, and a sealed call cannot be changed.",
    ])


def confirm_text(pick: str, offer: Mapping[str, Any]) -> str:
    return "\n".join([
        f"Seal {_word(pick)} on {offer.get('symbol')} for {offer.get('day')}?",
        f"Cutoff: {offer.get('cutoff_at')}.",
        "The entry is the one read at the moment you seal, and the call cannot be changed afterwards.",
    ])


def result_text(row: Optional[Mapping[str, Any]], now_iso: str) -> str:
    if not row:
        return "You have not sealed a call yet. Tap /duel to make today's."
    body = _payload(row)
    resolves = body.get("resolves_at") or row.get("resolves_at")
    if isinstance(resolves, str) and now_iso < resolves:
        state = (f"Not due yet. It settles at {resolves}. "
                 "A result before then would be a guess, so there isn't one.")
    else:
        state = ("This call is due. Scoring it is coming soon. "
                 "There is no result to show, and that is not a loss.")
    return state + "\n" + sealed_text(row, repeat=True)


def streak_text(reading: Optional[Mapping[str, Any]]) -> str:
    """A streak the gate returned, or an unread one. A failed read is not zero."""
    if not isinstance(reading, dict):
        return STREAK_UNREAD
    current = reading.get("current")
    best = reading.get("best")
    if type(current) is not int or type(best) is not int:
        return STREAK_UNREAD
    unit = "day" if current == 1 else "days"
    return (f"Streak: {current} {unit} (best {best}). This counts consecutive duel days "
            f"you sealed a call — the duel's own streak, not a second one.\n{COMING_SCORE}")


def parse_callback(data: str) -> Optional[tuple[str, Optional[str], Optional[str]]]:
    m = _CB.fullmatch(str(data or ""))
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3)


def health_body(state: Mapping[str, Any]) -> tuple[int, dict]:
    ok = bool(state.get("polling")) and not bool(state.get("stopping"))
    return (200 if ok else 503), {
        "ok": ok,
        "service": "runeclaw-daily-duel",
        "polling": bool(state.get("polling")),
        "stopping": bool(state.get("stopping")),
        "updates_seen": int(state.get("updates", 0)),
        "started_at": state.get("started_at"),
    }


def pid_path(env: Optional[Mapping[str, str]] = None) -> str:
    e = env if env is not None else os.environ
    base = e.get("DAILY_DUEL_STATE_DIR", os.path.join("data", "daily-duel"))
    return os.path.join(base, "daily-duel.pid")


def state_dir(env: Optional[Mapping[str, str]] = None) -> str:
    e = env if env is not None else os.environ
    return e.get("DAILY_DUEL_STATE_DIR", os.path.join("data", "daily-duel"))


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


def stale_pid(path: str, *, alive: Callable[[int], bool] = _alive,
              cmdline: Callable[[int], bytes] = _cmdline) -> Optional[int]:
    """An earlier copy of THIS module, or None. A reused pid that now belongs
    to the trading bot is left alone."""
    try:
        pid = int(Path(path).read_text(encoding="utf-8").strip())
    except Exception:
        return None
    if pid <= 0 or pid == os.getpid() or not alive(pid):
        return None
    return pid if b"bot.daily_duel_bot" in cmdline(pid) else None


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
        log.warning("could not stop the earlier daily duel bot (pid %s)", pid)


class DailyDuelBot:
    def __init__(self, *, salt: str, gate: Optional[DuelGate] = None,
                 store: Optional[CallStore] = None, clock: Callable[[], float] = time.monotonic,
                 now: Optional[Callable[[], str]] = None,
                 watches: Optional[ApprovalWatchStore] = None,
                 reader: Optional[ApprovalReader] = None,
                 exploits: Optional[ExploitDirectory] = None,
                 interval_sec: Optional[int] = None,
                 poll_sec: Optional[int] = None):
        self.salt = salt
        self.gate = gate if gate is not None else DuelGate()
        base = state_dir()
        self.store = store if store is not None else CallStore(Path(base) / "calls.sqlite")
        self._now = now or (lambda: datetime.now(timezone.utc).isoformat(
            timespec="milliseconds").replace("+00:00", "Z"))
        self.digest = ApprovalDigest(
            salt=salt,
            store=watches,
            store_path=Path(base) / "approval_watches.sqlite",
            reader=reader,
            exploits=exploits,
            now=self._now,
            interval_sec=interval_sec,
            poll_sec=poll_sec,
        )
        self.commands = RateLimit(*COMMAND_LIMIT, clock=clock)
        self.state: dict[str, Any] = {
            "polling": False, "stopping": False, "updates": 0,
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    @staticmethod
    async def _reply(update, text: str, markup=None) -> None:
        from bot.utils.outbound import reply_safe
        msg = getattr(update, "effective_message", None) or getattr(update, "message", None)
        if msg is None:
            return
        await msg.reply_text(reply_safe(text), reply_markup=markup)

    @staticmethod
    def _tg_id(update) -> str:
        user = getattr(update, "effective_user", None)
        return str(getattr(user, "id", "") or "")

    def _key(self, update) -> Optional[str]:
        tg = self._tg_id(update)
        if not tg:
            return None
        return player_key(tg, self.salt)

    @staticmethod
    def markup(rows):
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        built = [[InlineKeyboardButton(label, callback_data=data) for label, data in row] for row in rows]
        return InlineKeyboardMarkup(built) if built else None

    def ask_markup(self, symbol: str):
        return self.markup([
            [("Up", f"dd:ask:long:{symbol}"), ("Down", f"dd:ask:short:{symbol}")],
        ])

    def confirm_markup(self, pick: str, symbol: str):
        return self.markup([
            [(f"Seal {_word(pick)}", f"dd:seal:{pick}:{symbol}")],
            [("Back", "dd:back")],
        ])

    async def _limited(self, update) -> bool:
        who = self._tg_id(update) or self._chat(update)
        if self.commands.admit(who):
            return False
        await self._reply(update, "Slow down a little — try again in a minute.")
        return True

    @staticmethod
    def _chat(update) -> str:
        chat = getattr(update, "effective_chat", None)
        return str(getattr(chat, "id", "") or "")

    @property
    def _watches(self) -> Optional[ApprovalWatchStore]:
        return self.digest.store

    @_watches.setter
    def _watches(self, value: Optional[ApprovalWatchStore]) -> None:
        self.digest.store = value

    @property
    def _watch_path(self) -> Path:
        return self.digest.store_path

    @property
    def delivery(self) -> dict[str, str]:
        return self.digest.delivery

    @property
    def watches(self) -> ApprovalWatchStore:
        return self.digest.watches

    @property
    def reader(self) -> ApprovalReader:
        return self.digest.reader

    @property
    def exploits(self) -> ExploitDirectory:
        return self.digest.exploits

    @property
    def interval_sec(self) -> int:
        return self.digest.interval_sec

    @property
    def poll_sec(self) -> int:
        return self.digest.poll_sec

    def bind_chat(self, update) -> None:
        """Remember where to deliver a digest. Not written to the store."""
        self.digest.bind(self._chat(update))

    async def cmd_start(self, update, context) -> None:
        if await self._limited(update):
            return
        await self._reply(update, WELCOME)
        if scam.flag_on():
            await self._reply(update, scam.SCAM_WELCOME)

    async def cmd_help(self, update, context) -> None:
        if await self._limited(update):
            return
        rules = None
        try:
            rules = await asyncio.to_thread(self.gate.rules)
        except GateDown:
            rules = None
        await self._reply(update, help_text(rules))

    async def cmd_leaderboard(self, update, context) -> None:
        if await self._limited(update):
            return
        await self._reply(update, COMING_BOARD)

    async def cmd_streak(self, update, context) -> None:
        if await self._limited(update):
            return
        key = self._key(update)
        if key is None:
            await self._reply(update, STREAK_UNREAD)
            return
        times = await asyncio.to_thread(self.store.picked_at, key)
        reading = None
        try:
            reading = await asyncio.to_thread(self.gate.streak, times, self._now())
        except GateDown:
            reading = None
        await self._reply(update, streak_text(reading))

    async def cmd_result(self, update, context) -> None:
        if await self._limited(update):
            return
        key = self._key(update)
        row = None
        if key is not None:
            row = await asyncio.to_thread(self.store.latest, key)
        await self._reply(update, result_text(row, self._now()))

    async def _offer(self):
        return await asyncio.to_thread(self.gate.offer, self._now())

    async def cmd_watch(self, update, context) -> None:
        if await self._limited(update):
            return
        raw = command_argument(getattr(getattr(update, "effective_message", None), "text", "") or "")
        await self._reply(update, await self.digest.on_watch(self._chat(update), raw))

    async def cmd_digest(self, update, context) -> None:
        if await self._limited(update):
            return
        for text in await self.digest.on_digest(self._chat(update)):
            await self._reply(update, text)

    async def cmd_stop(self, update, context) -> None:
        if await self._limited(update):
            return
        await self._reply(update, await self.digest.on_stop(self._chat(update)))

    async def dispatch_due(self, send) -> int:
        return await self.digest.dispatch_due(send)

    async def cmd_duel(self, update, context) -> None:
        if await self._limited(update):
            return
        await self._show_duel(update)

    async def _show_duel(self, update) -> None:
        key = self._key(update)
        if key is None:
            await self._reply(update, "I could not tell who is asking, so nothing was sealed.")
            return
        try:
            offer = await self._offer()
        except GateDown:
            await self._reply(update, "The duel could not be read just now, so nothing was sealed.")
            return
        existing = await asyncio.to_thread(self.store.for_day, key, str(offer.get("day") or ""))
        if existing:
            await self._reply(update, sealed_text(existing, repeat=True))
            return
        text = offer_text(offer)
        markup = None
        if offer.get("callable") and offer.get("symbol") and offer.get("entry_price") is not None:
            markup = self.ask_markup(str(offer["symbol"]))
        await self._reply(update, text, markup)

    async def on_callback(self, update, context) -> None:
        query = getattr(update, "callback_query", None)
        if query is None:
            return
        try:
            await query.answer()
        except Exception as exc:
            log.debug("callback answer failed: %s", type(exc).__name__)
        parsed = parse_callback(str(getattr(query, "data", "") or ""))
        if parsed is None:
            return
        if await self._limited(update):
            return
        action, pick, symbol = parsed
        if action == "back" or action == "ask":
            if action == "ask" and pick and symbol:
                await self._confirm(update, pick, symbol)
            else:
                await self._show_duel(update)
            return
        if action == "seal" and pick and symbol:
            await self._seal(update, pick, symbol)

    async def _confirm(self, update, pick: str, symbol: str) -> None:
        key = self._key(update)
        if key is None:
            await self._reply(update, "I could not tell who is asking, so nothing was sealed.")
            return
        try:
            offer = await self._offer()
        except GateDown:
            await self._reply(update, "The duel could not be read just now, so nothing was sealed.")
            return
        existing = await asyncio.to_thread(self.store.for_day, key, str(offer.get("day") or ""))
        if existing:
            await self._reply(update, sealed_text(existing, repeat=True))
            return
        if not offer.get("callable") or offer.get("unreadable") or offer.get("symbol") != symbol:
            await self._reply(update, offer_text(offer),
                              self.ask_markup(str(offer["symbol"])) if offer.get("symbol") and offer.get("callable")
                              and offer.get("entry_price") is not None else None)
            return
        await self._reply(update, confirm_text(pick, offer), self.confirm_markup(pick, symbol))

    async def _seal(self, update, pick: str, symbol: str) -> None:
        tg = self._tg_id(update)
        key = self._key(update)
        if key is None or not tg:
            await self._reply(update, "I could not tell who is asking, so nothing was sealed.")
            return
        try:
            offer = await self._offer()
        except GateDown:
            await self._reply(update, "The duel could not be read just now, so nothing was sealed.")
            return
        existing = await asyncio.to_thread(self.store.for_day, key, str(offer.get("day") or ""))
        if existing:
            await self._reply(update, sealed_text(existing, repeat=True))
            return
        try:
            receipt = await asyncio.to_thread(self.gate.seal, {
                "now": self._now(), "pick": pick, "symbol": symbol,
            })
        except GateDown:
            await self._reply(update, "The seal could not be made, so nothing was recorded.")
            return
        if not receipt.get("ok"):
            await self._reply(update, self._refused(receipt))
            return
        row = {
            "player_key": key,
            "day": receipt.get("day"),
            "pick": receipt.get("pick"),
            "symbol": receipt.get("symbol"),
            "seal": receipt.get("seal"),
            "seal_payload": receipt.get("seal_payload"),
            "picked_at": receipt.get("picked_at"),
            "resolves_at": receipt.get("resolves_at"),
        }
        try:
            stored, created = await asyncio.to_thread(self.store.record, row, telegram_id=tg)
        except PayloadRefused:
            await self._reply(update, "The call was not sealed: the receipt would have carried your id. "
                                      "Nothing was recorded.")
            return
        except ValueError:
            await self._reply(update, "The call was not sealed. Nothing was recorded.")
            return
        await self._reply(update, sealed_text(stored, repeat=not created))

    @staticmethod
    def _refused(receipt: Mapping[str, Any]) -> str:
        err = receipt.get("error")
        if err == "closed":
            return (f"That duel day has closed (cutoff {receipt.get('cutoff_at')}). "
                    "Nothing was sealed. /duel shows the day that is open.")
        if err == "moved":
            return (f"The market is {receipt.get('symbol')} now, not the one you confirmed. "
                    "Nothing was sealed. /duel shows today's call.")
        if err == "unreadable":
            return "The entry could not be read, so nothing was sealed."
        return "That call was not sealed. Nothing was recorded."

    async def cmd_scam(self, update, context) -> None:
        if await self._limited(update):
            return
        if not scam.flag_on():
            await self._reply(update, "Scam or Safe is not open. Nothing was sealed.")
            return
        await self._show_scam(update)

    async def _show_scam(self, update) -> None:
        key = self._key(update)
        if key is None:
            await self._reply(update, "I could not tell who is asking, so nothing was sealed.")
            return
        try:
            offer = await asyncio.to_thread(self.gate.sos_offer, self._now(), key)
        except GateDown:
            await self._reply(update, "Today's request could not be read, so nothing was sealed.")
            return
        if not isinstance(offer, dict) or not offer.get("ok"):
            await self._reply(update, scam.refused_text(offer if isinstance(offer, dict) else {}))
            return
        if offer.get("sealed"):
            await self._reply(update, scam.reveal_text(offer))
            return
        raw_puzzle = offer.get("puzzle")
        puzzle = raw_puzzle if isinstance(raw_puzzle, dict) else {}
        pid = str(puzzle.get("id") or "")
        markup = self.markup([
            [("Safe", f"sos:ask:safe:{pid}"), ("Risky", f"sos:ask:risky:{pid}")],
        ]) if pid else None
        await self._reply(update, scam.offer_text(offer), markup)

    async def on_sos(self, update, context) -> None:
        query = getattr(update, "callback_query", None)
        if query is None:
            return
        try:
            await query.answer()
        except Exception as exc:
            log.debug("callback answer failed: %s", type(exc).__name__)
        if not scam.flag_on():
            await self._reply(update, "Scam or Safe is not open. Nothing was sealed.")
            return
        parsed = scam.parse_callback(str(getattr(query, "data", "") or ""))
        if parsed is None or await self._limited(update):
            return
        action, pick, puzzle_id = parsed
        if action == "back" or action == "ask" and not pick:
            await self._show_scam(update)
            return
        if action == "ask" and pick and puzzle_id:
            await self._confirm_scam(update, pick, puzzle_id)
            return
        if action == "seal" and pick and puzzle_id:
            await self._seal_scam(update, pick, puzzle_id)

    async def _confirm_scam(self, update, pick: str, puzzle_id: str) -> None:
        key = self._key(update)
        if key is None:
            await self._reply(update, "I could not tell who is asking, so nothing was sealed.")
            return
        try:
            offer = await asyncio.to_thread(self.gate.sos_offer, self._now(), key)
        except GateDown:
            await self._reply(update, "Today's request could not be read, so nothing was sealed.")
            return
        if not isinstance(offer, dict) or not offer.get("ok"):
            await self._reply(update, scam.refused_text(offer if isinstance(offer, dict) else {}))
            return
        if offer.get("sealed"):
            await self._reply(update, scam.reveal_text(offer))
            return
        raw_puzzle = offer.get("puzzle")
        puzzle = raw_puzzle if isinstance(raw_puzzle, dict) else {}
        if puzzle.get("id") != puzzle_id:
            await self._show_scam(update)
            return
        await self._reply(update, scam.confirm_text(pick, offer), self.markup([
            [(f"Seal {pick}", f"sos:seal:{pick}:{puzzle_id}")],
            [("Back", "sos:back")],
        ]))

    async def _seal_scam(self, update, pick: str, puzzle_id: str) -> None:
        tg = self._tg_id(update)
        key = self._key(update)
        if key is None or not tg:
            await self._reply(update, "I could not tell who is asking, so nothing was sealed.")
            return
        try:
            receipt = await asyncio.to_thread(self.gate.sos_seal, {
                "now": self._now(), "pick": pick, "player_key": key, "puzzle_id": puzzle_id,
            })
        except GateDown:
            await self._reply(update, "The seal could not be made, so nothing was recorded.")
            return
        if not isinstance(receipt, dict) or not receipt.get("ok"):
            await self._reply(update, scam.refused_text(receipt if isinstance(receipt, dict) else {}))
            return
        payload = str(receipt.get("seal_payload") or "")
        if payload_carries_id(payload, tg):
            await self._reply(update, "The call was not sealed: the receipt would have carried your id. "
                                      "Nothing was recorded.")
            return
        await self._reply(update, scam.reveal_text(receipt))

    async def on_any_update(self, update, context) -> None:
        self.state["updates"] = int(self.state.get("updates", 0)) + 1
        self.bind_chat(update)
        # A restart drops the in-memory chat id. The next message binds it again
        # and, if a digest is already due, sends it without waiting for the poll.
        sender = getattr(context, "bot", None)
        if sender is None or not hasattr(sender, "send_message"):
            return
        if self.digest.store is None and not self.digest.store_path.exists():
            return
        from bot.utils.outbound import reply_safe

        async def _send(chat_id: str, text: str) -> None:
            await sender.send_message(chat_id=int(chat_id), text=reply_safe(text))

        try:
            await self.dispatch_due(_send)
        except Exception as exc:
            log.warning("approval digest catch-up failed: %s", type(exc).__name__)

    COMMANDS = (
        ("start", "cmd_start"), ("help", "cmd_help"), ("duel", "cmd_duel"),
        ("result", "cmd_result"), ("streak", "cmd_streak"), ("leaderboard", "cmd_leaderboard"),
        ("watch", "cmd_watch"), ("digest", "cmd_digest"), ("stop", "cmd_stop"),
    )

    def build_app(self, token: str):
        from telegram import Update
        from telegram.ext import Application, CallbackQueryHandler, CommandHandler, TypeHandler
        app = Application.builder().token(token).build()
        app.add_handler(TypeHandler(Update, self.on_any_update), group=-1)
        for name, attr in self.COMMANDS:
            app.add_handler(CommandHandler(name, getattr(self, attr)))
        app.add_handler(CallbackQueryHandler(self.on_callback, pattern=r"^dd:"))
        # Off by default. /duel and its dd: callbacks stay as they are.
        if scam.flag_on():
            app.add_handler(CommandHandler("scam", self.cmd_scam))
            app.add_handler(CallbackQueryHandler(self.on_sos, pattern=r"^sos:"))
        return app


async def _start_health(bot: DailyDuelBot, host: str, port: int):
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


async def _approval_digest_loop(bot: DailyDuelBot, app, stop: asyncio.Event) -> None:
    """Weekly send. The chat id is whatever this process has seen since it started."""
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=bot.poll_sec)
        except asyncio.TimeoutError:
            pass
        if stop.is_set():
            return
        try:
            from bot.utils.outbound import reply_safe

            async def _send(chat_id: str, text: str) -> None:
                await app.bot.send_message(chat_id=int(chat_id), text=reply_safe(text))

            await bot.dispatch_due(_send)
        except Exception as exc:
            log.warning("approval digest tick failed: %s", type(exc).__name__)


async def serve(app, bot: DailyDuelBot, *, host: str = HEALTH_HOST_DEFAULT,
                port: int = HEALTH_PORT_DEFAULT) -> None:
    from bot.core.boot_health import format_stop_handlers, install_stop_handlers, poller_should_restart
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    digest_task: Optional[asyncio.Task[None]] = None

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
        digest_task = asyncio.create_task(_approval_digest_loop(bot, app, stop))
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
            except Exception as exc:
                log.debug("watchdog tick failed: %s", type(exc).__name__)
    finally:
        bot.state["polling"] = False
        if digest_task is not None:
            digest_task.cancel()
            try:
                await digest_task
            except asyncio.CancelledError:
                pass
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


def run_daily_duel_bot() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    load_env()
    problem = token_problem() or salt_problem()
    if problem:
        print(f"ERROR: {problem}", file=sys.stderr)
        log.error("%s", problem)
        sys.exit(EXIT_NOT_CONFIGURED)
    path = pid_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    stale = stale_pid(path)
    if stale is not None:
        log.info("stopping the earlier daily duel bot (pid %s) before polling", stale)
        stop_stale(stale)
    Path(path).write_text(str(os.getpid()), encoding="utf-8")
    port = int(os.environ.get("DAILY_DUEL_HEALTH_PORT", str(HEALTH_PORT_DEFAULT)) or HEALTH_PORT_DEFAULT)
    host = os.environ.get("DAILY_DUEL_HEALTH_HOST", HEALTH_HOST_DEFAULT) or HEALTH_HOST_DEFAULT
    salt = _clean_secret(os.environ.get("DAILY_DUEL_ID_SALT", ""))
    bot = DailyDuelBot(salt=salt, store=CallStore(Path(state_dir()) / "calls.sqlite"))
    app = bot.build_app(bot_token())
    try:
        asyncio.run(serve(app, bot, host=host, port=port))
    finally:
        try:
            if Path(path).read_text(encoding="utf-8").strip() == str(os.getpid()):
                os.unlink(path)
        except Exception:
            pass


if __name__ == "__main__":  # pragma: no cover - the entry point
    run_daily_duel_bot()
