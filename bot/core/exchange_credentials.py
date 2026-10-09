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
import re
import threading
from pathlib import Path
from typing import Any, Callable, Optional

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
    # Balances only: Bybit EU lists no perpetual futures (venues.BybitEuVenue).
    "bybiteu": ("api_key", "api_secret"),
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


def _free_detail(bal: Any, currency: str) -> str:
    """The balance line a key check reports: ``"52.56 USDT free"``, or that the
    venue answered without a readable figure.

    Every probe read ``float(bal["USDT"]["free"] or 0.0)`` and fell back to 0.0
    on a missing entry or an unparseable value, so a key whose account holds
    USDC (a Bybit or Bitget unified account can) was linked with "Balance: 0.00
    USDT free" on the card, a measurement of an empty account made from a line
    the reply never carried. ``read_money_field`` is the one reading of a
    money field: a real 0 stays 0, absent and unreadable are None.
    """
    row = bal.get(currency) if isinstance(bal, dict) else None
    free = read_money_field(row, "free")
    if free is None:
        return f"authenticated, but no readable {currency} balance in the reply"
    return f"{free:.2f} {currency} free"


def _fingerprint(key: bytes) -> str:
    """Twelve hex chars of sha256(key). Enough to answer "is the key I pinned
    the one in force?", short of being the disclosure itself."""
    return hashlib.sha256(bytes(key).strip()).hexdigest()[:12]


