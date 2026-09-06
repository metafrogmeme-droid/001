"""html.escape stops markup injection and does nothing about a credential.

Ten command handlers sent an exception straight to the user:

    await self._send(update,
        f"🔴 <b>Scan error:</b> <code>{html.escape(str(exc)[:200])}</code>")

Escaping is the wrong control for the wrong threat. F-15 is the hard line —
no secret or internal config ever reaches user-facing text — and every one of
those messages could carry one:

  * a ccxt error quotes the request URL, query string included;
  * an LLM provider error can echo the Authorization header;
  * a KeyError on config names the key it could not find;
  * a python-telegram-bot NetworkError carries the BOT TOKEN in its URL.

`_operator_exc_detail` already existed for the last of those, and was wired
into two places out of twelve. It is an ALLOWLIST: unknown class, say
nothing. Correct where the token rides in the URL and wrong here, because a
ccxt error is never on that list, so the operator would have been told
nothing at all about why their scan failed.

Different question, different tool. `_safe_exc_text` SHOWS the message and
scrubs it first:

    bot-token shape  ->  the shared key=value redactor  ->  URL query
                     ->  html.escape

in that order, because escaping first would break the very patterns the
redactors match.
"""
from __future__ import annotations

from bot.skills.telegram_handler import _safe_exc_text


class _Err(Exception):
    pass


class TestSecretsAreScrubbed:
    def test_a_key_value_credential_is_redacted(self):
        out = _safe_exc_text(_Err("bitget rejected: api_key=AKIA12345678 bad"))
        assert "AKIA12345678" not in out
        assert "REDACTED" in out

    def test_every_credential_keyword_is_covered(self):
        for key in ("api_key", "api-key", "apiSecret", "passphrase",
                    "password", "token", "secret", "credential"):
            out = _safe_exc_text(_Err(f"{key}=SUPERSECRETVALUE99 rejected"))
            assert "SUPERSECRETVALUE99" not in out, key

    def test_a_bot_token_in_a_url_is_redacted(self):
        # The shape the shared key=value redactor cannot see.
        out = _safe_exc_text(
            _Err("bot7654321:AAH0abcdefghijklmnopqrstuvwxyz123456 invalid"))
        assert "AAH0abcdefghijklmnopqrstuvwxyz123456" not in out
        assert "REDACTED" in out

    def test_a_url_query_string_is_dropped(self):
        # Credentials ride in query strings as often as in key=value pairs,
        # and once they are one token no key=value regex sees them.
        out = _safe_exc_text(
            _Err("GET https://api.bitget.com/v2/mix?apiKey=abc&sig=xyz failed"))
        assert "abc" not in out and "sig" not in out
        assert "api.bitget.com" in out, (
            "the host is the diagnostic — which venue failed — and carries "
            "no secret; dropping it would trade a leak for a mystery"
        )


class TestItIsStillUseful:
    def test_an_ordinary_message_survives(self):
        # The point is to keep telling the operator what broke.
        out = _safe_exc_text(_Err("Insufficient margin for BTC/USDT"))
        assert "Insufficient margin" in out and "BTC/USDT" in out

    def test_it_is_not_the_allowlist(self):
        # _operator_exc_detail returns "" for any class off its list, which
        # for these handlers is every class. This one must speak.
        assert _safe_exc_text(ValueError("bad tick size")) != ""
        assert _safe_exc_text(_Err("venue timeout")) != ""


class TestItIsStillSafeMarkup:
    def test_html_is_escaped(self):
        out = _safe_exc_text(_Err("<b>x</b> & 'y'"))
        assert "<b>" not in out
        assert "&lt;b&gt;" in out

    def test_escaping_happens_after_redaction(self):
        # Escape first and "api_key=..." becomes text the redactor no longer
        # matches on some inputs. Order is load-bearing, so assert the
        # composition rather than the steps.
        out = _safe_exc_text(_Err("<i>api_key=LEAKED123456</i>"))
        assert "LEAKED123456" not in out
        assert "&lt;i&gt;" in out


