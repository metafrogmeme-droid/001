"""
RUNECLAW User Store — file-backed user management with roles and tiers.
Persists to data/users.json. Thread-safe with file locking.

Roles control access (what you CAN do):
  admin > trader > viewer > pending

Tiers control features (what you GET):
  admin > elite > pro > basic

New users are auto-approved as trader/basic with paper trading.
Only admins (or users explicitly granted) can trade live.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from bot.compat import UTC
from pathlib import Path
from typing import Optional

from bot.utils.logger import audit, system_log

from bot.utils.atomic_write import atomic_write_json

# ── Roles: access control ──────────────────────────────────────
# Order is most-privileged first; /users renders in this order.
ROLES = ("admin", "trader", "paper", "viewer", "pending")

# Permissions whose commands mutate state SHARED BY EVERY ACCOUNT — the kill
# switch, the resume, the scan universe. Not a taste judgement; each is derived
# from what the handlers under it actually call, and
# tests/test_self_admission_is_not_vouched.py re-derives this set from the
# source on every run rather than trusting the list below:
#
#   halt   /halt /pause /emergency_stop and the destructive inline callbacks →
#          engine.risk.emergency_halt(), every per-user RiskEngine halted,
#          engine._pending_ideas cleared, engine._transition(HALTED)
#   reset  /reset /resume → engine.reset_circuit_breaker_all(), which by its own
#          docstring resets "the shared engine AND every per-user RiskEngine"
#          and clears engine._halted
#   mode   /mode → RUNTIME.asset_universe = ..., the universe every account's
#          scans run against
#
# `run` was a candidate and is NOT here. It writes engine._pending_ideas — but
# so do analyze_asset and pro_scan, and `scan` is in the VIEWER set, so the idea
# book is already shared by everyone who can scan. Removing `run` alone would be
# a refactor bought with no safety.
OPERATOR_CONTROL_PERMISSIONS = frozenset({"halt", "reset", "mode"})

# A user who let themselves in through PAPER_AUTO_ACCEPT gets this role, and an
# admin's /approve grants "trader". They were the SAME role, which is the whole
# of H4: the door opened for the Arena on-ramp handed every stranger who
# messaged the bot the permission set a vouched-for teammate holds — including
# `reset`, whose /reset clears the operator's tripped circuit breaker globally.
#
# `authorize()` clamps to this role whenever the admitting party is
# SELF_ADMISSION_BY, so the separation does not depend on every call site
# remembering it.
SELF_ADMISSION_ROLE = "paper"
SELF_ADMISSION_BY = "auto-accept"


def is_vouchable(telegram_id) -> bool:
    """Whether an admin could EVER have approved this id by hand.

    Both admin admission surfaces — ``/approve`` and the ``admit:`` inline
    callback — take a raw id typed by a human and refuse anything non-numeric,
    because Telegram ids are numeric and a typo must not conjure a phantom
    account. Web-only identities (``web:<n>``, provisioned by the gateway on
    first request) therefore cannot reach either one.

    That makes this predicate load-bearing OUTSIDE those two handlers. It is
    the reason ``scripts/migrate_self_admitted_roles.py`` can say a ``web:``
    account carrying no ``admitted_by`` was provisioned by the door rather than
    merely predating the stamp — for a numeric id those two are genuinely
    indistinguishable, and for a web id only one of them is possible. It lives
    here, and the handlers call it, so that claim rests on the same function
    they enforce instead of on an inference about their source.

    ASCII is checked as well as digit-ness, which the two `isdigit()` calls
    this replaces did not. `"٣٤".isdigit()` and `"²".isdigit()` are both True,
    so `/approve ٣٤` opened a record under a key no Telegram id can ever equal
    — a phantom account, permanently unreachable by the person it names.
    """
    s = str(telegram_id)
    return s.isascii() and s.isdigit()

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin": {"*"},  # everything
    # "lang" is in every role including pending: /lang is documented under
    # "Start here" for all users, and a person who cannot read the interface
    # cannot ask for permission to change its language. It sets a display
    # preference and touches nothing else.
    # `exposure`, `networth`, `research` and `rwa` were invented at the
    # @guard(...) decorator and never added here, so the permission string
    # existed and NO role held it: only admin (which holds "*") could run four
    # commands the catalog documents for everyone. All four are read-only and
    # either caller-scoped (`fetch_exposure(self._get_tg_id(update))`,
    # /networth's own docstring says "the caller's own") or pure market data
    # (/research a symbol, /rwa a sector radar) — nothing shared, nothing
    # mutable — so the PERMISSION was the error, not the catalogue.
    #
    # `token` (the contract detective) joins them on the same reasoning: it
    # reads public chain data, decides nothing, and writes nothing. It does
    # spend the shared explorer quota, which /research and /rwa already do
    # through their own upstreams — if that budget ever needs defending it
    # should be defended by a rate limit, not by hiding a safety check from
    # the users most likely to be handed a scam address.
    "trader": {
        "lang",
        "start", "help", "dashboard", "scan", "deepscan", "analyze", "portfolio",
        "trade", "risk", "status", "rejected", "halt", "reset", "macro",
        "backtest", "walkforward", "journal", "costs", "run", "learn",
        "patterns", "proposals", "optimize", "mode", "playbook",
        "exposure", "networth", "research", "rwa", "token",
        # /mystrategy: a trader's own tighten-only confirm gate — it can only
        # REFUSE that trader's confirms, touches nothing shared, so it belongs
        # to exactly the role that can confirm trades.
        "mystrategy",
    },
    # Self-admission (PAPER_AUTO_ACCEPT). "trader" minus OPERATOR_CONTROL_
    # PERMISSIONS, written out rather than computed as a set difference,
    # because the two directions fail differently and only one of them fails
    # safe. Derived, a permission added to "trader" would land here too — which
    # is exactly how a stranger got `reset`. Written out, a new trader
    # permission is WITHHELD until somebody decides, and the new feature being
    # invisible to self-admitted users is a complaint, not a breach.
    #
    # The maintenance cost of writing it out is paid by a test, not by
    # remembering: test_self_admission_is_not_vouched pins
    # `trader - paper == OPERATOR_CONTROL_PERMISSIONS` exactly, so adding to one
    # set without deciding about the other fails loudly instead of drifting.
    "paper": {
        "lang",
        "start", "help", "dashboard", "scan", "deepscan", "analyze", "portfolio",
        "trade", "risk", "status", "rejected", "macro",
        "backtest", "walkforward", "journal", "costs", "run", "learn",
        "patterns", "proposals", "optimize", "playbook",
        "exposure", "networth", "research", "rwa", "token",
        "mystrategy",
    },
    "viewer": {
        "lang",
        "start", "help", "dashboard", "scan", "deepscan", "status", "risk",
        "portfolio", "macro", "journal", "costs", "learn", "patterns",
        "exposure", "networth", "research", "rwa", "token",
    },
    # "journal" STAYS here even though /journal moved to an operator group.
    # It is not /journal's permission alone — `/daily_report` is `@guard("journal")`
    # too, and it is genuinely user-facing. Dropping the string to "tidy up"
    # after moving the catalogue entry silently revoked /daily_report from every
    # trader and viewer; the test caught it. /journal is restricted by its own
    # inline `if not self._is_admin(update)`, which is the layer that actually
    # decides. A role permission is necessary, not sufficient.
    "pending": {"start", "help", "lang"},
}

# ── Tiers: feature gating ──────────────────────────────────────
# Each tier inherits all features from lower tiers.
# Tier hierarchy: basic < pro < elite < admin
TIERS = ("basic", "pro", "elite", "admin")

TIER_FEATURES: dict[str, set[str]] = {
    "basic": {
        # Free tier: all features in paper mode (no live trading)
        "paper_trading",
        "scan", "deepscan",
        "analyze",
        "dashboard",
        "portfolio",
        "risk_status",
        "macro_view",
        "backtest",
        "walkforward",
        "journal",
        "patterns",
        "proposals",
        "optimize",
        "strategy_presets",
        "chart_alerts",
        "order_flow",
        "priority_signals",
        "early_access",
    },
    "pro": {
        # Pro tier: same as basic for now (reserved for future differentiation)
        "paper_trading",
        "scan", "deepscan",
        "analyze",
        "dashboard",
        "portfolio",
        "risk_status",
        "macro_view",
        "backtest",
        "walkforward",
        "journal",
        "patterns",
        "proposals",
        "optimize",
        "strategy_presets",
        "chart_alerts",
        "order_flow",
        "priority_signals",
        "early_access",
    },
    "elite": {
        # Elite tier: everything + live trading eligible
        "paper_trading",
        "live_trading_eligible",  # can be granted live by admin
        "scan", "deepscan",
        "analyze",
        "dashboard",
        "portfolio",
        "risk_status",
        "macro_view",
        "backtest",
        "walkforward",
        "journal",
        "patterns",
        "proposals",
        "optimize",
        "strategy_presets",
        "chart_alerts",
        "order_flow",
        "priority_signals",
        "early_access",
    },
    "admin": {
        "*",  # everything
    },
}

# Default tier for new auto-approved users
DEFAULT_TIER = "basic"
# Default role for new auto-approved users. Every caller of register() leaves
# auto_role empty, so this one name covers BOTH self-provisioning doors: a
# stranger messaging the bot on Telegram and a stranger signing up on the
# website (bot/web/user_gateway.py). Neither has been vouched for by a human,
# so neither gets the vouched-for role.
#
# The web door was found first and patched at the TRANSPORT: `halt` is absent
# from _WEB_SKILL_PERMISSION, with a comment saying the map cannot include it
# *because* "Web ids are auto-provisioned with DEFAULT_AUTO_ROLE, which holds
# the 'halt' permission". That workaround stays — defence in depth — but it is
# no longer the only thing standing between a signup and the shared kill
# switch, which is the wrong place for that load to sit.
DEFAULT_AUTO_ROLE = SELF_ADMISSION_ROLE

# F-14: a SENSITIVE command (trade/halt/reset/mode/golive/approve/revoke) is
# refused after this much inactivity, so a hijacked-but-idle chat cannot move
# money. Named because it was an unexplained 86400 inside has_permission and
# the message it produced named neither the rule nor the remedy.
SESSION_MAX_AGE_SECONDS = 86400

# `touch()` writes to disk at most this often. Every guarded command records
# activity; without a floor that is a users.json rewrite per message.
TOUCH_PERSIST_SECONDS = 300


log = logging.getLogger("runeclaw.user_store")


class UserStore:
    """JSON-file backed user database with roles and tiers."""

    def __init__(self, path: str | Path = "data/users.json") -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._users: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        """An unreadable store is not an empty one.

        This carried the exact defect bot/core/exchange_credentials.py was
        fixed for, and which tests/test_credential_store_never_self_destructs
        documents: an unreadable file became `{}`, and the next `_save()` —
        triggered by the very next person to message the bot, since register()
        saves — wrote that empty map over the real one. Every user gone, on a
        single transient read.

        The catch includes **OSError**, which is what moves this from exotic to
        likely: a disk-full, a permissions blip, an EINTR. No corruption
        required, and the loss is silent because registering a user looks like
        it worked.

        A MISSING file is legitimately empty — first boot — and still saves.
        """
        self._load_failed = False
        if not self._path.exists():
            self._users = {}
            return
        # A ZERO-BYTE file counts as fresh, not damaged. It is ambiguous in
        # principle — a truncated write looks identical to a `touch` — but
        # _save() now goes through atomic_write (temp file + rename), so the
        # destination is never partially written and 0 bytes means nobody has
        # written it yet. Callers legitimately create the path first:
        # tests/test_web_live_gate.py hands UserStore an mkstemp path, and
        # treating that as corruption moved the file aside mid-test.
        try:
            if self._path.stat().st_size == 0:
                self._users = {}
                return
        except OSError:
            pass
        try:
            with open(self._path) as f:
                self._users = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            # Preserve the bytes before anything can touch the path again.
            _kept = ""
            try:
                _damaged = self._path.with_suffix(self._path.suffix + ".corrupt")
                if not _damaged.exists():
                    os.replace(str(self._path), str(_damaged))
                    _kept = f" Original preserved at {_damaged.name}."
            except OSError:
                _kept = " Could not preserve the original."
            self._load_failed = True
            self._users = {}
            log.critical(
                "users.json unreadable (%s) — REFUSING to write this store "
                "until it is repaired, so a registration cannot overwrite the "
                "real user list with an empty file.%s Users will appear "
                "unregistered until this is resolved.",
                exc.__class__.__name__, _kept)

    def _save(self) -> None:
        # Fail-closed: never persist a map built from a FAILED read. Writing
        # here is what turns one unreadable file into permanent user loss.
        #
        # Unlike the credential store this does NOT raise. register() runs on
        # the message path for every user, and an exception there would take
        # the bot down rather than degrade it. Refusing the write preserves the
        # file on disk; the CRITICAL log is the alarm.
        if getattr(self, "_load_failed", False):
            log.critical("refusing to save users.json: the store failed to "
                         "load, so writing would destroy the real user list")
            return
        atomic_write_json(self._path, self._users, indent=2, default=str)

    # ── Public API ─────────────────────────────────────────────

    def get(self, telegram_id: int | str) -> Optional[dict]:
        """Get user record or None."""
        with self._lock:
            return self._users.get(str(telegram_id))

    def register(self, telegram_id: int | str, name: str = "",
                 auto_role: str = "") -> dict:
        """Register a new user or return existing.

        New users are auto-approved as trader/basic with paper trading.
        Never overwrites role/tier on existing users.
        """
        if not auto_role:
            auto_role = DEFAULT_AUTO_ROLE
        if auto_role not in ROLES:
            auto_role = DEFAULT_AUTO_ROLE
        key = str(telegram_id)
        with self._lock:
            if key in self._users:
                # Update last_seen and name
                self._users[key]["last_seen"] = datetime.now(UTC).isoformat()
                if name and not self._users[key].get("name"):
                    self._users[key]["name"] = name
                # Backfill tier for legacy users without one
                if "tier" not in self._users[key]:
                    role = self._users[key].get("role", "pending")
                    self._users[key]["tier"] = "admin" if role == "admin" else DEFAULT_TIER
                # Auto-upgrade legacy PENDING users on interaction — users who
                # registered before auto-approve existed and never had a
                # decision made about them.
                #
                # NOT users an admin revoked. Both states are `role: "pending",
                # authorized: False`, so this branch could not tell them apart
                # and cheerfully re-authorized anyone /revoke had just removed:
                # /start called register(), so revoking someone lasted exactly
                # until their next message. `revoked_at` is the distinction —
                # written by revoke(), absent on a legacy record — and a
                # deliberate decision outranks a migration.
                if (self._users[key].get("role") == "pending"
                        and not self._users[key].get("revoked_at")):
                    self._users[key]["role"] = auto_role
                    self._users[key]["authorized"] = True
                    self._users[key]["can_trade_live"] = False
                    if "tier" not in self._users[key] or self._users[key]["tier"] == "pending":
                        self._users[key]["tier"] = DEFAULT_TIER
                    audit(system_log,
                          f"Legacy pending user auto-upgraded on interaction: {key}",
                          action="user_auto_upgrade", result="OK")
                self._save()
                return self._users[key]

            # New user: auto-approve as trader with basic tier
            user = {
                "telegram_id": key,
                "name": name,
                "role": auto_role,
                "tier": DEFAULT_TIER,
                "authorized": True,  # auto-approved
                "can_trade_live": False,  # paper only by default
                "sim_opt_in": False,  # per-user PAPER (sim) practice mode opt-in
                "created_at": datetime.now(UTC).isoformat(),
                "last_seen": datetime.now(UTC).isoformat(),
            }
            self._users[key] = user
            self._save()
            audit(system_log,
                  f"New user auto-approved: {key} ({name}) role={auto_role} tier={DEFAULT_TIER}",
                  action="user_auto_approve", result="OK")
            return user

    def authorize(self, telegram_id: int | str, role: str = "trader",
                  by: str = "") -> bool:
        """Promote a user to an authorized role. Returns True on success.

        ``by`` is the Telegram id of the ADMIN performing the approval. When it
        is given, the record is also stamped ``admitted_at``/``admitted_by`` —
        the flag ``is_admitted()`` reads and the only thing besides the env
        allowlist that opens the bot's gate (see TelegramHandler._is_allowlisted).

        The parameter is not cosmetic and it is not optional by accident. F-2
        closed the hole where any /start made a stranger an authorized trader,
        by gating access on env vars alone. That left `authorized: True` — which
        register() sets for everyone — meaning nothing, and left /approve unable
        to grant the access it announces. Splitting the two restores /approve
        while keeping the hole shut: register() cannot name an approving admin,
        so it cannot admit anyone.

        ``by`` ALSO decides how much can be granted. F-2's remaining half was
        that it stamps ``admitted_by`` with whatever it is handed, so
        ``by="auto-accept"`` — a door that opens with no human on the other side
        — could name any role in ROLES. It named "trader", and a stranger's
        first message bought them /reset on the operator's circuit breaker.
        Self-admission is clamped to SELF_ADMISSION_ROLE here rather than at the
        call site, for the same reason F-2 put the admission stamp here: a rule
        that lives in one funnel cannot be forgotten by the next caller.

        For the same reason, an admitting party may only be recorded against an
        id a human could have typed (``is_vouchable``). Web-only identities are
        provisioned by the gateway and can never be approved by hand, and a
        migration relies on that being true of every path, not just the two
        that check it themselves.
        """
        key = str(telegram_id)
        if by and by != SELF_ADMISSION_BY and not is_vouchable(key):
            # A HUMAN ADMISSION CAN ONLY BE STAMPED ON AN ID A HUMAN COULD HAVE
            # TYPED. Both admin surfaces already refuse a non-numeric id, so
            # this changes nothing today — it makes the refusal structural
            # instead of duplicated, for the same reason the clamp below lives
            # here: a rule that lives in one funnel cannot be forgotten by the
            # next caller, and the next caller is what would break it.
            #
            # What depends on it is off in scripts/migrate_self_admitted_roles:
            # it reads an absent `admitted_by` on a "web:<n>" record as PROOF
            # the gateway provisioned that account, and downgrades it, where
            # the same absence on a numeric id is left alone as unknowable.
            # That inference is only sound while this is the one door.
            audit(system_log,
                  f"Refused to record an admission for un-vouchable id {key!r}",
                  action="admission_refused", result="DENIED")
            return False
        if by == SELF_ADMISSION_BY and role != SELF_ADMISSION_ROLE:
            # Clamp DOWN and record it. Refusing outright would leave the caller
            # having asked for access and received silence, which on the
            # auto-accept path means the newcomer is turned away with no reason
            # — the friction this door exists to remove.
            audit(system_log,
                  f"Self-admission asked for role={role!r}; "
                  f"granted {SELF_ADMISSION_ROLE!r}",
                  action="self_admission_clamped", result="CLAMPED")
            role = SELF_ADMISSION_ROLE
        if role not in ROLES or role == "pending":
            return False
        with self._lock:
            if key not in self._users:
                # Auto-create if approving unknown ID
                tier = "admin" if role == "admin" else DEFAULT_TIER
                self._users[key] = {
                    "telegram_id": key,
                    "name": "",
                    "role": role,
                    "tier": tier,
                    "authorized": True,
                    "can_trade_live": role == "admin",
                    "created_at": datetime.now(UTC).isoformat(),
                    "last_seen": datetime.now(UTC).isoformat(),
                }
            else:
                self._users[key]["role"] = role
                self._users[key]["authorized"] = True
                # Auto-set tier for admin role
                if role == "admin":
                    self._users[key]["tier"] = "admin"
                    self._users[key]["can_trade_live"] = True
            # Approving somebody clears a previous revocation — otherwise
            # /revoke followed by /approve leaves a record that reads as both.
            self._users[key].pop("revoked_at", None)
            if by:
                self._users[key]["admitted_at"] = datetime.now(UTC).isoformat()
                self._users[key]["admitted_by"] = str(by)
            self._save()
            audit(system_log,
                  f"User authorized: {key} as {role}"
                  + (f" by {by}" if by else " (no admitting admin recorded)"),
                  action="user_authorize", result="OK")
            return True

    def revoke(self, telegram_id: int | str) -> bool:
        """Revoke a user's access (set to pending)."""
        key = str(telegram_id)
        with self._lock:
            if key not in self._users:
                return False
            self._users[key]["role"] = "pending"
            self._users[key]["authorized"] = False
            self._users[key]["can_trade_live"] = False
            # Mark this as a DECISION, not an un-migrated legacy record.
            # Without it, register()'s legacy-pending auto-upgrade cannot tell
            # the two apart and re-authorizes them on their next message.
            self._users[key]["revoked_at"] = datetime.now(UTC).isoformat()
            # Drop the admission too, or /revoke would leave the gate open: the
            # allowlist reads admitted_by, not role.
            self._users[key].pop("admitted_at", None)
            self._users[key].pop("admitted_by", None)
            # Let them ask again — the one-shot request flag is what stops a
            # stranger spamming the operator, not a permanent ban.
            self._users[key].pop("access_requested_at", None)
            self._save()
            audit(system_log, f"User revoked: {key}",
                  action="user_revoke", result="OK")
            return True

    def is_authorized(self, telegram_id: int | str) -> bool:
        """Check if user exists and is authorized."""
        user = self.get(telegram_id)
        return user is not None and user.get("authorized", False)

    # ── Admission: an admin deliberately let this person in ────

    def is_admitted(self, telegram_id: int | str) -> bool:
        """Whether an ADMIN explicitly approved this user onto the bot.

        Distinct from ``authorized``, which register() sets for every first
        contact and therefore cannot gate anything. Both stamps are required:
        a record carrying only ``admitted_at`` names nobody responsible, and an
        unattributable admission is exactly what F-2 was closed against.
        """
        user = self.get(telegram_id)
        if not user:
            return False
        return bool(user.get("admitted_at") and user.get("admitted_by")
                    and user.get("authorized", False))

    def mark_access_requested(self, telegram_id: int | str) -> bool:
        """Flag that this user has been turned away; True the FIRST time only.

        The operator gets one notification per person, not one per refused
        command — a stranger tapping through the menu would otherwise page them
        twenty times. Cleared by revoke() so a later re-request still lands.
        """
        key = str(telegram_id)
        with self._lock:
            rec = self._users.get(key)
            if rec is None or rec.get("access_requested_at"):
                return False
            rec["access_requested_at"] = datetime.now(UTC).isoformat()
            self._save()
            return True

    def record_referrer(self, telegram_id: int | str, ref_code: str) -> bool:
        """Attribute this user to whoever invited them. First writer wins.

        Write-once on purpose. A referral is a fact about how someone arrived,
        so a later `/start ref_<someone-else>` must not rewrite history — and
        without that rule the field is trivially farmable by resending your own
        link to an existing user.

        Self-referral is refused: the code is the referrer's, and crediting
        yourself is the first thing anyone tries.
        """
        key = str(telegram_id)
        code = str(ref_code or "").strip()
        if not code:
            return False
        with self._lock:
            record = self._users.get(key)
            if record is None or record.get("referred_by"):
                return False
            if code == record.get("referral_code") or code == key:
                return False
            record["referred_by"] = code
            record["referred_at"] = datetime.now(UTC).isoformat()
            self._save()
            audit(system_log, f"User {key} attributed to referrer {code}",
                  action="user_referral", result="OK")
            return True

    def touch(self, telegram_id: int | str) -> None:
        """Record activity for the F-14 session window, persisting it.

        _guard used to refresh ``last_seen`` by mutating the dict in place with
        no save, so the timestamp lived only in memory. Every restart reverted
        each user to whatever was last written — for most people their
        registration — and the first sensitive command after a redeploy was
        refused as a permission problem. The window only means "24h of
        inactivity" if inactivity survives a restart.

        Writes are throttled to TOUCH_PERSIST_SECONDS; a lag of minutes against
        a 24-hour window changes no decision.
        """
        key = str(telegram_id)
        now = datetime.now(UTC)
        with self._lock:
            rec = self._users.get(key)
            if rec is None:
                return
            previous = rec.get("last_seen") or ""
            rec["last_seen"] = now.isoformat()
            due = True
            if previous:
                try:
                    due = ((now - datetime.fromisoformat(previous)).total_seconds()
                           >= TOUCH_PERSIST_SECONDS)
                except (ValueError, TypeError):
                    due = True
            if due:
                self._save()

    # ── Live trading permission ────────────────────────────────

    def web_live_enabled(self, telegram_id: int | str) -> bool:
        """Dedicated per-user opt-in for WEB live trading (web:<id> only).

        Deliberately separate from ``can_trade_live``: that flag stays
        structurally False for web ids (below), so a stale/legacy
        ``can_trade_live`` value can never open the web live path. This is the
        one flag the web live gate reads, and it only ever means anything for a
        web-only identity that also passes every other precondition (operator
        feature switch, own keys, enforce-mode Authority Envelope).
        """
        if not str(telegram_id).startswith("web:"):
            return False
        user = self.get(telegram_id)
        if not user or not user.get("authorized", False):
            return False
        return bool(user.get("web_live_enabled", False))

    def set_web_live_enabled(self, telegram_id: int | str, enabled: bool) -> bool:
        """Set the web live opt-in flag (web:<id> only). Returns True on success."""
        key = str(telegram_id)
        if not key.startswith("web:"):
            return False
        with self._lock:
            if key not in self._users:
                return False
            self._users[key]["web_live_enabled"] = bool(enabled)
            self._save()
            audit(system_log,
                  f"Web live trading {'enabled' if enabled else 'disabled'} for {key}",
                  action="web_live_permission", result="OK")
            return True

    def get_lang(self, telegram_id: int | str):
        """The user's stored UI language, or None if never set (unset signal)."""
        with self._lock:
            u = self._users.get(str(telegram_id))
            return u.get("lang") if isinstance(u, dict) else None

    def set_lang(self, telegram_id: int | str, lang: str) -> bool:
        """Set the user's UI language. Returns True on success."""
        key = str(telegram_id)
        with self._lock:
            if key not in self._users:
                return False
            self._users[key]["lang"] = lang
            self._save()
            audit(system_log, f"Language set to {lang} for {key}",
                  action="set_lang", result="OK")
            return True

    def can_trade_live(self, telegram_id: int | str) -> bool:
        """Check if user is allowed to execute live trades.

        Only admins can trade live by default. Users with explicit
        'can_trade_live' flag override this. Non-admins always get
        paper execution even when the bot is in live mode.
        """
        # Web-only identities ("web:<id>", provisioned by the web gateway)
        # are structurally paper-only — even an explicit flag can't override.
        # (Web live execution rides the SEPARATE web_live_enabled flag + gate.)
        if str(telegram_id).startswith("web:"):
            return False
        user = self.get(telegram_id)
        if not user or not user.get("authorized", False):
            return False
        # Explicit flag takes priority
        if "can_trade_live" in user:
            return bool(user["can_trade_live"])
        # Default: only admins can trade live
        return user.get("role") == "admin"

    def live_trading_revoked(self, telegram_id: int | str) -> bool:
        """True only when an operator has EXPLICITLY turned live trading off.

        Distinct from ``not can_trade_live(...)``, which is also false for
        everyone who was simply never granted it. Once live trading opens to
        every key-holder, "never granted" stops being a denial and only an
        explicit revoke is — so the two questions need separate answers.

        It reads ``live_revoked_at`` and NOT ``can_trade_live is False``, which
        was the first attempt and was wrong: ``register()`` writes
        ``can_trade_live: False`` on every new account as the paper-only
        DEFAULT, so that test called every trader revoked and would have banned
        the entire user base the moment live opened. A default and a decision
        are different facts and the store has to record them separately.
        """
        user = self.get(telegram_id)
        if not user:
            return False
        return bool(user.get("live_revoked_at"))

    def set_live_trading(self, telegram_id: int | str, enabled: bool) -> bool:
        """Grant or revoke live trading permission for a user."""
        key = str(telegram_id)
        with self._lock:
            if key not in self._users:
                return False
            self._users[key]["can_trade_live"] = enabled
            # A DECISION, recorded apart from the flag. `can_trade_live: False`
            # is also what register() writes by default, so the flag alone
            # cannot say whether a human ruled on this account.
            if enabled:
                self._users[key].pop("live_revoked_at", None)
            else:
                self._users[key]["live_revoked_at"] = datetime.now(UTC).isoformat()
            self._save()
            audit(system_log,
                  f"Live trading {'enabled' if enabled else 'disabled'} for user {key}",
                  action="live_trading_permission", result="OK")
            return True

    def max_margin(self, telegram_id: int | str) -> Optional[float]:
        """Operator-set max margin (USD) a user may commit to a single live trade,
        or None if unset. Used by the engine to tighten the per-user position cap."""
        user = self.get(telegram_id)
        if not user:
            return None
        v = user.get("max_margin_usd")
        if v is None:
            return None
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if f > 0 else None

    def set_max_margin(self, telegram_id: int | str, usd: Optional[float]) -> bool:
        """Set (or clear, when usd is None) a user's per-trade max margin cap.
        Returns True on success, False if the user does not exist."""
        key = str(telegram_id)
        with self._lock:
            if key not in self._users:
                return False
            if usd is None:
                self._users[key].pop("max_margin_usd", None)
            else:
                self._users[key]["max_margin_usd"] = float(usd)
            self._save()
            audit(system_log,
                  f"Max margin {'cleared' if usd is None else f'set to ${usd:.2f}'} "
                  f"for user {key}",
                  action="user_max_margin", result="OK")
            return True

    def sim_opt_in(self, telegram_id: int | str) -> bool:
        """Whether this user has opted into PAPER (sim) practice mode. When True
        (and PAPER_SIM_OPT_IN_ENABLED), their confirmed trades are simulated into
        their paper portfolio instead of sent to the exchange."""
        user = self.get(telegram_id)
        if not user or not user.get("authorized", False):
            return False
        return bool(user.get("sim_opt_in", False))

    def set_sim_opt_in(self, telegram_id: int | str, enabled: bool) -> bool:
        """Opt a user into or out of PAPER (sim) practice mode."""
        key = str(telegram_id)
        with self._lock:
            if key not in self._users:
                return False
            self._users[key]["sim_opt_in"] = enabled
            self._save()
            audit(system_log,
                  f"Paper sim mode {'enabled' if enabled else 'disabled'} for user {key}",
                  action="sim_opt_in", result="OK")
            return True

    # ── Tier management ────────────────────────────────────────

    def get_tier(self, telegram_id: int | str) -> str:
        """Get user's current tier. Returns 'basic' for unknown users."""
        user = self.get(telegram_id)
        if not user:
            return DEFAULT_TIER
        return user.get("tier", DEFAULT_TIER)

    def set_tier(self, telegram_id: int | str, tier: str) -> bool:
        """Set a user's tier. Admin only operation."""
        if tier not in TIERS:
            return False
        key = str(telegram_id)
        with self._lock:
            if key not in self._users:
                return False
            old_tier = self._users[key].get("tier", DEFAULT_TIER)
            self._users[key]["tier"] = tier
            self._save()
            audit(system_log,
                  f"User tier changed: {key} {old_tier} -> {tier}",
                  action="tier_change", result="OK")
            return True

    def has_feature(self, telegram_id: int | str, feature: str) -> bool:
        """Check if a user's tier grants access to a specific feature.

        Usage:
            if users.has_feature(uid, "backtest"):
                # run backtest
            else:
                # "Upgrade to Pro to unlock backtesting"
        """
        user = self.get(telegram_id)
        if not user:
            return feature in TIER_FEATURES.get(DEFAULT_TIER, set())
        tier = user.get("tier", DEFAULT_TIER)
        features = TIER_FEATURES.get(tier, set())
        return "*" in features or feature in features

    def get_sol_wallet(self, telegram_id: int | str) -> str | None:
        """Return the user's linked Solana wallet address, or None.

        Used by the $RCLAW token-tier gate (bot/token/tier_gate.py) to read an
        on-chain stake. Draft feature — see docs/TOKEN_ROADMAP.md.
        """
        user = self.get(telegram_id)
        if not user:
            return None
        return user.get("sol_wallet") or None

    def set_sol_wallet(
        self,
        telegram_id: int | str,
        address: str | None,
        *,
        verified: bool = False,
    ) -> bool:
        """Link (or clear, with None) a user's Solana wallet address.

        ``verified`` records that the caller checked an ed25519 signature proving
        control of ``address``. It defaults to False so that any existing call
        site links an **unproven** address, which the tier gate refuses to honour
        (bot/token/tier_gate.py). Linking a different address always clears a
        previous proof — a proof is about one specific key, not about the user.
        """
        key = str(telegram_id)
        with self._lock:
            if key not in self._users:
                return False
            if address:
                self._users[key]["sol_wallet"] = str(address)
                if verified:
                    self._users[key]["sol_wallet_verified_at"] = datetime.now(UTC).isoformat()
                else:
                    self._users[key].pop("sol_wallet_verified_at", None)
            else:
                self._users[key].pop("sol_wallet", None)
                self._users[key].pop("sol_wallet_verified_at", None)
            self._save()
            audit(system_log,
                  f"User Solana wallet {'linked' if address else 'cleared'}: {key}"
                  f"{' (verified)' if address and verified else ''}",
                  action="sol_wallet_link", result="OK")
            return True

    def is_sol_wallet_verified(self, telegram_id: int | str) -> bool:
        """Whether the linked wallet has a recorded ownership proof.

        A record written before wallet verification existed has no timestamp and
        is therefore unverified, which is the intended fail-closed answer.
        """
        user = self.get(telegram_id)
        if not user:
            return False
        return bool(user.get("sol_wallet") and user.get("sol_wallet_verified_at"))

    def tier_label(self, telegram_id: int | str) -> str:
        """Human-readable tier label with icon."""
        tier = self.get_tier(telegram_id)
        labels = {
            "basic": "\U0001f7e2 Basic",
            "pro": "\U0001f535 Pro",
            "elite": "\U0001f7e1 Elite",
            "admin": "\U0001f534 Admin",
        }
        return labels.get(tier, "\U0001f7e2 Basic")

    # ── Command permission check ───────────────────────────────

    def permission_denial(self, telegram_id: int | str,
                          command: str) -> Optional[str]:
        """Why ``command`` is refused for this user, or None when it is allowed.

        ``"role"``           the caller's role does not carry this command
        ``"stale_session"``  the role DOES carry it, but F-14 expires sensitive
                             commands after 24h of inactivity

        Split out of has_permission because every caller printed the role reason
        for both causes. A trader idle for a day was told "your role (trader)
        cannot use /trade" — which is false, the role can — and the message
        named no remedy, so /start (the one thing that fixes it) was unguessable.
        A heuristic is never a verdict; neither is one branch of an `or`.
        """
        user = self.get(telegram_id)
        if not user:
            return None if command in ROLE_PERMISSIONS.get("pending", set()) else "role"
        role = user.get("role", "pending")
        perms = ROLE_PERMISSIONS.get(role, set())
        if "*" not in perms and command not in perms:
            return "role"
        # F-14: session timeout for sensitive commands
        _SENSITIVE_CMDS = {"trade", "halt", "reset", "mode", "golive", "approve", "revoke"}
        if command in _SENSITIVE_CMDS:
            last_seen = user.get("last_seen", "")
            if last_seen:
                try:
                    from datetime import datetime as _dt
                    last_dt = _dt.fromisoformat(last_seen)
                    if (datetime.now(UTC) - last_dt).total_seconds() > SESSION_MAX_AGE_SECONDS:
                        return "stale_session"  # /start refreshes it
                except (ValueError, TypeError):
                    pass
        return None

    def has_permission(self, telegram_id: int | str, command: str) -> bool:
        """Check if user has permission for a specific command.

        F-14 FIX: Sensitive commands (trade, halt, reset, mode, golive)
        require the user to have been active within the last 24 hours.
        If the session is stale, only read-only commands are permitted.
        """
        return self.permission_denial(telegram_id, command) is None

    # ── Listing and counting ───────────────────────────────────

    def list_users(self) -> list[dict]:
        """List all registered users."""
        with self._lock:
            return list(self._users.values())

    def count(self) -> dict[str, int]:
        """Count users by role."""
        with self._lock:
            counts: dict[str, int] = {}
            for u in self._users.values():
                r = u.get("role", "pending")
                counts[r] = counts.get(r, 0) + 1
            return counts

    def all_tiers(self) -> dict[str, str]:
        """{telegram_id: tier} for every registered user — the payload the
        website tier sync pushes so the web 'plan' mirrors the bot's tier
        authority."""
        with self._lock:
            return {k: u.get("tier", DEFAULT_TIER)
                    for k, u in self._users.items()}

    def count_by_tier(self) -> dict[str, int]:
        """Count users by tier."""
        with self._lock:
            counts: dict[str, int] = {}
            for u in self._users.values():
                t = u.get("tier", DEFAULT_TIER)
                counts[t] = counts.get(t, 0) + 1
            return counts

    def migrate_pending_users(self) -> int:
        """One-time migration: upgrade legacy 'pending' users to auto-approved.

        Users registered before auto-approve was added are stuck as pending.
        This promotes them to trader/basic with paper trading, matching what
        new users get automatically.

        Skips anyone an admin REVOKED (``revoked_at``). This runs on every
        startup, so without the check a revocation survived exactly until the
        next restart — and unlike the register() path, silently, with no
        message from the person involved to prompt anyone to look.

        Returns the number of users migrated.
        """
        migrated = 0
        with self._lock:
            for key, user in self._users.items():
                if user.get("role") == "pending" and not user.get("revoked_at"):
                    user["role"] = DEFAULT_AUTO_ROLE
                    user["authorized"] = True
                    user["can_trade_live"] = False
                    if "tier" not in user:
                        user["tier"] = DEFAULT_TIER
                    migrated += 1
                    audit(system_log,
                          f"Legacy user migrated: {key} ({user.get('name', '')}) "
                          f"pending -> {DEFAULT_AUTO_ROLE}/{DEFAULT_TIER}",
                          action="user_migrate", result="OK")
            if migrated:
                self._save()
        return migrated

    def seed_admin(self, admin_ids: str) -> None:
        """Seed admin users from comma-separated TELEGRAM_CHAT_ID."""
        if not admin_ids:
            return
        for cid in admin_ids.split(","):
            cid = cid.strip()
            if cid:
                key = str(cid)
                with self._lock:
                    if key not in self._users:
                        self._users[key] = {
                            "telegram_id": key,
                            "name": "Admin",
                            "role": "admin",
                            "tier": "admin",
                            "authorized": True,
                            "can_trade_live": True,
                            "created_at": datetime.now(UTC).isoformat(),
                            "last_seen": datetime.now(UTC).isoformat(),
                        }
                    else:
                        if self._users[key].get("role") != "admin":
                            self._users[key]["role"] = "admin"
                        self._users[key]["tier"] = "admin"
                        self._users[key]["authorized"] = True
                        self._users[key]["can_trade_live"] = True
                    self._save()
