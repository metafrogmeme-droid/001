"""
Pull pending exchange-credential requests from the website and apply them.

Stage 2b of web wallet management. The website (Stage 2a) seals a user's
submitted exchange keys at rest and queues a `pending_credentials` row; this
module PULLS those rows over the shared-secret channel, opens them, optionally
validates them against the venue, imports them into the bot's own
Fernet-encrypted ExchangeCredentialStore (keyed by telegram_id), and ACKs so
the website clears the row and flips the user's connection status.

Two envelopes are accepted. ``v: 2`` is sealed to the bot's OWN key
(bot/utils/creds_sealing.py) — the default since 2026-09, and the reason the
connect form needs nothing configured by hand: this module publishes the
public half to the website over the same channel on the first pull after boot
and hourly after. ``v: 1`` is the legacy AES-256-GCM envelope under the shared
WEB_CREDS_KEY, still opened when that key is set, for rows an older website
wrote.

Security: the website never holds the long-term keys (the bot's Fernet store is
the single owner) and, on the sealed path, cannot read what it stored either;
raw keys are never logged here; a corrupt/undecryptable row is ACKed as failed
(not retried forever); a transient validation failure is left un-ACKed so it
retries next poll.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Callable, Optional

from bot.utils.creds_sealing import SealingKeyUnavailable
from bot.utils.site_url import site_url

log = logging.getLogger(__name__)

WEBSITE_URL = site_url()
SYNC_SECRET = os.getenv("BOT_SYNC_SECRET", "")

#: How often the public sealing key is re-published to the website when it
#: has not changed — a website whose database was reset would otherwise wait
#: for the bot's next restart before its connect form worked again.
PUBLISH_EVERY_S = 3600.0

#: How long to wait after a REFUSED publish before trying again, and how often
#: to say so. The pull runs every 30s, and the realistic failure here is a
#: website that has not been redeployed yet — its 404 would otherwise produce
#: 2,880 identical warnings a day, which is how an operator learns to skip
#: warnings. Short enough that a site coming up is picked up in two minutes.
PUBLISH_RETRY_S = 120.0


def _fresh_publish_state() -> dict:
    """The publisher's whole memory, in one place.

    A function rather than a literal because tests reset it, and a test that
    hand-built a dict with one key missing would fail on a KeyError three
    releases later instead of the day the field was added.
    """
    return {"kid": None, "at": 0.0, "tried_kid": None, "tried": 0.0, "warned": 0.0}


_published: dict = _fresh_publish_state()


def _load_key() -> Optional[bytes]:
    """The legacy shared AES key (WEB_CREDS_KEY): base64 (standard or url-safe) 32 bytes."""
    raw = (os.getenv("WEB_CREDS_KEY", "") or "").strip()
    if not raw:
        return None
    b64 = raw.replace("-", "+").replace("_", "/")
    try:
        key = base64.b64decode(b64)
    except Exception:
        return None
    return key if len(key) == 32 else None


def is_configured() -> bool:
    """The pull needs only the sync channel: the sealed envelope needs no
    shared key, and the bot's sealing key is created on first use."""
    return bool(SYNC_SECRET)


def decrypt_payload(envelope) -> dict:
    """Open a website envelope.

    ``v: 2`` is sealed to the bot's own key and handed to
    ``creds_sealing.unseal``. ``v: 1`` is the shared-key envelope; it mirrors
    app/lib/creds_crypto.js — Node splits the GCM tag out, so Python's AESGCM
    (which expects ciphertext||tag) gets ``ct + tag``.
    """
    e = json.loads(envelope) if isinstance(envelope, str) else envelope
    if isinstance(e, dict) and int(e.get("v", 1) or 1) == 2:
        from bot.utils.creds_sealing import unseal
        return unseal(e)
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = _load_key()
    if not key:
        raise ValueError("WEB_CREDS_KEY missing or not a 32-byte base64 key")
    iv = base64.b64decode(e["iv"])
    tag = base64.b64decode(e["tag"])
    ct = base64.b64decode(e["ct"])
    pt = AESGCM(key).decrypt(iv, ct + tag, None)
    return json.loads(pt.decode())


