"""NEWS-1: realtime news radar core (headline impact + relevance + stand-down).

All pure logic — no network. The async fetch is gated (NEWS_RADAR_ENABLED,
default ON — public RSS, no key) and best-effort; only the parsing/scoring/
recommendation logic is exercised here. Everything is ADVISORY: the stand-down
is a recommendation, never an auto-action (§4 — heuristic flags, never verdicts;
and the live money-path stays fail-open).
"""

from __future__ import annotations


from bot.core.news import (
    Impact, NewsItem, NewsRadar,
    classify_impact, match_symbols, parse_rss, standdown_for_holdings,
)


# ── impact classification (heuristic flag, with reasons) ─────────────────────

def test_high_impact_keywords_flag_high_with_reasons():
    impact, reasons = classify_impact("Major exchange hacked, $200M drained overnight")
    assert impact is Impact.HIGH
    assert "hacked" in reasons and "drained" in reasons


def test_medium_impact_keywords_flag_medium():
    impact, reasons = classify_impact("Bitcoin ETF sees record inflows")
    assert impact is Impact.MEDIUM
    assert "etf" in reasons


def test_benign_headline_is_low_impact():
    impact, reasons = classify_impact("Weekly market wrap: quiet tape into the weekend")
    assert impact is Impact.LOW
    assert reasons == ()


def test_impact_is_word_boundary_matched():
    # "sec" must not fire inside "second" / "security".
    impact, _ = classify_impact("A second look at the market this week")
    assert impact is Impact.LOW


# ── symbol relevance (ticker + common name) ──────────────────────────────────

def test_matches_by_ticker_and_normalizes_perp_symbols():
    assert match_symbols("Solana network halts amid outage",
                         ["SOL/USDT:USDT", "BTC/USDT:USDT"]) == ("SOL",)


def test_matches_by_common_name_alias():
    assert match_symbols("Ethereum upgrade goes live tonight", ["ETH/USDT"]) == ("ETH",)


def test_no_false_symbol_match():
    assert match_symbols("Gold rallies on macro fears", ["SOL/USDT", "BTC/USDT"]) == ()


# ── stand-down recommendation (advisory only) ────────────────────────────────

def _item(title, syms, impact, age, now):
    imp, reasons = classify_impact(title)
    return NewsItem(title=title, url="u", source="s", published_ts=now - age,
                    impact=impact, impact_reasons=reasons, symbols=tuple(syms))


def test_standdown_fires_for_fresh_high_impact_on_a_held_symbol():
    now = 1_000_000.0
    items = [_item("SOL exploit drains funds", ["SOL"], Impact.HIGH, 60, now)]
    recs = standdown_for_holdings(items, ["SOL/USDT:USDT"], now)
    assert len(recs) == 1
    assert recs[0]["symbol"] == "SOL"
    assert "Advisory only" in recs[0]["recommendation"]


def test_standdown_ignores_symbols_not_held():
    now = 1_000_000.0
    items = [_item("SOL exploit", ["SOL"], Impact.HIGH, 60, now)]
    assert standdown_for_holdings(items, ["BTC/USDT"], now) == []


def test_standdown_ignores_stale_and_low_impact_news():
    now = 1_000_000.0
    stale = _item("SOL exploit", ["SOL"], Impact.HIGH, 99_999, now)   # too old
    low = _item("SOL weekly recap", ["SOL"], Impact.LOW, 60, now)     # not high
    assert standdown_for_holdings([stale, low], ["SOL/USDT"], now) == []


# ── RSS parsing (tolerant, scored) ───────────────────────────────────────────

_RSS = """<?xml version="1.0"?><rss><channel>
<item><title>Solana halted after exploit</title><link>http://x/1</link>
<pubDate>Mon, 21 Jul 2026 15:04:05 GMT</pubDate></item>
<item><title>Weekly recap</title><link>http://x/2</link></item>
</channel></rss>"""


def test_parse_rss_scores_and_matches_each_item():
    items = parse_rss(_RSS, "coindesk.com", ["SOL/USDT:USDT"], now=1_753_110_000.0)
    assert len(items) == 2
    top = items[0]
    assert top.impact is Impact.HIGH and top.symbols == ("SOL",)
    assert top.published_ts > 0            # pubDate parsed
    # The second item has no pubDate. That is unreadable, not "now".
    assert items[1].published_ts == 0.0
    assert items[1].impact is Impact.LOW


def test_parse_rss_never_raises_on_garbage():
    assert parse_rss("<not valid xml", "s", [], now=0.0) == []


