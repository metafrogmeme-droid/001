"""The one vocabulary of secret shapes every scrub in this process reads.

There were four readers and they did not agree. `logger._redact_string` knew
`key=value` under seven key words; `exc_text._safe_exc_text` knew that plus the
Telegram bot-token shape and dropped URL query strings; `outbound.reply_safe`
knew the first two; `live_balance.scrub_reason` and `trade_gate._safe_detail`
each carried a private copy of the query-string pattern beside a call to the
first. Every one of them was a copy that knew less than some other one, and
CLAUDE.md said so about the outbound seam in as many words ("`Authorization:
Bearer …` still passes, and fixing that is its own slice").

Driven before this module was written, the outbound scrub passed:

    Authorization: Bearer sk-ant-api03-…          (no `=`, no key word)
    provider rejected sk-proj-…                    (a bare provider key)
    session eyJhbGciOi….eyJzdWIiOi….SflKxwRJ…      (a JWT)
    WEB3_SIGNER_PRIVATE_KEY=4f3c2b1a…              (`_KEY` is not `api_key`)
    RUNECLAW_SECRETS_KEY=Zm9vYmFy…                 (`SECRETS_KEY=` is not `secret=`)
    WEB_CREDS_KEY=0123456789abcdef…
    api key: bg_1234…                              (a space, not an underscore)

The last three are the names of the keys that encrypt every other secret on the
box, and a config error is exactly the kind of message that prints one with its
value.

ONE TABLE, EACH ROW PINNING ITSELF. Every `Shape` carries an `example` it must
scrub and a `decoy` it must leave alone, and `tests/test_one_secret_vocabulary.py`
drives every row from the table itself, so a row cannot drift from its own test.
The readers are driven too: plant a shape only this table knows, read what each
reader answers, then patch `scrub_secrets` and prove each reader answers what the
patch said — a byte-identical copy agrees on every fixture and diverges on the
first change, which is what a second copy looks like from outside.

WHAT IT DELIBERATELY DOES NOT DO, stated because a scrub whose coverage is
overstated is the failure CLAUDE.md exists to prevent:

* A bare 64-hex value is left alone. A transaction hash has exactly that shape,
  a card that prints one is right to, and no chokepoint can tell the two apart.
  A private key is scrubbed when it is LABELLED (`PRIVATE_KEY=`, `private key:`);
  a producer that prints one bare has a bug this module cannot fix.
* A card's link keeps its query string except for parameters NAMED like
  credentials: `?symbol=BTC%2FUSDT` and `?ref=…` survive, `?sign=…` does not.
  `drop_url_queries` is the exception path's stricter rule — a diagnostic never
  carries a link a user needs — and it is a separate function so a card never
  loses a link to it.
* Keys with no fixed prefix (a 32-character Mistral key, a 64-hex Together key)
  are scrubbed only when labelled, for the same reason as the hash.
* Python only. `app/lib/safe_error.js` has its own vocabulary — wider on labels,
  narrower on token shapes — and a second file read by two runtimes is filed,
  not done.

THE DISCORD WEBHOOK ROW (22 October 2026). The website's anchor watch posts
its alerts to a Discord webhook (`app/lib/discord_alerts.js`), and a webhook
URL is a credential with no key word in it: its last path segment is the token,
and whoever holds the URL can post as the hook. A fetch error is exactly the
kind of message that prints the URL it was fetching, and no other row sees it
(no `=`, no `Bearer`, no prefix). The row keeps the host and the hook's id, so a
failure still says which hook it was, and takes the token.

THE TELEGRAM CHAT-ID ROWS (27 October 2026). A chat id is not a credential, but
it is an identifier of a person (the anonymous Daily Duel bot stores a salted hash
instead of one for exactly that reason), and nothing scrubbed it: a send that
failed logged `chat_id=123456789`, an unhandled handler error logged the update's
repr (`Chat(first_name=…, id=123456789, …)`, `'chat': {'id': 123456789, …}`), and
a forwarded-to-a-channel failure printed the supergroup id (`-100` and ten digits).
Six rows cover the shapes a log line actually carries (the sixth, a bare id after
the word `chat` or `group`, was added the same day); each keeps its label so a
failure still says WHICH field it was, and each needs five or more digits so a
status word, a small test id and a negative percentage are left alone. The
structured side, where the id is an int under a `chat_id` key and no string row
sees it, is `CHAT_ID_KEY_RE`, read by `logger._redact_dict`.

Order matters and is the order of `SHAPES`: token-shaped patterns first, because
they carry no `key=value` the next family could see; the `key=value` families
next; query parameters last.

THE EXAMPLES SCAN AS SECRETS, which is the point of them, and CI's secret scan
(gitleaks) read three of them as leaks on the commit that added this file. The
values are obvious fixtures — the alphabet, a counting string, base64 of
"foobarbazqux" — and they are listed by VALUE in `.gitleaks.toml`'s stopwords,
not by path, so this file stays scanned and a real key pasted into it is still
caught. A new example with a realistic value goes there too, or the scan says
so. Every replacement carries no quote, backslash,
brace or angle bracket, so a JSON body or an HTML card that was valid still is.
"""
from __future__ import annotations