def default_validator(creds: dict):
    """Read-only-check submitted keys against the venue. Runs on a WORKER THREAD.

    Returns ``(True, "")`` / ``(False, reason)`` / ``(None, reason)`` — the
    three-outcome contract ``process_pending`` documents, with the venue's own
    words for the rejection so the website can tell the user what to fix
    instead of leaving the card at "applying…" forever.

    THE TRAP THIS EXISTS TO AVOID, stated because it is invisible once made.
    ``validate_venue_credentials`` is ``async def``, and ``process_pending`` is
    synchronous code that ``engine._maybe_pull_web_credentials`` runs inside
    ``asyncio.to_thread``. Passing the coroutine FUNCTION as the validator
    would make ``verdict`` a coroutine OBJECT — which is truthy, so
    ``verdict is False`` and ``verdict is None`` are both False and EVERY
    submitted key would be imported as valid. The only trace would be a
    "coroutine was never awaited" warning in a log nobody greps. That is
    `bot/core/basis.py`'s documented failure (a sync call to an async factory,
    swallowed by a broad except, dead forever while looking wired) one door
    along, on the path that decides whose keys trade.

    ``asyncio.run`` is correct HERE and only here: this thread has no running
    loop of its own. Called from the event loop it would raise, which is why
    that case returns ``None`` (retry) rather than a verdict.
    """
    import asyncio as _asyncio

    try:
        from bot.config import CONFIG
        from bot.core.exchange_credentials import _VENUE_FIELDS, validate_venue_credentials
    except Exception as exc:                       # noqa: BLE001
        log.warning("credential validation unavailable (%s) — leaving the row queued", exc)
        return None, "validation unavailable"

    venue = str(creds.get("venue") or "bitget").lower()
    required = _VENUE_FIELDS.get(venue)
    if not required:
        # A venue the bot does not know is not a transient fault; it will
        # never become valid, so it is a verdict and the row must clear.
        return False, f"this bot does not support {venue}"
    fields = {k: creds.get(k) for k in required}

    try:
        ok, detail = _asyncio.run(
            validate_venue_credentials(venue, fields,
                                       sandbox=CONFIG.exchange.sandbox))
    except RuntimeError as exc:
        # "asyncio.run() cannot be called from a running event loop" — the
        # caller offloaded wrongly. NOT a verdict about the keys.
        log.error("credential validation ran on the event loop (%s) — row left queued", exc)
        return None, "validation could not run"
    except Exception as exc:                       # noqa: BLE001
        # Network, venue outage, timeout: the keys may be perfect. Leaving it
        # un-acked retries next poll rather than destroying the submission.
        log.warning("credential validation for %s could not complete: %s", venue, exc)
        return None, "could not reach the exchange"

    if ok:
        return True, ""
    # The venue's own words, bounded. Never the key material — `detail` comes
    # from the venue's error body, and the probes never echo the credential.
    return False, str(detail or "the exchange rejected these keys")[:180]


def process_pending(rows, store, validator: Optional[Callable[[dict], Optional[bool]]] = None,
                    on_change: Optional[Callable[[str], None]] = None) -> list[dict]:
    """Apply each pending row to ``store``; return the ack list for the website.

    ``validator(creds) -> True|False|None``: True = keys valid (import), False =
    keys rejected (ack failed, do NOT import), None = could not verify right now
    (transient) → SKIP without acking so it retries next poll.

    ``on_change(telegram_id)`` is called after a successful connect/disconnect so
    the caller can invalidate any cached per-user executor.

    THREE OUTCOMES PER ROW, not two. Acking clears the row on the website, so
    the difference between "this can never be opened" and "this could not be
    opened right now" is the difference between a user resubmitting once and a
    user's keys being thrown away with "connection failed".
    """
    acks: list[dict] = []
    for r in rows:
        uid = r.get("user_id")
        action = r.get("action") or "connect"
        tg = str(r.get("telegram_id") or "")
        try:
            if uid is None or not tg:
                continue
            if action == "disconnect":
                # Venue-scoped disconnect (multi-venue store): remove only the
                # named venue when the row carries one; a row without a venue
                # (legacy) removes everything, preserving old behavior.
                venue = str(r.get("exchange") or "").lower().strip()
                if venue and hasattr(store, "delete_venue"):
                    store.delete_venue(tg, venue)
                else:
                    store.delete(tg)
                acks.append({"user_id": uid, "action": "disconnect", "ok": True})
                if on_change:
                    on_change(tg)
                continue
            # connect
            creds = decrypt_payload(r.get("encrypted_payload"))
            # Venue defaults to bitget so existing (venue-less) web rows import
            # unchanged. Each venue requires its own field set.
            venue = str(creds.get("venue") or "bitget").lower()
            # Local import: keeps the web-pull module importable without pulling
            # in the crypto-backed store at module load.
            from bot.core.exchange_credentials import _VENUE_FIELDS
            required = _VENUE_FIELDS.get(venue)
            if required is None or not all(creds.get(k) for k in required):
                acks.append({"user_id": uid, "action": "connect", "ok": False,
                             "error": "incomplete credentials"})
                continue
            if validator is not None:
                # A validator may answer with a bare verdict or with
                # (verdict, reason). The pair is preferred: "the exchange
                # rejected these keys" and "IP not whitelisted" are different
                # instructions to the person who typed them, and the website
                # can only show what the ack carries.
                verdict = validator(creds)
                reason = ""
                if isinstance(verdict, tuple):
                    verdict, reason = (list(verdict) + [""])[:2]
                if verdict is False:
                    acks.append({"user_id": uid, "action": "connect", "ok": False,
                                 "error": str(reason or "key validation failed")[:180]})
                    continue
                if verdict is None:
                    # Transient — leave un-acked so the row is retried next poll.
                    continue
            if venue == "bitget":
                # Byte-identical legacy path (keeps the 3-positional store.set).
                store.set(tg, creds["api_key"], creds["api_secret"], creds["passphrase"])
            else:
                store.set_venue(tg, venue, {k: creds[k] for k in required})
            acks.append({"user_id": uid, "action": "connect", "ok": True})
            if on_change:
                on_change(tg)
        except SealingKeyUnavailable as exc:
            # NOT acked, so the website keeps the row and the next poll opens
            # it. Acking would clear a submission that is perfectly openable
            # once this bot can read its own key file again — and tell the
            # user their connection failed. A row sealed to a key we no longer
            # HOLD is a different exception and is acked below, correctly.
            log.error("credential pull: this bot cannot read its own sealing key (%s) — "
                      "leaving user=%s queued for the next poll", exc, uid)
            continue
        except Exception as exc:
            # A corrupt/undecryptable row would otherwise retry forever — ack it
            # as failed so the website clears it. Never logs the payload.
            log.warning("credential pull: failed row user=%s action=%s: %s", uid, action, exc)
            acks.append({"user_id": uid, "action": action, "ok": False, "error": "processing error"})
    return acks


