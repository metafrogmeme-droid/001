"""
Per-user exchange credentials — encrypted at rest.

RUNECLAW today trades ONE shared operator Bitget account via the global
``CONFIG.exchange`` keys. To let each user trade THEIR OWN account, every user
links their own Bitget API key / secret / passphrase. Those are secrets that can
move real money, so this store keeps them **encrypted at rest** (Fernet / AES)
and only ever hands the plaintext back to the live-execution layer at trade time.

Design (mirrors bot/utils/attestation.py key handling):
  - One symmetric master key (Fernet). Sourced from the ``RUNECLAW_SECRETS_KEY``
    env var if set; otherwise generated once and persisted to a 0600 key file so
    ciphertext stays decryptable across restarts. A loud warning is logged when
    auto-generated, telling the operator to pin it in the environment.
  - Credentials are stored keyed by **Telegram id** (the id the execution layer
    has via ``confirm_trade(user_id=...)``), as a JSON map of Fernet ciphertexts.
  - Nothing here ever logs or returns a full key except ``get()`` (used only by
    the executor). Status surfaces use ``fingerprint()`` instead.

This module is pure storage + validation. It does NOT place trades and is not
wired into execution by itself — enabling per-user live trading is gated
separately (see PER_USER_LIVE_ENABLED and docs/LIVE_TRADING_ENABLEMENT.md).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Optional

from bot.core.margin_clamp import read_money_field
from bot.utils.atomic_write import atomic_write_json

log = logging.getLogger("runeclaw.exchange_creds")

_STATE_DIR = os.environ.get("RUNECLAW_STATE_DIR", "data")
_CREDS_FILE = os.path.join(_STATE_DIR, "exchange_creds.enc")
_KEY_FILE = os.path.join(_STATE_DIR, ".exchange_secret.key")

# Credential field names stored per user, per venue. Each venue authenticates
# with a different shape: Bitget uses an API key triple; Bybit/BingX use a plain
# key+secret; Hyperliquid uses the account wallet address + an *agent* (API)
# wallet private key (never the main wallet key). Adding a venue here + a
# matching create_exchange branch in bot/core/venues.py is all it takes to make
# it connectable (must match the venue ids registered in venues.py).
_VENUE_FIELDS: dict[str, tuple[str, ...]] = {
    "bitget": ("api_key", "api_secret", "passphrase"),
    "bybit": ("api_key", "api_secret"),
    "bingx": ("api_key", "api_secret"),
    "okx": ("api_key", "api_secret", "passphrase"),
    "gate": ("api_key", "api_secret"),
    "kucoin": ("api_key", "api_secret", "passphrase"),
    "hyperliquid": ("wallet_address", "agent_private_key"),
    "paradex": ("wallet_address", "agent_private_key"),
}

# venue id → ccxt exchange id (differs only where the perp product is a distinct
# ccxt class, e.g. KuCoin futures). Used by the read-only validation + balance
# probes for the plain key+secret[+passphrase] CEX venues.
_CCXT_ID: dict[str, str] = {
    "okx": "okx", "gate": "gate", "kucoin": "kucoinfutures",
}
_DEFAULT_VENUE = "bitget"
# Legacy alias — the pre-multi-venue field tuple. Kept so any external reference
# still resolves; the Bitget path is byte-identical to before.
_FIELDS = _VENUE_FIELDS[_DEFAULT_VENUE]


def _load_or_create_master_key(key_file: str = _KEY_FILE) -> bytes:
    """Return the Fernet master key.

    Precedence: RUNECLAW_SECRETS_KEY env (a urlsafe-base64 Fernet key) > a
    persisted key file > a freshly generated key (persisted, 0600, with a loud
    warning so the operator pins it in the environment).
    """
    from cryptography.fernet import Fernet

    env_key = os.environ.get("RUNECLAW_SECRETS_KEY", "").strip()
    if env_key:
        # Validate it is a usable Fernet key; fail loud rather than silently
        # falling back to a different key (which would orphan existing data).
        Fernet(env_key.encode())  # raises if malformed
        # Persist the env key to the 0600 file too, so that a wiped .env — which
        # removes RUNECLAW_SECRETS_KEY from the environment — falls back to the
        # SAME key from disk on the next boot instead of generating a fresh one
        # and orphaning all ciphertext (the secrets-vault + per-user store both
        # rely on this). Only write when the file is absent or differs.
        try:
            p = Path(key_file)
            if not p.exists() or p.read_bytes().strip() != env_key.encode():
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(env_key.encode())
                try:
                    os.chmod(str(p), 0o600)
                except OSError:
                    pass
        except OSError as exc:
            log.debug("Could not persist master key to %s: %s", key_file, exc)
        return env_key.encode()

    p = Path(key_file)
    if p.exists():
        return p.read_bytes().strip()

    key: bytes = Fernet.generate_key()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(key)
    try:
        os.chmod(str(p), 0o600)
    except OSError:
        pass
    # The key is NOT logged. It decrypts data/exchange_creds.enc (every user's
    # exchange key+secret+passphrase and agent private keys), data/secrets_vault.enc,
    # and the llm_api_key column — secrets_vault.py and db/models.py share this
    # loader. Logging it put all of that into stderr on the DEFAULT first boot,
    # where two containments both fail: the repo configures no root logger, so the
    # record falls through to logging.lastResort -> stderr -> the container log;
    # and bot/utils/logger.py's redactor is attached only to the runeclaw.trade/
    # risk/system/scan channels, not to this one. Anyone with `docker logs`, a log
    # aggregator, a support bundle or CI output could read it.
    #
    # A fingerprint is enough to answer the only question an operator has here —
    # "is the key I pinned the one being used?" — and answers it without the log
    # line itself becoming the disclosure.
    log.warning(
        "RUNECLAW_SECRETS_KEY is not set — generated a new exchange-encryption "
        "key and persisted it to %s (0600), fingerprint %s. For production, set "
        "RUNECLAW_SECRETS_KEY explicitly so the key is managed outside the data "
        "dir and survives it being wiped. Read the value from that file; it is "
        "deliberately never logged.",
        key_file, hashlib.sha256(key).hexdigest()[:12],
    )
    return key


class ExchangeCredentialStore:
    """Fernet-encrypted per-user Bitget credential store, keyed by Telegram id."""

    def __init__(self, creds_file: str = _CREDS_FILE, key_file: str = _KEY_FILE) -> None:
        self._path = Path(creds_file)
        self._lock = threading.Lock()
        self._key_file = key_file
        # Annotated Any (not None) so the lazy ``Fernet`` assignment in _cipher
        # type-checks without importing cryptography at module top (it's an
        # optional extra). Now reachable by the gated mypy run via
        # config -> secrets_vault -> exchange_credentials.
        self._fernet: Any = None  # lazy — only when crypto is actually needed
        # Raw on-disk map. Two record shapes coexist:
        #   NEW:    { telegram_id: { "venue": "bitget", "fields": { field: ct } } }
        #   LEGACY: { telegram_id: { field: ct } }  (implicitly Bitget)
        # _read_record() normalizes both; legacy files decrypt with zero rewrite.
        self._enc: dict[str, dict] = {}
        #: True when _load could not read an EXISTING file. Blocks _save.
        self._load_failed: bool = False
        self._load()

    # -- crypto ---------------------------------------------------------------

    def _cipher(self):
        if self._fernet is None:
            from cryptography.fernet import Fernet
            self._fernet = Fernet(_load_or_create_master_key(self._key_file))
        return self._fernet

    # -- persistence ----------------------------------------------------------

    def _load(self) -> None:
        """Read the encrypted store, or refuse to write if it cannot be read.

        This used to swallow the failure into ``self._enc = {}`` and carry on.
        Nothing else changed — but ``_save`` writes ``self._enc`` wholesale
        through tmp+rename, so the NEXT /connect by ANY user replaced the real
        file with an empty one. Every BYOK user's encrypted venue keys and
        every stored agent private key, gone, with no .bak anywhere in this
        module.

        The catch included OSError, which is the part that makes it likely
        rather than exotic: a transient read failure — disk full, a
        permissions blip, EINTR — was enough. The old log line ("will need to
        /connect again") shows the data loss was anticipated; the silent
        overwrite that made it permanent was not.

            AN UNREADABLE STORE IS NOT AN EMPTY STORE, AND THE DIFFERENCE IS
            EVERY KEY IN IT.

        A missing file IS legitimately empty — first boot — and still saves.
        """
        self._load_failed = False
        if not self._path.exists():
            self._enc = {}
            return
        try:
            with open(self._path) as f:
                self._enc = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            # Preserve the bytes before anything can touch the path again.
            _kept = ""
            try:
                _damaged = self._path.with_suffix(
                    self._path.suffix + ".corrupt")
                if not _damaged.exists():
                    os.replace(str(self._path), str(_damaged))
                    _kept = f" Original preserved at {_damaged.name}."
            except OSError:
                _kept = " Could not preserve the original."
            self._load_failed = True
            self._enc = {}
            log.critical(
                "exchange_creds unreadable (%s) — REFUSING to write this store "
                "until it is repaired, so a save cannot overwrite the real "
                "keys with an empty file.%s Linked accounts cannot be used "
                "until this is resolved.", exc.__class__.__name__, _kept)

    def _save(self) -> None:
        # Fail-closed: never persist an in-memory map that was built from a
        # FAILED read. Writing here is what turned an unreadable file into
        # permanent key loss.
        if getattr(self, "_load_failed", False):
            log.critical("refusing to save exchange_creds: the store failed to "
                         "load, so writing would destroy the real keys")
            raise RuntimeError(
                "credential store is unreadable — refusing to overwrite it")
        # 0600 is applied to the scratch file BEFORE the rename, so the
        # ciphertext is never briefly world-readable under the final name.
        atomic_write_json(self._path, self._enc, mode=0o600)

    # -- record normalization -------------------------------------------------

    @staticmethod
    def _normalize(enc: dict) -> dict:
        """Normalize any on-disk record shape to the multi-venue form
        ``{"active": venue, "venues": {venue: {field: ct}}}``.

        Three generations coexist and all keep decrypting with zero rewrite:
          v3: {"active": v, "venues": {v: fields, ...}}   (multi-venue)
          v2: {"venue": v, "fields": fields}              (single venue)
          v1: {field: ct}                                 (implicitly Bitget)
        """
        if isinstance(enc, dict) and "venues" in enc:
            venues = {str(v): dict(f) for v, f in dict(enc["venues"]).items()}
            active = str(enc.get("active") or next(iter(venues), _DEFAULT_VENUE))
            return {"active": active, "venues": venues}
        if isinstance(enc, dict) and "fields" in enc and "venue" in enc:
            v = str(enc["venue"])
            return {"active": v, "venues": {v: dict(enc["fields"])}}
        return {"active": _DEFAULT_VENUE, "venues": {_DEFAULT_VENUE: dict(enc)}}

    @classmethod
    def _read_record(cls, enc: dict) -> tuple[str, dict]:
        """The ACTIVE venue's ``(venue, {field: ciphertext})`` — the shape the
        pre-multi-venue callers expect."""
        rec = cls._normalize(enc)
        active = str(rec["active"])
        return active, dict(rec["venues"].get(active, {}))

    # -- public API -----------------------------------------------------------

    def set(self, telegram_id, api_key: str, api_secret: str, passphrase: str) -> None:
        """Encrypt and store a user's BITGET credentials (overwrites any existing).

        Kept for the Bitget path (its 3-positional signature is unchanged); it
        delegates to the venue-aware ``set_venue``.
        """
        self.set_venue(telegram_id, "bitget", {
            "api_key": api_key, "api_secret": api_secret, "passphrase": passphrase,
        })

    def set_venue(self, telegram_id, venue: str, fields: dict) -> None:
        """Encrypt and store a user's credentials for ``venue`` (overwrites any
        existing). ``fields`` must contain exactly the venue's required keys
        (see ``_VENUE_FIELDS``). Raises ValueError on an unknown venue or a
        missing field, so a bad connect can never persist a half-record."""
        venue = str(venue).lower().strip()
        expected = _VENUE_FIELDS.get(venue)
        if expected is None:
            raise ValueError(f"unknown venue {venue!r}")
        missing = [f for f in expected if not fields.get(f)]
        if missing:
            raise ValueError(f"missing {venue} credential field(s): {missing}")
        c = self._cipher()
        enc = {f: c.encrypt(str(fields[f]).encode()).decode() for f in expected}
        with self._lock:
            # MERGE into the user's venue map (multi-venue): connecting Bybit
            # must never wipe Bitget's stored keys. The just-connected venue
            # becomes the ACTIVE one — submitting keys for a venue is the user
            # saying "trade here", and the executor rebuild check follows the
            # active view. set_active() switches back without re-entering keys.
            rec = self._normalize(self._enc.get(str(telegram_id)) or {"active": venue, "venues": {}})
            rec["venues"][venue] = enc
            rec["active"] = venue
            self._enc[str(telegram_id)] = rec
            self._save()
        log.info("Stored encrypted %s credentials for user %s", venue, telegram_id)

    def has(self, telegram_id) -> bool:
        with self._lock:
            return str(telegram_id) in self._enc

    def user_ids(self) -> list:
        """All Telegram ids with stored credentials. Used at startup to rehydrate
        per-user executors so their open positions resume being monitored."""
        with self._lock:
            return list(self._enc.keys())

    def get(self, telegram_id) -> Optional[dict]:
        """Decrypt and return the venue-specific credential fields, or None.

        Bitget records return ``{api_key, api_secret, passphrase}`` (unchanged);
        Hyperliquid records return ``{wallet_address, agent_private_key}``. Used
        by the execution layer at trade time. Returns None (never raises) if the
        user has no credentials or decryption fails (e.g. the master key
        changed) — the caller treats that as 'not connected'.
        """
        with self._lock:
            enc = self._enc.get(str(telegram_id))
        if not enc:
            return None
        venue, fields_enc = self._read_record(enc)
        field_names = _VENUE_FIELDS.get(venue, _FIELDS)
        try:
            c = self._cipher()
            return {f: c.decrypt(fields_enc[f].encode()).decode() for f in field_names}
        except Exception as exc:  # InvalidToken, missing field, etc.
            log.error("Failed to decrypt exchange credentials for %s: %s", telegram_id, exc)
            return None

    def get_venue(self, telegram_id) -> str:
        """The user's ACTIVE venue (``"bitget"`` default, including for legacy
        records and users with nothing stored)."""
        with self._lock:
            enc = self._enc.get(str(telegram_id))
        if not enc:
            return _DEFAULT_VENUE
        venue, _ = self._read_record(enc)
        return venue

    def list_venues(self, telegram_id) -> list[str]:
        """Every venue this user has credentials stored for (may be empty)."""
        with self._lock:
            enc = self._enc.get(str(telegram_id))
        if not enc:
            return []
        return sorted(self._normalize(enc)["venues"].keys())

    def get_for_venue(self, telegram_id, venue: str) -> Optional[dict]:
        """Decrypt one SPECIFIC venue's fields (None when absent/undecryptable)."""
        venue = str(venue).lower().strip()
        with self._lock:
            enc = self._enc.get(str(telegram_id))
        if not enc:
            return None
        fields_enc = self._normalize(enc)["venues"].get(venue)
        if not fields_enc:
            return None
        field_names = _VENUE_FIELDS.get(venue, _FIELDS)
        try:
            c = self._cipher()
            return {f: c.decrypt(fields_enc[f].encode()).decode() for f in field_names}
        except Exception as exc:
            log.error("Failed to decrypt %s credentials for %s: %s", venue, telegram_id, exc)
            return None

    def set_active(self, telegram_id, venue: str) -> bool:
        """Switch the user's ACTIVE venue (must already have credentials for it)."""
        venue = str(venue).lower().strip()
        with self._lock:
            enc = self._enc.get(str(telegram_id))
            if not enc:
                return False
            rec = self._normalize(enc)
            if venue not in rec["venues"]:
                return False
            rec["active"] = venue
            self._enc[str(telegram_id)] = rec
            self._save()
        log.info("Active venue for user %s -> %s", telegram_id, venue)
        return True

    def delete_venue(self, telegram_id, venue: str) -> bool:
        """Remove ONE venue's credentials; the active pointer moves to another
        connected venue (or the whole record goes when none remain)."""
        venue = str(venue).lower().strip()
        with self._lock:
            enc = self._enc.get(str(telegram_id))
            if not enc:
                return False
            rec = self._normalize(enc)
            if venue not in rec["venues"]:
                return False
            del rec["venues"][venue]
            if not rec["venues"]:
                self._enc.pop(str(telegram_id), None)
            else:
                if rec["active"] == venue:
                    rec["active"] = next(iter(sorted(rec["venues"])))
                self._enc[str(telegram_id)] = rec
            self._save()
        log.info("Deleted %s credentials for user %s", venue, telegram_id)
        return True

    def delete(self, telegram_id) -> bool:
        with self._lock:
            existed = str(telegram_id) in self._enc
            self._enc.pop(str(telegram_id), None)
            if existed:
                self._save()
        if existed:
            log.info("Deleted exchange credentials for user %s", telegram_id)
        return existed

    def fingerprint(self, telegram_id) -> str:
        """A safe, non-reversible identifier of the stored key for status display.

        Returns e.g. ``"BG-1a2b…f9"`` (Bitget, a short hash of the api_key) or
        ``"HL-…"`` (Hyperliquid, hash of the wallet address), or "" if none.
        Never reveals the key itself.
        """
        creds = self.get(telegram_id)
        if not creds:
            return ""
        venue = self.get_venue(telegram_id)
        # Fingerprint the venue's identity field (Bitget key stays byte-identical).
        ident_field = "api_key" if venue == "bitget" else _VENUE_FIELDS.get(
            venue, ("",))[0]
        ident = creds.get(ident_field)
        if not ident:
            return ""
        import hashlib
        prefix = "BG" if venue == "bitget" else "HL" if venue == "hyperliquid" else venue[:2].upper()
        h = hashlib.sha256(ident.encode()).hexdigest()
        return f"{prefix}-{h[:4]}…{h[-2:]}"


