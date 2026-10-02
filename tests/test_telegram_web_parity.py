"""Telegram web-parity commands (/networth /exposure /research /rwa) — PR EE.

One brain, one implementation: networth/exposure/research/rwa are Node-side
libs the web panels already use; the Telegram commands fetch the SAME cards
over the shared-secret sync channel (bot/utils/web_data_pull.py). Each
fetches the card RENDERED, because a second Python formatter is a second
answer. Commands degrade to a "link the web app" hint when the channel is
unconfigured — never a crash.
"""
import inspect

import bot.utils.web_data_pull as wdp
from bot.skills.telegram_handler import TelegramHandler
from tests.source_scan import code_only

# ── Pull module (mirror the leaderboard_pull tri-state idiom) ────────────────

class TestWebDataPull:
    def test_unconfigured_returns_none(self, monkeypatch):
        monkeypatch.setattr(wdp, "SYNC_SECRET", "")
        assert wdp.fetch_exposure("111") is None
        assert wdp.fetch_research("BTC") is None
        assert wdp.fetch_networth("111") is None
        # `fetch_rwa` is gone: the RWA card is fetched RENDERED over the card
        # route, because a second Python formatter of it raised on the honest
        # `None` the radar publishes for an unreadable 24h change.
        assert wdp.fetch_web_card("rwa") is None

    def test_paths_and_sanitization(self, monkeypatch):
        monkeypatch.setattr(wdp, "SYNC_SECRET", "s" * 48)
        calls = []
        monkeypatch.setattr(wdp, "_request",
                            lambda path, body=None: calls.append(path) or {"ok": 1})
        wdp.fetch_exposure("111")
        wdp.fetch_research("pendle/usdt")           # junk stripped, USDT dropped
        wdp.fetch_networth("111")
        wdp.fetch_web_card("rwa")
        # Exposure left the raw `/api/bot/sync/exposure` payload. The command
        # pulls the rendered card, the same route wallet and DeFi use, so a
        # second Python formatter cannot drift from `app/lib/exposure.js`.
        # Research left the raw `/api/bot/sync/research/:symbol` payload.
        # The command pulls the rendered card, so a second Python formatter
        # cannot drift from `app/lib/research.js`. The symbol is a query
        # parameter: `pendle/usdt` is the base PENDLE.
        assert calls == ["/api/bot/sync/card/exposure?telegram_id=111",
                         "/api/bot/sync/card/research?symbol=PENDLE",
                         "/api/bot/sync/card/networth?telegram_id=111",
                         "/api/bot/sync/card/rwa"]

    def test_bad_symbol_never_reaches_the_wire(self, monkeypatch):
        monkeypatch.setattr(wdp, "SYNC_SECRET", "s" * 48)
        monkeypatch.setattr(wdp, "_request",
                            lambda path, body=None: (_ for _ in ()).throw(
                                AssertionError("must not be called")))
        assert wdp.fetch_research("!!!") is None
        assert wdp.fetch_research("") is None


# ── Formatters (pure) ────────────────────────────────────────────────────────

class TestFormatters:
    def test_the_networth_card_has_no_second_python_formatter(self):
        """There is ONE renderer, and it is `app/lib/networth.js`'s.

        `_format_networth` mirrored that card by hand. The card is fetched
        rendered now, so the claim worth pinning is that no Python copy
        came back. Nothing here places, confirms, sizes, or closes.
        """
        assert not hasattr(TelegramHandler, "_format_networth")
        src = code_only(inspect.getsource(TelegramHandler.networth_card_text))
        assert "_web_card_text" in src and '"networth"' in src
        assert "_format" not in src, "the card must not be re-formatted here"

    def test_the_exposure_card_has_no_second_python_formatter(self):
        """There is ONE renderer, and it is `app/lib/exposure.js`'s.

        `_format_exposure` mirrored that card by hand and read a missing
        total as zero (`or 0`). An unread book is not a flat one. The card
        is fetched rendered now, so the claim worth pinning is that no
        Python copy came back.
        """
        assert not hasattr(TelegramHandler, "_format_exposure")
        src = code_only(inspect.getsource(TelegramHandler.exposure_card_text))
        assert "_web_card_text" in src and '"exposure"' in src
        assert "_format" not in src, "the card must not be re-formatted here"

    def test_the_research_card_has_no_second_python_formatter(self):
        """There is ONE renderer, and it is `app/lib/research.js`'s.

        `_format_research` mirrored that card by hand. The card is fetched
        rendered now, so the claim worth pinning is that no Python copy
        came back. Nothing here places, confirms, sizes, or closes a trade.
        """
        assert not hasattr(TelegramHandler, "_format_research")
        src = code_only(inspect.getsource(TelegramHandler.research_card_text))
        assert "_web_card_text" in src and '"research"' in src
        assert "_format" not in src, "the card must not be re-formatted here"

    def test_the_rwa_card_has_no_second_python_formatter(self):
        """There is ONE renderer, and it is `app/lib/rwa.js`'s.

        `_format_rwa` mirrored that card by hand and this test asserted the
        mirror held. It did not, where it cost most: its `_pct` did
        ``float(v)`` and ``.get(k, 0)`` does not fire for a key PRESENT with
        ``None``, which is exactly what the radar publishes for a 24h change
        the venue did not report — so the whole card raised `TypeError` on
        the ordinary case. A mirror that must be kept in step by hand is the
        second-answer shape; the card is fetched rendered now, so the claim
        worth pinning is that no Python copy came back.
        """
        assert not hasattr(TelegramHandler, "_format_rwa")
        # `code_only` first: the docstring below NAMES `_format_rwa` to explain
        # the deletion, so a raw scan for that string matches the prose that
        # records the fix. CLAUDE.md's own "strip comments first", in the test
        # written for the deletion.
        src = code_only(inspect.getsource(TelegramHandler.rwa_card_text))
        assert "fetch_web_card" in src and '"rwa"' in src
        assert "_format" not in src, "the card must not be re-formatted here"


# ── Wiring pins ──────────────────────────────────────────────────────────────

def test_commands_registered_and_guarded():
    src = inspect.getsource(TelegramHandler)
    for cmd in ("networth", "exposure", "research", "rwa"):
        assert f'("{cmd}", self._cmd_{cmd})' in src, f"/{cmd} not registered"
    for meth in ("_cmd_networth", "_cmd_exposure", "_cmd_research", "_cmd_rwa"):
        fn_src = inspect.getsource(getattr(TelegramHandler, meth))
        assert "@guard(" in fn_src, f"{meth} must run the auth gate"


def test_commands_fetch_off_the_event_loop():
    # The sync-channel fetch is blocking urllib — it must run in a thread so a
    # slow website can never stall the Telegram event loop. /research and
    # /rwa fetch inside the seam their routed intents share, so the seam is
    # what is read.
    for meth in ("rwa_card_text",):
        src = inspect.getsource(getattr(TelegramHandler, meth))
        assert "to_thread" in src, f"{meth} must not block the loop"
    # /exposure and /research fetch inside `_web_card_text`, the same helper
    # the other rendered cards use. The command itself must not format a
    # second copy.
    for meth in ("exposure_card_text", "research_card_text", "networth_card_text"):
        src = inspect.getsource(getattr(TelegramHandler, meth))
        assert "_web_card_text" in src and "to_thread" not in src, meth
    assert "to_thread" in inspect.getsource(TelegramHandler._web_card_text)
    assert "fetch_research" in inspect.getsource(TelegramHandler._web_card_text)
    assert "fetch_networth" in inspect.getsource(TelegramHandler._web_card_text)
