"""
Bot-side pull of website-queued exchange credentials (Stage 2b of web wallet).

The website protects a user's keys at rest (app/lib/creds_crypto.js) and the
bot opens them on pull, then imports into its Fernet store. These cover the
LEGACY shared-key envelope — ``v: 1``, AES-256-GCM under WEB_CREDS_KEY,
including a REAL Node-produced envelope proving cross-language compatibility —
and the per-row apply/ack logic, which is shared by both envelopes.

The default since 2026-09 is ``v: 2``, sealed to the bot's own published key
with nothing configured by hand; ``tests/test_creds_sealing.py`` covers that
side. The legacy path is kept, and kept tested, because a `pending_credentials`
row written before an upgrade is still in flight when the bot restarts — the
same reason the frozen fixture below exists at all.
"""

import base64
import json

import pytest

import bot.utils.credential_pull as cp


# A real envelope produced by app/lib/creds_crypto.js (Node) with this key —
# decrypting it in Python proves the cross-language format is compatible.
NODE_KEY = "i9WZDHFdLxEAJYcGMeI7zEJojI2Ir5747f6SOhDMMU4="
NODE_ENVELOPE = ('{"v":1,"iv":"oxqTBtAZY9ek+Ohb","tag":"JNXo7PhQCuyCD79JN9NjwA==",'
                 '"ct":"50DNzqYtzyUNd1gcjl6e2dhld9EvHoNMaaBrQflf2r6XI8zp1FSS+kl/'
                 'IhpsU9Fz8ig9wNgbuLHSl7L0u0DQjL84uTA="}')


class _FakeStore:
    def __init__(self):
        self.creds = {}
        self.sets = []
        self.deletes = []

    def set(self, tg, api_key, api_secret, passphrase):
        self.creds[str(tg)] = (api_key, api_secret, passphrase)
        self.sets.append(str(tg))

    def delete(self, tg):
        self.deletes.append(str(tg))
        return self.creds.pop(str(tg), None) is not None


def _encrypt_py(obj, key_b64):
    """Encrypt in the website's {v,iv,tag,ct} format using Python (for fixtures)."""
    import os
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = base64.b64decode(key_b64)
    iv = os.urandom(12)
    ct_tag = AESGCM(key).encrypt(iv, json.dumps(obj).encode(), None)
    ct, tag = ct_tag[:-16], ct_tag[-16:]
    return json.dumps({"v": 1, "iv": base64.b64encode(iv).decode(),
                       "tag": base64.b64encode(tag).decode(),
                       "ct": base64.b64encode(ct).decode()})


class TestDecrypt:
    def test_decrypts_real_node_envelope(self, monkeypatch):
        monkeypatch.setenv("WEB_CREDS_KEY", NODE_KEY)
        out = cp.decrypt_payload(NODE_ENVELOPE)
        assert out == {"api_key": "NODE_AK", "api_secret": "NODE_SEC",
                       "passphrase": "NODE_PP"}

    def test_roundtrip_python_encrypted(self, monkeypatch):
        monkeypatch.setenv("WEB_CREDS_KEY", NODE_KEY)
        env = _encrypt_py({"api_key": "A", "api_secret": "B", "passphrase": "C"}, NODE_KEY)
        assert cp.decrypt_payload(env)["api_secret"] == "B"

    def test_missing_key_raises(self, monkeypatch):
        monkeypatch.delenv("WEB_CREDS_KEY", raising=False)
        with pytest.raises(ValueError):
            cp.decrypt_payload(NODE_ENVELOPE)