def master_key_state(key_file: str = _KEY_FILE) -> dict:
    """WHERE the master key lives, and what a wipe would cost. Never the key.

    Four surfaces in this repo explain this condition in prose -- ``/vault``
    ("a wiped data dir with RUNECLAW_SECRETS_KEY unset does it"), the boot
    preflight's undecryptable-accounts alert, ``secrets_vault``'s own warning,
    and this module's docstring -- and **not one of them ever read whether it
    is true here**. Every mention is a hypothesis offered at the moment the
    operator most needs the fact.

    Worse, the one warning that does measure something fires exactly once, on
    the boot that GENERATES the key. Every boot after that takes the
    ``if p.exists(): return`` branch below in silence, so a box that has been
    one ``rm -rf data/`` from losing every stored credential for six months has
    said so once, in a container log, half a year ago.

    The states, and what each costs:

        pinned      env set, and the file agrees (or is absent and will be
                    written). The key exists in TWO places: a wiped ``.env``
                    recovers from the file, a wiped ``data/`` recovers from
                    the environment.
        file_only   env unset, file present. Survives a wiped ``.env``. A
                    wiped ``data/`` is UNRECOVERABLE -- every vault entry and
                    every linked account's keys become permanently unreadable.
        diverged    env set and the file holds a DIFFERENT key. The env key
                    wins and the loader is about to overwrite the file with
                    it, so anything encrypted under the old one is orphaned.
                    The only state here that means something is already wrong.
        absent      env unset, no file. Nothing is encrypted yet; the next
                    load generates one and lands in ``file_only``.
        unreadable  the file could not be read, or RUNECLAW_SECRETS_KEY is not
                    a valid Fernet key. NOT a milder "absent": there may be
                    ciphertext that depends on what is in there.

    ``prior_backup`` IS PART OF THE READING, and driving the states is what
    showed why. ``diverged`` is transient: the loader overwrites the file on
    the first boot that sees it, after which this reports the healthiest state
    there is -- ``pinned`` -- at the exact moment every existing ciphertext
    stopped opening. The ``.bak`` the loader keeps is the durable evidence, and
    it is only ever written when a key was destroyed, so its presence (and
    whose fingerprint it holds) belongs on the same reading rather than being
    something an operator has to know to go looking for.

    Never raises and never returns key material -- only a fingerprint, which
    is what makes "is the key I pinned the one being used?" answerable at all.
    """
    out: dict = {"state": "unreadable", "env_set": False, "file_present": False,
                 "fingerprint": None, "survives_env_wipe": None,
                 "survives_data_wipe": None, "prior_backup": None, "detail": ""}
    try:
        env_key = os.environ.get("RUNECLAW_SECRETS_KEY", "").strip()
        out["env_set"] = bool(env_key)

        file_key = b""
        p = Path(key_file)
        try:
            if p.exists():
                out["file_present"] = True
                file_key = p.read_bytes().strip()
        except OSError as exc:
            out["detail"] = f"the key file could not be read ({type(exc).__name__})"
            return out

        # Written only when a key was about to be destroyed, so its existence
        # is a fact about this box's history that outlives the state that
        # created it.
        try:
            bak = Path(str(p) + ".bak")
            if bak.exists():
                prior = bak.read_bytes().strip()
                if prior:
                    out["prior_backup"] = {"path": str(bak),
                                           "fingerprint": _fingerprint(prior)}
        except OSError:
            pass

        if env_key:
            try:
                from cryptography.fernet import Fernet
                Fernet(env_key.encode())
            except Exception:
                # The loader raises on this, so the bot will not start -- but
                # the reason belongs on a surface, not only in a traceback.
                out["detail"] = ("RUNECLAW_SECRETS_KEY is set but is not a valid "
                                 "Fernet key; the loader refuses it rather than "
                                 "silently falling back to a different key")
                return out
            out["fingerprint"] = _fingerprint(env_key.encode())
            if file_key and file_key != env_key.encode():
                out.update(state="diverged", survives_env_wipe=False,
                           survives_data_wipe=True,
                           detail=("the key file holds a DIFFERENT key "
                                   f"(fingerprint {_fingerprint(file_key)}); the "
                                   "environment wins and the file is replaced, so "
                                   "anything encrypted under the old key is "
                                   "orphaned"))
            else:
                out.update(state="pinned", survives_env_wipe=True,
                           survives_data_wipe=True,
                           detail="the key is in the environment and mirrored to disk")
            return out

        if file_key:
            out.update(state="file_only", fingerprint=_fingerprint(file_key),
                       survives_env_wipe=True, survives_data_wipe=False,
                       detail=("generated or inherited; this file is the ONLY "
                               "copy, so wiping the data dir loses every stored "
                               "secret permanently"))
            return out

        out.update(state="absent", survives_env_wipe=None, survives_data_wipe=None,
                   detail="no key yet; the next load generates one into the data dir")
        return out
    except Exception as exc:  # pragma: no cover - a status read must never raise
        log.debug("master key state failed: %s", exc)
        out["detail"] = "the master key state could not be determined"
        return out


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
            prior = p.read_bytes().strip() if p.exists() else b""
            if prior != env_key.encode():
                p.parent.mkdir(parents=True, exist_ok=True)
                # KEEP THE KEY YOU ARE ABOUT TO DESTROY. `secrets_vault` was
                # hardened against exactly this shape and its note is in
                # CLAUDE.md: "One boot erased the lot, permanently, from a file
                # with no .bak." Here the casualty is worse than an entry -- it
                # is the key that reads every entry, every linked account and
                # the llm_api_key column. A wrong or stale RUNECLAW_SECRETS_KEY
                # (a copy-paste, a rotated deploy secret, a stale compose file)
                # silently replaced the only copy of the one that still opened
                # the data, and no amount of unsetting the variable afterwards
                # brought it back.
                #
                # Both files are 0600 and neither is logged. Two copies of a key
                # on one disk is a real cost; it is smaller than the operator
                # having no path back from a typo.
                if prior:
                    try:
                        bak = Path(str(p) + ".bak")
                        bak.write_bytes(prior)
                        try:
                            os.chmod(str(bak), 0o600)
                        except OSError:
                            pass
                        log.warning(
                            "RUNECLAW_SECRETS_KEY (fingerprint %s) differs from the "
                            "key on disk (fingerprint %s). The environment wins; "
                            "the previous key is kept at %s so data encrypted "
                            "under it is still recoverable. If that was not "
                            "intended, restore it before writing anything new.",
                            _fingerprint(env_key.encode()), _fingerprint(prior),
                            str(bak))
                    except OSError as exc:
                        # Refuse to destroy what we could not copy.
                        log.error(
                            "RUNECLAW_SECRETS_KEY differs from %s and the previous "
                            "key could NOT be backed up (%s) — leaving the file "
                            "alone. The environment key is in force this run.",
                            key_file, exc)
                        return env_key.encode()
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
        from bot.core.venues import per_user_execution_refusal
        balances_only = per_user_execution_refusal(venue) is not None
        with self._lock:
            # MERGE into the user's venue map (multi-venue): connecting Bybit
            # must never wipe Bitget's stored keys. The just-connected venue
            # becomes the ACTIVE one — submitting keys for a venue is the user
            # saying "trade here", and the executor rebuild check follows the
            # active view. set_active() switches back without re-entering keys.
            #
            # Except a venue no order routes to. Linking Bybit EU (or OKX,
            # Gate, KuCoin, Paradex) to read its balance made it active, and
            # the engine builds no executor for it, so the user's live trading
            # on the venue they already had stopped, with nothing on the
            # connect card saying so. It is active only as a user's first
            # venue, where the new record is created with it active and there
            # was nothing to take over.
            rec = self._normalize(self._enc.get(str(telegram_id)) or {"active": venue, "venues": {}})
            rec["venues"][venue] = enc
            if not balances_only:
                rec["active"] = venue
            self._enc[str(telegram_id)] = rec
            self._save()
        log.info("Stored encrypted %s credentials for user %s", venue, telegram_id)

    # -- readings -------------------------------------------------------------
    #
    # AN UNREADABLE RECORD IS NOT AN ABSENT ONE, AND THE DIFFERENCE IS WHETHER
    # THE USER HAS TO DO ANYTHING.
    #
    # `_load` says the same sentence one level up, about the FILE. This is the
    # record level, and it was the half nothing could express: `get()` returned
    # None both for "no credentials" and for "stored, but this store cannot
    # decrypt them", and its own docstring resolved the two into "the caller
    # treats that as 'not connected'". Meanwhile `has()` and `list_venues()`
    # read `_enc` without decrypting anything, so they answer CONNECTED for the
    # very same user. Two surfaces of one bot, opposite answers, and the second
    # factor of neither.
    #
    # It is not exotic. `_load_or_create_master_key` GENERATES a new key when
    # RUNECLAW_SECRETS_KEY is unset and the data dir was wiped — its own warning
    # says so — and every record then stops decrypting at once, with the file
    # itself perfectly readable.

    def _decrypt_fields(self, venue: str, fields_enc: dict,
                        telegram_id="", log_failure: bool = True) -> Optional[dict]:
        """The venue's plaintext fields, or None when this store cannot produce
        them. THE one test — every reading below and both getters derive their
        answer from this call, so no two of them can disagree about a record.

        The id rides along ONLY to be logged. Both getters carried it in their
        own log lines before they were folded into this one, and dropping it
        would leave an operator reading "a credential failed to decrypt" with no
        way to tell whether that is one stale account or every account at once —
        which are the two things a wiped data dir looks like from the log.
        """
        field_names = _VENUE_FIELDS.get(venue, _FIELDS)
        try:
            c = self._cipher()
            return {f: c.decrypt(fields_enc[f].encode()).decode() for f in field_names}
        except Exception as exc:  # InvalidToken, missing field, missing crypto
            if log_failure:
                log.error("Failed to decrypt %s credentials for %s: %s",
                          venue, telegram_id or "<unknown user>", exc)
            return None

    def venue_states(self, telegram_id, *, log_failure: bool = True) -> dict:
        """``{venue: "readable" | "unreadable"}`` for every venue stored.

        Empty when nothing is stored — which is the third state, expressed by
        the map being empty rather than by a word, because "this user has no
        venues" is not a property of any venue. ``log_failure=False`` is for a
        reader that asks on a timer (the website report), which would
        otherwise log the same undecryptable record every two minutes.
        """
        with self._lock:
            enc = self._enc.get(str(telegram_id))
        if not enc:
            return {}
        out = {}
        for venue, fields_enc in self._normalize(enc)["venues"].items():
            out[venue] = ("readable"
                          if self._decrypt_fields(venue, fields_enc, telegram_id,
                                                  log_failure=log_failure)
                          else "unreadable")
        return out

    def readable_venues(self, telegram_id) -> list:
        """The venues whose credentials this store can actually produce.

        `list_venues()` answers "which venues have a record", which is what
        routing was asking and is not the same question: a venue whose keys
        stopped decrypting stayed in that list, so it stayed in the routing set
        and never reached the caller's `dropped` — the exact silence
        `venue_selection`'s docstring is written against.
        """
        return sorted(v for v, s in self.venue_states(telegram_id).items()
                      if s == "readable")

    def credential_state(self, telegram_id) -> str:
        """``"readable" | "unreadable" | "absent"`` for the ACTIVE venue.

        The reading a status surface wants. "unreadable" is not a milder
        "absent": absent means the user never connected and `/connect` is an
        invitation, unreadable means they did and something on this side broke,
        which is a different sentence and a different remedy.
        """
        with self._lock:
            enc = self._enc.get(str(telegram_id))
        if not enc:
            return "absent"
        venue, fields_enc = self._read_record(enc)
        if not fields_enc:
            return "absent"
        return ("readable" if self._decrypt_fields(venue, fields_enc, telegram_id)
                else "unreadable")

    def has(self, telegram_id) -> bool:
        """Whether a RECORD exists. Says nothing about whether it can be read —
        use `credential_state()` for anything a user or operator will see, and
        `readable_venues()` for anything that decides where an order goes."""
        with self._lock:
            return str(telegram_id) in self._enc

    @property
    def file_unreadable(self) -> bool:
        """True when the store FILE could not be read (see ``_load``). The map
        is then empty, and that is not "nobody linked a key": a reader that
        reports on every user must say nothing rather than report none."""
        return bool(getattr(self, "_load_failed", False))

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
        changed).

        NONE STILL MEANS BOTH, and it has to: the execution layer's question is
        "can I trade with this", and the answer is no either way. What changed
        is that None is no longer the ONLY thing anyone can ask — a surface that
        has to say WHY calls `credential_state()`, and routing calls
        `readable_venues()`. This docstring used to end "the caller treats that
        as 'not connected'", which is the conflation those two exist to undo.
        """
        with self._lock:
            enc = self._enc.get(str(telegram_id))
        if not enc:
            return None
        venue, fields_enc = self._read_record(enc)
        return self._decrypt_fields(venue, fields_enc, telegram_id)

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
        return self._decrypt_fields(venue, fields_enc, telegram_id)

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

        `""` MEANS EITHER "nothing stored" OR "stored but undecryptable", since
        it is built from `get()`. That is fine for a fingerprint — there is no
        third string to return — but a caller that prints it beside a STATUS
        must decide the status from `credential_state()`, not from a presence
        test. `/exchange` did the latter and rendered "connected" above an empty
        `Key:` line: a heading that announces itself and then says nothing.
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

# Bitget's other refusals of a key, as instructions. The reply was Bitget's
# body ("AuthenticationError: bitget {"code":"40012","msg":"apikey/password is
# incorrect",...}"). The codes and their meaning are Bitget's, as ccxt's
# bitget driver maps them (40006 "Invalid ACCESS_KEY", 40009/40010 "sign
# signature error", 40012 "apikey/password is incorrect", 40014 "Incorrect
# permissions", 40018 "Invalid IP", 40037 "Apikey does not exist", 40004/40008
# "Request timestamp expired"). Each fits the 180 characters the website's ack
# carries to the card.
_BITGET_REFUSALS: dict[int, str] = {
    40006: ("Bitget does not know this API key (code 40006). Check it was copied whole "
            "from Bitget's API Management page."),
    40037: ("Bitget has no such API key (code 40037): it was mistyped or deleted. "
            "Create a new one in API Management."),
    40009: ("Bitget refused the signature (code 40009): this API secret is not the key's. "
            "Check the key and secret were not swapped."),
    40010: ("Bitget refused the signature (code 40010): this API secret is not the key's. "
            "Check the key and secret were not swapped."),
    40012: ("Bitget refused this key with this passphrase (code 40012). The passphrase is "
            "the one set when the key was made, not the account password."),
    40014: ("Bitget says this key lacks permission (code 40014). It needs read and trade "
            "permission on USDT-M futures."),
    40018: ("Bitget refused this server's IP (code 40018): the key is bound to IP addresses "
            "and this server's is not one of them."),
    40004: ("Bitget refused the request's timestamp (code 40004): this server's clock is off. "
            "That is the bot operator's to fix, not your key."),
    40008: ("Bitget refused the request's timestamp (code 40008): this server's clock is off. "
            "That is the bot operator's to fix, not your key."),
}


def _bitget_refusal(detail: str) -> str:
    """Bitget's refusal said as an instruction, or ``detail`` unchanged.

    The code is read off the probe's own detail, as the 40099 path above reads
    it, so a probe stand-in that returns Bitget's body is translated the same
    way the real one is."""
    m = re.search(r'"code"\s*:\s*"?(\d+)', str(detail or ""))
    code = int(m.group(1)) if m else None
    return _BITGET_REFUSALS.get(code, detail) if code is not None else detail


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
        return True, _free_detail(bal, "USDT")
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


def bitget_account_uid(info: Any) -> Optional[str]:
    """Bitget's ``userId`` from an account-info reply, or None when absent."""
    if not isinstance(info, dict):
        return None
    uid = str(info.get("userId") or "").strip()
    return uid or None


