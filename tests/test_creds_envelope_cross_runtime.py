"""
The website's encryptor and the bot's decryptor, run against each other.

`app/lib/creds_crypto.js` encrypts a user's exchange API keys at rest before
they touch MySQL; `bot/utils/credential_pull.py` decrypts them when the bot
pulls the row. The envelope between them — ``{v, iv, tag, ct}``, base64, with
the GCM tag split out of the ciphertext because Node's API hands it back
separately — is NOT a standard. It was invented here, it is described only in
two docstrings, and either side can be edited alone.

WHY THIS FILE EXISTS, given `test_credential_pull.py` already decodes a real
Node envelope. That fixture is FROZEN: a base64 string captured from Node once
and pasted in. It proves Python can read what the website emitted *at the time
it was captured*. It says nothing about what the website emits *now*, because
nothing in either suite runs `encryptJSON`.

That was verified rather than assumed, by changing the wire format on the JS
side — both halves of it, so Node stays self-consistent, which is the shape the
realistic mistake actually has. Emitting the three fields as hex instead of
base64, a change whose whole justification is "hex is easier to eyeball", left
**18/18 JS credential tests and 10/10 Python credential tests passing** and
broke decryption of every real credential. `process_pending` acks an
undecryptable row as FAILED so the website clears it: the submitted keys are
destroyed, not retried, and the user is told the connection failed.

The first mutation tried was folding the tag into the ciphertext —
``tag: '', ct: Buffer.concat([ct, tag])`` — on the reasoning that Python's
``AESGCM.decrypt`` wants ``ct || tag`` anyway. It is worth recording that this
one **still decrypts**, by accident: Python computes ``ct + tag`` and an empty
tag concatenates to nothing. So it is not a break — it is a silent change to a
format described in two docstrings, arriving with nothing able to report that
the description had stopped being true, and the next edit resting on it is the
one that breaks. Not every plausible mutation is a defect; this one is a
measurement that the format is unattended.

The two tests therefore answer two different questions, and neither implies
the other:

    frozen fixture   can the bot still read rows the OLD website wrote?
                     (in-flight `pending_credentials` rows across a deploy)
    this file        can the bot read rows the CURRENT website writes?

SINCE 2026-09 THERE ARE TWO ENVELOPES and the second one is the default, so
this file covers both. `v: 1` is the shared-key envelope below. `v: 2` is
SEALED to the bot's own key (`bot/utils/creds_sealing.py`): the bot publishes
the public half over the sync channel and the website wraps a fresh AES-256-GCM
content key with RSA-OAEP(SHA-256), so no shared secret is set by hand and the
website cannot reopen what it stored.

That one has a THIRD thing to agree on beyond the field names and the tag
split: the key fingerprint. Both sides take SHA-256 over the SPKI DER and keep
16 hex characters, and each envelope carries the result — so if the two
computations ever diverge, every submission is refused with "sealed to a key
this bot does not hold", which reads as a rotation nobody performed. Nothing
but a real cross-runtime run can catch that, because either side alone is
self-consistent by construction.

`app/lib/secrets_vault.js` is the same shape pointed the other way and needs no
equivalent, which is worth stating so this file is not copied there: Python
WRITES that one with `cryptography.Fernet` and Node reads it. Fernet is a
published spec, so the producer cannot drift, and the Node reader is already
pinned against a real Python-generated vault. Here the producer is two hundred
lines of our own code and it is the half nothing executes.

Skips where `node` is unavailable, matching `test_proofofpnl_publish.py`.
GitHub's ubuntu-latest images ship node, so this runs in CI.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

import bot.utils.credential_pull as cp
import bot.utils.creds_sealing as cs

REPO = Path(__file__).resolve().parents[1]
CREDS_JS = REPO / "app" / "lib" / "creds_crypto.js"

# Not a secret: a throwaway 32-byte key that exists only inside this file.
TEST_KEY = base64.b64encode(bytes(range(32))).decode()


def _node_encrypt(obj: dict, key_b64: str) -> str:
    """Encrypt with the REAL shipped encryptor, in a real node process.

    Deliberately `require`s the file on disk rather than restating what it
    does. A test that reimplements the thing it is checking agrees with itself
    and nothing else — which is the exact failure this file was written about.
    """
    if not shutil.which("node"):
        pytest.skip("node not available")
    assert CREDS_JS.exists(), f"the website's encryptor is gone from {CREDS_JS}"
    js = (
        f"const c = require({json.dumps(str(CREDS_JS))});"
        "let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{"
        "process.stdout.write(c.encryptJSON(JSON.parse(s)));});"
    )
    env = dict(os.environ, WEB_CREDS_KEY=key_b64)
    res = subprocess.run(
        ["node", "-e", js], input=json.dumps(obj), env=env,
        capture_output=True, text=True, timeout=60,
    )
    assert res.returncode == 0, f"node failed: {res.stderr.strip()[:400]}"
    out = res.stdout.strip()
    assert out, "the encryptor produced nothing — the harness is not exercising it"
    return out


class TestLiveRoundTrip:
    def test_bot_decrypts_what_the_website_encrypts_right_now(self, monkeypatch):
        monkeypatch.setenv("WEB_CREDS_KEY", TEST_KEY)
        creds = {"api_key": "AK-live", "api_secret": "SEC-live",
                 "passphrase": "PP-live", "venue": "bitget"}
        assert cp.decrypt_payload(_node_encrypt(creds, TEST_KEY)) == creds

    def test_round_trip_survives_non_ascii_and_quotes(self, monkeypatch):
        # Passphrases are user-chosen. JSON carries this fine on both sides;
        # asserting it means a future encoding change (utf8 -> latin1, an
        # escape pass) is caught here rather than by one user who cannot log in.
        monkeypatch.setenv("WEB_CREDS_KEY", TEST_KEY)
        creds = {"api_key": "AK", "api_secret": 'a"b\\c', "passphrase": "pä…ss 🔑"}
        assert cp.decrypt_payload(_node_encrypt(creds, TEST_KEY)) == creds

    def test_the_envelope_still_has_the_shape_both_sides_agree_on(self, monkeypatch):
        # The round-trip above would pass if BOTH sides changed together — and
        # they can, in one commit, because a JS-only edit that keeps Node
        # self-consistent is the realistic mistake. This pins the field names
        # the Python decryptor indexes by, so such an edit fails HERE too, with
        # a message that names the format rather than a decrypt error.
        monkeypatch.setenv("WEB_CREDS_KEY", TEST_KEY)
        env = json.loads(_node_encrypt({"api_key": "x"}, TEST_KEY))
        assert set(env) == {"v", "iv", "tag", "ct"}, (
            "the envelope's fields changed; bot/utils/credential_pull.py reads "
            "e['iv'], e['tag'] and e['ct'] by name and will raise KeyError on "
            "every pending row"
        )
        assert env["v"] == 1, "envelope version bumped without a decryptor that knows v"
        assert len(base64.b64decode(env["iv"])) == 12, "GCM nonce is 12 bytes on both sides"
        assert len(base64.b64decode(env["tag"])) == 16, (
            "the GCM tag is no longer a separate 16-byte field — Python passes "
            "ct + tag to AESGCM and would be authenticating the wrong bytes"
        )

    def test_a_wrong_key_does_not_decrypt(self, monkeypatch):
        """Proves the decrypt path is real and not short-circuiting.

        Without this, a `decrypt_payload` that returned its input on error
        would satisfy every assertion above.
        """
        env = _node_encrypt({"api_key": "x"}, TEST_KEY)
        other = base64.b64encode(bytes(range(1, 33))).decode()
        monkeypatch.setenv("WEB_CREDS_KEY", other)
        with pytest.raises(Exception):
            cp.decrypt_payload(env)


def _node_seal(obj: dict, pem: str) -> str:
    """Seal with the REAL shipped sealer, in a real node process.

    Same rule as `_node_encrypt`: `require` the file on disk. A mirror of
    `sealJSON` written here would agree with itself and prove nothing about
    what the website emits — which is the failure this whole file is about.
    """
    if not shutil.which("node"):
        pytest.skip("node not available")
    assert CREDS_JS.exists(), f"the website's sealer is gone from {CREDS_JS}"
    js = (
        f"const c = require({json.dumps(str(CREDS_JS))});"
        "let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{"
        "const i=JSON.parse(s);"
        "process.stdout.write(c.sealJSON(i.obj,{pem:i.pem,kid:i.kid}));});"
    )
    res = subprocess.run(
        ["node", "-e", js], input=json.dumps({"obj": obj, "pem": pem, "kid": cs.kid()}),
        capture_output=True, text=True, timeout=60,
    )
    assert res.returncode == 0, f"node failed: {res.stderr.strip()[:400]}"
    out = res.stdout.strip()
    assert out, "the sealer produced nothing — the harness is not exercising it"
    return out


@pytest.fixture()
def bot_key(tmp_path, monkeypatch):
    """A throwaway sealing key, so no test writes one into the real data/."""
    monkeypatch.setenv("RUNECLAW_STATE_DIR", str(tmp_path))
    cs._cache.clear()
    yield cs.public_key_pem()
    cs._cache.clear()


class TestSealedLiveRoundTrip:
    """`v: 2` — the website seals to the bot's published key."""

    def test_the_bot_opens_what_the_website_seals_right_now(self, bot_key, monkeypatch):
        monkeypatch.delenv("WEB_CREDS_KEY", raising=False)
        creds = {"api_key": "AK-sealed", "api_secret": "SEC-sealed",
                 "passphrase": "PP-sealed", "venue": "bitget"}
        # Through `decrypt_payload`, not `unseal` directly: the version
        # dispatch is part of what has to agree.
        assert cp.decrypt_payload(_node_seal(creds, bot_key)) == creds

    def test_round_trip_survives_non_ascii_and_quotes(self, bot_key):
        creds = {"api_key": "AK", "api_secret": 'a"b\\c', "passphrase": "pä…ss 🔑"}
        assert cs.unseal(_node_seal(creds, bot_key)) == creds

    def test_both_runtimes_compute_the_same_key_fingerprint(self, bot_key):
        """The pin that only a cross-runtime run can make.

        Python hashes its own SPKI DER; Node hashes the DER it derives from the
        PEM it was handed. A divergence — a hash swap, a different truncation,
        hashing the PEM text instead of the key — refuses every submission with
        a message about a key rotation that never happened.
        """
        env = json.loads(_node_seal({"api_key": "x"}, bot_key))
        assert env["kid"] == cs.kid()

    def test_the_sealed_envelope_has_the_shape_both_sides_agree_on(self, bot_key):
        env = json.loads(_node_seal({"api_key": "x"}, bot_key))
        assert set(env) == {"v", "alg", "kid", "ek", "iv", "tag", "ct"}, (
            "the sealed envelope's fields changed; bot/utils/creds_sealing.py "
            "reads e['ek'], e['iv'], e['tag'] and e['ct'] by name"
        )
        assert env["v"] == 2, "envelope version bumped without an opener that knows it"
        assert env["alg"] == cs.SEAL_ALG
        assert len(base64.b64decode(env["iv"])) == 12, "GCM nonce is 12 bytes on both sides"
        assert len(base64.b64decode(env["tag"])) == 16, (
            "the GCM tag is no longer a separate 16-byte field — Python passes "
            "ct + tag to AESGCM and would be authenticating the wrong bytes"
        )

    def test_the_website_holds_nothing_that_reopens_it(self, bot_key, monkeypatch):
        """The property the change was made for, asserted rather than assumed.

        The shared key is what the website used to hold; with one set, the v1
        reader must still refuse a v2 envelope — the versions are not two
        spellings of one format.
        """
        env = _node_seal({"api_key": "AK-secret"}, bot_key)
        assert "AK-secret" not in env
        monkeypatch.setenv("WEB_CREDS_KEY", TEST_KEY)
        with pytest.raises(Exception):
            cs.unseal(json.loads(_node_encrypt({"api_key": "x"}, TEST_KEY)))

    def test_a_different_bot_key_does_not_open_it(self, bot_key, tmp_path, monkeypatch):
        """Proves the open path is real and not short-circuiting — the same
        guard the shared-key half has, one door along."""
        env = _node_seal({"api_key": "x"}, bot_key)
        monkeypatch.setenv("RUNECLAW_STATE_DIR", str(tmp_path / "other"))
        cs._cache.clear()
        with pytest.raises(ValueError):
            cs.unseal(env)
