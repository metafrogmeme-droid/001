"""
RUNECLAW -- Multi-user Telegram middleware.
File: bot/skills/user_middleware.py

Every incoming message passes through require_registered().
  - Unregistered chat_ids get a friendly prompt to register + /link
  - Registered users get their personal UserContext injected
  - All bot commands become per-user automatically
"""

from __future__ import annotations
import asyncio
import functools
import json
import logging
import os
import urllib.request
import urllib.error
from telegram import Update
from telegram.ext import ContextTypes

from bot.db.models import (
    get_user_by_chat_id, get_user_settings, get_user_portfolio,
    save_user_portfolio, link_telegram,
    unlink_telegram, UserSettings,
)
from bot.db.models import User as DBUser

from bot.utils.i18n import t
from bot.utils.site_url import site_url

log = logging.getLogger(__name__)

WEBSITE_URL = site_url()
REGISTER_URL = WEBSITE_URL


_LANG_STORE_TTL = 60.0     # seconds
_lang_store: tuple = (0.0, None)


def _user_lang(chat_id) -> str:
    """Resolve the user's UI language for i18n. Fails safe to English.

    The language preference lives in the JSON UserStore (keyed by Telegram id),
    a separate store from the SQLite records this middleware uses — so we read
    it directly here (read-only; UserStore() just loads data/users.json).

    Cached for a minute rather than constructed per call. This is on the message
    path and `cmd_sync` alone called it three times, so it was re-reading and
    re-parsing users.json several times per command — and UserStore._load now
    moves the file aside on an unreadable read (a disk blip, an EINTR), so every
    extra construction is another chance to trip a rescue this caller has no
    business triggering.

    A minute, not forever: /lang writes through the handler's OWN store
    instance, so a cache here is a second copy that cannot see the change. One
    minute keeps /lang feeling immediate while cutting the reads by orders of
    magnitude. This is a display preference; a stale minute costs nothing.
    """
    global _lang_store
    try:
        import time as _time

        from bot.utils.i18n import get_user_lang
        from bot.utils.user_store import UserStore
        now = _time.monotonic()
        stamped_at, store = _lang_store
        if store is None or (now - stamped_at) > _LANG_STORE_TTL:
            store = UserStore()
            _lang_store = (now, store)
        return get_user_lang(store, str(chat_id))
    except Exception:
        return "en"


def _ensure_local_user(user_id: int, email: str, plan: str) -> None:
    """Create a stub user in the bot's local SQLite if it doesn't exist yet.
    This bridges the website (MySQL) and bot (SQLite) user stores."""
    from bot.db.models import get_db
    with get_db() as db:
        existing = db.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        if existing:
            return
        # Insert stub user with a placeholder password hash (not usable for login)
        db.execute(
            "INSERT INTO users (id, email, password_hash, plan) VALUES (?, ?, ?, ?)",
            (user_id, email, "website-linked:no-local-password", plan),
        )
        db.execute("INSERT INTO user_settings (user_id) VALUES (?)", (user_id,))
        db.execute("INSERT INTO user_portfolio (user_id) VALUES (?)", (user_id,))


# -- UserContext: injected into every handler --------------------------------

class UserContext:
    """Lightweight per-request user state."""

    def __init__(self, user: DBUser, settings: UserSettings, portfolio: dict):
        self.user = user
        self.settings = settings
        self.portfolio = portfolio

    @property
    def user_id(self) -> int:
        return self.user.id

    @property
    def chat_id(self) -> str:
        return self.user.telegram_chat_id

    @property
    def equity(self) -> float:
        return self.portfolio["equity"]

    def save_portfolio(self) -> None:
        save_user_portfolio(
            self.user_id,
            self.portfolio["equity"],
            self.portfolio["daily_pnl"],
            self.portfolio["positions"],
            self.portfolio["trade_history"],
        )