import re
from typing import Callable, Match, NamedTuple, Union

REDACTED = "***REDACTED***"

Replacement = Union[str, Callable[[Match[str]], str]]


class Shape(NamedTuple):
    name: str
    pattern: re.Pattern[str]
    replacement: Replacement
    example: str  # a string this shape MUST scrub — its own pin
    decoy: str  # a string this shape must LEAVE ALONE — its own pin


def looks_like_credential(value: str) -> bool:
    """A value worth redacting behind a label, as opposed to a status word.

    `WEB_CREDS_KEY=required` and `api key: not configured` are sentences about a
    key, not a key; a key carries a digit or is long. Eight letters with no digit
    is a word.
    """
    return any(ch.isdigit() for ch in value) or len(value) >= 20


def _keep_words(group_label: int, group_value: int, sep: str = "=") -> Callable[[Match[str]], str]:
    """A replacement that redacts the value only when it looks like one."""
    def _sub(m: Match[str]) -> str:
        value = m.group(group_value)
        if not looks_like_credential(value):
            return m.group(0)
        return f"{m.group(group_label)}{sep}{REDACTED}"
    _sub.__name__ = f"keep_words_{group_label}_{group_value}"
    # The rule as DATA, so a second runtime can be handed this row rather than a
    # second author's reading of it. `scripts/render_secret_shapes.py` renders it;
    # a closure cannot be inspected from outside, and a renderer that guessed the
    # separator from the function name would be the second copy all over again.
    _sub.spec = {  # type: ignore[attr-defined]
        "kind": "keep_words", "label": group_label, "value": group_value, "sep": sep,
    }
    return _sub


#: A Telegram bot token: <digits>:<base64ish>, as PTB puts it into a request URL
#: (`/bot123456:AA…`). No leading \b — there is no word boundary between `bot`
#: and the digits, and a \b-anchored version once matched nothing.
TELEGRAM_BOT_TOKEN_RE = re.compile(r"(?:bot)?\d{6,12}:[A-Za-z0-9_-]{20,}")

#: `Bearer <token>` — the Authorization header an LLM provider's error echoes.
#: Capitalised only: the header form always is, and the English word is not.
BEARER_RE = re.compile(r"\b(Bearer)\s+([A-Za-z0-9._~+/=-]{16,})")

#: Provider keys with a fixed prefix: OpenAI / Anthropic / DeepSeek / Alibaba /
#: OpenRouter (`sk-`), xAI (`xai-`), Groq (`gsk_`), Perplexity (`pplx-`),
#: Google (`AIza`). Keys with no prefix are scrubbed only when labelled.
PROVIDER_KEY_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{20,}|xai-[A-Za-z0-9_-]{20,}|gsk_[A-Za-z0-9_-]{20,}"
    r"|pplx-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{30,})"
)

#: A JSON Web Token: three base64url segments, the first always `eyJ`.
JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")

#: A Discord webhook URL (`https://discord.com/api/webhooks/<id>/<token>`): the
#: last segment is the credential, so whoever holds the URL can post as the
#: hook. The host and the id survive, to say which hook a failure was; the
#: token does not. The anchor watch posts to one (app/lib/discord_alerts.js).
DISCORD_WEBHOOK_RE = re.compile(
    r"(https?://(?:(?:canary|ptb)\.)?discord(?:app)?\.com/api(?:/v\d+)?/webhooks/\d+/)[A-Za-z0-9_-]{20,}"
)

#: `chat_id=123456789`, `chat id: -1001234567890`, `telegram_id=…`, `tg-id: …`: the
#: label and a signed integer of five to fifteen digits. A label with no digits
#: after it (`chat_id=required`) and a short number (`chat id: 42`) are left alone.
CHAT_ID_LABEL_RE = re.compile(
    r"\b((?:chat|telegram|tg)[_\s-]?id)\s*[=:]\s*['\"]?-?\d{5,15}\b",
    re.IGNORECASE,
)