async def probe_bitget_key_scope(api_key: str, api_secret: str, passphrase: str,
                                 sandbox: bool = False) -> dict:
    """Observe a Bitget key's granted scope. READ-ONLY, and never raises.

    Returns ``{"withdraw": "on"|"off"|"unknown", "ip_allowlist": [...]|None,
    "account_uid": str|None}``. No withdrawal is attempted and no order is
    placed — the scope is *asked for*, never *tested*. Every failure path (ccxt
    missing, endpoint absent on this ccxt version, HTTP error, a response shape
    we do not recognise) lands on ``"unknown"``/``None``, so the worst case is
    exactly the information we had before calling it.

    ``account_uid`` is Bitget's ``userId`` for the account the key opens: a
    sub-account has its own, and it is the same whichever API key opens it.
    It is how two different keys are recognised as one account
    (`same_bitget_account_as_operator`).
    """
    out: dict = {"withdraw": "unknown", "ip_allowlist": None, "account_uid": None}
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
        out["account_uid"] = bitget_account_uid(data)
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


# ── one account, one executor ────────────────────────────────────────────────
#
# 8 October: the operator linked NEW API credentials for the SAME Bitget
# sub-account with /connect. The operator's executor (the .env keys) and a
# per-user executor (the linked keys) then managed one account. Each took the
# other's orders for strangers: one adopted the other's resting OPEN/USDT limit
# with no stop on record, booked its fill with stop 0, and kept that record
# "open" after the other executor closed the position, so the unprotected
# alert told a person to place a stop on a position that no longer existed.
# Adopting a position also places a default stop, and placing one cancels the
# trigger orders it finds, so the two could take each other's stops off.
#
# Keys are not the identity: rotated credentials differ while the account is
# the same. Bitget's account UID is the identity.
OPERATOR_ACCOUNT_REFUSAL = (
    "These keys open the operator's own Bitget account, which the bot already "
    "trades. Linking it again would put two executors on one account. Nothing "
    "was stored.")


