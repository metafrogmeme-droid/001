"""Website credential submissions are sealed to the bot — this is the bot's half.

The website used to encrypt a submitted exchange key with WEB_CREDS_KEY, a
32-byte secret the operator had to generate and then set identically in two
deployments' env. Until both were set the connect form answered 503, and the
operator's report on 2026-09-06 was, in effect, "the form does not save".
The requirement is that a user enters their keys on the website and they are
saved — with no ceremony in two .env files first.

So the bot owns a keypair instead. The private half lives beside the master
key (``data/.web_creds_private.pem``, 0600, generated on first use); the
public half is PUBLISHED to the website over the bot-secret sync channel on
the first credential pull after boot (and hourly after), and the website
seals each submission to it: a fresh AES-256-GCM content key per submission,
wrapped with RSA-OAEP(SHA-256). The website therefore stores something it
cannot read, the bot is the only party that can open it, and nothing has to
be configured by hand — the form is live within a minute of the bot
connecting.

Envelope (``v: 2``), base64 fields, written by ``app/lib/creds_crypto.js``:

    {v: 2, alg: "rsa-oaep-sha256+aes-256-gcm", kid, ek, iv, tag, ct}

``kid`` is the first 16 hex characters of SHA-256 over the SPKI DER of the
public key, computed identically on both sides, so a submission sealed to a
key this bot no longer holds fails with a message that says so rather than a
bare padding error. ``tag`` is split out of ``ct`` because Node's GCM API
hands it back separately — the same convention as the v1 envelope.

A key file that is lost (a wiped ``data/``) is regenerated on the next call
and republished; any row sealed to the old key is acked as failed so the
user resubmits. The shared-key ``v: 1`` path stays accepted for rows an
older website wrote. ``tests/test_creds_sealing.py`` drives this file, and
``tests/test_creds_envelope_cross_runtime.py`` runs the real Node sealer
against it.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Optional

from bot.utils.atomic_write import atomic_write_bytes

log = logging.getLogger(__name__)

class SealingKeyUnavailable(Exception):
    """This bot could not read (or create) its OWN sealing key.

    A distinct exception because the caller must act differently, and the
    difference decides whether somebody's submitted API keys survive.

    A row sealed to a key this bot no longer HOLDS — a regenerated key after a
    wiped ``data/`` — is unopenable forever, so the puller acks it as failed
    and the website clears it; the user resubmits against the republished key
    and it works. A row this bot cannot open because it could not READ its key
    file is a completely different fact: the row is perfectly openable a moment
    later. Acking it would clear a submission that was never unopenable, and
    tell the user their connection failed, so the puller leaves it queued
    instead. Same rule as a transient validator verdict, one layer down.
    """


#: The only algorithm the bot accepts; named in the envelope so a change on
#: either side is a loud mismatch rather than a silent one.
SEAL_ALG = "rsa-oaep-sha256+aes-256-gcm"
KEY_BITS = 3072
_KEY_BASENAME = ".web_creds_private.pem"

# One parsed key per path per process; a test that points RUNECLAW_STATE_DIR
# somewhere else gets a different path and therefore a fresh key.
_cache: dict[str, object] = {}


def _state_dir() -> str:
    return os.environ.get("RUNECLAW_STATE_DIR", "data")


def private_key_path() -> Path:
    return Path(_state_dir()) / _KEY_BASENAME


def _write_private(path: Path, pem: bytes) -> None:
    """0600 from the first byte, published by a rename.

    Through ``atomic_write_bytes`` rather than a hand-rolled temp-and-rename:
    the first draft here was a 28th independent copy of the idiom that helper
    exists to replace, and ``tests/test_atomic_write.py`` failed on it the
    same day. Its scratch name came from the destination, so two writers
    would have shared one file — and this one is a private key.

    ``mkstemp`` creates the scratch file 0600 and the mode is re-applied
    before the rename, so the key is never briefly world-readable either way.
    """
    atomic_write_bytes(path, pem, mode=0o600)


def _spki_der(public_key) -> bytes:
    from cryptography.hazmat.primitives import serialization
    der: bytes = public_key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return der


def load_or_create_private_key():
    """The bot's sealing key: read from the state dir, or generated once.

    The path and the fingerprint are logged when a key is generated; the key
    itself never is (the master-key loader records why: the default first
    boot's stderr is the container log).

    Every failure to read or create it becomes ``SealingKeyUnavailable``, so a
    caller can tell "I cannot get at my key" from "this row was sealed to a
    key I do not hold" — see that class for why the two must not be one.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    path = private_key_path()
    key = _cache.get(str(path))
    if key is not None:
        return key
    try:
        if path.exists():
            key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        else:
            key = rsa.generate_private_key(public_exponent=65537, key_size=KEY_BITS)
            _write_private(path, key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption()))
            log.info("Generated the website sealing key at %s (kid %s)",
                     path, kid(_spki_der(key.public_key())))
    except Exception as exc:
        raise SealingKeyUnavailable(f"{path}: {exc}") from exc
    _cache[str(path)] = key
    return key


