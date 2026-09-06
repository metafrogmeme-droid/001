"""`/scan <venue>` reads one venue's own market list — three outcomes, not two.

The engine's sweep reads Bitget and overlays only the ACTIVE venue's own
markets; a person trading on Bitget could not look at Hyperliquid's builder
perps or a Bybit-only listing at all. `MarketScanner.scan_venue` reads any
venue through a keyless public client, and `bot/core/venue_scan.py` is the
seam that renders it, so the states can be planted here:

  answered, movers  → the list, ranked by 24h move, unread moves LAST and
                      printed as "—" (0.0% would be a claim nobody measured)
  answered, none    → "answered with N markets, none cleared the floor"
  did not answer    → "did not answer — nothing was scanned"

The overlay it grew from returns `[]` for the last two alike. Fine for an
overlay — the Bitget scan stands alone — and wrong for a command whose whole
answer this is.
"""
from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest

import bot.core.market_scanner as ms
from bot.core.market_scanner import MarketScanner
from bot.core.venue_scan import VenueScan, render_venue_scan
from bot.core.venues import valid_venue_ids
from bot.utils.i18n import SUPPORTED_LANGS, t
from bot.utils.models import MarketSignal


def _run(coro):
    return asyncio.run(coro)


def _tick(volume, pct=1.0, last=1.0):
    return {"last": last, "percentage": pct, "quoteVolume": volume}


TICKERS = {
    "HYPE/USDC:USDC": _tick(50_000_000, 12.5, 40.0),
    "XYZ-CL/USDC:USDC": _tick(120_000_000, -3.2, 64.1),
    "DUST/USDC:USDC": _tick(10_000, 40.0),               # below the crypto floor
    "NOPCT/USDC:USDC": {"last": 2.0, "quoteVolume": 5_000_000},   # no 24h move reported
}


class _Fake:
    def __init__(self, tickers=TICKERS):
        self._tickers = tickers
        self.calls = 0

    async def fetch_tickers(self):
        self.calls += 1
        return dict(self._tickers)


class _Boom:
    async def fetch_tickers(self):
        raise ConnectionError("venue down")


def _scanner_with(monkeypatch, client):
    scanner = MarketScanner()

    async def _client(vid):
        return client

    monkeypatch.setattr(scanner, "venue_data_exchange", _client)
    return scanner


# ── the scanner ────────────────────────────────────────────────────────

def test_scan_venue_ranks_by_move_and_keeps_an_unread_move_unread(monkeypatch):
    vs = _run(_scanner_with(monkeypatch, _Fake()).scan_venue("hyperliquid"))
    assert vs.error is None and not vs.unreadable
    assert vs.venue_id == "hyperliquid" and vs.display_name == "Hyperliquid"
    assert vs.markets == 4, "every ticker the venue returned, floor or no floor"
    syms = [s.symbol for s in vs.signals]
    assert syms[:2] == ["HYPE/USDC:USDC", "XYZ-CL/USDC:USDC"], "largest |move| first"
    assert "DUST/USDC:USDC" not in syms, "the volume floor still applies"
    nop = next(s for s in vs.signals if s.symbol == "NOPCT/USDC:USDC")
    assert nop.change_pct_24h is None, "no move reported is not a 0.0% move"
    assert syms[-1] == "NOPCT/USDC:USDC", "an unread move sorts last, not as flat"
    cl = next(s for s in vs.signals if s.symbol == "XYZ-CL/USDC:USDC")
    assert cl.asset_category == "Commodity", "builder perps keep their class"


def test_scan_venue_reports_a_dead_venue_as_unreadable_not_empty(monkeypatch):
    vs = _run(_scanner_with(monkeypatch, _Boom()).scan_venue("bybit"))
    assert vs.unreadable and "venue down" in (vs.error or "")
    assert vs.signals == [] and vs.markets == 0
    quiet = _run(_scanner_with(monkeypatch, _Fake({})).scan_venue("bybit"))
    assert not quiet.unreadable and quiet.signals == [] and quiet.markets == 0
    # The two states must never collapse into one reading.
    assert render_venue_scan(vs) != render_venue_scan(quiet)


