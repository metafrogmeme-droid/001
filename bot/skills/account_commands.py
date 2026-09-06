"""The account-linking command group — a slice out of the handler.

`/connect`, `/disconnect`, `/exchange`, `/livebalance` and `/linkwallet` are
a user's own account: their exchange keys (Fernet-encrypted under the master
key), their balance, their wallet link. `/setexchange`, `/setgateway` and
`/vault` are the operator's: the engine's own keys into the secrets vault,
and the card that says which key protects what. Their behaviour is covered
where it always was (`test_setexchange_admin`,
`test_credential_keys_are_named_honestly`, `test_unreadable_credentials_are_not_reported_present`,
`test_balance_fields_beyond_free_are_three_valued`, `test_telegram_commands`);
`tests/test_handler_mixins.py` holds this class to the split's rules.

A mixin, not a leaf: every method reads `self.users` or `self.engine`, and
every reply — including the ones that say "nothing was stored" — goes
through `self._send`, the F-15 redaction chokepoint. The wallet-challenge
store is the mixin's own state, created on first use.
"""
from __future__ import annotations

import asyncio
import html
import time
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import ContextTypes

from bot.config import CONFIG
from bot.skills.command_guard import guard
from bot.utils.exc_text import _safe_exc_text
from bot.utils.logger import audit, system_log
from bot.utils.trade_filter import ORPHAN_PREFIXES as _ORPHAN_PREFIXES
from bot.warroom.warroom_bot import _bar

if TYPE_CHECKING:
    from bot.core.engine import RuneClawEngine
    from bot.utils.user_store import UserStore