def require_registered(handler):
    """
    Decorator -- gates any command behind registration + Telegram link.

    Usage:
        @require_registered
        async def cmd_scan(update, context, uc: UserContext):
            ...do scan for uc.user...
    """
    @functools.wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_chat or not update.message:
            return
        # Only allow private chats for user-gated commands
        if update.effective_chat.type != "private":
            await update.message.reply_text(
                "RUNECLAW commands are available in private chat only."
            )
            return
        chat_id = str(update.effective_chat.id)
        user = get_user_by_chat_id(chat_id)

        if user is None or not user.is_active:
            await update.message.reply_text(
                "Welcome to RUNECLAW\n\n"
                "You need a free account to use this bot.\n\n"
                f"1. Register at: {REGISTER_URL}\n"
                "2. Then come back and send: /link <your-token>",
                parse_mode=None,
            )
            return

        settings = get_user_settings(user.id)
        portfolio = get_user_portfolio(user.id)
        uc = UserContext(user, settings, portfolio)

        try:
            await handler(update, context, uc)
        except Exception as exc:
            log.exception(f"Handler error for user {user.id}: {exc}")
            await update.message.reply_text(
                f"Something went wrong: {type(exc).__name__}\n"
                "The team has been notified. Try again in a moment.",
                parse_mode=None,
            )

    return wrapper


# -- /link command -----------------------------------------------------------

async def cmd_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /link <token>
    Called by the user after they register on the website and copy their token.
    """
    chat_id = str(update.effective_chat.id)
    username = update.effective_user.username or ""

    # Already linked?
    existing = get_user_by_chat_id(chat_id)
    if existing:
        await update.message.reply_text(t("link_already_linked", _user_lang(chat_id)))
        return

    # Need token
    if not context.args:
        await update.message.reply_text(t("link_prompt", _user_lang(chat_id), url=REGISTER_URL))
        return

    token = context.args[0].strip()

    # Validate token via website API (tokens live in MySQL, not local SQLite)
    api_url = f"{WEBSITE_URL}/api/auth/validate-token"
    payload = json.dumps({"token": token, "chat_id": chat_id}).encode()
    # X-Bot-Secret. This endpoint no longer answers an anonymous caller: it
    # consumes the link token AND binds telegram_id to the account, which is a
    # write, and the bot is its only legitimate caller. Same header and same
    # variable as every other bot->web call (bot/utils/website_sync.py).
    #
    # Sent unconditionally. An old server ignores an unknown header, which is
    # why the deploy order is bot FIRST; reversed, every /link gets a 403.
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "RUNECLAW-Bot/1.0",
    }
    bot_secret = os.getenv("BOT_SYNC_SECRET", "")
    if bot_secret:
        headers["X-Bot-Secret"] = bot_secret
    else:
        # Named, not silent, and the header is omitted rather than sent blank.
        # A blank secret fails the constant-time comparison exactly like a wrong
        # one, so the operator would read "invalid bot secret" for what is
        # really "this bot has none configured" — and the user would be told
        # their token is bad, which is the one thing that is fine.
        log.error("BOT_SYNC_SECRET is not set, so /link will be refused by the "
                  "website with 403. This is a bot configuration fault, not a "
                  "bad token.")
    req_obj = urllib.request.Request(
        api_url,
        data=payload,
        headers=headers,
        method="POST",
    )
    def _validate_token():
        """Blocking. Runs on a worker thread — see below."""
        with urllib.request.urlopen(req_obj, timeout=10) as resp:
            return json.loads(resp.read().decode())

    try:
        # OFF THE LOOP. This is a 10s synchronous urlopen inside a coroutine,
        # on the loop that also runs SL/TP monitoring and the WS feed — so an
        # unreachable website meant any user typing /link could freeze position
        # protection for ten seconds. Same shape as the engine's web pulls,
        # found by re-running the search across the whole package after the
        # engine ones were fixed; an engine-only scan would never have reached
        # it. `to_thread` re-raises in the awaiting coroutine, so the HTTPError
        # handling below is unchanged.
        result = await asyncio.to_thread(_validate_token)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            await update.message.reply_text(t("link_token_invalid", _user_lang(chat_id), url=REGISTER_URL))
            return
        log.error(f"Token validation HTTP error: {e.code}")
        await update.message.reply_text(t("link_validate_failed", _user_lang(chat_id)))
        return
    except Exception as exc:
        log.error(f"Token validation error: {exc}")
        await update.message.reply_text(t("link_unreachable", _user_lang(chat_id)))
        return

    # A 200 with the wrong shape used to raise KeyError straight past every
    # handler here into the generic "Something went wrong" — which reads as a
    # bad token and sends the user round the loop again, generating tokens
    # against a website that is answering but not with this.
    try:
        user_id = int(result["user_id"])
        email = str(result["email"])
    except (KeyError, TypeError, ValueError):
        log.error("validate-token returned an unexpected body: %r",
                  sorted(result) if isinstance(result, dict) else type(result))
        await update.message.reply_text(t("link_validate_failed", _user_lang(chat_id)))
        return
    plan = result.get("plan", "free")

    # Ensure a matching user record exists in local SQLite (website uses MySQL,
    # bot uses SQLite -- we create a stub so FK constraints are satisfied)
    _ensure_local_user(user_id, email, plan)

    # Link in local SQLite so bot commands work
    success = link_telegram(user_id, chat_id, username)
    if not success:
        await update.message.reply_text(t("link_other_account", _user_lang(chat_id)))
        return

    # Initial sync: push current portfolio state to website
    try:
        from bot.utils.website_sync import sync_in_background
        portfolio = get_user_portfolio(user_id)
        sync_in_background(user_id, portfolio.get("equity", 800), [], [])
    except Exception as exc:
        log.warning(f"Initial sync failed: {exc}")

    await update.message.reply_text(
        t("link_success", _user_lang(chat_id), email=email, plan=plan,
          url=REGISTER_URL))


# -- /unlink command ---------------------------------------------------------

async def cmd_unlink(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Disconnect this Telegram from the linked RUNECLAW account.

    A link has TWO sides. This used to clear only the bot's local
    ``user_telegram`` row and then say "Unlinked from {email}. Your data is
    preserved." — while the website kept ``telegram_linked = TRUE`` and the
    stored ``telegram_id``, which is exactly the pair its credential and
    live-control routes gate on. So a user who believed they had disconnected
    could still have exchange-key submissions pointed at this chat.

    Now the website is told first, and the reply says which halves actually
    completed. A website that cannot be reached is reported as unknown, not as
    done: an unreadable result is not a result.
    """
    chat_id = str(update.effective_chat.id)
    lang = _user_lang(chat_id)
    user = get_user_by_chat_id(chat_id)
    if not user:
        await update.message.reply_text(t("unlink_not_linked", lang))
        return

    try:
        from bot.utils.website_sync import unlink_telegram_on_website
        website = unlink_telegram_on_website(user.id, chat_id)
    except Exception as exc:                       # never block the local half
        log.warning(f"Website unlink failed: {exc}")
        website = None

    # The local row goes regardless. Leaving the bot linked because the website
    # was unreachable would mean /unlink did nothing at all — the worse of the
    # two partial states, since this side is what the bot itself reads.
    unlink_telegram(user.id)

    if website is True:
        await update.message.reply_text(
            t("unlink_success", lang, email=user.email))
    elif website is False:
        await update.message.reply_text(
            t("unlink_partial_refused", lang, email=user.email))
    else:
        await update.message.reply_text(
            t("unlink_partial_unknown", lang, email=user.email))


