"""Telegram /yield had the web panel's defect one surface over -- and its own.

`_engine_free_usdt()` already answers three ways: 0.0 in paper mode (nothing
to read), None in live mode when the balance cache is empty or stale (could
not read), a float otherwise. `/yield` did not call it. It read the cache
itself with `float(cache.get("free", 0) or 0)`, turned None into 0.0, and
`build_report` took the "nothing idle on futures" path: a spot-only figure
presented as the operator's whole idle capital.

And even a None handed through would have gone unsaid: `format_report_html`
never rendered `report.incomplete`. With no spot rows either, the card said
"nothing to stake" -- a confident negative from a failed read, on the
surface that decides whether money sits idle.
"""
from __future__ import annotations

import io
import tokenize
from pathlib import Path

from bot.core.yield_radar import YieldReport, YieldRow, format_report_html

ROOT = Path(__file__).resolve().parent.parent
NOTE = "Free futures margin could not be read, so it is not counted below. Spot holdings are complete."


def _row(coin="USDC", usd=50.0):
    return YieldRow(coin=coin, idle_amount=usd, idle_usd=usd, stakeable_usd=usd,
                    apy_flexible=4.0, source="spot", est_year_usd=2.0)


def test_an_incomplete_report_with_rows_says_so_and_labels_the_total_partial():
    rep = YieldReport(rows=[_row()], total_idle_usd=50.0, total_est_year_usd=2.0, incomplete=NOTE)
    out = format_report_html(rep)
    assert "could not be read" in out
    assert "Total idle (partial" in out and "futures margin unread" in out
    assert "\nTotal idle:" not in out, "the plain total label claims the figure is whole"


def test_an_incomplete_report_with_no_rows_is_not_nothing_to_stake():
    rep = YieldReport(incomplete=NOTE)
    out = format_report_html(rep)
    assert "nothing to stake" not in out, "THE DEFECT: a confident negative from a failed read"
    assert "could not be read" in out
    assert "unknown, not empty" in out


def test_a_complete_report_carries_no_note():
    rep = YieldReport(rows=[_row()], total_idle_usd=50.0, total_est_year_usd=2.0)
    out = format_report_html(rep)
    assert "could not be read" not in out and "partial" not in out
    assert "Total idle: <code>$50.00</code>" in out


def test_a_complete_empty_report_still_says_nothing_to_stake():
    """A real 'nothing idle' deserves the sentence it was written for."""
    assert "nothing to stake" in format_report_html(YieldReport())


def _code_only(path: Path) -> str:
    src = path.read_text(encoding="utf-8")
    return " ".join(t.string for t in tokenize.generate_tokens(io.StringIO(src).readline)
                    if t.type != tokenize.COMMENT)


def test_yield_reads_the_margin_through_the_helper():
    # /yield and the helper live in the yield mixin since the handler split.
    code = _code_only(ROOT / "bot" / "skills" / "yield_commands.py")
    i = code.find("async def _cmd_yield")
    assert i > 0
    body = code[i:code.find("def _yield_client", i)]
    assert "self . _engine_free_usdt ( )" in body, "/yield must read through the None-aware helper"
    assert 'cache . get ( "free" , 0 ) or 0' not in body, "the inline coercion is back"
    assert "futures_free_usdt = free_usdt" in body, "None must be handed to build_report by name"


# ── behaviour: drive /yield itself with an unread cache ──────────────────────

def _handler_class():
    import bot.skills.telegram_handler as th
    return next(v for v in vars(th).values()
                if isinstance(v, type) and hasattr(v, "_cmd_yield") and hasattr(v, "_engine_free_usdt"))


async def _drive_yield(monkeypatch, cache, *, live=True):
    """Bind the real /yield and the real helper onto a bare host; fake the
    client, the balance cache and build_report; capture what build_report
    was handed and what the operator was sent."""
    import asyncio
    from types import SimpleNamespace

    import bot.config as cfg
    from bot.core.yield_radar import YieldReport

    cls = _handler_class()
    seen, sent = {}, []
    monkeypatch.setattr(type(cfg.CONFIG), "is_live", lambda self: live)
    monkeypatch.setattr("bot.core.bitget_v3_client.BitgetV3Client.from_config",
                        staticmethod(lambda: SimpleNamespace(has_credentials=True)))

    def fake_build(client, futures_free_usdt=0.0, prices=None):
        seen["futures_free_usdt"] = futures_free_usdt
        rep = YieldReport()
        if futures_free_usdt is None:
            rep.incomplete = NOTE
        return rep
    monkeypatch.setattr("bot.core.yield_radar.build_report", fake_build)
    monkeypatch.setattr("bot.core.yield_radar.fetch_bybit_savings_catalog", lambda: None)

    async def _send(update, text, *a, **k):
        sent.append(text)

    host = SimpleNamespace(
        _is_admin=lambda update: True,
        _send=_send,
        engine=SimpleNamespace(live_balance_cached=lambda max_age_s=900.0: cache),
    )
    host._engine_free_usdt = cls._engine_free_usdt.__get__(host)
    host._cmd_yield = cls._cmd_yield.__get__(host)
    await host._cmd_yield(SimpleNamespace(), SimpleNamespace(args=[]))
    await asyncio.sleep(0)
    return seen, sent


import pytest  # noqa: E402  (grouped with the behaviour tests it serves)


@pytest.mark.asyncio
async def test_yield_hands_an_unread_margin_to_build_report_as_none(monkeypatch):
    seen, sent = await _drive_yield(monkeypatch, None, live=True)
    assert seen["futures_free_usdt"] is None, \
        "THE DEFECT: the helper's None was coerced to 0 on the way to build_report"
    card = sent[-1]
    assert "could not be read" in card and "unknown, not empty" in card
    assert "nothing to stake" not in card


@pytest.mark.asyncio
async def test_yield_hands_a_real_margin_through(monkeypatch):
    seen, _sent = await _drive_yield(monkeypatch, {"free": 12.5}, live=True)
    assert seen["futures_free_usdt"] == 12.5


@pytest.mark.asyncio
async def test_yield_in_paper_mode_reports_a_complete_zero_not_unknown(monkeypatch):
    seen, sent = await _drive_yield(monkeypatch, None, live=False)
    assert seen["futures_free_usdt"] == 0.0, "paper: there is no live margin, and that is known"
    assert "could not be read" not in sent[-1]
