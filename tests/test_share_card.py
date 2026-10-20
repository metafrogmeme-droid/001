"""Share card — the privacy-safe PNG behind the web share flow.

The card is a pure function of (symbol, direction, pnl_pct). Its privacy
contract is the point: symbol + direction + PnL PERCENT only — never a dollar
figure, size, margin, or entry/exit price, so a shared win can never leak
account size. The gateway endpoint clamps caller-supplied inputs hard because
the render is CPU-bound Pillow work.
"""
import inspect

import pytest

from bot.formatters.signal_card import render_share_card


def test_render_share_card_returns_png_bytes():
    png = render_share_card(
        {"symbol": "BTC/USDT:USDT", "direction": "LONG", "pnl_pct": 12.34})
    assert isinstance(png, bytes) and len(png) > 0
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_share_card_negative_and_short():
    png = render_share_card(
        {"symbol": "ETH", "direction": "SHORT", "pnl_pct": -3.5})
    assert isinstance(png, bytes) and len(png) > 0


def test_render_share_card_tolerates_garbage_pct():
    # Caller-supplied junk must render (as 0.00%), never raise.
    png = render_share_card(
        {"symbol": "SOL", "direction": "LONG", "pnl_pct": "not-a-number"})
    assert isinstance(png, bytes) and len(png) > 0


def test_share_card_renderer_never_touches_dollar_fields():
    # PRIVACY REGRESSION GUARD: the renderer must not read any size/dollar key.
    # render_close_card (the visually-closest sibling) draws $ cells — a
    # refactor that "reuses" it would leak account size into a public share.
    # The docstring DOCUMENTS the forbidden keys, so check the code body only.
    import ast
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(render_share_card)))
    fn = tree.body[0]
    body = fn.body
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)):
        body = body[1:]                              # drop the docstring
    code = "\n".join(ast.unparse(stmt) for stmt in body)
    for forbidden in ("pnl_usd", "size_usd", "margin_usd", "net_pnl",
                      "cost_usd", "equity", "entry_price", "exit_price"):
        assert forbidden not in code, f"share card must never read {forbidden}"


async def _call(qs):
    from aiohttp.test_utils import make_mocked_request
    from bot.web.user_gateway import handle_share_card
    req = make_mocked_request("GET", f"/gateway/share-card?{qs}")
    return await handle_share_card(req)


@pytest.mark.asyncio
async def test_gateway_share_card_validates_and_serves_png():
    # The aiohttp handler: clamps inputs, 400s junk, serves image/png.
    ok = await _call("symbol=BTC&direction=LONG&pnl_pct=4.20")
    assert ok.status == 200
    assert ok.content_type == "image/png"
    assert bytes(ok.body)[:8] == b"\x89PNG\r\n\x1a\n"

    assert (await _call("symbol=btc%2Fusdt&direction=LONG&pnl_pct=1")).status == 400
    assert (await _call("symbol=BTC&direction=SIDEWAYS&pnl_pct=1")).status == 400
    assert (await _call("symbol=BTC&direction=LONG&pnl_pct=abc")).status == 400
    assert (await _call("symbol=BTC&direction=LONG&pnl_pct=inf")).status == 400
    assert (await _call("symbol=&direction=LONG&pnl_pct=1")).status == 400


@pytest.mark.asyncio
async def test_gateway_share_card_translates_missing_pillow_to_503(monkeypatch):
    import bot.formatters.signal_card as sc

    monkeypatch.setattr(sc, "render_share_card", lambda data: b"")
    resp = await _call("symbol=BTC&direction=LONG&pnl_pct=1")
    assert resp.status == 503


# ── The review of 2026-10-19: when, and which book ─────────────────────────

def _rows(data):
    from tests.png_text import drawn
    return drawn(render_share_card, data)


BASE = {"symbol": "TRUMP", "direction": "SHORT", "pnl_pct": 17.41}


def test_the_card_says_when_the_trade_was_opened_and_closed():
    rows = _rows(dict(BASE, opened_at="2026-10-17T20:12:00Z", closed_at="2026-10-19T20:35:10+00:00"))
    texts = [t for t, _ in rows]
    assert "Opened 17 Oct 2026 20:12 UTC · Closed 19 Oct 2026 20:35 UTC" in texts, texts


def test_an_unparseable_or_absent_time_is_no_date_at_all():
    from bot.formatters.signal_card import _share_when
    assert _share_when(None, None) == ""
    assert _share_when("soon", "2026-10-19T20:35:10Z") == "Closed 19 Oct 2026 20:35 UTC"
    assert not any(t.startswith("Opened") or t.startswith("Closed") for t, _ in _rows(BASE))


def test_the_book_is_a_chip_and_a_sentence_only_when_known():
    from bot.formatters import signal_card as SC
    from tests.png_text import fill_of
    paper = _rows(dict(BASE, book="paper"))
    assert fill_of(paper, "PAPER") == SC._GRAY
    assert any(t.startswith("Paper trade, traded autonomously.") for t, _ in paper)
    live = _rows(dict(BASE, book="live"))
    assert fill_of(live, "LIVE") == SC._GREEN
    assert any(t.startswith("Live trade, traded autonomously.") for t, _ in live)
    unknown = [t for t, _ in _rows(BASE)]
    assert "PAPER" not in unknown and "LIVE" not in unknown
    assert any(t.startswith("Traded autonomously. Shared without") for t, _ in _rows(BASE)), (
        "an unknown book claims neither")
    assert not any("Live trade" in t or "Paper trade" in t for t in unknown)


def test_the_disclaimer_is_readable():
    from bot.formatters import signal_card as SC
    from tests.png_text import fill_of
    assert fill_of(_rows(BASE), "Not investment advice.") == SC._GRAY


@pytest.mark.asyncio
async def test_gateway_relays_the_optional_fields_and_refuses_junk():
    import bot.formatters.signal_card as sc
    seen = {}

    def _spy(data):
        seen.update(data)
        return b"\x89PNG\r\n\x1a\n" + b"x" * 16

    orig = sc.render_share_card
    sc.render_share_card = _spy
    try:
        ok = await _call("symbol=BTC&direction=LONG&pnl_pct=1&opened_at=2026-10-17T20:12:00Z"
                         "&closed_at=2026-10-19T20:35:10%2B02:00&book=Paper")
        assert ok.status == 200, ok.status
        assert seen["opened_at"] == "2026-10-17T20:12:00+00:00"
        assert seen["closed_at"] == "2026-10-19T18:35:10+00:00", "relayed as a UTC instant"
        assert seen["book"] == "paper"
        seen.clear()
        bare = await _call("symbol=BTC&direction=LONG&pnl_pct=1")
        assert bare.status == 200 and "book" not in seen and "opened_at" not in seen
        assert (await _call("symbol=BTC&direction=LONG&pnl_pct=1&opened_at=yesterday")).status == 400
        assert (await _call("symbol=BTC&direction=LONG&pnl_pct=1&book=margin")).status == 400
    finally:
        sc.render_share_card = orig
