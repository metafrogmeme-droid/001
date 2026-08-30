"""A per-user executor must sign v3 requests with THAT user's keys.

`LiveExecutor.__init__` takes `user_id` and a `credentials` dict, and its own
comment says what they are for: "Per-user executors authenticate with the user's
OWN linked keys (decrypted from the credential store); the shared operator
executor uses CONFIG.exchange exactly as before."

The ccxt path honours that — `_get_exchange` passes `self._credentials` into
`self._venue.create_exchange(cfg, self._credentials)`. The Bitget **v3** channel
does not. Every one of its four call sites builds a client with
`BitgetV3Client.from_config()`, which reads the global `CONFIG.exchange`:

    line 1390  GET  /api/v3/account/settings          read
    line 4996  GET  /api/v3/position/current-position read
    line 5321  POST /api/v3/trade/place-strategy-order  THE STOP-LOSS / TAKE-PROFIT
    line 8765  POST /api/v3/trade/close-positions       THE FLASH CLOSE

The two writes are the money. On a per-user executor:

  * the protective stop for the USER's position is signed with the OPERATOR's
    keys and lands on the OPERATOR's account, so the user's live position
    carries no stop of its own; and
  * a flash close — which runs when something has already gone wrong — tries to
    close the position on the operator's account, so the user stays exposed and,
    if the operator holds the same symbol and side, THEIR position is closed
    instead.

The reads are quieter but still wrong: a per-user executor reconciling against
the operator's positions and account settings.

Latent today — `PER_USER_LIVE_ENABLED` defaults False (`bot/config.py`), so
every executor IS the operator executor and `from_config()` is the correct
source. It becomes real the moment that documented, supported feature is turned
on, and nothing warns that it has.

The fix mirrors what `bot/core/venues.py:172-177` already does for the ccxt
path: take the per-user credentials when present, fall back to `CONFIG.exchange`
otherwise.
"""

from __future__ import annotations

import pytest

from bot.core.bitget_v3_client import BitgetV3Client

USER_CREDS = {"api_key": "USER-KEY", "api_secret": "USER-SECRET",
              "passphrase": "USER-PASS"}


class TestFromCredentials:
    """The constructor half — a client built from an explicit credential dict."""

    def test_uses_the_supplied_credentials(self):
        c = BitgetV3Client.from_credentials(USER_CREDS)
        assert c._api_key == "USER-KEY"
        assert c._api_secret == "USER-SECRET"
        assert c._passphrase == "USER-PASS"

    def test_falls_back_to_config_when_no_credentials(self):
        """The operator executor passes None and must behave exactly as before."""
        from bot.config import CONFIG

        c = BitgetV3Client.from_credentials(None)
        assert c._api_key == CONFIG.exchange.api_key
        assert c._api_secret == CONFIG.exchange.api_secret

    @pytest.mark.parametrize("creds", [{}, {"api_key": "", "api_secret": ""},
                                       {"api_key": "k"}])
    def test_incomplete_credentials_fall_back_rather_than_signing_with_half(self, creds):
        """A half-populated dict must not produce a client that signs with a
        blank secret — that is an authentication failure dressed as a request.
        `venues.py:178` takes the same position for the ccxt path.
        """
        from bot.config import CONFIG

        c = BitgetV3Client.from_credentials(creds)
        assert c._api_key == CONFIG.exchange.api_key
        assert c._api_secret == CONFIG.exchange.api_secret


class TestExecutorPicksTheRightClient:
    """The wiring half — which credentials an executor's v3 client actually gets."""

    def _executor(self, credentials=None, user_id=None):
        from bot.core.live_executor import LiveExecutor
        return LiveExecutor(user_id=user_id, credentials=credentials)

    def test_per_user_executor_signs_with_the_users_keys(self):
        ex = self._executor(credentials=USER_CREDS, user_id="999")
        client = ex._v3_client()
        assert client._api_key == "USER-KEY", (
            "a per-user executor built its v3 client from CONFIG.exchange, so "
            "its stop-loss and flash-close would be signed with the OPERATOR's "
            "keys and land on the operator's account"
        )
        assert client._api_secret == "USER-SECRET"

    def test_operator_executor_is_unchanged(self):
        """The control. No credentials → CONFIG.exchange, byte-identical."""
        from bot.config import CONFIG

        ex = self._executor()
        client = ex._v3_client()
        assert client._api_key == CONFIG.exchange.api_key
        assert client._api_secret == CONFIG.exchange.api_secret


def _executor_source() -> str:
    """live_executor.py with comments and docstrings blanked.

    The call sites are explained in prose naming `from_config`, so an unstripped
    scan matches the explanation rather than the code.
    """
    import io
    import tokenize
    from pathlib import Path

    text = (Path(__file__).resolve().parent.parent / "bot" / "core"
            / "live_executor.py").read_text(encoding="utf-8")
    doomed, prev = [], None
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                doomed.append((tok.start, tok.end))
                continue
            if tok.type == tokenize.STRING and prev in (
                    None, tokenize.NEWLINE, tokenize.NL,
                    tokenize.INDENT, tokenize.DEDENT):
                doomed.append((tok.start, tok.end))
                continue
            prev = tok.type
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return text
    grid = [list(line) for line in text.splitlines(keepends=True)]
    for (srow, scol), (erow, ecol) in doomed:
        for row in range(srow, erow + 1):
            if not 1 <= row <= len(grid):
                continue
            line = grid[row - 1]
            for i in range(scol if row == srow else 0,
                           min(ecol if row == erow else len(line), len(line))):
                if line[i] != "\n":
                    line[i] = " "
    return "".join("".join(r) for r in grid)


def test_no_v3_call_site_still_reaches_for_the_global_config():
    """A guard that is not reached is not a guard.

    `_v3_client()` fixes nothing while a call site still calls
    `BitgetV3Client.from_config()` directly. This is a property of the call
    sites, which no unit test can see — the exact case CLAUDE.md sanctions a
    source scan for.
    """
    import re

    code = _executor_source()
    offenders = [
        (i + 1, ln.strip())
        for i, ln in enumerate(code.splitlines())
        if re.search(r"BitgetV3Client\.from_config\(", ln)
    ]
    assert not offenders, (
        "a v3 call site still builds its client from the global CONFIG.exchange "
        "instead of the executor's own credentials, so a per-user request there "
        "is signed with the operator's keys:\n  "
        + "\n  ".join(f"line {n}: {t[:120]}" for n, t in offenders)
    )
