"""One vocabulary of secret shapes, and every scrub in the process reads it.

Four readers, four answers, before `bot/utils/secret_shapes.py`:
`logger._redact_string` knew `key=value` under seven words;
`exc_text._safe_exc_text` knew that plus the bot-token shape and dropped URL
queries; `outbound.reply_safe` knew the first two; `live_balance.scrub_reason`
and `trade_gate._safe_detail` each kept a private copy of the query pattern.
CLAUDE.md said of the outbound seam that `Authorization: Bearer …` still
passed and fixing it was its own slice. Driven, more than the header passed:
a bare provider key, a session JWT, and the three env names whose values
encrypt everything else on the box (`WEB3_SIGNER_PRIVATE_KEY=`,
`RUNECLAW_SECRETS_KEY=`, `WEB_CREDS_KEY=`) — `_KEY` is not `api_key`, and
`SECRETS_KEY=` is not `secret=`.

Three things are pinned here and nowhere else:

  1. THE TABLE PINS ITSELF. Every row carries the example it must scrub and
     the decoy it must leave, and the test reads both off the row, so a row
     cannot drift from its own test.
  2. WHAT IS DELIBERATELY KEPT. A transaction hash has a private key's
     shape; a card's `?symbol=` link is not a credential; `Key: <code>BG-…`
     on the /exchange card is a fingerprint under a capitalised label. A
     scrub that ate those would be the failed-read-as-empty defect with the
     sign flipped, on the card.
  3. THE WALK IS ONE. A byte-identical copy of the table agrees on every
     fixture and diverges on the first change, which is what a second copy
     looks like from outside. So a shape is PLANTED in the table and every
     reader — the logger, the outbound seam, the exception path, the venue
     reasons, the web middleware, the SSE frame, the Telegram stream — is
     read for it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re

import pytest

from bot.utils import secret_shapes as shapes
from bot.utils.secret_shapes import (
    REDACTED,
    SHAPES,
    TX_HASH_SHAPED_DECOY,
    Shape,
    drop_url_queries,
    looks_like_credential,
    scrub_diagnostic,
    scrub_secrets,
)

# ── 1. the table pins itself ─────────────────────────────────────────────────


@pytest.mark.parametrize("shape", SHAPES, ids=[s.name for s in SHAPES])
def test_each_row_scrubs_its_own_example_and_leaves_its_own_decoy(shape: Shape):
    assert shape.pattern.search(shape.example), f"{shape.name}: the example is not an instance"
    scrubbed = scrub_secrets(shape.example)
    assert scrubbed != shape.example, f"{shape.name}: the example survived the whole table"
    assert REDACTED in scrubbed, f"{shape.name}: redacted with no marker"
    # The decoy goes through the WHOLE table, not the row alone: a decoy one
    # row leaves and another eats is still eaten.
    assert scrub_secrets(shape.decoy) == shape.decoy, f"{shape.name}: the decoy was changed"


def test_the_rows_are_the_rows():
    """Adding or removing a shape is a deliberate act that edits this list in
    the same commit, with the reason in the module docstring."""
    assert [s.name for s in SHAPES] == [
        "telegram_bot_token", "bearer_token", "provider_key", "jwt",
        "named_key_value", "prose_key_label", "env_name_value", "secret_query_param",
    ]


def test_token_shapes_come_before_the_key_value_families():
    """A `Bearer sk-…` carries no `=` the key=value family could see; the
    order is the table's, and it is the order `_safe_exc_text` documented."""
    names = [s.name for s in SHAPES]
    assert names.index("bearer_token") < names.index("named_key_value")
    assert names.index("provider_key") < names.index("named_key_value")
    assert names[-1] == "secret_query_param"


# ── 2. the drive that justified the module ──────────────────────────────────