#: A bare supergroup / channel id: `-100` and nine to twelve more digits. The
#: `\B` means "not glued to a word", so `BTC-1001234567890` and `1-1001234567890`
#: are left alone and `-100.5%` is not long enough to be one.
TELEGRAM_SUPERGROUP_ID_RE = re.compile(r"\B-100\d{9,12}\b")

#: A private channel's message link, `https://t.me/c/<channel id>/<message>`. The
#: channel id is the credential-adjacent part; a public `t.me/<name>` link is not.
TELEGRAM_PRIVATE_LINK_RE = re.compile(r"(https?://t\.me/c/)\d{6,}")

#: python-telegram-bot's `repr(Chat)`, which is what an unhandled handler error
#: prints: `Chat(first_name='A', id=123456789, type=<ChatType.PRIVATE>)`.
TELEGRAM_CHAT_REPR_RE = re.compile(r"(\bChat\([^)\n]{0,200}?\bid=)-?\d{5,15}")

#: The same chat as a dict, in a printed `Update` or a JSON body:
#: `'chat': {'id': 123456789, 'type': 'private'}`.
TELEGRAM_CHAT_DICT_RE = re.compile(
    r"(\bchat['\"]?\s*:\s*\{\s*['\"]id['\"]\s*:\s*)-?\d{5,15}",
    re.IGNORECASE,
)

#: A chat id written as a bare number after the word for what it is:
#: `Proactive alerts enabled for chat 123456789`, `group -987654321`,
#: `channel id 1234567`. No label (`chat_id=`), no `Chat(` repr and no `-100`
#: prefix, so none of the rows above sees it; a private-chat id and a basic
#: group's negative id both read this way in a message line. Five or more
#: digits, so `chat 42` and a count of chats are left alone, and the word has
#: to stand directly in front of the number.
TELEGRAM_CHAT_WORD_RE = re.compile(
    r"\b((?:chat|group|channel)(?:\s+id)?\s+)-?\d{5,15}\b",
    re.IGNORECASE,
)

#: A structured KEY that names a chat id (`chat_id`, `admin_chat_id`, `telegramId`,
#: `chat_ids`). `logger._redact_dict` reads it: an id that is an int under such a
#: key has no string for the rows above to match.
CHAT_ID_KEY_RE = re.compile(r"(?:chat|telegram|tg)[_-]?ids?(?![a-z])", re.IGNORECASE)

#: `key=value` / `key: value` under a credential word. The logger's original
#: shape, unchanged: seven words, a value of four or more characters, the
#: separator normalised to `=` on output (tests pin `api_key=***REDACTED***`).
INLINE_SECRET_RE = re.compile(
    r"(api[_-]?key|api[_-]?secret|passphrase|password|token|secret|credential)"
    r"\s*[=:]\s*['\"]?([^\s'\"]{4,})",
    re.IGNORECASE,
)

#: The same family spelled as prose — `api key: …`, `private key: …` — which the
#: underscore shape never saw. Prose labels also introduce status sentences
#: ("api key: not configured"), so the value must look like a credential.
PROSE_KEY_LABEL_RE = re.compile(
    r"\b((?:api|private|secret|signing|master)\s+key)\s*[=:]\s*['\"]?([A-Za-z0-9+/=_.:-]{8,})",
    re.IGNORECASE,
)

#: An environment-variable name that ends in a credential word, with its value:
#: `WEB3_SIGNER_PRIVATE_KEY=…`, `RUNECLAW_SECRETS_KEY=…`, `WEB_CREDS_KEY=…`,
#: `WEB_GATEWAY_SECRET=…`. Upper case only, so `Key: <code>BG-1a2b…</code>` on
#: the /exchange card — a fingerprint under a capitalised label — is not a hit.
ENV_NAME_VALUE_RE = re.compile(
    r"\b([A-Z][A-Z0-9_]*(?:KEY|SECRET|SECRETS|TOKEN|PASSWORD|PASSPHRASE|CREDS?|CREDENTIALS?)"
    r"(?:_[A-Z0-9_]+)?)\s*[=:]\s*['\"]?([^\s'\"<>]{8,})"
)