def _operator_bitget_fields(cfg: Any = None) -> Optional[dict]:
    """The operator's Bitget keys from the running config, or None when unset."""
    if cfg is None:
        from bot.config import CONFIG
        cfg = CONFIG.exchange
    key = str(getattr(cfg, "api_key", "") or "")
    secret = str(getattr(cfg, "api_secret", "") or "")
    passphrase = str(getattr(cfg, "passphrase", "") or "")
    if not (key and secret and passphrase):
        return None
    return {"api_key": key, "api_secret": secret, "passphrase": passphrase}


async def same_bitget_account_as_operator(fields: dict, *, cfg: Any = None,
                                          probe=None) -> Optional[bool]:
    """Whether ``fields`` open the operator's own Bitget account.

    True or False when both accounts' UIDs were read; None when either could
    not be (no operator keys, a key without account-read permission, a venue
    outage). None is not False: the callers say what they could not check
    rather than call the account a different one. Read-only, never raises.
    """
    probe = probe or probe_bitget_key_scope
    op = _operator_bitget_fields(cfg)
    if op is None:
        return None
    sandbox = venue_sandbox("bitget", cfg)
    try:
        theirs = (await probe(fields["api_key"], fields["api_secret"],
                              fields["passphrase"], sandbox=sandbox)).get("account_uid")
        ours = (await probe(op["api_key"], op["api_secret"], op["passphrase"],
                            sandbox=sandbox)).get("account_uid")
    except Exception:
        return None
    if not theirs or not ours:
        return None
    return str(theirs) == str(ours)


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
    if ok:
        return ok, detail
    if _WRONG_ENV_CODE not in detail:
        return False, _bitget_refusal(detail)
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
        return True, _free_detail(bal, "USDC")
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
    """Validate Hyperliquid credentials before they are stored. Returns
    ``(ok, detail)``; never places an order.

    WHAT THE BALANCE PROBE PROVES IS THE WALLET, NOT THE KEY. Hyperliquid's
    balance is a public read keyed on the wallet address; the agent key is
    not signed with at all. This docstring used to say the probe "proves the
    agent key authenticates for the wallet", and driven, a key above the
    curve order (0xff…ff, which cannot sign anything) came back
    ``(True, "123.00 USDC free")`` and was encrypted and stored, to fail at
    the first signed action — a stop, on the money path. A MASTER key was
    stored the same way and refused only when a client was built.

    So the key's ROLE is read first (`hyperliquid_key_role`), three outcomes
    in the /setsigner shape:

      * confirmed — the signing library derived the key's address and it is
        a different account from the wallet: an agent key;
      * well-formed but unconfirmed — the arithmetic says it can sign, and no
        library is installed to say WHICH account it signs for (the CI
        install); stored, and the detail says so rather than implying a check
        that did not happen;
      * rejected — not a signing key, or the wallet's own master key, refused
        in the words the client constructor uses, quoting none of the key.

    That the key AUTHENTICATES for this wallet is proven only at the first
    signed action; nothing here can prove it without placing something.
    """
    from bot.core.venues import hyperliquid_key_refusal, hyperliquid_key_role
    role = hyperliquid_key_role(agent_private_key, wallet_address)
    refusal = hyperliquid_key_refusal(role)
    if refusal:
        return False, refusal
    ok, detail = await _hyperliquid_balance_probe(wallet_address, agent_private_key, sandbox)
    if ok and role == "unconfirmed":
        detail = (f"{detail}. The key is well-formed; no signing library is "
                  f"installed here to confirm which account it signs for, so the "
                  f"first order is where that is proven")
    return ok, detail


