"""A headline whose time cannot be read is counted on the card, not dropped.

PR 521 ranked undated rows last so they would not read as just published.
In practice that dropped them: with three feeds the store holds more dated
rows than any card lists, so an undated row never reached "Latest headlines",
and once the store hit its cap it was dropped on arrival while `refresh`
counted it as added. A feed whose `pubDate` spelled the time without seconds
("15:04 GMT", valid RFC 822) parsed as undated for every item, so that feed
contributed nothing and nothing said so. And "15:04:05 -0400" was read as
15:04 UTC, four hours off.
"""
from __future__ import annotations

import asyncio
import calendar
import json
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

import bot.web.user_gateway as ug
from bot.core.news import NewsItem, NewsRadar, _parse_pubdate, render_news_digest
from bot.skills.telegram_handler import TelegramHandler

T0 = float(calendar.timegm((2026, 7, 21, 15, 4, 0, 0, 0, 0)))
NOW = T0 + 3600


# ── the clock ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, ts", [
    ("Mon, 21 Jul 2026 15:04 GMT", T0),                   # no seconds
    ("Mon, 21 Jul 2026 15:04:05 GMT", T0 + 5),
    ("Tue, 21 Jul 2026 15:04:00 -0400", T0 + 4 * 3600),   # a numeric zone
    ("21 Jul 2026 15:04:00 +0200", T0 - 2 * 3600),        # no weekday
    ("2026-07-21T15:04:00Z", T0),                         # Atom
    ("2026-07-21T17:04:00.250+02:00", T0 + 0.25),
    ("published 21 Jul 2026 15:04:00 GMT, updated", T0),  # inside other text
])
def test_a_valid_date_is_read_at_its_own_instant(raw, ts):
    assert _parse_pubdate(raw) == ts


@pytest.mark.parametrize("raw", ["", "not-a-date", "Mon, 21 Jul 2026", "yesterday"])
def test_a_date_that_does_not_read_is_unknown(raw):
    assert _parse_pubdate(raw) == 0.0


# ── the store ────────────────────────────────────────────────────────────

def _dated(n, *, src="desk.example", start=NOW - 60):
    return [NewsItem(title=f"dated {src} {i}", url=f"http://{src}/{i}", source=src,
                     published_ts=start - i * 60) for i in range(n)]


def _blind(n, *, src="blind.example"):
    return [NewsItem(title=f"undated {i}", url=f"http://{src}/{i}", source=src,
                     published_ts=0.0) for i in range(n)]


def test_an_undated_row_is_not_dropped_when_the_dated_rows_fill_the_store():
    radar = NewsRadar(max_items=3)
    assert radar.ingest(_dated(3, start=NOW - 3600)) == 3
    assert radar.ingest(_blind(2)) == 2
    # Three newer dated rows fill the store: the older dated ones go.
    assert radar.ingest(_dated(3, src="wire.example", start=NOW - 30)) == 3
    assert {i.source for i in radar.recent(3)} == {"wire.example"}
    assert len(radar.recent(10)) == 5
    assert [i.title for i in radar.undated()] == ["undated 0", "undated 1"]
    # Listed after every dated row, as before.
    assert [i.published_ts > 0 for i in radar.recent(10)] == [True] * 3 + [False] * 2


def test_the_count_is_what_the_store_kept():
    """A dated row older than everything at the cap is dropped on arrival,
    and it was counted as added."""
    radar = NewsRadar(max_items=2)
    assert radar.ingest(_dated(3)) == 2
    assert radar.ingest(_dated(1, src="old.example", start=NOW - 86400)) == 0
    assert radar.ingest(_dated(1, src="new.example", start=NOW - 1)) == 1


def test_the_undated_store_is_bounded_by_arrival():
    radar = NewsRadar(max_undated=2)
    radar.ingest(_blind(2))
    radar.ingest(_blind(1, src="later.example"))
    assert [i.source for i in radar.undated()] == ["later.example", "blind.example"]


def test_an_undated_high_impact_row_is_still_found_by_symbol():
    radar = NewsRadar(max_items=1)
    radar.ingest(_dated(2))
    blind = NewsItem(title="SOL exploit", url="u", source="s", published_ts=0.0,
                     symbols=("SOL",))
    radar.ingest([blind])
    assert radar.for_symbol("SOL/USDT") == [blind]


# ── the card, on both surfaces ───────────────────────────────────────────

def _full_radar():
    radar = NewsRadar()
    radar.ingest(_dated(8))
    radar.ingest(_blind(2))
    return radar


def test_the_digest_says_how_many_undated_headlines_it_did_not_list():
    radar = _full_radar()
    out = render_news_digest(radar.recent(8), [], NOW, undated=radar.undated())
    assert ("2 more headlines from blind.example carry no time this bot can read "
            "and are not listed here") in out
    assert "undated 0" not in out


def test_one_unlisted_undated_headline_is_said_in_the_singular():
    radar = NewsRadar()
    radar.ingest(_dated(8))
    radar.ingest(_blind(1))
    out = render_news_digest(radar.recent(8), [], NOW, undated=radar.undated())
    assert "1 more headline from blind.example carries no time" in out
    assert "is not listed here" in out


def test_no_note_when_every_undated_headline_is_listed():
    radar = NewsRadar()
    radar.ingest(_dated(2))
    radar.ingest(_blind(2))
    out = render_news_digest(radar.recent(8), [], NOW, undated=radar.undated())
    assert "undated 0" in out and "undated 1" in out
    assert "not listed here" not in out


def test_the_telegram_door_hands_the_radar_its_undated_rows(monkeypatch):
    radar = _full_radar()

    async def _no_fetch(**_k):
        return 0

    monkeypatch.setattr(radar, "refresh", _no_fetch)
    host = SimpleNamespace(_news_radar=radar, _held_symbols=lambda: [])
    out = asyncio.run(TelegramHandler._news_digest_text(host))
    assert "2 more headlines from blind.example carry no time" in out


def test_the_web_payload_counts_the_undated_rows_it_did_not_list(monkeypatch):
    radar = NewsRadar()
    radar.ingest(_dated(12))
    radar.ingest(_blind(3))

    async def _no_fetch(**_k):
        return 0

    monkeypatch.setattr(radar, "refresh", _no_fetch)
    eng = SimpleNamespace(_news_radar=radar,
                          user_portfolios=SimpleNamespace(get=lambda _u: SimpleNamespace(open_positions=[])))
    app = web.Application()
    app["engine"] = eng
    app["tg_handler"] = SimpleNamespace()
    monkeypatch.setattr(ug, "_guard_user", lambda *a, **k: None)
    req = make_mocked_request("GET", "/news?telegram_id=42", app=app)
    body = json.loads(asyncio.run(ug.handle_news(req)).text)
    assert len(body["recent"]) == 12
    assert body["undated_unlisted"] == 3
    assert body["undated_unlisted_sources"] == ["blind.example"]
    # And none when the page lists them all.
    radar2 = NewsRadar()
    radar2.ingest(_blind(2))
    monkeypatch.setattr(radar2, "refresh", _no_fetch)
    eng._news_radar = radar2
    req = make_mocked_request("GET", "/news?telegram_id=42", app=app)
    body = json.loads(asyncio.run(ug.handle_news(req)).text)
    assert body["undated_unlisted"] == 0 and body["undated_unlisted_sources"] == []