def test_scan_venue_refuses_an_unknown_venue(monkeypatch):
    scanner = _scanner_with(monkeypatch, _Fake())
    with pytest.raises(ValueError):
        _run(scanner.scan_venue("binance"))
    with pytest.raises(ValueError):
        _run(scanner.scan_venue(""))


def test_venue_data_exchange_is_keyless_and_cached_per_venue(monkeypatch):
    built = []

    class _Client:
        def __init__(self, opts):
            built.append(opts)

        async def close(self):
            pass

    monkeypatch.setattr(ms, "ccxt", SimpleNamespace(
        hyperliquid=_Client, bybit=_Client, kucoinfutures=_Client))
    scanner = MarketScanner()
    a = _run(scanner.venue_data_exchange("hyperliquid"))
    b = _run(scanner.venue_data_exchange("hyperliquid"))
    c = _run(scanner.venue_data_exchange("bybit"))
    _run(scanner.venue_data_exchange("kucoin"))       # ccxt id is kucoinfutures
    assert a is b and c is not a
    assert len(built) == 3
    assert all("apiKey" not in o and "secret" not in o for o in built), "public data only"
    assert all(o["options"]["defaultType"] == "swap" for o in built)
    # Bitget is the futures client the engine already holds.
    sentinel = object()

    async def _fut():
        return sentinel

    monkeypatch.setattr(scanner, "_get_futures_exchange", _fut)
    assert _run(scanner.venue_data_exchange("bitget")) is sentinel
    _run(scanner.close())
    assert scanner._venue_clients == {}


def test_the_active_venue_client_is_untouched():
    """`_get_venue_data_exchange` (the ACTIVE venue's client, rebuilt on a
    /venue switch, routed to by the engine for :USDC symbols) keeps its own
    cache; the on-demand client does not replace it."""
    src = inspect.getsource(MarketScanner._get_venue_data_exchange)
    assert "_venue_data_exchange_id != venue.id" in src
    assert "_venue_clients" not in src


# ── the renderer ───────────────────────────────────────────────────────

def _signals():
    return [
        MarketSignal(symbol="HYPE/USDC:USDC", price=40.0, change_pct_24h=12.5,
                     volume_usd_24h=50_000_000.0, momentum_score=1.0),
        MarketSignal(symbol="XYZ-CL/USDC:USDC", price=64.1, change_pct_24h=-3.2,
                     volume_usd_24h=120_000_000.0, momentum_score=-0.32,
                     asset_category="Commodity"),
        MarketSignal(symbol="NOPCT/USDC:USDC", price=2.0, change_pct_24h=None,
                     volume_usd_24h=5_000_000.0),
    ]


@pytest.mark.parametrize("lang", list(SUPPORTED_LANGS))
def test_render_three_outcomes_in_every_language(lang):
    dead = VenueScan("bybit", "Bybit", error="ConnectionError: venue down")
    quiet = VenueScan("bybit", "Bybit", markets=300)
    full = VenueScan("hyperliquid", "Hyperliquid", signals=_signals(), markets=180)
    d, q, f = (render_venue_scan(x, lang) for x in (dead, quiet, full))
    assert d == t("venue_scan_unreachable", lang, venue="Bybit",
                  detail="ConnectionError: venue down")
    assert q == t("venue_scan_empty", lang, venue="Bybit", markets=300)
    assert len({d, q, f}) == 3, "three outcomes, three readings"
    assert "Hyperliquid" in f and "HYPE" in f and "+12.5%" in f and "-3.2%" in f
    assert "$50.0M" in f and "$120.0M" in f
    nop_row = next(line for line in f.splitlines() if "NOPCT" in line)
    assert "—" in nop_row and "%" not in nop_row, "an unread move is a dash, not 0.0%"
    assert nop_row.startswith("⚪"), "no direction was measured, so no colour"
    hype_row = next(line for line in f.splitlines() if "HYPE" in line)
    assert hype_row.startswith("🟢")