def public_key_pem() -> str:
    """The half the website gets: SPKI PEM."""
    from cryptography.hazmat.primitives import serialization
    pem: bytes = load_or_create_private_key().public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    return pem.decode()


def kid(spki_der: Optional[bytes] = None) -> str:
    """Key fingerprint: SHA-256 over the SPKI DER, first 16 hex characters.

    ``app/lib/creds_crypto.js`` computes the same from the PEM it is handed,
    and a sealed envelope carries it, so a mismatch is named rather than
    surfacing as a decryption error.
    """
    der = spki_der if spki_der is not None else _spki_der(load_or_create_private_key().public_key())
    return hashlib.sha256(der).hexdigest()[:16]


def public_key_record() -> dict:
    """What the bot publishes: the public half, its fingerprint, the algorithm."""
    return {"kid": kid(), "pem": public_key_pem(), "alg": SEAL_ALG}


def unseal(envelope) -> dict:
    """Open a ``v: 2`` envelope sealed to this bot's key.

    Raises ``ValueError`` on a version, algorithm or key mismatch and on a
    malformed envelope, and the AEAD's own error on a tampered ciphertext —
    it never returns a guess. The caller (``credential_pull.process_pending``)
    acks any failure so the website clears the row and the user resubmits.
    """
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    e = json.loads(envelope) if isinstance(envelope, str) else envelope
    if not isinstance(e, dict) or int(e.get("v", 0) or 0) != 2:
        raise ValueError("not a sealed (v2) credential envelope")
    if e.get("alg") != SEAL_ALG:
        raise ValueError(f"unsupported sealing algorithm {e.get('alg')!r}")
    mine = kid()
    if str(e.get("kid") or "") != mine:
        raise ValueError(f"sealed to key {e.get('kid')!r}; this bot holds {mine!r} — the website "
                         "had a stale key; the current one is republished on the next pull")
    try:
        ek = base64.b64decode(e["ek"])
        iv = base64.b64decode(e["iv"])
        tag = base64.b64decode(e["tag"])
        ct = base64.b64decode(e["ct"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"malformed sealed envelope: {exc}") from None
    content_key = load_or_create_private_key().decrypt(
        ek, padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),
                         algorithm=hashes.SHA256(), label=None))
    if len(content_key) != 32:
        raise ValueError("wrapped content key is not 32 bytes")
    pt = AESGCM(content_key).decrypt(iv, ct + tag, None)
    creds = json.loads(pt.decode())
    # A sealed envelope whose plaintext is a string or a list opens perfectly
    # and then fails three frames away, inside `creds.get("venue")`, as an
    # AttributeError the puller logs as a "processing error". Say what it is
    # here, where the format is known.
    if not isinstance(creds, dict):
        raise ValueError(f"sealed payload is {type(creds).__name__}, not an object")
    return creds