#: A query parameter NAMED like a credential, inside a URL or on its own.
#: `?symbol=`, `?ref=`, `?code=`, `?chain=` and a cache-busting `?v=` survive.
SECRET_QUERY_PARAM_RE = re.compile(
    r"([?&](?:api[_-]?key|apikey|key|secret|token|access_token|auth|sig|sign|signature"
    r"|passphrase|password|private_key)=)[^&\s\"'<>]+",
    re.IGNORECASE,
)

#: A URL with a query string, whole. The exception path's rule and NOT a card's:
#: the host says which service failed and the query never has to.
URL_QUERY_RE = re.compile(r"(https?://[^\s?]+)\?[^\s]*")

SHAPES: tuple[Shape, ...] = (
    Shape("telegram_bot_token", TELEGRAM_BOT_TOKEN_RE, REDACTED,
          "https://api.telegram.org/bot123456789:AAHfjdkslfjdkslfjdkslfjdkslfjdksl/sendMessage",
          "closed 2026-09-14 12:30:15 UTC at 63,000.00"),
    Shape("bearer_token", BEARER_RE, _keep_words(1, 2, " "),
          "Authorization: Bearer sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789",
          "the bearer of responsibilities"),
    Shape("provider_key", PROVIDER_KEY_RE, REDACTED,
          "provider rejected sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcdefghijk",
          "sk-8 is a sticker, gsk_ alone is not a key, AIzaShort neither"),
    Shape("jwt", JWT_RE, REDACTED,
          "session eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0"
          ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
          "eyJ is how every JWT header starts"),
    Shape("discord_webhook", DISCORD_WEBHOOK_RE, r"\1" + REDACTED,
          "post failed: https://discord.com/api/webhooks/123456789012345678/tokentokentokentokentokentokentokentoken",
          "join us at https://discord.gg/runeclaw or https://discord.com/channels/1/2"),
    Shape("telegram_chat_id_label", CHAT_ID_LABEL_RE, r"\1=" + REDACTED,
          "send failed: chat_id=123456789 retrying in 5s",
          "chat_id=required, chat id: 42 and telegram_id not set"),
    Shape("telegram_supergroup_id", TELEGRAM_SUPERGROUP_ID_RE, REDACTED,
          "forward to -1001234567890 failed: bot was kicked",
          "drawdown -100.5% on BTC-1001234567890 and 1-1001234567890"),
    Shape("telegram_private_link", TELEGRAM_PRIVATE_LINK_RE, r"\1" + REDACTED,
          "see https://t.me/c/1234567890/55 for the thread",
          "join https://t.me/runeclaw or https://t.me/c/ and https://t.me/c/12"),
    Shape("telegram_chat_repr", TELEGRAM_CHAT_REPR_RE, r"\1" + REDACTED,
          "handler error on Chat(first_name='A', id=123456789, type=<ChatType.PRIVATE>)",
          "Chat(id=42) and Chat(type='private') and Chat(first_name='A', id=required)"),
    Shape("telegram_chat_dict", TELEGRAM_CHAT_DICT_RE, r"\1" + REDACTED,
          "update {'message': {'chat': {'id': 123456789, 'type': 'private'}}}",
          "chat: {'id': 42} and a chat room {'id': 123456789} with no chat label"),
    Shape("telegram_chat_word", TELEGRAM_CHAT_WORD_RE, r"\1" + REDACTED,
          "Proactive alerts enabled for chat 123456789 and group -987654321",
          "chat 42, 3 chats 12345678, a chat room 123456789 and group of 12345 users"),
    Shape("named_key_value", INLINE_SECRET_RE, r"\1=" + REDACTED,
          "auth failed: api_key=sk-abcdefghijklmnopqrs",
          "token: BTC"),
    Shape("prose_key_label", PROSE_KEY_LABEL_RE, _keep_words(1, 2, "="),
          "api key: bg_1234567890abcdef1234567890abcdef",
          "api key: not configured"),
    Shape("env_name_value", ENV_NAME_VALUE_RE, _keep_words(1, 2, "="),
          "RUNECLAW_SECRETS_KEY=Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4YQ==",
          "Key: <code>BG-1a2b…f9</code> · WEB_CREDS_KEY=required"),
    Shape("secret_query_param", SECRET_QUERY_PARAM_RE, r"\1" + REDACTED,
          "GET https://api.bitget.com/api/v2/mix?apiKey=bg_1234567890abcdef&sign=ZZZ failed",
          "https://www.humanoid-traders.com/api/insight?symbol=BTC%2FUSDT&v=12"),
)

