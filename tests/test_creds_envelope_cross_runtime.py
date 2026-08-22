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