class AccountCommands:
    """A user's own account, and the operator's keys. Host contract below."""

    #: Pending wallet-link challenges, created on first use by
    #: `_wallet_challenges` — the mixin's own state.
    _wallet_challenge_store: dict

    if TYPE_CHECKING:
        # Provided by TelegramHandler, and ONLY declared here — declarations,
        # never bodies; tests/test_handler_mixins.py checks every name against
        # what the handler really defines.
        engine: RuneClawEngine
        users: UserStore

        async def _send(self, update: Update, text: str,
                        reply_markup=None, edit: bool = False) -> None: ...

        async def _guard(self, update: Update, command: str = "", ctx=None) -> bool: ...

        def _is_admin(self, update: Update) -> bool: ...

        def _get_tg_id(self, update: Update) -> str: ...

    async def _cmd_connect(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/connect [venue] <credentials…> — link YOUR OWN exchange account.

        Bitget (default): /connect <api_key> <api_secret> <passphrase>
        Hyperliquid:      /connect hyperliquid <wallet_address> <agent_private_key>

        The message carrying the keys is deleted immediately and the keys are
        encrypted at rest. Places no orders."""
        # Delete the secret-bearing message FIRST — before any gate can return
        # — so keys never linger in chat history even on a denied/rate-limited call.
        try:
            if update.message:
                await update.message.delete()
        except Exception as del_exc:
            system_log.warning(
                "Failed to delete /connect message containing API keys: %s — "
                "keys may be visible in chat history", del_exc)

        # Private chat only: never accept secrets in a group.
        if update.effective_chat and update.effective_chat.type != "private":
            await self._send(update,
                "⚠️ Send <code>/connect</code> in a <b>private chat</b> only "
                "— never in a group.")
            return

        if not await self._guard(update, "status"):
            return

        from bot.core.exchange_credentials import (
            get_credential_store, validate_venue_credentials, basic_venue_format_ok,
            valid_venue_ids, _VENUE_FIELDS,
        )
        from bot.core.venues import get_venue

        def _venue_label(v: str) -> str:
            try:
                return getattr(get_venue(v), "display_name", None) or v.title()
            except Exception:
                return v.title()

        # Optional leading venue token; default Bitget so the legacy form
        # (/connect <key> <secret> <pass>) is byte-identical.
        args = list(ctx.args or [])
        venue = "bitget"
        if args and args[0].lower() in valid_venue_ids():
            venue = args[0].lower()
            args = args[1:]

        required = _VENUE_FIELDS.get(venue, ())
        if len(args) != len(required):
            # Data-driven usage across every connectable venue.
            def _usage(v: str) -> str:
                fields = " ".join(f"&lt;{f}&gt;" for f in _VENUE_FIELDS[v])
                cmd = "/connect" if v == "bitget" else f"/connect {v}"
                return f"<b>{_venue_label(v)}</b> — <code>{cmd} {fields}</code>"
            lines = "\n".join(_usage(v) for v in valid_venue_ids())
            await self._send(update,
                "<b>Link your own exchange account</b>\n\n" + lines + "\n\n"
                "• Bitget keys need USDT-M futures (read + trade); Bybit/BingX "
                "must be in ONE-WAY mode; Hyperliquid uses an <b>agent</b> "
                "(API) wallet key — never your main wallet key.\n"
                "• Keys are <b>encrypted at rest</b> and never logged.\n"
                "• This message is deleted immediately after you send it.\n"
                "• Use <code>/exchange</code> to check status, "
                "<code>/disconnect</code> to remove.")
            return

        fields = {k: args[i].strip() for i, k in enumerate(required)}
        label = _venue_label(venue)
        if not basic_venue_format_ok(venue, fields):
            await self._send(update,
                f"🔴 Those don't look like valid {label} credentials "
                "(empty, contain spaces, or wrong shape). Nothing was stored.")
            return

        await self._send(update,
            f"⏳ Validating your {label} credentials (read-only balance check)…")
        ok, detail = await validate_venue_credentials(
            venue, fields, sandbox=CONFIG.exchange.sandbox)
        if not ok:
            await self._send(update,
                f"🔴 Could not authenticate with {label}. Nothing was stored.\n"
                f"<code>{html.escape(detail)}</code>\n\n"
                "Check the credentials and their trading permissions.")
            return

        # The balance probe above proves the key can READ. It says nothing about
        # whether the key can move the money OUT, which is the fact this product's
        # non-custodial promise actually rests on — and until now nothing in the
        # codebase asked. Bitget answers it on a read-only endpoint; other venues
        # have no equivalent yet, so they render the honest "not readable" line
        # rather than a silence that reads as reassurance.
        scope: dict = {"withdraw": "unknown", "ip_allowlist": None}
        if venue == "bitget":
            try:
                from bot.core.exchange_credentials import probe_bitget_key_scope
                scope = await probe_bitget_key_scope(
                    fields["api_key"], fields["api_secret"], fields["passphrase"],
                    sandbox=CONFIG.exchange.sandbox)
            except Exception:
                pass   # stays "unknown" — the line below says so out loud

        tg_id = self._get_tg_id(update)
        store = get_credential_store()
        store.set_venue(tg_id, venue, fields)
        # Drop any cached executor so the next trade rebuilds with the new keys.
        try:
            self.engine.invalidate_user_executor(tg_id)
        except Exception:
            pass
        audit(system_log, f"User linked own {label} account via /connect",
              action="connect", result="OK",
              data={"user": tg_id, "venue": venue, "fingerprint": store.fingerprint(tg_id)})
        from bot.guardian.authority_preflight import withdraw_notice
        await self._send(update,
            f"🟢 <b>{label} account linked</b>\n\n"
            f"Key: <code>{store.fingerprint(tg_id)}</code>\n"
            f"Balance: {html.escape(detail)}\n\n"
            f"{withdraw_notice(scope.get('withdraw'))}\n\n"
            "Your keys are encrypted at rest. Per-user live trading is not yet "
            "enabled — you'll be notified when it goes live. Use "
            "<code>/exchange</code> to review or <code>/disconnect</code> to remove.")

    async def _cmd_setexchange(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/setexchange [venue] <credentials…> — ADMIN ONLY.

        Bitget (default): /setexchange <api_key> <api_secret> <passphrase>
        Bybit / BingX:    /setexchange bybit <api_key> <api_secret>

        Sets — or repairs — the OPERATOR credentials the engine trades on.
        This began as the recovery path for a wiped .env that lost
        BITGET_PASSPHRASE — the engine account then can't authenticate ("bitget
        requires password"), leaving live positions unprotected. The keys are
        validated read-only, stored ENCRYPTED in the secrets vault (survives
        future .env wipes), and the operator exchange client is rebuilt live —
        no restart needed. The message carrying the keys is deleted
        immediately. Places no orders.

        Bybit and BingX joined because .env was the ONLY place their operator
        keys could live. The vault MIRRORS .env; it never replaces it, so a key
        that only ever arrived through .env stays in the clear there for as
        long as the file exists. A key set here is vault-only: delete the
        lines from .env and it is restored from the vault on every boot.
        """
        # Delete the secret-bearing message FIRST, before any gate can return.
        try:
            if update.message:
                await update.message.delete()
        except Exception as del_exc:
            system_log.warning(
                "Failed to delete /setexchange message with keys: %s", del_exc)

        # Admin only — these are the OPERATOR keys the whole engine trades on.
        if not self._is_admin(update):
            return
        # Private chat only: never accept secrets in a group.
        if update.effective_chat and update.effective_chat.type != "private":
            await self._send(update,
                "⚠️ Send <code>/setexchange</code> in a <b>private chat</b> only.")
            return

        # venue -> (the env names the vault stores, the CONFIG.exchange fields
        # the running engine reads). Kept HERE, next to the store call, so the
        # names the vault gets and the names the engine reads cannot drift.
        OPERATOR_VENUES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
            "bitget": (("BITGET_API_KEY", "BITGET_API_SECRET", "BITGET_PASSPHRASE"),
                       ("api_key", "api_secret", "passphrase")),
            "bybit": (("BYBIT_API_KEY", "BYBIT_API_SECRET"),
                      ("bybit_api_key", "bybit_api_secret")),
            "bingx": (("BINGX_API_KEY", "BINGX_API_SECRET"),
                      ("bingx_api_key", "bingx_api_secret")),
        }
        from bot.core.exchange_credentials import (
            _VENUE_FIELDS, basic_venue_format_ok, validate_venue_credentials,
        )
        args = [a.strip() for a in (ctx.args or [])]
        venue = "bitget"
        if args and args[0].lower() in OPERATOR_VENUES:
            venue = args[0].lower()
            args = args[1:]
        env_names, cfg_fields = OPERATOR_VENUES[venue]
        required = _VENUE_FIELDS[venue]
        label = venue.title() if venue != "bingx" else "BingX"

        if len(args) != len(required):
            def _usage(v: str) -> str:
                fields = " ".join(f"&lt;{f}&gt;" for f in _VENUE_FIELDS[v])
                cmd = "/setexchange" if v == "bitget" else f"/setexchange {v}"
                return f"• <code>{cmd} {fields}</code>"
            await self._send(update,
                "<b>Set the operator (engine) exchange credentials</b>\n\n"
                "The account the bot itself trades on — recovers it after a "
                "wiped .env, or takes it out of .env for good.\n"
                + "\n".join(_usage(v) for v in OPERATOR_VENUES) + "\n\n"
                "• Validated read-only, then <b>encrypted in the vault</b> "
                "(survives future .env wipes).\n"
                "• The engine client is rebuilt live — no restart.\n"
                "• This message is deleted immediately.")
            return

        fields = {k: args[i] for i, k in enumerate(required)}
        if not basic_venue_format_ok(venue, fields):
            await self._send(update,
                f"🔴 Those don't look like valid {label} keys "
                "(empty, contain spaces, or too short). Nothing was stored.")
            return

        await self._send(update,
            f"⏳ Validating the operator {label} keys (read-only balance check)…")
        ok, detail = await validate_venue_credentials(
            venue, fields, sandbox=CONFIG.exchange.sandbox)
        if not ok:
            await self._send(update,
                f"🔴 Could not authenticate with {label}. Nothing was changed.\n"
                f"<code>{html.escape(detail)}</code>")
            return

        # 1) Persist ENCRYPTED to the vault + inject into os.environ (so a future
        #    redeploy restores them before CONFIG reads the environment).
        try:
            from bot.core.secrets_vault import store_secrets
            store_secrets({env: fields[f] for env, f in zip(env_names, required)})
        except Exception as exc:
            system_log.error("setexchange: vault store failed: %s", exc)

        # 2) Hot-patch the live CONFIG (frozen dataclass) so every operator code
        #    path sees the corrected creds without a restart, then drop the
        #    cached operator exchange client so it rebuilds authenticated.
        try:
            _ex_cfg = CONFIG.exchange
            for attr, f in zip(cfg_fields, required):
                object.__setattr__(_ex_cfg, attr, fields[f])
        except Exception as exc:
            system_log.error("setexchange: CONFIG hot-patch failed: %s", exc)
        try:
            self.engine.live_executor._exchange = None
            self.engine._invalidate_live_balance_cache()
        except Exception as exc:
            system_log.warning("setexchange: executor rebuild hint failed: %s", exc)

        audit(system_log, f"Admin set operator {label} credentials via /setexchange",
              action="setexchange", result="OK", data={"venue": venue})
        await self._send(update,
            f"🟢 <b>Operator {label} credentials updated</b>\n\n"
            f"Balance: {html.escape(detail)}\n\n"
            "Stored <b>encrypted</b> in the vault (survives .env wipes) and the "
            "engine client was rebuilt. If these keys are also in .env you can "
            "delete those lines now — the vault restores them on every boot. "
            "Run <code>/start</code> — equity should read live now.")

    async def _cmd_setgateway(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/setgateway <secret> — ADMIN ONLY.

        Repairs the WEB_GATEWAY_SECRET the website uses to reach this bot's
        chat/trade gateway. A wiped .env that predates vault coverage loses it,
        and web chat then errors with "gateway_disabled" while the bot trades
        fine. The secret is stored ENCRYPTED in the vault (survives future .env
        wipes) and takes effect immediately — the gateway re-reads the
        environment per request, no restart needed. The message carrying the
        secret is deleted immediately. Must equal the website's value."""
        # Delete the secret-bearing message FIRST, before any gate can return.
        try:
            if update.message:
                await update.message.delete()
        except Exception as del_exc:
            system_log.warning(
                "Failed to delete /setgateway message with secret: %s", del_exc)

        if not self._is_admin(update):
            return
        if update.effective_chat and update.effective_chat.type != "private":
            await self._send(update,
                "⚠️ Send <code>/setgateway</code> in a <b>private chat</b> only.")
            return

        args = ctx.args or []
        if len(args) != 1:
            await self._send(update,
                "<b>Set the website↔bot gateway secret</b>\n\n"
                "Re-pairs web chat + web trading after a wiped .env.\n"
                "<code>/setgateway &lt;secret&gt;</code>\n\n"
                "• Must be the SAME value as <code>WEB_GATEWAY_SECRET</code> "
                "on the website (&gt;=32 chars).\n"
                "• Stored <b>encrypted</b> in the vault; effective immediately, "
                "no restart.\n"
                "• This message is deleted immediately.")
            return

        secret = args[0].strip()
        if len(secret) < 32 or any(c.isspace() for c in secret):
            await self._send(update,
                "🔴 The gateway secret must be at least <b>32 characters</b> "
                "with no spaces. Nothing was stored.")
            return

        try:
            from bot.core.secrets_vault import store_secrets
            store_secrets({"WEB_GATEWAY_SECRET": secret})
        except Exception as exc:
            system_log.error("setgateway: vault store failed: %s", exc)
            await self._send(update,
                "🔴 Could not store the secret. Check the logs.")
            return

        audit(system_log, "Admin set the web gateway secret via /setgateway",
              action="setgateway", result="OK")
        await self._send(update,
            "🟢 <b>Web gateway secret updated</b>\n\n"
            "Stored <b>encrypted</b> in the vault (survives .env wipes) and "
            "live now — no restart needed. If web chat still shows a gateway "
            "error, make sure the website's <code>WEB_GATEWAY_SECRET</code> "
            "is the exact same value.")

    async def _cmd_vault(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/vault — secret-protection status (admin). Names only, never values.

        Shows every vault-managed secret with whether it is live in the
        environment and whether an ENCRYPTED copy exists in the vault (i.e.
        survives the next redeploy/.env wipe). Anything set once via
        /setexchange, /setgateway, or /setllm is stored and auto-restored on
        every future boot — this command is how you verify nothing is left
        unprotected."""
        if not self._is_admin(update):
            return
        from bot.core.secrets_vault import vault_status
        status = vault_status()
        if not status:
            await self._send(update,
                "🔴 Vault unavailable (disabled or crypto missing) — secrets "
                "will NOT survive a redeploy.")
            return
        FIX = {
            "BITGET": "/setexchange", "BYBIT": "/setexchange bybit",
            "BINGX": "/setexchange bingx", "WEB_GATEWAY_SECRET": "/setgateway",
            "TELEGRAM_BOT_TOKEN": ".env only",
            "BOT_SYNC_SECRET": ".env (auto-vaults from env)",
            "WEB_CREDS_KEY": "not needed — website submissions are sealed to this bot's own key",
        }
        def _fix_for(key: str) -> str:
            for prefix, cmd in FIX.items():
                if key.startswith(prefix):
                    return cmd
            return "/setllm <provider> <key>" if key.endswith("_API_KEY") else ".env"
        protected, env_only, absent = [], [], []
        for key, s in sorted(status.items()):
            if s["vault"]:
                protected.append(key)
            elif s["env"]:
                env_only.append(key)  # present but would die with .env
            else:
                absent.append(key)
        SEP = "─" * 16
        lines = [f"🔐 <b>Secrets vault</b>\n{SEP}"]
        lines.append(f"🟢 <b>Protected</b> (encrypted, survive redeploys): "
                     f"<code>{len(protected)}</code>")
        if env_only:
            # Present in .env and not yet in the vault: mirrored on the next
            # boot, but the .env copy is the one that stays in the clear —
            # so each line names the command that makes it vault-only.
            lines.append("🟡 <b>Env-only</b> (mirrored to the vault on next boot; "
                         "the .env copy stays until set via the command):\n"
                         + "\n".join(f"- <code>{k}</code> → {_fix_for(k)}" for k in env_only))
        # WEB_CREDS_KEY is the legacy shared-key path for website submissions;
        # unset is the normal state now that they are sealed to the bot's own
        # key, so it is not reported as missing.
        used_absent = [k for k in absent
                       if not k.startswith(("HYPERLIQUID", "BYBIT", "BINGX",
                                            "ONCHAIN", "RUNECLAW", "WEB_CREDS_KEY"))]
        if used_absent:
            lines.append("🔴 <b>Missing</b> (set once, protected forever):\n"
                         + "\n".join(f"- <code>{k}</code> → {_fix_for(k)}"
                                     for k in used_absent))
        lines.append(f"{SEP}\n<i>Anything set via /setexchange, /setgateway, "
                     "or /setllm is stored encrypted and restored on every "
                     "boot — you never re-enter it.</i>")
        # WHICH KEY PROTECTS WHAT. This card listed names, and a name alone
        # was misread: "WEB_CREDS_KEY missing" was taken to mean the exchange
        # keys users had linked were sitting in the clear. Three stores,
        # three answers, stated where the operator looks for them.
        #
        # Website submissions are sealed to the bot's OWN key since 2026-09
        # (bot/utils/creds_sealing.py; the website holds only the public
        # half), so WEB_CREDS_KEY is the legacy shared-key path: accepted if
        # set, not needed if unset. Reading the key file can generate it on a
        # first boot, which is RSA keygen — off the loop.
        try:
            from bot.utils import creds_sealing as _sealing
            _kid = await asyncio.to_thread(_sealing.kid)
            seal_line = (f"🟢 sealed to this bot's own key "
                         f"(<code>{html.escape(str(_sealing.private_key_path()))}</code>, "
                         f"kid <code>{_kid}</code>), published to the website over the sync "
                         "channel — the website cannot read what it stores.")
        except Exception as exc:
            seal_line = (f"🔴 this bot's sealing key could not be read "
                         f"({_safe_exc_text(exc)}) — the website's connect form refuses "
                         "until it can.")
        web_key = status.get("WEB_CREDS_KEY", {})
        web_line = ("<code>WEB_CREDS_KEY</code> is set — the legacy shared-key envelope "
                    "is still accepted."
                    if web_key.get("env") or web_key.get("vault") else
                    "<code>WEB_CREDS_KEY</code> is unset — not needed; it was the legacy "
                    "shared-key path. Keys already linked are unaffected.")
        lines.append(
            f"{SEP}\n<b>What encrypts what</b>\n"
            "• Keys users link (/connect, the website): Fernet under the "
            "master key (<code>RUNECLAW_SECRETS_KEY</code> / "
            "<code>data/.exchange_secret.key</code>) — always, whatever else is set.\n"
            "• Website submissions in transit to the bot: " + seal_line + " " + web_line + "\n"
            "• The operator's own keys: this vault mirrors .env, it does not "
            "replace it — a key that only ever came from .env stays in the clear "
            "there. Set it with /setexchange and delete the .env lines to make "
            "it vault-only.")
        await self._send(update, "\n".join(lines))

    async def _cmd_disconnect(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/disconnect — remove YOUR linked Bitget account credentials."""
        if not await self._guard(update, "status"):
            return
        from bot.core.exchange_credentials import get_credential_store
        tg_id = self._get_tg_id(update)
        existed = get_credential_store().delete(tg_id)
        # Drop any cached executor bound to the now-deleted credentials.
        try:
            self.engine.invalidate_user_executor(tg_id)
        except Exception:
            pass
        if existed:
            audit(system_log, "User removed own Bitget account via /disconnect",
                  action="disconnect", result="OK", data={"user": tg_id})
            await self._send(update,
                "🔴 <b>Bitget account unlinked</b>\n"
                "Your encrypted keys were deleted. Use <code>/connect</code> to relink.")
        else:
            await self._send(update,
                "No Bitget account is linked. Use <code>/connect</code> to link one.")

    async def _cmd_exchange(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/exchange — show YOUR linked-account status (never reveals keys)."""
        if not await self._guard(update, "status"):
            return
        from bot.core.exchange_credentials import get_credential_store
        tg_id = self._get_tg_id(update)
        store = get_credential_store()
        bitget_env = ("DEMO trading (BITGET_SANDBOX=true)"
                      if CONFIG.exchange.sandbox else "PRODUCTION")
        if not store.has(tg_id):
            await self._send(update,
                "<b>Your exchange link</b>\n\n"
                "Status: <code>not connected</code>\n"
                f"Environment: <code>{bitget_env}</code>\n\n"
                "Link your own Bitget account with\n"
                "<code>/connect &lt;api_key&gt; &lt;api_secret&gt; &lt;passphrase&gt;</code>")
            return
        per_user = getattr(CONFIG, "per_user_live_enabled", False)
        live_state = "enabled" if per_user else "preparing (not yet live)"
        await self._send(update,
            "<b>Your exchange link</b>\n\n"
            "Status: <code>connected</code>\n"
            f"Key: <code>{store.fingerprint(tg_id)}</code>\n"
            f"Environment: <code>{bitget_env}</code>\n"
            f"Per-user live trading: <code>{live_state}</code>\n\n"
            "Use <code>/disconnect</code> to remove your keys.")

    @guard("portfolio")
    async def _cmd_livebalance(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/livebalance — check real USDT balance + spot holdings on Bitget."""
        try:
            # Route to the CALLER's own account: if they linked one via /connect,
            # /livebalance must show THAT account's balance — not the shared
            # operator account (which is what a linked user would otherwise see as
            # $0.00). Viewing your own balance is read-only, so this works
            # regardless of PER_USER_LIVE_ENABLED (that flag gates order
            # placement, not balance viewing). Falls back to the operator
            # executor when the caller has no linked account.
            tg_id = self._get_tg_id(update)
            balance_exec = self.engine.balance_view_executor(tg_id)
            is_operator_view = balance_exec is self.engine.live_executor
            bal = await balance_exec.fetch_balance()
            # LIVE FIX: update engine's cached balance so /status shows fresh data
            # — but ONLY for the operator account, never a per-user linked one
            # (that cache feeds operator-account equity/telemetry).
            if is_operator_view and ("error" not in bal or bal.get("total", 0) > 0):
                self.engine._live_balance_cache = bal
                # MUST be time.monotonic(): the TTL check in get_live_equity()
                # and the staleness watchdog both diff this against monotonic.
                # A wall-clock stamp here made the cache read as fresh FOREVER
                # (epoch >> monotonic), freezing live sizing equity after one
                # /livebalance and blinding the stale-balance alert.
                self.engine._live_balance_cache_ts = time.monotonic()
            # RC-2026-015. Was `bal.get("total", 0)` and friends, which cannot
            # tell a reported 0.0 from a read that never happened -- so
            # fetch_balance's own error return ({"error": ..., "total": 0, ...})
            # printed Cash $0.00 / Used $0.00 / Equity $0.00 / NET $0.00, a
            # complete account statement with no error text. The reading is
            # three-valued now; see bot/formatters/live_balance.py for why the
            # fix is here and not in fetch_balance (bot/main.py classifies its
            # startup auth halt on that dict).
            from bot.formatters.live_balance import (
                money,
                read_balance,
                render_balance_block,
            )
            _reading = read_balance(bal)
            holdings = _reading.holdings

            # Fetch prices and compute portfolio value. `holdings` is None when
            # nothing was read -- NOT [], which would claim the venue answered
            # and the account holds no spot.
            spot_items = []
            total_usd = _reading.total
            if holdings:
                exchange = await balance_exec._get_exchange()
            for h in sorted(holdings or [], key=lambda x: x["asset"]):
                asset = h["asset"]
                qty = h["total"]
                symbol = f"{asset}/USDT"
                usd_val = 0.0
                price = 0.0
                try:
                    ticker = await exchange.fetch_ticker(symbol)
                    price = float(ticker.get("last", 0))
                    usd_val = qty * price
                    # None + float raises. An unreadable equity stays
                    # unreadable; a priced holding cannot rescue it into a
                    # number, and a partial total printed as a whole one is
                    # the shape the table warns about.
                    if total_usd is not None:
                        total_usd += usd_val
                except Exception:
                    pass
                spot_items.append({"asset": asset, "qty": qty, "price": price, "usd": usd_val})

            # Live executor stats — same account the balance was read from.
            executor = balance_exec
            open_pos = executor.open_positions
            closed_pos = executor.closed_positions
            # Filter out adopted/injected trades and never-filled orders (canceled/
            # expired/price_drift/rejected close at $0 PnL) for consistency with
            # the Performance view.
            from bot.utils.trade_filter import NON_TRADE_CLOSE_REASONS as _non_trade_reasons_bal
            user_closed = [t for t in closed_pos
                           if not any(getattr(t, "trade_id", "").startswith(p) for p in _ORPHAN_PREFIXES)
                           and getattr(t, "close_reason", "") not in _non_trade_reasons_bal]
            adopted_closed = [t for t in closed_pos
                              if any(getattr(t, "trade_id", "").startswith(p) for p in _ORPHAN_PREFIXES)]
            # /balance is /portfolio's un-cured sibling: the same store, the
            # same three sums, the same `or 0`. /portfolio now routes these
            # through realized_totals() and prints "unknown ⚠️" when nothing
            # could be priced; this card still folded every unpriced close in
            # at break-even and printed the result beside "{n} trades", so the
            # total read as covering all of them.
            #
            # scripts/rebuild_closed_trades.py sets commission to null on EVERY
            # rebuilt row, so "absent fee" is not hypothetical here.
            from bot.formatters.realized_totals import realized_totals
            _bal = realized_totals(user_closed)
            _bal_adopted = realized_totals(adopted_closed)
            _realized_known = _bal["net"] is not None
            _fees_known = _bal["fees"] is not None
            realized_pnl = _bal["net"] if _realized_known else 0.0
            total_fees = _bal["fees"] if _fees_known else 0.0
            adopted_pnl = _bal_adopted["net"]
            exposure = executor.total_exposure_usd

            # PnL sign
            pnl_sign = "+" if realized_pnl >= 0 else ""
            # ⚪ already meant "exactly break-even" here, so an unreadable total
            # would have taken the SAME glyph as a measured flat book. The
            # unknown case gets ⚠️ and the word, not a colour it has not earned.
            pnl_icon = ("\u26a0\ufe0f" if not _realized_known
                        else "\u26aa" if realized_pnl == 0
                        else ("\U0001f7e2" if realized_pnl > 0 else "\U0001f534"))
            _realized_str = (f"${pnl_sign}{realized_pnl:.2f}" if _realized_known
                             else "unknown")
            _fees_str = f"${total_fees:.2f}" if _fees_known else "unknown"
            _partial = (f" \u2014 {_bal['unpriced']} of {_bal['total']} unpriced"
                        if _realized_known and _bal["unpriced"] else "")

            # "Used" from exchange only counts filled positions in cross margin.

            # Header — name WHICH account this is so a linked user isn't
            # confused about seeing their own balance vs the operator's.
            account_label = "BITGET PORTFOLIO"
            if not is_operator_view:
                try:
                    from bot.core.exchange_credentials import get_credential_store
                    _fp = get_credential_store().fingerprint(tg_id)
                except Exception:
                    _fp = ""
                account_label = (f"YOUR BITGET ACCOUNT · {_fp}" if _fp
                                 else "YOUR BITGET ACCOUNT")
            SEP = "─" * 16
            lines = [
                f"💰 <b>{account_label}</b>",
                f"{SEP}",
                f"   {pnl_icon}  Net PnL: <code>{_realized_str}</code> (fees: {_fees_str}){_partial}",
                "",
            ]
            # The higher of venue-reported `used` and bot-tracked exposure is
            # only meaningful when both are readings; the renderer decides.
            lines += render_balance_block(_reading, exposure=exposure,
                                          equity=total_usd, sep=SEP)

            # Spot holdings section. Skipped entirely when nothing was read:
            # an omitted section says "we cannot tell you", an empty one says
            # "you hold none", and only one of those is true here.
            real_holdings = [s for s in spot_items if s["usd"] >= 0.01]
            dust_holdings = [s for s in spot_items if 0 < s["usd"] < 0.01]

            if real_holdings:
                lines.append("")
                lines.append("📦 <b>Spot Holdings</b>")
                lines.append(SEP)
                for s in sorted(real_holdings, key=lambda x: -x["usd"]):
                    pct = (s["usd"] / total_usd * 100) \
                        if (total_usd or 0) > 0 else 0
                    bar = _bar(pct / 100, 1.0, 8)
                    lines.append(
                        f"- <b>{s['asset']}</b>  "
                        f"<code>{s['qty']:.8g}</code>  "
                        f"<code>${s['usd']:.2f}</code>  "
                        f"{bar} {pct:.0f}%"
                    )
                if dust_holdings:
                    lines.append(f"- <i>+{len(dust_holdings)} dust</i>")

            # PnL waterfall
            lines.append("")
            lines.append("📈 <b>PnL Waterfall</b>")
            lines.append(SEP)
            lines.append("- Realized: <code>"
                         + (f"${pnl_sign}{realized_pnl:.4f}" if _realized_known
                            else "unknown") + "</code>")
            lines.append(f"- Exposure: <code>${exposure:,.2f}</code>")
            lines.append(SEP)
            # The headline number on the card. `${None:,.2f}` raises, and the
            # nearest except would have swallowed the whole reply -- so being
            # honest here without money() would have DELETED the card rather
            # than corrected it.
            lines.append(f"- <b>NET: <code>{money(total_usd)}</code></b>")

            # Footer — use filtered trade count (consistent with Performance)
            n_trades = len(user_closed)
            n_open = len(open_pos)
            trade_word = "trade" if n_trades == 1 else "trades"
            pos_word = f"{n_open} open" if n_open > 0 else "no open positions"
            lines.append("")
            lines.append(f"<i>{n_trades} {trade_word} • {pos_word}</i>")
            if adopted_closed:
                lines.append(
                    f"<i>⚠️ Excluded {len(adopted_closed)} adopted orphan"
                    f"{'s' if len(adopted_closed) != 1 else ''}"
                    + (f" ({'+' if adopted_pnl >= 0 else ''}{adopted_pnl:.2f})</i>"
                       if adopted_pnl is not None else " (P&L not recorded)</i>")
                )

            await self._send(update, "\n".join(lines))
        except Exception as exc:
            await self._send(update,
                             f"\u274c Balance fetch failed: {_safe_exc_text(exc)}")

    WALLET_CHALLENGE_TTL_SECONDS = 300

    def _wallet_challenges(self) -> dict:
        """Pending wallet-link challenges, keyed by telegram id.

        In-memory and deliberately not persisted: an unanswered challenge is
        worthless, and a restart should invalidate every outstanding nonce.
        """
        existing = getattr(self, "_wallet_challenge_store", None)
        if existing is None:
            existing = {}
            self._wallet_challenge_store = existing
        return existing

    @guard("help")
    async def _cmd_linkwallet(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Link (or clear) the Solana wallet used by the $RCLAW tier gate.

        Linking is a two-step challenge-response: an address alone proves
        nothing, so `/linkwallet <address>` only issues a nonce and
        `/linkwallet verify <signature>` commits it after checking an ed25519
        signature. Without that, any user could paste any staker's address and
        inherit their tier.

        Read-only throughout: the address is used solely for public balance and
        stake reads, and signing the challenge authorizes no transaction.
        """
        from bot.token import solana_verify as sv

        args = (ctx.args if ctx and getattr(ctx, "args", None) else [])
        uid = self._get_tg_id(update)

        if args and args[0].lower() in ("clear", "none", "off"):
            self.users.set_sol_wallet(uid, None)
            self._wallet_challenges().pop(uid, None)
            await self._send(update, "✅ Solana wallet unlinked.")
            return

        if not args:
            current = self.users.get_sol_wallet(uid)
            verified = self.users.is_sol_wallet_verified(uid) if hasattr(
                self.users, "is_sol_wallet_verified") else False
            if current:
                status = (
                    "◎ <b>Linked Solana wallet:</b> <code>" + html.escape(current) + "</code>\n"
                    + ("✅ <i>Ownership verified.</i>\n" if verified else
                       "⚠️ <i>Unverified — this wallet grants no tier until you prove "
                       "you control it.</i>\n")
                )
            else:
                status = "◎ <b>No Solana wallet linked.</b>\n"
            await self._send(update,
                status
                + "Usage: <code>/linkwallet &lt;address&gt;</code>, then "
                  "<code>/linkwallet verify &lt;signature&gt;</code>, or "
                  "<code>/linkwallet clear</code>\n"
                  "<i>Read-only — used only to read your public $RCLAW balance/stake.</i>")
            return

        # ── step 2: commit a pending challenge ────────────────────────────────
        if args[0].lower() == "verify":
            if len(args) < 2:
                await self._send(update,
                    "\U0001f534 Usage: <code>/linkwallet verify &lt;base64 signature&gt;</code>")
                return
            pending = self._wallet_challenges().get(uid)
            if not pending or pending["expires_at"] < time.time():
                self._wallet_challenges().pop(uid, None)
                await self._send(update,
                    "\U0001f534 No pending verification (or it expired). "
                    "Start again with <code>/linkwallet &lt;address&gt;</code>.")
                return
            message = sv.challenge_message(pending["address"], pending["nonce"])
            if not sv.verify_signed_message(message, args[1].strip(), pending["address"]):
                # Burn the nonce: a failed attempt must not be retryable against
                # the same challenge.
                self._wallet_challenges().pop(uid, None)
                await self._send(update,
                    "\U0001f534 Signature did not verify for that address. "
                    "Challenge discarded — run <code>/linkwallet &lt;address&gt;</code> to retry.")
                return
            self._wallet_challenges().pop(uid, None)
            if not self.users.set_sol_wallet(uid, pending["address"], verified=True):
                await self._send(update, "\U0001f534 Could not link that wallet (unknown user).")
                return
            await self._send(update,
                f"✅ Solana wallet verified and linked: "
                f"<code>{html.escape(pending['address'])}</code>\n"
                "<i>Read-only. Used to read your public $RCLAW stake for tier access.</i>")
            return

        # ── step 1: issue a challenge ─────────────────────────────────────────
        addr = args[0].strip()
        # Decode to exactly 32 bytes rather than shape-matching: a 32-44 char
        # base58 string can decode to 23-33 bytes, so a regex alone admits values
        # that are not public keys.
        if not sv.is_valid_address(addr):
            await self._send(update,
                "\U0001f534 That is not a valid Solana address "
                "(must be base58 decoding to 32 bytes).")
            return
        nonce = sv.new_nonce()
        self._wallet_challenges()[uid] = {
            "address": addr,
            "nonce": nonce,
            "expires_at": time.time() + self.WALLET_CHALLENGE_TTL_SECONDS,
        }
        message = sv.challenge_message(addr, nonce)
        await self._send(update,
            "◎ <b>Prove you control this wallet.</b>\n\n"
            "Sign this exact message in your wallet "
            "(Phantom/Backpack: Settings → Sign Message):\n\n"
            f"<pre>{html.escape(message)}</pre>\n"
            "Then send:\n<code>/linkwallet verify &lt;base64 signature&gt;</code>\n\n"
            f"<i>Expires in {self.WALLET_CHALLENGE_TTL_SECONDS // 60} minutes. "
            "Signing authorizes no transaction and moves no funds.</i>")