#: A bare 64-hex value is a transaction hash as often as a key. Kept, on purpose
#: (see the module docstring); the pin lives with the decoys in the test.
TX_HASH_SHAPED_DECOY = (
    "tx 0x9d5f7b3a9d5f7b3a9d5f7b3a9d5f7b3a9d5f7b3a9d5f7b3a9d5f7b3a9d5f7b3a confirmed"
)


def replacement_spec(replacement: Replacement) -> dict:
    """``replacement`` as data: what a second runtime has to do with a match.

    Three kinds, and the table holds all three: ``redact`` (the whole match
    goes), ``template`` (a backreference string, ``\\1`` spelled the way
    Python spells it), and ``keep_words`` (the conditional one, whose rule is
    `looks_like_credential` — the row keeps its label and redacts its value
    only when the value looks like a credential).
    """
    spec = getattr(replacement, "spec", None)
    if spec is not None:
        return dict(spec)
    if replacement == REDACTED:
        return {"kind": "redact"}
    if isinstance(replacement, str):
        return {"kind": "template", "template": replacement}
    raise TypeError(f"replacement with no spec: {replacement!r}")


def table_rows() -> list[dict]:
    """`SHAPES` as data, in table order — the one description of this table.

    `app/lib/safe_error.js` is a SECOND RUNTIME reading these shapes, and the
    module docstring recorded that as "filed, not done" while its own vocabulary
    knew less: driven, it published a Telegram bot token, a bare provider key, a
    JWT, `RUNECLAW_SECRETS_KEY=`, `WEB3_SIGNER_PRIVATE_KEY=`, `WEB_CREDS_KEY=`
    and `api key: …`, and its `Authorization: Bearer <token>` rule redacted the
    word *Bearer* and printed the token after it. A reader saw a redaction
    marker and concluded the line was scrubbed.

    So the rows travel rather than being re-read by a second author:
    `scripts/render_secret_shapes.py` renders this into
    `app/lib/secret_shapes.generated.json`, the committed artifact the website
    compiles, and `tests/test_the_website_reads_this_vocabulary.py` regenerates
    it and compares byte for byte, so the artifact cannot go stale — the rule
    the README command tables already follow. Each row carries its own example
    and decoy, so the JS side drives the rows from the table exactly as
    `tests/test_one_secret_vocabulary.py` drives them here.
    """
    return [
        {
            "name": s.name,
            "pattern": s.pattern.pattern,
            "ignorecase": bool(s.pattern.flags & re.IGNORECASE),
            "replacement": replacement_spec(s.replacement),
            "example": s.example,
            "decoy": s.decoy,
        }
        for s in SHAPES
    ]


def scrub_secrets(text: str) -> str:
    """``text`` with every shape in `SHAPES` redacted, in table order.

    Pure and total over strings: it neither escapes nor truncates, so a card
    full of `<b>` renders as before and a JSON body stays valid. Callers keep
    their own fault policy (`reply_safe` returns the text unchanged on a fault
    because a scrub that breaks a chat is worse than the defence it buys).
    """
    out = text
    for shape in SHAPES:
        out = shape.pattern.sub(shape.replacement, out)
    return out


def drop_url_queries(text: str) -> str:
    """Every URL's query string replaced by `?***`, host and path kept.

    The exception path's rule, not a card's — see the module docstring.
    """
    return URL_QUERY_RE.sub(r"\1?***", text)


def scrub_diagnostic(text: str) -> str:
    """A driver's message for an operator's eyes: secrets scrubbed, every URL
    query dropped, whitespace collapsed. Not escaped and not truncated — the
    callers do both to their own surface's rule."""
    return " ".join(drop_url_queries(scrub_secrets(text)).split())


__all__ = [
    "REDACTED",
    "replacement_spec",
    "table_rows",
    "Shape",
    "SHAPES",
    "TX_HASH_SHAPED_DECOY",
    "TELEGRAM_BOT_TOKEN_RE",
    "URL_QUERY_RE",
    "INLINE_SECRET_RE",
    "CHAT_ID_KEY_RE",
    "CHAT_ID_LABEL_RE",
    "TELEGRAM_SUPERGROUP_ID_RE",
    "TELEGRAM_PRIVATE_LINK_RE",
    "TELEGRAM_CHAT_REPR_RE",
    "TELEGRAM_CHAT_DICT_RE",
    "looks_like_credential",
    "scrub_secrets",
    "drop_url_queries",
    "scrub_diagnostic",
]