PASSED_BEFORE = {
    "bearer header": ("Authorization: Bearer sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcdefghijk",
                      "sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcdefghijk"),
    "bare provider key": ("provider rejected sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcdefghijk",
                          "sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcdefghijk"),
    "xai key": ("xai-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcdefghijk was refused",
                "xai-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcdefghijk"),
    "jwt": ("session eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0"
            ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"),
    "signer key env": ("WEB3_SIGNER_PRIVATE_KEY=4f3c2b1a4f3c2b1a4f3c2b1a4f3c2b1a4f3c2b1a4f3c2b1a4f3c2b1a4f3c2b1a",
                       "4f3c2b1a4f3c2b1a4f3c2b1a4f3c2b1a4f3c2b1a4f3c2b1a4f3c2b1a4f3c2b1a"),
    "master key env": ("RUNECLAW_SECRETS_KEY=Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4YQ==",
                       "Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4YQ=="),
    "creds key env": ("WEB_CREDS_KEY=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                      "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"),
    "api key with a space": ("api key: bg_1234567890abcdef1234567890abcdef",
                             "bg_1234567890abcdef1234567890abcdef"),
    "private key label": ("private key: 4f3c2b1a4f3c2b1a4f3c2b1a4f3c2b1a4f3c2b1a4f3c2b1a4f3c2b1a4f3c2b1a",
                          "4f3c2b1a4f3c2b1a4f3c2b1a4f3c2b1a4f3c2b1a4f3c2b1a4f3c2b1a4f3c2b1a"),
    "query key= that is not api_key": ("see https://maps.example.com/v1/x?key=AIzaSyA0123456789abcdefghijklmnopqrstuv&v=3",
                                       "AIzaSyA0123456789abcdefghijklmnopqrstuv"),
}


@pytest.mark.parametrize("name", list(PASSED_BEFORE), ids=list(PASSED_BEFORE))
def test_what_passed_the_outbound_scrub_before_is_scrubbed_now(name):
    from bot.utils.outbound import reply_safe
    text, secret = PASSED_BEFORE[name]
    out = reply_safe(text)
    assert secret not in out, (name, out)
    assert REDACTED in out, (name, out)


def test_the_env_name_survives_because_it_is_the_diagnostic():
    """Which key was printed is what an operator needs; its value never is."""
    out = scrub_secrets("RUNECLAW_SECRETS_KEY=Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4YQ==")
    assert out == "RUNECLAW_SECRETS_KEY=***REDACTED***"


def test_the_named_family_is_the_loggers_original_shape_unchanged():
    """`tests/test_security.py` pins `api_key=***REDACTED***` and a
    `password = "hunter2"` traceback line; the row is the old regex verbatim,
    so nothing that scrubbed before scrubs differently now."""
    assert scrub_secrets("auth failed: api_key=sk-abcdefghijklmnopqrs") == "auth failed: api_key=***REDACTED***"
    tb = 'File "config.py", line 10\n  password = "hunter2"\nValueError: password=hunter2 invalid'
    assert "hunter2" not in scrub_secrets(tb)


# ── 3. what is deliberately kept ────────────────────────────────────────────

KEPT = [
    TX_HASH_SHAPED_DECOY,
    "signer 0x4f3c2b1a4f3c2b1a4f3c2b1a4f3c2b1a4f3c2b1a4f3c2b1a4f3c2b1a4f3c2b1a",  # bare hex: a hash's shape
    "Key: <code>BG-1a2b…f9</code>",  # the /exchange fingerprint under a capitalised label
    "token: BTC · secret: n/a",  # a symbol under the word, too short to be a credential
    "api key: not configured · private key: missing · WEB_CREDS_KEY=required",  # status sentences
    "<b>BTC/USDT</b> entry <code>63,000.00</code> · 3 &amp; rising · closed 12:30:15 UTC",
    "https://www.humanoid-traders.com/api/insight?symbol=BTC%2FUSDT&v=12",
    "https://www.humanoid-traders.com/invite?code=RUNE1234&ref=arena",
    "https://dexscreener.com/solana/9d5f7b3a9d5f7b3a9d5f7b3a9d5f7b3a?chain=solana",
    "the bearer of responsibilities · eyJ is how every JWT header starts · sk-8 stickers",
]


@pytest.mark.parametrize("text", KEPT)
def test_what_a_card_is_right_to_print_is_returned_byte_identical(text):
    from bot.utils.outbound import reply_safe
    assert scrub_secrets(text) == text
    assert reply_safe(text) == text


def test_the_original_row_keeps_its_conservative_side():
    """`Token: PEPE` IS redacted, by the logger's original `key=value` row
    (four characters under the word `token`), and that is kept on purpose: the
    only way to spare it is to demand the value look like a credential, and a
    Bitget passphrase is a user-chosen word with no digit in it — narrowing
    the row would let `passphrase=mypassphrase` through to spare a symbol.
    Over-redacting a word on a card is the safe direction; no card prints one
    today (the one `Token:` label in the tree is an approval token id)."""
    assert scrub_secrets("Token: PEPE") == "Token=***REDACTED***"
    assert "mypassphrase" not in scrub_secrets("passphrase=mypassphrase")


def test_a_bare_hex_64_is_left_alone_because_it_is_also_a_transaction_hash():
    """The decision, not just the behaviour: a private key printed BARE is a
    producer's bug this chokepoint cannot fix, because the same 64 hex
    characters are the hash a swap card is right to print. Labelled, it is
    scrubbed."""
    key = "4f3c2b1a" * 8
    assert scrub_secrets(f"tx 0x{key} confirmed") == f"tx 0x{key} confirmed"
    assert key not in scrub_secrets(f"WEB3_SIGNER_PRIVATE_KEY=0x{key}")
    assert key not in scrub_secrets(f"private key: 0x{key}")


def test_a_cards_link_keeps_its_query_and_the_exception_path_drops_it():
    """Two rules, two functions, both driven. `?symbol=` is the bridge's own
    query form for a slashed ticker; a card that lost it would link nowhere.
    A diagnostic never carries a link a user needs, so there the whole query
    goes — including a parameter nobody thought to name."""
    link = "https://x.com/insight?symbol=BTC%2FUSDT&mystery=abc123def456"
    assert scrub_secrets(link) == link
    assert drop_url_queries(link) == "https://x.com/insight?***"
    assert scrub_diagnostic(f"GET {link}\n failed") == "GET https://x.com/insight?*** failed"


def test_a_secret_named_query_parameter_is_scrubbed_inside_a_kept_link():
    out = scrub_secrets("https://api.bitget.com/v2/mix?symbol=BTCUSDT&sign=abc123&timestamp=1")
    assert out == "https://api.bitget.com/v2/mix?symbol=BTCUSDT&sign=***REDACTED***&timestamp=1"


@pytest.mark.parametrize("value,expected", [
    ("required", False), ("missing", False), ("notconfigured", False),
    ("hunter2", True), ("bg_1234", True), ("Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6", True),
])
def test_looks_like_credential_separates_a_value_from_a_status_word(value, expected):
    assert looks_like_credential(value) is expected


# ── 4. the replacements cannot corrupt a body ───────────────────────────────


@pytest.mark.parametrize("shape", SHAPES, ids=[s.name for s in SHAPES])
def test_a_json_body_holding_the_example_is_still_json_after_the_scrub(shape: Shape):
    body = json.dumps({"reply_html": f"<b>x</b> {shape.example}", "n": [shape.example]})
    out = scrub_secrets(body)
    parsed = json.loads(out)
    assert set(parsed) == {"reply_html", "n"}
    assert parsed["reply_html"].startswith("<b>x</b>")
    assert not re.search(r'[\\"{}<>]', REDACTED)


def test_scrubbing_is_idempotent():
    for s in SHAPES:
        once = scrub_secrets(s.example)
        assert scrub_secrets(once) == once, s.name


# ── 5. the walk is one: plant a shape, read every reader ────────────────────

PLANT = "PLANTEDSECRET9f3a"
PLANTED_SHAPE = Shape("planted", re.compile(r"PLANTEDSECRET[0-9a-f]{4}"), "***PLANTED***",
                      PLANT, "planted seeds")


@pytest.fixture
def planted(monkeypatch):
    """A shape only the table knows. Every reader iterates `SHAPES` at call
    time, so patching the table is patching the vocabulary — a reader that
    kept its own copy would still answer the old vocabulary and show up here."""
    monkeypatch.setattr(shapes, "SHAPES", (PLANTED_SHAPE,))
    return PLANT


def test_the_logger_reads_the_table(planted):
    from bot.utils.logger import _JSONFormatter, _redact_dict, _redact_string
    assert _redact_string(f"boot {planted}") == "boot ***PLANTED***"
    assert _redact_dict({"data": {"note": planted}})["data"]["note"] == "***PLANTED***"
    rec = logging.LogRecord("system", logging.INFO, __file__, 1, f"x {planted} y", None, None)
    assert "***PLANTED***" in _JSONFormatter().format(rec)
    assert planted not in _JSONFormatter().format(rec)


def test_the_outbound_seam_reads_the_table(planted):
    from bot.utils.outbound import reply_safe
    assert reply_safe(f"<b>hi</b> {planted}") == "<b>hi</b> ***PLANTED***"


def test_the_exception_path_reads_the_table(planted):
    from bot.skills.quant_skill import _safe_reason
    from bot.utils.exc_text import _safe_exc_text
    assert _safe_exc_text(RuntimeError(f"venue said {planted}")) == "venue said ***PLANTED***"
    assert planted not in _safe_reason(RuntimeError(f"venue said {planted}"))


def test_the_venue_reasons_read_the_table(planted):
    from bot.core.exchange_credentials import _safe_venue_detail
    from bot.core.trade_gate import _safe_detail
    from bot.formatters.live_balance import scrub_reason
    assert scrub_reason(f"auth {planted}") == "auth ***PLANTED***"
    assert _safe_detail(f"auth {planted}") == "auth ***PLANTED***"
    out = _safe_venue_detail(RuntimeError(f"auth {planted}"))
    assert planted not in out and "***PLANTED***" in out


def test_the_web_middleware_and_the_sse_frame_read_the_table(planted):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from bot.web.user_gateway import _sse_frame, outbound_redaction_middleware

    async def handler(_req):
        return web.json_response({"reply_html": f"<b>hi</b> {planted}"})

    async def drive():
        app = web.Application(middlewares=[outbound_redaction_middleware])
        app.router.add_post("/x", handler)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/x")
            return await resp.text()

    body = asyncio.run(drive())
    assert planted not in body and "***PLANTED***" in body
    frame = _sse_frame("final", {"reply_html": planted}).decode()
    assert planted not in frame and "***PLANTED***" in frame


def test_the_telegram_stream_reads_the_table(planted):
    from bot.skills.chat_runtime import TelegramStream

    class _Msg:
        def __init__(self):
            self.edits = []

        async def edit_text(self, text, **kw):
            self.edits.append(text)

    msg = _Msg()
    assert asyncio.run(TelegramStream(msg).finish(f"<b>Done.</b> {planted}")) is True
    assert planted not in msg.edits[-1] and "***PLANTED***" in msg.edits[-1]


def test_the_real_send_reads_the_table(planted):
    """Driven on the REAL `_send`: the halt fixture every "what did the bot
    say" suite uses REPLACES it with a stub, so this is the only place the
    chokepoint's own body is read."""
    from types import SimpleNamespace as NS

    from bot.skills.telegram_handler import TelegramHandler
    sent = []

    class _Message:
        async def reply_text(self, text, **kw):
            sent.append(text)
            return NS(message_id=1)

    update = NS(callback_query=None, message=_Message(), effective_chat=NS(id=1), effective_user=NS(id=1))
    me = NS(_split_message=lambda text, limit: [text])
    asyncio.run(TelegramHandler._send(me, update, f"<b>hi</b> {planted}"))
    assert sent and planted not in sent[-1] and "***PLANTED***" in sent[-1]


# ── 6. the copies are gone ──────────────────────────────────────────────────


def test_no_module_outside_the_table_compiles_its_own_query_or_token_pattern():
    """`live_balance`, `trade_gate` and `exc_text` each carried the URL-query
    pattern; a fourth copy tomorrow would be the same defect. Source-scanned
    because reachability is proved above — this pins that no private copy is
    being READ beside the shared one."""
    import pathlib

    from tests.source_scan import code_only
    root = pathlib.Path(__file__).resolve().parents[1]
    offenders = []
    for path in (root / "bot").rglob("*.py"):
        if path.name == "secret_shapes.py":
            continue
        src = code_only(path.read_text(encoding="utf-8", errors="replace"))
        if re.search(r"re\.compile\(\s*r?\"\(https\?://\[\^\\s\?\]\+\)", src):
            offenders.append(str(path.relative_to(root)))
        if re.search(r"re\.compile\(\s*r?\"\(\?:bot\)\?\\d\{6,12\}", src):
            offenders.append(str(path.relative_to(root)))
    assert offenders == [], offenders