class TestProcessPending:
    def _row(self, action="connect", uid=1, tg="999", payload=None):
        return {"user_id": uid, "telegram_id": tg, "action": action,
                "encrypted_payload": payload}

    def _payload(self):
        return _encrypt_py({"api_key": "AK", "api_secret": "SEC", "passphrase": "PP"}, NODE_KEY)

    def test_connect_imports_and_acks(self, monkeypatch):
        monkeypatch.setenv("WEB_CREDS_KEY", NODE_KEY)
        store = _FakeStore()
        changed = []
        acks = cp.process_pending([self._row(payload=self._payload())], store,
                                  on_change=changed.append)
        assert store.creds["999"] == ("AK", "SEC", "PP")
        assert acks == [{"user_id": 1, "action": "connect", "ok": True}]
        assert changed == ["999"]

    def test_disconnect_deletes_and_acks(self):
        store = _FakeStore()
        store.creds["999"] = ("x", "y", "z")
        acks = cp.process_pending([self._row(action="disconnect", payload=None)], store)
        assert store.deletes == ["999"]
        assert acks == [{"user_id": 1, "action": "disconnect", "ok": True}]

    def test_validator_false_rejects_without_import(self, monkeypatch):
        monkeypatch.setenv("WEB_CREDS_KEY", NODE_KEY)
        store = _FakeStore()
        acks = cp.process_pending([self._row(payload=self._payload())], store,
                                  validator=lambda c: False)
        assert store.sets == []
        assert acks[0]["ok"] is False

    def test_validator_none_skips_without_ack(self, monkeypatch):
        # Transient/unverifiable -> leave un-acked so it retries next poll.
        monkeypatch.setenv("WEB_CREDS_KEY", NODE_KEY)
        store = _FakeStore()
        acks = cp.process_pending([self._row(payload=self._payload())], store,
                                  validator=lambda c: None)
        assert store.sets == []
        assert acks == []

    def test_corrupt_payload_acked_failed(self, monkeypatch):
        # Undecryptable row must be acked (ok=False) so it isn't retried forever.
        monkeypatch.setenv("WEB_CREDS_KEY", NODE_KEY)
        store = _FakeStore()
        acks = cp.process_pending([self._row(payload='{"v":1,"iv":"AA","tag":"AA","ct":"AA"}')],
                                  store)
        assert store.sets == []
        assert acks[0]["ok"] is False

    def test_missing_telegram_id_skipped(self):
        store = _FakeStore()
        acks = cp.process_pending([self._row(tg="", payload=None)], store)
        assert acks == []


class TestConfigGate:
    """The gate is the SYNC CHANNEL, not a hand-set shared key.

    This asserted that an unset WEB_CREDS_KEY made the pull a no-op, which was
    true while that key was the only thing that could open a submission.
    Submissions are sealed to the bot's own key now (tests/test_creds_sealing.py)
    and there is nothing to configure, so the only question left is whether the
    website has been paired at all.

    Worth stating why it is asserted through `SYNC_SECRET` rather than the
    environment: the module reads `BOT_SYNC_SECRET` once at import, so a
    `monkeypatch.setenv` here would set a variable nothing reads again and the
    test would pass for a reason unrelated to the rule.
    """

    def test_unpaired_website_pull_is_noop(self, monkeypatch):
        monkeypatch.setattr(cp, "SYNC_SECRET", "")
        monkeypatch.setattr(cp, "_request",
                            lambda *a, **k: pytest.fail("no website is paired"))
        assert cp.is_configured() is False
        assert cp.pull_and_apply() == 0

    def test_an_unset_shared_key_no_longer_turns_the_pull_off(self, monkeypatch):
        """The shared key is legacy. A bot with a paired website pulls with it
        unset — that is the deployment the change exists to serve."""
        monkeypatch.delenv("WEB_CREDS_KEY", raising=False)
        monkeypatch.setattr(cp, "SYNC_SECRET", "s" * 48)
        assert cp.is_configured() is True
        calls = []
        monkeypatch.setattr(cp, "publish_sealing_key", lambda *a, **k: calls.append("pub"))
        monkeypatch.setattr(cp, "_request", lambda p, d=None: (calls.append(p), {})[1])
        cp.pull_and_apply()
        assert "pub" in calls, "the sealing key is published before anything can be submitted"
        assert any(str(c).endswith("/credentials/pending") for c in calls)