class TestItNeverBecomesTheFailure:
    def test_a_length_cap_is_applied(self):
        assert len(_safe_exc_text(_Err("x" * 5000))) <= 200
        assert len(_safe_exc_text(_Err("x" * 5000), limit=50)) <= 50

    def test_newlines_are_flattened(self):
        assert "\n" not in _safe_exc_text(_Err("line one\nline two"))

    def test_an_unprintable_exception_yields_empty(self):
        class Nasty(Exception):
            def __str__(self):
                raise RuntimeError("cannot render")
        assert _safe_exc_text(Nasty()) == ""

    def test_an_empty_message_yields_empty(self):
        assert _safe_exc_text(_Err("")) == ""


class TestNoHandlerBypassesIt:
    """The half-fixed surface, on a hard line, is the defect this closes."""

    def _src(self):
        # Every file the handler class is made of: the scrubber's call sites
        # are spread across the mixins since the handler split, and a count
        # over telegram_handler.py alone read the move as eight of them
        # dropping the helper.
        from tests.source_scan import code_only, handler_sources
        return "\n".join(code_only(p.read_text(encoding="utf-8")) for p in handler_sources())

    def test_no_user_reply_renders_a_raw_exception(self):
        r"""Parsed, not grepped — the regex version had a blind spot.

        It was `_(?:send|reply)\([^\n]*?...str\(exc`, and `[^\n]*?` CANNOT
        CROSS A NEWLINE. Every call written the way black formats a long one —

            await self._send(update,
                             f"🔴 Could not read closed trades ({str(exc)})")

        — put `str(exc)` on the next line and was invisible to it. Four sites
        were leaking behind a green guard, including the credential preflight,
        whose error text is built FROM the request that carried the key:

            bitget GET https://api.bitget.com/...?apiKey=bg_LIVE...&passphrase=...

        A guard that reports clean because of where it looked is worse than no
        guard: it is the reason nobody looked again. The AST has no such blind
        spot — it sees the call, not its formatting.
        """
        import ast
        tree = ast.parse(self._src())
        bad: list[str] = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"_send", "_reply"}):
                continue
            for arg in list(node.args) + [kw.value for kw in node.keywords]:
                for sub in ast.walk(arg):
                    if (isinstance(sub, ast.Call)
                            and isinstance(sub.func, ast.Name)
                            and sub.func.id == "str"
                            and len(sub.args) == 1
                            and isinstance(sub.args[0], ast.Name)
                            and sub.args[0].id.startswith("exc")):
                        bad.append(f"line {sub.lineno}")
        assert not bad, (
            f"{len(bad)} handler(s) still send an unscrubbed exception to a "
            f"user ({', '.join(bad)}); html.escape is not a secret filter"
        )

    def test_the_guard_itself_can_see_a_multiline_call(self):
        """The blind spot, pinned. This fails against the old regex."""
        import ast
        planted = (
            "class H:\n"
            "    async def f(self, update, exc):\n"
            "        await self._send(update,\n"
            "                         f'boom {str(exc)[:200]}')\n"
        )
        tree = ast.parse(planted)
        found = [
            s for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "_send"
            for a in n.args for s in ast.walk(a)
            if isinstance(s, ast.Call) and isinstance(s.func, ast.Name)
            and s.func.id == "str"
        ]
        assert found, "the guard cannot see a leak split across two lines"

    def test_the_scrubber_is_actually_wired(self):
        assert self._src().count("_safe_exc_text(") >= 10, (
            "ten sites shared the defect; a helper wired to two of them is "
            "how the other eight keep leaking"
        )

    def test_the_allowlist_path_is_left_intact(self):
        # _operator_exc_detail still guards the Telegram-error path, where an
        # unknown class really must say nothing.
        assert "_operator_exc_detail(exc)" in self._src()