def test_render_escapes_what_the_venue_said():
    dead = VenueScan("bybit", "<Bybit>", error="<b>boom</b>")
    out = render_venue_scan(dead)
    assert "<b>boom</b>" not in out and "&lt;b&gt;boom" in out
    assert "&lt;Bybit&gt;" in out


def test_render_caps_the_list_and_says_so():
    many = [MarketSignal(symbol=f"S{i}/USDC:USDC", price=1.0, change_pct_24h=float(i),
                         volume_usd_24h=1e6) for i in range(40)]
    out = render_venue_scan(VenueScan("bybit", "Bybit", signals=many, markets=500), limit=15)
    assert out.count("\n") == 15, "header plus fifteen rows"
    assert t("venue_scan_header", "en", venue="Bybit", n=15, markets=500) in out


# ── the handler ────────────────────────────────────────────────────────

def _handler(scan_result, card_ok=False):
    from bot.skills.telegram_handler import TelegramHandler as H
    h = H.__new__(H)
    h.users = None
    h.sent = []
    h.cards = []

    async def _send(update, text, **kw):
        h.sent.append(text)

    async def _scan_venue(vid):
        h.scanned = vid
        return scan_result

    async def _card(update, signals, title, exchange=None):
        h.cards.append((list(signals), title, exchange))
        return card_ok

    async def _client(vid):
        return f"client:{vid}"

    h._send = _send
    h._render_scan_signals_card = _card
    h.engine = SimpleNamespace(scanner=SimpleNamespace(scan_venue=_scan_venue,
                                                       venue_data_exchange=_client))
    h.registry = SimpleNamespace()
    return h


def _update():
    async def _action(*a, **kw):
        pass

    return SimpleNamespace(
        effective_user=SimpleNamespace(id=7, language_code="en"),
        effective_chat=SimpleNamespace(id=7, type="private", send_chat_action=_action),
        message=SimpleNamespace(text="/scan hyperliquid"), callback_query=None)


def test_a_dead_venue_is_reported_not_rendered_as_quiet():
    h = _handler(VenueScan("hyperliquid", "Hyperliquid", error="ConnectionError: down"))
    _run(h._scan_one_venue(_update(), "hyperliquid"))
    assert h.scanned == "hyperliquid"
    assert h.sent[0] == t("venue_scan_ack", "en", venue="Hyperliquid")
    assert h.sent[-1] == t("venue_scan_unreachable", "en", venue="Hyperliquid",
                           detail="ConnectionError: down")
    assert h.cards == [], "nothing was read, so nothing is drawn"


def test_movers_draw_the_card_from_the_venues_own_client_and_fall_back_to_text():
    vs = VenueScan("hyperliquid", "Hyperliquid", signals=_signals(), markets=180)
    h = _handler(vs, card_ok=True)
    _run(h._scan_one_venue(_update(), "hyperliquid"))
    assert h.cards and h.cards[0][1] == "HYPERLIQUID SCAN"
    assert h.cards[0][2] == "client:hyperliquid", "sparklines come from the venue itself"
    assert len(h.sent) == 1, "the card stood in for the text"
    h = _handler(vs, card_ok=False)
    _run(h._scan_one_venue(_update(), "hyperliquid"))
    assert h.sent[-1] == render_venue_scan(vs, "en")


def test_scan_venues_lists_every_venue_as_a_command():
    h = _handler(VenueScan("bybit", "Bybit"))
    text = h._scan_venues_text("en")
    for vid in valid_venue_ids():
        assert f"/scan {vid}" in text
    assert text.startswith(t("venue_scan_list", "en"))


def test_cmd_scan_routes_a_venue_name_before_the_market_sweep():
    """Wiring: the venue branch runs BEFORE the "Scanning the market" ack and
    the scan_market dispatch, and only for a name the venue table knows —
    `/scan BTC` and `/scan deep` still reach the sweep."""
    from bot.skills.telegram_handler import TelegramHandler as H
    src = inspect.getsource(H._cmd_scan)
    assert "valid_venue_ids()" in src and "_scan_one_venue(update, " in src
    assert src.index("_scan_one_venue(") < src.index('dispatch("scan_market"')
    assert '("venues", "venue")' in src and "_scan_venues_text(" in src