# ── radar store: dedup, per-symbol, gating ───────────────────────────────────

def test_radar_ingests_dedups_and_queries_by_symbol():
    radar = NewsRadar()
    items = parse_rss(_RSS, "coindesk.com", ["SOL/USDT"], now=1_753_110_000.0)
    assert radar.ingest(items) == 2
    assert radar.ingest(items) == 0                       # same items → deduped
    assert [i.title for i in radar.for_symbol("SOL/USDT")] == ["Solana halted after exploit"]
    assert len(radar.high_impact()) == 1


def test_radar_is_on_by_default_and_operator_can_disable(monkeypatch):
    # Advisory public-RSS radar defaults ON (no API key needed); an operator
    # can turn it off with NEWS_RADAR_ENABLED=0.
    monkeypatch.delenv("NEWS_RADAR_ENABLED", raising=False)
    assert NewsRadar.enabled() is True
    monkeypatch.setenv("NEWS_RADAR_ENABLED", "0")
    assert NewsRadar.enabled() is False
    monkeypatch.setenv("NEWS_RADAR_ENABLED", "1")
    assert NewsRadar.enabled() is True


def test_radar_feeds_are_public_and_keyless():
    for url in NewsRadar.feeds():
        assert url.startswith("https://")
        # No API-key query params baked into the default feeds.
        assert "apikey" not in url.lower() and "token=" not in url.lower()


# ── NEWS-1b: digest formatter + /news command wiring ─────────────────────────

def test_digest_leads_with_held_position_standdown_then_headlines():
    from bot.core.news import render_news_digest, parse_rss, NewsRadar
    now = 1_753_110_000.0
    rss = ('<?xml version="1.0"?><rss><channel><item>'
           '<title>Solana halted after exploit</title><link>http://x/1</link>'
           '<pubDate>Mon, 21 Jul 2026 15:04:05 GMT</pubDate></item></channel></rss>')
    radar = NewsRadar()
    radar.ingest(parse_rss(rss, "coindesk.com", ["SOL/USDT:USDT"], now))
    out = render_news_digest(radar.recent(5), radar.standdown(["SOL/USDT:USDT"], now), now)
    assert "On your positions" in out
    assert "SOL" in out and "Advisory only" in out
    assert "Latest headlines" in out
    # No dollar figures / trade actions — advisory text only.
    assert "traded" not in out.lower() or "nothing was traded" in out.lower()


def test_digest_handles_the_empty_radar():
    from bot.core.news import render_news_digest
    out = render_news_digest([], [], 1_753_110_000.0)
    assert "News radar" in out
    assert "fills on the next refresh" in out


def test_news_command_is_registered_and_advisory():
    import inspect
    from bot.skills import telegram_handler as th
    src = inspect.getsource(th)
    assert '("news", self._cmd_news)' in src
    # The radar gating + rendering now live in the shared _news_digest_text
    # helper (reused by /news AND the free-text "news" intercept on both
    # surfaces); _cmd_news delegates to it.
    cmd = inspect.getsource(th.TelegramHandler._cmd_news)
    assert "_news_digest_text" in cmd
    digest = inspect.getsource(th.TelegramHandler._news_digest_text)
    assert "NewsRadar.enabled()" in digest
    assert "render_news_digest" in digest
    assert "never moves or blocks a trade" in digest


def test_held_symbols_helper_is_best_effort():
    import inspect
    from bot.skills import telegram_handler as th
    src = inspect.getsource(th.TelegramHandler._held_symbols)
    assert "open_positions" in src
    assert "except Exception" in src   # a missing source is skipped, never fatal


def _rfc(ts: float) -> str:
    """English RFC-822, independent of the process locale."""
    import time
    t = time.gmtime(ts)
    mon = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")[t.tm_mon - 1]
    day = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[t.tm_wday]
    return (f"{day}, {t.tm_mday:02d} {mon} {t.tm_year} "
            f"{t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d} GMT")


def _rss(rows: list[tuple[str, str, float | None]]) -> str:
    """Newest-first RSS, the order the live feeds actually use. ``None`` is a
    pubDate that does not parse."""
    parts = []
    for title, url, ts in rows:
        pub = _rfc(ts) if ts else "not-a-date"
        parts.append(
            f"<item><title>{title}</title><link>{url}</link>"
            f"<pubDate>{pub}</pubDate></item>")
    return ("<?xml version=\"1.0\"?><rss><channel>"
            + "".join(parts) + "</channel></rss>")


def _meta(out: str, title: str) -> str:
    lines = out.splitlines()
    for i, line in enumerate(lines):
        if title in line and i + 1 < len(lines):
            return lines[i + 1]
    raise AssertionError(title)