async def _keysecret_balance_probe(exchange_id: str, api_key: str,
                                   api_secret: str, sandbox: bool, *,
                                   demo: bool = False,
                                   client_config: Optional[dict] = None,
                                   detail_of: Optional[Callable[[Any], str]] = None,
                                   refusal: Optional[dict] = None) -> tuple[bool, str]:
    """Read-only balance fetch for a plain key+secret ccxt venue (Bybit, BingX).

    Both are USDT-margined swap exchanges that authenticate with apiKey/secret
    only. Returns (ok, detail). Never places an order. ``demo`` points a Bybit
    client at Demo Trading (api-demo.bybit.com). ``client_config`` is merged
    over the client's construction (Bybit EU's host and market type), and
    ``detail_of`` turns a successful reply into the detail instead of the
    USDT line. ``refusal``, when given, gets ``{"code": <retCode>}`` from a
    Bybit refusal, so the caller can say what it means without parsing the
    display string."""
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
            **(client_config or {}),
        })
        try:
            client.set_sandbox_mode(sandbox)
        except Exception:
            if sandbox:
                raise
        if demo:
            client.enable_demo_trading(True)
        bal = await client.fetch_balance()
        if detail_of is not None:
            return True, detail_of(bal)
        return True, _free_detail(bal, "USDT")
    except Exception as exc:
        if refusal is not None:
            m = re.search(r'"retCode"\s*:\s*(\d+)', str(exc))
            refusal["code"] = int(m.group(1)) if m else None
        return False, _safe_venue_detail(exc)
    finally:
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass


# ── Bybit's refusals, as instructions ────────────────────────────────────────
#
# Bybit answers a key it will not take with a JSON body whose retCode says why,
# and that body was the whole reply: "AuthenticationError: bybit
# {"retCode":10003,"retMsg":"API key is invalid.",...}". The codes are Bybit's
# own (https://bybit-exchange.github.io/docs/v5/error). 10003 reads in full
# "API key is invalid. Check whether the key and domain are matched, there are
# 4 env: mainnet, testnet, mainnet-demo, testnet-demo"; the regional sites
# (bybit.tr, .kz, .ae, .eu, bybitgeorgia.ge, …) have API hosts of their own
# (https://bybit-exchange.github.io/docs/v5/guide). Each sentence fits the 180
# characters the website's ack carries to the card.
_BYBIT_REFUSALS: dict[int, str] = {
    10003: ("Bybit {env} does not know this API key (code 10003). Keys from another "
            "environment (testnet, Demo Trading) or a regional Bybit (EU, TR, KZ, AE…) fail here."),
    10004: ("Bybit refused the signature (code 10004): this API secret is not the key's. "
            "Check the key and secret were not swapped."),
    10005: ("Bybit says this key lacks permission (code 10005). It needs read and trade "
            "permission on derivatives (contracts)."),
    10010: ("Bybit refused this server's IP (code 10010): the key is bound to IP addresses "
            "and this server's is not one of them."),
    33004: "This Bybit API key has expired (code 33004). Create a new one.",
}
_BYBIT_TESTNET_KEY = ("These are Bybit testnet keys, and this bot trades Bybit mainnet. "
                      "Create the key in your main account on bybit.com (API Management).")
