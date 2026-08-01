"""A public market-data URL was reported as "the venue rejected authentication".

Live, 2026-08-01, on an account holding two open positions:

    🚨 STARTUP: exchange auth FAILED
    Live trading is on but the venue rejected authentication — open positions
    cannot be protected until this is fixed.
    Venue said: bitget GET .../api/v3/market/instruments?category=USDC-FUTURES

Three things are wrong with that, and the operator's own /start a minute
later showed all of them: equity $388.33, two open positions, win rate 59%.
Reading a balance is an AUTHENTICATED call. Auth was fine.

  * /api/v3/market/instruments is a PUBLIC endpoint. It takes no credentials
    and cannot reject them.
  * category=USDC-FUTURES is a product line this bot does not trade.
  * The string carries no 40006 / 40012 / 40099. The preflight has bespoke
    hints for each of those precisely because that is what a real rejection
    looks like.

`fetch_balance()` is authenticated, but ccxt calls `load_markets()` first, so
a transient failure fetching instruments propagated into

    except Exception as _auth_exc:
        bal = {"error": str(_auth_exc)}

and every failure inside that call became a credential verdict. Which then
ran `set_live_auth_status(False)` -- the pre-execute gate reads that and
HALTS NEW LIVE ENTRIES until a restart.

So a network blip stopped a healthy account from trading, and told its owner
their positions were unprotected.

The costs are asymmetric in BOTH directions, which is why this is a
classifier and not a loosened guard:

    blip called auth failure  ->  entries halted on a healthy account
    auth failure called blip  ->  a live position with no stop, unannounced

An explicit auth marker halts. A positively-identified transport failure does
not. ANYTHING ELSE STILL HALTS -- the unknown case keeps failing closed,
because an unprotected position costs more than a missed entry.
"""
from __future__ import annotations

import pytest

from bot.main import _is_auth_rejection, _is_transport_failure

#: Verbatim from the live alert.
LIVE_ERROR = ("bitget GET https://api.bitget.com/api/v3/market/instruments"
              "?category=USDC-FUTURES")


def _halts(err: str) -> bool:
    """Mirrors the caller: only a positive transport identification skips."""
    return not _is_transport_failure(err)


class TestTheLiveIncident:
    def test_the_exact_string_is_not_an_auth_rejection(self):
        assert not _is_auth_rejection(LIVE_ERROR)

    def test_the_exact_string_is_recognised_as_transport(self):
        assert _is_transport_failure(LIVE_ERROR)

    def test_the_exact_string_would_no_longer_halt_entries(self):
        assert not _halts(LIVE_ERROR), (
            "this halted a healthy account with two open positions while "
            "/start read its equity a minute later"
        )


class TestRealRejectionsStillHalt:
    @pytest.mark.parametrize("err", [
        'bitget {"code":"40006","msg":"invalid ACCESS_KEY"}',
        "bitget 40012 passphrase does not match",
        "bitget 40099 wrong environment for this key",
        "Invalid API key supplied",
        "signature verification failed",
        "401 Unauthorized",
        "permission denied for this endpoint",
    ])
    def test_an_auth_marker_halts(self, err):
        assert _is_auth_rejection(err)
        assert _halts(err), f"must halt: {err}"

    def test_an_auth_error_mentioning_a_url_still_halts(self):
        # The dangerous overlap: a genuine rejection whose message happens to
        # contain a URL or a status code. The auth marker must win.
        err = ("bitget GET https://api.bitget.com/api/v2/account/assets "
               '502 {"code":"40006","msg":"invalid ACCESS_KEY"}')
        assert _is_auth_rejection(err)
        assert not _is_transport_failure(err), (
            "an auth rejection must never be reclassified as transport just "
            "because the message also mentions a gateway error"
        )
        assert _halts(err)


class TestTransportFailuresDoNotCondemnTheKey:
    @pytest.mark.parametrize("err", [
        "Request timeout",
        "Connection reset by peer",
        "502 Bad Gateway",
        "503 Service Unavailable",
        "504 Gateway Timeout",
        "429 Too Many Requests",
        "dns resolution failed",
        "SSL certificate verify failed",
        "bitget GET /api/v3/market/instruments?category=SPOT",
    ])
    def test_they_are_not_auth_and_do_not_halt(self, err):
        assert not _is_auth_rejection(err), err
        assert not _halts(err), err


class TestTheUnknownCaseStillFailsClosed:
    """The property that keeps this a narrowing rather than a weakening."""

    @pytest.mark.parametrize("err", [
        "something nobody has seen before",
        "",
        "venue returned an unexpected shape",
        "ccxt.base.errors.ExchangeError",
    ])
    def test_an_unclassifiable_error_halts(self, err):
        assert not _is_transport_failure(err), err
        assert _halts(err), (
            f"an unrecognised error must keep the old behaviour: {err!r} — "
            "an unprotected live position costs more than a missed entry"
        )

    def test_none_is_handled(self):
        assert not _is_auth_rejection(None)      # type: ignore[arg-type]
        assert not _is_transport_failure(None)   # type: ignore[arg-type]
        assert _halts(None)                      # type: ignore[arg-type]


class TestTheCallerActsOnTheClassification:
    def _src(self):
        from tests.source_scan import code_only
        return code_only(open("bot/main.py", encoding="utf-8").read())

    def _joined(self):
        """Source with adjacent f-string literals reconstituted.

        A user-facing sentence written across continuation lines --
        `f"not a "` / `f"credential rejection"` -- contains no such substring
        in the raw source. Third time this session a source scan has matched
        the file's LAYOUT instead of its CONTENT; joining first asks the
        question that was meant.
        """
        import re
        return re.sub(r'"\s*\n\s*f?"', "", self._src())

    def test_the_transport_branch_does_not_set_auth_down(self):
        src = self._src()
        i = src.index("if _is_transport_failure(_e):")
        j = src.index("else:", i)
        branch = src[i:j]
        assert "set_live_auth_status(False" not in branch, (
            "the whole point is that a transport failure must not halt entries"
        )

    def test_the_auth_branch_still_sets_auth_down(self):
        src = self._src()
        i = src.index("STARTUP: exchange auth FAILED")
        assert "set_live_auth_status(False" in src[i:i + 2000]

    def test_the_transport_message_says_the_key_was_not_judged(self):
        src = self._joined()
        i = src.index("could not reach the ")
        window = src[i:i + 700]
        assert "not a credential rejection" in window
        assert "never judged" in window
        assert "NOT halted" in window, (
            "the operator needs to know their trading is still live — the old "
            "message told them the opposite"
        )