def test_latest_headlines_follow_publication_time_not_feed_order():
    """A newest-first page used to render backwards.

    Decrypt's feed is current and long: today's post first, a December 2025
    post last. Prepending each item put that tail at the front of the card,
    so "Latest headlines" was six rows about 270 days old.
    """
    import calendar

    from bot.core.news import headline_view, render_news_digest

    now = float(calendar.timegm((2026, 10, 4, 13, 48, 0, 0, 0, 0)))
    today = now - 30 * 60
    old = now - 270 * 86400
    rss = _rss([
        ("Desk note from this afternoon", "http://x/today", today),
        ("Note from an hour ago", "http://x/h1", now - 3600),
        ("Note from two hours ago", "http://x/h2", now - 2 * 3600),
        ("Note from three hours ago", "http://x/h3", now - 3 * 3600),
        ("Note from four hours ago", "http://x/h4", now - 4 * 3600),
        ("Note from five hours ago", "http://x/h5", now - 5 * 3600),
        ("Year-start roundup", "http://x/old", old),
        ("Clockless note", "http://x/blind", None),
    ])
    radar = NewsRadar()
    radar.ingest(parse_rss(rss, "decrypt.co", ["BTC/USDT"], now))
    shown = radar.recent(8)
    assert [i.title for i in shown[:6]] == [
        "Desk note from this afternoon",
        "Note from an hour ago",
        "Note from two hours ago",
        "Note from three hours ago",
        "Note from four hours ago",
        "Note from five hours ago",
    ]
    assert shown[6].title == "Year-start roundup"
    assert shown[7].title == "Clockless note"
    assert shown[7].published_ts == 0.0

    out = render_news_digest(shown, [], now, limit=8)
    today_line = _meta(out, "Desk note from this afternoon")
    old_line = _meta(out, "Year-start roundup")
    blind_line = _meta(out, "Clockless note")
    assert "30m ago" in today_line
    assert "270d" not in today_line
    assert "270d ago" in old_line
    assert "Clockless note" in out
    assert "time unreadable" in blind_line
    assert "1s ago" not in blind_line
    assert "0d ago" not in blind_line

    by_title = {i.title: headline_view(i, now) for i in shown}
    assert by_title["Desk note from this afternoon"]["age_label"] == "30m ago"
    assert by_title["Desk note from this afternoon"]["age_sec"] == 30 * 60
    assert by_title["Year-start roundup"]["age_label"] == "270d ago"
    assert by_title["Year-start roundup"]["age_sec"] == 270 * 86400
    assert by_title["Clockless note"]["age_sec"] is None
    assert by_title["Clockless note"]["age_label"] == "time unreadable"
    assert by_title["Clockless note"]["title"] == "Clockless note"

    # A later item files by its own clock, not by arriving last.
    radar.ingest([NewsItem(
        title="Just filed", url="http://x/new", source="s",
        published_ts=now - 60)])
    assert radar.recent(1)[0].title == "Just filed"
    radar.ingest([NewsItem(
        title="Older than the tail", url="http://x/ancient", source="s",
        published_ts=old - 86400)])
    assert radar.recent(1)[0].title == "Just filed"


def test_the_store_keeps_newer_headlines_when_the_cap_bites():
    now = 1_800_000_000.0
    radar = NewsRadar(max_items=2)
    # Feed order: newest first. Prepending this list used to drop the newest
    # when the cap bit, and keep the oldest.
    radar.ingest([
        NewsItem(title="newest", url="n", source="s", published_ts=now - 60),
        NewsItem(title="mid", url="m", source="s", published_ts=now - 3600),
        NewsItem(title="oldest", url="o", source="s",
                 published_ts=now - 270 * 86400),
    ])
    assert [i.title for i in radar.recent()] == ["newest", "mid"]


def test_an_unreadable_clock_does_not_stand_down_and_a_fresh_one_still_does():
    now = 1_000_000.0
    fresh = NewsItem(
        title="SOL exploit drains funds", url="http://x/f", source="s",
        published_ts=now - 60, impact=Impact.HIGH, symbols=("SOL",))
    blind = NewsItem(
        title="SOL exploit drains funds again", url="http://x/b", source="s",
        published_ts=0.0, impact=Impact.HIGH, symbols=("SOL",))
    recs = standdown_for_holdings([blind, fresh], ["SOL/USDT"], now)
    assert [r["headline"] for r in recs] == ["SOL exploit drains funds"]
    assert recs[0]["age_label"] == "60s ago"