_BYBIT_DEMO_KEY = ("These are Bybit Demo Trading keys, and this bot trades Bybit mainnet. "
                   "Create the key in your main account, not inside Demo Trading.")
# Reported 8 October: the operator's key answered 10003 on bybit.com because it
# was a Bybit EU key. Bybit EU lists no perpetual futures, so this is not a key
# to fix: the venue cannot take this bot's orders. It can be linked to read.
_BYBIT_EU_KEY = ("This is a Bybit EU key. Bybit EU offers spot trading only, not the "
                 "perpetual futures this bot trades. Connect it as Bybit EU to read its balance.")
# The two refusals that read differently on Bybit EU; the rest are Bybit's.
_BYBIT_EU_REFUSALS: dict[int, str] = {
    10003: ("Bybit EU does not know this API key (code 10003). A key from bybit.com "
            "is not a Bybit EU key: connect that one as Bybit."),
    10005: ("Bybit EU says this key lacks permission (code 10005). Read permission is "
            "enough: Bybit EU is linked for balances only."),
}


def _bybit_total_equity(bal: Any) -> Optional[float]:
    """Bybit's own USD valuation of the whole wallet (``totalEquity``), or None.

    Read from the raw wallet-balance reply ccxt keeps under ``info``. A Bybit EU
    wallet may hold euros and MiCA-compliant stablecoins rather than USDT, so
    its USDT line, the figure every other venue reads, is not its balance.
    Absent, empty or unparseable is None: a reply with no figure is not an
    empty account.
    """
    try:
        rows = bal["info"]["result"]["list"]
        return read_money_field(rows[0], "totalEquity") if rows else None
    except (KeyError, IndexError, TypeError):
        return None


def _bybit_equity_detail(bal: Any) -> str:
    equity = _bybit_total_equity(bal)
    if equity is None:
        return "authenticated, but no readable total equity in Bybit EU's reply"
    return f"{equity:.2f} USD total equity"


async def validate_bybit_credentials(api_key: str, api_secret: str,
                                     sandbox: bool = False) -> tuple[bool, str]:
    """Read-only-validate a Bybit key, and say what a refusal means.

    A 10003 on mainnet is retried on Bybit EU, on testnet and on Demo Trading,
    purely to diagnose, as Bitget's 40099 is: a key that answers there is
    still refused here, with the place it belongs to named. Never stores
    anything and never places an order.
    """
    from bot.core.venues import bybit_eu_client_config
    seen: dict = {}
    ok, detail = await _keysecret_balance_probe("bybit", api_key, api_secret, sandbox,
                                                refusal=seen)
    code = seen.get("code")
    if ok or code not in _BYBIT_REFUSALS:
        return ok, detail
    if code == 10003 and not sandbox:
        if (await _keysecret_balance_probe("bybit", api_key, api_secret, False,
                                           client_config=bybit_eu_client_config()))[0]:
            return False, _BYBIT_EU_KEY
        if (await _keysecret_balance_probe("bybit", api_key, api_secret, True))[0]:
            return False, _BYBIT_TESTNET_KEY
        if (await _keysecret_balance_probe("bybit", api_key, api_secret, False, demo=True))[0]:
            return False, _BYBIT_DEMO_KEY
    return False, _BYBIT_REFUSALS[code].format(env="testnet" if sandbox else "mainnet")