def _request(path: str, data: Optional[dict] = None) -> Optional[dict]:
    url = f"{WEBSITE_URL}{path}"
    headers = {"Accept": "application/json", "User-Agent": "RUNECLAW-Bot/1.0",
               "X-Bot-Secret": SYNC_SECRET}
    body = None
    method = "GET"
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
        method = "POST"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        log.error("credential pull HTTP %s on %s", e.code, path)
        return None
    except Exception as exc:
        log.error("credential pull error on %s: %s", path, exc)
        return None


def publish_sealing_key(force: bool = False) -> bool:
    """Hand the website the PUBLIC half of the bot's sealing key.

    This is what turns the website's connect form on: until the site holds
    the key it refuses submissions (it has nothing safe to seal to). Sent on
    the first pull after boot, again whenever the key changed (a regenerated
    key after a wiped data/), and hourly otherwise — a website whose database
    was reset gets it back without a bot restart. Fail-open: a site that is
    down leaves the form as it was and a later pull tries again. Returns
    True when the website acknowledged the key.

    FAIL-OPEN ON THE CALL, FAIL-CLOSED ON THE RECORD. A refused publish never
    stamps ``_published``, so nothing here can report a key the website does
    not hold; it only backs the RETRY off, because the pull ticks every 30s
    and the realistic refusal is a website still running the build that has no
    such endpoint.
    """
    if not SYNC_SECRET:
        return False
    try:
        from bot.utils.creds_sealing import public_key_record
        rec = public_key_record()
    except Exception as exc:
        log.warning("sealing key unavailable; the website's connect form stays off: %s", exc)
        return False
    now = time.monotonic()
    # Already published, and not yet due for the hourly refresh.
    if (not force and _published["kid"] == rec["kid"]
            and now - _published["at"] < PUBLISH_EVERY_S):
        return True
    # A refusal for THIS key backs off; a NEW key always goes out at once. The
    # regenerated key after a wiped data/ is the case that most needs to reach
    # the website promptly — every queued row is sealed to a key the bot no
    # longer holds until it does — and a plain timer would delay exactly that.
    if (not force and _published["tried_kid"] == rec["kid"]
            and now - _published["tried"] < PUBLISH_RETRY_S):
        return False
    _published["tried_kid"], _published["tried"] = rec["kid"], now
    resp = _request("/api/bot/sync/credentials/sealing-key", rec)
    ok = bool(resp and resp.get("ok"))
    if ok:
        if _published["kid"] != rec["kid"]:
            log.info("Published the website sealing key (kid %s)", rec["kid"])
        _published["kid"], _published["at"] = rec["kid"], now
        # Reset, so the NEXT time it starts refusing says so immediately
        # rather than sitting inside an hour-old quiet period.
        _published["warned"] = 0.0
    elif _published["warned"] == 0.0 or now - _published["warned"] >= PUBLISH_EVERY_S:
        # Once an hour, not every 30 seconds. The line an operator needs is
        # that the form is off and why; repeating it 2,880 times a day is how
        # they learn to scroll past it.
        _published["warned"] = now
        log.warning("the website did not accept the sealing key (kid %s) — its exchange-key "
                    "connect form stays off until it does; retrying every %.0fs",
                    rec["kid"], PUBLISH_RETRY_S)
    return ok


def pull_and_apply(store=None, validator=None, on_change=None) -> int:
    """Fetch pending credential requests, apply them, ack. Returns #acked.

    No-op (returns 0) when BOT_SYNC_SECRET is unset, so the default deployment
    is unaffected until the operator pairs the website. Publishes the sealing
    key first, so the form is live before anyone has a chance to submit.
    """
    if not is_configured():
        return 0
    publish_sealing_key()
    resp = _request("/api/bot/sync/credentials/pending")
    rows = (resp or {}).get("pending", []) if resp else []
    if not rows:
        return 0
    if store is None:
        from bot.core.exchange_credentials import get_credential_store
        store = get_credential_store()
    acks = process_pending(rows, store, validator=validator, on_change=on_change)
    if acks:
        _request("/api/bot/sync/credentials/ack", {"acks": acks})
    return len(acks)
