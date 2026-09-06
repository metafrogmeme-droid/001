"""An empty radar has two causes and only one of them is "nothing happened".

`_news_digest_text` swallows a refresh error into a debug log:

    try:
        await radar.refresh(symbols=watch)
    except Exception as exc:
        system_log.debug("news refresh failed: %s", exc)

and then renders whatever the cache holds. With an unreachable feed set that
is nothing, so /news answered a total outage with:

    "No headlines yet — the radar fills on the next refresh."

A promise, printed at the instant the refresh had just failed, and repeated
identically for as long as the feeds stayed down. Absent is never a
measurement — the same rule the scan coverage note and the position mark
now enforce.

There is a second, quieter case: stand-down nudges on held positions render
from CACHED items. Those are real, but after a failed refresh they are not
current, and a stand-down nudge is exactly the kind of line an operator acts
on immediately.
"""
from __future__ import annotations

from bot.core.news import Impact, NewsItem, render_news_digest


def _item(title="BTC ETF approved", impact=Impact.LOW):
    return NewsItem(title=title, url="https://x/y", source="CoinDesk",
                    published_ts=100.0, impact=impact, symbols=("BTC",))


_REC = [{"symbol": "BTC", "headline": "Exchange halted withdrawals",
         "url": "https://x/z", "reasons": ["halt"], "age_sec": 120}]


class TestAQuietTapeAndAnUnreadOneReadDifferently:
    def test_a_successful_empty_refresh_keeps_the_warm_up_line(self):
        out = render_news_digest([], [], 1000.0)
        assert "fills on the next refresh" in out

    def test_a_failed_refresh_does_not_promise_a_next_refresh(self):
        out = render_news_digest([], [], 1000.0, refresh_failed=True)
        assert "fills on the next refresh" not in out, (
            "that sentence describes a radar warming up, printed at the "
            "moment the fetch had just failed"
        )

    def test_a_failed_refresh_names_the_fetch_failure(self):
        out = render_news_digest([], [], 1000.0, refresh_failed=True)
        assert "could not be read" in out
        assert "not a quiet news day" in out

    def test_a_failed_refresh_claims_nothing_about_the_world(self):
        out = render_news_digest([], [], 1000.0, refresh_failed=True)
        assert "nothing here says whether anything happened" in out

    def test_the_two_empty_states_are_distinguishable(self):
        assert (render_news_digest([], [], 1000.0)
                != render_news_digest([], [], 1000.0, refresh_failed=True))


class TestCachedItemsAreLabelledStaleNotCurrent:
    def test_standdown_nudges_after_a_failed_refresh_are_marked(self):
        out = render_news_digest([], _REC, 1000.0, refresh_failed=True)
        assert "last successful read" in out, (
            "a stand-down nudge is acted on immediately; rendering a cached "
            "one as current is the claim this file exists to prevent"
        )

    def test_a_healthy_refresh_adds_no_staleness_caveat(self):
        # A caveat on every healthy digest is how a real one gets skipped.
        out = render_news_digest([], _REC, 1000.0)
        assert "last successful read" not in out

    def test_headlines_present_and_refresh_ok_says_nothing_extra(self):
        out = render_news_digest([_item()], [], 1000.0)
        assert "could not be read" not in out
        assert "last successful read" not in out


class TestTheHandlerActuallyPassesTheFlag:
    def test_the_refresh_failure_reaches_the_renderer(self):
        from tests.source_scan import code_only, handler_sources
        # `_news_digest_text` lives in the market-context mixin since the
        # handler split; read every file the handler class is made of.
        src = "\n".join(code_only(p.read_text(encoding="utf-8")) for p in handler_sources())
        assert "_refresh_failed = True" in src, (
            "the except block must record the failure, not only log it"
        )
        assert "refresh_failed=_refresh_failed" in src, (
            "a flag the renderer never receives fixes nothing"
        )