# -- /me command -------------------------------------------------------------

@require_registered
async def cmd_me(update: Update, context: ContextTypes.DEFAULT_TYPE,
                 uc: UserContext) -> None:
    """Show the user's account info."""
    pf = uc.portfolio
    s = uc.settings
    await update.message.reply_text(
        t("me_account", _user_lang(str(update.effective_chat.id)),
          email=uc.user.email, plan=uc.user.plan,
          equity=f"{pf['equity']:.2f}", pnl=f"{pf['daily_pnl']:.2f}",
          trades=len(pf['trade_history']), llm=s.llm_provider,
          notif=('on' if s.notifications_on else 'off')),
        parse_mode="HTML",
    )


# -- /sync command -----------------------------------------------------------

@require_registered
async def cmd_sync(update: Update, context: ContextTypes.DEFAULT_TYPE,
                   uc: UserContext) -> None:
    """Manually sync portfolio data to the website dashboard."""
    try:
        from bot.utils.website_sync import sync_portfolio
        pf = uc.portfolio
        positions = pf.get("positions", [])
        history = pf.get("trade_history", [])
        success = sync_portfolio(uc.user_id, pf["equity"], positions, history)
        if success:
            await update.message.reply_text(
                t("sync_success", _user_lang(str(update.effective_chat.id)),
                  equity=f"{pf['equity']:.2f}", positions=len(positions),
                  trades=len(history), url=REGISTER_URL))
        else:
            await update.message.reply_text(t("sync_failed", _user_lang(str(update.effective_chat.id))))
    except Exception as exc:
        log.error(f"Sync command error: {exc}")
        await update.message.reply_text(t("sync_failed", _user_lang(str(update.effective_chat.id))))

