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
    "looks_like_credential",
    "scrub_secrets",
    "drop_url_queries",
    "scrub_diagnostic",
]