# Bitget error for API keys that belong to the OTHER environment: a
# demo-trading key hitting the live API (or a live key hitting demo).
_WRONG_ENV_CODE = "40099"


async def _bitget_balance_probe(api_key: str, api_secret: str,
                                passphrase: str, sandbox: bool) -> tuple[bool, str]:
    """One read-only balance fetch against ONE Bitget environment.

    ``sandbox=True`` activates Bitget demo trading via ccxt's
    set_sandbox_mode (sends the PAPTRADING=1 header). Returns (ok, detail).
    """
    client = None
    try:
        import ccxt.async_support as ccxt
    except Exception as exc:  # pragma: no cover - import guard
        return False, f"ccxt unavailable: {exc}"
    try:
        client = ccxt.bitget({
            "apiKey": api_key,
            "secret": api_secret,
            "password": passphrase,
            "timeout": 15000,
            "enableRateLimit": True,
            "options": {
                "defaultType": "swap",
                "uta": True,  # Support Bitget Unified Trading Account
            },
        })
        # Explicit and version-stable: for bitget this toggles the demo-trading
        # header rather than relying on a constructor key.
        try:
            client.set_sandbox_mode(sandbox)
        except Exception:
            if sandbox:
                raise
        bal = await client.fetch_balance({"type": "swap"})
        free = 0.0
        try:
            free = float((bal.get("USDT") or {}).get("free", 0.0) or 0.0)
        except (TypeError, ValueError):
            free = 0.0
        return True, f"{free:.2f} USDT free"
    except Exception as exc:
        return False, _safe_venue_detail(exc)
    finally:
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass


