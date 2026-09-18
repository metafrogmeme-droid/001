"""Telegram web-parity commands (/networth /exposure /research /rwa) — PR EE.

One brain, one implementation: exposure/research/rwa are Node-side libs the
web panels already use; the Telegram commands fetch the SAME payloads over the
shared-secret sync channel (bot/utils/web_data_pull.py). /networth, /exposure
and /research format the payload here; /rwa fetches the card RENDERED, because
a second Python formatter of it raised on the honest `None` the radar
publishes for an unreadable 24h change.
Net worth reuses the gateway's own read-only primitives. Commands degrade to a
"link the web app" hint when the channel is unconfigured — never a crash.
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
        wdp.fetch_web_card("rwa")
        assert calls == ["/api/bot/sync/exposure?telegram_id=111",
                         "/api/bot/sync/research/PENDLE",
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
    def test_networth_connected_and_not(self):
        msg = TelegramHandler._format_networth(
            {"equity_usd": 10140.0, "total_pnl": 140.0},
            {"connected": True, "venue": "bitget", "equity_usd": 2500.5})
        assert "$10,140.00" in msg and "Bitget" in msg and "$2,500.50" in msg
        msg2 = TelegramHandler._format_networth(None, {"connected": False})
        assert "not connected" in msg2 and "no snapshot" in msg2

    def test_exposure_rows_flags_and_warnings(self):
        msg = TelegramHandler._format_exposure({
            "net_total_usd": 900.0, "gross_total_usd": 1100.0, "cash_usd": 50.0,
            "assets": [{"base": "ETH", "net_usd": 900.0, "perp_long_usd": 500.0,
                        "perp_short_usd": 0.0, "spot_usd": 400.0,
                        "flags": ["stacked_long"]}],
            "warnings": ["ETH: you hold it on-chain AND are long the perp"],
        })
        assert "ETH" in msg and "stacked_long" in msg
        assert "$900.00" in msg and "⚠️ ETH:" in msg
        assert "nothing here can resize" in msg

    def test_research_strips_web_html_to_telegram_subset(self):
        msg = TelegramHandler._format_research({
            "base": "PENDLE",
            "sections": [{"title": "Market",
                          "html": "Price <b>$3.2</b><br><span data-x=1>vol up</span>"}],
            "disclaimer": "Not financial advice.",
        })
        assert "Research: PENDLE" in msg
        assert "<b>$3.2</b>" in msg and "vol up" in msg
        assert "<span" not in msg and "<br" not in msg
        assert "Not financial advice." in msg

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
    for meth in ("_cmd_exposure", "research_card_text", "rwa_card_text"):
        src = inspect.getsource(getattr(TelegramHandler, meth))
        assert "to_thread" in src, f"{meth} must not block the loop"
