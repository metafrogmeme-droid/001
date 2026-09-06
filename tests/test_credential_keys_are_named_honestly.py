"""Three keys protect three stores, and the product must say which is which.

The operator's report was "WEB_CREDS_KEY is unset, so that Bybit key remains
in plaintext". The first half is a fact; the "so" was the product's fault.
`boot_health.py` described WEB_CREDS_KEY as "decrypting stored per-user
exchange keys", so the boot WARNING that names it read as "the keys users
linked are sitting unencrypted". They are not:

  * keys users link (/connect, the website)  → Fernet under the MASTER key,
                                               whatever else is set;
  * a website submission in transit to the bot → AES-GCM under WEB_CREDS_KEY,
                                               or refused (nothing is queued);
  * the operator's own venue keys            → .env, mirrored encrypted into
                                               the vault; the .env copy stays
                                               until it is set through
                                               /setexchange and deleted.

The third line is where a Bybit key really can remain in the clear, and it
has nothing to do with WEB_CREDS_KEY. So: the boot line now prints the effect
beside the name, /vault says what encrypts what, and /setexchange takes Bybit
and BingX so the .env copy can be retired (tests/test_setexchange_admin.py).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from bot.core import boot_health as bh

# ── the boot line ──────────────────────────────────────────────────────────

def test_every_important_variable_states_its_effect():
    for name in bh.IMPORTANT_ENV:
        assert name in bh.IMPORTANT_ENV_EFFECT and bh.IMPORTANT_ENV_EFFECT[name].strip(), name


def test_the_boot_line_prints_the_effect_beside_the_name():
    msg = bh.format_preflight(bh.env_preflight({"TELEGRAM_BOT_TOKEN": "t" * 40}))
    for name, effect in bh.IMPORTANT_ENV_EFFECT.items():
        assert f"{name} — {effect}" in msg, name


def test_the_creds_key_line_does_not_claim_linked_keys_are_exposed():
    effect = bh.IMPORTANT_ENV_EFFECT["WEB_CREDS_KEY"]
    assert "website" in effect and "connect form" in effect
    assert "stay encrypted" in effect and "master key" in effect
    assert "stored per-user" not in effect, "the 2026-09-06 misreading, back"


# ── /vault ─────────────────────────────────────────────────────────────────

def _vault_host(sent):
    from bot.skills.telegram_handler import TelegramHandler
    h = TelegramHandler.__new__(TelegramHandler)

    async def _send(update, text, *a, **k):
        sent.append(str(text))

    h._send = _send
    h._is_admin = lambda update: True
    return h


def _status(web_creds_env: bool):
    keys = ("BITGET_API_KEY", "BITGET_API_SECRET", "BITGET_PASSPHRASE",
            "TELEGRAM_BOT_TOKEN", "WEB_GATEWAY_SECRET", "BOT_SYNC_SECRET",
            "WEB_CREDS_KEY", "BYBIT_API_KEY", "BYBIT_API_SECRET")
    st = {k: {"env": True, "vault": True} for k in keys}
    st["WEB_CREDS_KEY"] = {"env": web_creds_env, "vault": False}
    st["BYBIT_API_KEY"] = {"env": True, "vault": False}     # .env only
    st["BYBIT_API_SECRET"] = {"env": True, "vault": False}
    return st


@pytest.mark.asyncio
@pytest.mark.parametrize("web_set", [False, True])
async def test_vault_says_what_encrypts_what(monkeypatch, web_set):
    import bot.core.secrets_vault as sv
    monkeypatch.setattr(sv, "vault_status", lambda: _status(web_set))
    sent = []
    h = _vault_host(sent)
    await h._cmd_vault(SimpleNamespace(effective_user=SimpleNamespace(id=1)),
                       SimpleNamespace(args=[]))
    card = sent[-1]
    assert "What encrypts what" in card
    assert "master key" in card and "RUNECLAW_SECRETS_KEY" in card
    assert "WEB_CREDS_KEY" in card
    if web_set:
        assert "is set" in card and "unset" not in card.split("What encrypts what")[1]
    else:
        assert "is unset" in card
        assert "refuses" in card, "an unset key must read as OFF, not as 'stored in the clear'"
        assert "already linked are unaffected" in card
    # The Bybit key that is only in .env is named as env-only, with its path out.
    assert "BYBIT_API_KEY" in card
    assert "/setexchange" in card
    assert "does not replace it" in card, (
        "the card must say the vault mirrors .env — that is where a key stays in the clear")


@pytest.mark.asyncio
async def test_vault_points_the_bybit_key_at_its_own_command(monkeypatch):
    """A Bybit key missing from BOTH env and vault used to be hidden, and a
    present one had no command to point at. It has one now."""
    import bot.core.secrets_vault as sv
    st = _status(True)
    st["BYBIT_API_KEY"] = {"env": True, "vault": False}
    monkeypatch.setattr(sv, "vault_status", lambda: st)
    sent = []
    h = _vault_host(sent)
    await h._cmd_vault(SimpleNamespace(effective_user=SimpleNamespace(id=1)),
                       SimpleNamespace(args=[]))
    assert "/setexchange bybit" in sent[-1]


# ── the website half: unset means refused, never plaintext ─────────────────

def test_the_website_refuses_rather_than_queues_without_the_key():
    """Pinned from the Node side by reading, because the claim in the boot
    line ("nothing is queued unencrypted") is only true while this holds."""
    from pathlib import Path
    route = Path("app/routes/credentials.js").read_text(encoding="utf-8")
    i = route.index("router.post('/'")
    body = route[i:route.index("router.delete('/'", i)]
    assert "if (!creds.isConfigured())" in body
    assert body.index("if (!creds.isConfigured())") < body.index("encryptJSON(")
    assert "503" in body[body.index("if (!creds.isConfigured())"):][:300]