# ── key SCOPE: what a key is PERMITTED to do, not what it holds ──────────────
#
# Every probe above answers "can this key read the account". None of them answer
# the question a non-custodial product actually rests on: CAN THIS KEY MOVE THE
# MONEY OUT? Bitget's `GET /api/v2/spot/account/info` returns the calling key's
# granted permissions in `authorities` (and its IP pinning in `ips`), which is
# the only endpoint in ccxt's bitget surface that describes the CALLING key —
# every other apikey endpoint there is broker/sub-account management, acts on
# other keys, and needs broker privileges.

# Transcribed from Bitget's API documentation. NOT confirmed against a live key
# from inside this repository — nobody here holds one — and the rule below is
# shaped entirely around that fact.
_BITGET_WITHDRAW_AUTHORITIES = frozenset({"withdraw"})
_BITGET_KNOWN_AUTHORITIES = frozenset({
    "readonly", "spot_trade", "contract_trade", "margin_trade",
    "wallet_transfer", "transfer", "withdraw",
})


def bitget_withdraw_scope(authorities: Any) -> str:
    """``"on"`` | ``"off"`` | ``"unknown"`` — a Bitget key's withdraw permission.

    THE ASYMMETRY HERE IS THE WHOLE POINT. ``"off"`` renders downstream as
    "key has no withdraw permission — non-custodial, as intended", which is a
    confident all-clear about somebody's money. So it is only ever returned when
    EVERY authority in the response is one this function recognises: if we do not
    understand the entire permission set, we do not get to conclude that a
    permission is absent from it.

    That makes a wrong or stale vocabulary degrade in the safe direction. If
    Bitget renames a scope, or adds one, or spells withdrawal something this set
    does not contain, every real response carries an unrecognised token and the
    answer becomes ``"unknown"`` — which is precisely the status quo before this
    function existed. The failure mode it cannot have is the other one: reading
    an unrecognised response as proof of non-custody.

    ``"on"`` is the one verdict that survives an unknown token, because a
    recognised withdraw authority is positive evidence regardless of what else
    sits beside it.
    """
    if not isinstance(authorities, (list, tuple, set, frozenset)):
        return "unknown"          # absent or a shape we did not expect
    tokens = [str(a).strip().lower() for a in authorities if str(a).strip()]
    if not tokens:
        # An empty list is not "no permissions" — a key with no permissions
        # could not have authenticated to ask the question in the first place.
        return "unknown"
    if any(t in _BITGET_WITHDRAW_AUTHORITIES for t in tokens):
        return "on"
    if all(t in _BITGET_KNOWN_AUTHORITIES for t in tokens):
        return "off"
    return "unknown"