async def validate_bybit_eu_credentials(api_key: str, api_secret: str) -> tuple[bool, str]:
    """Read-only-validate a Bybit EU key, on api.bybit.eu, for balances.

    Success says the wallet's total equity in USD. A refusal is said as an
    instruction; Bybit EU has no testnet, so nothing else is asked. Never
    stores anything and never places an order.
    """
    from bot.core.venues import bybit_eu_client_config
    seen: dict = {}
    ok, detail = await _keysecret_balance_probe(
        "bybit", api_key, api_secret, False, client_config=bybit_eu_client_config(),
        detail_of=_bybit_equity_detail, refusal=seen)
    code = seen.get("code")
    if ok or code not in _BYBIT_REFUSALS:
        return ok, detail
    return False, _BYBIT_EU_REFUSALS.get(code) or _BYBIT_REFUSALS[code].format(env="EU")


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
        return True, _free_detail(bal, "USDT")
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
        return True, _free_detail(bal, currency)
    except Exception as exc:
        return False, _safe_venue_detail(exc)
    finally:
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass


def venue_sandbox(venue: str, cfg: Any = None) -> bool:
    """Whether ``venue``'s client runs in its test or demo environment.

    The adapter's own reading (``Venue.uses_sandbox``), so a key is checked,
    and a balance read, where the venue's client will trade. ``cfg`` defaults
    to the running ``CONFIG.exchange``. The callers used to pass
    ``CONFIG.exchange.sandbox`` (BITGET_SANDBOX) for every venue, which Bybit's
    and BingX's clients never read and Hyperliquid's reads under its own flag.
    """
    if cfg is None:
        from bot.config import CONFIG
        cfg = CONFIG.exchange
    from bot.core.venues import venue_uses_sandbox
    return venue_uses_sandbox(venue, cfg)


async def validate_venue_credentials(venue: str, fields: dict,
                                     sandbox: Optional[bool] = None) -> tuple[bool, str]:
    """Read-only-validate a user's credentials for ``venue`` (dispatches to the
    per-venue probe). Returns (ok, detail). Never places an order.

    ``sandbox=None``, the default, checks the key in the environment the
    venue's client trades in (``venue_sandbox``). An explicit bool is for a
    caller asking about the other environment on purpose."""
    venue = str(venue).lower().strip()
    if sandbox is None:
        sandbox = venue_sandbox(venue)
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
    if venue == "bybit":
        return await validate_bybit_credentials(
            fields["api_key"], fields["api_secret"], sandbox)
    if venue == "bybiteu":
        return await validate_bybit_eu_credentials(fields["api_key"], fields["api_secret"])
    if venue == "bingx":
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
                           sandbox: Optional[bool] = None) -> dict:
    """READ-ONLY equity snapshot for a user's stored venue credentials.

    One fetch_balance — the exact same call the connect-time validators
    make — returning numbers instead of a validation string:
    ``{ok, venue, currency, equity_usd, detail}``. Never raises, never
    writes, never places an order; credentials are used in-process only
    and never appear in the returned dict.

    ``sandbox=None``, the default, reads the environment the venue's client
    trades in (``venue_sandbox``). It used to default to live for every
    venue, so under Bitget demo trading a user's Bitget card read the live
    account their demo key does not open.
    """
    venue = str(venue).lower().strip()
    client = None
    try:
        import ccxt.async_support as ccxt
    except Exception as exc:  # pragma: no cover - import guard
        return {"ok": False, "venue": venue, "equity_usd": None,
                "detail": f"ccxt unavailable: {exc}"}
    # Bybit EU is read as Bybit's own USD valuation of the wallet
    # (`_bybit_total_equity`): its USDT line is not its balance.
    currency = ("USDC" if venue in ("hyperliquid", "paradex")
                else "USD" if venue == "bybiteu" else "USDT")
    try:
        if sandbox is None:
            sandbox = venue_sandbox(venue)
        if venue == "bybiteu":
            from bot.core.venues import bybit_eu_client_config
            client = ccxt.bybit({
                "apiKey": fields["api_key"], "secret": fields["api_secret"],
                "timeout": 15000, "enableRateLimit": True,
                **bybit_eu_client_config(),
            })
        elif venue == "bitget":
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
        equity = (_bybit_total_equity(bal) if venue == "bybiteu"
                  else _balance_total(bal, currency))
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
    if venue in ("bybit", "bybiteu", "bingx", "gate"):
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