def bitget_ip_allowlist(info: Any) -> Optional[list]:
    """The IPs a Bitget key is pinned to, or ``None`` when that is not readable.

    Bitget returns ``ips`` as a comma-separated string. ``None`` and ``[]`` are
    different answers and both are real: ``[]`` means the venue told us the key
    is NOT IP-restricted, ``None`` means nobody could look.
    """
    if not isinstance(info, dict) or "ips" not in info:
        return None
    raw = info.get("ips")
    if raw is None:
        return None
    if isinstance(raw, (list, tuple)):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str):
        return [p.strip() for p in raw.split(",") if p.strip()]
    return None


async def probe_bitget_key_scope(api_key: str, api_secret: str, passphrase: str,
                                 sandbox: bool = False) -> dict:
    """Observe a Bitget key's granted scope. READ-ONLY, and never raises.

    Returns ``{"withdraw": "on"|"off"|"unknown", "ip_allowlist": [...]|None}``.
    No withdrawal is attempted and no order is placed — the scope is *asked for*,
    never *tested*. Every failure path (ccxt missing, endpoint absent on this
    ccxt version, HTTP error, a response shape we do not recognise) lands on
    ``"unknown"``/``None``, so the worst case is exactly the information we had
    before calling it.
    """
    out: dict = {"withdraw": "unknown", "ip_allowlist": None}
    client = None
    try:
        import ccxt.async_support as ccxt
    except Exception:
        return out
    try:
        client = ccxt.bitget({
            "apiKey": api_key,
            "secret": api_secret,
            "password": passphrase,
            "timeout": 15000,
            "enableRateLimit": True,
        })
        try:
            client.set_sandbox_mode(sandbox)
        except Exception:
            if sandbox:
                return out
        fetch = getattr(client, "privateSpotGetV2SpotAccountInfo", None)
        if fetch is None:
            return out          # ccxt version without the endpoint — not a failure
        resp = await fetch({})
        data = resp.get("data") if isinstance(resp, dict) else None
        if not isinstance(data, dict):
            return out
        out["withdraw"] = bitget_withdraw_scope(data.get("authorities"))
        out["ip_allowlist"] = bitget_ip_allowlist(data)
        return out
    except Exception:
        # A key without spot-account read permission answers 401/403 here while
        # being a perfectly good futures key. That is "we could not look", not
        # "it cannot withdraw".
        return out
    finally:
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass


async def validate_bitget_credentials(
    api_key: str, api_secret: str, passphrase: str, sandbox: bool = False
) -> tuple[bool, str]:
    """Functionally validate Bitget credentials with a READ-ONLY balance fetch.

    Returns (ok, detail). ``detail`` is a short free USDT summary on success or a
    trimmed error string on failure. Proves the keys authenticate before we store
    them and before any order is ever placed. Never places an order.

    Bitget code 40099 ("exchange environment is incorrect") means the key
    belongs to the OTHER environment (demo vs live). We retry once against the
    opposite environment purely to diagnose, and if the key authenticates
    there, return a precise actionable message instead of the raw JSON —
    without ever storing a wrong-environment key.
    """
    ok, detail = await _bitget_balance_probe(api_key, api_secret, passphrase, sandbox)
    if ok or _WRONG_ENV_CODE not in detail:
        return ok, detail
    # 40099: diagnose which environment the key actually belongs to.
    other_ok, _ = await _bitget_balance_probe(api_key, api_secret, passphrase,
                                              not sandbox)
    if other_ok:
        if sandbox:
            return False, (
                "These are LIVE Bitget keys, but this bot runs in DEMO "
                "(paper) trading (BITGET_SANDBOX=true in the bot's .env). "
                "Create the API keys inside Bitget demo trading, with "
                "USDT-M futures read + trade permission — or ask the "
                "operator to set BITGET_SANDBOX=false for production.")
        return False, (
            "These are DEMO-trading Bitget keys, but this bot trades LIVE "
            "(bot environment: PRODUCTION). Create the API keys in your "
            "main Bitget account (API Management, not demo trading), with "
            "USDT-M futures read + trade permission.")
    return False, detail


async def _hyperliquid_balance_probe(wallet_address: str, agent_private_key: str,
                                     sandbox: bool) -> tuple[bool, str]:
    """One read-only balance fetch against Hyperliquid (USDC perps).

    Hyperliquid authenticates with the account's public wallet address plus an
    *agent* (API) wallet private key — never the main wallet key. ``sandbox``
    routes to the testnet. Returns (ok, detail).
    """
    client = None
    try:
        import ccxt.async_support as ccxt
    except Exception as exc:  # pragma: no cover - import guard
        return False, f"ccxt unavailable: {exc}"
    try:
        client = ccxt.hyperliquid({
            "walletAddress": wallet_address,
            "privateKey": agent_private_key,
            "timeout": 15000,
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        })
        try:
            client.set_sandbox_mode(sandbox)
        except Exception:
            if sandbox:
                raise
        bal = await client.fetch_balance()
        free = 0.0
        try:
            free = float((bal.get("USDC") or {}).get("free", 0.0) or 0.0)
        except (TypeError, ValueError):
            free = 0.0
        return True, f"{free:.2f} USDC free"
    except Exception as exc:
        return False, _safe_venue_detail(exc)
    finally:
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass


async def validate_hyperliquid_credentials(
    wallet_address: str, agent_private_key: str, sandbox: bool = False
) -> tuple[bool, str]:
    """Functionally validate Hyperliquid credentials with a READ-ONLY balance
    fetch. Returns (ok, detail) — a short free USDC summary on success or a
    trimmed error on failure. Proves the agent key authenticates for the wallet
    before we store it and before any order is placed. Never places an order."""
    return await _hyperliquid_balance_probe(wallet_address, agent_private_key, sandbox)


async def _keysecret_balance_probe(exchange_id: str, api_key: str,
                                   api_secret: str, sandbox: bool) -> tuple[bool, str]:
    """Read-only balance fetch for a plain key+secret ccxt venue (Bybit, BingX).

    Both are USDT-margined swap exchanges that authenticate with apiKey/secret
    only. Returns (ok, detail). Never places an order."""
    client = None
    try:
        import ccxt.async_support as ccxt
    except Exception as exc:  # pragma: no cover - import guard
        return False, f"ccxt unavailable: {exc}"
    try:
        factory = getattr(ccxt, exchange_id, None)
        if factory is None:
            return False, f"ccxt has no exchange {exchange_id!r}"
        client = factory({
            "apiKey": api_key,
            "secret": api_secret,
            "timeout": 15000,
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        })
        try:
            client.set_sandbox_mode(sandbox)
        except Exception:
            if sandbox:
                raise
        bal = await client.fetch_balance()
        free = 0.0
        try:
            free = float((bal.get("USDT") or {}).get("free", 0.0) or 0.0)
        except (TypeError, ValueError):
            free = 0.0
        return True, f"{free:.2f} USDT free"
    except Exception as exc:
        return False, _safe_venue_detail(exc)
    finally:
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass


async def _ccxt_keysecret_probe(ccxt_id: str, api_key: str, api_secret: str,
                                passphrase: str, sandbox: bool) -> tuple[bool, str]:
    """Read-only balance fetch for a ccxt swap venue that authenticates with
    apiKey/secret and (optionally) a passphrase — OKX, Gate, KuCoin. Never places
    an order."""
    client = None
    try:
        import ccxt.async_support as ccxt
    except Exception as exc:  # pragma: no cover - import guard
        return False, f"ccxt unavailable: {exc}"
    try:
        factory = getattr(ccxt, ccxt_id, None)
        if factory is None:
            return False, f"ccxt has no exchange {ccxt_id!r}"
        opts = {
            "apiKey": api_key, "secret": api_secret,
            "timeout": 15000, "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        }
        if passphrase:
            opts["password"] = passphrase
        client = factory(opts)
        try:
            client.set_sandbox_mode(sandbox)
        except Exception:
            if sandbox:
                raise
        bal = await client.fetch_balance()
        free = 0.0
        try:
            free = float((bal.get("USDT") or {}).get("free", 0.0) or 0.0)
        except (TypeError, ValueError):
            free = 0.0
        return True, f"{free:.2f} USDT free"
    except Exception as exc:
        return False, _safe_venue_detail(exc)
    finally:
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass


async def _wallet_balance_probe(ccxt_id: str, currency: str, wallet_address: str,
                                agent_private_key: str, sandbox: bool) -> tuple[bool, str]:
    """Read-only balance fetch for a wallet-authenticated ccxt DEX (walletAddress +
    privateKey) — e.g. Paradex. Never places an order."""
    client = None
    try:
        import ccxt.async_support as ccxt
    except Exception as exc:  # pragma: no cover - import guard
        return False, f"ccxt unavailable: {exc}"
    try:
        factory = getattr(ccxt, ccxt_id, None)
        if factory is None:
            return False, f"ccxt has no exchange {ccxt_id!r}"
        client = factory({
            "walletAddress": wallet_address, "privateKey": agent_private_key,
            "timeout": 15000, "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        })
        try:
            client.set_sandbox_mode(sandbox)
        except Exception:
            if sandbox:
                raise
        bal = await client.fetch_balance()
        free = 0.0
        try:
            free = float((bal.get(currency) or {}).get("free", 0.0) or 0.0)
        except (TypeError, ValueError):
            free = 0.0
        return True, f"{free:.2f} {currency} free"
    except Exception as exc:
        return False, _safe_venue_detail(exc)
    finally:
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass


async def validate_venue_credentials(venue: str, fields: dict,
                                     sandbox: bool = False) -> tuple[bool, str]:
    """Read-only-validate a user's credentials for ``venue`` (dispatches to the
    per-venue probe). Returns (ok, detail). Never places an order."""
    venue = str(venue).lower().strip()
    if venue == "bitget":
        return await validate_bitget_credentials(
            fields["api_key"], fields["api_secret"], fields["passphrase"], sandbox)
    if venue == "hyperliquid":
        return await validate_hyperliquid_credentials(
            fields["wallet_address"], fields["agent_private_key"], sandbox)
    if venue == "paradex":
        return await _wallet_balance_probe(
            "paradex", "USDC", fields["wallet_address"],
            fields["agent_private_key"], sandbox)
    if venue in ("bybit", "bingx"):
        return await _keysecret_balance_probe(
            venue, fields["api_key"], fields["api_secret"], sandbox)
    if venue in _CCXT_ID:   # okx, gate, kucoin (kucoin → kucoinfutures)
        return await _ccxt_keysecret_probe(
            _CCXT_ID[venue], fields["api_key"], fields["api_secret"],
            fields.get("passphrase", ""), sandbox)
    return False, f"unknown venue {venue!r}"


def _safe_venue_detail(exc: BaseException, limit: int = 200) -> str:
    """A venue's rejection reason, with inline secrets scrubbed.

    These strings are the ANSWER a user gets from /connect and /setexchange —
    "wrong passphrase", "IP not allowlisted", "invalid key" — so dropping them
    for a class name would take away the only thing that tells them what to
    fix. They were `str(exc)[:200]`, which is the raw driver message: a ccxt
    error carries the request URL, and for several venues the API key travels
    in that URL's query string. Escaping is not the issue; the credential is.

    Scrubbed rather than suppressed, through the same chokepoint the log
    formatter uses, so a pattern added there covers this too. The class name
    is prepended because a scrubbed message can end up empty or unhelpful, and
    "AuthenticationError" is worth more than nothing.
    """
    try:
        from bot.utils.logger import _redact_string
        msg = _redact_string(str(exc))
    except Exception:
        msg = ""
    msg = msg.strip()
    name = type(exc).__name__
    if not msg or msg == name:
        return name[:limit]
    return f"{name}: {msg}"[:limit]


def _balance_total(bal: dict, currency: str) -> Optional[float]:
    """Total (free+used) of ``currency`` from a ccxt fetch_balance dict, or None.

    RC-2026-017. This returned 0.0 on any malformed shape — its own docstring
    said so — and `balance_snapshot` published that as
    ``ok: True, equity_usd: 0.0, detail: "0.00 USDC total"``. An affirmative
    success, on the flow where somebody has just linked an exchange account,
    telling them it holds nothing when the truth is that the currency entry
    was never found.

    Still never raises: a malformed shape yields None, which the callers
    already render as "unavailable" because their own timeout path produces
    it. Not-read and empty are now different answers.
    """
    try:
        row = bal.get(currency) or {}
        total = read_money_field(row, "total")
        if total is not None:
            return total
        free = read_money_field(row, "free")
        used = read_money_field(row, "used")
        if free is None and used is None:
            return None
        return (free or 0.0) + (used or 0.0)
    except (TypeError, ValueError, AttributeError):
        return None


async def balance_snapshot(venue: str, fields: dict,
                           sandbox: bool = False) -> dict:
    """READ-ONLY equity snapshot for a user's stored venue credentials.

    One fetch_balance — the exact same call the connect-time validators
    make — returning numbers instead of a validation string:
    ``{ok, venue, currency, equity_usd, detail}``. Never raises, never
    writes, never places an order; credentials are used in-process only
    and never appear in the returned dict.
    """
    venue = str(venue).lower().strip()
    client = None
    try:
        import ccxt.async_support as ccxt
    except Exception as exc:  # pragma: no cover - import guard
        return {"ok": False, "venue": venue, "equity_usd": None,
                "detail": f"ccxt unavailable: {exc}"}
    currency = "USDC" if venue in ("hyperliquid", "paradex") else "USDT"
    try:
        if venue == "bitget":
            client = ccxt.bitget({
                "apiKey": fields["api_key"], "secret": fields["api_secret"],
                "password": fields["passphrase"], "timeout": 15000,
                "enableRateLimit": True,
                "options": {"defaultType": "swap", "uta": True},
            })
        elif venue in ("hyperliquid", "paradex"):
            factory = getattr(ccxt, venue, None)
            if factory is None:
                return {"ok": False, "venue": venue, "equity_usd": None,
                        "detail": f"ccxt has no exchange {venue!r}"}
            client = factory({
                "walletAddress": fields["wallet_address"],
                "privateKey": fields["agent_private_key"],
                "timeout": 15000, "enableRateLimit": True,
                "options": {"defaultType": "swap"},
            })
        elif venue in ("bybit", "bingx") or venue in _CCXT_ID:
            ccxt_id = _CCXT_ID.get(venue, venue)   # kucoin → kucoinfutures
            factory = getattr(ccxt, ccxt_id, None)
            if factory is None:
                return {"ok": False, "venue": venue, "equity_usd": None,
                        "detail": f"ccxt has no exchange {ccxt_id!r}"}
            opts = {
                "apiKey": fields["api_key"], "secret": fields["api_secret"],
                "timeout": 15000, "enableRateLimit": True,
                "options": {"defaultType": "swap"},
            }
            if fields.get("passphrase"):           # okx, kucoin
                opts["password"] = fields["passphrase"]
            client = factory(opts)
        else:
            return {"ok": False, "venue": venue, "equity_usd": None,
                    "detail": f"unknown venue {venue!r}"}
        try:
            client.set_sandbox_mode(sandbox)
        except Exception:
            if sandbox:
                raise
        params = {"type": "swap"} if venue == "bitget" else {}
        bal = await client.fetch_balance(params)
        equity = _balance_total(bal, currency)
        if equity is None:
            # Auth SUCCEEDED — the venue answered. It just did not answer with
            # a figure for this currency, and `ok` reports authentication, not
            # the balance. Reporting 0.00 here read as an empty account.
            return {"ok": True, "venue": venue, "currency": currency,
                    "equity_usd": None,
                    "detail": f"authenticated, but no readable {currency} "
                              f"balance in the venue's response"}
        return {"ok": True, "venue": venue, "currency": currency,
                "equity_usd": round(equity, 2),
                "detail": f"{equity:.2f} {currency} total"}
    except Exception as exc:
        return {"ok": False, "venue": venue, "equity_usd": None,
                "detail": _safe_venue_detail(exc)}
    finally:
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass


def basic_venue_format_ok(venue: str, fields: dict) -> bool:
    """Cheap per-venue paste-mistake check before the network probe."""
    venue = str(venue).lower().strip()
    if venue == "bitget":
        return basic_key_format_ok(
            fields.get("api_key", ""), fields.get("api_secret", ""),
            fields.get("passphrase", ""))
    if venue in ("hyperliquid", "paradex"):
        return basic_hl_format_ok(
            fields.get("wallet_address", ""), fields.get("agent_private_key", ""))
    if venue in ("bybit", "bingx", "gate"):
        ak, sec = fields.get("api_key", ""), fields.get("api_secret", "")
        for v in (ak, sec):
            if not v or " " in v or "\n" in v:
                return False
        return len(ak) >= 8 and len(sec) >= 8
    if venue in ("okx", "kucoin"):   # key + secret + passphrase
        ak, sec, pw = (fields.get("api_key", ""), fields.get("api_secret", ""),
                       fields.get("passphrase", ""))
        for v in (ak, sec, pw):
            if not v or " " in v or "\n" in v:
                return False
        return len(ak) >= 8 and len(sec) >= 8
    return False


_STORE: Optional["ExchangeCredentialStore"] = None
_STORE_LOCK = threading.Lock()


def get_credential_store() -> "ExchangeCredentialStore":
    """Process-wide singleton credential store (lazy)."""
    global _STORE
    if _STORE is None:
        with _STORE_LOCK:
            if _STORE is None:
                _STORE = ExchangeCredentialStore()
    return _STORE


def basic_key_format_ok(api_key: str, api_secret: str, passphrase: str) -> bool:
    """Cheap sanity check before the network validation: non-empty, no spaces,
    plausible lengths. Not a security control — just catches obvious paste
    mistakes early."""
    for v in (api_key, api_secret, passphrase):
        if not v or " " in v or "\n" in v:
            return False
    return len(api_key) >= 12 and len(api_secret) >= 12 and len(passphrase) >= 1


def basic_hl_format_ok(wallet_address: str, agent_private_key: str) -> bool:
    """Cheap sanity check for Hyperliquid: a 0x-prefixed 40-hex-char wallet
    address and a 0x-prefixed 64-hex-char private key (with or without the 0x).
    Not a security control — catches obvious paste mistakes before the network
    probe."""
    for v in (wallet_address, agent_private_key):
        if not v or " " in v or "\n" in v:
            return False
    addr = wallet_address[2:] if wallet_address.lower().startswith("0x") else wallet_address
    key = agent_private_key[2:] if agent_private_key.lower().startswith("0x") else agent_private_key
    hexset = set("0123456789abcdefABCDEF")
    if len(addr) != 40 or any(ch not in hexset for ch in addr):
        return False
    if len(key) != 64 or any(ch not in hexset for ch in key):
        return False
    return True


def valid_venue_ids() -> tuple[str, ...]:
    """Venues the per-user credential store can hold (for /connect + web)."""
    return tuple(_VENUE_FIELDS.keys())
